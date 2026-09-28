"""The holdout pre-registration and the one-shot holdout guard (`V2-P6-008`).

The protocol's holdout runs exactly once, after the chosen configuration, its pass criteria and its
measurement settings were committed to git. `register` writes that registration;
`assert_holdout_allowed` is the guard; and `run_holdout` is the only path that writes the ledger's
holdout stage (`grid.append_ledger` and `grid.run_grid` both refuse it).

The guard reads the registration's bytes once and refuses four ways, in this order, each with its
own exception:

1. `RegistrationNotCommittedError` -- the registration is not in the committed history of the
   repository's `HEAD`, or the bytes on disk are not the bytes `HEAD` holds (an edit after the
   commit is an unregistered change).
2. `RegistrationAfterHoldoutError` -- the last commit that touched the registration is not earlier
   than a holdout row the ledger already holds: the holdout was looked at before the choice was
   fixed. Git keeps commit times to the second, so a row recorded within the commit's second is
   treated as not after it.
3. `HoldoutAlreadyRanError` -- the ledger already holds a holdout row: a measurement, or a claim
   left by a run that crashed. The holdout runs once, and a started run is that once.
4. `SourceChangedError` -- the bound code (`REGISTERED_PATHS`: `src/`, `scripts/research/`,
   `pyproject.toml`, `uv.lock`) at `HEAD`, or in the working tree, is not what it was at the
   registration's `code_commit`. `src/` is the package; `scripts/research/` computes every holdout
   metric (the complete-period filter, `p_excess`, the one-sided conversion, the information ratio,
   the annualisation); the two project files decide which libraries run them.
5. `ForeignPackageError` -- the `openalpha_cn` this process imported is not the one under the
   repository's `src/`: a worktree run can import the main checkout's package, and then the code
   measuring is not the code the diff checked.
6. `ForeignScriptsError` -- the `grid` and `registry` modules this process imported are not the
   ones under the repository's `scripts/research/`, for the same reason: `grid` computes the
   holdout's metrics and `registry` is this guard.

`uv.lock` in the binding proves the lock file did not change since the registration. It does not
prove the running virtual environment matches the lock: a venv synced from another lock, or edited
by hand, passes. Only `uv sync --locked` before the run (or checking installed versions against the
lock) would establish that, and nothing here does.

`run_holdout` then refuses, before writing anything, a configuration that is not the registered
one or whose measured window leaves the holdout window (`HoldoutConfigurationError`), and a measure
whose settings -- seed, sign-flip samples, excess benchmark, annualisation -- are not the registered
ones (`HoldoutSettingsError`). It writes a `holdout_claim` row carrying the registration's digest,
commit and settings **before** measuring, then the measurement row with the same binding. A crash
between the two leaves the claim, and the guard's third refusal then holds.

There is no forward runner. The holdout window is open-ended, so every date from 2024-01-02 on is
measured by `run_holdout` once or not at all; the forward period is evaluated through predictions
registered before their outcomes (the daily command, V2-P6-011, and the forward report,
V2-P6-012), which this module neither runs nor replaces.

What this does not establish. A commit time is what the committer's clock (or
`GIT_COMMITTER_DATE`) said, and a ledger row's `recorded_at` is what this machine's clock said;
the guard orders two self-reported times and cannot detect either being set back. Pushing the
registration commit to a remote someone else holds before the run is what makes its time
witnessed, and nothing here does that. The ledger file itself is not under git either: deleting
it deletes the claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import grid as _grid
from grid import (
    DEFAULT_REFUSALS,
    HOLDOUT_CLAIM,
    HOLDOUT_STAGE,
    MEASUREMENT,
    PROTOCOL_DEPENDENCE,
    PROTOCOL_FALSE_DISCOVERY_RATE,
    ResearchLedgerError,
    SettledMeasure,
    _append_holdout,
    check_window,
    config_id,
    measured_window,
    protocol_settings,
    read_ledger,
    to_json_value,
)

import openalpha_cn
from openalpha_cn.runtime.provenance import resolve_code_commit

REGISTRATION_SCHEMA: Final[str] = "openalpha-research-registration/v1"
REGISTERED_PATHS: Final[tuple[str, ...]] = ("src", "scripts/research", "pyproject.toml", "uv.lock")
"""The pathspecs whose bytes must be the registration's code commit's when the holdout runs."""
SCRIPTS_ROOT: Final[str] = "scripts/research"
"""Where, under the repository, the imported `grid` and `registry` modules must live."""
PACKAGE_ROOT: Final[str] = "src"
"""Where, under the repository, the imported `openalpha_cn` must live."""
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_GIT_TIMEOUT_SECONDS: Final[int] = 60


class RegistrationError(ValueError):
    """A registration that cannot be written as asked."""


class HoldoutRefusedError(RuntimeError):
    """The holdout may not run now. Each subclass is one refusal."""


class RegistrationNotCommittedError(HoldoutRefusedError):
    """The registration is not the committed one."""


class RegistrationAfterHoldoutError(HoldoutRefusedError):
    """The registration was committed no earlier than a holdout row already in the ledger."""


class HoldoutAlreadyRanError(HoldoutRefusedError):
    """The ledger already holds a holdout row: a measurement, or a crashed run's claim."""


