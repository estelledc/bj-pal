"""Contract and integration tests for the canonical planning use case."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, RerouteEvent, Step, UserPreferences  # noqa: E402
from application import (  # noqa: E402
    PlanRequest,
    PlanningCallbacks,
    PlanningCancelled,
    PlanningDeadlineExceeded,
    PlanningService,
)
from data_profile import DataProfile  # noqa: E402


def _plan(plan_id: str, poi_name: str) -> Plan:
    return Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(
                step_index=1,
                kind="meal",
                poi_id=f"poi-{plan_id}",
                poi_name=poi_name,
                start_time="14:00",
                rationale="contract fixture",
            )
        ],
        plan_id=plan_id,
    )


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={"pois": "fixture"},
        counts={"pois": 1},
        limitations=("not live data",),
    )


def test_request_rejects_empty_input_and_persona_mismatch() -> None:
    with pytest.raises(ValueError, match="user_input"):
        PlanRequest(user_input="   ")
    with pytest.raises(ValueError, match="persona"):
        PlanRequest(
            user_input="出去玩",
            persona="friends",
            preferences=UserPreferences(persona="family"),
        )


def test_service_forwards_request_and_returns_one_canonical_result() -> None:
    initial = _plan("plan-v1", "初始餐厅")
    final = _plan("plan-v2", "替补餐厅")
    route_evidence = {
        "version": "route_refresh_v1",
        "scope": "full_plan",
        "status": "complete",
    }
    schedule_evidence = {
        "version": "schedule_reconcile_v1",
        "status": "complete",
    }
    final.route_context = route_evidence
    final.schedule_context = schedule_evidence
    captured = {}
    callback_plans = []
    recorded_plans = []
    progress = []

    def planner(**kwargs):
        captured["planner"] = kwargs
        return initial

    def prober(plan, **kwargs):
        captured["prober"] = {"plan": plan, **kwargs}
        return final, [
            RerouteEvent(
                failed_step_idx=0,
                failed_poi_name="初始餐厅",
                reason="queue",
                replacement_poi_name="替补餐厅",
                route_refresh=route_evidence,
                schedule_refresh=schedule_evidence,
            )
        ]

    prefs = UserPreferences(persona="family", party_size=2)
    request = PlanRequest(
        user_input="  下午出去玩  ",
        preferences=prefs,
        user_id="user-contract",
    )
    result = PlanningService(
        planner=planner,
        prober=prober,
        profile_loader=_profile,
        plan_recorder=recorded_plans.append,
    ).execute(
        request,
        callbacks=PlanningCallbacks(
            on_progress=progress.append,
            on_initial_plan=callback_plans.append,
        ),
    )

    assert request.user_input == "下午出去玩"
    assert captured["planner"]["prefs"] is prefs
    assert captured["planner"]["user_id"] == "user-contract"
    assert captured["prober"]["plan"] is initial
    assert captured["prober"]["prefs"] is prefs
    assert captured["prober"]["auto_reroute"] is True
    assert callback_plans == [initial]
    assert any("检查方案风险" in message for message in progress)
    assert result.initial_plan is initial
    assert result.final_plan is final
    assert recorded_plans == [final]
    assert result.was_adjusted is True
    assert result.data_profile.name == "demo"
    serialized = result.to_dict()
    assert serialized["reroute_events"][0]["reason"] == "queue"
    assert serialized["reroute_events"][0]["route_refresh"] == route_evidence
    assert serialized["reroute_events"][0]["schedule_refresh"] == schedule_evidence
    assert serialized["final_plan"]["route_context"] == route_evidence
    assert serialized["final_plan"]["schedule_context"] == schedule_evidence
    assert serialized["requirements"]["version"] == "requirement_gate_v1"
    assert serialized["requirements"]["status"] == "proceed_with_assumptions"
    assert serialized["constraints"]["version"] == "constraint_ledger_v1"


def test_default_service_runs_the_public_offline_path() -> None:
    request = PlanRequest(
        user_input="周末下午带娃在五道营附近玩四小时",
        preferences=UserPreferences(
            persona="family",
            party_size=3,
            has_child=True,
            child_age=5,
            budget_per_person=120,
        ),
    )
    with patch.dict(os.environ, {"BJ_PAL_LLM": "mock"}, clear=False):
        result = PlanningService().execute(request)

    assert len(result.initial_plan.steps) >= 3
    assert len(result.final_plan.steps) >= 3
    assert result.data_profile.name == "demo"
    assert result.data_profile.public_reproducible is True
    assert all(step.confidence is not None for step in result.final_plan.steps)
    assert result.final_plan.weather_context is not None
    assert result.final_plan.weather_context["classification"] == "synthetic"
    assert result.final_plan.schedule_context["version"] == "schedule_reconcile_v1"
    assert result.final_plan.schedule_context["status"] == "complete"
    assert result.final_plan.schedule_context["overrun_minutes"] == 0
    assert {item["domain"] for item in result.final_plan.data_provenance} >= {"weather"}
    assert all(
        step.weather_shelter in {"open", "covered", "subway_direct", "full_indoor"}
        for step in result.final_plan.steps
        if step.kind != "depart"
    )
    rainy_steps = [
        step
        for step in result.final_plan.steps
        if step.start_time[:2] in {"14", "15"} and step.kind != "depart"
    ]
    assert all(step.weather_shelter != "open" for step in rainy_steps)

    from agents.plan_tracer import iter_steps

    traces = iter_steps(result.final_plan.plan_id)
    assert len(traces) == len(result.final_plan.steps)
    assert [trace.poi_id for trace in traces] == [step.poi_id for step in result.final_plan.steps]


def test_service_stops_at_safe_boundary_when_cancellation_is_requested() -> None:
    initial = _plan("plan-cancel", "初始餐厅")
    checks = iter((False, True))
    prober_calls = []
    recorded = []

    service = PlanningService(
        planner=lambda **kwargs: initial,
        prober=lambda *args, **kwargs: prober_calls.append((args, kwargs)),
        profile_loader=_profile,
        plan_recorder=recorded.append,
    )
    with pytest.raises(PlanningCancelled):
        service.execute(
            PlanRequest(user_input="下午出去玩"),
            callbacks=PlanningCallbacks(should_cancel=lambda: next(checks)),
        )

    assert prober_calls == []
    assert recorded == []


def test_service_stops_at_safe_boundary_when_durable_deadline_is_exceeded() -> None:
    initial = _plan("plan-timeout", "初始餐厅")
    checks = iter((False, True))
    prober_calls = []
    recorded = []

    service = PlanningService(
        planner=lambda **kwargs: initial,
        prober=lambda *args, **kwargs: prober_calls.append((args, kwargs)),
        profile_loader=_profile,
        plan_recorder=recorded.append,
    )
    with pytest.raises(PlanningDeadlineExceeded):
        service.execute(
            PlanRequest(user_input="下午出去玩"),
            callbacks=PlanningCallbacks(should_timeout=lambda: next(checks)),
        )

    assert prober_calls == []
    assert recorded == []


def test_ui_and_cli_depend_on_application_service_for_core_planning() -> None:
    ui_source = (ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")
    cli_source = (ROOT / "src" / "demo_cli.py").read_text(encoding="utf-8")

    for source in (ui_source, cli_source):
        assert (
            "PLANNING_SERVICE.execute" in source
            or "planning_service.execute" in source
        )
        assert "from agents.planner import plan" not in source
        assert "from agents.replanner import probe_plan" not in source
    assert "PlanningCallbacks" in ui_source
    assert "PlanRequest" in cli_source


def test_application_service_has_no_streamlit_dependency() -> None:
    from application import planning_service

    assert "streamlit" not in inspect.getsource(planning_service)
