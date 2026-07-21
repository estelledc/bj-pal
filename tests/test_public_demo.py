from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from http_api.public_app import create_public_demo_app  # noqa: E402
from http_api.public_demo import (  # noqa: E402
    PublicDemoGuardMiddleware,
    PublicDemoSettings,
    validate_public_demo_environment,
)
from http_api.public_server import configured_port  # noqa: E402


COMPLETE_PLAN = {
    "user_input": "周末下午带娃在五道营附近玩四小时，不吃辣",
    "persona": "family",
    "preferences": {
        "party_size": 3,
        "has_child": True,
        "child_age": 5,
        "diet_flags": ["no_spicy"],
        "duration_hours": 4,
    },
}


@pytest.fixture(autouse=True)
def public_demo_environment(monkeypatch) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "BJ_PAL_CONTROL_PRINCIPALS_JSON",
        "BJ_PAL_CONTROL_TOKEN",
        "DEEPSEEK_API_KEY",
        "DPSK_API_KEY",
        "LONGCAT_API_KEY",
        "OPEN_METEO_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BJ_PAL_LLM", "mock")
    monkeypatch.setenv("BJ_PAL_TRACE", "off")


def test_public_demo_exposes_only_bounded_read_and_plan_routes(tmp_path, monkeypatch) -> None:
    feedback_db = tmp_path / "feedback.db"
    clarification_db = tmp_path / "clarifications.db"
    monkeypatch.setenv("BJ_PAL_FEEDBACK_DB", str(feedback_db))
    monkeypatch.setenv("BJ_PAL_CLARIFICATION_DB", str(clarification_db))
    app = create_public_demo_app()

    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        planned = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "public-demo-plan"},
            json=COMPLETE_PLAN,
        )
        hidden_feedback = client.get("/v1/feedback-summary")
        hidden_jobs = client.get("/v1/planning-jobs")

    assert set(schema["paths"]) == {"/healthz", "/readyz", "/v1/plans"}
    assert schema["info"]["title"] == "BJ-Pal Synthetic Public Demo API"
    assert planned.status_code == 200, planned.text
    assert planned.headers["X-BJ-Pal-Demo-Mode"] == "synthetic-mock"
    assert planned.headers["X-Request-ID"] == "public-demo-plan"
    assert planned.headers["Cache-Control"] == "no-store"
    assert planned.json().get("feedback") is None
    assert hidden_feedback.status_code == 404
    assert hidden_jobs.status_code == 404
    assert not feedback_db.exists()
    assert not clarification_db.exists()


def test_public_demo_clarification_is_not_persisted(tmp_path, monkeypatch) -> None:
    clarification_db = tmp_path / "clarifications.db"
    monkeypatch.setenv("BJ_PAL_CLARIFICATION_DB", str(clarification_db))
    app = create_public_demo_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/plans",
            json={"user_input": "还是上次那个地方，下午安排一下"},
        )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "clarification_required"
    assert error["details"]["continuation_available"] is False
    assert "continuation" not in error["details"]
    assert not clarification_db.exists()


def test_public_demo_refuses_user_and_trial_capabilities() -> None:
    app = create_public_demo_app()

    with TestClient(app) as client:
        identified = client.post(
            "/v1/plans",
            json={**COMPLETE_PLAN, "user_id": "portfolio-viewer-1"},
        )
        trial_bound = client.post(
            "/v1/plans",
            headers={"X-Trial-Participant-Capability": "not-for-public-demo"},
            json=COMPLETE_PLAN,
        )

    assert identified.status_code == 422
    assert identified.json()["error"]["code"] == "public_demo_user_id_unsupported"
    assert trial_bound.status_code == 422
    assert trial_bound.json()["error"]["code"] == (
        "public_demo_capability_unsupported"
    )


def test_public_demo_counts_raw_attempts_before_schema_validation() -> None:
    app = create_public_demo_app(
        settings=PublicDemoSettings(
            requests_per_window=1,
            window_seconds=60,
            max_concurrent_plans=1,
            max_body_bytes=8_192,
        )
    )

    with TestClient(app) as client:
        invalid = client.post("/v1/plans", content=b"{}")
        limited = client.post("/v1/plans", json=COMPLETE_PLAN)

    assert invalid.status_code == 422
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "public_demo_rate_limited"
    assert limited.headers["Retry-After"] == "60"
    assert limited.headers["X-RateLimit-Remaining"] == "0"


def test_public_demo_rejects_oversized_stream_before_planning() -> None:
    app = create_public_demo_app(
        settings=PublicDemoSettings(
            requests_per_window=2,
            window_seconds=60,
            max_concurrent_plans=1,
            max_body_bytes=32,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/plans",
            headers={"X-Request-ID": "oversized-public-demo"},
            content=b"x" * 33,
        )

    assert response.status_code == 413
    assert response.headers["Connection"] == "close"
    assert response.json()["error"] == {
        "code": "public_demo_body_too_large",
        "message": "The public demo request body exceeds its bounded input limit.",
        "request_id": "oversized-public-demo",
    }


def test_public_demo_rejects_work_above_concurrent_capacity() -> None:
    async def exercise_guard() -> list[dict]:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def downstream(scope, receive, send) -> None:
            del scope, receive
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        guard = PublicDemoGuardMiddleware(
            downstream,
            settings=PublicDemoSettings(
                requests_per_window=3,
                window_seconds=60,
                max_concurrent_plans=1,
                max_body_bytes=32,
            ),
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/plans",
            "headers": [],
        }

        def receiver():
            delivered = False

            async def receive() -> dict:
                nonlocal delivered
                if delivered:
                    return {"type": "http.request", "body": b"", "more_body": False}
                delivered = True
                return {"type": "http.request", "body": b"{}", "more_body": False}

            return receive

        first_messages: list[dict] = []
        second_messages: list[dict] = []

        async def first_send(message: dict) -> None:
            first_messages.append(message)

        async def second_send(message: dict) -> None:
            second_messages.append(message)

        first = asyncio.create_task(guard(scope, receiver(), first_send))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await guard(scope, receiver(), second_send)
        release.set()
        await asyncio.wait_for(first, timeout=1)
        return second_messages

    messages = asyncio.run(exercise_guard())
    assert messages[0]["status"] == 503
    assert b"public_demo_busy" in messages[1]["body"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BJ_PAL_LLM", "dpsk"),
        ("DPSK_API_KEY", "configured-but-never-read"),
        ("BJ_PAL_CONTROL_TOKEN", "configured-but-never-read"),
    ],
)
def test_public_demo_refuses_non_mock_backends_and_credentials(name, value) -> None:
    environment = {"BJ_PAL_LLM": "mock", name: value}
    with pytest.raises(RuntimeError, match="public demo"):
        validate_public_demo_environment(environment)


@pytest.mark.parametrize(
    "settings",
    [
        {"requests_per_window": 0},
        {"window_seconds": 3_601},
        {"max_concurrent_plans": True},
        {"max_body_bytes": 65_537},
    ],
)
def test_public_demo_settings_fail_closed_outside_bounds(settings) -> None:
    with pytest.raises(ValueError):
        PublicDemoSettings(**settings)


@pytest.mark.parametrize("raw", ["0", "65536", "not-a-port"])
def test_public_server_rejects_invalid_port(monkeypatch, raw) -> None:
    monkeypatch.setenv("PORT", raw)
    with pytest.raises(RuntimeError, match="PORT"):
        configured_port()


def test_public_server_uses_platform_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "9080")
    assert configured_port() == 9080
