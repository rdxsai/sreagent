"""Load the API key from the environment, falling back to a local .env file.

The key is never hardcoded. If ANTHROPIC_API_KEY is already set we use it; else
we read it from .env in the project root so a developer can run the eval without
exporting it by hand.
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def load_api_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    dotenv = _ROOT / ".env"
    if not dotenv.exists():
        return False
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                os.environ["ANTHROPIC_API_KEY"] = value
                return True
    return False
