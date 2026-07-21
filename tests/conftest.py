"""Session-wide isolation for mutable runtime state created by tests."""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


_TEST_RUNTIME = TemporaryDirectory(prefix="bj-pal-pytest-state-")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.environ["BJ_PAL_PLAN_EVIDENCE_DB"] = str(
    Path(_TEST_RUNTIME.name) / "plan-evidence.db"
)
os.environ["BJ_PAL_USER_MEMORY_DB"] = str(
    Path(_TEST_RUNTIME.name) / "user-memory.db"
)
os.environ["BJ_PAL_PREDICTION_DB"] = str(
    Path(_TEST_RUNTIME.name) / "prediction.db"
)
atexit.register(_TEST_RUNTIME.cleanup)
