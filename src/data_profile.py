"""Dataset provenance shared by bootstrap, loader, tests, and UI layers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "manifest.json"
DEFAULT_DATABASE = ROOT / "bj_pal.db"
REQUIRED_TABLES = frozenset({"pois", "ugc_aspects", "routes", "dataset_metadata"})


class DataProfileError(RuntimeError):
    """The manifest exists but cannot be trusted as a runtime contract."""


@dataclass(frozen=True)
class DataProfile:
    name: str
    classification: str
    public_reproducible: bool
    sources: Mapping[str, str]
    counts: Mapping[str, int]
    limitations: tuple[str, ...]

    @property
    def contains_synthetic_data(self) -> bool:
        return self.classification in {"synthetic", "mixed"}


@dataclass(frozen=True)
class RuntimeDataAudit:
    ready: bool
    profile: DataProfile
    checks: Mapping[str, str]


UNKNOWN_PROFILE = DataProfile(
    name="unknown",
    classification="unknown",
    public_reproducible=False,
    sources={},
    counts={},
    limitations=("dataset manifest is missing",),
)


def load_data_profile(path: Optional[Path] = None) -> DataProfile:
    manifest = path or DEFAULT_MANIFEST
    if not manifest.exists():
        return UNKNOWN_PROFILE
    try:
        payload: Mapping[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("manifest root must be an object")
        return DataProfile(
            name=str(payload.get("profile") or "unknown"),
            classification=str(payload.get("classification") or "unknown"),
            public_reproducible=bool(payload.get("public_reproducible", False)),
            sources={str(k): str(v) for k, v in (payload.get("sources") or {}).items()},
            counts={str(k): int(v) for k, v in (payload.get("counts") or {}).items()},
            limitations=tuple(str(item) for item in (payload.get("limitations") or [])),
        )
    except (json.JSONDecodeError, OSError, TypeError, ValueError, AttributeError) as exc:
        raise DataProfileError("dataset manifest is invalid") from exc


def inspect_runtime_data(
    *,
    manifest_path: Optional[Path] = None,
    database_path: Optional[Path] = None,
) -> RuntimeDataAudit:
    """Verify that manifest and read-only SQLite state describe the same dataset."""

    checks: dict[str, str] = {}
    try:
        profile = load_data_profile(manifest_path)
    except DataProfileError:
        return RuntimeDataAudit(
            ready=False,
            profile=UNKNOWN_PROFILE,
            checks={
                "dataset_manifest": "invalid",
                "sqlite_database": "not_checked",
                "sqlite_integrity": "not_checked",
                "sqlite_schema": "not_checked",
                "profile_consistency": "not_checked",
                "row_counts": "not_checked",
            },
        )

    if profile.name == "unknown":
        return RuntimeDataAudit(
            ready=False,
            profile=profile,
            checks={
                "dataset_manifest": "missing",
                "sqlite_database": "not_checked",
                "sqlite_integrity": "not_checked",
                "sqlite_schema": "not_checked",
                "profile_consistency": "not_checked",
                "row_counts": "not_checked",
            },
        )
    checks["dataset_manifest"] = "ok"

    database = database_path or DEFAULT_DATABASE
    if not database.is_file():
        checks.update({
            "sqlite_database": "missing",
            "sqlite_integrity": "not_checked",
            "sqlite_schema": "not_checked",
            "profile_consistency": "not_checked",
            "row_counts": "not_checked",
        })
        return RuntimeDataAudit(ready=False, profile=profile, checks=checks)

    checks["sqlite_database"] = "ok"
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        with connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            checks["sqlite_integrity"] = "ok" if integrity == "ok" else "failed"

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing_tables = sorted(REQUIRED_TABLES - tables)
            checks["sqlite_schema"] = (
                "ok" if not missing_tables else f"missing:{','.join(missing_tables)}"
            )
            if missing_tables:
                checks["profile_consistency"] = "not_checked"
                checks["row_counts"] = "not_checked"
            else:
                metadata = dict(
                    connection.execute(
                        "SELECT key, value FROM dataset_metadata"
                    ).fetchall()
                )
                expected_metadata = {
                    "profile": profile.name,
                    "classification": profile.classification,
                    "public_reproducible": json.dumps(profile.public_reproducible),
                    "sources": json.dumps(
                        dict(profile.sources), ensure_ascii=False, sort_keys=True
                    ),
                    "limitations": json.dumps(profile.limitations, ensure_ascii=False),
                }
                mismatched_metadata = sorted(
                    key
                    for key, expected in expected_metadata.items()
                    if metadata.get(key) != expected
                )
                checks["profile_consistency"] = (
                    "ok"
                    if not mismatched_metadata
                    else f"mismatch:{','.join(mismatched_metadata)}"
                )

                expected_counts = {
                    key: profile.counts.get(key)
                    for key in ("pois", "ugc_aspects", "routes")
                }
                mismatched_counts = []
                for table, expected in expected_counts.items():
                    if expected is None:
                        mismatched_counts.append(f"{table}:missing_expected")
                        continue
                    actual = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    if actual != expected:
                        mismatched_counts.append(f"{table}:{actual}!={expected}")
                checks["row_counts"] = (
                    "ok" if not mismatched_counts else f"mismatch:{','.join(mismatched_counts)}"
                )
    except sqlite3.Error:
        checks.update({
            "sqlite_database": "unreadable",
            "sqlite_integrity": "failed",
            "sqlite_schema": "not_checked",
            "profile_consistency": "not_checked",
            "row_counts": "not_checked",
        })
    finally:
        if "connection" in locals():
            connection.close()

    ready = all(value == "ok" for value in checks.values())
    return RuntimeDataAudit(ready=ready, profile=profile, checks=checks)
