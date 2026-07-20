from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.live_model.verify import verify_live_model_suite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    result = verify_live_model_suite(args.artifacts)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
