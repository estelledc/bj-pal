"""Fail closed unless a release tag exactly matches both version sources."""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from pathlib import Path


TAG_PATTERN = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$"
)


def read_source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    versions: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "SERVICE_VERSION"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            versions.append(node.value.value)
    if len(versions) != 1:
        raise ValueError("src/version.py must define one literal SERVICE_VERSION")
    return versions[0]


def verify_release_tag(tag: str, root: Path) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError("release tag must use exact vMAJOR.MINOR.PATCH form")

    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = package["project"]["version"]
    source_version = read_source_version(root / "src" / "version.py")
    tag_version = match.group("version")
    if package_version != source_version:
        raise ValueError("pyproject and service versions do not match")
    if tag_version != package_version:
        raise ValueError(
            f"release tag {tag!r} does not match declared version v{package_version}"
        )
    return package_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    args = parser.parse_args()
    version = verify_release_tag(args.tag, args.root.resolve())
    print(f"release tag verified: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
