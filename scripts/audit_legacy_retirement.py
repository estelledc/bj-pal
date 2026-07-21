#!/usr/bin/env python3
"""Print a payload-free audit of legacy shared-state retirement readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storage.legacy_retirement import inspect_legacy_retirement  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless every mutable owner resolves to a verified store.",
    )
    args = parser.parse_args()
    audit = inspect_legacy_retirement()
    print(json.dumps(audit.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if audit.ready or not args.require_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
