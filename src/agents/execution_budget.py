"""Request-local execution budget with privacy-minimized evidence.

The budget is a server-owned safety policy. It bounds logical LLM calls,
provider/data batches, instrumented tool calls, transport retries per LLM call,
reported token usage, and wall-clock time observed at safe checkpoints.

It deliberately does not claim active cancellation of an already-blocking
network call and does not estimate currency cost when a provider omits usage.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal, Mapping


EXECUTION_BUDGET_VERSION = "execution_budget_v1"
BudgetStatus = Literal["succeeded", "terminated"]
TerminationReason = Literal[
    "completed",
    "llm_call_limit",
    "data_provider_batch_limit",
    "tool_call_limit",
    "reported_token_limit",
    "wall_clock_limit",
]


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _env_int(
    environ: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ExecutionBudgetPolicy:
    """Trusted process policy; callers cannot override it per request."""

    max_llm_calls: int = 2
    max_data_provider_batches: int = 1
    max_tool_calls: int = 8
    max_transport_attempts_per_llm_call: int = 4
    max_reported_tokens: int = 32768
    max_wall_clock_ms: int = 120000

    def __post_init__(self) -> None:
        bounds = {
            "max_llm_calls": (self.max_llm_calls, 1, 32),
            "max_data_provider_batches": (
                self.max_data_provider_batches,
                1,
                32,
            ),
            "max_tool_calls": (self.max_tool_calls, 0, 256),
            "max_transport_attempts_per_llm_call": (
                self.max_transport_attempts_per_llm_call,
                1,
                8,
            ),
            "max_reported_tokens": (self.max_reported_tokens, 1, 10_000_000),
            "max_wall_clock_ms": (self.max_wall_clock_ms, 1, 3_600_000),
        }
        for field, (value, minimum, maximum) in bounds.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(
                    f"{field} must be between {minimum} and {maximum}"
                )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ExecutionBudgetPolicy":
        values = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            max_llm_calls=_env_int(
                values,
                "BJ_PAL_MAX_LLM_CALLS",
                defaults.max_llm_calls,
                minimum=1,
                maximum=32,
            ),
            max_data_provider_batches=_env_int(
                values,
                "BJ_PAL_MAX_DATA_PROVIDER_BATCHES",
                defaults.max_data_provider_batches,
                minimum=1,
                maximum=32,
            ),
            max_tool_calls=_env_int(
                values,
                "BJ_PAL_MAX_TOOL_CALLS",
                defaults.max_tool_calls,
                minimum=0,
                maximum=256,
            ),
            max_transport_attempts_per_llm_call=_env_int(
                values,
                "BJ_PAL_MAX_TRANSPORT_ATTEMPTS_PER_LLM_CALL",
                defaults.max_transport_attempts_per_llm_call,
                minimum=1,
                maximum=8,
            ),
            max_reported_tokens=_env_int(
                values,
                "BJ_PAL_MAX_REPORTED_TOKENS",
                defaults.max_reported_tokens,
                minimum=1,
                maximum=10_000_000,
            ),
            max_wall_clock_ms=_env_int(
                values,
                "BJ_PAL_MAX_EXECUTION_MS",
                defaults.max_wall_clock_ms,
                minimum=1,
                maximum=3_600_000,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionBudgetUsage:
    llm_call_count: int
    data_provider_batch_count: int
    tool_call_count: int
    reported_token_call_count: int
    reported_total_tokens: int | None
    elapsed_ms: float

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionBudgetSnapshot:
    version: str
    status: BudgetStatus
    termination_reason: TerminationReason
    policy: ExecutionBudgetPolicy
    usage: ExecutionBudgetUsage
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        status: BudgetStatus,
        termination_reason: TerminationReason,
        policy: ExecutionBudgetPolicy,
        usage: ExecutionBudgetUsage,
    ) -> "ExecutionBudgetSnapshot":
        payload = {
            "version": EXECUTION_BUDGET_VERSION,
            "status": status,
            "termination_reason": termination_reason,
            "policy": policy.to_dict(),
            "usage": usage.to_dict(),
        }
        return cls(
            version=EXECUTION_BUDGET_VERSION,
            status=status,
            termination_reason=termination_reason,
            policy=policy,
            usage=usage,
            artifact_sha256=_canonical_sha256(payload),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionBudgetSnapshot":
        return cls(
            version=str(payload["version"]),
            status=payload["status"],
            termination_reason=payload["termination_reason"],
            policy=ExecutionBudgetPolicy(**dict(payload["policy"])),
            usage=ExecutionBudgetUsage(**dict(payload["usage"])),
            artifact_sha256=str(payload["artifact_sha256"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "termination_reason": self.termination_reason,
            "policy": self.policy.to_dict(),
            "usage": self.usage.to_dict(),
            "artifact_sha256": self.artifact_sha256,
        }

    def verify_integrity(self) -> bool:
        payload = self.to_dict()
        observed = payload.pop("artifact_sha256")
        return (
            self.version == EXECUTION_BUDGET_VERSION
            and observed == _canonical_sha256(payload)
        )


class ExecutionBudgetExceeded(RuntimeError):
    """A server-owned budget stopped work at a safe checkpoint."""

    def __init__(self, snapshot: ExecutionBudgetSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"execution budget terminated: {snapshot.termination_reason}"
        )

    @property
    def code(self) -> str:
        return "execution_budget_exceeded"

    def safe_details(self) -> dict[str, Any]:
        return self.snapshot.to_dict()


class ExecutionBudgetTracker:
    """Mutable request-local counter that emits immutable snapshots."""

    def __init__(
        self,
        policy: ExecutionBudgetPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._started_at = clock()
        self._llm_calls = 0
        self._provider_batches = 0
        self._tool_calls = 0
        self._reported_token_calls = 0
        self._reported_tokens = 0
        self._terminated: ExecutionBudgetSnapshot | None = None
        self._lock = threading.Lock()

    def before_span(self, name: str) -> None:
        with self._lock:
            self._raise_if_terminated()
            self._check_wall_clock()
            reason: TerminationReason | None = None
            if name.startswith("llm.") and name.endswith(".complete"):
                self._llm_calls += 1
                if self._llm_calls > self.policy.max_llm_calls:
                    reason = "llm_call_limit"
            elif name == "planner.collect_data":
                self._provider_batches += 1
                if self._provider_batches > self.policy.max_data_provider_batches:
                    reason = "data_provider_batch_limit"
            elif name.startswith("tool."):
                self._tool_calls += 1
                if self._tool_calls > self.policy.max_tool_calls:
                    reason = "tool_call_limit"
            if reason is not None:
                self._terminate(reason)

    def after_span(self, name: str, attrs: Mapping[str, Any]) -> None:
        with self._lock:
            self._raise_if_terminated()
            if name.startswith("llm.") and name.endswith(".complete"):
                input_tokens = _reported_token(attrs.get("input_tokens"))
                output_tokens = _reported_token(attrs.get("output_tokens"))
                if input_tokens is not None or output_tokens is not None:
                    self._reported_token_calls += 1
                    self._reported_tokens += (input_tokens or 0) + (output_tokens or 0)
                    if self._reported_tokens > self.policy.max_reported_tokens:
                        self._terminate("reported_token_limit")
            self._check_wall_clock()

    def checkpoint(self) -> None:
        with self._lock:
            self._raise_if_terminated()
            self._check_wall_clock()

    def complete(self) -> ExecutionBudgetSnapshot:
        with self._lock:
            self._raise_if_terminated()
            self._check_wall_clock()
            return self._snapshot(status="succeeded", reason="completed")

    def _check_wall_clock(self) -> None:
        if self._elapsed_ms() > self.policy.max_wall_clock_ms:
            self._terminate("wall_clock_limit")

    def _terminate(self, reason: TerminationReason) -> None:
        snapshot = self._snapshot(status="terminated", reason=reason)
        self._terminated = snapshot
        raise ExecutionBudgetExceeded(snapshot)

    def _raise_if_terminated(self) -> None:
        if self._terminated is not None:
            raise ExecutionBudgetExceeded(self._terminated)

    def _snapshot(
        self,
        *,
        status: BudgetStatus,
        reason: TerminationReason,
    ) -> ExecutionBudgetSnapshot:
        return ExecutionBudgetSnapshot.create(
            status=status,
            termination_reason=reason,
            policy=self.policy,
            usage=ExecutionBudgetUsage(
                llm_call_count=self._llm_calls,
                data_provider_batch_count=self._provider_batches,
                tool_call_count=self._tool_calls,
                reported_token_call_count=self._reported_token_calls,
                reported_total_tokens=(
                    self._reported_tokens
                    if self._reported_token_calls
                    else None
                ),
                elapsed_ms=round(self._elapsed_ms(), 3),
            ),
        )

    def _elapsed_ms(self) -> float:
        return max(0.0, self._clock() - self._started_at) * 1000


def _reported_token(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_current_execution_budget: contextvars.ContextVar[
    ExecutionBudgetTracker | None
] = contextvars.ContextVar("bj_pal_execution_budget", default=None)


def current_execution_budget() -> ExecutionBudgetTracker | None:
    return _current_execution_budget.get()


def max_transport_attempts(default: int = 4) -> int:
    tracker = current_execution_budget()
    if tracker is None:
        return default
    return tracker.policy.max_transport_attempts_per_llm_call


@contextmanager
def enforce_execution_budget(
    policy: ExecutionBudgetPolicy,
    *,
    clock: Callable[[], float] = time.monotonic,
):
    tracker = ExecutionBudgetTracker(policy, clock=clock)
    token = _current_execution_budget.set(tracker)
    try:
        yield tracker
    finally:
        _current_execution_budget.reset(token)
