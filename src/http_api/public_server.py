"""Portable Uvicorn launcher for managed container platforms."""

from __future__ import annotations

import os

import uvicorn


def configured_port() -> int:
    raw = os.environ.get("PORT", "8000").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer") from exc
    if not 1 <= port <= 65_535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


def main() -> None:
    uvicorn.run(
        "http_api.public_app:app",
        host="0.0.0.0",
        port=configured_port(),
        proxy_headers=False,
        forwarded_allow_ips="",
    )


if __name__ == "__main__":
    main()
