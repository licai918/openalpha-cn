"""Real code-commit resolution and config-digest computation (V2-P0B-009).

Before this module existed, `cli.py`'s `--code-commit`/`--config-digest` options
defaulted to the literal strings `"development"` and `"0" * 64` -- values that *look*
like they could be meaningful but are not, so two runs built from genuinely different
code or configuration minted the identical `decision_id`. `resolve_code_commit()`
replaces that with an honest three-tier resolution (git workspace -> build-time-embedded
stamp -> an explicit, unmistakable "unknown" marker); `compute_config_digest()` replaces
the zero digest with a real SHA-256 over the resolved, non-secret `OpenAlphaConfig`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openalpha_cn.config import OpenAlphaConfig
from openalpha_cn.runtime import provenance
from openalpha_cn.runtime.provenance import (
    UNKNOWN_CODE_COMMIT,
    compute_config_digest,
    resolve_code_commit,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_a_file(path: Path, *, name: str = "file.txt", content: str = "v1") -> None:
    (path / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


# --- resolve_code_commit: git workspace tier --------------------------------------------


def test_resolve_code_commit_returns_the_real_head_sha_in_a_clean_git_workspace(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _commit_a_file(tmp_path)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = resolve_code_commit(anchor=tmp_path)

    assert result == expected
    assert len(result) == 40
    assert all(char in "0123456789abcdef" for char in result)


def test_resolve_code_commit_appends_a_dirty_suffix_for_uncommitted_modifications(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _commit_a_file(tmp_path)
    (tmp_path / "file.txt").write_text("v2 uncommitted", encoding="utf-8")

    result = resolve_code_commit(anchor=tmp_path)

    assert result.endswith("-dirty")
    assert len(result) == 46


def test_resolve_code_commit_treats_a_new_untracked_file_as_dirty_too(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit_a_file(tmp_path)
    (tmp_path / "new_file.txt").write_text("untracked", encoding="utf-8")

    result = resolve_code_commit(anchor=tmp_path)

    assert result.endswith("-dirty")


def test_resolve_code_commit_has_no_dirty_suffix_in_a_clean_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit_a_file(tmp_path)

    result = resolve_code_commit(anchor=tmp_path)

    assert not result.endswith("-dirty")


# --- resolve_code_commit: no-git-workspace tier (the hard "must work outside a git
# repository" requirement -- the wheel-install scenario) --------------------------------


def test_resolve_code_commit_returns_the_unknown_marker_outside_any_git_workspace(
    tmp_path: Path,
) -> None:
    """The hard constraint: this must work when not inside a git repository at all.
    `tmp_path` is a fresh pytest temporary directory with no `.git` of its own or in any
    parent up to the filesystem root, and no build-time stamp is present either."""
    result = resolve_code_commit(anchor=tmp_path)

    assert result == UNKNOWN_CODE_COMMIT


def test_resolve_code_commit_returns_the_unknown_marker_when_git_itself_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target machine that installed this package from a wheel might not have the
    `git` binary on `PATH` at all -- `subprocess.run` then raises `FileNotFoundError`,
    which must be handled exactly like "not a git repository", never propagate and
    crash the caller."""

    def _missing_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(provenance.subprocess, "run", _missing_git)

    result = resolve_code_commit(anchor=tmp_path)

    assert result == UNKNOWN_CODE_COMMIT


# --- resolve_code_commit: build-time-embedded tier --------------------------------------


def test_resolve_code_commit_uses_the_build_embedded_stamp_when_git_has_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provenance._build_commit, "BUILD_COMMIT", "b" * 40)

    result = resolve_code_commit(anchor=tmp_path)

    assert result == "b" * 40


def test_resolve_code_commit_prefers_a_real_git_workspace_over_the_build_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _commit_a_file(tmp_path)
    monkeypatch.setattr(provenance._build_commit, "BUILD_COMMIT", "b" * 40)

    result = resolve_code_commit(anchor=tmp_path)

    assert result != "b" * 40
    assert len(result) == 40


# --- the governing principle, made an executable assertion ------------------------------


def test_unknown_code_commit_marker_cannot_be_mistaken_for_a_real_git_sha() -> None:
    """A real git SHA is lowercase-hex-only, 7-40 characters. The fallback marker must
    satisfy `RunManifest.code_commit`'s `min_length=7` while being structurally
    impossible to confuse with a real commit."""
    assert len(UNKNOWN_CODE_COMMIT) >= 7
    assert not all(char in "0123456789abcdef" for char in UNKNOWN_CODE_COMMIT)
    assert UNKNOWN_CODE_COMMIT != "development"


# --- compute_config_digest ---------------------------------------------------------------


def test_compute_config_digest_is_a_real_sha256_hex_digest() -> None:
    digest = compute_config_digest(OpenAlphaConfig())

    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)
    assert digest != "0" * 64


def test_compute_config_digest_is_deterministic_for_an_equal_config() -> None:
    first = compute_config_digest(OpenAlphaConfig(host="127.0.0.1", port=8000))
    second = compute_config_digest(OpenAlphaConfig(host="127.0.0.1", port=8000))

    assert first == second


def test_compute_config_digest_changes_when_a_config_field_changes() -> None:
    baseline = compute_config_digest(OpenAlphaConfig(port=8000))
    changed = compute_config_digest(OpenAlphaConfig(port=9000))

    assert baseline != changed


def test_compute_config_digest_is_unaffected_by_unrelated_credential_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's trap: a credential must never enter the digest input, directly or by
    changing its value. `OpenAlphaConfig` only ever reads `OPENALPHA_*` names (see
    `config.py`'s module docstring), so setting an unrelated provider credential must
    leave the digest byte-for-byte identical."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    without_credential = compute_config_digest(OpenAlphaConfig())

    monkeypatch.setenv("TUSHARE_TOKEN", "sk-super-secret-value-must-not-affect-the-digest")
    with_credential = compute_config_digest(OpenAlphaConfig())

    assert without_credential == with_credential
