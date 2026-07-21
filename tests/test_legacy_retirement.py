from __future__ import annotations

import sqlite3
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from storage import prediction_feedback, state_layout, user_memory
from storage.legacy_retirement import (
    COMPATIBILITY_POLICY,
    DEDICATED_REQUIRED_POLICY,
    LegacyRetirementAudit,
    STATE_LAYOUT_POLICY_ENV,
    inspect_legacy_retirement,
    state_layout_policy,
)


app_module = importlib.import_module("http_api.app")


def _legacy(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(state_layout.PLAN_EVIDENCE_SCHEMA)
        connection.executescript(prediction_feedback.PREDICTION_FEEDBACK_SCHEMA)
        connection.executescript(user_memory.USER_MEMORY_SCHEMA)
        connection.executescript(
            """
            CREATE TABLE tool_calls(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL
            );
            INSERT INTO tool_calls(tool_name) VALUES ('legacy.synthetic');
            """
        )
        connection.execute(
            "INSERT INTO plan_trace VALUES (7,'plan-a',0,'visit','poi-a',"
            "'choose',0.8,NULL,NULL,1.0)"
        )
        connection.execute(
            "INSERT INTO plan_outcome VALUES (9,'plan-a',0,1,NULL,"
            "'legacy_unclassified',2.0)"
        )
        connection.execute(
            "INSERT INTO prediction_log VALUES "
            "(11,'poi-a','14:00',10,'2026-01-01T13:00:00',NULL,NULL,0.8)"
        )
        connection.execute(
            "INSERT INTO user_memory VALUES "
            "(13,'user-a','fact','area:city','\"北京\"',0.8,1,1.0,2.0,0,"
            "'explicit_user_input',2.0,NULL,1)"
        )
        connection.execute(
            "INSERT INTO user_memory_events VALUES "
            "(17,'user-a','fact','area:city','created',1,"
            "'explicit_user_input',?,NULL,'new_memory',2.0)",
            ("a" * 64,),
        )


def _tool_audit(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE tool_calls(id INTEGER PRIMARY KEY, tool_name TEXT NOT NULL)"
        )


def _configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    migrate: bool = True,
) -> tuple[Path, Path]:
    source = tmp_path / "tool_calls.db"
    _legacy(source)
    destinations = {
        "plan": tmp_path / "runtime" / "plan_evidence.db",
        "prediction": tmp_path / "runtime" / "prediction_feedback.db",
        "memory": tmp_path / "runtime" / "user_memory.db",
    }
    tool_audit = tmp_path / "runtime" / "tool_audit.db"
    tool_audit.parent.mkdir(parents=True, exist_ok=True)
    _tool_audit(tool_audit)
    if migrate:
        state_layout.migrate_plan_evidence_store(
            source=source, destination=destinations["plan"], apply=True
        )
        prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destinations["prediction"], apply=True
        )
        user_memory.migrate_user_memory_store(
            source=source, destination=destinations["memory"], apply=True
        )

    monkeypatch.setattr(state_layout, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(prediction_feedback, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(user_memory, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(
        state_layout, "PLAN_EVIDENCE_DEFAULT_DB", destinations["plan"]
    )
    monkeypatch.setattr(
        prediction_feedback,
        "PREDICTION_FEEDBACK_DEFAULT_DB",
        destinations["prediction"],
    )
    monkeypatch.setattr(user_memory, "USER_MEMORY_DEFAULT_DB", destinations["memory"])
    monkeypatch.delenv(state_layout.PLAN_EVIDENCE_DB_ENV, raising=False)
    monkeypatch.delenv(prediction_feedback.PREDICTION_FEEDBACK_DB_ENV, raising=False)
    monkeypatch.delenv(user_memory.USER_MEMORY_DB_ENV, raising=False)
    return source, tool_audit


def test_retirement_audit_accepts_verified_dedicated_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, tool_audit = _configure(tmp_path, monkeypatch)

    audit = inspect_legacy_retirement(
        legacy_path=source,
        tool_audit_path=tool_audit,
        policy=DEDICATED_REQUIRED_POLICY,
    )

    assert audit.ready is True
    assert audit.legacy_counts == {
        "plan_outcome": 1,
        "plan_trace": 1,
        "prediction_log": 1,
        "tool_calls": 1,
        "user_memory": 1,
        "user_memory_events": 1,
    }
    assert set(audit.resolved_database_names) == {
        "plan_evidence",
        "prediction_feedback",
        "user_memory",
        "tool_audit",
    }
    assert all(value == "ok" for value in audit.checks.values())


def test_retirement_audit_rejects_legacy_source_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, tool_audit = _configure(tmp_path, monkeypatch)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO prediction_log(poi_name) VALUES ('drift-marker')"
        )

    audit = inspect_legacy_retirement(
        legacy_path=source, tool_audit_path=tool_audit
    )

    assert audit.ready is False
    assert audit.checks["prediction_feedback_legacy_binding"] == "source_drift"


def test_retirement_audit_rejects_unknown_legacy_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, tool_audit = _configure(tmp_path, monkeypatch)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE mystery_state(id INTEGER PRIMARY KEY)")

    audit = inspect_legacy_retirement(
        legacy_path=source, tool_audit_path=tool_audit
    )

    assert audit.ready is False
    assert audit.checks["legacy_known_tables"] == "unknown:mystery_state"


def test_retirement_audit_rejects_legacy_fallback_without_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, tool_audit = _configure(tmp_path, monkeypatch, migrate=False)

    audit = inspect_legacy_retirement(
        legacy_path=source, tool_audit_path=tool_audit
    )

    assert audit.ready is False
    assert audit.checks["plan_evidence_dedicated"] == "legacy_fallback"
    assert audit.checks["prediction_feedback_dedicated"] == "legacy_fallback"
    assert audit.checks["user_memory_dedicated"] == "legacy_fallback"


def test_retirement_audit_rejects_tool_audit_pointing_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _tool_audit_path = _configure(tmp_path, monkeypatch)

    audit = inspect_legacy_retirement(
        legacy_path=source, tool_audit_path=source
    )

    assert audit.ready is False
    assert audit.checks["tool_audit_dedicated"] == "legacy_fallback"
    assert audit.checks["tool_audit_owner"] == "unexpected_tables"


def test_state_layout_policy_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STATE_LAYOUT_POLICY_ENV, raising=False)
    assert state_layout_policy() == COMPATIBILITY_POLICY
    monkeypatch.setenv(STATE_LAYOUT_POLICY_ENV, DEDICATED_REQUIRED_POLICY)
    assert state_layout_policy() == DEDICATED_REQUIRED_POLICY
    monkeypatch.setenv(STATE_LAYOUT_POLICY_ENV, "silent_fallback")
    with pytest.raises(ValueError, match=STATE_LAYOUT_POLICY_ENV):
        state_layout_policy()


def test_default_readiness_fails_closed_when_dedicated_policy_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STATE_LAYOUT_POLICY_ENV, DEDICATED_REQUIRED_POLICY)
    monkeypatch.setattr(
        app_module,
        "inspect_runtime_data",
        lambda: SimpleNamespace(
            ready=True,
            profile=SimpleNamespace(name="demo", classification="synthetic"),
            checks={"dataset_manifest": "ok"},
        ),
    )
    monkeypatch.setattr(
        app_module,
        "inspect_legacy_retirement",
        lambda **_kwargs: LegacyRetirementAudit(
            ready=False,
            policy=DEDICATED_REQUIRED_POLICY,
            legacy_database_name="tool_calls.db",
            legacy_counts={},
            resolved_database_names={},
            checks={"user_memory_dedicated": "legacy_fallback"},
        ),
    )

    result = app_module.default_readiness_probe()

    assert result.status == "not_ready"
    assert result.checks["state_layout_policy"] == "failed"
    assert result.checks["state_layout_user_memory_dedicated"] == "legacy_fallback"
