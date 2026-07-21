from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from smoke_deployed_api import (  # noqa: E402
    normalize_base_url,
    requires_public_demo_contract,
)
from verify_release_tag import verify_release_tag  # noqa: E402
from http_api.public_healthcheck import healthcheck_url  # noqa: E402


def test_v627_release_tag_matches_both_version_sources() -> None:
    assert verify_release_tag("v6.27.0", ROOT) == "6.27.0"


@pytest.mark.parametrize("tag", ["6.25.0", "v6.25", "v06.25.0", "v6.25.0-rc1"])
def test_release_tag_rejects_noncanonical_or_unsupported_versions(tag: str) -> None:
    with pytest.raises(ValueError):
        verify_release_tag(tag, ROOT)


def test_remote_smoke_requires_https_and_never_accepts_url_credentials() -> None:
    assert normalize_base_url("https://api.example.test/bj-pal") == (
        "https://api.example.test/bj-pal/"
    )
    assert normalize_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/"
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_base_url("http://api.example.test")
    with pytest.raises(ValueError, match="credentials"):
        normalize_base_url("https://user:secret@api.example.test")


def test_deployed_smoke_preserves_v624_and_enforces_public_demo_from_v625() -> None:
    assert requires_public_demo_contract("6.24.0") is False
    assert requires_public_demo_contract("6.25.0") is True
    assert requires_public_demo_contract("7.0.0") is True
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        requires_public_demo_contract("v6.25.0")


def test_release_workflow_smokes_before_registry_login_and_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-container.yml").read_text(
        encoding="utf-8"
    )
    smoke_index = workflow.index("Smoke hardened release container before publish")
    login_index = workflow.index("docker login ghcr.io")
    push_index = workflow.index("docker push")
    assert smoke_index < login_index < push_index
    assert "packages: write" in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777" in workflow
    assert "--cap-drop ALL" in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    publish_step_index = workflow.index(
        "Publish release, immutable SHA, and latest tags"
    )
    build_section = workflow[workflow.index("docker build") : publish_step_index]
    assert "GITHUB_TOKEN" not in build_section
    assert "DPSK_API_KEY" not in workflow


def test_public_compose_is_local_only_and_hardened() -> None:
    compose = (ROOT / "compose.public.yaml").read_text(encoding="utf-8")
    assert "127.0.0.1:${BJ_PAL_PORT:-8000}:8000" in compose
    assert "read_only: true" in compose
    assert "/tmp:rw,noexec,nosuid,size=64m,mode=1777" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "BJ_PAL_LLM: mock" in compose
    assert "BJ_PAL_PUBLIC_DEMO_REQUESTS_PER_WINDOW" in compose
    assert "BJ_PAL_PUBLIC_DEMO_MAX_CONCURRENT_PLANS" in compose


def test_dockerfile_uses_fixed_non_root_identity_and_oci_labels() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "org.opencontainers.image.source" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "org.opencontainers.image.version" in dockerfile
    assert "org.opencontainers.image.licenses=\"NOASSERTION\"" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert 'CMD ["python", "-m", "http_api.public_healthcheck"]' in dockerfile
    assert 'CMD ["python", "-m", "http_api.public_server"]' in dockerfile


def test_container_healthcheck_follows_platform_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    assert healthcheck_url() == "http://127.0.0.1:10000/healthz"
