"""CLI: `python -m bench.run adaptive_threshold --repeats 9 --out bench/out`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .arms import ARMS, available_arms
from .harness import run_workload
from .workloads import WORKLOADS, get_workload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench", description="Benchmark a workload across the COOL comparison arms."
    )
    parser.add_argument("workload", nargs="?", help="workload name (--list to see them)")
    parser.add_argument("--repeats", type=int, default=7, help="timed runs per arm")
    parser.add_argument("--warmup", type=int, default=2, help="discarded runs per arm")
    parser.add_argument("--out", default="bench/out", help="output directory")
    parser.add_argument("--arms", default="", help="comma-separated arm names to include")
    parser.add_argument("--list", action="store_true", help="list workloads and arms")
    parser.add_argument("--json-only", action="store_true", help="print JSON, write nothing")
    args = parser.parse_args(argv)

    if args.list or not args.workload:
        print("workloads:")
        for name, workload in sorted(WORKLOADS.items()):
            flag = " [COOL-relevant]" if workload.cool_relevant else ""
            print(f"  {name:<20} {workload.description}{flag}")
        print("\narms:")
        runnable, skipped = available_arms()
        for arm in runnable:
            print(f"  {arm.name:<20} ready      {arm.description}")
        for arm, reason in skipped:
            print(f"  {arm.name:<20} skipped    {reason}")
        print(
            "\nConfigure remote arms with BENCH_GRAVITON_HOST, BENCH_COOL_HOST,"
            "\nBENCH_HYBRID_HOST and BENCH_SSH_KEY."
        )
        return 0

    workload = get_workload(args.workload)
    selected = None
    if args.arms:
        wanted = {name.strip() for name in args.arms.split(",")}
        selected = [arm for arm in ARMS() if arm.name in wanted]
        if not selected:
            parser.error(f"no arms matched {sorted(wanted)}")

    report = run_workload(
        workload, selected, repeats=args.repeats, warmup=args.warmup
    )

    if args.json_only:
        print(report.to_json())
        return 0

    print(report.to_markdown())
    json_path, md_path = report.write(Path(args.out))
    print(f"\nwrote {json_path}\nwrote {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
