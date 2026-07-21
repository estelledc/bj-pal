from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.check_no_secrets import scan_file, scan_text  # noqa: E402


def test_placeholders_are_allowed() -> None:
    path = Path("env.example")
    text = "\n".join(
        (
            "LONGCAT_API_KEY=<required>",
            "# DPSK_API_KEY=替换成你的_Key",
            "# BJ_PAL_CONTROL_TOKEN=replace-with-random-token",
        )
    )

    assert scan_text(path, text) == []


def test_sensitive_assignment_is_reported_without_value() -> None:
    path = Path("env.example")
    secret = "live_" + "A" * 32

    findings = scan_text(path, f"LONGCAT_API_KEY={secret}")

    assert len(findings) == 1
    rendered = findings[0].render(Path.cwd())
    assert findings[0].rule == "sensitive_assignment_literal"
    assert findings[0].variable == "LONGCAT_API_KEY"
    assert secret not in rendered


def test_known_provider_token_prefix_is_reported() -> None:
    path = Path("config.txt")
    secret = "sk-" + "B" * 40

    findings = scan_text(path, f"provider={secret}")

    assert [finding.rule for finding in findings] == ["openai_or_anthropic_token"]
    assert secret not in findings[0].render(Path.cwd())


def test_binary_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"TOKEN=" + b"C" * 40 + b"\x00")

    assert scan_file(path) == []
