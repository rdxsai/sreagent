"""Run the Sentinel alert receiver: `python -m sentinel.api` or the `sentinel-api` script."""

from __future__ import annotations

import os

import uvicorn

from sentinel.observability import configure


def main() -> None:
    configure()
    uvicorn.run(
        "sentinel.api.app:app",
        host=os.environ.get("SENTINEL_HOST", "0.0.0.0"),
        port=int(os.environ.get("SENTINEL_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
