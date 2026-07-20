from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.live_model.verify import verify_live_model_pair  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("flash_artifact", type=Path)
    parser.add_argument("pro_artifact", type=Path)
    args = parser.parse_args()
    result = verify_live_model_pair(args.flash_artifact, args.pro_artifact)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
