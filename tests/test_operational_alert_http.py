from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import tracing  # noqa: E402
from http_api.app import create_app  # noqa: E402
from http_api.schemas import ReadinessResponse  # noqa: E402
from jobs import DurableWorkloadHealth  # noqa: E402


TOKEN = "operational-alert-test-token-0123456789-abcdef"


class _UnusedPlanningService:
    pass


class _EmptyWorkloadService:
    def workload_health(
        self,
        *,
        tenant_id: str,
        window_start: str,
        window_end: str,
    ) -> DurableWorkloadHealth:
        assert tenant_id == "default"
        return DurableWorkloadHealth.create(
            window_start=window_start,
            window_end=window_end,
            records=(),
        )


def _ready() -> ReadinessResponse:
    return ReadinessResponse(
        status="ready",
        data_profile="demo",
        classification="synthetic",
        checks={"fixture": "ok"},
    )


def test_operational_alert_endpoint_is_authenticated_and_small_sample_safe(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BJ_PAL_CONTROL_TOKEN", TOKEN)
    monkeypatch.setenv("BJ_PAL_TRACE", "off")
    tracing.reset_backend_for_tests()
    app = create_app(
        service=_UnusedPlanningService(),
        readiness_probe=_ready,
        job_service=_EmptyWorkloadService(),
    )
    now = datetime.now(timezone.utc)
    params = {
        "window_start": (now - timedelta(minutes=5)).isoformat(),
        "window_end": (now - timedelta(minutes=1)).isoformat(),
    }

    with TestClient(app) as client:
        missing = client.get("/v1/operational-alerts", params=params)
        allowed = client.get(
            "/v1/operational-alerts",
            params=params,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        invalid = client.get(
            "/v1/operational-alerts",
            params={
                "window_start": params["window_end"],
                "window_end": params["window_start"],
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert missing.status_code == 401
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["overall_state"] == "insufficient_data"
    assert payload["insufficient_data_rule_count"] == 3
    assert payload["disabled_rule_count"] == 1
    assert payload["evaluated_rule_count"] == 0
    assert payload["rules"][-1]["reason_code"] == "otlp_export_not_configured"
    assert payload["links"]["trace_export"] == "/v1/trace-export-status"
    assert "tenant" not in json.dumps(payload)
    assert "endpoint_origin" not in json.dumps(payload)
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_operational_alert_window"


def test_openapi_exposes_read_scoped_operational_alert_contract(monkeypatch) -> None:
    monkeypatch.setenv("BJ_PAL_CONTROL_TOKEN", TOKEN)
    app = create_app(
        service=_UnusedPlanningService(),
        readiness_probe=_ready,
        job_service=_EmptyWorkloadService(),
    )

    operation = app.openapi()["paths"]["/v1/operational-alerts"]["get"]

    assert operation["security"] == [{"BJPalControlBearer": []}]
    assert "200" in operation["responses"]
    assert "400" in operation["responses"]
    assert "409" in operation["responses"]
    assert "500" in operation["responses"]
    assert "503" in operation["responses"]