class SourceChangedError(HoldoutRefusedError):
    """The bound code is not what it was at the registration's code commit."""


class ForeignPackageError(HoldoutRefusedError):
    """The imported `openalpha_cn` is not the repository's own."""


class ForeignScriptsError(HoldoutRefusedError):
    """The imported research scripts are not the repository's own."""


class HoldoutConfigurationError(HoldoutRefusedError):
    """The configuration handed to the holdout is not the registered one, or not a holdout one."""


class HoldoutSettingsError(HoldoutRefusedError):
    """The measure's settings are not the registered settings."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Admitted:
    """What the guard read and checked: the registration's bytes (read once), its body, and the
    commit that last touched it."""

    content: bytes
    registered: Mapping[str, Any]
    commit: str
    committed_at: datetime


def register(
    config: Mapping[str, object],
    criteria: Mapping[str, object],
    path: Path,
    *,
    code_commit: str | None = None,
    settings: Mapping[str, object] | None = None,
) -> str:
    """Write the registration to `path` and return the SHA-256 of its bytes.

    The file records the configuration (in the ledger's canonical form, with its `config_id`), the
    pass criteria, the code commit, the measurement settings (default: `grid.protocol_settings()`)
    and the protocol's false discovery rate and dependence assumption. `code_commit` defaults to
    this checkout's `HEAD` and must be a full commit id: an unknown or `-dirty` commit cannot be
    checked out to reproduce the run. Writing the same registration again returns the same
    digest; a different one at an existing path is refused. Committing the file is the caller's
    step, and the guard's first check.

    **Pass `code_commit` explicitly.** The default resolves this checkout's `HEAD` at the moment
    of writing, which the registration's own commit then moves -- and a registration file inside
    the repository makes the tree dirty until it is committed -- so a second `register` call with
    the default names a different commit (or refuses) and the idempotence above does not hold.
    Resolve the commit once, before writing, and pass it.
    """
    commit = (
        resolve_code_commit(anchor=Path(__file__).resolve().parent)
        if code_commit is None
        else code_commit
    )
    if not _FULL_COMMIT.fullmatch(commit):
        raise RegistrationError(
            f"code commit {commit!r} is not a full, clean git commit id; register from a "
            "committed checkout with no uncommitted change"
        )
    body = {
        "schema": REGISTRATION_SCHEMA,
        "config": to_json_value(config),
        "config_id": config_id(config),
        "criteria": to_json_value(criteria),
        "code_commit": commit,
        "settings": to_json_value(protocol_settings() if settings is None else settings),
        "false_discovery_rate": PROTOCOL_FALSE_DISCOVERY_RATE,
        "dependence": PROTOCOL_DEPENDENCE,
    }
    content = (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != content:
            raise RegistrationError(
                f"{path} already holds a different registration; a registration is written once"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run git in `repo` with no inherited `GIT_*` variable, so a hook's `GIT_DIR` cannot aim it
    at another repository."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _committed_registration(registration: Path, repo: Path) -> tuple[Path, _Admitted]:
    """The repository root and the registration as `HEAD` holds it: its bytes, read once and
    compared with `HEAD`'s, its body, and the last commit on `HEAD` that touched it."""
    if not registration.is_file():
        raise RegistrationNotCommittedError(
            f"{registration} is not a file; there is no registration to hold the holdout to"
        )
    content = registration.read_bytes()
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise RegistrationNotCommittedError(
            f"{repo} is not a git repository: {top.stderr.decode(errors='replace').strip()}"
        )
    root = Path(top.stdout.decode().strip()).resolve()
    try:
        relative = registration.resolve().relative_to(root).as_posix()
    except ValueError as error:
        raise RegistrationNotCommittedError(
            f"{registration} is outside the repository {root}; only a file git holds can be a "
            "committed registration"
        ) from error
    log = _git(root, "log", "-1", "--format=%H%x00%cI", "HEAD", "--", relative)
    stdout = log.stdout.decode().strip()
    if log.returncode != 0 or not stdout:
        raise RegistrationNotCommittedError(
            f"{relative} is not in the committed history of {root}'s HEAD; commit the "
            "registration before the holdout runs"
        )
    commit, committed_at = stdout.split("\x00")
    held = _git(root, "show", f"HEAD:{relative}")
    if held.returncode != 0 or held.stdout != content:
        raise RegistrationNotCommittedError(
            f"the bytes on disk at {relative} are not the ones HEAD holds; an edit after the "
            "commit is an unregistered change"
        )
    try:
        registered = json.loads(content)
    except ValueError as error:
        raise HoldoutConfigurationError(f"{relative} is not JSON: {error}") from error
    if not isinstance(registered, dict) or registered.get("schema") != REGISTRATION_SCHEMA:
        raise HoldoutConfigurationError(f"{relative} is not a {REGISTRATION_SCHEMA} file")
    return root, _Admitted(
        content=content,
        registered=registered,
        commit=commit,
        committed_at=datetime.fromisoformat(committed_at).astimezone(UTC),
    )


def _refuse_a_changed_source(
    root: Path,
    code_commit: object,
    *,
    paths: Sequence[str] = REGISTERED_PATHS,
    purpose: str = "the holdout measures with the registered code only",
) -> None:
    if not isinstance(code_commit, str) or not _FULL_COMMIT.fullmatch(code_commit):
        raise SourceChangedError(f"the registration names no full code commit: {code_commit!r}")
    bound = ", ".join(paths)
    committed = _git(root, "diff", "--quiet", code_commit, "HEAD", "--", *paths)
    if committed.returncode != 0:
        detail = "differs from" if committed.returncode == 1 else "cannot be compared with"
        raise SourceChangedError(
            f"the bound code ({bound}) at HEAD {detail} the registered code commit "
            f"{code_commit}; {purpose}"
        )
    working = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    if working.returncode != 0 or working.stdout.strip():
        raise SourceChangedError(
            f"the bound code ({bound}) in the working tree is not what HEAD holds: "
            f"{working.stdout.decode(errors='replace').strip() or 'git status failed'}"
        )


def _imported_package() -> Path:
    """Where this process imported `openalpha_cn` from."""
    return Path(openalpha_cn.__file__).resolve()


def _refuse_a_foreign_package(root: Path) -> None:
    package = _imported_package()
    try:
        package.relative_to(root / PACKAGE_ROOT)
    except ValueError as error:
        raise ForeignPackageError(
            f"this process imported openalpha_cn from {package}, which is not under "
            f"{root / PACKAGE_ROOT}; the code measuring would not be the code the registration "
            "binds (set PYTHONPATH to this repository's src)"
        ) from error


def _imported_scripts() -> tuple[Path, ...]:
    """Where this process imported the research scripts from: `grid`, then this module."""
    return (Path(_grid.__file__).resolve(), Path(__file__).resolve())


def _refuse_foreign_scripts(root: Path) -> None:
    for script in _imported_scripts():
        try:
            script.relative_to(root / SCRIPTS_ROOT)
        except ValueError as error:
            raise ForeignScriptsError(
                f"this process imported {script.name} from {script}, which is not under "
                f"{root / SCRIPTS_ROOT}; the holdout's metrics and guard would not be the code the "
                "registration binds"
            ) from error


def _holdout_allowed(registration: Path, ledger: Path, repo: Path) -> _Admitted:
    root, admitted = _committed_registration(registration, repo)
    rows = [row for row in read_ledger(ledger) if row.stage == HOLDOUT_STAGE]
    for row in rows:
        if admitted.committed_at >= row.recorded_at.replace(microsecond=0):
            raise RegistrationAfterHoldoutError(
                f"the registration was committed at {admitted.committed_at.isoformat()} "
                f"({admitted.commit}), after the holdout row recorded at "
                f"{row.recorded_at.isoformat()} (line {row.line}); a registration written after "
                "the holdout was seen registers nothing"
            )
    if any(row.kind == MEASUREMENT for row in rows):
        raise HoldoutAlreadyRanError(
            f"{ledger} already holds the holdout's measurement; the holdout runs once"
        )
    if rows:
        raise HoldoutAlreadyRanError(
            f"a holdout run was claimed at {rows[0].recorded_at.isoformat()} (line {rows[0].line}) "
            "and recorded no result; a run that started and crashed is still the one run -- the "
            "holdout runs once"
        )
    _refuse_a_changed_source(root, admitted.registered.get("code_commit"))
    _refuse_a_foreign_package(root)
    _refuse_foreign_scripts(root)
    return admitted


def admit_registered_code(
    registration: Path, repo: Path, *, also_bound: Sequence[str] = ()
) -> tuple[Path, _Admitted]:
    """The committed registration, provided the code running now is the code it registered.

    The holdout guard's admission without its ledger half, for a caller that produces forward
    results from the registered configuration -- the daily command (`V2-P6-011`) -- rather than
    measuring the holdout: the registration must be committed and byte-equal to `HEAD`'s
    (`RegistrationNotCommittedError`); the bound code (`REGISTERED_PATHS` plus `also_bound`, the
    caller's own files) at `HEAD` and in the working tree must be the registration's
    `code_commit` (`SourceChangedError`); and `openalpha_cn` and these scripts must have been
    imported from this repository (`ForeignPackageError`, `ForeignScriptsError`). Returns the
    repository root and what was admitted: the registration's bytes (read once), its body and the
    commit that last touched it.
    """
    root, admitted = _committed_registration(registration, repo)
    _refuse_a_changed_source(
        root,
        admitted.registered.get("code_commit"),
        paths=(*REGISTERED_PATHS, *also_bound),
        purpose="forward results come from the registered code only",
    )
    _refuse_a_foreign_package(root)
    _refuse_foreign_scripts(root)
    return root, admitted


def registered_checkout(
    registration: Path, repo: Path, *, also_bound: Sequence[str] = ()
) -> tuple[Path, str, str]:
    """The commit a checkout must stand at to run the registered code: the registration's own.

    Returns the repository root, the commit that last touched the registration on `HEAD`, and the
    registration's `code_commit`. The registration's commit is the one to stand at rather than
    `code_commit`: the registration file exists only from that commit on, and a checkout without
    it has no registration to admit. It is refused (`SourceChangedError`) unless the bound code
    (`REGISTERED_PATHS` plus `also_bound`) at that commit is exactly `code_commit`'s -- which is
    what a checkout standing there then passes `admit_registered_code` with, whatever the
    development checkout has moved on to since. Nothing about the running process is checked
    here: this answers where a checkout should stand, not whether this one does.
    """
    root, admitted = _committed_registration(registration, repo)
    code_commit = admitted.registered.get("code_commit")
    if not isinstance(code_commit, str) or not _FULL_COMMIT.fullmatch(code_commit):
        raise SourceChangedError(f"the registration names no full code commit: {code_commit!r}")
    paths = (*REGISTERED_PATHS, *also_bound)
    compared = _git(root, "diff", "--quiet", code_commit, admitted.commit, "--", *paths)
    if compared.returncode != 0:
        raise SourceChangedError(
            f"the bound code ({', '.join(paths)}) at the registration's commit "
            f"{admitted.commit} is not the registered code commit {code_commit}; no checkout "
            "of this history runs the registered code"
        )
    return root, admitted.commit, code_commit


def assert_holdout_allowed(registration: Path, ledger: Path, repo: Path) -> None:
    """Raise a `HoldoutRefusedError` unless the holdout may run now; see the module docstring."""
    _holdout_allowed(registration, ledger, repo)


def run_holdout(
    registration: Path,
    ledger: Path,
    repo: Path,
    config: Mapping[str, object],
    measure: SettledMeasure,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, object]:
    """Run the registered configuration once, under the registered settings, and ledger it.

    Guarded by `assert_holdout_allowed`; refuses a configuration that is not the registration's
    `config_id` or whose `start..end` leaves the holdout window, and a measure whose `settings`
    are not the registration's. Writes the claim, measures, and writes the measurement; a refusal
    the measure raises (`grid.DEFAULT_REFUSALS`) is the measurement's recorded error, and any other
    error propagates and leaves the claim.

    The window check reads no label past `end` (`label_sessions=0`), which is right for the
    strategy backtest the holdout runs: its last period is marked at `end`. A measure that reads a
    label past `end` would need its label length here.
    """
    now = _utc_now if clock is None else clock
    admitted = _holdout_allowed(registration, ledger, repo)
    registered = admitted.registered
    if config_id(config) != registered.get("config_id"):
        raise HoldoutConfigurationError(
            f"configuration {config_id(config)} is not the registered one "
            f"({registered.get('config_id')}); the holdout runs the registered configuration"
        )
    try:
        first, last = measured_window(config, label_sessions=0)
        check_window(HOLDOUT_STAGE, first, last)
    except ResearchLedgerError as error:
        raise HoldoutConfigurationError(f"the registered configuration: {error}") from error
    settings = to_json_value(measure.settings)
    if settings != registered.get("settings"):
        raise HoldoutSettingsError(
            f"the measure's settings {settings} are not the registered settings "
            f"{registered.get('settings')}"
        )
    binding = {
        "registration_sha256": hashlib.sha256(admitted.content).hexdigest(),
        "registration_commit": admitted.commit,
        "settings": settings,
    }
    started_at = now()
    _append_holdout(
        ledger, HOLDOUT_CLAIM, config, {**binding, "claimed_at": started_at}, recorded_at=started_at
    )
    try:
        result = dict(measure(config))
    except DEFAULT_REFUSALS as error:
        result = {"error": f"{type(error).__name__}: {error}"}
    row = {**result, **binding, "started_at": started_at}
    _append_holdout(ledger, MEASUREMENT, config, row, recorded_at=now())
    return row


def _utc_now() -> datetime:
    return datetime.now(UTC)
