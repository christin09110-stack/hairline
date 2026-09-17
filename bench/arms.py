"""The benchmark arms, and the honesty that has to go with them.

The trap: the stock PyPI aarch64 wheel **already
ships KleidiCV 26.03**, confirmed by `strings` on `cv2.abi3.so`. Most COOL
entries will benchmark COOL-on-Graviton against pip-on-x86, get a big number,
and be measuring the architecture rather than COOL. Arm B below is a pip wheel
on Graviton precisely so the comparison isolates what COOL actually adds, and
every report prints `kleidicv_disclosure` saying so.

Arms are declared here with their hourly cost so cost-per-1000-frames is
computed from the same table judges can check against AWS pricing.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

# us-east-1 on-demand, from the AWS Price List API, queried 2026-09-16
# COOL software fees come from the Marketplace
# listing's usage-cost table (2.2) and are additive to the EC2 rate.
EC2_USD_PER_HOUR: dict[str, float] = {
    "t4g.micro": 0.0084,
    "t4g.small": 0.0168,
    "t4g.medium": 0.0336,
    "c7g.medium": 0.0363,
    "c7g.large": 0.0725,
    "c7g.xlarge": 0.1450,
    "c7g.2xlarge": 0.2900,
    "c8g.large": 0.0798,
    "c8g.xlarge": 0.1596,
    "c8g.2xlarge": 0.3192,
    "c8g.4xlarge": 0.6384,
    "c6i.large": 0.0850,
    "c7i.large": 0.0893,
    "c7i.4xlarge": 0.7140,
    "t3.micro": 0.0104,
}

COOL_USD_PER_HOUR: dict[str, float] = {
    "c8g.large": 0.01,
    "c8g.xlarge": 0.01,
    "c8g.2xlarge": 0.02,
    "c8g.4xlarge": 0.04,
    "c8g.8xlarge": 0.08,
    "r8g.xlarge": 0.01,
    "r8g.24xlarge": 0.36,
    "m8g.16xlarge": 0.24,
    "m8g.48xlarge": 0.80,
}


@dataclass
class Arm:
    """One place a workload can run.

    `host` empty means "this machine". Anything else runs over ssh, and the arm
    reports itself unavailable when the host is not configured or not reachable,
    so `run_workload` can skip it cleanly rather than failing the whole run.
    """

    name: str
    description: str
    instance_type: str = "local"
    host: str = ""  # user@host for ssh; empty means local
    python: str = "python3"  # interpreter path on the remote
    ssh_key: str = ""
    cool: bool = False
    env: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    # ---- cost ------------------------------------------------------------
    @property
    def usd_per_hour(self) -> float | None:
        if self.instance_type == "local":
            return None
        base = EC2_USD_PER_HOUR.get(self.instance_type)
        if base is None:
            return None
        return base + (COOL_USD_PER_HOUR.get(self.instance_type, 0.0) if self.cool else 0.0)

    def cost_per_1k_frames(self, ms_per_frame: float) -> float | None:
        rate = self.usd_per_hour
        if rate is None or ms_per_frame <= 0:
            return None
        return rate / 3_600_000.0 * ms_per_frame * 1000.0

    @property
    def is_local(self) -> bool:
        return self.instance_type == "local"

    # ---- availability -----------------------------------------------------
    def availability(self) -> tuple[bool, str]:
        if self.is_local:
            return True, "local"
        if not self.host:
            env_var = "BENCH_" + self.name.split("-")[0].upper() + "_HOST"
            if self.name == "graviton-cool":
                env_var = "BENCH_COOL_HOST"
            elif self.name == "hybrid-x86":
                env_var = "BENCH_HYBRID_HOST"
            return False, f"{env_var} is not set; no instance to run on"
        if not shutil.which("ssh"):
            return False, "ssh is not installed on this machine"
        probe = subprocess.run(
            [*self.ssh_command(), "true"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if probe.returncode != 0:
            detail = probe.stderr.decode(errors="replace").strip().splitlines()
            return False, f"ssh to {self.host} failed: {detail[-1] if detail else 'no route'}"
        return True, "reachable"

    def ssh_command(self) -> list[str]:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
               "-o", "StrictHostKeyChecking=accept-new"]
        if self.ssh_key:
            cmd += ["-i", self.ssh_key]
        cmd.append(self.host)
        return cmd

    def run_python(self, script: str, timeout: float = 900.0) -> str:
        """Execute `script` with this arm's interpreter and return its stdout."""
        environment = {**os.environ, **self.env}
        if not self.host:
            result = subprocess.run(
                [self.python or "python3", "-c", script],
                capture_output=True, text=True, timeout=timeout, check=False,
                env=environment,
            )
        else:
            exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in self.env.items())
            remote = f"{exports} {shlex.quote(self.python)} -c {shlex.quote(script)}".strip()
            result = subprocess.run(
                [*self.ssh_command(), remote],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"arm {self.name}: remote python exited {result.returncode}\n"
                f"{result.stderr.strip()[-2000:]}"
            )
        return result.stdout

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "instance_type": self.instance_type,
            "host": self.host or "local",
            "cool": self.cool,
            "usd_per_hour": self.usd_per_hour,
            "notes": self.notes,
        }


