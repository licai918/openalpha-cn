"""The half of reconciliation that history's own testimony used to hide (`V2-P5-029`).

`V2-P5-026` says its reconciliation works "by inspecting the schema, never trusting either
counter". Measured, it trusts one of them completely. `_unrecorded` selects on
`migration.name not in (recorded | repaired)`, so `effect_present` is only ever consulted
for migrations the audit trail does **not** mention. The other class -- *the effect is gone
but the audit row is still there* -- was never checked by anything.

That class is not hypothetical, and it is the same silent permanent stall `V2-P5-026` exists
to end, entered through the other door. Measured on a database at head, dropping
`validation_results` and **leaving its `schema_migrations` row in place**:

    after 5 `migrate run` restarts:
      user_version=8, recorded=[1..8], pending=[], repairs=absent,
      validation_results=False
      `migrate run`    -> "schema version 8 is up to date; nothing to do"
      `migrate status` -> eight `applied` lines and nothing else

The table is gone, permanently, and every operator surface reports a healthy database.

**The existing fixture could not have found it.** `test_cli_migrate.py`'s repair cases always
do `DROP TABLE validation_results` **and** `DELETE FROM schema_migrations WHERE version = 2`
together, and one of them documents that pair as "what a dropped table looks like from the
engine's side". It is not: `DROP TABLE` does not touch `schema_migrations`. Deleting the audit
row is precisely the condition `_unrecorded` keys on, so the fixture manufactured the state
the guard detects and the half where history lies went untested.

This file tests only that half, always dropping the effect and always **leaving the audit row
alone**, which is what really happens.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.storage.migrations import (
    ADD_RUNS_MODE_PROJECTION_VERSION,
    CREATE_VALIDATION_RESULTS_VERSION,
    MIGRATIONS,
    REPAIR_APPLIED,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    read_status,
)

runner = CliRunner()


def _at_head(tmp_path: Path) -> Path:
    """A real runtime directory migrated to head through the command line."""
    runtime_dir = tmp_path / "runtime"
    for _ in range(3):
        result = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
        assert result.exit_code == 0, result.output
    return runtime_dir


def _status(runtime_dir: Path) -> dict:
    result = runner.invoke(app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _drop_the_table_only(runtime_dir: Path) -> None:
    """Drop `validation_results` and leave `schema_migrations` exactly as it was.

    The whole point of this file: no `DELETE FROM schema_migrations` here, because a real
    `DROP TABLE` does not perform one.
    """
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection, connection:
        connection.execute("DROP TABLE validation_results")


def _has_validation_results(runtime_dir: Path) -> bool:
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection:
        return (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("validation_results",),
            ).fetchone()[0]
            > 0
        )


def _audit_row_survived(runtime_dir: Path) -> bool:
    """The fixture's own precondition: history still claims this migration ran."""
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection:
        return (
            connection.execute(
                "SELECT count(*) FROM schema_migrations WHERE version = ?",
                (CREATE_VALIDATION_RESULTS_VERSION,),
            ).fetchone()[0]
            > 0
        )


def test_migrate_status_names_an_effect_the_schema_has_lost(tmp_path: Path) -> None:
    """`migrate status` must not report a database with a missing table as healthy.

    Before this row it reported eight `applied` lines, an empty `pending`, an empty
    `unrecorded` and nothing else, which is the whole defect: the operator surface agreed
    with the audit trail instead of with the database.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)
    assert _audit_row_survived(runtime_dir), "fixture broken: it deleted the audit row"

    payload = _status(runtime_dir)

    assert [entry["name"] for entry in payload["damaged"]] == ["create_validation_results"]
    # The old surfaces are untouched: history really does still record it, and the counter
    # really is past it. Reporting it as `unrecorded` or `pending` would be a different lie.
    assert payload["unrecorded"] == []
    assert payload["pending"] == []
    assert CREATE_VALIDATION_RESULTS_VERSION in [entry["version"] for entry in payload["applied"]]


def test_migrate_status_text_names_it_too(tmp_path: Path) -> None:
    """The default rendering, not only `--json` -- an operator reads the text one."""
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)

    result = runner.invoke(app, ["migrate", "status", "--runtime-dir", str(runtime_dir)])

    assert result.exit_code == 0, result.output
    assert "damaged  2 create_validation_results" in result.output


def test_migrate_run_recreates_an_effect_the_schema_has_lost(tmp_path: Path) -> None:
    """The repair path already existed; it was only ever reached through the other door."""
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)
    assert not _has_validation_results(runtime_dir)

    repaired = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert repaired.exit_code == 0, repaired.output
    assert "repaired 2 create_validation_results" in repaired.output
    assert "nothing to do" not in repaired.output
    assert _has_validation_results(runtime_dir)
    after = _status(runtime_dir)
    assert after["damaged"] == []
    assert [(entry["version"], entry["resolution"]) for entry in after["repaired"]] == [
        (CREATE_VALIDATION_RESULTS_VERSION, REPAIR_APPLIED)
    ]


def test_the_repair_line_does_not_claim_nothing_had_recorded_it(tmp_path: Path) -> None:
    """`migrate run` must not tell an operator the opposite of what the audit trail holds.

    The line read "schema version was already past it **and nothing had recorded it**", which
    was true of every repair while `_unrecorded` was the only way in. For a damaged migration
    it is exactly backwards: `schema_migrations` records it, which is the whole reason nothing
    had ever checked its effect. Caught by reading the real output of the installed binary
    after the repair path was widened -- the assertion for the outcome half was already
    passing, so nothing in the suite objected to the false half beside it.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)

    repaired = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert repaired.exit_code == 0, repaired.output
    assert "nothing had recorded it" not in repaired.output
    assert "schema version was already past it" in repaired.output
    assert "its effect was missing and has been created" in repaired.output


