"""Run the Sentinel alert receiver: `python -m sentinel.api` or the `sentinel-api` script."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from sentinel.observability import configure


def _load_api_key() -> None:
    """Load ANTHROPIC_API_KEY from a project-root .env if it isn't already set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    dotenv = Path(__file__).resolve().parents[2] / ".env"
    if not dotenv.exists():
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                os.environ["ANTHROPIC_API_KEY"] = value
            return


def main() -> None:
    configure()
    _load_api_key()
    uvicorn.run(
        "sentinel.api.app:app",
        host=os.environ.get("SENTINEL_HOST", "0.0.0.0"),
        port=int(os.environ.get("SENTINEL_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
