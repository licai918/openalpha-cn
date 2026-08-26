"""The stalled-chain defect a version renumbering left in already-migrated databases (V2-P5-026).

Integration, not unit: every property here is about `PRAGMA user_version`, a real
`schema_migrations` table, and real DDL against a real file -- none of which has a meaningful
in-memory double.

The fixture below does not hand-poke a database into the broken shape; it *replays the two
registry generations that produced it*, so the shape under test is derived from the same cause
the real database has rather than asserted into existence.
"""

import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.storage import migrations as migrations_module
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    ADD_RUNS_MODE_PROJECTION_VERSION,
    BASELINE_VERSION,
    CREATE_QUERY_PATH_INDEXES_VERSION,
    CREATE_VALIDATION_RESULTS_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    MIGRATIONS,
    REPAIR_APPLIED,
    REPAIR_VERIFIED,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    Migration,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository


def _build_v1_shaped_tables(path: Path) -> None:
    """Create the pre-migration table layout the way a real v1 install got it: as a side
    effect of constructing the stores that own the tables, with no `schema_migrations`.

    Row-less on purpose. The database this module reproduces holds zero rows in all fourteen
    of its tables -- measured on a copy of it -- so an empty fixture is the faithful one, and
    "records stay readable across this chain" is already `test_migrations.py`'s and
    `test_identity_rewrite.py`'s subject on the ordinary path, which the repair does not
    change. What this fixture must get exactly right is the *table set*, because table
    existence is the precondition every migration from 3 upward is gated on.
    """
    SQLiteRunRepository(path)
    SQLiteResearchMemory(path)
    SQLitePortfolioLedger(path)
    SQLiteReportStore(path)
    SQLiteBatchTaskStore(path)


def _registry_entry(version: int) -> Migration:
    return next(migration for migration in MIGRATIONS if migration.version == version)


def _pre_reorder_registry() -> tuple[Migration, ...]:
    """The registry as commit `1e54104` shipped it: baseline at 1, the demo migration at 2.

    `create_validation_results` did not exist yet. Commit `6eba39c` added it at version 2 and
    renumbered the demo migration 2 -> 3, which is the event this whole module is about: every
    database that had already crossed version 2 under the first numbering had version 2 mean
    something else from then on.
    """
    demo = _registry_entry(DEMO_ADD_RUNS_ARCHIVED_AT_VERSION)
    return (
        _registry_entry(BASELINE_VERSION),
        Migration(version=2, name=demo.name, apply=demo.apply),
    )


def _post_reorder_registry_as_a_v2_database_sees_it() -> tuple[Migration, ...]:
    """Today's registry with version 2 removed -- which is what a database already at
    version 2 *sees*, because `_pending()` filters on `version > user_version`.

    Passing it explicitly rather than passing `MIGRATIONS` is what keeps this fixture honest
    after the repair below exists: the fixture must reach the stalled shape by replaying the
    history that produced it, not by depending on today's executor still having the defect.
    The database this produces is byte-identical either way -- `create_validation_results` was
    invisible to the real run for exactly this reason.
    """
    return tuple(
        migration
        for migration in MIGRATIONS
        if migration.version != CREATE_VALIDATION_RESULTS_VERSION
    )


def build_stalled_database(path: Path, *, clock: Callable[[], datetime]) -> None:
    """Reproduce the user's real `state.sqlite3`: version 4, one name recorded twice, no
    `validation_results` table, and a chain that never advances again.

    Three stages, in the order that actually happened:

    1. A pre-migration ("v1-shaped") install, whose tables were created by their owning
       stores' constructors and which has no `schema_migrations` at all.
    2. `1e54104`'s registry stamps `baseline` at 1 and `demo_add_runs_archived_at` at 2.
    3. `6eba39c`'s registry -- as a database already at version 2 sees it -- applies
       `demo_add_runs_archived_at` a second time at its new number 3 (a silent no-op: the
       column it adds is already there) and `create_query_path_indexes` at 4, then stops at
       `rewrite_contract_identities`, whose `require_table("validation_results")` can never
       be satisfied because the migration that creates that table is below the watermark.
    """
    _build_v1_shaped_tables(path)
    run_migrations(path, clock=clock, migrations=_pre_reorder_registry())
    run_migrations(path, clock=clock, migrations=_post_reorder_registry_as_a_v2_database_sees_it())


