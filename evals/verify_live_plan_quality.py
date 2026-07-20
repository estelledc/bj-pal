from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evals.live_model.quality_verify import verify_live_plan_quality  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("quality_artifact", type=Path)
    parser.add_argument("observation_artifact", type=Path)
    args = parser.parse_args()
    artifact = verify_live_plan_quality(
        args.quality_artifact,
        args.observation_artifact,
    )
    print(
        json.dumps(
            {
                "scenario_id": artifact["scenario_id"],
                "metrics": artifact["metrics"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if artifact["metrics"]["hard_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
