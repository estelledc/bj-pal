from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools import tool_call_log  # noqa: E402


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "tool-calls.db"
    monkeypatch.setattr(tool_call_log, "LOG_DB", database)
    return database


def test_v2_log_minimizes_sensitive_payload_and_keeps_stable_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(tmp_path, monkeypatch)
    secret = "sk-" + "x" * 40
    email = "alice" + "@example.com"
    session_id = email
    private_text = "和 Alice 去一个不应进入日志的地点"

    tool_call_log.set_session(session_id)
    tool_call_log.log_call(
        "fixture.lookup",
        params={
            "api_key": secret,
            "user_input": private_text,
            "email": email,
            "persona": "family",
            "nested": {"query": private_text, "status": "requested"},
            secret: "credential-shaped dictionary keys must not survive",
        },
        response={
            "status": "failed",
            "message": private_text,
            "unknown_free_text": private_text,
        },
        status="error",
        error=f"RuntimeError: provider returned {secret}",
        latency_ms=12.3456,
    )

    rows = tool_call_log.fetch_calls(session_id=session_id)
    assert len(rows) == 1
    row = rows[0]
    serialized = json.dumps(row, ensure_ascii=False)
    assert row["privacy_version"] == tool_call_log.PRIVACY_VERSION
    assert row["session_id"] != session_id
    assert row["session_id"].startswith("session-sha256-")
    assert row["error"] is None
    assert row["error_code"] == "runtime_error"
    assert row["redaction_count"] >= 6
    assert row["integrity_valid"] is True
    assert secret not in serialized
    assert email not in serialized
    assert private_text not in serialized
    assert json.loads(row["params_json"])["persona"] == "family"
    assert json.loads(row["response_json"])["status"] == "failed"
    assert tool_call_log.verify_session_chain(session_id)["chain_valid"] is True


def test_v2_rows_are_append_only_and_chain_detects_forced_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _configure(tmp_path, monkeypatch)
    tool_call_log.set_session("append-only-session")
    tool_call_log.log_call("fixture.lookup", response={"status": "ok"})

    with sqlite3.connect(database) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE tool_calls SET status='error'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM tool_calls")

        conn.execute("DROP TRIGGER tool_calls_v2_no_update")
        conn.execute("UPDATE tool_calls SET status='error'")
        conn.commit()

    assert (
        tool_call_log.verify_session_chain("append-only-session")["chain_valid"]
        is False
    )


def test_clear_session_appends_reset_and_hides_prior_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(tmp_path, monkeypatch)
    session_id = "reset-session"
    tool_call_log.set_session(session_id)
    tool_call_log.log_call("fixture.before", response={"status": "ok"})
    tool_call_log.clear_session(session_id)
    tool_call_log.log_call("fixture.after", response={"status": "ok"})

    visible = tool_call_log.fetch_calls(session_id=session_id)
    assert [row["tool_name"] for row in visible] == ["fixture.after"]
    chain = tool_call_log.verify_session_chain(session_id)
    assert chain["event_count"] == 3
    assert chain["chain_valid"] is True


def test_legacy_payload_is_not_returned_by_default_after_additive_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _configure(tmp_path, monkeypatch)
    private_text = "legacy-private-payload"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TABLE tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                tool_name TEXT NOT NULL,
                params_json TEXT,
                response_json TEXT,
                status TEXT,
                latency_ms REAL,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tool_calls(
                session_id, timestamp, tool_name, params_json, response_json,
                status, latency_ms, error
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-session",
                "2026-07-20T00:00:00",
                "legacy.tool",
                json.dumps({"query": private_text}),
                json.dumps({"message": private_text}),
                "error",
                1.0,
                private_text,
            ),
        )
        conn.commit()

    rows = tool_call_log.fetch_calls(session_id="legacy-session")
    assert len(rows) == 1
    serialized = json.dumps(rows[0], ensure_ascii=False)
    assert private_text not in serialized
    assert rows[0]["error_code"] == "legacy_unverified"
    assert rows[0]["integrity_valid"] is False
