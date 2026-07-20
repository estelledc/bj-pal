from __future__ import annotations

from pathlib import Path

import httpx

from evals.run_http_benchmark import _response_error_code
from evals.run_socket_http_benchmark import _SECRET_ENV_KEYS, _server_environment


def test_socket_server_environment_removes_provider_and_control_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for key in _SECRET_ENV_KEYS:
        monkeypatch.setenv(key, f"private-{key}")

    environment = _server_environment(tmp_path)

    assert _SECRET_ENV_KEYS.isdisjoint(environment)
    assert environment["BJ_PAL_LLM"] == "mock"
    assert environment["BJ_PAL_ENV_FILE"] == str(tmp_path / "disabled.env")
    assert environment["BJ_PAL_FEEDBACK_DB"] == str(tmp_path / "feedback.db")
    assert environment["BJ_PAL_JOB_DB"] == str(tmp_path / "jobs.db")
    assert environment["BJ_PAL_CLARIFICATION_DB"] == str(
        tmp_path / "clarifications.db"
    )
    assert environment["BJ_PAL_TOOL_AUDIT_DB"] == str(tmp_path / "tool-audit.db")
    assert environment["BJ_PAL_PLAN_EVIDENCE_DB"] == str(
        tmp_path / "plan-evidence.db"
    )
    assert environment["BJ_PAL_USER_MEMORY_DB"] == str(tmp_path / "user-memory.db")
    assert environment["BJ_PAL_PREDICTION_DB"] == str(
        tmp_path / "prediction-feedback.db"
    )


def test_benchmark_retains_safe_api_error_code_without_response_body() -> None:
    typed = httpx.Response(
        422,
        json={"error": {"code": "invalid_planning_request", "message": "private"}},
    )
    malformed = httpx.Response(503, content=b"not-json")

    assert _response_error_code(typed) == "invalid_planning_request"
    assert _response_error_code(malformed) == "http_503"
