"""CLI for deterministic weather-provider acceptance evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.weather.acceptance import build_weather_acceptance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "fixtures" / "weather" / "beijing_synthetic.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "results" / "weather-acceptance.json",
    )
    args = parser.parse_args()

    artifact = build_weather_acceptance(fixture_path=args.fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "weather acceptance: "
        f"level={artifact['acceptance_level']} "
        f"live={artifact['live_network_used']} "
        f"fixture={artifact['fixture']['sha256'][:12]} "
        f"artifact={artifact['artifact_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
