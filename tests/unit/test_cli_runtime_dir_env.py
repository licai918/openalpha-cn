"""`OPENALPHA_RUNTIME_DIR` must reach every command that takes `--runtime-dir`.

`config.py` states the precedence this package promises: "an already-exported real
environment variable always wins over ... a field's compiled-in default." Every CLI
command that names a runtime directory used to break that promise the same way -- it
declared `runtime_dir: ... = Path("./runtime")` as a *Typer option default*, so the
option was never "omitted" as far as the command body could tell, and `load_config()`
was never consulted. The exported variable lost to a compiled-in default.

That is a production fault, not a tidiness one. `Dockerfile` sets
`ENV OPENALPHA_RUNTIME_DIR=/data` alongside `WORKDIR /data` and `VOLUME ["/data"]`, so
the served application opens `/data/state.sqlite3` while `docker exec ... openalpha
migrate status` resolved `./runtime` against the working directory and reported on
`/data/runtime/state.sqlite3` -- a *different*, absent database. The operator was told
"schema version 0, 8 pending" about a file nobody serves, and a decoy database was
created on the mounted volume as a side effect. `migrate run` would then have migrated
the decoy.

The guard here is deliberately structural rather than a list of command names. An
enumeration of the affected commands was made by hand once and was wrong -- it named
eight of the twenty-eight that actually had the fault, and the twenty missed included
`jobs list`, which silently created *and migrated* a decoy database. So
`test_no_command_hardcodes_the_runtime_directory_as_an_option_default` walks the live
Typer application instead of a list, and a newly added command with the old shape turns
it red without anyone remembering to update a fixture.

Every behavioural claim below goes through `CliRunner` against the real `app`, never an
internal import, because the fault was in the option wiring -- calling the resolution
helper directly would have passed against the broken tree.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Final

import pytest
import typer
from typer.models import OptionInfo
from typer.testing import CliRunner

from openalpha_cn.cli import app

runner = CliRunner()


def _walk(group: typer.Typer, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """Every ``(command path, callback)`` pair reachable in ``group``.

    Walks the registered Typer tree rather than the module's globals so that a command
    which exists but was never registered cannot hide from the guard, and a registered
    one cannot be missed because its function name does not match its command name.
    """
    found: list[tuple[tuple[str, ...], Any]] = []
    for command in group.registered_commands:
        callback = command.callback
        if callback is None:
            continue
        name = command.name or callback.__name__.replace("_", "-")
        found.append(((*prefix, name), callback))
    for sub in group.registered_groups:
        typer_instance = sub.typer_instance
        if typer_instance is None:
            continue
        name = sub.name or ""
        found.extend(_walk(typer_instance, (*prefix, name) if name else prefix))
    return found


def _runtime_dir_defaults() -> dict[str, Any]:
    """The declared default of every ``runtime_dir`` parameter in the CLI, by command."""
    defaults: dict[str, Any] = {}
    for path, callback in _walk(app):
        parameter = inspect.signature(callback).parameters.get("runtime_dir")
        if parameter is None:
            continue
        defaults[" ".join(path)] = parameter.default
    return defaults


def test_the_cli_really_does_expose_many_runtime_dir_commands() -> None:
    """Guard the guard: an empty walk would make every assertion below vacuous.

    `_walk` reaches into Typer's registration internals, so a Typer upgrade that renames
    `registered_groups` would silently return nothing and turn the structural test into
    a test that passes on any tree at all. Twenty is a floor well under the twenty-eight
    measured here, not an equality -- this file has no business going red because a
    command was legitimately added or removed.
    """
    assert len(_runtime_dir_defaults()) >= 20


def test_no_command_hardcodes_the_runtime_directory_as_an_option_default() -> None:
    """No `--runtime-dir` option may carry a path as its Typer default.

    `None` is the only default that lets a command tell "the caller omitted this" from
    "the caller asked for ./runtime", and that distinction is the whole fix: only the
    first case may fall back to `load_config().runtime_dir`.
    """
    offenders = {
        command: default
        for command, default in _runtime_dir_defaults().items()
        if isinstance(default, Path)
        or (isinstance(default, OptionInfo) and isinstance(default.default, Path))
    }
    assert offenders == {}


ENV_VAR: Final[str] = "OPENALPHA_RUNTIME_DIR"


@pytest.mark.parametrize(
    "command",
    [
        # Two of the eight the hand-made enumeration caught ...
        ["migrate", "status", "--json"],
        ["migrate", "run"],
        # ... and one it missed, which on the broken tree created *and migrated* its
        # decoy: `jobs list` logged `migration_applied` for versions 1 and 2 against a
        # database in the working directory that nothing serves.
        ["jobs", "list"],
    ],
)
def test_an_exported_runtime_dir_is_where_a_storage_command_opens_its_database(
    command: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exported variable decides where state lives, and nothing lands in the cwd.

    Restricted to the commands that genuinely open the database, because those are the
    ones where an empty fixture can tell the two answers apart. Measured on the broken
    tree, these three left a `./runtime/state.sqlite3` decoy in the working directory
    and the exported directory empty; commands that only read -- `shortlist list`,
    `model predictions` -- created nothing in *either* place, so putting them here would
    be an assertion that passes on the broken tree too. They are covered instead by the
    structural test above and, for `shortlist list`, by the seeded test below.

    The working directory is a fresh `tmp_path/cwd` so the *absence* of a decoy is a
    real observation rather than an artefact of running somewhere already dirty --
    and so a regression can never write into the repository's own `runtime/`.
    """
    state = tmp_path / "state"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv(ENV_VAR, str(state))
    monkeypatch.chdir(cwd)

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert (state / "state.sqlite3").exists(), f"{command} did not open the exported directory"
    assert not (cwd / "runtime").exists(), f"{command} created a decoy at ./runtime"


