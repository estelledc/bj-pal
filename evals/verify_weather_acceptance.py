"""CLI wrapper for independent weather acceptance verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.weather.verify import verify_weather_acceptance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "weather" / "beijing_synthetic.json",
    )
    args = parser.parse_args()
    artifact = verify_weather_acceptance(args.artifact, args.fixture)
    print(
        "weather acceptance verified: "
        f"level={artifact['acceptance_level']} "
        f"live={artifact['live_network_used']} "
        f"artifact={artifact['artifact_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
