from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools import footprint, tool_call_log  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }


def test_default_tool_audit_path_is_not_the_legacy_shared_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_call_log, "LOG_DB", None)
    monkeypatch.delenv(tool_call_log.TOOL_AUDIT_DB_ENV, raising=False)

    assert tool_call_log.database_path() == ROOT / "runtime" / "tool_audit.db"
    assert tool_call_log.database_path() != ROOT / "tool_calls.db"


def test_make_check_redirects_and_resets_mutable_runtime_stores() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    reset_rule = makefile.split("check-runtime-reset:", 1)[1].split("\n\n", 1)[0]
    for variable, environment, filename in (
        ("CHECK_TOOL_AUDIT_DB", "BJ_PAL_TOOL_AUDIT_DB", "tool-audit.db"),
        ("CHECK_CLARIFICATION_DB", "BJ_PAL_CLARIFICATION_DB", "clarifications.db"),
        ("CHECK_JOB_DB", "BJ_PAL_JOB_DB", "planning-jobs.db"),
        ("CHECK_FEEDBACK_DB", "BJ_PAL_FEEDBACK_DB", "plan-feedback.db"),
    ):
        assert f"{variable} ?= $(CURDIR)/runtime/check/{filename}" in makefile
        assert f"check: export {environment} := $({variable})" in makefile
        assert f'Path("$({variable})")' in reset_rule


def test_configured_clean_start_creates_only_the_diagnostic_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "nested" / "tool-audit.db"
    monkeypatch.setattr(tool_call_log, "LOG_DB", None)
    monkeypatch.setenv(tool_call_log.TOOL_AUDIT_DB_ENV, str(database))

    tool_call_log.set_session("clean-start")
    tool_call_log.log_call("fixture.lookup", response={"status": "ok"})

    assert database.exists()
    assert _tables(database) == {"tool_calls"}


def test_new_audit_write_leaves_legacy_shared_state_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_database = tmp_path / "tool_calls.db"
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(
            """
            CREATE TABLE user_memory(user_id TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE plan_trace(plan_id TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE tool_calls(id INTEGER PRIMARY KEY, params_json TEXT);
            INSERT INTO user_memory VALUES ('user-1', 'legacy-memory-marker');
            INSERT INTO plan_trace VALUES ('plan-1', 'legacy-trace-marker');
            INSERT INTO tool_calls VALUES (1, 'legacy-private-marker');
            """
        )
    before = _sha256(legacy_database)

    audit_database = tmp_path / "runtime" / "tool_audit.db"
    monkeypatch.setattr(tool_call_log, "LOG_DB", None)
    monkeypatch.setenv(tool_call_log.TOOL_AUDIT_DB_ENV, str(audit_database))
    tool_call_log.set_session("isolated-write")
    tool_call_log.log_call("fixture.lookup", response={"status": "ok"})

    assert _sha256(legacy_database) == before
    assert _tables(legacy_database) == {"user_memory", "plan_trace", "tool_calls"}
    assert _tables(audit_database) == {"tool_calls"}
    assert b"legacy-private-marker" not in audit_database.read_bytes()


def test_footprint_reads_configured_v2_store_and_ignores_legacy_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "audit.db"
    monkeypatch.setattr(tool_call_log, "LOG_DB", database)
    tool_call_log.set_session("v2-session")
    tool_call_log.log_call(
        "agents.replanner.replan_step",
        response={"reason": "weather"},
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO tool_calls(
                session_id, timestamp, tool_name, params_json, response_json,
                status, latency_ms, error
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-session",
                "2026-07-20T00:00:00",
                "legacy.fixture",
                '{"query":"legacy-private-marker"}',
                '{}',
                "ok",
                1.0,
                None,
            ),
        )
        connection.commit()

    sessions = footprint.fetch_recent_sessions(limit=20)

    assert [entry.session_id for entry in sessions] == ["v2-session"]
    assert sessions[0].reroute_count == 1
    assert footprint.cumulative_stats() == {
        "total_sessions": 1,
        "total_reroutes": 1,
        "total_bookings": 0,
    }
