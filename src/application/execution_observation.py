"""Privacy-minimized, self-checking execution evidence for one planning run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from agents.execution_budget import ExecutionBudgetSnapshot

EXECUTION_OBSERVATION_VERSION = "execution_observation_v2"
ExecutionStatus = Literal["succeeded", "failed", "not_observed"]
UsageCompleteness = Literal["complete", "partial", "unavailable", "not_applicable"]


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ObservedSpan:
    name: str
    span_id: str
    parent_span_id: str | None
    offset_ms: float
    duration_ms: float
    status: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TokenUsage:
    completeness: UsageCompleteness
    reported_calls: int
    input_tokens: int | None
    output_tokens: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionObservation:
    version: str
    status: ExecutionStatus
    execution_id: str
    correlation_id: str | None
    trace_id: str
    started_at: str | None
    duration_ms: float
    spans: tuple[ObservedSpan, ...]
    operation_counts: dict[str, int]
    business_counts: dict[str, int]
    token_usage: TokenUsage
    execution_budget: ExecutionBudgetSnapshot | None
    artifact_sha256: str

    @classmethod
    def not_observed(cls) -> "ExecutionObservation":
        payload = {
            "version": EXECUTION_OBSERVATION_VERSION,
            "status": "not_observed",
            "execution_id": "",
            "correlation_id": None,
            "trace_id": "",
            "started_at": None,
            "duration_ms": 0.0,
            "spans": [],
            "operation_counts": {
                "span_count": 0,
                "llm_call_count": 0,
                "data_provider_batch_count": 0,
                "tool_call_count": 0,
            },
            "business_counts": {
                "reroute_count": 0,
                "provider_issue_count": 0,
                "requirement_assumption_count": 0,
                "constraint_warning_count": 0,
            },
            "token_usage": {
                "completeness": "not_applicable",
                "reported_calls": 0,
                "input_tokens": None,
                "output_tokens": None,
            },
            "execution_budget": None,
        }
        return cls._from_payload(payload)

    @classmethod
    def from_trace_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        status: ExecutionStatus,
        reroute_count: int = 0,
        provider_issue_count: int = 0,
        requirement_assumption_count: int = 0,
        constraint_warning_count: int = 0,
        execution_budget: ExecutionBudgetSnapshot | None = None,
    ) -> "ExecutionObservation":
        raw_spans = snapshot.get("spans")
        if not isinstance(raw_spans, list) or not raw_spans:
            raise ValueError("an observed execution must contain spans")
        roots = [item for item in raw_spans if item.get("parent_span_id") is None]
        if len(roots) != 1 or roots[0].get("name") != "planning.execute":
            raise ValueError("execution trace must have exactly one planning.execute root")
        root = roots[0]
        root_start = float(root["started_at_epoch"])
        spans = tuple(
            ObservedSpan(
                name=str(item["name"]),
                span_id=str(item["span_id"]),
                parent_span_id=(
                    str(item["parent_span_id"])
                    if item.get("parent_span_id") is not None
                    else None
                ),
                offset_ms=round(
                    max(0.0, float(item["started_at_epoch"]) - root_start) * 1000,
                    3,
                ),
                duration_ms=round(max(0.0, float(item["duration_ms"])), 3),
                status=str(item["status"]),
                input_tokens=_optional_nonnegative_int(item.get("input_tokens")),
                output_tokens=_optional_nonnegative_int(item.get("output_tokens")),
            )
            for item in raw_spans
        )
        _validate_span_tree(spans)
        llm_spans = tuple(
            span for span in spans if span.name.startswith("llm.") and span.name.endswith(".complete")
        )
        reported = tuple(
            span
            for span in llm_spans
            if span.input_tokens is not None or span.output_tokens is not None
        )
        if not llm_spans:
            completeness: UsageCompleteness = "not_applicable"
        elif not reported:
            completeness = "unavailable"
        elif len(reported) == len(llm_spans):
            completeness = "complete"
        else:
            completeness = "partial"
        token_usage = TokenUsage(
            completeness=completeness,
            reported_calls=len(reported),
            input_tokens=(
                sum(span.input_tokens or 0 for span in reported) if reported else None
            ),
            output_tokens=(
                sum(span.output_tokens or 0 for span in reported) if reported else None
            ),
        )
        if execution_budget is not None:
            if not execution_budget.verify_integrity():
                raise ValueError("execution budget artifact failed integrity verification")
            if status == "succeeded" and (
                execution_budget.status != "succeeded"
                or execution_budget.termination_reason != "completed"
            ):
                raise ValueError("successful execution requires a completed budget")
            budget_usage = execution_budget.usage
            expected_reported_tokens = (
                sum((span.input_tokens or 0) + (span.output_tokens or 0) for span in reported)
                if reported
                else None
            )
            if (
                budget_usage.llm_call_count != len(llm_spans)
                or budget_usage.data_provider_batch_count
                != sum(span.name == "planner.collect_data" for span in spans)
                or budget_usage.tool_call_count
                != sum(span.name.startswith("tool.") for span in spans)
                or budget_usage.reported_token_call_count != len(reported)
                or budget_usage.reported_total_tokens != expected_reported_tokens
            ):
                raise ValueError("execution budget usage does not match trace spans")
        payload = {
            "version": EXECUTION_OBSERVATION_VERSION,
            "status": status,
            "execution_id": str(snapshot.get("execution_id") or ""),
            "correlation_id": snapshot.get("correlation_id"),
            "trace_id": str(snapshot.get("trace_id") or ""),
            "started_at": datetime.fromtimestamp(root_start, timezone.utc).isoformat(),
            "duration_ms": round(max(0.0, float(root["duration_ms"])), 3),
            "spans": [span.to_dict() for span in spans],
            "operation_counts": {
                "span_count": len(spans),
                "llm_call_count": len(llm_spans),
                "data_provider_batch_count": sum(
                    span.name == "planner.collect_data" for span in spans
                ),
                "tool_call_count": sum(span.name.startswith("tool.") for span in spans),
            },
            "business_counts": {
                "reroute_count": _nonnegative_int(reroute_count, "reroute_count"),
                "provider_issue_count": _nonnegative_int(
                    provider_issue_count, "provider_issue_count"
                ),
                "requirement_assumption_count": _nonnegative_int(
                    requirement_assumption_count,
                    "requirement_assumption_count",
                ),
                "constraint_warning_count": _nonnegative_int(
                    constraint_warning_count,
                    "constraint_warning_count",
                ),
            },
            "token_usage": token_usage.to_dict(),
            "execution_budget": (
                execution_budget.to_dict() if execution_budget is not None else None
            ),
        }
        return cls._from_payload(payload)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any]) -> "ExecutionObservation":
        artifact_sha256 = _canonical_sha256(payload)
        return cls(
            version=str(payload["version"]),
            status=payload["status"],
            execution_id=str(payload["execution_id"]),
            correlation_id=payload["correlation_id"],
            trace_id=str(payload["trace_id"]),
            started_at=payload["started_at"],
            duration_ms=float(payload["duration_ms"]),
            spans=tuple(ObservedSpan(**item) for item in payload["spans"]),
            operation_counts=dict(payload["operation_counts"]),
            business_counts=dict(payload["business_counts"]),
            token_usage=TokenUsage(**payload["token_usage"]),
            execution_budget=(
                ExecutionBudgetSnapshot.from_dict(payload["execution_budget"])
                if payload.get("execution_budget") is not None
                else None
            ),
            artifact_sha256=artifact_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "spans": [span.to_dict() for span in self.spans],
            "operation_counts": dict(self.operation_counts),
            "business_counts": dict(self.business_counts),
            "token_usage": self.token_usage.to_dict(),
            "execution_budget": (
                self.execution_budget.to_dict()
                if self.execution_budget is not None
                else None
            ),
            "artifact_sha256": self.artifact_sha256,
        }

    def verify_integrity(self) -> bool:
        payload = self.to_dict()
        observed = payload.pop("artifact_sha256")
        return observed == _canonical_sha256(payload)


def _validate_span_tree(spans: tuple[ObservedSpan, ...]) -> None:
    by_id = {span.span_id: span for span in spans}
    if len(by_id) != len(spans) or "" in by_id:
        raise ValueError("execution span IDs must be non-empty and unique")
    roots = [span for span in spans if span.parent_span_id is None]
    if len(roots) != 1:
        raise ValueError("execution span tree must have exactly one root")
    for span in spans:
        if span.parent_span_id is not None and span.parent_span_id not in by_id:
            raise ValueError("execution span references an unknown parent")
        if span.duration_ms < 0 or span.offset_ms < 0:
            raise ValueError("execution span timings must be non-negative")
        if span.status not in {"ok", "error"}:
            raise ValueError("execution span status must be ok or error")
        seen = {span.span_id}
        current = span
        while current.parent_span_id is not None:
            if current.parent_span_id in seen:
                raise ValueError("execution span tree contains a cycle")
            seen.add(current.parent_span_id)
            parent = by_id[current.parent_span_id]
            if current.offset_ms + 0.01 < parent.offset_ms:
                raise ValueError("execution child starts before its parent")
            if (
                current.offset_ms + current.duration_ms
                > parent.offset_ms + parent.duration_ms + 1.0
            ):
                raise ValueError("execution child ends after its parent")
            current = parent


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, "token usage")


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value
