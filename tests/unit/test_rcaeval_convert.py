from pathlib import Path

from sentinel.fixtures.replay import load_public_fixture
from sentinel.tools.store import FixtureStore
from sentinel_rcaeval.convert import convert_case
from sentinel_rcaeval.truth import RCAEvalTruth
from tests.unit.rcaeval_synth import write_synth_case


def test_convert_writes_loadable_fixture(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    out = convert_case(raw, tmp_path / "converted")
    assert out.name == "ob_cartservice_cpu_1"
    fixture = load_public_fixture(out / "public")
    assert fixture.manifest.source == "rcaeval-re2"
    assert fixture.manifest.window.start == 0 and fixture.manifest.window.end == 480
    assert len(fixture.manifest.alerts) >= 1
    assert fixture.metrics and fixture.logs and fixture.traces


def test_convert_writes_truth_and_no_leak(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    out = convert_case(raw, tmp_path / "converted")
    truth = RCAEvalTruth.model_validate_json((out / "eval_only" / "truth.json").read_text())
    assert truth.root_cause.service == "cartservice"
    # ground-truth service/fault must not leak into any public file
    public_blob = "".join(p.read_text() for p in (out / "public").glob("*"))
    assert "cpu exhaustion" not in public_blob


def test_converted_store_satisfies_protocol(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    out = convert_case(raw, tmp_path / "converted")
    store = FixtureStore(out / "public")
    assert store.window().end == 480
    assert store.list_services()
    assert store.list_metric_keys()
    assert store.metric_series("cartservice", "cpu_utilization")
    assert store.all_spans()
    assert store.find_spans(status="ERROR")
    assert store.search_logs(severity_min="error")
    assert store.list_changes() == []
    assert store.alerts()