def test_a_second_run_after_a_repair_finds_nothing_to_do(tmp_path: Path) -> None:
    """Idempotent: the predicate, not a ledger lookup, is what stops the second pass.

    A damaged migration's name lands in `schema_repairs`, and `_unrecorded` skips names it
    finds there -- but that ledger must *not* be what silences the damage check, or a table
    dropped a second time after being repaired once would become invisible forever. This
    pins the first half; `test_the_same_migration_can_be_repaired_twice` pins the second.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0

    again = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert again.exit_code == 0, again.output
    assert "is up to date; nothing to do" in again.output
    assert _status(runtime_dir)["damaged"] == []


def test_the_same_migration_can_be_repaired_twice(tmp_path: Path) -> None:
    """A table dropped, repaired, and dropped again is repaired again.

    `schema_repairs`' primary key is `(version, name)`, so a second repair of one migration
    is a key collision -- which under the old design was unreachable, because a repaired name
    was excluded from `_unrecorded` forever. Checking the effect regardless of the ledger makes
    it reachable, and a raw `INSERT` here would surface `sqlite3.IntegrityError` as
    `MigrationFailedError` against a database that is merely damaged twice.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    _drop_the_table_only(runtime_dir)

    second = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert second.exit_code == 0, second.output
    assert "repaired 2 create_validation_results" in second.output
    assert _has_validation_results(runtime_dir)
    assert [entry["resolution"] for entry in _status(runtime_dir)["repaired"]] == [REPAIR_APPLIED]


