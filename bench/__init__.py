"""bench - the COOL benchmark harness.

Four arms, one workload, honest numbers. See `bench/README.md`.
"""

from .arms import ARMS, Arm, available_arms, local_arm
from .harness import ArmResult, BenchReport, run_workload
from .workloads import WORKLOADS, Workload, get_workload

__all__ = [
    "ARMS",
    "WORKLOADS",
    "Arm",
    "ArmResult",
    "BenchReport",
    "Workload",
    "available_arms",
    "get_workload",
    "local_arm",
    "run_workload",
]
