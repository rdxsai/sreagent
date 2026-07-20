"""Considered-vs-executed + near-miss report, folded from an action journal. The audit the
guide requires: every proposed action's terminal state, what executed, and every near-miss.

  python -m sentinel.actions.report runs/actions/<run_id>.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinel.actions.journal import ActionJournal


def build_report(journal: ActionJournal) -> dict:
    fold = journal.fold()
    events = journal.events()
    considered = len(fold)
    executed = sum(1 for st in fold.values() if st.status in ("done", "failed"))
    approved = sum(1 for st in fold.values() if st.status in ("approved", "executing", "done", "failed"))
    near_misses = [e for e in events if e["kind"] == "near_miss"]
    by_status: dict[str, int] = {}
    for st in fold.values():
        by_status[st.status] = by_status.get(st.status, 0) + 1
    # safety invariant: no execute_result without a prior approved for the same action
    approved_ids = {e["action_id"] for e in events if e["kind"] == "approved"}
    unapproved_exec = [e["action_id"] for e in events
                       if e["kind"] == "execute_result" and e["action_id"] not in approved_ids]
    return {
        "considered": considered,
        "approved": approved,
        "executed": executed,
        "near_miss_count": len(near_misses),
        "by_status": by_status,
        "unapproved_executions": unapproved_exec,   # MUST be empty
        "safe": len(unapproved_exec) == 0,
        "near_misses": [{"action_id": e["action_id"], "attempted": e.get("attempted"),
                         "by": e.get("by"), "why": e.get("why")} for e in near_misses],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Considered-vs-executed + near-miss report.")
    ap.add_argument("journal")
    args = ap.parse_args(argv)
    print(json.dumps(build_report(ActionJournal(Path(args.journal))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
