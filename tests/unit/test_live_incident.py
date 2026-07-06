"""Pure planning functions for live incidents: event payloads, truth docs,
run metadata. Orchestration is exercised with injected fakes; no network."""

from pathlib import Path

from labs.otel.live_incident import (
    SPECS,
    change_events_payload,
    run_live_incident,
    run_meta_doc,
    truth_doc,
)
from sentinel.fixtures.schemas import DerivedAlert, PrivateTruth

INJ = 1_751_700_300_000  # injection epoch ms


def test_specs_cover_control_and_hero():
    assert "payment_failure_live_001" in SPECS
    assert "kafka_queue_problems_live_001" in SPECS
    kafka = SPECS["kafka_queue_problems_live_001"]
    assert kafka.raw_flag_key == "kafkaQueueProblems"
    assert kafka.root_cause["service"] == "kafka"


def test_change_events_payload_has_culprit_and_decoys_with_timestamps():
    spec = SPECS["payment_failure_live_001"]
    events = change_events_payload(spec, INJ)
    assert all(e["eventType"] == "SentinelChange" for e in events)
    ids = {e["changeId"] for e in events}
    assert spec.culprit.id in ids and len(ids) == 1 + len(spec.decoys)
    culprit = next(e for e in events if e["changeId"] == spec.culprit.id)
    assert culprit["timestamp"] == INJ + spec.culprit.offset_s * 1000
    assert culprit["diffTouches"] == ",".join(spec.culprit.diff_touches)


def test_truth_doc_validates_as_private_truth():
    for spec in SPECS.values():
        truth = PrivateTruth.model_validate(truth_doc(spec))
        assert truth.culprit_change_id == spec.culprit.id
        assert len(truth.decoy_change_ids) == len(spec.decoys)


def test_run_meta_doc_window_and_alert_shape():
    spec = SPECS["kafka_queue_problems_live_001"]
    end = INJ + spec.soak_s * 1000
    meta = run_meta_doc(spec, INJ, end)
    assert meta["window_start_ms"] == INJ - spec.baseline_s * 1000
    assert meta["window_end_ms"] == end
    alert = DerivedAlert.model_validate(meta["alerts"][0])
    assert alert.starts_at_second == spec.baseline_s + 60


def test_run_live_incident_orchestration(tmp_path: Path):
    spec = SPECS["payment_failure_live_001"]
    calls = []

    class FakeFlagd:
        def set_flag_variant(self, key, variant):
            calls.append(("flag", key, variant))

    posted = []
    slept = []
    run_dir = run_live_incident(
        spec,
        tmp_path,
        flagd=FakeFlagd(),
        poster=lambda events: posted.append(events),
        sleeper=lambda s: slept.append(s),
        clock_ms=lambda: INJ,
    )
    assert ("flag", "paymentFailure", spec.variant) in calls
    assert len(posted[0]) == 1 + len(spec.decoys)
    assert slept == [spec.soak_s]
    assert (run_dir / "truth.json").exists()
    assert (run_dir / "run_meta.json").exists()


def test_targeting_patch_applied_when_spec_requires_it(tmp_path: Path):
    spec = SPECS["product_catalog_failure_live_001"]
    doc = {"flags": {"productCatalogFailure": {
        "defaultVariant": "off",
        "variants": {"on": True, "off": False},
        "targeting": {"if": [{"==": [{"var": "product_id"}, "OLJCESPC7Z"]}, "off", "off"]},
    }}}

    class FakeFlagd:
        def __init__(self):
            self.doc = doc
            self.written = None

        def set_flag_variant(self, key, variant):
            self.doc["flags"][key]["defaultVariant"] = variant

        def read(self):
            return self.doc

        def write(self, document):
            self.written = document

    flagd = FakeFlagd()
    run_live_incident(
        spec, tmp_path, flagd=flagd,
        poster=lambda events: None, sleeper=lambda s: None,
        clock_ms=lambda: INJ,
    )
    assert flagd.written["flags"]["productCatalogFailure"]["targeting"]["if"][1] == "on"
