"""Fail closed when public-release sources contain unsafe files or secrets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
MAX_GIT_FILE_BYTES = 50 * 1024 * 1024
BLOCKED_SUFFIXES = {
    ".appimage",
    ".bak",
    ".db",
    ".dmg",
    ".duckdb",
    ".exe",
    ".key",
    ".msi",
    ".p12",
    ".parquet",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".zip",
}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "local-user-path": re.compile(r"\b[A-Za-z]:\\Users\\[^\\\r\n]+"),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
HTML_SOURCE = re.compile(r"""(?:src|href)=["']([^"']+)["']""")


class Blocker(TypedDict):
    path: str
    reason: str


class PublicationReport(TypedDict):
    schema_version: str
    status: str
    files_scanned: int
    nested_checkouts: list[str]
    blockers: list[Blocker]
    max_git_file_bytes: int


def _is_nested_checkout(path: Path) -> bool:
    """Whether `path` is a directory carrying its own `.git`.

    `git ls-files --others` reports such a directory as a *single* entry rather than
    descending into it, because its contents belong to another repository. This gate used to
    treat every entry as a file and died with `IsADirectoryError` on the first one -- which
    happens whenever an agent worktree exists under `.claude/worktrees/`, i.e. whenever this
    repository's own tooling is mid-run. A gate that fails on the presence of a sibling
    directory is reporting on its environment, not on what would be published.

    Skipping is correct rather than a loophole: a nested checkout is not published by *this*
    repository, and its contents are governed by its own run of this script. But the skip is
    named in the report instead of being silent -- "I found things I did not look at" is a
    fact the caller is owed, and `files_scanned` alone cannot carry it.
    """
    return path.is_dir() and (path / ".git").exists()


def _publication_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def _scan() -> PublicationReport:
    listed = _publication_files()
    nested = [p for p in listed if _is_nested_checkout(p)]
    files = [p for p in listed if p not in nested]
    blockers: list[Blocker] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.is_dir():
            blockers.append({"path": relative, "reason": "directory git listed but not a checkout"})
            continue
        suffix = path.suffix.lower()
        if suffix in BLOCKED_SUFFIXES:
            blockers.append({"path": relative, "reason": f"blocked suffix {suffix}"})
            continue
        if path.stat().st_size > MAX_GIT_FILE_BYTES:
            blockers.append({"path": relative, "reason": "file exceeds 50 MiB Git budget"})
            continue
        if suffix in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            blockers.append({"path": relative, "reason": "unclassified binary file"})
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                blockers.append({"path": relative, "reason": f"matched {name}"})
        if suffix == ".md":
            for target in [*MARKDOWN_LINK.findall(text), *HTML_SOURCE.findall(text)]:
                target = target.strip().strip("<>")
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                local_target = target.split("#", maxsplit=1)[0]
                if local_target and not (path.parent / local_target).exists():
                    blockers.append(
                        {
                            "path": relative,
                            "reason": f"broken relative link {target}",
                        }
                    )

    required = [
        "LICENSE",
        "README.md",
        "README.en.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
    ]
    for relative in required:
        if not (ROOT / relative).is_file():
            blockers.append({"path": relative, "reason": "required public metadata missing"})

    return {
        "schema_version": "publication-scan/v2",
        "status": "ok" if not blockers else "blocked",
        "files_scanned": len(files),
        "nested_checkouts": sorted(p.relative_to(ROOT).as_posix() for p in nested),
        "blockers": blockers,
        "max_git_file_bytes": MAX_GIT_FILE_BYTES,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = _scan()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"publication scan: {report['status']} ({report['files_scanned']} files)")
        for nested in report["nested_checkouts"]:
            print(f"- skipped {nested}: nested git checkout, governed by its own scan")
        for blocker in report["blockers"]:
            print(f"- {blocker['path']}: {blocker['reason']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
