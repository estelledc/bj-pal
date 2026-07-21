"""Typed data-plane contracts and local provider adapters."""

from .contracts import (
    DataEvidence,
    PlanningDataProvider,
    PlanningDataSnapshot,
    ProviderIssue,
    RetrievedEvidence,
)
from .sqlite_demo import SQLitePlanningDataProvider
from .weather import (
    OpenMeteoConfig,
    OpenMeteoWeatherProvider,
    RecordedWeatherProvider,
    WeatherConfigurationError,
    WeatherHour,
    WeatherHTTPResponse,
    WeatherProvider,
    WeatherProviderError,
    WeatherRequest,
    WeatherSnapshot,
    create_weather_provider,
    decision_weather_context,
    resolve_weather_target_date,
)

__all__ = [
    "DataEvidence",
    "PlanningDataProvider",
    "PlanningDataSnapshot",
    "ProviderIssue",
    "RetrievedEvidence",
    "SQLitePlanningDataProvider",
    "OpenMeteoConfig",
    "OpenMeteoWeatherProvider",
    "RecordedWeatherProvider",
    "WeatherConfigurationError",
    "WeatherHour",
    "WeatherHTTPResponse",
    "WeatherProvider",
    "WeatherProviderError",
    "WeatherRequest",
    "WeatherSnapshot",
    "create_weather_provider",
    "decision_weather_context",
    "resolve_weather_target_date",
]
