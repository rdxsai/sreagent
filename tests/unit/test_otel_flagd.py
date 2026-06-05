from __future__ import annotations

import pytest

from labs.otel.flagd import FlagdControlError, reset_all_flags, set_flag_variant


def test_set_flag_variant_returns_changed_copy() -> None:
    document = _flag_document()

    updated = set_flag_variant(document, "paymentUnreachable", "on")

    assert updated["flags"]["paymentUnreachable"]["defaultVariant"] == "on"
    assert document["flags"]["paymentUnreachable"]["defaultVariant"] == "off"


def test_set_flag_variant_rejects_unknown_variant() -> None:
    with pytest.raises(FlagdControlError):
        set_flag_variant(_flag_document(), "paymentUnreachable", "missing")


def test_reset_all_flags_sets_off_variant() -> None:
    document = _flag_document()
    document["flags"]["paymentUnreachable"]["defaultVariant"] = "on"
    document["flags"]["paymentFailure"]["defaultVariant"] = "100%"

    updated = reset_all_flags(document)

    assert updated["flags"]["paymentUnreachable"]["defaultVariant"] == "off"
    assert updated["flags"]["paymentFailure"]["defaultVariant"] == "off"


def _flag_document() -> dict[str, object]:
    return {
        "flags": {
            "paymentUnreachable": {
                "defaultVariant": "off",
                "variants": {"on": True, "off": False},
            },
            "paymentFailure": {
                "defaultVariant": "off",
                "variants": {"100%": 1, "off": 0},
            },
        }
    }
