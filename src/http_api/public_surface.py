"""Route filtering for the deliberately narrow synthetic public API."""

from __future__ import annotations

from fastapi import FastAPI


PUBLIC_PATHS = frozenset(
    {
        "/docs",
        "/docs/oauth2-redirect",
        "/healthz",
        "/openapi.json",
        "/readyz",
        "/v1/plans",
    }
)


def retain_public_routes(application: FastAPI) -> None:
    """Remove private routes, including routes nested by modern FastAPI includes."""
    retained = []
    for route in application.router.routes:
        if getattr(route, "path", None) in PUBLIC_PATHS:
            retained.append(route)
            continue

        included_router = getattr(route, "original_router", None)
        if included_router is None:
            continue
        included_router.routes = [
            child
            for child in included_router.routes
            if getattr(child, "path", None) in PUBLIC_PATHS
        ]
        if included_router.routes:
            retained.append(route)

    application.router.routes = retained
