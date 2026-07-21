"""Local SQLite data-plane adapter for reproducible and cached profiles."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor, as_completed
from typing import Callable, Sequence

from data_profile import DataProfile, load_data_profile
from retrieval import ExplainableUGCRetriever, UGCRetrievalHit
from tools.amap_search import resolve_area_center, search_pois
from tools.types import POI, SearchConstraints
from tools.ugc_signals import summarize_area

from .contracts import DataEvidence, PlanningDataSnapshot, ProviderIssue, RetrievedEvidence
from .weather import (
    WeatherProvider,
    WeatherProviderError,
    WeatherRequest,
    WeatherSnapshot,
    create_weather_provider,
)


SearchFunction = Callable[..., list[POI]]
SummaryFunction = Callable[[str], dict]
RetrievalFunction = Callable[..., Sequence[UGCRetrievalHit]]


# FastAPI already runs the synchronous planning route in a worker thread. A
# per-request pool multiplied HTTP concurrency into dozens of short-lived data
# threads. This process-level executor bounds that resource without sharing
# SQLite connections or request results between calls.
_SHARED_READ_EXECUTOR = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="bj-pal-data",
)
_SHARED_WEATHER_LOCK = threading.Lock()
_SHARED_WEATHER_PROVIDER: WeatherProvider | None = None
_SHARED_WEATHER_INITIALIZED = False
_SHARED_WEATHER_ENV: tuple[str, ...] | None = None


def _shared_weather_provider() -> WeatherProvider | None:
    """Keep one cache-bearing adapter per effective process configuration."""

    import os

    global _SHARED_WEATHER_ENV
    global _SHARED_WEATHER_INITIALIZED
    global _SHARED_WEATHER_PROVIDER
    keys = (
        "BJ_PAL_WEATHER_PROVIDER",
        "BJ_PAL_OPEN_METEO_USAGE",
        "BJ_PAL_OPEN_METEO_BASE_URL",
        "OPEN_METEO_API_KEY",
        "BJ_PAL_OPEN_METEO_NONCOMMERCIAL_ACK",
        "BJ_PAL_WEATHER_TIMEOUT_SECONDS",
        "BJ_PAL_WEATHER_CACHE_TTL_SECONDS",
        "BJ_PAL_WEATHER_STALE_IF_ERROR_SECONDS",
    )
    fingerprint = tuple(os.environ.get(key, "") for key in keys)
    with _SHARED_WEATHER_LOCK:
        if not _SHARED_WEATHER_INITIALIZED or fingerprint != _SHARED_WEATHER_ENV:
            _SHARED_WEATHER_PROVIDER = create_weather_provider()
            _SHARED_WEATHER_ENV = fingerprint
            _SHARED_WEATHER_INITIALIZED = True
        return _SHARED_WEATHER_PROVIDER


class SQLitePlanningDataProvider:
    """Fan out independent reads, then merge their typed results once.

    Candidate branches are optional so a single category can degrade without
    being mistaken for a free or successful result. An entirely empty candidate
    set remains a required failure at the planner boundary.
    """

    def __init__(
        self,
        *,
        search: SearchFunction = search_pois,
        summarize: SummaryFunction = summarize_area,
        retrieve: RetrievalFunction | None = None,
        profile_loader: Callable[[], DataProfile] = load_data_profile,
        per_category_limit: int = 12,
        executor: Executor | None = None,
        weather_provider: WeatherProvider | None = None,
        weather_provider_loader: Callable[[], WeatherProvider | None] = _shared_weather_provider,
    ) -> None:
        self._search = search
        self._summarize = summarize
        self._retriever = ExplainableUGCRetriever()
        self._retrieve = retrieve or self._retriever.retrieve
        self._profile_loader = profile_loader
        self._per_category_limit = per_category_limit
        self._executor = executor or _SHARED_READ_EXECUTOR
        self._weather_provider = weather_provider or weather_provider_loader()

    def collect(
        self,
        *,
        query: str,
        area_anchor: str,
        constraints: SearchConstraints,
        categories: Sequence[str],
        target_local_time: str = "14:00",
        target_date: str | None = None,
    ) -> PlanningDataSnapshot:
        unique_categories = tuple(dict.fromkeys(categories))
        candidates: dict[str, tuple[POI, ...]] = {category: () for category in unique_categories}
        issues: list[ProviderIssue] = []
        area_summary: dict = {}
        retrieved_evidence: tuple[RetrievedEvidence, ...] = ()
        weather: WeatherSnapshot | None = None

        summary_future = self._executor.submit(self._summarize, area_anchor)
        retrieval_future = self._executor.submit(
            self._retrieve,
            query,
            top_k=5,
            area_anchor=area_anchor,
        )
        center = resolve_area_center(area_anchor)
        weather_future = None
        if self._weather_provider is not None and center is not None:
            weather_future = self._executor.submit(
                self._weather_provider.forecast,
                WeatherRequest(
                    latitude=center[1],
                    longitude=center[0],
                    target_local_time=target_local_time,
                    target_date=target_date,
                ),
            )
        elif self._weather_provider is not None:
            issues.append(
                ProviderIssue(
                    domain="weather",
                    code="weather_area_unresolved",
                    retryable=False,
                    required=False,
                    message="Weather lookup skipped because the area anchor has no safe center point.",
                )
            )
        futures: dict[Future[list[POI]], str] = {
            self._executor.submit(
                self._search,
                area_anchor=area_anchor,
                category=category,
                constraints=constraints,
                limit=self._per_category_limit,
            ): category
            for category in unique_categories
        }
        for future in as_completed(futures):
            category = futures[future]
            try:
                candidates[category] = tuple(future.result())
            except (FileNotFoundError, OSError, sqlite3.Error):
                issues.append(
                    ProviderIssue(
                        domain=f"poi:{category}",
                        code="candidate_source_unavailable",
                        retryable=True,
                        required=False,
                        message=f"Candidate data for category {category} is unavailable.",
                    )
                )
        food_source_failed = any(
            issue.domain == "poi:food" and issue.code == "candidate_source_unavailable"
            for issue in issues
        )
        if (
            "food" in candidates
            and constraints.diet_flags
            and not candidates["food"]
            and not food_source_failed
        ):
            issues.append(
                ProviderIssue(
                    domain="poi:food",
                    code="diet_evidence_unavailable",
                    retryable=False,
                    required=False,
                    message=(
                        "No food candidates had positive structured evidence for "
                        "every explicit dietary constraint; food planning was omitted."
                    ),
                )
            )
        try:
            area_summary = summary_future.result()
        except (FileNotFoundError, OSError, sqlite3.Error):
            issues.append(
                ProviderIssue(
                    domain="ugc",
                    code="area_context_unavailable",
                    retryable=True,
                    required=False,
                    message="Area context is unavailable; planning continues without UGC signals.",
                )
            )
        try:
            retrieval_hits = tuple(retrieval_future.result())
            retrieved_evidence = tuple(
                RetrievedEvidence(
                    domain="ugc",
                    item_id=hit.record_id,
                    subject=hit.poi_name,
                    text=hit.evidence_summary,
                    score=hit.final_score,
                    algorithm=self._retriever.algorithm,
                    matched_features=hit.matched_features,
                    expanded_terms=hit.expanded_terms,
                )
                for hit in retrieval_hits
            )
        except (FileNotFoundError, OSError, sqlite3.Error, RuntimeError):
            issues.append(
                ProviderIssue(
                    domain="ugc:query_retrieval",
                    code="query_evidence_unavailable",
                    retryable=True,
                    required=False,
                    message="Query-specific UGC evidence is unavailable; planning continues.",
                )
            )

        if weather_future is not None:
            try:
                weather = weather_future.result()
            except WeatherProviderError as exc:
                issues.append(
                    ProviderIssue(
                        domain="weather",
                        code=exc.code,
                        retryable=exc.retryable,
                        required=False,
                        message=exc.safe_message,
                    )
                )

        profile = self._profile_loader()
        evidence = list(self._evidence(profile))
        if weather is not None:
            evidence.append(self._weather_evidence(weather))
        return PlanningDataSnapshot(
            area_summary=area_summary,
            candidates={category: candidates[category] for category in unique_categories},
            evidence=tuple(evidence),
            retrieved_evidence=retrieved_evidence,
            issues=tuple(sorted(issues, key=lambda item: item.domain)),
            weather=weather,
        )

    @staticmethod
    def _evidence(profile: DataProfile) -> tuple[DataEvidence, ...]:
        freshness = "not_applicable" if profile.contains_synthetic_data else "cache_timestamp_missing"
        common_warnings = tuple(profile.limitations)
        return tuple(
            DataEvidence(
                domain=domain,
                provider="sqlite-data-profile",
                source=profile.sources.get(source_key, "unknown"),
                classification=profile.classification,
                provider_reference=f"dataset:{profile.name}:{source_key}",
                freshness=freshness,
                bookable=False,
                warnings=common_warnings,
            )
            for domain, source_key in (("poi", "pois"), ("ugc", "ugc"), ("route", "routes"))
        )

    @staticmethod
    def _weather_evidence(snapshot: WeatherSnapshot) -> DataEvidence:
        return DataEvidence(
            domain="weather",
            provider=snapshot.provider,
            source=snapshot.source,
            classification=snapshot.classification,
            provider_reference=snapshot.provider_reference,
            freshness=snapshot.freshness,
            retrieved_at=snapshot.retrieved_at,
            valid_until=snapshot.valid_until,
            bookable=False,
            warnings=snapshot.warnings
            + (
                f"{snapshot.attribution}: {snapshot.attribution_url}",
                f"License: {snapshot.license_url}",
            ),
        )
