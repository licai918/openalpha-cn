"""`V2-P4-111`: importing the API module wrote to the user's real runtime directory, forever.

`app = create_app()` sat at module scope, so `import openalpha_cn.api.app` ran migrations and
took a SQLite backup under `runtime/backups/`. `create_app`'s own docstring makes a point of that
line being "filesystem-free for `.env`"; it was not filesystem-free for `runtime/`.

## The second cause, which is the one that made the count grow

Measured against a **copy** of the user's real `runtime/state.sqlite3` (the original untouched):

    run 1: from=4 to=4 applied=[] backup=True | backups now=1
    run 2: from=4 to=4 applied=[] backup=True | backups now=2
    run 3: from=4 to=4 applied=[] backup=True | backups now=3

That database is at `user_version=4` and its `schema_migrations` reads
`[(1, baseline), (2, demo_add_runs_archived_at), (3, demo_add_runs_archived_at),
(4, create_query_path_indexes)]` -- it predates the ordering fix that puts
`create_validation_results` before the demo migration, so it has no `validation_results` table.
`_rewrite_contract_identities` is precondition-bound to that table, raises
`MigrationNotYetApplicable` and leaves itself pending; but `run_migrations` took the backup
*before* the loop, so **every process start copies the database and applies nothing**. In the
user's repository that is 125 of 128 files, all `v4`, all 139,264 bytes, one per run, with no
terminating condition. It is not the import that grows the directory -- it is that a deferred
migration pays for a backup it never uses.

`run_migrations`' own docstring already promised the right behaviour for the empty case: "If
nothing is pending, this is a fast no-op that opens no write transaction and takes no backup."
A migration that defers is the same situation arrived at one step later.

## What is fixed and what is deliberately not

Two changes and one refusal:

- `app` is created on first **attribute access** (PEP 562 `__getattr__`) rather than at import.
  `uvicorn openalpha_cn.api.app:app` and `from openalpha_cn.api.app import app` both resolve
  through `getattr`, so every real caller is unchanged; `import openalpha_cn.api.app` touches
  nothing.
- the backup is taken immediately before the **first migration that actually applies**, so a run
  where everything defers copies nothing.
- the existing backups are **not** deleted. They are the user's data and nothing here removes
  them; `openalpha migrate prune-backups` is the documented cleanup path and it does nothing
  unless a person runs it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.storage.migrations import (
    MIGRATIONS,
    Migration,
    MigrationNotYetApplicable,
    run_migrations,
)

NOW: Final[datetime] = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


def _deferring(connection: object) -> None:
    """A migration that is never applicable, which is the shape a real one takes on a fresh store.

    `_demo_add_runs_archived_at` and `_rewrite_contract_identities` both defer this way when the
    table they alter has not been created yet, and `_rewrite_contract_identities` defers
    *permanently* on a database whose history predates `create_validation_results`. Written as a
    stub rather than driven through the real one because the property under test is
    `run_migrations`' own, and a stub is the only way to hold it at an arbitrary version.
    """
    raise MigrationNotYetApplicable("this migration is never applicable")


def test_importing_the_api_module_writes_nothing(tmp_path: Path) -> None:
    """The row's own reproduction, in a subprocess because an import happens once per process.

    A bare `import` is the whole test: it is what a linter, a documentation build, a `--help`
    that touches the package, or an editor's auto-import does, and none of those asked for a
    database. The runtime directory is pointed at `tmp_path` through the environment so that a
    regression writes somewhere harmless rather than into the repository -- which is exactly how
    the user's own `runtime/backups/` reached 128 files.
    """
    runtime = tmp_path / "runtime"

    subprocess.run(
        [sys.executable, "-c", "import openalpha_cn.api.app"],
        check=True,
        env={"PATH": "/usr/bin:/bin", "OPENALPHA_RUNTIME_DIR": str(runtime)},
        capture_output=True,
    )

    assert not runtime.exists(), sorted(path.name for path in runtime.rglob("*"))


def test_asking_the_module_for_its_app_still_builds_one(tmp_path: Path) -> None:
    """The other direction: laziness must not become absence.

    `uvicorn openalpha_cn.api.app:app` imports the module and then reads the attribute, and
    `openalpha serve` passes exactly that string. If `__getattr__` did not answer, the fix would
    have moved a defect from "an import writes a file" to "the service does not start", so this
    asserts the attribute resolves *and* that reading it is what creates the directory.
    """
    runtime = tmp_path / "runtime"
    script = (
        "import openalpha_cn.api.app as module, json, pathlib\n"
        "before = pathlib.Path(module.load_config().runtime_dir).exists()\n"
        "app = module.app\n"
        "after = pathlib.Path(module.load_config().runtime_dir).exists()\n"
        "print(json.dumps({'before': before, 'after': after, 'routes': len(app.routes) > 10}))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env={"PATH": "/usr/bin:/bin", "OPENALPHA_RUNTIME_DIR": str(runtime)},
        capture_output=True,
        text=True,
    )
    answer = json.loads(completed.stdout.strip().splitlines()[-1])

    assert answer == {"before": False, "after": True, "routes": True}


def test_a_run_where_every_migration_defers_takes_no_backup(tmp_path: Path) -> None:
    """The cause of the growth, held at the version the user's own database is stuck at.

    Three runs and the assertion is on the third as much as the first: a backup taken once and
    then not again would be the correct behaviour for a *pending* migration, and what was
    measured is a backup taken on **every** run with nothing ever applied. Driving it three
    times is what separates those.
    """
    path = tmp_path / "state.sqlite3"
    migrations = (*MIGRATIONS[:1], Migration(version=99, name="never", apply=_deferring))

    settled = run_migrations(path, clock=lambda: NOW, migrations=MIGRATIONS[:1])
    backups = tmp_path / "backups"
    taken_by_the_real_migration = len(list(backups.iterdir()))

    results = [run_migrations(path, clock=lambda: NOW, migrations=migrations) for _ in range(3)]

    assert settled.applied and settled.backup_path is not None
    assert taken_by_the_real_migration == 1
    assert [result.applied for result in results] == [(), (), ()]
    assert [result.backup_path for result in results] == [None, None, None]
    assert len(list(backups.iterdir())) == taken_by_the_real_migration


def test_a_run_that_actually_applies_something_still_backs_up_first(tmp_path: Path) -> None:
    """The separator, and the property the whole backup exists for.

    A fix that stopped backing up would satisfy the test above and destroy the guarantee
    `MigrationFailedError` is written around -- "re-raised as `MigrationFailedError` naming the
    pre-migration backup". So the backup must still be taken, and taken *before* anything is
    written: the assertion is that the copy exists and that it is a copy of the database as it
    was, which is what `from_version` in its own filename records.
    """
    path = tmp_path / "state.sqlite3"

    result = run_migrations(path, clock=lambda: NOW, migrations=MIGRATIONS[:2])

    assert result.applied
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.name.startswith("state.sqlite3.v0.")


def test_the_backup_is_taken_before_the_migration_that_needs_it(tmp_path: Path) -> None:
    """Moving the backup later must not move it past the write it protects.

    A migration that raises must find its own pre-migration copy on disk. Held by making one
    fail on purpose and reading the path off the refusal, because "the backup happens somewhere
    before the end" is not the guarantee -- the guarantee is that the bytes in that file predate
    the failed `apply()`.
    """

    def _explodes(connection: object) -> None:
        raise RuntimeError("this migration fails")

    path = tmp_path / "state.sqlite3"
    migrations = (*MIGRATIONS[:1], Migration(version=98, name="explodes", apply=_explodes))

    from openalpha_cn.storage.migrations import MigrationFailedError

    with pytest.raises(MigrationFailedError) as raised:
        run_migrations(path, clock=lambda: NOW, migrations=migrations)

    assert raised.value.backup_path.exists()
    assert raised.value.name == "explodes"


def test_the_cleanup_path_exists_names_what_it_would_remove_and_removes_nothing_unasked(
    tmp_path: Path,
) -> None:
    """`openalpha migrate prune-backups`, the documented cleanup path this row chose.

    A retention cap applied automatically would have deleted the user's 128 existing backups the
    next time they ran anything, and those are their data. So the removal is a command a person
    runs, `--dry-run` is what it does by default in the sense that matters -- it lists before it
    deletes -- and `--keep` is explicit. The dry run is asserted to leave every file in place,
    which is the assertion that would have caught the opposite choice.
    """
    backups = tmp_path / "backups"
    backups.mkdir(parents=True)
    made = []
    for index in range(5):
        target = backups / f"state.sqlite3.v4.2026082{index}T100000Z.bak"
        target.write_bytes(b"x" * (index + 1))
        made.append(target)

    dry = CliRunner().invoke(
        app,
        ["migrate", "prune-backups", "--runtime-dir", str(tmp_path), "--keep", "2", "--dry-run"],
    )

    assert dry.exit_code == 0, dry.output
    assert all(path.exists() for path in made)
    assert "3" in dry.output
    assert made[0].name in dry.output
    assert made[-1].name not in dry.output


def test_pruning_keeps_the_newest_and_says_which_it_removed(tmp_path: Path) -> None:
    """The command's own effect, and the boundary of `--keep`.

    Newest-first because a backup's value decays: the copy taken before the migration that is
    still pending is the one an operator might restore from, and a copy from three months of
    deferrals ago is not. `--keep 2` is driven rather than the default so the ordering is
    asserted rather than assumed.
    """
    backups = tmp_path / "backups"
    backups.mkdir(parents=True)
    made = []
    for index in range(5):
        target = backups / f"state.sqlite3.v4.2026082{index}T100000Z.bak"
        target.write_bytes(b"x")
        made.append(target)

    result = CliRunner().invoke(
        app, ["migrate", "prune-backups", "--runtime-dir", str(tmp_path), "--keep", "2"]
    )

    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in backups.iterdir()) == sorted(
        path.name for path in made[-2:]
    )
    assert made[0].name in result.output


def test_pruning_a_directory_that_holds_nothing_is_not_an_error(tmp_path: Path) -> None:
    """A cleanup command that failed on a clean tree would be `|| true`-d in the first script.

    The same reasoning `PanelExit` gives for `panel doctor`'s notices: an exit code that fires on
    the healthy case stops being read.
    """
    result = CliRunner().invoke(app, ["migrate", "prune-backups", "--runtime-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "nothing to remove" in result.output
