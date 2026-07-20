"""Claim and execute one approved sandbox side-effect operation."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from operations import SideEffectOperationService  # noqa: E402


def main() -> int:
    operation = SideEffectOperationService().run_once()
    if operation is None:
        print("operation worker: no approved sandbox operation available")
        return 0
    print(
        "operation worker: "
        f"operation={operation.operation_id} "
        f"status={operation.status} "
        f"attempt={operation.attempt} "
        f"provider_operation_id={operation.provider_operation_id or 'none'} "
        f"receipt_sha256={operation.receipt_sha256 or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
