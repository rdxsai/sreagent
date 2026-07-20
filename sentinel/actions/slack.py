"""Slack outbound: post the brief as a Block Kit card with one Approve and one Deny button
carrying the action id, alternatives as text. Bot token from env. SLACK_DRY_RUN=1 logs the
payload instead of posting (dev + tests). Posting appends `posted` to the journal.
"""

from __future__ import annotations

import logging
import os

import httpx

from sentinel.actions.brief import Brief

logger = logging.getLogger("sentinel.actions.slack")
_API = "https://slack.com/api"


def _blocks(brief: Brief) -> list[dict]:
    lines = [f"*Incident:* {brief.symptom}",
             f"*Root cause:* `{brief.root_cause_service}`  ({brief.fault_type or 'unknown'})  "
             f"_graph: {brief.graph_source}_"]
    for r in brief.ranked:
        ev = r["evidence"][0] if r.get("evidence") else "(no citation)"
        lines.append(f"• `{r['service']}` — {ev}")
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Sentinel incident brief"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    p = brief.primary_action
    if p is not None:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": f"*Proposed remediation ({p.risk} risk, "
                               f"{'reversible' if p.reversible else 'IRREVERSIBLE'}):*\n"
                               f"{p.description}\n`{p.preview}`"}})
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"},
             "action_id": "approve", "value": p.id},
            {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Deny"},
             "action_id": "deny", "value": p.id},
        ]})
        if brief.alternatives:
            alt = "  •  ".join(f"{a.kind} {a.target_service}" for a in brief.alternatives)
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Alternatives: {alt}"}]})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "_No automated remediation proposed (low confidence). Notify-only._"}})
    return blocks


def post_brief(brief: Brief, *, channel: str | None = None) -> dict:
    """Returns {ok, ts, dry_run}. Dry-run logs and returns a synthetic ts."""
    channel = channel or os.environ.get("SLACK_CHANNEL_ID", "")
    payload = {"channel": channel, "blocks": _blocks(brief),
               "text": f"Incident: {brief.root_cause_service} ({brief.fault_type})"}
    if os.environ.get("SLACK_DRY_RUN") == "1" or not os.environ.get("SLACK_BOT_TOKEN"):
        logger.info("SLACK_DRY_RUN post to %s: %s", channel, payload["text"])
        return {"ok": True, "ts": "dry-run", "dry_run": True}
    r = httpx.post(f"{_API}/chat.postMessage",
                   headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
                   json=payload, timeout=20)
    d = r.json()
    return {"ok": bool(d.get("ok")), "ts": d.get("ts"), "dry_run": False, "error": d.get("error")}


def post_thread(ts: str, text: str, *, channel: str | None = None) -> None:
    """Post an outcome back to the brief's thread."""
    channel = channel or os.environ.get("SLACK_CHANNEL_ID", "")
    if os.environ.get("SLACK_DRY_RUN") == "1" or not os.environ.get("SLACK_BOT_TOKEN"):
        logger.info("SLACK_DRY_RUN thread reply: %s", text)
        return
    httpx.post(f"{_API}/chat.postMessage",
               headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
               json={"channel": channel, "thread_ts": ts, "text": text}, timeout=20)
