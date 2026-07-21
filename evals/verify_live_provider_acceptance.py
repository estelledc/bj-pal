"""CLI for independent live-provider acceptance verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from evals.live_provider.verify import verify_live_provider_acceptance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("observation", type=Path)
    parser.add_argument("quality", type=Path)
    args = parser.parse_args()
    receipt = verify_live_provider_acceptance(
        args.receipt,
        args.observation,
        args.quality,
    )
    usage = receipt["execution_evidence"]["provider_reported_usage"]
    print(
        "live-provider acceptance verified: "
        f"gate_pass={receipt['acceptance']['gate_pass']} "
        f"scenario={receipt['scenario_id']} "
        f"reported_calls={usage['reported_calls']} "
        f"reported_total_tokens={usage['total_tokens']}"
    )
    return 0 if receipt["acceptance"]["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
