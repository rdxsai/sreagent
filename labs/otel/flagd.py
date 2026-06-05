"""flagd-ui control client for lab-only scenario injection."""

from __future__ import annotations

import copy
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from labs.otel.source import FLAGD_UI_BASE_URL

FlagDocument = dict[str, Any]


class FlagdControlError(RuntimeError):
    """Raised when flagd-ui control fails."""


class FlagdClient:
    """Small client for the verified flagd-ui JSON API."""

    def __init__(self, base_url: str = FLAGD_UI_BASE_URL, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def read(self) -> FlagDocument:
        request = Request(f"{self.base_url}/api/read", method="GET")
        return _request_json(request, self.timeout_seconds)

    def write(self, document: FlagDocument) -> None:
        payload = json.dumps({"data": document}).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/write",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        _request_json(request, self.timeout_seconds)

    def set_flag_variant(self, raw_flag_key: str, variant: str) -> FlagDocument:
        document = set_flag_variant(self.read(), raw_flag_key, variant)
        self.write(document)
        return document

    def reset_all_flags(self, variant: str = "off") -> FlagDocument:
        document = reset_all_flags(self.read(), variant)
        self.write(document)
        return document


def set_flag_variant(document: FlagDocument, raw_flag_key: str, variant: str) -> FlagDocument:
    """Return a copy of a flag document with one default variant changed."""

    updated = copy.deepcopy(document)
    flags = _flags(updated)
    flag = flags.get(raw_flag_key)
    if flag is None:
        raise FlagdControlError(f"unknown flag: {raw_flag_key}")
    variants = flag.get("variants")
    if not isinstance(variants, dict) or variant not in variants:
        raise FlagdControlError(f"unknown variant {variant} for flag {raw_flag_key}")
    flag["defaultVariant"] = variant
    return updated


def reset_all_flags(document: FlagDocument, variant: str = "off") -> FlagDocument:
    """Return a copy of a flag document with every compatible flag reset."""

    updated = copy.deepcopy(document)
    for raw_flag_key, flag in _flags(updated).items():
        variants = flag.get("variants")
        if isinstance(variants, dict) and variant in variants:
            flag["defaultVariant"] = variant
        else:
            raise FlagdControlError(f"flag {raw_flag_key} has no {variant} variant")
    return updated


def _flags(document: FlagDocument) -> dict[str, Any]:
    flags = document.get("flags")
    if not isinstance(flags, dict):
        raise FlagdControlError("flag document missing flags object")
    return flags


def _request_json(request: Request, timeout_seconds: float) -> FlagDocument:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        raise FlagdControlError(f"flagd-ui request failed: {exc}") from exc

    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FlagdControlError(f"flagd-ui returned invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise FlagdControlError("flagd-ui returned non-object JSON")
    return decoded
