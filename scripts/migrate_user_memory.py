#!/usr/bin/env python3
"""Preflight or apply the non-destructive user-memory migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storage.user_memory import (  # noqa: E402
    LEGACY_SHARED_DB,
    USER_MEMORY_DEFAULT_DB,
    USER_MEMORY_DOMAIN,
    migrate_user_memory_store,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy user_memory and user_memory_events into their dedicated "
            "runtime store. Dry-run is the default; the legacy database is "
            "never deleted."
        )
    )
    parser.add_argument("--source", type=Path, default=LEGACY_SHARED_DB)
    parser.add_argument("--destination", type=Path, default=USER_MEMORY_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-domain",
        help=f"Required with --apply; must equal {USER_MEMORY_DOMAIN}.",
    )
    args = parser.parse_args()
    if args.apply and args.confirm_domain != USER_MEMORY_DOMAIN:
        parser.error(f"--apply requires --confirm-domain {USER_MEMORY_DOMAIN}")
    return args


def main() -> int:
    args = parse_args()
    result = migrate_user_memory_store(
        source=args.source,
        destination=args.destination,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
