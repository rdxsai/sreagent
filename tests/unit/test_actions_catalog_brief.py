"""Components 3+4: remediation catalog (deterministic fault->actions) and brief builder
(deterministic brief from a result.json shape)."""

from __future__ import annotations

from sentinel.actions.brief import build_brief
from sentinel.actions.catalog import elect_primary, suggest_actions
from sentinel.actions.models import MUTATE_KINDS


def _result(root="emailservice", fault="resource saturation", supported=True, sig=None):
    sig = sig or {"resource": True, "latency": True, "error": False}
    return {
        "run_id": "ob_emailservice_cpu_1",
        "root_cause_service": root,
        "ranked_services": [root, "recommendationservice", "currencyservice"],
        "synthesis": {"root_cause_service": root, "ranked_services": [root, "recommendationservice"],
                      "fault_type": fault, "justification": "cpu rose at onset"},
        "graph_source": "static",
        "verdicts": [
            {"candidate_service": root, "supported": supported, "root_cause_service": root if supported else None,
             "signature": "resource", "observed_signatures": sig,
             "evidence": ["emailservice cpu_utilization 0.27->18.68 at onset", "downstream flat"]},
        ],
    }


# -- catalog ---------------------------------------------------------------------------
def test_each_fault_type_yields_ranked_actions():
    for fault in ("cpu", "mem", "disk", "delay", "loss", "socket", "error"):
        acts = suggest_actions(target_service="svc", fault_type=fault)
        assert acts, f"{fault} produced no actions"
        # reversible-first
        assert acts[0].reversible


def test_confident_resource_elects_a_mutate_primary():
    acts = suggest_actions(target_service="emailservice", fault_type="cpu")
    primary = elect_primary(acts)
    assert primary is not None and primary.kind in MUTATE_KINDS
    assert primary.preview and primary.params_hash   # preview + binding filled


def test_low_confidence_is_notify_only():
    acts = suggest_actions(target_service="svc", fault_type="cpu", confident=False)
    assert acts and all(a.effect == "notify" for a in acts)
    assert elect_primary(acts) is None   # no remediation button


def test_delay_maps_to_remove_impairment_first():
    acts = suggest_actions(target_service="orders", fault_type="delay")
    assert acts[0].kind == "remove_impairment"


# -- brief -----------------------------------------------------------------------------
def test_brief_from_confident_result_has_primary_and_citations():
    b = build_brief(_result())
    assert b.root_cause_service == "emailservice"
    assert b.confident is True
    assert b.primary_action is not None and b.primary_action.effect == "mutate"
    assert b.primary_action.preview            # exact op shown to the human
    assert b.ranked and b.ranked[0]["evidence"]  # top hypothesis carries citations


def test_brief_from_unsupported_result_is_notify_only():
    # root not supported by any verdict -> not confident -> no mutate button
    b = build_brief(_result(supported=False))
    assert b.confident is False
    assert b.primary_action is None


def test_brief_top3_ranked_with_signatures():
    b = build_brief(_result())
    assert len(b.ranked) <= 3
    assert "observed_signatures" in b.ranked[0]
