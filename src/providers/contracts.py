"""Contracts separating planning decisions from data acquisition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Protocol, Sequence

from tools.types import POI, SearchConstraints

from .weather import WeatherSnapshot


@dataclass(frozen=True)
class DataEvidence:
    """Where one class of planning evidence came from and what it can prove."""

    domain: str
    provider: str
    source: str
    classification: str
    provider_reference: str
    freshness: str
    retrieved_at: str | None = None
    valid_until: str | None = None
    bookable: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProviderIssue:
    """A structured, non-secret description of a degraded provider branch."""

    domain: str
    code: str
    retryable: bool
    required: bool
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetrievedEvidence:
    """One query-specific evidence item passed into the decision layer."""

    domain: str
    item_id: str
    subject: str
    text: str
    score: float
    algorithm: str
    matched_features: tuple[str, ...] = ()
    expanded_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanningDataSnapshot:
    """Immutable fan-out results merged once before planning."""

    area_summary: Mapping[str, object]
    candidates: Mapping[str, tuple[POI, ...]]
    evidence: tuple[DataEvidence, ...]
    retrieved_evidence: tuple[RetrievedEvidence, ...] = ()
    issues: tuple[ProviderIssue, ...] = ()
    weather: WeatherSnapshot | None = None


class PlanningDataProvider(Protocol):
    def collect(
        self,
        *,
        query: str,
        area_anchor: str,
        constraints: SearchConstraints,
        categories: Sequence[str],
        target_local_time: str = "14:00",
        target_date: str | None = None,
    ) -> PlanningDataSnapshot: ...
