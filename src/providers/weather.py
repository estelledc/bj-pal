"""Typed weather providers with an offline-first Open-Meteo boundary.

The default provider reads a deterministic synthetic contract fixture. Live
Open-Meteo access is opt-in because the public free endpoint is restricted to
non-commercial use; portfolio/promotional deployments must use a commercial or
self-hosted endpoint instead of silently calling the free service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from tools.weather_shelter import WeatherContext, WeatherState


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURE_PATH = ROOT / "fixtures" / "weather" / "beijing_synthetic.json"
OPEN_METEO_FREE_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_COMMERCIAL_ENDPOINT = "https://customer-api.open-meteo.com/v1/forecast"
OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com"
OPEN_METEO_ATTRIBUTION_URL = "https://open-meteo.com/"
OPEN_METEO_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

WeatherUsage = Literal["noncommercial", "commercial", "self_hosted"]
CacheStatus = Literal["miss", "hit", "stale", "fixture"]


class WeatherProviderError(RuntimeError):
    """A safe, structured weather branch failure."""

    def __init__(self, *, code: str, retryable: bool, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.safe_message = message


class WeatherConfigurationError(WeatherProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(code="weather_configuration_invalid", retryable=False, message=message)


@dataclass(frozen=True)
class WeatherRequest:
    latitude: float
    longitude: float
    target_local_time: str = "14:00"
    target_date: str | None = None
    timezone_name: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("weather latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("weather longitude must be between -180 and 180")
        _parse_local_time(self.target_local_time)
        if self.target_date is not None:
            try:
                date.fromisoformat(self.target_date)
            except ValueError as exc:
                raise ValueError("weather target_date must use YYYY-MM-DD") from exc
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("weather timezone_name must be an IANA timezone") from exc

    def target_at(self, now: datetime) -> datetime:
        zone = ZoneInfo(self.timezone_name)
        local_now = _as_aware_utc(now).astimezone(zone)
        target_clock = _parse_local_time(self.target_local_time)
        target_day = (
            date.fromisoformat(self.target_date)
            if self.target_date is not None
            else local_now.date()
        )
        target = datetime.combine(target_day, target_clock, tzinfo=zone)
        if self.target_date is None and target < local_now - timedelta(minutes=30):
            target += timedelta(days=1)
        return target

    def cache_key(self, now: datetime) -> tuple[float, float, str, str]:
        target = self.target_at(now)
        return (
            round(self.latitude, 4),
            round(self.longitude, 4),
            self.timezone_name,
            target.isoformat(timespec="minutes"),
        )


@dataclass(frozen=True)
class WeatherHour:
    local_time: str
    temperature_c: float
    apparent_temperature_c: float
    precipitation_probability_pct: int
    precipitation_mm: float
    weather_code: int
    wind_speed_kmh: float
    state: WeatherState
    severity: float
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WeatherSnapshot:
    provider: str
    source: str
    classification: str
    provider_reference: str
    model: str
    timezone_name: str
    latitude: float
    longitude: float
    target_at: str
    retrieved_at: str | None
    valid_until: str | None
    freshness: str
    cache_status: CacheStatus
    hours: tuple[WeatherHour, ...]
    attribution: str = OPEN_METEO_ATTRIBUTION
    attribution_url: str = OPEN_METEO_ATTRIBUTION_URL
    license_url: str = OPEN_METEO_LICENSE_URL
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hours:
            raise ValueError("weather snapshot must contain at least one hourly point")

    def context_at(self, local_time: str) -> WeatherContext:
        wanted = _minutes_of_day(_parse_local_time(local_time))
        point = min(
            self.hours,
            key=lambda item: abs(_minutes_of_day(_parse_iso_local_time(item.local_time)) - wanted),
        )
        return WeatherContext(
            state=point.state,
            description=point.description,
            severity=point.severity,
        )

    def to_decision_context(self, *, max_hours: int = 8) -> dict:
        return {
            "provider": self.provider,
            "classification": self.classification,
            "provider_reference": self.provider_reference,
            "model": self.model,
            "timezone": self.timezone_name,
            "target_at": self.target_at,
            "freshness": self.freshness,
            "retrieved_at": self.retrieved_at,
            "valid_until": self.valid_until,
            "attribution": self.attribution,
            "attribution_url": self.attribution_url,
            "license_url": self.license_url,
            "hours": [item.to_dict() for item in self.hours[:max_hours]],
            "warnings": list(self.warnings),
        }


class WeatherProvider(Protocol):
    def forecast(self, request: WeatherRequest) -> WeatherSnapshot: ...


@dataclass(frozen=True)
class WeatherHTTPResponse:
    status_code: int
    payload: Mapping[str, object]


class WeatherTransport(Protocol):
    def get(
        self,
        *,
        url: str,
        params: Mapping[str, object],
        timeout_seconds: float,
    ) -> WeatherHTTPResponse: ...


class HTTPXWeatherTransport:
    def get(
        self,
        *,
        url: str,
        params: Mapping[str, object],
        timeout_seconds: float,
    ) -> WeatherHTTPResponse:
        try:
            response = httpx.get(url, params=params, timeout=timeout_seconds)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise WeatherProviderError(
                code="weather_transport_unavailable",
                retryable=True,
                message="Weather provider could not be reached within the bounded timeout.",
            ) from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise WeatherProviderError(
                code="weather_response_invalid_json",
                retryable=False,
                message="Weather provider returned an invalid JSON response.",
            ) from exc
        if not isinstance(payload, Mapping):
            raise WeatherProviderError(
                code="weather_response_invalid_schema",
                retryable=False,
                message="Weather provider response root must be an object.",
            )
        return WeatherHTTPResponse(status_code=response.status_code, payload=payload)


@dataclass(frozen=True)
class OpenMeteoConfig:
    usage: WeatherUsage
    endpoint: str
    api_key: str | None = None
    noncommercial_ack: bool = False
    timeout_seconds: float = 3.0
    cache_ttl_seconds: int = 900
    stale_if_error_seconds: int = 3600

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WeatherConfigurationError("Open-Meteo endpoint must be an absolute HTTP URL.")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise WeatherConfigurationError("Weather timeout must be in (0, 30] seconds.")
        if self.cache_ttl_seconds < 1 or self.stale_if_error_seconds < 0:
            raise WeatherConfigurationError("Weather cache TTL values must be non-negative.")

        host = parsed.hostname or ""
        if host == "api.open-meteo.com":
            if self.usage != "noncommercial" or not self.noncommercial_ack:
                raise WeatherConfigurationError(
                    "The Open-Meteo free endpoint requires explicit non-commercial acknowledgement."
                )
            if self.api_key:
                raise WeatherConfigurationError("The free Open-Meteo endpoint must not receive an API key.")
        elif host == "customer-api.open-meteo.com":
            if self.usage != "commercial" or not self.api_key:
                raise WeatherConfigurationError(
                    "The Open-Meteo commercial endpoint requires commercial usage and an API key."
                )
        elif self.usage != "self_hosted":
            raise WeatherConfigurationError(
                "Custom weather endpoints must be declared as self_hosted."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OpenMeteoConfig":
        values = env if env is not None else os.environ
        usage = str(values.get("BJ_PAL_OPEN_METEO_USAGE") or "").strip().lower()
        if usage not in {"noncommercial", "commercial", "self_hosted"}:
            raise WeatherConfigurationError(
                "BJ_PAL_OPEN_METEO_USAGE must be noncommercial, commercial, or self_hosted."
            )
        endpoint = str(values.get("BJ_PAL_OPEN_METEO_BASE_URL") or "").strip()
        if not endpoint:
            endpoint = (
                OPEN_METEO_FREE_ENDPOINT
                if usage == "noncommercial"
                else OPEN_METEO_COMMERCIAL_ENDPOINT
                if usage == "commercial"
                else ""
            )
        if not endpoint:
            raise WeatherConfigurationError("Self-hosted weather mode requires BJ_PAL_OPEN_METEO_BASE_URL.")
        return cls(
            usage=usage,  # type: ignore[arg-type]
            endpoint=endpoint,
            api_key=(str(values.get("OPEN_METEO_API_KEY") or "").strip() or None),
            noncommercial_ack=_env_true(values.get("BJ_PAL_OPEN_METEO_NONCOMMERCIAL_ACK")),
            timeout_seconds=_env_float(values, "BJ_PAL_WEATHER_TIMEOUT_SECONDS", 3.0),
            cache_ttl_seconds=_env_int(values, "BJ_PAL_WEATHER_CACHE_TTL_SECONDS", 900),
            stale_if_error_seconds=_env_int(
                values,
                "BJ_PAL_WEATHER_STALE_IF_ERROR_SECONDS",
                3600,
            ),
        )


class OpenMeteoWeatherProvider:
    """Fetch model forecasts with bounded I/O and a process-local TTL cache."""

    def __init__(
        self,
        config: OpenMeteoConfig,
        *,
        transport: WeatherTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or HTTPXWeatherTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache: dict[tuple[float, float, str, str], WeatherSnapshot] = {}
        self._lock = threading.Lock()

    def forecast(self, request: WeatherRequest) -> WeatherSnapshot:
        now = _as_aware_utc(self._clock())
        key = request.cache_key(now)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and _parse_iso_datetime(cached.valid_until) > now:
            return replace(cached, freshness="fresh_cache", cache_status="hit")

        try:
            snapshot = self._fetch(request, now)
        except WeatherProviderError as exc:
            if cached is not None and _within_stale_window(
                cached,
                now,
                self._config.stale_if_error_seconds,
            ):
                return replace(
                    cached,
                    freshness="stale_if_error",
                    cache_status="stale",
                    warnings=cached.warnings
                    + (f"Live refresh failed ({exc.code}); stale weather is exposed explicitly.",),
                )
            raise

        with self._lock:
            self._cache[key] = snapshot
        return snapshot

    def _fetch(self, request: WeatherRequest, now: datetime) -> WeatherSnapshot:
        if request.target_date is None:
            raise WeatherProviderError(
                code="weather_target_date_unresolved",
                retryable=False,
                message="Live weather lookup requires an explicit resolved target date.",
            )
        target_at = request.target_at(now)
        local_today = now.astimezone(ZoneInfo(request.timezone_name)).date()
        horizon_days = (target_at.date() - local_today).days
        if horizon_days < 0 or horizon_days > 15:
            raise WeatherProviderError(
                code="weather_target_outside_forecast_horizon",
                retryable=False,
                message="Weather target date is outside the supported 16-day forecast horizon.",
            )
        params: dict[str, object] = {
            "latitude": request.latitude,
            "longitude": request.longitude,
            "timezone": request.timezone_name,
            "start_date": request.target_date,
            "end_date": request.target_date,
            "models": "best_match",
            "hourly": ",".join(
                (
                    "temperature_2m",
                    "apparent_temperature",
                    "precipitation_probability",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                )
            ),
        }
        if self._config.api_key:
            params["apikey"] = self._config.api_key

        response = self._transport.get(
            url=self._config.endpoint,
            params=params,
            timeout_seconds=self._config.timeout_seconds,
        )
        _raise_for_status(response.status_code)
        hours = _parse_open_meteo_hours(
            response.payload,
            request=request,
            target_at=target_at,
            normalize_fixture_date=False,
        )
        valid_until = now + timedelta(seconds=self._config.cache_ttl_seconds)
        hosted = (urlparse(self._config.endpoint).hostname or "").endswith("open-meteo.com")
        warnings = ["Forecast values are model-derived, not station observations."]
        if hosted:
            warnings.append("Only area-anchor coordinates are sent; provider may retain request logs.")
        return WeatherSnapshot(
            provider="open-meteo",
            source=self._config.endpoint,
            classification="live_model_forecast",
            provider_reference="open-meteo:forecast:best_match",
            model="best_match",
            timezone_name=request.timezone_name,
            latitude=float(response.payload.get("latitude", request.latitude)),
            longitude=float(response.payload.get("longitude", request.longitude)),
            target_at=target_at.isoformat(timespec="minutes"),
            retrieved_at=now.isoformat(timespec="seconds"),
            valid_until=valid_until.isoformat(timespec="seconds"),
            freshness="fresh",
            cache_status="miss",
            hours=hours,
            warnings=tuple(warnings),
        )


class RecordedWeatherProvider:
    """Read a synthetic Open-Meteo-shaped response without network access."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._fixture_path = fixture_path

    def forecast(self, request: WeatherRequest) -> WeatherSnapshot:
        try:
            raw = self._fixture_path.read_bytes()
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise WeatherProviderError(
                code="weather_fixture_unavailable",
                retryable=False,
                message="The deterministic weather contract fixture is unavailable.",
            ) from exc
        metadata = document.get("fixture_metadata") or {}
        payload = document.get("response") or {}
        if metadata.get("classification") != "synthetic" or not isinstance(payload, Mapping):
            raise WeatherProviderError(
                code="weather_fixture_invalid",
                retryable=False,
                message="The deterministic weather fixture must be explicitly synthetic.",
            )
        target_at = _fixture_target_at(request, str(metadata.get("scenario_date") or ""))
        hours = _parse_open_meteo_hours(
            payload,
            request=request,
            target_at=target_at,
            normalize_fixture_date=True,
        )
        return WeatherSnapshot(
            provider="recorded-open-meteo-contract",
            source=str(metadata.get("source_documentation") or OPEN_METEO_ATTRIBUTION_URL),
            classification="synthetic",
            provider_reference=f"fixture:{self._fixture_path.name}:{hashlib.sha256(raw).hexdigest()[:12]}",
            model="synthetic_contract_fixture",
            timezone_name=request.timezone_name,
            latitude=float(payload.get("latitude", request.latitude)),
            longitude=float(payload.get("longitude", request.longitude)),
            target_at=target_at.isoformat(timespec="minutes"),
            retrieved_at=None,
            valid_until=None,
            freshness="not_applicable",
            cache_status="fixture",
            hours=hours,
            warnings=(
                "Synthetic contract fixture: it proves adapter behavior, not current Beijing weather.",
            ),
        )


