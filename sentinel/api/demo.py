"""Demo API: scenario metadata + a live SSE stream of an agent investigation.

GET /demo/scenarios            -> the six scenarios with human descriptions
GET /demo/run/{scenario_id}    -> Server-Sent Events of a live agent run

The descriptions here are for humans only and are never given to the agent; the
agent receives only the symptom + firing alert from the public manifest and reads
the recorded telemetry through its tools.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import anthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from sentinel.agent.events import EventSink
from sentinel.agent.runner import investigate
from sentinel.tools.store import FixtureStore

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"

# Plain-English descriptions of each recorded incident, shown to humans only.
DESCRIPTIONS: dict[str, dict[str, str]] = {
    "payment_failure_001": {
        "title": "Payment charge failures",
        "text": (
            "This is the checkout flow of an online shop where shoppers pay by card. During the window, card "
            "charges began failing and checkout attempts errored out. The hidden cause is a configuration change "
            "to the payment service's charge handler."
        ),
    },
    "payment_unreachable_001": {
        "title": "Payment service unreachable",
        "text": (
            "The same shop's checkout. Here the payment service itself is healthy, but checkout can no longer reach "
            "it: the calls from checkout to payment fail to connect. The hidden cause is a routing change in checkout "
            "that points it at the wrong payment address."
        ),
    },
    "product_catalog_failure_001": {
        "title": "Product catalog lookups failing",
        "text": (
            "The shop's browse path: the storefront lists products by calling the product-catalog service. During "
            "the window, product lookups started erroring, breaking listings and anything needing product details. "
            "The hidden cause is a change to the product-catalog lookup path."
        ),
    },
    "cart_failure_001": {
        "title": "Cart failures",
        "text": (
            "The shopping-cart service backs add-to-cart and cart retrieval, keeping items in a cache. During the "
            "window, cart operations began failing. The hidden cause is a change to the cart's storage-backend "
            "setting that broke its connection to the cache."
        ),
    },
    "ad_manual_gc_001": {
        "title": "Ad service slowdown (GC pauses)",
        "text": (
            "Every page renders ads via the ad service. During the window the ads, and the pages waiting on them, "
            "became slow, though nothing errored. The hidden cause is a change that triggers heavy garbage-collection "
            "pauses in the ad service: a latency fault with no errors."
        ),
    },
    "ad_high_cpu_001": {
        "title": "Ad service CPU saturation",
        "text": (
            "Every page renders ads via the ad service. During the window the ad service's CPU saturated, pegging its "
            "cores, while latency and error rates stayed roughly normal. The hidden cause is a change that makes the "
            "ad service burn CPU: a resource-saturation fault that error- and latency-only views miss."
        ),
    },
}

router = APIRouter()


def _manifest(scenario_id: str) -> dict | None:
    path = FIXTURES / scenario_id / "public" / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/demo/scenarios")
def demo_scenarios() -> dict:
    scenarios = []
    for sid, meta in DESCRIPTIONS.items():
        manifest = _manifest(sid)
        if manifest is None:
            continue
        alerts = manifest.get("alerts", [])
        first = alerts[0] if alerts else {}
        scenarios.append(
            {
                "id": sid,
                "title": meta["title"],
                "description": meta["text"],
                "symptom": manifest.get("symptom", ""),
                "alert": first.get("alertname"),
                "alert_at_second": first.get("starts_at_second"),
            }
        )
    return {"scenarios": scenarios}


def _hydrate_changes(store: FixtureStore) -> dict[str, dict]:
    """Map change id -> its human-readable content for the report (no opaque ids in the UI)."""
    out: dict[str, dict] = {}
    for c in store.list_changes():
        out[c.id] = {
            "id": c.id,
            "service": getattr(c, "service", None),
            "summary": getattr(c, "summary", None),
            "time": getattr(c, "time", None),
            "diff_touches": list(getattr(c, "diff_touches", []) or []),
        }
    return out


def _run_agent(sink: EventSink, store: FixtureStore, symptom: str, alertnames: list[str]) -> None:
    try:
        sink.emit("status", message="starting investigation")
        result = investigate(anthropic.Anthropic(), store, symptom, alertnames, events=sink)
        # Emit a single enriched report: change ids resolved to real descriptions, plus
        # the blast radius from the subagent findings, so the UI can show a real RCA.
        report = result.report or {}
        by_id = _hydrate_changes(store)
        culprit_id = report.get("culprit_change_id")
        sink.emit(
            "report",
            agent="manager",
            root_cause=report.get("root_cause"),
            culprit=by_id.get(culprit_id, {"id": culprit_id}) if culprit_id else None,
            ruled_out=[by_id.get(cid, {"id": cid}) for cid in report.get("ruled_out_change_ids", [])],
            evidence=report.get("evidence", []),
            timeline=report.get("timeline", []),
            findings=result.findings,
            subagents=result.subagents,
        )
    except Exception as exc:  # surface any failure to the UI rather than hanging
        sink.emit("error", message=f"{type(exc).__name__}: {exc}"[:300])
    finally:
        sink.emit("done")
        sink.close()


async def _sse(scenario_id: str):
    manifest = _manifest(scenario_id)
    if manifest is None:
        yield f"data: {json.dumps({'type': 'error', 'message': 'unknown scenario'})}\n\n"
        return
    symptom = manifest.get("symptom", "")
    alertnames = [a.get("alertname") for a in manifest.get("alerts", []) if a.get("alertname")]
    store = FixtureStore(FIXTURES / scenario_id / "public")
    sink = EventSink()
    threading.Thread(target=_run_agent, args=(sink, store, symptom, alertnames), daemon=True).start()

    loop = asyncio.get_event_loop()
    while True:
        event = await loop.run_in_executor(None, sink.get)  # blocking queue read off the event loop
        if event is None:
            break
        yield f"data: {json.dumps(event)}\n\n"


@router.get("/demo/run/{scenario_id}")
async def demo_run(scenario_id: str) -> StreamingResponse:
    return StreamingResponse(
        _sse(scenario_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
