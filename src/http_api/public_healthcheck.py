"""Container liveness probe that follows the platform-selected port."""

from __future__ import annotations

from urllib.request import urlopen

from .public_server import configured_port


def healthcheck_url() -> str:
    return f"http://127.0.0.1:{configured_port()}/healthz"


def main() -> None:
    with urlopen(healthcheck_url(), timeout=2) as response:  # noqa: S310 - loopback only
        if response.status != 200:
            raise RuntimeError(f"health endpoint returned HTTP {response.status}")
        response.read()


if __name__ == "__main__":
    main()
