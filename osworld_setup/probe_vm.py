"""One-time desktop file probe against the prepared WorkSwarm VM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from common import (bash, chat, load_state, new_environment, restore_snapshot, save_trace,
                    start_backend, start_guest_recording, stop_guest_recording)
from review_video import make_review_video


PROBE_PROMPT = "打开记事本，输入 hello，保存到~/下面，文件名为 hello.txt。"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    state = load_state(args.state_file.resolve())
    output = args.output.resolve()
    if (output / "probe.json").exists():
        parser.error(f"Probe already passed at {output}; choose a new output directory to rerun")
    osworld_repo = Path(state["osworld_repo"])
    vm_path = Path(state["vm_path"])
    restore_snapshot(state["vm_provider"], vm_path, state["snapshot_name"])
    env = new_environment(osworld_repo, state["vm_provider"], vm_path, state["snapshot_name"])
    try:
        # The host already restored the clean baseline before powering on.
        env.is_environment_used = False
        env.reset()
        bash(env, "test ! -e /home/user/hello.txt")
        start_backend(env)
        output.mkdir(parents=True, exist_ok=True)
        (output / "baseline.json").write_text(json.dumps({
            "snapshot_name": state["snapshot_name"],
            "hello_absent_before_run": True,
        }, indent=2), encoding="utf-8")
        recording_requested_at = time.time()
        recording_id = start_guest_recording(env)
        (output / "recording.json").write_text(json.dumps({
            "recording_id": recording_id,
            "requested_at": recording_requested_at,
            "ready_at": time.time(),
        }, indent=2), encoding="utf-8")
        try:
            session_id = chat(env, PROBE_PROMPT, output, args.timeout)
        except Exception:
            session_file = output / "session.json"
            if session_file.exists():
                failed_session = json.loads(session_file.read_text(encoding="utf-8"))
                failed_id = failed_session.get("data", {}).get("session_id")
                if failed_id:
                    try:
                        save_trace(env, failed_id, output, partial=True)
                    except Exception as trace_error:
                        print(f"Could not export failed-session trace: {trace_error}", flush=True)
            raise
        finally:
            stop_guest_recording(env, recording_id, output / "desktop.mp4")
            try:
                make_review_video(output)
            except Exception as review_error:
                print(f"Could not build review video: {review_error}", flush=True)
        save_trace(env, session_id, output)
        verified_path = bash(env, "python3 - <<'PY'\nfrom pathlib import Path\np=Path('/home/user/hello.txt')\nif not p.is_file():\n    raise RuntimeError(f'Probe file missing: {p}')\nif p.read_text(encoding='utf-8').strip() != 'hello':\n    raise RuntimeError(f'Probe file content is not hello: {p}')\nprint(p)\nPY").strip()
        review_created = (output / "review.mp4").is_file()
        (output / "probe.json").write_text(json.dumps({
            "session_id": session_id,
            "snapshot_name": state["snapshot_name"],
            "guest_file": verified_path,
            "file_verified": True,
            "trace_exported": True,
            "video_exported": True,
            "review_video_exported": review_created,
        }, indent=2), encoding="utf-8")
        print(f"Probe passed; WorkSwarm trace saved to {output}", flush=True)
    finally:
        try:
            env.close()
        finally:
            restore_snapshot(state["vm_provider"], vm_path, state["snapshot_name"])


if __name__ == "__main__":
    main()
