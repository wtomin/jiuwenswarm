"""One-time WorkSwarm installation and configured OSWorld VM snapshot."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import os
from pathlib import Path

from common import (
    install_workswarm,
    model_dotenv,
    new_environment,
    require_vm_cli,
    save_state,
    snapshot_exists,
    source_archive,
    take_snapshot,
    virtualbox_first_boot_recovery,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osworld-repo", type=Path, required=True)
    parser.add_argument("--workswarm-repo", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--vm-provider", choices=("virtualbox", "vmware"), default="virtualbox")
    parser.add_argument("--vm-path", type=Path, help="Optional .vbox or .vmx; VirtualBox can import the official VM automatically")
    parser.add_argument("--snapshot-name", default="workswarm_ready")
    parser.add_argument("--osworld-guest-password", default="password", help="Official Ubuntu VM user password, for first-boot service recovery")
    parser.add_argument("--include-untracked", action="store_true", help="Also sync untracked, non-ignored repository files")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-base", required=True)
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--api-key", help="API key value; visible in shell history and process listings")
    key.add_argument("--api-key-env", help="Host environment variable containing the API key")
    parser.add_argument("--model-provider", default="OpenAI")
    parser.add_argument("--endpoint-profile", default="openai")
    args = parser.parse_args()

    osworld_repo = args.osworld_repo.resolve()
    workswarm_repo = args.workswarm_repo.resolve()
    state_file = args.state_file.resolve()
    vm_path = args.vm_path.resolve() if args.vm_path else None
    if not osworld_repo.is_dir() or not workswarm_repo.is_dir():
        parser.error("OSWorld and WorkSwarm repositories must already exist")
    if vm_path and not vm_path.is_file():
        parser.error(f"VM path not found: {vm_path}")
    if args.vm_provider == "vmware" and not vm_path:
        parser.error("--vm-path with the official Ubuntu.vmx is required for VMware")
    if state_file.exists():
        parser.error(f"State file already exists; preparation is one-time: {state_file}")
    api_key = args.api_key if args.api_key is not None else os.environ.get(args.api_key_env)
    if not api_key:
        parser.error("API key is empty or its environment variable is unset")

    archive = source_archive(workswarm_repo, include_untracked=args.include_untracked)
    archive_hash = hashlib.sha256(archive).hexdigest()
    dotenv = model_dotenv(args.model, args.api_base, api_key, args.model_provider, args.endpoint_profile)
    require_vm_cli(args.vm_provider)
    recovery = (virtualbox_first_boot_recovery(osworld_repo, args.osworld_guest_password)
                if args.vm_provider == "virtualbox" and vm_path is None else nullcontext())
    with recovery:
        env = new_environment(osworld_repo, args.vm_provider, vm_path, "init_state")
    try:
        vm_path = Path(env.path_to_vm).resolve()
        if snapshot_exists(args.vm_provider, vm_path, args.snapshot_name):
            raise RuntimeError(f"Snapshot already exists: {args.snapshot_name}; choose another name explicitly")
        if snapshot_exists(args.vm_provider, vm_path, "network_ready"):
            env.snapshot_name = "network_ready"
            print("Resetting VM to network_ready snapshot", flush=True)
        env.reset()
        print(f"Installing WorkSwarm source ({len(archive):,} compressed bytes)", flush=True)
        install_workswarm(env, archive, dotenv)
        take_snapshot(args.vm_provider, vm_path, args.snapshot_name)
        state = {
            "schema_version": 1,
            "osworld_repo": str(osworld_repo),
            "vm_provider": args.vm_provider,
            "vm_path": str(vm_path),
            "snapshot_name": args.snapshot_name,
            "workswarm_source_sha256": archive_hash,
            "model": args.model,
        }
        save_state(state_file, state)
        print(f"Saved configured VM snapshot {args.snapshot_name} and state file {state_file}", flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
