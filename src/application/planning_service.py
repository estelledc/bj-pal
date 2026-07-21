"""End-to-end planning use case shared by delivery adapters."""

from __future__ import annotations

from typing import Callable, Optional

from agents.execution_budget import (
    ExecutionBudgetPolicy,
    enforce_execution_budget,
)
from agents.planner import plan as default_plan
from agents.planner import record_plan_to_tracer as default_record_plan
from agents.replanner import probe_plan as default_probe_plan
from agents.tracing import capture_execution, trace_span
from agents.types import Plan, RerouteEvent, UserPreferences
from data_profile import DataProfile, load_data_profile

from .contracts import (
    PlanRequest,
    PlanResult,
    PlanningCallbacks,
    PlanningCancelled,
    PlanningDeadlineExceeded,
)
from .execution_observation import ExecutionObservation
from .constraint_ledger import ConstraintNormalizer
from .preflight import PlanningPreflight, PreflightResult
from .requirement_gate import RequirementNormalizer


Planner = Callable[..., Plan]
Prober = Callable[..., tuple[Plan, list[RerouteEvent]]]
ProfileLoader = Callable[[], DataProfile]
PlanRecorder = Callable[[Plan], None]


class PlanningService:
    """Orchestrate generation, risk probing, and provenance in one path.

    The service owns workflow order. Planner and probe implementations stay
    injectable so contract tests do not need Streamlit, a network, or SQLite.
    """

    def __init__(
        self,
        *,
        planner: Optional[Planner] = None,
        prober: Optional[Prober] = None,
        profile_loader: Optional[ProfileLoader] = None,
        plan_recorder: Optional[PlanRecorder] = None,
        requirement_normalizer: Optional[RequirementNormalizer] = None,
        constraint_normalizer: Optional[ConstraintNormalizer] = None,
        preflight: Optional[PlanningPreflight] = None,
        execution_budget_policy: Optional[ExecutionBudgetPolicy] = None,
        budget_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._planner = planner or default_plan
        self._prober = prober or default_probe_plan
        self._profile_loader = profile_loader or load_data_profile
        self._plan_recorder = plan_recorder or default_record_plan
        self._execution_budget_policy = (
            execution_budget_policy or ExecutionBudgetPolicy.from_env()
        )
        self._budget_clock = budget_clock
        self._preflight = preflight or PlanningPreflight(
            requirement_normalizer=requirement_normalizer,
            constraint_normalizer=constraint_normalizer,
        )

    @property
    def requirement_normalizer(self) -> RequirementNormalizer:
        return self._preflight.requirement_normalizer

    @property
    def constraint_normalizer(self) -> ConstraintNormalizer:
        return self._preflight.constraint_normalizer

    @property
    def preflight_engine(self) -> PlanningPreflight:
        return self._preflight

    @property
    def execution_budget_policy(self) -> ExecutionBudgetPolicy:
        return self._execution_budget_policy

    def preflight(self, request: PlanRequest) -> PreflightResult:
        """Normalize execution-critical fields and stop unresolved requests."""
        return self._preflight.normalize(request)

    def execute(
        self,
        request: PlanRequest,
        *,
        callbacks: Optional[PlanningCallbacks] = None,
    ) -> PlanResult:
        callbacks = callbacks or PlanningCallbacks()
        budget_kwargs = {}
        if self._budget_clock is not None:
            budget_kwargs["clock"] = self._budget_clock
        with enforce_execution_budget(
            self._execution_budget_policy,
            **budget_kwargs,
        ) as execution_budget:
            with capture_execution(callbacks.correlation_id) as execution_capture:
                with trace_span("planning.execute"):
                    self._raise_if_stopped(callbacks, execution_budget)
                    with trace_span("planning.preflight"):
                        preflight = self.preflight(request)
                        request = preflight.request
                    self._raise_if_stopped(callbacks, execution_budget)
                    with trace_span("planning.generate"):
                        initial_plan = self._planner(
                            user_input=request.user_input,
                            persona=request.persona,
                            prefs=request.preferences,
                            area_anchor=request.area_anchor,
                            user_id=request.user_id,
                            on_token=callbacks.on_token,
                            on_progress=callbacks.on_progress,
                            on_stream_event=callbacks.on_stream_event,
                        )
                    if callbacks.on_initial_plan is not None:
                        callbacks.on_initial_plan(initial_plan)
                    self._raise_if_stopped(callbacks, execution_budget)

                    if callbacks.on_progress is not None:
                        callbacks.on_progress("检查方案风险：排队、天气、营业和预约状态")
                    with trace_span("planning.probe_and_replan"):
                        final_plan, events = self._prober(
                            initial_plan,
                            prefs=request.preferences,
                            auto_reroute=request.auto_reroute,
                        )
                    self._raise_if_stopped(callbacks, execution_budget)
                    # Planner 记录的是初版；probe/reroute 后必须用最终方案替换同 plan_id trace。
                    with trace_span("planning.persist_trace"):
                        self._plan_recorder(final_plan)
                    with trace_span("planning.load_data_profile"):
                        data_profile = self._profile_loader()
            budget_snapshot = execution_budget.complete()
        execution = ExecutionObservation.from_trace_snapshot(
            execution_capture.snapshot(),
            status="succeeded",
            reroute_count=len(events),
            provider_issue_count=len(final_plan.data_warnings),
            requirement_assumption_count=len(preflight.requirements.assumptions),
            constraint_warning_count=len(preflight.constraints.warnings),
            execution_budget=budget_snapshot,
        )
        return PlanResult(
            request=request,
            initial_plan=initial_plan,
            final_plan=final_plan,
            reroute_events=tuple(events),
            data_profile=data_profile,
            requirements=preflight.requirements,
            constraints=preflight.constraints,
            execution=execution,
        )

    @staticmethod
    def _raise_if_stopped(callbacks: PlanningCallbacks, execution_budget) -> None:
        execution_budget.checkpoint()
        if callbacks.should_cancel is not None and callbacks.should_cancel():
            raise PlanningCancelled("planning job cancellation requested")
        if callbacks.should_timeout is not None and callbacks.should_timeout():
            raise PlanningDeadlineExceeded("planning job durable deadline exceeded")
