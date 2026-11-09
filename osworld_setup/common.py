"""Shared host-side helpers for the three OSWorld VM commands."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
import shlex

import requests


GUEST_SOURCE = "/home/user/workswarm"
GUEST_DATA = "/home/user/.jiuwenswarm"
AGENT_HTTP_PORT = 8766


def osworld_env(repo: Path):
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from desktop_env.desktop_env import DesktopEnv
    from desktop_env.providers.virtualbox.provider import VirtualBoxProvider

    # The official provider uses a fixed 60-second timeout for all VBoxManage
    # calls. Saved-state transitions on large VM images can exceed that while
    # VirtualBox is still making progress.
    if not getattr(VirtualBoxProvider, "_workswarm_timeout_extended", False):
        def execute_virtualbox(command: list[str]) -> str:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=300, text=True, encoding="utf-8")
            if result.returncode != 0:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout.strip()

        VirtualBoxProvider._execute_command = staticmethod(execute_virtualbox)
        VirtualBoxProvider._workswarm_timeout_extended = True

    return DesktopEnv


def require_vm_cli(provider: str) -> None:
    command = "vmrun" if provider == "vmware" else "VBoxManage"
    if shutil.which(command):
        return
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = (
            [program_files / "Oracle" / "VirtualBox" / "VBoxManage.exe"]
            if provider == "virtualbox"
            else [program_files / "VMware" / "VMware Workstation" / "vmrun.exe",
                  Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
                  / "VMware" / "VMware Workstation" / "vmrun.exe"]
        )
        for candidate in candidates:
            if candidate.is_file():
                os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
                return
    raise RuntimeError(f"{command} is unavailable; install {provider} or add its CLI to PATH")


def new_environment(osworld_repo: Path, provider: str, vm_path: Path | None, snapshot_name: str):
    os.chdir(osworld_repo)
    desktop_env = osworld_env(osworld_repo)
    require_vm_cli(provider)
    return desktop_env(
        provider_name=provider,
        path_to_vm=str(vm_path) if vm_path else None,
        snapshot_name=snapshot_name,
        action_space="pyautogui",
        require_a11y_tree=False,
        os_type="Ubuntu",
        recording_update="off",
    )


@contextmanager
def virtualbox_first_boot_recovery(osworld_repo: Path, guest_password: str):
    """Restart the official guest server if it fails before X11 is ready.

    OSWorld's VirtualBox manager waits indefinitely for /screenshot on the
    first import. The image's osworld.service can hit systemd's start limit
    before GNOME creates display :0, so a small host-side watcher repairs only
    that observed failure while the manager is waiting.
    """
    stop = threading.Event()
    vm_root = osworld_repo / "virtualbox_vm_data"

    def guest_run(vm_name: str, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["VBoxManage", "guestcontrol", vm_name, "run", "--username", "user",
             "--password", guest_password, "--exe", "/bin/bash", "--wait-stdout",
             "--wait-stderr", "--", "-lc", script],
            text=True, capture_output=True, timeout=15,
        )

    def watch() -> None:
        repaired: set[str] = set()
        while not stop.wait(5):
            try:
                running = subprocess.run(
                    ["VBoxManage", "list", "runningvms"],
                    text=True, capture_output=True, timeout=10, check=True,
                ).stdout
                for vm_file in vm_root.glob("Ubuntu*/Ubuntu*.vbox"):
                    vm_name = vm_file.stem
                    if vm_name in repaired or f'"{vm_name}"' not in running:
                        continue
                    failed = guest_run(
                        vm_name,
                        "test -S /tmp/.X11-unix/X0 && systemctl is-failed --quiet osworld.service",
                    )
                    if failed.returncode != 0:
                        continue
                    password = shlex.quote(guest_password)
                    repair = (
                        f"printf '%s\\n' {password} | sudo -S -p '' systemctl reset-failed osworld.service; "
                        f"printf '%s\\n' {password} | sudo -S -p '' systemctl start osworld.service; "
                        "systemctl is-active --quiet osworld.service"
                    )
                    if guest_run(vm_name, repair).returncode == 0:
                        repaired.add(vm_name)
                        print(f"Recovered OSWorld screenshot service in {vm_name}", flush=True)
            except (OSError, subprocess.SubprocessError):
                continue

    watcher = threading.Thread(target=watch, name="osworld-first-boot-recovery", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join(timeout=1)


def bash(env, script: str, timeout: int = 120) -> str:
    url = f"http://{env.vm_ip}:{env.server_port}"
    script = "set -e\n" + script
    response = requests.post(
        url + "/run_bash_script",
        json={"script": script, "timeout": timeout, "working_dir": None},
        timeout=(10, timeout + 100),
    )
    if response.status_code == 404:
        response = requests.post(
            url + "/execute",
            json={"command": ["bash", "-lc", script], "shell": False},
            timeout=(10, timeout + 100),
        )
    response.raise_for_status()
    result = response.json()
    if result.get("returncode") != 0:
        raise RuntimeError(f"Guest command failed: {result!r}")
    return result.get("output", "")


def upload(env, path: str, content: bytes) -> None:
    response = requests.post(
        f"http://{env.vm_ip}:{env.server_port}/setup/upload",
        data={"file_path": path},
        files={"file_data": (Path(path).name, io.BytesIO(content))},
        timeout=180,
    )
    response.raise_for_status()


def start_guest_recording(env) -> str:
    """Record directly in the OSWorld guest; its legacy end route can truncate MP4."""
    recording_id = uuid.uuid4().hex
    video_path = f"/tmp/workswarm-{recording_id}.mp4"
    pid_path = f"/tmp/workswarm-{recording_id}.pid"
    log_path = f"/tmp/workswarm-{recording_id}.log"
    bash(env, f"nohup ffmpeg -y -nostdin -f x11grab -draw_mouse 1 -video_size 1024x768 "
         f"-i :0.0 -c:v libx264 -r 20 -pix_fmt yuv420p -profile:v main "
         f"-level:v 3.1 -movflags +faststart "
         f"{video_path} >{log_path} 2>&1 </dev/null & echo $! >{pid_path}; "
         f"sleep 2; kill -0 $(cat {pid_path})")
    return recording_id


def stop_guest_recording(env, recording_id: str, destination: Path) -> None:
    video_path = f"/tmp/workswarm-{recording_id}.mp4"
    pid_path = f"/tmp/workswarm-{recording_id}.pid"
    bash(env, f"kill -INT $(cat {pid_path}); "
         f"for i in $(seq 1 30); do "
         f"state=$(ps -o stat= -p $(cat {pid_path}) 2>/dev/null || true); "
         f"case \"$state\" in ''|Z*) break;; esac; sleep 1; done; "
         f"test -s {video_path}", timeout=60)
    video = env.controller.get_file(video_path, strict=True)
    if not video or b"moov" not in video:
        raise RuntimeError("Guest recording is empty or has no MP4 index")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(video)
    probe = shutil.which("ffprobe")
    if probe:
        subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(destination)],
                       check=True, capture_output=True, timeout=30)


def source_archive(repo: Path, *, include_untracked: bool = False) -> bytes:
    excluded = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
    listing = ["git", "ls-files", "--cached"]
    if include_untracked:
        listing += ["--others", "--exclude-standard"]
    listing.append("-z")
    listed = subprocess.run(
        listing,
        cwd=repo, capture_output=True, check=True,
    ).stdout
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for raw in listed.split(b"\0"):
            if not raw:
                continue
            relative = Path(os.fsdecode(raw))
            name = relative.name
            if (excluded.intersection(relative.parts) or name == ".env"
                    or (name.startswith(".env.") and name not in {".env.template", ".env.example"})
                    or name.endswith((".sqlite3", ".db", ".log", ".pem", ".p12", ".pfx"))):
                continue
            path = repo / relative
            if path.is_file():
                archive.write(path, relative.as_posix())
    return output.getvalue()


def vm_identifier(provider: str, path: Path) -> str:
    if provider == "vmware":
        return str(path)
    if path.suffix.lower() != ".vbox":
        return str(path)
    machine = ET.parse(path).getroot().find(".//{http://www.virtualbox.org/}Machine")
    if machine is None or not machine.get("uuid"):
        raise RuntimeError(f"VirtualBox VM UUID not found in {path}")
    return machine.attrib["uuid"].strip("{}")


def snapshot_exists(provider: str, vm_path: Path, name: str) -> bool:
    target = vm_identifier(provider, vm_path)
    command = (["vmrun", "-T", "ws", "listSnapshots", target] if provider == "vmware"
               else ["VBoxManage", "snapshot", target, "list", "--machinereadable"])
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    if provider == "vmware":
        return name in result.stdout.splitlines()
    return any(line.startswith("SnapshotName") and line.split("=", 1)[-1].strip('"') == name
               for line in result.stdout.splitlines())


def restore_snapshot(provider: str, vm_path: Path, name: str) -> None:
    """Restore the configured baseline before DesktopEnv can skip its own reset."""
    require_vm_cli(provider)
    target = vm_identifier(provider, vm_path)
    if not snapshot_exists(provider, vm_path, name):
        raise RuntimeError(f"VM snapshot does not exist: {name}")
    if provider == "virtualbox":
        info = subprocess.run(["VBoxManage", "showvminfo", target, "--machinereadable"],
                              text=True, capture_output=True, check=True).stdout
        state = next((line.split("=", 1)[1].strip('"') for line in info.splitlines()
                      if line.startswith("VMState=")), None)
        if state == "running":
            subprocess.run(["VBoxManage", "controlvm", target, "savestate"],
                           check=True, timeout=300)
        elif state not in {"saved", "poweroff", "aborted"}:
            raise RuntimeError(f"Cannot restore snapshot while VM is {state}")
        subprocess.run(["VBoxManage", "snapshot", target, "restore", name],
                       check=True, timeout=300)
    else:
        subprocess.run(["vmrun", "-T", "ws", "revertToSnapshot", target, name],
                       check=True, timeout=300)


def take_snapshot(provider: str, vm_path: Path, name: str) -> None:
    target = vm_identifier(provider, vm_path)
    command = (["vmrun", "-T", "ws", "snapshot", target, name] if provider == "vmware"
               else ["VBoxManage", "snapshot", target, "take", name])
    subprocess.run(command, check=True)


def dotenv_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def model_dotenv(model: str, api_base: str, api_key: str, provider: str, endpoint_profile: str) -> bytes:
    return "\n".join((
        f"API_BASE={dotenv_value(api_base)}",
        f"API_KEY={dotenv_value(api_key)}",
        f"MODEL_NAME={dotenv_value(model)}",
        f"MODEL_PROVIDER={dotenv_value(provider)}",
        f"ENDPOINT_PROFILE={dotenv_value(endpoint_profile)}",
        "AGENT_HTTP_ENABLED=true",
        "AGENT_HTTP_HOST=0.0.0.0",
        f"AGENT_HTTP_PORT={AGENT_HTTP_PORT}",
        "",
    )).encode("utf-8")


def install_workswarm(env, archive: bytes, dotenv: bytes) -> None:
    upload(env, "/tmp/workswarm-source.zip", archive)
    bash(env, f"mkdir -p {GUEST_SOURCE}; python3 -m zipfile -e /tmp/workswarm-source.zip {GUEST_SOURCE}; rm /tmp/workswarm-source.zip", timeout=300)
    uv_url = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"
    uv_response = requests.get(uv_url, timeout=120)
    uv_response.raise_for_status()
    with tarfile.open(fileobj=io.BytesIO(uv_response.content), mode="r:gz") as uv_archive:
        if "uv-x86_64-unknown-linux-gnu/uv" not in uv_archive.getnames():
            raise RuntimeError("uv release archive is missing the Linux executable")
    upload(env, "/tmp/workswarm-uv.tar.gz", uv_response.content)
    bash(env, "mkdir -p /home/user/.local/bin; tar -xzf /tmp/workswarm-uv.tar.gz -C /tmp uv-x86_64-unknown-linux-gnu/uv; cp /tmp/uv-x86_64-unknown-linux-gnu/uv /home/user/.local/bin/uv; chmod 755 /home/user/.local/bin/uv; rm /tmp/workswarm-uv.tar.gz", timeout=300)
    bash(env, f"cd {GUEST_SOURCE}; /home/user/.local/bin/uv python install 3.11; /home/user/.local/bin/uv sync --python 3.11 --no-default-groups --extra cua", timeout=1800)
    bash(env, f"cd {GUEST_SOURCE}; .venv/bin/python -m jiuwenswarm.init_workspace", timeout=180)
    # Keep the normal prompts and tools; enable CUA, trajectory diagnosis, and
    # the per-session debug dump for all Agent/Code requests by default.
    bash(env, f"cd {GUEST_SOURCE}; .venv/bin/python - <<'PY'\n"
         "from pathlib import Path\n"
         "from ruamel.yaml import YAML\n"
         "p = Path('/home/user/.jiuwenswarm/config/config.yaml')\n"
         "yaml = YAML()\n"
         "with p.open(encoding='utf-8') as source:\n"
         "    config = yaml.load(source)\n"
         "config['trajectory_ui']['enabled'] = True\n"
         "config['trajectory_ui']['diagnosis_enabled'] = True\n"
         "config['react']['subagents']['cua_agent']['enabled'] = True\n"
         "for mode in ('agent', 'code'):\n"
         "    config['debug_trace'][mode]['enabled'] = True\n"
         "    config['debug_trace'][mode]['dump_enabled'] = True\n"
         "with p.open('w', encoding='utf-8') as target:\n"
         "    yaml.dump(config, target)\n"
         "PY")
    upload(env, f"{GUEST_DATA}/config/.env", dotenv)
    bash(env, f"chmod 600 {GUEST_DATA}/config/.env; test -f {GUEST_DATA}/config/config.yaml")


def start_backend(env) -> None:
    bash(env, f"test -s {GUEST_DATA}/config/.env; cd {GUEST_SOURCE}; nohup env DISPLAY=:0 JIUWENSWARM_HOME=/home/user .venv/bin/python -m jiuwenswarm.start_services app >/tmp/workswarm-backend.log 2>&1 </dev/null &", timeout=20)
    health = f"http://{env.vm_ip}:{AGENT_HTTP_PORT}/api/v1/health"
    for _ in range(60):
        try:
            if requests.get(health, timeout=3).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    log = bash(env, "tail -n 100 /tmp/workswarm-backend.log")
    raise RuntimeError(f"WorkSwarm backend did not become ready; guest log:\n{log}")


def chat(env, prompt: str, output: Path, timeout: int) -> str:
    base = f"http://{env.vm_ip}:{AGENT_HTTP_PORT}/api/v1"
    response = requests.post(base + "/sessions", headers={"X-Channel-Id": "web"},
                             json={"create_token": uuid.uuid4().hex, "mode": "agent"}, timeout=30)
    response.raise_for_status()
    created = response.json()
    session_id = created.get("data", {}).get("session_id")
    if not session_id:
        raise RuntimeError(f"No session_id in response: {created!r}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "session.json").write_text(json.dumps(created, indent=2), encoding="utf-8")
    chat_request = {
        "session_id": session_id,
        "query": prompt,
        "content": prompt,
        "mode": "agent",
        "enable_streaming": True,
        "channel_id": "web",
    }
    (output / "request.json").write_text(
        json.dumps(chat_request, ensure_ascii=False, indent=2), encoding="utf-8")
    deadline = time.monotonic() + timeout
    with requests.post(base + "/chat/completions", headers={"Accept": "text/event-stream", "X-Channel-Id": "web"}, json={
        key: chat_request[key] for key in ("session_id", "query", "content", "mode", "enable_streaming")
    }, stream=True, timeout=(20, timeout)) as stream:
        stream.raise_for_status()
        with (output / "stream.sse").open("wb") as target:
            for chunk in stream.iter_content(chunk_size=8192):
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Agent session exceeded {timeout} seconds")
                target.write(chunk)
    complete = False
    errors = []
    with (output / "stream.sse").open("r", encoding="utf-8") as source:
        for line in source:
            if not line.startswith("data: "):
                continue
            frame = json.loads(line[6:])
            payload = frame.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if frame.get("ok") is False or payload.get("event_type") in {"chat.error", "runtime.error"}:
                errors.append(frame)
            complete = complete or frame.get("is_complete") is True
    if errors or not complete:
        raise RuntimeError(f"Agent session did not complete: errors={errors[-2:]!r}, complete={complete}")
    return session_id


def save_trace(env, session_id: str, output: Path, *, partial: bool = False) -> None:
    """Copy original WorkSwarm artifacts; on failure preserve every available file."""
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def copy_file(source: str, name: str) -> None:
        try:
            data = env.controller.get_file(source, strict=True)
            if not data:
                raise RuntimeError(f"Guest file is absent or empty: {source}")
            (output / name).write_bytes(data)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    base = f"{GUEST_DATA}/agent/sessions/{session_id}"
    for name in ("history.jsonl", "metadata.json"):
        copy_file(f"{base}/{name}", name)
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    db = f"{GUEST_DATA}/.trace/sessions/{digest[:2]}/{digest}.sqlite3"
    try:
        bash(env, f"python3 - <<'PY'\nimport sqlite3, time\npath={db!r}\nfor attempt in range(20):\n    try:\n        src=sqlite3.connect('file:' + path + '?mode=ro', uri=True)\n        count=src.execute('SELECT count(*) FROM otlp_span_records').fetchone()[0]\n        if count:\n            break\n        src.close()\n    except sqlite3.Error:\n        pass\n    time.sleep(0.5)\nelse:\n    raise RuntimeError('No WorkSwarm trajectory spans were written')\ndst=sqlite3.connect('/tmp/workswarm-trace.sqlite3')\nsrc.backup(dst)\ndst.close(); src.close()\nPY")
        copy_file("/tmp/workswarm-trace.sqlite3", "trajectory.sqlite3")
    except Exception as exc:
        errors.append(f"trajectory.sqlite3: {exc}")
    debug_dump = f"{GUEST_DATA}/.agent/traces/dump-agent-{session_id}.txt"
    copy_file(debug_dump, "debug-trace.txt")
    # The OTel file exporter writes daily JSONL files asynchronously. Let its
    # default batch window elapse before collecting the guest's raw files.
    time.sleep(6)
    try:
        traces_dir = f"{GUEST_DATA}/.trace"
        paths = bash(env, f"find {traces_dir} -maxdepth 1 -type f "
                     "-name 'traces-????-??-??.jsonl' -size +0c -print").splitlines()
        if not paths:
            raise RuntimeError(f"No nonempty OTel daily trace files in {traces_dir}")
        for source in paths:
            name = Path(source).name
            if not re.fullmatch(r"traces-\d{4}-\d{2}-\d{2}\.jsonl", name):
                raise RuntimeError(f"Unexpected OTel trace filename: {source}")
            copy_file(source, name)
    except Exception as exc:
        errors.append(f"OTel daily traces: {exc}")
    if errors:
        (output / "trace-export-errors.json").write_text(
            json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        if not partial:
            raise RuntimeError(f"WorkSwarm trace export incomplete: {errors}")
    else:
        (output / "trace-export-errors.json").unlink(missing_ok=True)


def load_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    for key in ("osworld_repo", "vm_provider", "vm_path", "snapshot_name"):
        if not state.get(key):
            raise ValueError(f"VM state is missing {key}: {path}")
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_example(repo: Path, domain: str, example_id: str) -> dict:
    path = repo / "evaluation_examples" / "examples" / domain / f"{example_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))
