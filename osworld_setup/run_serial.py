"""Run the full OSWorld test_all manifest in order, recording per-case wall time."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

from common import load_state


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def choose_cases(args: argparse.Namespace, osworld_repo: Path) -> tuple[list[tuple[str, str]], str | None, int | None]:
    if args.case:
        chosen: list[tuple[str, str]] = []
        for value in args.case:
            parts = value.split("/", 1)
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"Expected --case DOMAIN/EXAMPLE_ID, got {value!r}")
            chosen.append((parts[0], parts[1]))
        if len(chosen) != len(set(chosen)):
            raise ValueError("Duplicate --case entries would overwrite artifacts")
        manifest_path = None
        seed = None
    else:
        manifest_path = args.manifest if args.manifest.is_absolute() else osworld_repo / args.manifest
        manifest_path = manifest_path.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pool = [(domain, example_id) for domain, ids in manifest.items() for example_id in ids]
        if len(pool) != len(set(pool)):
            raise ValueError(f"Manifest contains duplicate cases: {manifest_path}")
        count = len(pool) if args.count == 0 else args.count
        if count < 1 or count > len(pool):
            raise ValueError(f"--count must be 0 or within 1..{len(pool)}")
        seed = args.seed
        chosen = random.Random(seed).sample(pool, count) if seed is not None else pool[:count]
    for domain, example_id in chosen:
        example = osworld_repo / "evaluation_examples" / "examples" / domain / f"{example_id}.json"
        if not example.is_file():
            raise FileNotFoundError(f"OSWorld example does not exist: {example}")
    return chosen, str(manifest_path) if manifest_path else None, seed


def run_case(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as exc:
            message = f"Could not start run_local.py: {exc}\n"
            log.write(message)
            print(message, end="", flush=True)
            return 127
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        return process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("evaluation_examples/test_all.json"))
    parser.add_argument("--count", type=int, default=0, help="First N cases in manifest order; 0 means all (default)")
    parser.add_argument("--seed", type=int, help="Select --count cases randomly instead of taking the first N")
    parser.add_argument("--case", action="append", help="Explicit DOMAIN/EXAMPLE_ID; repeat for a serial list")
    parser.add_argument("--timeout", type=int, default=1800, help="Agent chat limit per case in seconds")
    parser.add_argument("--virtualbox-nat", action="store_true",
                        help="Pass NAT on VirtualBox NIC 1 to each run_local.py process")
    parser.add_argument("--plan-only", action="store_true", help="Save the selection without starting a VM")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()
    if args.seed is not None and args.count == 0:
        parser.error("--seed requires --count greater than 0; full runs follow manifest order")

    state_file = args.state_file.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"Output directory is not empty: {output}")
    state = load_state(state_file)
    if args.virtualbox_nat and state["vm_provider"] != "virtualbox":
        parser.error("--virtualbox-nat requires a VirtualBox VM")
    osworld_repo = Path(state["osworld_repo"])
    chosen, manifest, seed = choose_cases(args, osworld_repo)
    output.mkdir(parents=True, exist_ok=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=osworld_repo, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    plan = {
        "created_at_utc": utc_now(),
        "osworld_revision": revision,
        "manifest": manifest,
        "seed": seed,
        "snapshot_name": state["snapshot_name"],
        "timeout_seconds_per_case": args.timeout,
        "virtualbox_nat": args.virtualbox_nat,
        "cases": [{"domain": domain, "example_id": example_id} for domain, example_id in chosen],
    }
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Selected {len(chosen)} case(s) in {'random sample' if seed is not None else 'manifest'} order; "
          f"seed={seed}; plan={output / 'plan.json'}", flush=True)
    if args.plan_only:
        return

    runner = Path(__file__).with_name("run_local.py")
    results: list[dict] = []
    total_started = time.monotonic()
    with (output / "serial_results.jsonl").open("w", encoding="utf-8") as stream:
        for index, (domain, example_id) in enumerate(chosen, start=1):
            case_dir = output / domain / example_id
            command = [
                sys.executable, str(runner), "--state-file", str(state_file),
                "--output", str(output), "--domain", domain,
                "--example-id", example_id, "--timeout", str(args.timeout),
            ]
            if args.virtualbox_nat:
                command.append("--virtualbox-nat")
            print(f"[{index}/{len(chosen)}] {domain}/{example_id}", flush=True)
            started_at = utc_now()
            started = time.monotonic()
            exit_code = run_case(command, case_dir / "driver.log")
            elapsed = round(time.monotonic() - started, 3)
            result_file = case_dir / "result.json"
            score = None
            if result_file.is_file():
                score = json.loads(result_file.read_text(encoding="utf-8")).get("score")
            record = {
                "domain": domain,
                "example_id": example_id,
                "started_at_utc": started_at,
                "ended_at_utc": utc_now(),
                "wall_seconds": elapsed,
                "exit_code": exit_code,
                "score": score,
                "result_file": str(result_file) if result_file.is_file() else None,
            }
            results.append(record)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"[{index}/{len(chosen)}] exit={exit_code} score={score} wall={elapsed}s", flush=True)
            if exit_code != 0 and args.stop_on_error:
                break

    successful = [record for record in results if record["exit_code"] == 0]
    summary = {
        "planned": len(chosen),
        "completed": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "total_wall_seconds": round(time.monotonic() - total_started, 3),
        "mean_case_wall_seconds": round(statistics.mean(r["wall_seconds"] for r in results), 3) if results else None,
        "scores": [record["score"] for record in results],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Serial summary: {output / 'summary.json'}", flush=True)
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
