"""Real provenance for a research run: the code commit and the effective config digest.

Both `RunManifest.code_commit` and `RunManifest.config_digest` (`domain/run.py`) feed
`DecisionLedger.decision_id`'s content-addressed hash (`domain/_identity.py`). Before
this module existed, every caller that did not pass its own value fell back to a
literal placeholder that *looked* like a value but was not one: `cli.py`'s
`--code-commit` defaulted to the string `"development"` and `--config-digest` to
`"0" * 64` -- so two runs built from genuinely different code or configuration minted
the identical `decision_id`, defeating the one property content-addressed identity
exists to provide.

`resolve_code_commit()` replaces that with an honest three-tier resolution: a live git
workspace, a build-time-embedded stamp, or -- when neither is available -- an explicit
marker that cannot be mistaken for a real commit (see `UNKNOWN_CODE_COMMIT`).
`compute_config_digest()` replaces the zero digest with a real SHA-256 over the
resolved, non-secret `OpenAlphaConfig` -- the one and only configuration surface this
package has today. Neither function is ever called from `domain/`, and neither one ever
reads a credential; see each function's docstring for exactly what is (and is not)
included.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from openalpha_cn import _build_commit
from openalpha_cn.config import OpenAlphaConfig
from openalpha_cn.domain.json_value import canonical_json_bytes

__all__ = [
    "UNKNOWN_CODE_COMMIT",
    "compute_config_digest",
    "resolve_code_commit",
]


UNKNOWN_CODE_COMMIT = "unknown-not-a-git-commit"
"""Returned by `resolve_code_commit()` when no real commit is available at all.

Deliberately not lowercase-hex-only (a real git SHA is 7-40 lowercase hex characters),
so it can never be mistaken for one, and long enough to satisfy
`RunManifest.code_commit`'s `min_length=7` -- see this module's docstring and the
task-17 report's governing principle: "never produce a value that looks real but is
not."
"""

_GIT_TIMEOUT_SECONDS = 5.0

# The exact `OpenAlphaConfig` fields that participate in `compute_config_digest`'s
# input, spelled out explicitly rather than derived from `config.model_dump()` -- see
# that function's docstring for why.
_CONFIG_DIGEST_FIELDS: tuple[str, ...] = (
    "runtime_dir",
    "web_dir",
    "max_request_bytes",
    "host",
    "port",
    "log_level",
)


def _run_git(args: list[str], *, cwd: Path) -> str | None:
    """Run one `git` subcommand anchored at `cwd`; return stripped stdout, or `None`.

    `None` covers every way this can fail to produce a usable answer: `git` is not on
    `PATH` at all (`FileNotFoundError` -- the wheel-install-with-no-git scenario this
    package must still work under), `cwd` is not inside any git working tree (git's own
    non-zero exit, e.g. "fatal: not a git repository"), or the process hangs past
    `_GIT_TIMEOUT_SECONDS`. Every branch is a graceful "this tier has no answer" --
    never an exception that propagates out of `resolve_code_commit()`.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def resolve_code_commit(*, anchor: Path | None = None) -> str:
    """Resolve the real code commit, in priority order, and never lie about it.

    1. **Git workspace** -- `git rev-parse HEAD` anchored at `anchor` (default: this
       module's own installed location, never the process's current working
       directory, which could coincidentally sit inside an unrelated git repository and
       report *that* repo's commit). Git itself walks upward from `anchor` to find
       `.git`, exactly like running the command by hand from anywhere inside a
       checkout. A workspace with uncommitted changes (staged, unstaged, or untracked
       and not `.gitignore`d) gets a literal `-dirty` suffix appended to the
       40-character SHA: an honest signal that *this exact run* cannot be reproduced
       from that commit alone, per the task-17 brief.
    2. **Build-time embedded value** -- `openalpha_cn._build_commit.BUILD_COMMIT`, a
       single constant a release build can stamp immediately before `uv build` (while
       `.git` is still present in the build environment), so a wheel installed with no
       `.git` at all can still report the exact commit it was built from. See
       `_build_commit.py`'s docstring for why no such stamping step exists yet.
    3. **Explicit unknown marker** -- `UNKNOWN_CODE_COMMIT`, when neither tier above has
       an answer. This is the case a wheel installed with no build stamp and no `.git`
       must hit, and it is covered by a test that never touches a real git repository
       (the hard "must work outside a git repository" requirement).
    """
    workspace_anchor = anchor if anchor is not None else Path(__file__).resolve().parent
    commit = _run_git(["rev-parse", "HEAD"], cwd=workspace_anchor)
    if commit:
        status = _run_git(
            ["status", "--porcelain", "--untracked-files=normal"], cwd=workspace_anchor
        )
        return f"{commit}-dirty" if status else commit
    if _build_commit.BUILD_COMMIT:
        return _build_commit.BUILD_COMMIT
    return UNKNOWN_CODE_COMMIT


def _digest_value(value: object) -> object:
    return str(value) if isinstance(value, Path) else value


def compute_config_digest(config: OpenAlphaConfig) -> str:
    """Return a SHA-256 hex digest of the fully-resolved, non-secret `OpenAlphaConfig`.

    `OpenAlphaConfig` (`config.py`) is, today, the entire configuration surface this
    package has: every `OPENALPHA_*` setting `load_config()` resolves from the real
    process environment. It is deliberately safe to hash in full -- no provider or
    model credential (`TUSHARE_TOKEN`, `CHAINLIN_API_KEY`, ...) is ever read into this
    settings object; those are read directly via `os.environ` in `providers/`,
    `models/`, and `cli.py`, and stay outside `OpenAlphaConfig` by design (see
    `config.py`'s module docstring). So there is no credential field to filter out here
    -- but this function still lists its fields explicitly (`_CONFIG_DIGEST_FIELDS`)
    rather than call `config.model_dump()`, so a field added to `OpenAlphaConfig` later
    requires a deliberate decision to add it to the digest too, never a silent default
    inclusion (or a silent gap that would make two differently-configured runs collide).

    Uses `canonical_json_bytes` -- the same deterministic-serialization helper
    `RunManifest`/`DecisionLedger` content-addressing already relies on
    (`domain/_identity.py`) -- so this digest is reproducible byte for byte given the
    same config, independent of dict ordering or platform.
    """
    payload = {name: _digest_value(getattr(config, name)) for name in _CONFIG_DIGEST_FIELDS}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
