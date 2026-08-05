"""CPU-fault control for the dockerized Sock Shop lab, shared by the CLI runner and
the live dashboard. The lab is not OTel-native and has no flagd, so the fault is
injected directly: detached `yes` busy-loops pegged inside the target container
(Rosetta-emulated images lack `timeout`/`seq`, hence one docker exec per hog), and
swept with plain `pkill yes` (busybox pkill -x is unreliable here).
"""
from __future__ import annotations

import subprocess

# Targets with an archived live investigation on record (runs/oss_live): 4/5 HITs
# across shipping, catalogue, payment, orders. Only vetted targets are injectable.
VETTED_TARGETS: tuple[str, ...] = ("shipping", "catalogue", "payment", "orders")

# The 13 app services of the lab (compose also runs otel-collector and load-test,
# which are infrastructure, not investigation candidates).
APP_SERVICES: tuple[str, ...] = (
    "front-end", "catalogue", "catalogue-db", "carts", "carts-db", "orders",
    "orders-db", "shipping", "queue-master", "rabbitmq", "payment", "user", "user-db",
)

DEFAULT_HOGS = 3


def inject_cpu(target: str, hogs: int = DEFAULT_HOGS, *, run=subprocess.run) -> None:
    """Peg ~`hogs` host cores inside `target` with detached busy-loops."""
    if target not in VETTED_TARGETS:
        raise ValueError(f"target {target!r} is not vetted; choose one of {VETTED_TARGETS}")
    for _ in range(hogs):
        run(["docker", "exec", "-d", target, "sh", "-c", "yes >/dev/null 2>&1"], check=True)


def clear_cpu(target: str, *, run=subprocess.run) -> None:
    """Sweep every hog in `target`; repeated because pkill can race fresh spawns."""
    for _ in range(3):
        run(["docker", "exec", target, "pkill", "yes"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sweep_all(*, run=subprocess.run) -> None:
    """Clear hogs from every vetted target; used before a run so a prior run's
    residue never contaminates the baseline."""
    for target in VETTED_TARGETS:
        clear_cpu(target, run=run)
