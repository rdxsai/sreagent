"""Phase 1 of the two-phase flow: from a completed run's result.json, build the brief, elect
the primary action, journal it as proposed, post to Slack (and/or web pending), journal posted,
exit. Execution happens later in the server on human approval (sentinel/actions/api.py).

  python -m sentinel.actions.run --result runs/oss/<id>.result.json [--surface slack|web|both]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from sentinel.actions.brief import build_brief
from sentinel.actions.journal import ActionJournal
from sentinel.actions.slack import post_brief

_JOURNAL_DIR = Path("runs/actions")


def propose_from_result(result: dict, journal: ActionJournal, *, surface: str = "both",
                        ttl_s: float | None = None) -> dict:
    brief = build_brief(result)
    ttl_s = ttl_s if ttl_s is not None else float(os.environ.get("ACTION_APPROVAL_TTL_S", "3600"))

    # journal every proposed action (primary + notify alternatives are all audited)
    proposed = []
    all_actions = ([brief.primary_action] if brief.primary_action else []) + list(brief.alternatives)
    for a in all_actions:
        journal.proposed(a, expires_at=time.time() + ttl_s)
        proposed.append(a.id)

    posted = {"slack": None, "web": len([a for a in all_actions])}
    if surface in ("slack", "both") and brief.primary_action is not None:
        res = post_brief(brief)
        if res.get("ok"):
            journal.posted(brief.primary_action.id, "slack", res.get("ts", ""))
            posted["slack"] = res.get("ts")
    if surface in ("web", "both"):
        for a in all_actions:
            journal.posted(a.id, "web", f"/actions/{a.id}")
    return {"proposed": proposed, "posted": posted,
            "primary": brief.primary_action.id if brief.primary_action else None,
            "confident": brief.confident, "journal": str(journal.path())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Propose + post remediation from a completed run.")
    ap.add_argument("--result", required=True, help="runs/oss/<id>.result.json")
    ap.add_argument("--surface", default="both", choices=["slack", "web", "both"])
    ap.add_argument("--journal", default=None, help="journal path (default runs/actions/<run_id>.jsonl)")
    args = ap.parse_args(argv)

    result = json.loads(Path(args.result).read_text())
    run_id = result.get("run_id") or Path(args.result).stem
    journal = ActionJournal(args.journal or _JOURNAL_DIR / f"{run_id}.jsonl")
    out = propose_from_result(result, journal, surface=args.surface)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
