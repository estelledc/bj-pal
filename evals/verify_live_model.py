from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.live_model.verify import verify_live_model_observation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = verify_live_model_observation(args.artifact)
    print(
        "live-model observation verified: "
        f"outcome={artifact['result']['outcome']} scenario={artifact['scenario_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
