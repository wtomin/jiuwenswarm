"""Run one OSWorld case using the prepared WorkSwarm VM snapshot.

This command neither installs WorkSwarm nor changes the saved model settings.
Run prepare_vm.py once, optionally probe_vm.py once, then this command for cases.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from common import (chat, load_example, load_state, new_environment, restore_snapshot, save_trace,
                    start_backend, start_guest_recording, stop_guest_recording,
                    require_vm_cli, vm_identifier)
from review_video import make_review_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--domain", default="os")
    parser.add_argument("--example-id", default="5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--virtualbox-nat", action="store_true",
                        help="Use NAT on VirtualBox NIC 1 after restoring the snapshot; keep NIC 2 host-only")
    args = parser.parse_args()

    state = load_state(args.state_file.resolve())
    if args.virtualbox_nat and state["vm_provider"] != "virtualbox":
        parser.error("--virtualbox-nat requires a VirtualBox VM")
    osworld_repo = Path(state["osworld_repo"])
    example = load_example(osworld_repo, args.domain, args.example_id)
    case = args.output.resolve() / args.domain / args.example_id
    case.mkdir(parents=True, exist_ok=True)
    vm_path = Path(state["vm_path"])
    restore_snapshot(state["vm_provider"], vm_path, state["snapshot_name"])
    if args.virtualbox_nat:
        require_vm_cli("virtualbox")
        subprocess.run(["VBoxManage", "modifyvm", vm_identifier("virtualbox", vm_path),
                        "--nic1", "nat"], check=True)
    env = new_environment(osworld_repo, state["vm_provider"], vm_path, state["snapshot_name"])
    try:
        # The host already restored the clean baseline before powering on.
        env.is_environment_used = False
        # Apply this case's setup to the already restored WorkSwarm snapshot.
        observation = env.reset(task_config=example)
        screenshot = observation.get("screenshot")
        if screenshot:
            (case / "initial.png").write_bytes(screenshot)
        start_backend(env)
        recording_requested_at = time.time()
        recording_id = start_guest_recording(env)
        (case / "recording.json").write_text(json.dumps({
            "recording_id": recording_id,
            "requested_at": recording_requested_at,
            "ready_at": time.time(),
        }, indent=2), encoding="utf-8")
        try:
            session_id = chat(env, example["instruction"], case, args.timeout)
        except Exception:
            session_file = case / "session.json"
            if session_file.exists():
                failed_session = json.loads(session_file.read_text(encoding="utf-8"))
                failed_id = failed_session.get("data", {}).get("session_id")
                if failed_id:
                    try:
                        save_trace(env, failed_id, case, partial=True)
                    except Exception as trace_error:
                        print(f"Could not export failed-session trace: {trace_error}", flush=True)
            raise
        finally:
            stop_guest_recording(env, recording_id, case / "desktop.mp4")
            try:
                make_review_video(case)
            except Exception as review_error:
                print(f"Could not build review video: {review_error}", flush=True)
        save_trace(env, session_id, case)
        score = env.evaluate()
        review_created = (case / "review.mp4").is_file()
        (case / "result.json").write_text(json.dumps({
            "score": score,
            "session_id": session_id,
            "example_id": args.example_id,
            "domain": args.domain,
            "snapshot_name": state["snapshot_name"],
            "review_video_exported": review_created,
        }, indent=2), encoding="utf-8")
        print(f"OSWorld score: {score}; artifacts: {case}", flush=True)
    finally:
        try:
            env.close()
        finally:
            restore_snapshot(state["vm_provider"], vm_path, state["snapshot_name"])


if __name__ == "__main__":
    main()
