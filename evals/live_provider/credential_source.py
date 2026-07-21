"""Explicit, fail-closed credential handoff for a local CSSwitch profile.

The loader is only used by the opt-in live acceptance runner. Production
delivery adapters continue to consume ordinary DPSK_* environment variables.
The raw credential is held in memory only and excluded from repr/equality.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import urlsplit


MAX_CONFIG_BYTES = 64 * 1024
_PROVIDER_ENV_KEYS = (
    "BJ_PAL_LLM",
    "DPSK_API_KEY",
    "DPSK_BASE_URL",
    "DPSK_MODEL",
    "DPSK_MAX_TOKENS",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_MAX_TOKENS",
    "LONGCAT_API_KEY",
    "LONGCAT_BASE_URL",
    "LONGCAT_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)


def _https_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("CSSwitch DeepSeek profile requires a base_url")
    rendered = value.strip()
    parsed = urlsplit(rendered)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "CSSwitch DeepSeek base_url must be credential-free HTTPS"
        )
    return rendered.rstrip("/")


def _secret(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("CSSwitch DeepSeek profile has no API credential")
    if not 16 <= len(value) <= 512:
        raise ValueError("CSSwitch DeepSeek credential length is invalid")
    if value != value.strip() or any(ord(char) < 33 for char in value):
        raise ValueError("CSSwitch DeepSeek credential contains whitespace/control data")
    return value


@dataclass(frozen=True)
class CsswitchCredential:
    """Validated local credential plus payload-free handoff metadata."""

    api_key: str = field(repr=False, compare=False)
    base_url: str
    source_type: str = "csswitch_active_profile"
    config_file_mode: str = "0600"
    owner_uid_match: bool = True
    regular_file: bool = True
    symlink: bool = False
    profile_template: str = "deepseek"
    api_format: str = "anthropic"

    def safe_metadata(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "config_file_mode": self.config_file_mode,
            "owner_uid_match": self.owner_uid_match,
            "regular_file": self.regular_file,
            "symlink": self.symlink,
            "profile_template": self.profile_template,
            "api_format": self.api_format,
        }

    @contextmanager
    def provider_environment(
        self,
        *,
        model: str,
        max_output_tokens: int,
        environ: dict[str, str] | None = None,
    ) -> Iterator[None]:
        """Temporarily expose one explicit provider config to BJ-Pal."""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("live acceptance requires an explicit model")
        if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
            raise ValueError("max_output_tokens must be an integer")
        if not 1 <= max_output_tokens <= 32768:
            raise ValueError("max_output_tokens must be between 1 and 32768")
        target = os.environ if environ is None else environ
        previous = {key: target.get(key) for key in _PROVIDER_ENV_KEYS}
        try:
            for key in _PROVIDER_ENV_KEYS:
                target.pop(key, None)
            target.update(
                {
                    "BJ_PAL_LLM": "dpsk",
                    "DPSK_API_KEY": self.api_key,
                    "DPSK_BASE_URL": self.base_url,
                    "DPSK_MODEL": model.strip(),
                    "DPSK_MAX_TOKENS": str(max_output_tokens),
                }
            )
            yield
        finally:
            for key in _PROVIDER_ENV_KEYS:
                target.pop(key, None)
            for key, value in previous.items():
                if value is not None:
                    target[key] = value


def load_csswitch_credential(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> CsswitchCredential:
    """Load exactly the active DeepSeek profile after filesystem checks."""
    values = os.environ if environ is None else environ
    configured = values.get("CSSWITCH_CONFIG")
    config_path = path or (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".csswitch" / "config.json"
    )
    if config_path.is_symlink():
        raise ValueError("CSSwitch config must not be a symlink")
    metadata = config_path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("CSSwitch config must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError("CSSwitch config must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("CSSwitch config must not grant group/other permissions")
    if not 0 < metadata.st_size <= MAX_CONFIG_BYTES:
        raise ValueError("CSSwitch config size is outside the accepted boundary")

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CSSwitch config must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("CSSwitch config schema_version must be 2")
    active_id = payload.get("active_id")
    profiles = payload.get("profiles")
    if not isinstance(active_id, str) or not active_id:
        raise ValueError("CSSwitch config requires an active profile")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 100:
        raise ValueError("CSSwitch profiles must be a bounded non-empty list")
    matches = [
        item
        for item in profiles
        if isinstance(item, dict) and item.get("id") == active_id
    ]
    if len(matches) != 1:
        raise ValueError("CSSwitch active profile must resolve exactly once")
    profile = matches[0]
    if profile.get("template_id") != "deepseek":
        raise ValueError("CSSwitch active profile must use the DeepSeek template")
    if profile.get("api_format") != "anthropic":
        raise ValueError("CSSwitch DeepSeek profile must use Anthropic API format")

    return CsswitchCredential(
        api_key=_secret(profile.get("api_key")),
        base_url=_https_base_url(profile.get("base_url")),
        config_file_mode=f"{stat.S_IMODE(metadata.st_mode):04o}",
        owner_uid_match=metadata.st_uid == os.getuid(),
        regular_file=stat.S_ISREG(metadata.st_mode),
        symlink=False,
    )
