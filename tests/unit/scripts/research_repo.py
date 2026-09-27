"""Throwaway git repositories for the research registry's tests (`V2-P6-008`).

Each repository is built with no global or system configuration and every `GIT_*` variable
dropped, and each commit's time is pinned through `GIT_COMMITTER_DATE`, so the ordering the
holdout guard judges is the one a test wrote.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path


def git(repo: Path, *args: str, at: datetime | None = None) -> str:
    """Run git in `repo` hermetically; `at` pins the author and committer time."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Research",
            "GIT_AUTHOR_EMAIL": "research@example.invalid",
            "GIT_COMMITTER_NAME": "Research",
            "GIT_COMMITTER_EMAIL": "research@example.invalid",
        }
    )
    if at is not None:
        env["GIT_COMMITTER_DATE"] = at.isoformat()
        env["GIT_AUTHOR_DATE"] = at.isoformat()
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def commit_file(repo: Path, path: Path, message: str, *, at: datetime) -> None:
    """Stage `path` (inside `repo`) and commit it at `at`."""
    git(repo, "add", path.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", message, at=at)