def _audit_rows(path: Path) -> list[tuple[int, str]]:
    connection = sqlite3.connect(path)
    try:
        return [
            (row[0], row[1])
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
        ]
    finally:
        connection.close()


def _seed_one_gap(path: Path, *, user_version: int, setup: str = "") -> None:
    """A database at `user_version` whose audit trail records `baseline` and nothing else.

    The synthetic registries below pair `baseline` with one migration under test, and without
    this the *baseline* is a gap too -- `PRAGMA user_version` past it, no audit row -- so the
    reconciler would resolve two things and every assertion about "the repair" would be about
    two repairs. Recording it here leaves exactly one gap, which is what each test is naming.
    (Found by watching these tests fail: the first draft asserted one repair and got two.)
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (BASELINE_VERSION, "baseline", "2026-08-07T12:33:46+00:00"),
        )
        if setup:
            connection.execute(setup)
        connection.execute(f"PRAGMA user_version = {user_version:d}")
        connection.commit()
    finally:
        connection.close()


def _table_exists(path: Path, name: str) -> bool:
    connection = sqlite3.connect(path)
    try:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def test_the_fixture_reproduces_the_shape_measured_on_the_real_database(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Fidelity check, so nothing below is a test of an invented shape.

    Measured on a **copy** of the user's own `runtime/state.sqlite3` (the file itself is never
    opened for writing): `user_version = 4`; `schema_migrations` holding
    `[(1, 'baseline'), (2, 'demo_add_runs_archived_at'), (3, 'demo_add_runs_archived_at'),
    (4, 'create_query_path_indexes')]`; `validation_results` absent. Every one of those four
    facts is reproduced here from the two registry generations alone.

    Note what the duplicate is and is not. `schema_migrations.version` is a PRIMARY KEY and it
    held: no version is recorded twice. What is recorded twice is a *name*, at two different
    versions, because the same migration really was applied twice under two numbers. The table
    constrains exactly what it claims to.
    """
    path = tmp_path / "state.sqlite3"

    build_stalled_database(path, clock=migration_clock)

    assert _audit_rows(path) == [
        (BASELINE_VERSION, "baseline"),
        (CREATE_VALIDATION_RESULTS_VERSION, "demo_add_runs_archived_at"),
        (DEMO_ADD_RUNS_ARCHIVED_AT_VERSION, "demo_add_runs_archived_at"),
        (CREATE_QUERY_PATH_INDEXES_VERSION, "create_query_path_indexes"),
    ]
    assert not _table_exists(path, "validation_results")
    status = read_status(path)
    assert status.current_version == CREATE_QUERY_PATH_INDEXES_VERSION
    assert [migration.version for migration in status.pending] == [
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]


