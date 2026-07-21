from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from providers import (  # noqa: E402
    OpenMeteoConfig,
    OpenMeteoWeatherProvider,
    RecordedWeatherProvider,
    WeatherConfigurationError,
    WeatherHTTPResponse,
    WeatherProviderError,
    WeatherRequest,
    create_weather_provider,
    decision_weather_context,
    resolve_weather_target_date,
)


FIXTURE = ROOT / "fixtures" / "weather" / "beijing_synthetic.json"
NOW = datetime(2026, 7, 20, 5, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, *responses: WeatherHTTPResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, *, url, params, timeout_seconds):
        self.calls.append(
            {"url": url, "params": dict(params), "timeout_seconds": timeout_seconds}
        )
        return self.responses.pop(0)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["response"]


def _request() -> WeatherRequest:
    return WeatherRequest(
        latitude=39.9474,
        longitude=116.4166,
        target_local_time="14:00",
        target_date="2026-07-20",
    )


def _noncommercial_config(**overrides) -> OpenMeteoConfig:
    values = {
        "usage": "noncommercial",
        "endpoint": "https://api.open-meteo.com/v1/forecast",
        "noncommercial_ack": True,
    }
    values.update(overrides)
    return OpenMeteoConfig(**values)


def test_default_weather_provider_is_offline_synthetic_fixture() -> None:
    provider = create_weather_provider({})
    assert isinstance(provider, RecordedWeatherProvider)

    snapshot = provider.forecast(_request())

    assert snapshot.classification == "synthetic"
    assert snapshot.retrieved_at is None
    assert snapshot.valid_until is None
    assert snapshot.cache_status == "fixture"
    assert snapshot.context_at("14:00").state == "rain"
    assert "proves adapter behavior" in snapshot.warnings[0]


def test_free_endpoint_fails_closed_without_noncommercial_acknowledgement() -> None:
    with pytest.raises(WeatherConfigurationError, match="non-commercial acknowledgement"):
        OpenMeteoConfig(
            usage="noncommercial",
            endpoint="https://api.open-meteo.com/v1/forecast",
        )


def test_commercial_endpoint_requires_api_key_and_custom_endpoint_requires_self_hosted() -> None:
    with pytest.raises(WeatherConfigurationError, match="commercial endpoint"):
        OpenMeteoConfig(
            usage="commercial",
            endpoint="https://customer-api.open-meteo.com/v1/forecast",
        )
    with pytest.raises(WeatherConfigurationError, match="self_hosted"):
        OpenMeteoConfig(
            usage="commercial",
            endpoint="https://weather.example.test/v1/forecast",
            api_key="secret",
        )


def test_live_adapter_validates_response_and_reuses_fresh_cache_without_leaking_key() -> None:
    transport = FakeTransport(WeatherHTTPResponse(status_code=200, payload=_payload()))
    provider = OpenMeteoWeatherProvider(
        _noncommercial_config(),
        transport=transport,
        clock=lambda: NOW,
    )

    first = provider.forecast(_request())
    second = provider.forecast(_request())

    assert first.classification == "live_model_forecast"
    assert first.freshness == "fresh"
    assert first.cache_status == "miss"
    assert second.freshness == "fresh_cache"
    assert second.cache_status == "hit"
    assert len(transport.calls) == 1
    assert transport.calls[0]["timeout_seconds"] == 3.0
    assert "apikey" not in transport.calls[0]["params"]
    assert transport.calls[0]["params"]["start_date"] == "2026-07-20"
    assert transport.calls[0]["params"]["end_date"] == "2026-07-20"
    assert "apikey" not in first.provider_reference
    assert first.context_at("14:00").state == "rain"


def test_expired_cache_is_returned_only_as_explicit_stale_if_refresh_fails() -> None:
    clock = MutableClock(NOW)
    transport = FakeTransport(
        WeatherHTTPResponse(status_code=200, payload=_payload()),
        WeatherHTTPResponse(status_code=503, payload={"reason": "maintenance"}),
    )
    provider = OpenMeteoWeatherProvider(
        _noncommercial_config(cache_ttl_seconds=1, stale_if_error_seconds=30),
        transport=transport,
        clock=clock,
    )
    first = provider.forecast(_request())
    clock.now += timedelta(seconds=2)

    stale = provider.forecast(_request())

    assert first.cache_status == "miss"
    assert stale.cache_status == "stale"
    assert stale.freshness == "stale_if_error"
    assert stale.valid_until == first.valid_until
    assert "weather_upstream_unavailable" in stale.warnings[-1]


def test_rate_limit_and_unit_drift_have_typed_failure_taxonomy() -> None:
    limited = OpenMeteoWeatherProvider(
        _noncommercial_config(),
        transport=FakeTransport(WeatherHTTPResponse(status_code=429, payload={})),
        clock=lambda: NOW,
    )
    with pytest.raises(WeatherProviderError) as limited_error:
        limited.forecast(_request())
    assert limited_error.value.code == "weather_rate_limited"
    assert limited_error.value.retryable is True

    invalid_payload = _payload()
    invalid_payload["hourly_units"] = dict(invalid_payload["hourly_units"])
    invalid_payload["hourly_units"]["temperature_2m"] = "°F"
    invalid = OpenMeteoWeatherProvider(
        _noncommercial_config(),
        transport=FakeTransport(WeatherHTTPResponse(status_code=200, payload=invalid_payload)),
        clock=lambda: NOW,
    )
    with pytest.raises(WeatherProviderError) as schema_error:
        invalid.forecast(_request())
    assert schema_error.value.code == "weather_response_unit_mismatch"
    assert schema_error.value.retryable is False


def test_decision_context_rebuilds_same_hour_for_probe() -> None:
    snapshot = RecordedWeatherProvider(FIXTURE).forecast(_request())
    payload = snapshot.to_decision_context()

    context = decision_weather_context(payload, "15:15")

    assert context is not None
    assert context.state == "rain"
    assert context.severity == pytest.approx(0.8)


def test_live_lookup_refuses_unresolved_or_out_of_horizon_dates_without_network() -> None:
    transport = FakeTransport(WeatherHTTPResponse(status_code=200, payload=_payload()))
    provider = OpenMeteoWeatherProvider(
        _noncommercial_config(),
        transport=transport,
        clock=lambda: NOW,
    )
    unresolved = WeatherRequest(
        latitude=39.9474,
        longitude=116.4166,
        target_local_time="14:00",
    )
    with pytest.raises(WeatherProviderError) as unresolved_error:
        provider.forecast(unresolved)
    assert unresolved_error.value.code == "weather_target_date_unresolved"

    outside = WeatherRequest(
        latitude=39.9474,
        longitude=116.4166,
        target_local_time="14:00",
        target_date="2026-08-10",
    )
    with pytest.raises(WeatherProviderError) as horizon_error:
        provider.forecast(outside)
    assert horizon_error.value.code == "weather_target_outside_forecast_horizon"
    assert transport.calls == []


def test_target_date_resolver_handles_short_horizon_and_rejects_ambiguity() -> None:
    assert resolve_weather_target_date("今天下午", now=NOW) == "2026-07-20"
    assert resolve_weather_target_date("明天下午", now=NOW) == "2026-07-21"
    assert resolve_weather_target_date("周六下午", now=NOW) == "2026-07-25"
    assert resolve_weather_target_date("2026-07-26 下午", now=NOW) == "2026-07-26"
    assert resolve_weather_target_date("最近找个时间出去", now=NOW) is None
