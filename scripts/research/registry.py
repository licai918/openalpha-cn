"""The holdout pre-registration and the one-shot holdout guard (`V2-P6-008`).

The protocol's holdout runs exactly once, after the chosen configuration and its pass criteria were
committed to git. `register` writes that registration; `assert_holdout_allowed` is the guard; and
`run_holdout` is the only path that writes the ledger's holdout row, through the guard.

The guard refuses three ways, in this order, each with its own exception:

1. `RegistrationNotCommittedError` -- the registration is not in the committed history of the
   repository's `HEAD`, or the bytes on disk are not the bytes `HEAD` holds (an edit after the
   commit is an unregistered change).
2. `RegistrationAfterHoldoutError` -- the last commit that touched the registration is not earlier
   than a holdout row the ledger already holds: the holdout was looked at before the choice was
   fixed. Git keeps commit times to the second, so a row recorded within the commit's second is
   treated as not after it.
3. `HoldoutAlreadyRanError` -- the ledger already holds a holdout row. The holdout runs once.

What this does not establish. A commit time is what the committer's clock (or
`GIT_COMMITTER_DATE`) said, and a ledger row's `recorded_at` is what this machine's clock said;
the guard orders two self-reported times and cannot detect either being set back. Pushing the
registration commit to a remote someone else holds before the run is what makes its time
witnessed, and nothing here does that.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from grid import (
    HOLDOUT_STAGE,
    PROTOCOL_BOOTSTRAP_SAMPLES,
    PROTOCOL_DEPENDENCE,
    PROTOCOL_FALSE_DISCOVERY_RATE,
    PROTOCOL_RANDOM_SEED,
    Measure,
    append_ledger,
    config_id,
    read_ledger,
    to_json_value,
)

from openalpha_cn.runtime.provenance import resolve_code_commit

REGISTRATION_SCHEMA: Final[str] = "openalpha-research-registration/v1"
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_GIT_TIMEOUT_SECONDS: Final[int] = 60


class RegistrationError(ValueError):
    """A registration that cannot be written as asked."""


class HoldoutRefusedError(RuntimeError):
    """The holdout may not run now. Each subclass is one of the guard's refusals."""


class RegistrationNotCommittedError(HoldoutRefusedError):
    """The registration is not the committed one."""


class RegistrationAfterHoldoutError(HoldoutRefusedError):
    """The registration was committed no earlier than a holdout row already in the ledger."""


class HoldoutAlreadyRanError(HoldoutRefusedError):
    """The ledger already holds the holdout's row."""


class HoldoutConfigurationError(HoldoutRefusedError):
    """The configuration handed to the holdout is not the registered one."""


def register(
    config: Mapping[str, object],
    criteria: Mapping[str, object],
    path: Path,
    *,
    code_commit: str | None = None,
) -> str:
    """Write the registration to `path` and return the SHA-256 of its bytes.

    The file records the configuration (in the ledger's canonical form, with its `config_id`), the
    pass criteria, the code commit, and the protocol's seed, sign-flip sample count, false
    discovery rate and dependence assumption. `code_commit` defaults to this checkout's `HEAD` and
    must be a full commit id: an unknown or `-dirty` commit cannot be checked out to reproduce the
    run. Writing the same registration again returns the same digest; a different one at an
    existing path is refused. Committing the file is the caller's step, and the guard's first check.
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
        "random_seed": PROTOCOL_RANDOM_SEED,
        "bootstrap_samples": PROTOCOL_BOOTSTRAP_SAMPLES,
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


def _committed_registration(registration: Path, repo: Path) -> tuple[str, datetime]:
    """The last commit on `HEAD` that touched the registration, and its commit time -- after
    checking the bytes on disk are the bytes that commit left."""
    if not registration.is_file():
        raise RegistrationNotCommittedError(
            f"{registration} is not a file; there is no registration to hold the holdout to"
        )
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
    if held.returncode != 0 or held.stdout != registration.read_bytes():
        raise RegistrationNotCommittedError(
            f"the bytes on disk at {relative} are not the ones HEAD holds; an edit after the "
            "commit is an unregistered change"
        )
    return commit, datetime.fromisoformat(committed_at).astimezone(UTC)


def _holdout_allowed(registration: Path, ledger: Path, repo: Path) -> tuple[str, datetime]:
    commit, committed_at = _committed_registration(registration, repo)
    rows = [row for row in read_ledger(ledger) if row.stage == HOLDOUT_STAGE]
    for row in rows:
        if committed_at >= row.recorded_at.replace(microsecond=0):
            raise RegistrationAfterHoldoutError(
                f"the registration was committed at {committed_at.isoformat()} ({commit}), after "
                f"the holdout row recorded at {row.recorded_at.isoformat()} (line {row.line}); a "
                "registration written after the holdout was seen registers nothing"
            )
    if rows:
        raise HoldoutAlreadyRanError(
            f"{ledger} already holds the holdout's row (line {rows[0].line}); the holdout runs once"
        )
    return commit, committed_at


def assert_holdout_allowed(registration: Path, ledger: Path, repo: Path) -> None:
    """Raise a `HoldoutRefusedError` unless the holdout may run now; see the module docstring."""
    _holdout_allowed(registration, ledger, repo)


def run_holdout(
    registration: Path,
    ledger: Path,
    repo: Path,
    config: Mapping[str, object],
    measure: Measure,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, object]:
    """Run the registered configuration once and write its row to the ledger's holdout stage.

    Guarded by `assert_holdout_allowed`, and refuses a `config` whose identity is not the
    registration's `config_id`. The row's result carries the registration's digest and commit and
    the instant the run started. An error `measure` raises propagates and writes no row: nothing
    was measured.
    """
    now = _utc_now if clock is None else clock
    commit, _ = _holdout_allowed(registration, ledger, repo)
    content = registration.read_bytes()
    registered = json.loads(content)
    if registered.get("schema") != REGISTRATION_SCHEMA:
        raise HoldoutConfigurationError(f"{registration} is not a {REGISTRATION_SCHEMA} file")
    if config_id(config) != registered.get("config_id"):
        raise HoldoutConfigurationError(
            f"configuration {config_id(config)} is not the registered one "
            f"({registered.get('config_id')}); the holdout runs the registered configuration"
        )
    started_at = now()
    result = {
        **measure(config),
        "registration_sha256": hashlib.sha256(content).hexdigest(),
        "registration_commit": commit,
        "started_at": started_at,
    }
    append_ledger(ledger, HOLDOUT_STAGE, config, result, recorded_at=now())
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)