def test_a_read_only_command_reads_what_the_exported_directory_holds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`shortlist list` -- one of the twenty the enumeration missed -- reads the right store.

    A read-only command touching an empty runtime directory creates nothing anywhere, so
    an empty fixture cannot separate "read the exported directory" from "read ./runtime";
    both print nothing and exit 0. This seeds a well-formed content address into the
    *exported* directory only, which makes the two answers differ in the output itself:
    resolving `./runtime` prints an empty list here, not this id.
    """
    shortlist_id = "sla_0123456789abcdef01234567"
    state = tmp_path / "state"
    (state / "shortlists").mkdir(parents=True)
    (state / "shortlists" / f"{shortlist_id}.json").write_text("{}", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv(ENV_VAR, str(state))
    monkeypatch.chdir(cwd)

    result = runner.invoke(app, ["shortlist", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"shortlist_ids": [shortlist_id]}


def test_migrate_status_reports_the_exported_directorys_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`migrate status --json` names the database an operator's server actually opens.

    The `path` field is what a `docker exec ... migrate status` reader believes they are
    being told about, so it reporting the wrong file is the fault at its most direct.
    """
    state = tmp_path / "state"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv(ENV_VAR, str(state))
    monkeypatch.chdir(cwd)

    result = runner.invoke(app, ["migrate", "status", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["path"] == str(state / "state.sqlite3")


def test_an_explicit_flag_still_beats_the_exported_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--runtime-dir` outranks the environment -- the fix must not invert precedence.

    Both directories exist and differ, so this cannot pass by accident: the assertion
    separates the two answers rather than merely observing that one of them was used.
    """
    flagged = tmp_path / "flagged"
    exported = tmp_path / "exported"
    monkeypatch.setenv(ENV_VAR, str(exported))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["migrate", "status", "--json", "--runtime-dir", str(flagged)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["path"] == str(flagged / "state.sqlite3")
    assert not exported.exists()


def test_with_neither_flag_nor_variable_the_compiled_in_default_still_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment with no `OPENALPHA_RUNTIME_DIR` behaves exactly as before.

    `config.py` promises "a process with no `OPENALPHA_*` variables set at all behaves
    exactly as before", and `OpenAlphaConfig.runtime_dir` still defaults to `./runtime`.
    Run from `tmp_path` so the relative path lands there and never in the repository.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["migrate", "status", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["path"] == str(Path("runtime") / "state.sqlite3")
    assert (tmp_path / "runtime" / "state.sqlite3").exists()


def test_an_unrelated_broken_config_field_is_named_only_when_config_is_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consulting `load_config()` stays lazy, so `--runtime-dir` bypasses its validation.

    `load_config()` validates every `OPENALPHA_*` field atomically, which is why P0.B
    Finding 2 removed it from `main()`. Resolving `--runtime-dir` through it re-admits
    that coupling on purpose but only for callers who gave the command no other way to
    know where state lives: for them a named `ConfigError` and exit 1 beats silently
    operating on the wrong database. A caller who passed the flag is unaffected, and
    this asserts both halves against the same broken environment so the pair cannot
    drift apart.
    """
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "exported"))
    monkeypatch.chdir(tmp_path)

    without_flag = runner.invoke(app, ["migrate", "status", "--json"])
    assert without_flag.exit_code == 1
    assert "OPENALPHA_MAX_REQUEST_BYTES" in without_flag.output

    flagged = tmp_path / "flagged"
    with_flag = runner.invoke(app, ["migrate", "status", "--json", "--runtime-dir", str(flagged)])
    assert with_flag.exit_code == 0, with_flag.output
    assert json.loads(with_flag.output)["path"] == str(flagged / "state.sqlite3")
