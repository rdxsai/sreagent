"""Parse an RCAEval case directory into a typed handle.

A case directory is named `{system}_{service}_{fault}_{instance}`; the name is
the ground-truth source (root-cause service and fault type). Service names may
contain hyphens (Train Ticket) but not underscores, so split on underscores:
first token is the system, last is the instance, second-to-last is the fault,
and the remainder is the service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def parse_case_name(name: str) -> tuple[str, str, str, str]:
    parts = name.split("_")
    if len(parts) < 4:
        raise ValueError(f"unexpected RCAEval case name: {name!r}")
    system = parts[0]
    instance = parts[-1]
    fault = parts[-2]
    service = "_".join(parts[1:-2])
    return system, service, fault, instance


@dataclass(frozen=True)
class RCAEvalCase:
    case_id: str
    system: str
    service: str
    fault: str
    instance: str
    raw_dir: Path

    @property
    def metrics_path(self) -> Path:
        return self.raw_dir / "metrics.json"

    @property
    def logs_path(self) -> Path:
        return self.raw_dir / "logs.csv"

    @property
    def traces_path(self) -> Path:
        return self.raw_dir / "traces.csv"

    @property
    def inject_time_path(self) -> Path:
        return self.raw_dir / "inject_time.txt"


def load_case(raw_dir: Path) -> RCAEvalCase:
    raw_dir = Path(raw_dir)
    system, service, fault, instance = parse_case_name(raw_dir.name)
    return RCAEvalCase(
        case_id=raw_dir.name,
        system=system,
        service=service,
        fault=fault,
        instance=instance,
        raw_dir=raw_dir,
    )


def read_inject_time(path: Path) -> int:
    return int(float(Path(path).read_text(encoding="utf-8").strip()))
