"""Compose clarification decisions into durable, typed continuation sessions."""

from __future__ import annotations

from typing import Any

from application import (
    ClarificationResolution,
    PlanRequest,
    PlanningClarificationRequired,
)

from .models import ClarificationOption, ClarificationSession
from .repository import ClarificationRepository


class ClarificationContinuationService:
    def __init__(
        self,
        *,
        repository: ClarificationRepository | None = None,
        ttl_seconds: int = 900,
    ) -> None:
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("clarification ttl_seconds must be between 60 and 86400")
        self.repository = repository or ClarificationRepository()
        self.ttl_seconds = ttl_seconds

    def get(self, continuation_id: str) -> ClarificationSession | None:
        return self.repository.get(continuation_id)

    def claim_execution(
        self,
        *,
        continuation_id: str,
        owner: str,
        lease_seconds: int = 900,
    ) -> ClarificationSession:
        return self.repository.claim_execution(
            continuation_id=continuation_id,
            owner=owner,
            lease_seconds=lease_seconds,
        )

    def complete(
        self,
        *,
        continuation_id: str,
        owner: str,
        result_payload: dict[str, Any],
    ) -> ClarificationSession:
        return self.repository.complete(
            continuation_id=continuation_id,
            owner=owner,
            result_payload=result_payload,
        )

    def release_execution(self, *, continuation_id: str, owner: str) -> None:
        self.repository.release_execution(
            continuation_id=continuation_id,
            owner=owner,
        )

    def issue(
        self,
        *,
        request: PlanRequest,
        error: PlanningClarificationRequired,
        delivery: str,
        job_policy: dict[str, Any] | None = None,
    ) -> ClarificationSession:
        options = _build_options(request, error)
        return self.repository.issue(
            delivery=delivery,
            request_payload=request.to_dict(),
            decision_payload=error.decision.to_dict(),
            constraints_payload=(
                error.constraints.to_dict() if error.constraints is not None else None
            ),
            job_policy=dict(job_policy or {}),
            options=options,
            ttl_seconds=self.ttl_seconds,
        )

    def resolve_request(
        self,
        *,
        continuation_id: str,
        delivery: str,
        option_id: str,
        answer: str | None = None,
    ) -> tuple[ClarificationSession, PlanRequest]:
        session = self.repository.get(continuation_id)
        if session is None:
            from .repository import ClarificationNotFound

            raise ClarificationNotFound("clarification continuation not found")
        if session.status == "expired":
            from .repository import ClarificationExpired

            raise ClarificationExpired("clarification continuation expired")
        if session.delivery != delivery:
            from .repository import InvalidClarificationTransition

            raise InvalidClarificationTransition(
                "clarification continuation delivery does not match this endpoint"
            )
        option = next(
            (item for item in session.options if item.option_id == option_id),
            None,
        )
        if option is None:
            raise ValueError("clarification option_id is not allowed for this session")

        normalized_answer = answer.strip() if answer else ""
        if option.requires_answer and not normalized_answer:
            raise ValueError("this clarification option requires an answer")
        if not option.requires_answer and normalized_answer:
            raise ValueError("this clarification option does not accept a free-form answer")

        original = PlanRequest.from_dict(session.request_payload)
        unresolved = session.decision_payload.get("unresolved") or []
        if not unresolved:
            raise ValueError("clarification session has no unresolved requirement")
        code = str(unresolved[0]["code"])
        field = option.field
        value = normalized_answer if option.requires_answer else option.value

        if field == "user_input":
            if option.option_id == "restart_with_area":
                value = f"在{original.area_anchor}重新生成本次短时活动方案"
            if not isinstance(value, str) or not value.strip():
                raise ValueError("clarified user_input must not be empty")
            value = value.strip()
        elif field == "area_anchor":
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
                raise ValueError("clarified area_anchor must contain 1-100 characters")
            value = value.strip()

        resolution = ClarificationResolution(
            code=code,
            field=field,
            value=value,
            option_id=option.option_id,
            answer=normalized_answer or option.label,
            decision_sha256=session.decision_sha256,
        )
        resolved = original.with_resolution(resolution)
        if field == "user_input":
            resolved = resolved.with_user_input(value)
        session = self.repository.resolve(
            continuation_id=continuation_id,
            resolution_payload=resolution.to_dict(),
            resolved_request_payload=resolved.to_dict(),
        )
        assert session.resolved_request_payload is not None
        return session, PlanRequest.from_dict(session.resolved_request_payload)


def _build_options(
    request: PlanRequest,
    error: PlanningClarificationRequired,
) -> tuple[ClarificationOption, ...]:
    unresolved = error.decision.unresolved[0]
    question = error.decision.questions[0]
    if error.constraints is not None and error.constraints.conflicts:
        conflict = error.constraints.conflicts[0]
        return (
            ClarificationOption(
                option_id="use_text_value",
                label=f"使用文本值：{conflict.text_value}",
                field=conflict.field,
                value=conflict.text_value,
            ),
            ClarificationOption(
                option_id="use_structured_value",
                label=f"使用结构化值：{conflict.structured_value}",
                field=conflict.field,
                value=conflict.structured_value,
            ),
        )

    if unresolved.field == "area_anchor":
        options: list[ClarificationOption] = []
        for index, label in enumerate(question.options):
            if label.startswith("使用") and len(label) > 2:
                options.append(
                    ClarificationOption(
                        option_id=f"use_area_{index + 1}",
                        label=label,
                        field="area_anchor",
                        value=label[2:],
                    )
                )
            elif not any(item.requires_answer for item in options):
                options.append(
                    ClarificationOption(
                        option_id="provide_area",
                        label="补充其他片区或具体地点",
                        field="area_anchor",
                        requires_answer=True,
                    )
                )
        if len(options) < 2:
            options.append(
                ClarificationOption(
                    option_id="provide_area",
                    label="补充其他片区或具体地点",
                    field="area_anchor",
                    requires_answer=True,
                )
            )
        return tuple(options[:3])

    if unresolved.field == "user_input":
        return (
            ClarificationOption(
                option_id="provide_revised_request",
                label="重新描述本次完整需求",
                field="user_input",
                requires_answer=True,
            ),
            ClarificationOption(
                option_id="restart_with_area",
                label=f"只保留{request.area_anchor}并重新生成",
                field="user_input",
                value=f"在{request.area_anchor}重新生成本次短时活动方案",
            ),
        )

    raise ValueError(f"unsupported clarification field: {unresolved.field}")
