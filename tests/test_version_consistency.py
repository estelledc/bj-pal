from __future__ import annotations

import tomllib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from http_api.app import SERVICE_VERSION  # noqa: E402
from version import SERVICE_VERSION as CORE_SERVICE_VERSION  # noqa: E402


def test_package_and_http_service_versions_match() -> None:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert package["project"]["version"] == SERVICE_VERSION == CORE_SERVICE_VERSION
