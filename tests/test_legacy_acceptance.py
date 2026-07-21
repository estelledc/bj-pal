"""Run the historical ``t1_*`` acceptance scripts under the real pytest gate.

The repository predates pytest naming conventions. These functions contain
assertions but were silently skipped by both unittest discovery and default
pytest collection. This adapter keeps their script entry points intact while
making every assertion part of CI without treating their diagnostic return
values as pytest return values.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest


TESTS_ROOT = Path(__file__).resolve().parent
LEGACY_NAME = re.compile(r"^t\d+_")


def _load_legacy_cases():
    cases = []
    for path in sorted(TESTS_ROOT.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        module_name = f"bj_pal_legacy_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load legacy test module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if LEGACY_NAME.match(name):
                cases.append((f"{path.stem}.{name}", function))
    return tuple(cases)


LEGACY_CASES = _load_legacy_cases()


@pytest.mark.parametrize(
    ("case_name", "case"),
    LEGACY_CASES,
    ids=[name for name, _ in LEGACY_CASES],
)
def test_legacy_acceptance(case_name, case) -> None:
    del case_name
    case()
