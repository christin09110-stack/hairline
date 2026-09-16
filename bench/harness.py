"""Run a workload across arms, report median / p95 latency, throughput and cost.

The harness runs with **only the local arm present**, skipping every remote arm
with a stated reason, so a product can benchmark its pipeline before any EC2
instance exists. That is the point: nobody should have to launch a c8g to find
out whether their workload is worth benchmarking at all.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .arms import KLEIDICV_DISCLOSURE, Arm, available_arms
from .workloads import Workload

# The script shipped to each arm. Timing happens where the work happens, so ssh
# latency never lands in a measurement.
RUNNER_TEMPLATE = '''
import json, platform, sys, time
import cv2, numpy as np

{setup}

def _once():
{run_indented}

# Warm-up runs are discarded: first-touch page faults and lazy dispatch tables
# would otherwise show up as a slow first sample on every arm.
for _ in range({warmup}):
    _once()

samples = []
for _ in range({repeats}):
    t0 = time.perf_counter()
    _once()
    samples.append((time.perf_counter() - t0) * 1000.0)

build = cv2.getBuildInformation()
def _field(key):
    for line in build.splitlines():
        s = line.strip()
        if s.startswith(key + ":"):
            return s.split(":", 1)[1].strip()
    return ""

print("@@BENCH@@" + json.dumps({{
    "samples": samples,
    "opencv_version": cv2.__version__,
    "numpy_version": np.__version__,
    "python_version": platform.python_version(),
    "machine": platform.machine(),
    "threads": cv2.getNumThreads(),
    "custom_hal": _field("Custom HAL"),
    "baseline": _field("Baseline"),
    "kleidicv": "kleidicv" in build.lower(),
    "ipp": "ipp" in build.lower(),
}}))
'''


@dataclass
class ArmResult:
    arm: Arm
    ok: bool
    samples_ms: list[float] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    skipped_reason: str = ""

    # ---- statistics -------------------------------------------------------
    @property
    def median_ms(self) -> float | None:
        return statistics.median(self.samples_ms) if self.samples_ms else None

    @property
    def p95_ms(self) -> float | None:
        if not self.samples_ms:
            return None
        ordered = sorted(self.samples_ms)
        # Nearest-rank p95. With few samples this is the slowest run, which is
        # the honest answer rather than an interpolated one.
        index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered)) - 1))
        return ordered[index]

    @property
    def min_ms(self) -> float | None:
        return min(self.samples_ms) if self.samples_ms else None

    @property
    def stdev_ms(self) -> float | None:
        return statistics.stdev(self.samples_ms) if len(self.samples_ms) > 1 else None

    def fps(self, frames_per_run: int) -> float | None:
        median = self.median_ms
        if not median:
            return None
        return frames_per_run * 1000.0 / median

    def cost_per_1k_frames(self, frames_per_run: int) -> float | None:
        median = self.median_ms
        if median is None:
            return None
        return self.arm.cost_per_1k_frames(median / max(1, frames_per_run))

    def to_dict(self, frames_per_run: int = 1) -> dict[str, Any]:
        def r(v: float | None, n: int = 3) -> float | None:
            return None if v is None else round(v, n)

        return {
            "arm": self.arm.to_dict(),
            "ok": self.ok,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
            "runs": len(self.samples_ms),
            "latency_ms": {
                "median": r(self.median_ms),
                "p95": r(self.p95_ms),
                "min": r(self.min_ms),
                "stdev": r(self.stdev_ms),
            },
            "throughput_fps": r(self.fps(frames_per_run), 2),
            "cost_per_1k_frames_usd": r(self.cost_per_1k_frames(frames_per_run), 6),
            "env": dict(self.env),
            "samples_ms": [round(s, 4) for s in self.samples_ms],
        }


@dataclass
class BenchReport:
    workload: Workload
    results: list[ArmResult]
    repeats: int
    warmup: int
    started_at: str
    duration_s: float

    # ---- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "workload": self.workload.to_dict(),
            "repeats": self.repeats,
            "warmup": self.warmup,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 2),
            "kleidicv_disclosure": KLEIDICV_DISCLOSURE,
            "arms": [r.to_dict(self.workload.frames_per_run) for r in self.results],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        w = self.workload
        lines = [
            f"# Benchmark: {w.name}",
            "",
            w.description,
            "",
            f"`{self.repeats}` timed runs per arm after `{self.warmup}` warm-up runs, "
            f"{self.started_at}.",
            "",
            "| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |",
            "|---|---|---:|---:|---:|---:|---|---|",
        ]
        for result in self.results:
            if not result.ok:
                reason = result.skipped_reason or result.error or "unavailable"
                lines.append(
                    f"| {result.arm.name} | {result.arm.instance_type} | — | — | — | — | — | "
                    f"_skipped: {reason}_ |"
                )
                continue
            payload = result.to_dict(w.frames_per_run)
            cost = payload["cost_per_1k_frames_usd"]
            lines.append(
                "| {name} | {inst} | {median} | {p95} | {fps} | {cost} | {cv} | {hal} |".format(
                    name=result.arm.name,
                    inst=result.arm.instance_type,
                    median=payload["latency_ms"]["median"],
                    p95=payload["latency_ms"]["p95"],
                    fps=payload["throughput_fps"],
                    cost="—" if cost is None else f"${cost:.6f}",
                    cv=result.env.get("opencv_version", "?"),
                    hal=result.env.get("custom_hal", "?") or "?",
                )
            )

        baseline = self._speedup_table()
        if baseline:
            lines += ["", "## Speedup against `graviton-pip`", "",
                      "| Arm | median ms | speedup |", "|---|---:|---:|"]
            lines += baseline

        lines += [
            "",
            "## What this comparison does and does not show",
            "",
            KLEIDICV_DISCLOSURE,
            "",
        ]
        failures = [r for r in self.results if not r.ok and r.error]
        if failures:
            lines += ["## Failures", ""]
            lines += [f"- **{r.arm.name}**: {r.error}" for r in failures]
            lines.append("")
        return "\n".join(lines)

    def _speedup_table(self) -> list[str]:
        reference = next(
            (r for r in self.results if r.ok and r.arm.name == "graviton-pip"), None
        )
        if reference is None or not reference.median_ms:
            return []
        rows = []
        for result in self.results:
            if not result.ok or not result.median_ms:
                continue
            rows.append(
                f"| {result.arm.name} | {result.median_ms:.2f} | "
                f"{reference.median_ms / result.median_ms:.2f}x |"
            )
        return rows

    def write(self, out_dir: Path | str) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "").replace("-", "")[:15]
        json_path = out / f"{self.workload.name}-{stamp}.json"
        md_path = out / f"{self.workload.name}-{stamp}.md"
        json_path.write_text(self.to_json())
        md_path.write_text(self.to_markdown())
        return json_path, md_path


def _indent(source: str, spaces: int = 4) -> str:
    body = source.strip("\n")
    if not body.strip():
        return " " * spaces + "pass"
    return "\n".join(" " * spaces + line if line.strip() else line
                     for line in body.splitlines())


def build_runner(workload: Workload, repeats: int, warmup: int) -> str:
    return RUNNER_TEMPLATE.format(
        setup=workload.setup_src.strip("\n"),
        run_indented=_indent(workload.run_src),
        repeats=repeats,
        warmup=warmup,
    )


def run_workload(
    workload: Workload,
    arms: list[Arm] | None = None,
    *,
    repeats: int = 7,
    warmup: int = 2,
    timeout: float = 1800.0,
) -> BenchReport:
    """Run `workload` on every reachable arm. Unreachable arms are skipped, not fatal."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    runnable, skipped = available_arms(arms)
    script = build_runner(workload, repeats, warmup)
    started = datetime.now(UTC).isoformat()
    clock = time.perf_counter()

    results: list[ArmResult] = [
        ArmResult(arm=arm, ok=False, skipped_reason=reason) for arm, reason in skipped
    ]

    for arm in runnable:
        try:
            stdout = arm.run_python(script, timeout=timeout)
            payload = _parse(stdout)
            samples = [float(s) for s in payload.pop("samples")]
            results.append(ArmResult(arm=arm, ok=True, samples_ms=samples, env=payload))
        except Exception as exc:  # one bad arm must not lose the other arms' numbers
            results.append(ArmResult(arm=arm, ok=False, error=str(exc)[:1500]))

    order = {"local": 0, "graviton-pip": 1, "graviton-cool": 2, "hybrid-x86": 3}
    results.sort(key=lambda r: order.get(r.arm.name.split("-")[0], 9)
                 if r.arm.name.startswith("local") else order.get(r.arm.name, 9))

    return BenchReport(
        workload=workload,
        results=results,
        repeats=repeats,
        warmup=warmup,
        started_at=started,
        duration_s=time.perf_counter() - clock,
    )


def _parse(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith("@@BENCH@@"):
            return json.loads(line[len("@@BENCH@@"):])
    raise RuntimeError(f"no benchmark payload in output:\n{stdout[-1500:]}")
