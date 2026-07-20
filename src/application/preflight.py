"""One canonical preflight shared by synchronous and durable delivery paths."""

from __future__ import annotations

from dataclasses import dataclass

from .constraint_ledger import ConstraintLedger, ConstraintNormalizer
from .contracts import PlanRequest
from .requirement_gate import (
    ClarificationQuestion,
    PlanningClarificationRequired,
    REQUIREMENT_GATE_VERSION,
    RequirementDecision,
    RequirementNormalizer,
    UnresolvedRequirement,
)


@dataclass(frozen=True)
class PreflightResult:
    request: PlanRequest
    requirements: RequirementDecision
    constraints: ConstraintLedger


class PlanningPreflight:
    def __init__(
        self,
        *,
        requirement_normalizer: RequirementNormalizer | None = None,
        constraint_normalizer: ConstraintNormalizer | None = None,
    ) -> None:
        self.requirement_normalizer = requirement_normalizer or RequirementNormalizer()
        self.constraint_normalizer = constraint_normalizer or ConstraintNormalizer()

    def normalize(self, request: PlanRequest) -> PreflightResult:
        requirements = self.requirement_normalizer.normalize(request)
        if requirements.requires_clarification:
            raise PlanningClarificationRequired(requirements)
        request = request.with_area_anchor(requirements.resolved_area_anchor)

        constraint_result = self.constraint_normalizer.normalize(request)
        if constraint_result.requires_clarification:
            decision = _constraint_clarification(
                requirements=requirements,
                ledger=constraint_result.ledger,
            )
            raise PlanningClarificationRequired(
                decision,
                constraints=constraint_result.ledger,
            )
        return PreflightResult(
            request=constraint_result.request,
            requirements=requirements,
            constraints=constraint_result.ledger,
        )


def _constraint_clarification(
    *,
    requirements: RequirementDecision,
    ledger: ConstraintLedger,
) -> RequirementDecision:
    conflict = ledger.conflicts[0]
    return RequirementDecision(
        version=REQUIREMENT_GATE_VERSION,
        status="clarification_required",
        normalized_input=requirements.normalized_input,
        resolved_area_anchor=requirements.resolved_area_anchor,
        signals=requirements.signals,
        assumptions=requirements.assumptions,
        unresolved=(
            UnresolvedRequirement(
                code="constraint_conflict",
                field=conflict.field,
                evidence=conflict.evidence,
                reason=conflict.reason,
            ),
        ),
        questions=(
            ClarificationQuestion(
                code="clarify_constraint_conflict",
                field=conflict.field,
                prompt=f"{conflict.field} 应以哪一个值为准？",
                options=(
                    f"使用文本值：{conflict.text_value}",
                    f"使用结构化值：{conflict.structured_value}",
                    "重新描述该约束",
                ),
                reason=conflict.reason,
            ),
        ),
    )
