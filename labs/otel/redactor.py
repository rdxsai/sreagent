"""Redaction helpers for public OpenTelemetry fixture files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BANNED_PUBLIC_TOKENS: tuple[str, ...] = (
    "paymentFailure",
    "paymentUnreachable",
    "recommendationCacheFailure",
    "productCatalogFailure",
    "adHighCpu",
    "adManualGc",
    "cartFailure",
    "kafkaQueueProblems",
    "loadGeneratorFloodHomepage",
    "imageSlowLoad",
    "emailMemoryLeak",
    "feature_flag.key",
    "feature_flag.result.variant",
    "feature_flag.result.value",
    "feature_flag.",
    "raw_flag_key",
)


@dataclass(frozen=True)
class RedactionFinding:
    path: Path
    token: str
    line_number: int


class RedactionError(ValueError):
    """Raised when public fixture data leaks private scenario tokens."""

    def __init__(self, findings: list[RedactionFinding]) -> None:
        self.findings = findings
        detail = ", ".join(
            f"{finding.path}:{finding.line_number} contains {finding.token}"
            for finding in findings[:5]
        )
        if len(findings) > 5:
            detail = f"{detail}, and {len(findings) - 5} more"
        super().__init__(f"public fixture redaction failed: {detail}")


def find_banned_tokens(
    public_dir: Path,
    banned_tokens: tuple[str, ...] = BANNED_PUBLIC_TOKENS,
) -> list[RedactionFinding]:
    """Return all banned token occurrences under a public fixture directory."""

    root = public_dir.resolve()
    findings: list[RedactionFinding] = []
    for path in _iter_public_files(root):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                for token in banned_tokens:
                    if token in line:
                        findings.append(RedactionFinding(path, token, line_number))
    return findings


def assert_no_banned_tokens(public_dir: Path) -> None:
    """Raise if public fixture files contain raw flag or truth tokens."""

    findings = find_banned_tokens(public_dir)
    if findings:
        raise RedactionError(findings)


def _iter_public_files(root: Path) -> list[Path]:
    if root.name != "public":
        raise ValueError("redaction scans must target a public/ directory")
    if not root.is_dir():
        raise ValueError(f"public fixture directory does not exist: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file())
