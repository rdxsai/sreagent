"""Live eval plumbing: prompt building from run_meta and run-dir loading.
The agent loop itself is not exercised here (integration territory)."""

import json
from pathlib import Path

from sentinel_tool_eval.live import build_live_prompt, load_run

META = {
    "scenario_id": "kafka_queue_problems_live_001",
    "window_start_ms": 1_751_700_000_000,
    "window_end_ms": 1_751_700_600_000,
    "symptom": "Order processing is delayed.",
    "alerts": [
        {
            "alertname": "OrderProcessingLag", "severity": "critical",
            "starts_at_second": 360, "labels": {}, "annotations": {"summary": "lag detected"},
            "value": 1.0, "expr": "live:kafkaQueueProblems", "fingerprint": "live-k",
        }
    ],
}


def test_build_live_prompt_contains_symptom_alerts_and_window():
    prompt = build_live_prompt(META)
    assert "Symptom: Order processing is delayed." in prompt
    assert "OrderProcessingLag (critical) at second 360" in prompt
    assert "seconds 0 to 600" in prompt
    assert "report tool" in prompt


def test_load_run_reads_meta_and_truth(tmp_path: Path):
    truth = {
        "scenario_id": "kafka_queue_problems_live_001",
        "injection": {"raw_flag_key": "kafkaQueueProblems", "variant": "on", "enabled_at_second": 300},
        "root_cause": {"kind": "service", "type": "queue_backpressure", "service": "kafka",
                       "caller": None, "callee": None},
        "culprit_change_id": "chg_2003",
        "expected_evidence": ["kafka consumer lag degrades after onset"],
        "decoy_change_ids": ["chg_2001"],
    }
    (tmp_path / "run_meta.json").write_text(json.dumps(META), encoding="utf-8")
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    meta, loaded_truth = load_run(tmp_path)
    assert meta["scenario_id"] == "kafka_queue_problems_live_001"
    assert loaded_truth.culprit_change_id == "chg_2003"