def test_a_renumbered_database_reaches_head_instead_of_stalling_forever(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The defect, stated as the outcome an operator cares about.

    Before the repair this asserts on a database that is permanently frozen: `run_migrations`
    computes `pending` from `user_version` alone, so `create_validation_results` (version 2,
    below the version-4 watermark) is never reconsidered, `validation_results` is never
    created, and `rewrite_contract_identities` defers on every call for the rest of the
    installation's life. Restarting does not help, which is why this loops three times.
    """
    path = tmp_path / "state.sqlite3"
    build_stalled_database(path, clock=migration_clock)

    for _ in range(3):
        run_migrations(path, clock=migration_clock)

    assert _table_exists(path, "validation_results")
    assert read_status(path).current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION


def test_starting_the_api_against_the_stalled_database_heals_it_and_serves_validations(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The path the user is actually on: they start the app, they do not run `migrate run`.

    This is what made the defect so hard to see. `create_app()` against the stalled database
    **succeeded** -- it just silently never advanced, and the one store whose table was missing
    is the only one no constructor creates, so `GET /backtests/validations/by-decision/{id}`
    raised `sqlite3.OperationalError: no such table: validation_results` out of the handler
    (a 500 to a real client) forever, while every other route kept working. Measured: with
    reconciliation disabled this test dies on exactly that error, so the endpoint -- not just
    the schema version -- is what separates the two answers.
    """
    from fastapi.testclient import TestClient

    from openalpha_cn.api.app import create_app

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    path = runtime_dir / "state.sqlite3"
    build_stalled_database(path, clock=migration_clock)

    client = TestClient(create_app(runtime_dir=runtime_dir))
    response = client.get("/api/v1/backtests/validations/by-decision/decision_absent")

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert read_status(path).current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert [repair.name for repair in read_status(path).repairs] == ["create_validation_results"]


def test_the_repair_is_recorded_as_applied_and_only_once(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """One repair row, saying `applied`, for the one migration whose effect was really missing.

    `applied` and not `verified` is the load-bearing half: `validation_results` genuinely did
    not exist on this database, so the repair really did run DDL. A `verified` here would mean
    the predicate had answered "already present" about a table that was not there, and the
    chain would still be stuck -- the same wrong answer, differently spelled.

    Exactly one row, after three runs, is the idempotence claim: the repair ledger is what
    `_unrecorded` consults to stop reconsidering a name, so a growing count would mean the
    repair re-runs on every process start -- the shape `V2-P4-111` spent a row removing.
    """
    path = tmp_path / "state.sqlite3"
    build_stalled_database(path, clock=migration_clock)

    first = run_migrations(path, clock=migration_clock)
    for _ in range(2):
        run_migrations(path, clock=migration_clock)

    assert [(repair.version, repair.name, repair.resolution) for repair in first.repairs] == [
        (CREATE_VALIDATION_RESULTS_VERSION, "create_validation_results", REPAIR_APPLIED)
    ]
    status = read_status(path)
    assert [(repair.version, repair.resolution) for repair in status.repairs] == [
        (CREATE_VALIDATION_RESULTS_VERSION, REPAIR_APPLIED)
    ]
    assert status.unrecorded == ()


def test_a_skipped_migration_whose_effect_is_already_present_is_verified_not_rerun(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The assertion that separates "inspects the schema" from "re-runs anything unrecorded".

    Both designs turn `MigrationStatus.unrecorded` empty and both let the chain advance, so
    neither the version counter nor the audit trail can tell them apart -- which is the trap
    this test exists to avoid falling into. What tells them apart is whether `apply()` ran, so
    the migration here counts its own invocations while its `effect_present` predicate reports
    the effect already present. A reconciler that trusted the audit trail's silence would
    re-run it and the counter would read 1.

    This matters far beyond bookkeeping: `rewrite_contract_identities` re-run against an
    already-rewritten database would recompute content addresses from payloads that have
    already moved once. "Re-run whatever the trail does not name" is the cheap implementation
    of this feature and it is the one that would corrupt a database.
    """
    calls: list[int] = []

    def _apply(connection: sqlite3.Connection) -> None:
        calls.append(1)
        connection.execute("CREATE TABLE IF NOT EXISTS already_there (id INTEGER PRIMARY KEY)")

    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(
            version=2,
            name="already_applied_under_another_number",
            apply=_apply,
            effect_present=lambda connection: (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'already_there'"
                ).fetchone()
                is not None
            ),
        ),
    )
    path = tmp_path / "state.sqlite3"
    # The effect is present but nothing recorded it, and the counter is already past it --
    # the exact three-fact state the real database is in, minus the missing effect.
    _seed_one_gap(
        path,
        user_version=2,
        setup="CREATE TABLE already_there (id INTEGER PRIMARY KEY)",
    )

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert calls == []
    assert [(repair.name, repair.resolution) for repair in result.repairs] == [
        ("already_applied_under_another_number", REPAIR_VERIFIED)
    ]


def test_a_skipped_data_rewrite_is_reported_rather_than_guessed_at(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """A migration with no `effect_present` is never repaired, and never silently ignored.

    The two ways to "resolve" a skipped data rewrite are to re-run it (may corrupt: identity
    rewrites are not idempotent) and to record it as done without looking (invents history).
    Both are worse than the stall, so `_reconcile` does neither and stops; `read_status`
    carries it out to the operator through `unrecorded` instead, which is what
    `openalpha migrate status` prints.
    """
    ran: list[int] = []

    def _apply(connection: sqlite3.Connection) -> None:
        del connection
        ran.append(1)

    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(version=2, name="opaque_data_rewrite", apply=_apply),
    )
    path = tmp_path / "state.sqlite3"
    _seed_one_gap(path, user_version=2)

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert ran == []
    assert result.repairs == ()
    assert [migration.name for migration in read_status(path, migrations=registry).unrecorded] == [
        "opaque_data_rewrite"
    ]


def test_a_run_that_only_reports_an_undecidable_gap_leaves_no_backup_behind(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """`V2-P4-111`'s rule extended to the new pass, so the repair cannot revive the pile.

    A gap the reconciler refuses to guess at is present on *every* process start, exactly like
    the permanently-deferring migration that wrote 125 identical backups. The backup still has
    to be taken before any write (a migration announces an unmet precondition from inside
    `apply()`), so the guard is the same one: a call that applied nothing and repaired nothing
    removes its own copy.
    """
    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(version=2, name="opaque_data_rewrite", apply=lambda connection: None),
    )
    path = tmp_path / "state.sqlite3"
    _seed_one_gap(path, user_version=2)

    for _ in range(3):
        result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert result.backup_path is None
    assert sorted((tmp_path / "backups").glob("*.bak")) == []


def test_the_repair_never_destroys_a_row_of_the_existing_audit_trail(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Whatever reconciles the two records must not do it by deleting the disagreement.

    The row `(2, 'demo_add_runs_archived_at')` is a true statement about what happened -- that
    migration really did run, at that number, at that time. It is stale only relative to
    today's registry. Rewriting or dropping it would trade a stall for a falsified history,
    and it is also structurally impossible to "correct" it in place: `version` is the PRIMARY
    KEY, so version 2 cannot simultaneously name `create_validation_results`. That constraint
    is why the repair is recorded beside the trail rather than inside it.
    """
    path = tmp_path / "state.sqlite3"
    build_stalled_database(path, clock=migration_clock)
    before = _audit_rows(path)

    for _ in range(3):
        run_migrations(path, clock=migration_clock)

    after = _audit_rows(path)
    assert after[: len(before)] == before


def test_a_fresh_database_is_unchanged_by_the_repair_path(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The measured fresh-database behaviour, held byte-for-byte.

    Nothing is ever skipped on a database that starts at version 0, so the reconciliation pass
    must find no work and change no observable outcome: first call lands on
    `create_validation_results` with `baseline` beside it, the second reaches head once the
    stores have created their tables, the third is a stable no-op.
    """
    from openalpha_cn.api.app import create_app

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    path = runtime_dir / "state.sqlite3"

    create_app(runtime_dir=runtime_dir)
    first = read_status(path)
    assert first.current_version == CREATE_VALIDATION_RESULTS_VERSION
    assert [item.name for item in first.applied] == ["baseline", "create_validation_results"]

    create_app(runtime_dir=runtime_dir)
    second = read_status(path)
    assert second.current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert second.pending == ()

    create_app(runtime_dir=runtime_dir)
    third = read_status(path)
    assert third.current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert [item.version for item in third.applied] == [
        migration.version for migration in MIGRATIONS
    ]


# --- structural guard: the cause, not the symptom ---------------------------------------


SHIPPED_VERSION_NAMES: Final[Mapping[int, str]] = MappingProxyType(
    {
        1: "baseline",
        2: "create_validation_results",
        3: "demo_add_runs_archived_at",
        4: "create_query_path_indexes",
        5: "rewrite_contract_identities",
        6: "add_runs_mode_projection",
        7: "split_batch_task_items",
        8: "rewrite_manifest_component_planes",
    }
)
"""What each version number means in every database this project has ever written.

Frozen deliberately, and **append-only**: a new migration adds a line at the end, and no
existing line may ever change. Changing one is not a refactor -- it silently reassigns the
meaning of a number that real databases have already recorded, which is the entire defect
`V2-P5-026` repairs.
"""


def test_no_shipped_migration_may_be_renumbered_or_renamed() -> None:
    """The guard that would have caught the original defect at the commit that caused it.

    `test_migrations_registry_is_declared_in_strictly_increasing_version_order` already holds
    that versions are unique and sorted, and commit `6eba39c` satisfied it perfectly while
    inserting `create_validation_results` at version 2 and renumbering
    `demo_add_runs_archived_at` 2 -> 3. Uniqueness and order are properties of one registry
    snapshot; what a version number *means* is a property held **across releases**, by every
    database that recorded it, and nothing in this repository was checking it.

    So this is not a test of the repair -- it is the reason a second database will not need
    one. A contributor who must insert a migration into the middle of the chain now has to
    edit this map to do it, and reads why that is the wrong move while doing so. Appending is
    the only edit that keeps this green, and appending is exactly the operation that is safe.
    """
    assert {migration.version: migration.name for migration in MIGRATIONS} == dict(
        SHIPPED_VERSION_NAMES
    )


def test_every_registry_migration_either_declares_a_predicate_or_is_a_data_rewrite() -> None:
    """Nobody may add a migration without deciding whether its effect is inspectable.

    `effect_present=None` is a real, correct answer -- for a migration whose effect is payload
    bytes -- but it must be a *decision*, not the default that got left in place. A schema-
    shaped migration that silently inherits `None` is one that can never be repaired if it is
    ever skipped, which is precisely the position `create_validation_results` was in.

    The set below is the hand-maintained half, so this test names what moved rather than
    quietly widening. Every migration outside it must carry a predicate.
    """
    data_rewrites = {
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    }
    missing = {
        migration.name: migration.version
        for migration in MIGRATIONS
        if migration.effect_present is None and migration.version not in data_rewrites
    }
    assert not missing, (
        "migration(s) declare no `effect_present` predicate and are not listed as data "
        f"rewrites; give them one or say why they cannot have one: {missing}"
    )
    unnecessary = {
        migration.name: migration.version
        for migration in MIGRATIONS
        if migration.effect_present is not None and migration.version in data_rewrites
    }
    assert not unnecessary, (
        f"migration(s) listed as data rewrites now declare a predicate; update the set: "
        f"{unnecessary}"
    )


def test_every_predicate_answers_false_on_a_genuinely_empty_database() -> None:
    """A predicate that cannot say "absent" cannot drive a repair.

    The failure mode this catches is a predicate written to inspect the wrong thing -- a table
    some *store constructor* also creates, say -- which answers `True` everywhere and turns
    every repair into a no-op `verified`. Against a database with nothing in it at all, the
    only correct answer for a migration that makes something is `False`.

    `baseline` is exempt and is the one honest exception: it creates nothing, so its effect is
    the empty set and is present in every database including this one.
    """
    baseline = _registry_entry(BASELINE_VERSION)
    assert baseline.effect_present is not None
    connection = sqlite3.connect(":memory:")
    try:
        # The exemption is a positive claim, not a hole: baseline's effect is the empty set,
        # so `True` here is the right answer and `False` would make every database with an
        # unrecorded baseline re-run and re-record a migration that does nothing.
        assert baseline.effect_present(connection) is True
    finally:
        connection.close()

    for migration in MIGRATIONS:
        if migration.effect_present is None or migration.version == BASELINE_VERSION:
            continue
        connection = sqlite3.connect(":memory:")
        try:
            assert migration.effect_present(connection) is False, (
                f"{migration.name}'s effect_present answered True against a completely empty "
                "database, so it can never report a missing effect"
            )
        finally:
            connection.close()


def test_a_generated_column_counts_as_present_even_though_table_info_hides_it(
    tmp_path: Path,
) -> None:
    """`runs.mode` is `GENERATED ALWAYS AS ... VIRTUAL` and `PRAGMA table_info` does not list it.

    Written against `table_info`, `_runs_mode_projection_effect_present` answers "absent" on
    every database that has the column, and the repair then re-runs DDL and records
    `applied` about something that was already there. The predicate would still be *inspecting
    the schema* and would still be wrong, which is the failure mode this test exists for: a
    predicate is only worth having if it can return `True`.

    `SQLiteRunRepository`'s constructor establishes the projection, so this asserts on the
    shape a real installation actually has rather than on hand-written DDL.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_tables(path)
    migration = _registry_entry(ADD_RUNS_MODE_PROJECTION_VERSION)
    assert migration.effect_present is not None

    connection = sqlite3.connect(path)
    try:
        assert "mode" not in [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
        assert migration.effect_present(connection) is True
    finally:
        connection.close()


def test_a_projection_missing_only_its_index_reports_its_effect_absent(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Column present, index dropped: the effect is half there, which is not there.

    `ensure_runs_mode_projection` establishes a column *and* an index, and Finding F70 was
    about the scan the index removes -- a database holding only the column still pays it. A
    predicate joining the two with `or` would call this present, record `verified`, and leave
    the index missing forever, because a repaired name is never reconsidered.

    The three repairs *before* the one under test arrived with `V2-P5-029` and are correct.
    This fixture writes `schema_migrations` rows for every migration except the target without
    applying any of them, over `_build_v1_shaped_tables`' v1 schema -- so the trail claims
    `create_validation_results`, `demo_add_runs_archived_at` and `create_query_path_indexes`
    ran while the schema shows they did not. That is exactly the damage class reconciliation
    now inspects for, and before `V2-P5-029` it was invisible only because a recorded name was
    never asked about. `rewrite_contract_identities` (version 5) is recorded here too and is
    absent from the list on purpose: it carries no predicate, so it can neither be confirmed
    nor called damaged.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_tables(path)
    registry = MIGRATIONS[:ADD_RUNS_MODE_PROJECTION_VERSION]
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP INDEX runs_mode_run_id_idx")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            [
                (migration.version, migration.name, "2026-08-08T00:00:00+00:00")
                for migration in registry
                if migration.version != ADD_RUNS_MODE_PROJECTION_VERSION
            ],
        )
        connection.execute(f"PRAGMA user_version = {ADD_RUNS_MODE_PROJECTION_VERSION:d}")
        connection.commit()
    finally:
        connection.close()

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert [(repair.name, repair.resolution) for repair in result.repairs] == [
        ("create_validation_results", REPAIR_APPLIED),
        ("demo_add_runs_archived_at", REPAIR_APPLIED),
        ("create_query_path_indexes", REPAIR_APPLIED),
        ("add_runs_mode_projection", REPAIR_APPLIED),
    ]
    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("runs_mode_run_id_idx",),
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def test_a_partly_indexed_query_path_migration_reports_its_effect_absent(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Two of three indexes present is not "applied" -- it is one index short, forever.

    `create_query_path_indexes` is one unit with no per-index granularity, exactly as its
    `require_table` triple already says. A predicate joining its three checks with `any` would
    report the effect present, record `verified`, and strand
    `research_reports_subject_idx` -- the full scan Finding F69 named -- with nothing left to
    reconsider it.

    The two repairs before it arrived with `V2-P5-029`, for the reason given at length in
    `test_a_projection_missing_only_its_index_reports_its_effect_absent`: this fixture records
    migrations it never applies, which is the damage class reconciliation now inspects for.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_tables(path)
    registry = MIGRATIONS[:CREATE_QUERY_PATH_INDEXES_VERSION]
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE INDEX checkpoints_run_id_idx ON checkpoints(run_id)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            [
                (migration.version, migration.name, "2026-08-08T00:00:00+00:00")
                for migration in registry
                if migration.version != CREATE_QUERY_PATH_INDEXES_VERSION
            ],
        )
        connection.execute(f"PRAGMA user_version = {CREATE_QUERY_PATH_INDEXES_VERSION:d}")
        connection.commit()
    finally:
        connection.close()

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert [(repair.name, repair.resolution) for repair in result.repairs] == [
        ("create_validation_results", REPAIR_APPLIED),
        ("demo_add_runs_archived_at", REPAIR_APPLIED),
        ("create_query_path_indexes", REPAIR_APPLIED),
    ]
    connection = sqlite3.connect(path)
    try:
        present = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    finally:
        connection.close()
    assert {
        "checkpoints_run_id_idx",
        "portfolio_transitions_subject_idx",
        "research_reports_subject_idx",
    } <= present


def test_an_undecidable_gap_stops_the_repair_of_every_gap_after_it(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Stop-not-skip, for the same reason the pending loop stops rather than skipping.

    Gaps are a chain, not an unordered set: a later one may assume the earlier one ran. Once
    reconciliation reaches a version it cannot settle, everything after it is equally
    unsettled, and repairing the later one anyway would apply a migration out of order against
    a database whose earlier state is unknown. Both are reported instead.
    """
    ran: list[str] = []

    def _later(connection: sqlite3.Connection) -> None:
        ran.append("later")
        connection.execute("CREATE TABLE IF NOT EXISTS later_effect (id INTEGER PRIMARY KEY)")

    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(version=2, name="opaque_data_rewrite", apply=lambda connection: None),
        Migration(
            version=3,
            name="later_but_inspectable",
            apply=_later,
            effect_present=lambda connection: (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'later_effect'"
                ).fetchone()
                is not None
            ),
        ),
    )
    path = tmp_path / "state.sqlite3"
    _seed_one_gap(path, user_version=3)

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert ran == []
    assert result.repairs == ()
    assert [migration.name for migration in read_status(path, migrations=registry).unrecorded] == [
        "opaque_data_rewrite",
        "later_but_inspectable",
    ]


def test_a_repair_only_run_keeps_the_backup_it_took(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """A repair writes DDL, so its pre-repair copy is the only one that predates the write.

    `V2-P4-111`'s removal rule is "a call that changed nothing removes the copy it took", and
    the proof it leans on is that the copy is byte-for-byte the database beside it. A repair
    breaks that premise: after it, the backup is the only remaining picture of the database as
    it was, which is precisely what an operator would want if the repair turned out to be the
    wrong call. Deleting it would be the same mistake as an automatic retention cap.
    """
    path = tmp_path / "state.sqlite3"
    _seed_one_gap(path, user_version=2)
    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(
            version=2,
            name="creates_its_own_table",
            apply=lambda connection: connection.execute("CREATE TABLE repaired (id INTEGER)"),
            effect_present=lambda connection: (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repaired'"
                ).fetchone()
                is not None
            ),
        ),
    )

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert [repair.resolution for repair in result.repairs] == [REPAIR_APPLIED]
    assert result.applied == ()  # nothing was pending: the repair is the only work
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_losing_the_race_to_repair_is_not_reported_as_a_failure(
    tmp_path: Path, migration_clock: Callable[[], datetime], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two processes against one `runtime_dir` both decide the same migration needs repairing.

    `_unrecorded` is a snapshot taken before any lock is held, so both callers see the gap;
    `BEGIN IMMEDIATE` then serialises them and the loser wakes up to a database the winner has
    already fixed. Without the in-transaction re-read the loser evaluates the predicate against
    the winner's finished work (now `True`), records `verified`, and collides on
    `schema_repairs`' `(version, name)` primary key -- a `sqlite3.IntegrityError` surfaced as
    `MigrationFailedError` about a database that is perfectly healthy. That is the same false
    failure `run_migrations`' pending loop already guards against, arriving through the other
    ledger.

    The winner is injected in the one window where the race is actually live: **after
    `_reconcile`'s own `_unrecorded()` call returns and before this connection reaches
    `BEGIN IMMEDIATE`**. An earlier injection point proves nothing -- the first draft of this
    test committed the winner inside `_take_backup`, and `_reconcile`'s later `_unrecorded()`
    then simply filtered the repaired name out, so the guard never ran and removing it left
    this test green. Deterministic either way; only this window separates the two answers.
    """
    path = tmp_path / "state.sqlite3"
    _seed_one_gap(path, user_version=2)
    registry = (
        _registry_entry(BASELINE_VERSION),
        Migration(
            version=2,
            name="raced_repair",
            apply=lambda connection: connection.execute("CREATE TABLE raced (id INTEGER)"),
            effect_present=lambda connection: (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'raced'"
                ).fetchone()
                is not None
            ),
        ),
    )

    real_unrecorded = migrations_module._unrecorded
    calls = 0

    def _winner_commits_after_the_snapshot(
        connection: sqlite3.Connection, migrations: object
    ) -> tuple[Migration, ...]:
        nonlocal calls
        snapshot: tuple[Migration, ...] = real_unrecorded(connection, migrations)  # type: ignore[arg-type]
        calls += 1
        # Call 1 is `run_migrations`' own; call 2 is `_reconcile`'s, and the window this test
        # is about opens the instant it returns.
        if calls == 2 and snapshot:
            winner = sqlite3.connect(path)
            try:
                winner.execute("CREATE TABLE raced (id INTEGER)")
                winner.execute(migrations_module._SCHEMA_REPAIRS_DDL)
                winner.execute(
                    "INSERT INTO schema_repairs (version, name, resolution, repaired_at) "
                    "VALUES (?, ?, ?, ?)",
                    (2, "raced_repair", REPAIR_APPLIED, "2026-08-25T00:00:00+00:00"),
                )
                winner.commit()
            finally:
                winner.close()
        return snapshot

    monkeypatch.setattr(migrations_module, "_unrecorded", _winner_commits_after_the_snapshot)

    result = run_migrations(path, clock=migration_clock, migrations=registry)

    assert result.repairs == ()  # the winner did it; this call correctly did nothing
    assert [
        (repair.version, repair.resolution)
        for repair in read_status(path, migrations=registry).repairs
    ] == [(2, REPAIR_APPLIED)]
    assert read_status(path, migrations=registry).unrecorded == ()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
