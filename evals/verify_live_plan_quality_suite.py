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

from evals.live_model.quality_verify import (  # noqa: E402
    verify_live_plan_quality_suite,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pairs",
        nargs="+",
        help="quality=observation artifact pair",
    )
    args = parser.parse_args()
    pairs = []
    for item in args.pairs:
        if "=" not in item:
            parser.error("each pair must use quality=observation")
        quality, observation = item.split("=", 1)
        pairs.append((Path(quality), Path(observation)))
    result = verify_live_plan_quality_suite(pairs)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["suite_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
