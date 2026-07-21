"""Hosted entrypoint that exposes only the bounded synthetic portfolio demo."""

from __future__ import annotations

from .public_demo import (
    PublicDemoGuardMiddleware,
    PublicDemoSettings,
    validate_public_demo_environment,
)

# Validate before importing ``http_api.app`` because that module also materializes
# the full internal application object at import time.
validate_public_demo_environment()

from fastapi import FastAPI  # noqa: E402

from data_profile import inspect_runtime_data  # noqa: E402

from .app import create_app  # noqa: E402


def create_public_demo_app(
    *,
    settings: PublicDemoSettings | None = None,
    **app_kwargs,
) -> FastAPI:
    validate_public_demo_environment()
    audit = inspect_runtime_data()
    if not (
        audit.ready
        and audit.profile.name == "demo"
        and audit.profile.classification == "synthetic"
        and audit.profile.public_reproducible
    ):
        raise RuntimeError(
            "public demo requires the ready, public-reproducible synthetic demo dataset"
        )
    resolved_settings = settings or PublicDemoSettings.from_environment()
    application = create_app(public_demo=True, **app_kwargs)
    application.add_middleware(
        PublicDemoGuardMiddleware,
        settings=resolved_settings,
    )
    application.state.public_demo_settings = resolved_settings
    return application


app = create_public_demo_app()