def local_arm() -> Arm:
    """This machine, whatever it is. Always available, so a bench run always works."""
    machine = platform.machine()
    return Arm(
        name=f"local-{machine}-pip",
        description=f"this machine ({machine}), stock pip wheel",
        instance_type="local",
        python=os.environ.get("BENCH_LOCAL_PYTHON", "") or _default_python(),
        notes="no cost attributed: not a billed instance",
    )


def _default_python() -> str:
    import sys

    return sys.executable or "python3"


# The four arms of the COOL comparison. Hosts come from the environment so the
# harness runs with none of them configured.
def ARMS() -> list[Arm]:  # a factory, not a constant: it reads the environment each call
    arms = [local_arm()]

    graviton_host = os.environ.get("BENCH_GRAVITON_HOST", "")
    arms.append(
        Arm(
            name="graviton-pip",
            description="Graviton, stock pip wheel",
            instance_type=os.environ.get("BENCH_GRAVITON_TYPE", "c8g.large"),
            host=graviton_host,
            python=os.environ.get("BENCH_GRAVITON_PYTHON", "python3"),
            ssh_key=os.environ.get("BENCH_SSH_KEY", ""),
            notes=(
                "ALREADY CONTAINS KleidiCV 26.03. This is not an unaccelerated "
                "baseline and the report must not present it as one."
            ),
        )
    )

    cool_host = os.environ.get("BENCH_COOL_HOST", "")
    arms.append(
        Arm(
            name="graviton-cool",
            description="Graviton, COOL AMI",
            instance_type=os.environ.get("BENCH_COOL_TYPE", "c8g.large"),
            host=cool_host,
            python=os.environ.get(
                "BENCH_COOL_PYTHON", "/opt/cool/venvs/python_3.12/bin/python"
            ),
            ssh_key=os.environ.get("BENCH_SSH_KEY", ""),
            cool=True,
            # COOL's venvs do not contain cv2. Its `activate` script adds the build to
            # PYTHONPATH and the C++ SDK to LD_LIBRARY_PATH; without both, the venv's
            # python raises ModuleNotFoundError.
            env={
                "PYTHONPATH": "/opt/cool/python_3.12/site-packages/cv2/python-3.12",
                "LD_LIBRARY_PATH": "/opt/cool/cpp_sdk/lib",
            },
            notes=(
                "COOL-Graviton4-v2 AMI. Measured 2026-09-16: OpenCV 4.14.0-pre, "
                "KleidiCV 0.7.0 HAL, -mcpu=neoverse-v2, TBB, Arm Performance Libraries"
            ),
        )
    )

    hybrid_host = os.environ.get("BENCH_HYBRID_HOST", "")
    if hybrid_host:
        arms.append(
            Arm(
                name="hybrid-x86",
                description="x86 arm of a documented hybrid architecture",
                instance_type=os.environ.get("BENCH_HYBRID_TYPE", "c7i.large"),
                host=hybrid_host,
                python=os.environ.get("BENCH_HYBRID_PYTHON", "python3"),
                ssh_key=os.environ.get("BENCH_SSH_KEY", ""),
                notes="cost-matched x86 comparison, IPP HAL",
            )
        )
    return arms


def available_arms(arms: list[Arm] | None = None) -> tuple[list[Arm], list[tuple[Arm, str]]]:
    """Split arms into (runnable, [(skipped, reason)])."""
    runnable, skipped = [], []
    for arm in arms if arms is not None else ARMS():
        ready, reason = arm.availability()
        if ready:
            runnable.append(arm)
        else:
            skipped.append((arm, reason))
    return runnable, skipped


KLEIDICV_DISCLOSURE = (
    "The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 "
    "(verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV "
    "(ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, "
    "not a plain one. Any speedup attributed to COOL is measured against that, "
    "and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL."
)
