#!/usr/bin/env python3
"""Fail when the current release tree contains credential-like literals.

This intentionally scans the index plus non-ignored untracked files. It does not
scan Git history and cannot revoke a credential that was already published.
Findings report only path, line, rule and variable name; secret values are never
printed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_TEXT_BYTES = 2_000_000

SENSITIVE_ASSIGNMENT = re.compile(
    r"^\s*#?\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))"
    r"\s*=\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
LITERAL_VALUE = re.compile(r"^[\"']?[A-Za-z0-9_./+=:@-]{8,}[\"']?(?:\s+#.*)?$")
PRIVATE_KEY_HEADER = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
KNOWN_CREDENTIAL_PATTERNS = (
    (
        "openai_or_anthropic_token",
        re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}\b"),
    ),
    ("github_token", re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{16,}\b")),
)
PLACEHOLDER_MARKERS = (
    "<required>",
    "placeholder",
    "replace",
    "example",
    "changeme",
    "change-me",
    "your_",
    "your-",
    "替换",
    "填写",
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str
    variable: str | None = None

    def render(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        suffix = f" variable={self.variable}" if self.variable else ""
        return f"{display_path}:{self.line}: rule={self.rule}{suffix}"


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if not normalized:
        return True
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def _is_env_template(path: Path) -> bool:
    name = path.name.lower()
    return (
        name == ".env"
        or name.startswith(".env_")
        or name.startswith(".env.")
        or name in {"env.example", "env.template", "env.sample"}
        or path.suffix.lower() == ".env"
    )


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        assignment = SENSITIVE_ASSIGNMENT.match(line)
        if assignment and _is_env_template(path):
            value = assignment.group("value")
            if LITERAL_VALUE.fullmatch(value) and not _is_placeholder(value):
                findings.append(
                    Finding(
                        path=path,
                        line=line_number,
                        rule="sensitive_assignment_literal",
                        variable=assignment.group("name"),
                    )
                )

        if PRIVATE_KEY_HEADER.search(line):
            findings.append(
                Finding(path=path, line=line_number, rule="private_key_header")
            )

        for rule, pattern in KNOWN_CREDENTIAL_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path=path, line=line_number, rule=rule))

    return findings


def scan_file(path: Path) -> list[Finding]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            return []
        payload = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in payload:
        return []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return scan_text(path, text)


def discover_release_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_file(path))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan the current release tree for credential-like literals."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Git repository root (default: BJ-Pal root).",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    paths = discover_release_paths(root)
    findings = scan_paths(paths)
    if findings:
        print(
            "secret_scan_failed: credential-like literals found; values are redacted"
        )
        for finding in findings:
            print(finding.render(root))
        return 1
    print(
        "secret_scan_ok: "
        f"files={len(paths)} scope=index_plus_nonignored_untracked history_scanned=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