def create_weather_provider(
    env: Mapping[str, str] | None = None,
    *,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
) -> WeatherProvider | None:
    values = env if env is not None else os.environ
    provider = str(values.get("BJ_PAL_WEATHER_PROVIDER") or "fixture").strip().lower()
    if provider == "fixture":
        return RecordedWeatherProvider(fixture_path)
    if provider in {"none", "disabled"}:
        return None
    if provider == "open_meteo":
        return OpenMeteoWeatherProvider(OpenMeteoConfig.from_env(values))
    raise WeatherConfigurationError(
        "BJ_PAL_WEATHER_PROVIDER must be fixture, open_meteo, or disabled."
    )


def decision_weather_context(payload: Mapping[str, object] | None, local_time: str) -> WeatherContext | None:
    """Rebuild the exact planner weather point for downstream risk probing."""

    if not payload:
        return None
    raw_hours = payload.get("hours")
    if not isinstance(raw_hours, Sequence) or isinstance(raw_hours, (str, bytes)):
        return None
    points: list[WeatherHour] = []
    for item in raw_hours:
        if not isinstance(item, Mapping):
            continue
        try:
            points.append(
                WeatherHour(
                    local_time=str(item["local_time"]),
                    temperature_c=float(item["temperature_c"]),
                    apparent_temperature_c=float(item["apparent_temperature_c"]),
                    precipitation_probability_pct=int(item["precipitation_probability_pct"]),
                    precipitation_mm=float(item["precipitation_mm"]),
                    weather_code=int(item["weather_code"]),
                    wind_speed_kmh=float(item["wind_speed_kmh"]),
                    state=_validated_weather_state(str(item["state"])),
                    severity=float(item["severity"]),
                    description=str(item["description"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return None
    wanted = _minutes_of_day(_parse_local_time(local_time))
    point = min(
        points,
        key=lambda item: abs(_minutes_of_day(_parse_iso_local_time(item.local_time)) - wanted),
    )
    return WeatherContext(
        state=point.state,
        description=point.description,
        severity=point.severity,
    )


def resolve_weather_target_date(
    query: str,
    *,
    target_local_time: str = "14:00",
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> str | None:
    """Resolve only unambiguous short-horizon Chinese date expressions.

    Unsupported or ambiguous expressions intentionally return ``None`` so a
    live provider degrades instead of fetching the wrong day.
    """

    zone = ZoneInfo(timezone_name)
    current = _as_aware_utc(now or datetime.now(timezone.utc)).astimezone(zone)
    text = str(query or "")
    target_clock = _parse_local_time(target_local_time)
    iso_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    if iso_match:
        try:
            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            ).isoformat()
        except ValueError:
            return None
    offsets = (("后天", 2), ("明天", 1), ("今天", 0), ("今日", 0))
    for token, days in offsets:
        if token in text:
            return (current.date() + timedelta(days=days)).isoformat()

    match = re.search(r"(?:本|这)?(?:周|星期)([一二三四五六日天])", text)
    if match:
        target_weekday = {
            "一": 0,
            "二": 1,
            "三": 2,
            "四": 3,
            "五": 4,
            "六": 5,
            "日": 6,
            "天": 6,
        }[match.group(1)]
        days = (target_weekday - current.weekday()) % 7
        if days == 0 and target_clock < current.time():
            days = 7
        return (current.date() + timedelta(days=days)).isoformat()
    if "周末" in text or "星期末" in text:
        days = (5 - current.weekday()) % 7
        if days == 0 and target_clock < current.time():
            days = 7
        return (current.date() + timedelta(days=days)).isoformat()
    return None


def _parse_open_meteo_hours(
    payload: Mapping[str, object],
    *,
    request: WeatherRequest,
    target_at: datetime,
    normalize_fixture_date: bool,
) -> tuple[WeatherHour, ...]:
    hourly = payload.get("hourly")
    units = payload.get("hourly_units")
    if not isinstance(hourly, Mapping) or not isinstance(units, Mapping):
        raise WeatherProviderError(
            code="weather_response_invalid_schema",
            retryable=False,
            message="Weather response is missing hourly values or units.",
        )
    expected_units = {
        "temperature_2m": "°C",
        "apparent_temperature": "°C",
        "precipitation_probability": "%",
        "precipitation": "mm",
        "weather_code": "wmo code",
        "wind_speed_10m": "km/h",
    }
    if any(units.get(name) != unit for name, unit in expected_units.items()):
        raise WeatherProviderError(
            code="weather_response_unit_mismatch",
            retryable=False,
            message="Weather response units do not match the typed adapter contract.",
        )
    names = ("time", *expected_units)
    arrays = {name: hourly.get(name) for name in names}
    if any(not isinstance(value, list) for value in arrays.values()):
        raise WeatherProviderError(
            code="weather_response_invalid_schema",
            retryable=False,
            message="Weather hourly fields must be arrays.",
        )
    lengths = {len(value) for value in arrays.values() if isinstance(value, list)}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise WeatherProviderError(
            code="weather_response_length_mismatch",
            retryable=False,
            message="Weather hourly arrays must be non-empty and have equal lengths.",
        )

    zone = ZoneInfo(request.timezone_name)
    parsed: list[tuple[datetime, WeatherHour]] = []
    for index, raw_time in enumerate(arrays["time"]):  # type: ignore[index]
        try:
            local_at = datetime.fromisoformat(str(raw_time)).replace(tzinfo=zone)
            if normalize_fixture_date:
                local_at = datetime.combine(target_at.date(), local_at.timetz(), tzinfo=zone)
            temperature_c = float(arrays["temperature_2m"][index])  # type: ignore[index]
            apparent_c = float(arrays["apparent_temperature"][index])  # type: ignore[index]
            probability = int(arrays["precipitation_probability"][index])  # type: ignore[index]
            precipitation = float(arrays["precipitation"][index])  # type: ignore[index]
            weather_code = int(arrays["weather_code"][index])  # type: ignore[index]
            wind_speed = float(arrays["wind_speed_10m"][index])  # type: ignore[index]
        except (TypeError, ValueError, IndexError) as exc:
            raise WeatherProviderError(
                code="weather_response_invalid_value",
                retryable=False,
                message="Weather response contains an invalid hourly value.",
            ) from exc
        state, severity, description = _derive_weather_state(
            weather_code=weather_code,
            temperature_c=temperature_c,
            precipitation_probability_pct=probability,
            precipitation_mm=precipitation,
        )
        parsed.append(
            (
                local_at,
                WeatherHour(
                    local_time=local_at.isoformat(timespec="minutes"),
                    temperature_c=temperature_c,
                    apparent_temperature_c=apparent_c,
                    precipitation_probability_pct=probability,
                    precipitation_mm=precipitation,
                    weather_code=weather_code,
                    wind_speed_kmh=wind_speed,
                    state=state,
                    severity=severity,
                    description=description,
                ),
            )
        )
    closest_index = min(range(len(parsed)), key=lambda idx: abs(parsed[idx][0] - target_at))
    if not normalize_fixture_date and abs(parsed[closest_index][0] - target_at) > timedelta(hours=2):
        raise WeatherProviderError(
            code="weather_target_outside_response",
            retryable=False,
            message="Weather response does not contain the requested local time.",
        )
    start = max(0, closest_index - 1)
    end = min(len(parsed), closest_index + 7)
    return tuple(item for _, item in parsed[start:end])


def _derive_weather_state(
    *,
    weather_code: int,
    temperature_c: float,
    precipitation_probability_pct: int,
    precipitation_mm: float,
) -> tuple[WeatherState, float, str]:
    snow_codes = {71, 73, 75, 77, 85, 86}
    rain_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
    if weather_code in snow_codes:
        severity = min(1.0, max(0.5, precipitation_mm / 3))
        return "snow", round(severity, 3), f"降雪 {precipitation_mm:g} mm"
    if weather_code in rain_codes or precipitation_mm > 0.1:
        severity = min(1.0, max(precipitation_probability_pct / 100, precipitation_mm / 5))
        return (
            "rain",
            round(severity, 3),
            f"降水概率 {precipitation_probability_pct}%，前一小时降水 {precipitation_mm:g} mm",
        )
    if temperature_c >= 35:
        severity = min(1.0, 0.6 + (temperature_c - 35) / 10)
        return "heatwave", round(severity, 3), f"高温 {temperature_c:g}°C"
    if temperature_c <= -5:
        severity = min(1.0, 0.5 + (-5 - temperature_c) / 15)
        return "cold", round(severity, 3), f"低温 {temperature_c:g}°C"
    return "clear", 0.0, f"无显著降水，气温 {temperature_c:g}°C"


def _validated_weather_state(value: str) -> WeatherState:
    if value not in {"clear", "rain", "snow", "heatwave", "aqi_high", "cold"}:
        raise ValueError("unsupported weather state")
    return value  # type: ignore[return-value]


def _raise_for_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        return
    if status_code == 429:
        raise WeatherProviderError(
            code="weather_rate_limited",
            retryable=True,
            message="Weather provider rate limit was reached.",
        )
    if status_code >= 500:
        raise WeatherProviderError(
            code="weather_upstream_unavailable",
            retryable=True,
            message="Weather provider is temporarily unavailable.",
        )
    raise WeatherProviderError(
        code="weather_request_rejected",
        retryable=False,
        message="Weather provider rejected the request.",
    )


def _within_stale_window(snapshot: WeatherSnapshot, now: datetime, seconds: int) -> bool:
    if seconds <= 0 or not snapshot.valid_until:
        return False
    return now <= _parse_iso_datetime(snapshot.valid_until) + timedelta(seconds=seconds)


def _fixture_target_at(request: WeatherRequest, scenario_date: str) -> datetime:
    try:
        date_value = datetime.fromisoformat(scenario_date).date()
    except ValueError as exc:
        raise WeatherProviderError(
            code="weather_fixture_invalid",
            retryable=False,
            message="Weather fixture scenario_date must use ISO format.",
        ) from exc
    return datetime.combine(
        date_value,
        _parse_local_time(request.target_local_time),
        tzinfo=ZoneInfo(request.timezone_name),
    )


def _parse_local_time(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("target_local_time must use HH:MM") from exc


def _parse_iso_local_time(value: str) -> time:
    try:
        return datetime.fromisoformat(value).time()
    except ValueError as exc:
        raise ValueError("weather local_time must be ISO-8601") from exc


def _minutes_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _parse_iso_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value)
    return _as_aware_utc(parsed)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(values: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key, str(default)))
    except ValueError as exc:
        raise WeatherConfigurationError(f"{key} must be an integer.") from exc


def _env_float(values: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(values.get(key, str(default)))
    except ValueError as exc:
        raise WeatherConfigurationError(f"{key} must be numeric.") from exc