def test_dry_run_does_not_call_a_damaged_database_up_to_date(tmp_path: Path) -> None:
    """`migrate run --dry-run` branches on `pending` alone, and damage leaves `pending` empty.

    The same shape as the defect `test_migrate_run_that_only_repairs_does_not_claim_there_was
    _nothing_to_do` fixed for the real run, still present in the preview of it: an operator
    checking what a migration would do, on a database missing a table, was told "up to date;
    nothing to do". Dry-run is the surface people use *before* touching production, so it
    saying nothing is worse here than in the command that at least fixes it.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)

    result = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "nothing to do" not in result.output
    assert "create_validation_results" in result.output
    # Still a preview: it must not have repaired anything.
    assert not _has_validation_results(runtime_dir)
    assert _status(runtime_dir)["repaired"] == []


def test_dry_run_on_a_healthy_database_still_says_nothing_to_do(tmp_path: Path) -> None:
    """The negative half, without which the assertion above is satisfied by any wording."""
    runtime_dir = _at_head(tmp_path)

    result = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "is up to date; nothing to do" in result.output


def test_a_healthy_database_reports_no_damage(tmp_path: Path) -> None:
    """The negative case, without which every assertion above could pass on any database."""
    runtime_dir = _at_head(tmp_path)

    payload = _status(runtime_dir)

    assert payload["damaged"] == []
    assert payload["unrecorded"] == []
    assert payload["pending"] == []


def test_damage_at_exactly_the_watermark_is_found(tmp_path: Path) -> None:
    """`version <= current_version`, not `<` -- the migration the counter is *standing on*.

    Found by a surviving mutant, not by reading: changing `_damaged`'s comparison to `<` left
    every other assertion in this file green, because they all damage version 2 on a database
    at version 8 and never exercise the boundary. That is the same "the assertion cannot
    separate the two answers" shape this row was filed about, so it gets its own case rather
    than a wider fixture.

    `<` would be wrong in the most ordinary way possible: the head migration of any database is
    at exactly `user_version`, so a fresh install that loses its newest table would be the one
    case reconciliation could not see. Driven through `read_status` with a truncated registry
    because the *only* way to put the damaged migration at the watermark is to control both
    numbers, which the command line deliberately does not let a caller do.
    """
    runtime_dir = _at_head(tmp_path)
    path = runtime_dir / "state.sqlite3"
    _drop_the_table_only(runtime_dir)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {CREATE_VALIDATION_RESULTS_VERSION:d}")

    registry = MIGRATIONS[:CREATE_VALIDATION_RESULTS_VERSION]
    status = read_status(path, migrations=registry)

    assert status.current_version == CREATE_VALIDATION_RESULTS_VERSION
    assert [migration.name for migration in status.damaged] == ["create_validation_results"]
    assert status.pending == ()


def test_a_data_rewrite_is_never_called_damaged(tmp_path: Path) -> None:
    """The three migrations with no predicate stay out of `damaged` entirely.

    `damaged` means "the schema was asked and answered no". A migration whose effect is
    payload bytes cannot be asked, so claiming it is intact and claiming it is damaged are
    equally unfounded; `V2-P5-026`'s refusal to guess for those is unchanged here. This is
    the assertion that stops `damaged` from quietly becoming "everything at or below the
    watermark that we cannot prove is fine".
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)

    names = [entry["name"] for entry in _status(runtime_dir)["damaged"]]

    assert names == ["create_validation_results"]
    assert "rewrite_contract_identities" not in names
    assert "split_batch_task_items" not in names
    assert "rewrite_manifest_component_planes" not in names


def test_damage_below_an_undecidable_gap_is_still_repaired(tmp_path: Path) -> None:
    """An undecidable migration blocks what is *behind* it, not what is in front of it.

    `_reconcile` stops at the first migration it cannot decide, deliberately: repairs are a
    chain in version order, exactly like the pending loop. That discipline is kept. But the
    damaged `create_validation_results` here is at version 2 and the undecidable
    `rewrite_contract_identities` is at version 5, so version 2 must be repaired before the
    stop -- and under a design that appended the damaged list after the unrecorded one
    instead of merging both in version order, it would not be.
    """
    runtime_dir = _at_head(tmp_path)
    _drop_the_table_only(runtime_dir)
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection, connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (REWRITE_CONTRACT_IDENTITIES_VERSION,),
        )

    result = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert result.exit_code == 0, result.output
    assert _has_validation_results(runtime_dir)
    payload = _status(runtime_dir)
    assert [entry["version"] for entry in payload["repaired"]] == [
        CREATE_VALIDATION_RESULTS_VERSION
    ]
    # The undecidable one is still reported and still unresolved -- that policy is unchanged.
    assert [entry["version"] for entry in payload["unrecorded"]] == [
        REWRITE_CONTRACT_IDENTITIES_VERSION
    ]


def test_an_unrecorded_migration_behind_an_undecidable_one_is_not_called_undecidable(
    tmp_path: Path,
) -> None:
    """The `unrecorded` text must not assert a reason it never established.

    Measured before this row: unrecording versions 5 and 6 together left `migrate status`
    printing, for **both**, "its effect cannot be established by inspecting the schema". For
    version 5 (`rewrite_contract_identities`, no predicate) that is true. For version 6
    (`add_runs_mode_projection`, which has a predicate) it is false -- the loop simply broke
    at 5 and never asked. One sentence was doing duty for two different situations, and for
    the second one it was a claim nothing had measured.
    """
    runtime_dir = _at_head(tmp_path)
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection, connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version IN (?, ?)",
            (REWRITE_CONTRACT_IDENTITIES_VERSION, ADD_RUNS_MODE_PROJECTION_VERSION),
        )
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0

    result = runner.invoke(app, ["migrate", "status", "--runtime-dir", str(runtime_dir)])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("unrecorded")]
    assert len(lines) == 2, result.output
    undecidable, blocked = lines
    assert "rewrite_contract_identities" in undecidable
    assert "cannot be established by inspecting the schema" in undecidable
    assert "add_runs_mode_projection" in blocked
    assert "cannot be established by inspecting the schema" not in blocked
    assert "rewrite_contract_identities" in blocked, "say which migration is blocking it"
