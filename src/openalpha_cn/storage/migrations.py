"""Forward-only SQLite schema-migration engine for the shared `state.sqlite3` (V2-P0B-004).

Why this exists: the nine tables spread across the seven SQLite stores in this package are
each created by their own store's `CREATE TABLE IF NOT EXISTS` -- table creation is a side
effect of constructing a store, not a tracked, versioned event. Rows are opaque JSON payloads
validated by pydantic models with `extra="forbid"` and a literal `schema_version` -- any new
field makes old rows unreadable to new code and new rows unreadable to old code, with no code
path able to tell the two apart. `V2-P4-001` then made three breaking contract changes at once;
without an explicit, auditable, reversible migration mechanism, that would have silently
destroyed every research record a user had accumulated. `rewrite_contract_identities` (version
5, below) is that change, and it is the one migration here that rewrites *identities* rather
than shape: see its docstring for why a read-time upcast could not have done the job.

Design, in one paragraph: `PRAGMA user_version` is the cheap, atomic source of truth for "what
schema version is this database at"; a `schema_migrations` table is a human-readable audit trail
recorded alongside it (version, name, applied_at) -- both are required by the brief because
`user_version` alone can't answer "when was this applied" or "what was it called" without
decoding a version number back to a name. Migrations are a plain, hand-ordered tuple
(`MIGRATIONS`) -- no directory scanning, no plugin discovery, no migration framework: this
repository's runtime dependency set is deliberately seven packages, and a dozen-line executor
over a hardcoded, version-sorted tuple needs none of alembic's machinery. Each migration is one
function `apply(connection) -> None` that runs arbitrary DDL/DML against a connection already
inside an explicit transaction; the executor sorts by `.version` (not by declaration order, so a
future contributor who appends out of order still gets correct ordering), applies only versions
strictly greater than the current `user_version`, and commits the DDL, the audit row, and the
`PRAGMA user_version` bump together, atomically, per migration.

The one subtlety worth documenting: this engine's mount point is `runtime/composition.py`'s
`build_storage()`, which must run migrations *before constructing any store* (per the brief) --
so on a brand-new install, migrations run against a database that has zero tables, because the
stores that would create them haven't been constructed yet. A migration that alters a
pre-existing table (like this module's demo migration) has nothing to alter yet in that case.
Rather than crash a fresh install, such a migration raises `MigrationNotYetApplicable`: the
executor stops (does not advance past it, does not record it as applied) and leaves it pending
for the next invocation, by which point the owning store has created its table. This is not
speculative -- `test_new_database_lands_on_baseline_and_defers_the_demo_migration` and
`test_build_storage_catches_up_the_demo_migration_on_a_second_call` exercise exactly this path.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openalpha_cn.batch_contracts import BATCH_RESEARCH_TASK_VERSIONS, BatchResearchTask
from openalpha_cn.domain.decision import DECISION_LEDGER_VERSIONS, DecisionLedger, DecisionLedgerV1
from openalpha_cn.domain.horizon import is_countable_horizon
from openalpha_cn.domain.memory import MEMORY_ENTRY_VERSIONS, MemoryEntry
from openalpha_cn.domain.report import RESEARCH_REPORT_VERSIONS, ResearchReport
from openalpha_cn.domain.run import (
    RUN_MANIFEST_VERSIONS,
    RunManifest,
    RunManifestV2,
    upgrade_run_manifest_v2,
)
from openalpha_cn.domain.run_mode import RUN_MODES
from openalpha_cn.domain.validation import (
    VALIDATION_RESULT_VERSIONS,
    ValidationResult,
    ValidationResultV1,
)
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.batch import (
    BATCH_TASK_ITEMS_DDL,
    load_task_row,
    split_task_payload,
    store_task_row,
)
from openalpha_cn.storage.sqlite import (
    RUNS_MODE_COLUMN,
    RUNS_MODE_PAYLOAD_PATH,
    RUNS_TABLE,
    ensure_runs_mode_projection,
)

logger = logging.getLogger(__name__)

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationNotYetApplicable(RuntimeError):
    """Raised by a migration's `apply()` when its precondition isn't met yet.

    Not a failure: the executor treats this as "nothing to roll back, try again later" --
    it stops applying further migrations, but does not touch `user_version` or record
    anything, because nothing was attempted. The canonical case is a migration that alters
    a table an existing store owns, running against a brand-new database where that store
    has not yet created the table (migrations run before any store is constructed).
    """


class MigrationFailedError(RuntimeError):
    """Raised after a migration's transaction has already been rolled back.

    Carries the failed migration's identity and the pre-migration backup path so a caller
    (the CLI, in particular) can tell the user exactly where to find their data.

    `backup_path` is `None` only when the backup itself is what failed -- `V2-P4-111` moved it
    inside the same `try` as the first `apply()`, so a disk that cannot be written to now reaches
    a caller as this refusal naming the migration rather than as a bare `sqlite3.OperationalError`
    from before the loop. Nothing was migrated in that case either, so the honest answer is that
    there is no copy to point at rather than a path that does not exist.
    """

    def __init__(self, message: str, *, version: int, name: str, backup_path: Path | None) -> None:
        super().__init__(message)
        self.version = version
        self.name = name
        self.backup_path = backup_path


@dataclass(frozen=True)
class Migration:
    """One forward-only, idempotently-applied schema change."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class AppliedMigration:
    """One row of the `schema_migrations` audit trail."""

    version: int
    name: str
    applied_at: str


@dataclass(frozen=True)
class MigrationStatus:
    """A read-only snapshot of a database's migration state."""

    path: Path
    current_version: int
    applied: tuple[AppliedMigration, ...]
    pending: tuple[Migration, ...]


@dataclass(frozen=True)
class MigrationRunResult:
    """The outcome of one `run_migrations()` call."""

    path: Path
    from_version: int
    to_version: int
    applied: tuple[AppliedMigration, ...]
    backup_path: Path | None


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def require_table(connection: sqlite3.Connection, name: str) -> None:
    """Raise `MigrationNotYetApplicable` unless table `name` already exists.

    Reusable precondition guard for any migration that alters a table owned by one of
    this package's stores. Migrations run before any store is constructed (see module
    docstring), so on a fresh install the owning table does not exist yet; a migration
    that assumes otherwise and runs a bare `ALTER TABLE`/`UPDATE`/etc. against it crashes
    every fresh install with a bare `sqlite3.OperationalError`, wrapped by the executor
    into `MigrationFailedError` and propagated out of `build_storage()` -- i.e. it takes
    down `OpenAlphaSDK.__init__` and `create_app()` for every new installation, not just
    this one migration. Call this as the first line of `apply()` instead of hand-rolling
    the same `_table_exists` check --
    `test_every_non_baseline_migration_defers_gracefully_on_a_fresh_empty_database`
    (tests/integration/storage/test_migrations.py) enforces that every non-baseline
    migration in `MIGRATIONS` actually does this.
    """
    if not _table_exists(connection, name):
        raise MigrationNotYetApplicable(f"{name} table does not exist yet")


def _baseline_apply(connection: sqlite3.Connection) -> None:
    """Stamp an unversioned database at the baseline version. Touches no data.

    The nine pre-migration tables are (and stay) created by their owning stores'
    `CREATE TABLE IF NOT EXISTS` statements, not by this migration -- see the module
    docstring. Whether `connection` already holds a populated v1-shaped database or is
    completely empty, there is nothing for this migration to do beyond being recorded:
    both land on `BASELINE_VERSION`.
    """


def _create_validation_results_table(connection: sqlite3.Connection) -> None:
    """Create `validation_results` (V2-P0B-010): the durable outcome-validation ledger.

    Unlike `_demo_add_runs_archived_at`, this migration does not alter a table some other
    store's constructor also creates as a side effect -- it creates a table nothing else
    in this package creates (`storage/validation.py#SQLiteValidationStore` deliberately
    does not, per this task's brief), so there is no pre-existing owner whose absence,
    on a brand-new database, could block it. It is precondition-free by construction,
    for the same reason `_baseline_apply` is: it has nothing to require. `require_table`
    is therefore correctly unused here, and this migration is exempted (alongside
    `BASELINE_VERSION`) from
    `test_every_non_baseline_migration_defers_gracefully_on_a_fresh_empty_database`'s
    loop for that exact reason -- see that test's docstring.

    Ordering matters, not just precondition-freedom: this migration is registered at
    `CREATE_VALIDATION_RESULTS_VERSION` (2), *before* `DEMO_ADD_RUNS_ARCHIVED_AT_VERSION`
    (bumped from 2 to 3 to make room). The executor applies pending migrations in strictly
    increasing version order and stops -- does not skip ahead -- the moment one raises
    `MigrationNotYetApplicable` (see `run_migrations`'s docstring). Had this migration been
    appended *after* the demo migration instead, it would sit behind the demo migration's
    routine first-call deferral on every brand-new install (the demo migration always
    defers there: `runs` is created by `SQLiteRunRepository`'s constructor, which
    `build_storage()` does not invoke until after `run_migrations()` returns) and would
    never even be attempted in that first `run_migrations()` call -- silently leaving
    `validation_results` missing for the entire lifetime of that process, since nothing
    else ever creates it. Ordered first, it always applies in the very same call that
    stamps the baseline, so a fresh install can validate an outcome immediately, with no
    second `build_storage()` call required. Two indexes are created alongside the table,
    not added later: `decision_id` and `signal_id` are this store's only query paths
    (`SQLiteValidationStore.list_by_decision` / `list_by_signal`), and this project already
    has three tables that skipped an index on their query column and pay for it with a
    full scan (`checkpoints.run_id`, `portfolio_transitions.subject`,
    `research_reports.subject` -- Finding F69); this table does not become a fourth.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_results (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_id TEXT UNIQUE NOT NULL,
            decision_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS validation_results_decision_id_idx
        ON validation_results(decision_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS validation_results_signal_id_idx
        ON validation_results(signal_id)
        """
    )


def _create_query_path_indexes(connection: sqlite3.Connection) -> None:
    """Add the three query-path indexes Finding F69 flagged as missing (V2-P0B-015 / task 21).

    `checkpoints.run_id` (`SQLiteRunRepository.list_checkpoints`),
    `portfolio_transitions.subject` (`SQLitePortfolioLedger.list`), and
    `research_reports.subject` (`SQLiteReportStore.list`) are each filtered on directly,
    with no matching index -- every call is a full table scan. `validation_results`
    avoided exactly this at creation time (see `_create_validation_results_table` above,
    `decision_id_idx`/`signal_id_idx`); these three tables predate the migration engine
    (V2-P0B-004) and never got the same treatment, because their tables are created as a
    `CREATE TABLE IF NOT EXISTS` constructor side effect, not through a migration -- see
    the module docstring.

    Unlike `_create_validation_results_table`, this migration cannot be precondition-free:
    it *alters* three tables this package's stores each own and create as that constructor
    side effect, so on a genuinely empty database none of them exist yet. All three
    `require_table` guards run before any `CREATE INDEX`, so a database that happens to
    have, say, `checkpoints` but not yet `portfolio_transitions` -- not reachable through
    `build_storage()`, which constructs all eight `state.sqlite3` stores together right
    after migrations run, but not ruled out for a hand-built or historical database --
    defers the whole migration rather than indexing two of the three tables and never
    getting a second chance at the third (`schema_migrations` has no per-index-within-a-
    migration granularity: once this version is recorded applied, it never runs again).

    Ordering: registered *after* `DEMO_ADD_RUNS_ARCHIVED_AT_VERSION`, not before it like
    `CREATE_VALIDATION_RESULTS_VERSION`. That ordering trick only pays off for a
    precondition-free migration racing the demo migration's routine deferral on a fresh
    install (see that migration's docstring); this migration is not precondition-free --
    none of `checkpoints`/`portfolio_transitions`/`research_reports` exist on a fresh
    database either, at the point migrations run, regardless of position -- so it defers
    on a fresh install no matter where in the registry it sits, and simple append-only
    growth is the honest placement.
    """
    require_table(connection, "checkpoints")
    require_table(connection, "portfolio_transitions")
    require_table(connection, "research_reports")
    connection.execute("CREATE INDEX IF NOT EXISTS checkpoints_run_id_idx ON checkpoints(run_id)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS portfolio_transitions_subject_idx "
        "ON portfolio_transitions(subject)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS research_reports_subject_idx ON research_reports(subject)"
    )


def _demo_add_runs_archived_at(connection: sqlite3.Connection) -> None:
    """Demonstration migration (V2-P0B-004's acceptance proof): add `runs.archived_at`.

    Proves the executor can make a real, additive schema change to a table that already
    holds real records, while leaving those records fully readable through
    `SQLiteRunRepository` afterwards -- its INSERT/SELECT statements name explicit
    columns, so an additive column is invisible to it. This migration is illustrative
    scaffolding for this task, not one of the three breaking changes `V2-P4-001` will make.

    Guarded twice: if `runs` doesn't exist yet, there is nothing to alter (see module
    docstring), so this raises `MigrationNotYetApplicable` via `require_table`. If
    `archived_at` is already present (this migration has already run once, or the table
    was created after this migration first shipped), it is a silent no-op rather than a
    duplicate-column error.
    """
    require_table(connection, "runs")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    if "archived_at" in columns:
        return
    connection.execute("ALTER TABLE runs ADD COLUMN archived_at TEXT")


class UnmigratableHorizonError(ValueError):
    """A stored signal states a horizon `V2-P4-001` no longer admits, and cannot be converted.

    `SignalFrame.horizon` narrowed from four units to the one with a session count (see
    `domain/horizon.py::COUNTABLE_HORIZON_PATTERN`). Every value that stayed legal serialises
    to the bytes it always did, so no `signal_id` moved and this migration has nothing to
    rewrite -- but a stored frame carrying `3m` is now outside the contract, and turning it
    into a session count would need the sessions-per-month constant this repository has
    deliberately never measured.

    So the migration refuses rather than inventing one, and it refuses *here* rather than
    letting the row surface later as a regex `ValidationError` from whichever store happened
    to read it first. The message names the runs, because the remedy is a judgement about
    those specific runs -- restate the horizon in trading days, or drop the recovery row --
    and not something a migration may decide.
    """


def _rows_if_present(
    connection: sqlite3.Connection, query: str, table: str
) -> Sequence[tuple[str, ...]]:
    """Run `query` if `table` exists, and return no rows if it does not.

    The identity rewrite's reference passes are bound to `runs`, `decisions` and
    `validation_results` (see `_rewrite_contract_identities`) and merely *tolerant* of the
    four optional tables it also updates. Tolerant rather than required, because through
    `build_storage()` all of them are created together immediately after migrations run, and
    a table that does not exist cannot hold a row referencing anything -- so skipping it
    strands nothing, whereas requiring it would defer the whole rewrite forever against any
    database (a test fixture, a hand-built file) that legitimately has only some stores.
    """
    if not _table_exists(connection, table):
        return ()
    rows: list[tuple[str, ...]] = connection.execute(query).fetchall()
    return rows


_UNCOUNTABLE_HORIZON_REMEDY = (
    "Restate each of those signals with a horizon in trading days (the only unit with a "
    "session count -- see domain/horizon.py), or delete the run_recovery rows for those "
    "runs: they are operational recovery state, not research records. This migration will "
    "not convert a calendar horizon into sessions, because the number of sessions in a "
    "month is not a constant this repository has measured."
)


def _refuse_uncountable_stored_horizons(connection: sqlite3.Connection) -> None:
    """Refuse the whole rewrite if any stored signal carries a now-inadmissible horizon.

    The recovery plane is the only place in this database a whole `SignalFrame` is stored;
    everywhere else a signal appears as an ID. Read as raw JSON rather than through
    `read_versioned`, deliberately: validating the row is exactly what would fail, and the
    point of this pass is to produce a message about horizons instead of one about a regex.

    **Two tables since `V2-P4-020`, and both are read here.** That issue moved a run's
    completed results out of `run_recovery.payload` and into one row per agent slot in
    `run_recovery_results`, so a database written after it holds no `completed_results` key
    at all -- and a pass that kept looking only in the payload would have gone on reporting
    "no offenders" while every signal it exists to inspect sat one table over. Rows written
    before the split still carry theirs inline and are still read from there, which is why
    this looks in both places rather than switching from one to the other.
    """
    offenders: dict[str, set[str]] = {}

    def _charge(run_id: object, result: object) -> None:
        if not isinstance(result, dict):
            return
        horizon = (result.get("signal") or {}).get("horizon")
        if horizon is not None and not is_countable_horizon(horizon):
            offenders.setdefault(str(run_id), set()).add(str(horizon))

    if _table_exists(connection, "run_recovery"):
        for run_id, payload in connection.execute("SELECT run_id, payload FROM run_recovery"):
            document = json.loads(payload)
            if not isinstance(document, dict):
                continue
            for result in document.get("completed_results") or ():
                _charge(run_id, result)
    if _table_exists(connection, "run_recovery_results"):
        rows = connection.execute(
            "SELECT run_id, payload FROM run_recovery_results WHERE payload IS NOT NULL"
        )
        for run_id, payload in rows:
            _charge(run_id, json.loads(payload))
    if offenders:
        listed = "; ".join(
            f"{run_id}: {sorted(values)}" for run_id, values in sorted(offenders.items())
        )
        raise UnmigratableHorizonError(
            f"stored signals carry horizons SignalFrame no longer admits ({listed}). "
            f"{_UNCOUNTABLE_HORIZON_REMEDY}"
        )


def _stored_run_manifest(payload: str | bytes) -> RunManifest:
    """Bring one stored `runs` payload to the current contract version, whatever it was at.

    `read_versioned` cannot be the whole answer any more. `V2-P4-010` registers a **refusing**
    upgrade for `run-manifest/v2` -- see `domain/run.py::refuse_run_manifest_v2_upgrade`, whose
    point is that advancing a v2 manifest on read hands back a manifest whose address no stored
    decision names -- and a migration is the one caller allowed past that refusal, because it
    is the one caller that can re-point the rows behind it in the same transaction.

    Structured so the version-to-model map still comes from `RUN_MANIFEST_VERSIONS` rather than
    being restated: the only thing special-cased here is which *upgrade* runs for v2, which is
    exactly the one thing the registry deliberately answers differently for readers and for
    this module. Migration 5 (`_rewrite_run_manifests`) routes through here for the same reason
    it needs to keep working: a database at `user_version` 4 holds v1 payloads in practice, but
    nothing in the schema stops it holding v2 ones.
    """
    version = json.loads(payload).get("schema_version")
    if version == "run-manifest/v2":
        return upgrade_run_manifest_v2(RunManifestV2.model_validate_json(payload))
    return read_versioned(RUN_MANIFEST_VERSIONS, payload)


def _rewrite_run_manifests(connection: sqlite3.Connection) -> dict[str, str]:
    """Advance every `runs` payload to the current version and return each run's address.

    `runs.run_id` is caller-supplied, so nothing about the row's key changes here; the manifest
    is re-serialised so the stored payload states its own version honestly rather than relying
    on every future reader upgrading it again. The returned map is what the decision rewrite
    needs -- `DecisionLedger.run_manifest_id` is not derivable from a decision row.
    """
    addresses: dict[str, str] = {}
    for run_id, payload in connection.execute("SELECT run_id, payload FROM runs").fetchall():
        manifest = _stored_run_manifest(payload)
        addresses[str(run_id)] = manifest.run_manifest_id
        connection.execute(
            "UPDATE runs SET payload = ? WHERE run_id = ?",
            (manifest.model_dump_json(exclude_computed_fields=True), run_id),
        )
    return addresses


def _rewrite_decision_ledgers(
    connection: sqlite3.Connection, addresses: Mapping[str, str]
) -> dict[str, str]:
    """Advance every `decisions` row to v2, moving its primary key, and return the ID map.

    This is the rewrite roadmap section 8 says P4 owes: read the old row, add the field, let
    the identity move, and update the key -- with every reference to it updated in the same
    transaction by this migration's later passes. `DecisionLedgerV1` validates the payload
    first, so a corrupt row fails as a corrupt row rather than being re-keyed on a guess.
    """
    identities: dict[str, str] = {}
    rows = connection.execute("SELECT decision_id, run_id, payload FROM decisions").fetchall()
    for decision_id, run_id, payload in rows:
        if json.loads(payload).get("schema_version") != "decision-ledger/v1":
            continue
        address = addresses.get(str(run_id))
        if address is None:
            raise ValueError(
                f"decision {decision_id!r} references run {run_id!r}, which has no manifest "
                "row; its run-level identity cannot be derived and it will not be re-keyed"
            )
        fields = DecisionLedgerV1.model_validate_json(payload).model_dump(
            mode="python", exclude_computed_fields=True
        )
        fields["schema_version"] = "decision-ledger/v2"
        fields["run_manifest_id"] = address
        upgraded = DecisionLedger.model_validate(fields)
        connection.execute(
            "UPDATE decisions SET decision_id = ?, payload = ? WHERE decision_id = ?",
            (
                upgraded.decision_id,
                upgraded.model_dump_json(exclude_computed_fields=True),
                decision_id,
            ),
        )
        identities[str(decision_id)] = upgraded.decision_id
    return identities


def _rewrite_validation_results(
    connection: sqlite3.Connection, identities: Mapping[str, str]
) -> None:
    """Advance every `validation_results` row to v2, re-pointing and re-keying it.

    Two things move at once and both have to, in one statement: the row's own
    `validation_id` (its content address, and this table's unique key) and its `decision_id`
    (a reference the decision pass just moved). `unexplained_return` starts at `0.0` for every
    migrated row, which is the truth about it -- a v1 result's terms summed to
    `net_active_return` by construction, so nothing was unexplained; `V2-P5-005`/`006` are
    what start producing a non-zero one.
    """
    rows = connection.execute("SELECT validation_id, payload FROM validation_results").fetchall()
    for validation_id, payload in rows:
        if json.loads(payload).get("schema_version") != "validation-result/v1":
            continue
        old = ValidationResultV1.model_validate_json(payload)
        fields = old.model_dump(mode="python", exclude_computed_fields=True)
        fields["schema_version"] = "validation-result/v2"
        fields["decision_id"] = identities.get(old.decision_id, old.decision_id)
        upgraded = ValidationResult.model_validate(fields)
        connection.execute(
            """
            UPDATE validation_results
            SET validation_id = ?, decision_id = ?, signal_id = ?, payload = ?
            WHERE validation_id = ?
            """,
            (
                upgraded.validation_id,
                upgraded.decision_id,
                upgraded.signal_id,
                upgraded.model_dump_json(exclude_computed_fields=True),
                validation_id,
            ),
        )


def _rewrite_research_reports(
    connection: sqlite3.Connection, identities: Mapping[str, str]
) -> None:
    """Re-point and re-key every report that names a moved decision.

    `ResearchReport` has no `schema_version` and its shape did not change -- but `report_id`
    is `stable_model_id` over a payload containing `decision_id`, so a report is re-keyed by
    a decision moving underneath it even though nothing about reports was versioned. This is
    the pass that would be easiest to forget, and forgetting it leaves `research_reports`
    keyed on an address that no longer describes its own contents.
    """
    if not _table_exists(connection, "research_reports"):
        return
    rows = connection.execute("SELECT report_id, payload FROM research_reports").fetchall()
    for report_id, payload in rows:
        report = read_versioned(RESEARCH_REPORT_VERSIONS, payload)
        moved = identities.get(report.decision_id)
        if moved is None:
            continue
        rewritten = ResearchReport.model_validate(
            {
                **report.model_dump(mode="python", exclude_computed_fields=True),
                "decision_id": moved,
            }
        )
        connection.execute(
            "UPDATE research_reports SET report_id = ?, payload = ? WHERE report_id = ?",
            (
                rewritten.report_id,
                rewritten.model_dump_json(exclude_computed_fields=True),
                report_id,
            ),
        )


def _rewrite_decision_references(
    connection: sqlite3.Connection, identities: Mapping[str, str]
) -> None:
    """Re-point the two remaining tables that name a decision without owning an identity.

    `research_memory` names one in a `UNIQUE` column *and* inside its payload;
    `batch_tasks` names one inside `BatchResultRef`, nested two levels down its payload.
    Neither row's own key is content-derived, so nothing is re-keyed here -- but a reference
    left behind is a `research_memory` row that can never be joined back to its decision
    again, which is the silent half of the failure section 8 warns about.
    """
    memory_rows = (
        connection.execute("SELECT decision_id, payload FROM research_memory").fetchall()
        if _table_exists(connection, "research_memory")
        else ()
    )
    for decision_id, payload in memory_rows:
        moved = identities.get(str(decision_id))
        if moved is None:
            continue
        entry = read_versioned(MEMORY_ENTRY_VERSIONS, payload)
        rewritten = MemoryEntry.model_validate(
            {
                **entry.model_dump(mode="python", exclude_computed_fields=True),
                "decision_id": moved,
            }
        )
        connection.execute(
            "UPDATE research_memory SET decision_id = ?, payload = ? WHERE decision_id = ?",
            (moved, rewritten.model_dump_json(), decision_id),
        )
    batch_rows = (
        connection.execute("SELECT batch_id, payload FROM batch_tasks").fetchall()
        if _table_exists(connection, "batch_tasks")
        else ()
    )
    for batch_id, payload in batch_rows:
        # Either row shape, because this migration is ordered *before*
        # `split_batch_task_items` and so must keep working when re-run against a database
        # that has already been through it -- see `storage/batch.py::load_task_row`.
        split = "items" not in json.loads(payload)
        task = load_task_row(connection, batch_id=str(batch_id), header=payload)
        items = [
            item.model_dump(mode="python", exclude_computed_fields=True) for item in task.items
        ]
        touched = False
        for item in items:
            result = item.get("result")
            if result is not None and result["decision_id"] in identities:
                result["decision_id"] = identities[result["decision_id"]]
                touched = True
        if not touched:
            continue
        repointed = BatchResearchTask.model_validate(
            {
                **task.model_dump(mode="python", exclude_computed_fields=True),
                "items": items,
            }
        )
        store_task_row(connection, repointed, split=split)


def _audit_identity_rewrite(connection: sqlite3.Connection, identities: Mapping[str, str]) -> None:
    """Re-read the whole database and refuse to commit an incomplete rewrite.

    A run-time audit rather than a claim in a docstring, for this repository's most repeated
    lesson: a hand-written list of the places an identity is referenced goes stale the moment
    somebody adds a table, and "I updated all the references" is exactly the sentence nothing
    checks. Three properties are read back, inside the same transaction, so a failure rolls
    the whole migration back rather than leaving a half-re-keyed ledger:

    1. Every payload of a bumped contract states the current version.
    2. Every content-addressed key equals the address of the payload stored beside it --
       which is what makes `decisions.decision_id` and `validation_results.validation_id`
       usable as keys at all.
    3. No superseded decision ID survives anywhere a decision is referenced.
    """
    superseded = set(identities)
    for decision_id, payload in connection.execute(
        "SELECT decision_id, payload FROM decisions"
    ).fetchall():
        ledger = read_versioned(DECISION_LEDGER_VERSIONS, payload)
        if ledger.decision_id != decision_id:
            raise ValueError(
                f"decision row {decision_id!r} is keyed on an address its payload does not "
                f"produce ({ledger.decision_id!r}); the identity rewrite is incomplete"
            )
    for validation_id, referenced, payload in connection.execute(
        "SELECT validation_id, decision_id, payload FROM validation_results"
    ).fetchall():
        result = read_versioned(VALIDATION_RESULT_VERSIONS, payload)
        if result.validation_id != validation_id or result.decision_id != referenced:
            raise ValueError(
                f"validation row {validation_id!r} disagrees with its own payload; the "
                "identity rewrite is incomplete"
            )
        if result.decision_id in superseded:
            raise ValueError(
                f"validation row {validation_id!r} still references superseded decision "
                f"{result.decision_id!r}"
            )
    for (value,) in _rows_if_present(
        connection, "SELECT decision_id FROM research_memory", "research_memory"
    ):
        if value in superseded:
            raise ValueError(
                f"research_memory still references superseded decision {value!r}; the "
                "identity rewrite is incomplete"
            )
    for batch_id, payload in _rows_if_present(
        connection, "SELECT batch_id, payload FROM batch_tasks", "batch_tasks"
    ):
        # Shape-tolerant for `_rewrite_decision_references`' reason: this audit runs inside
        # the version-5 migration, which may be re-run against an already-split database.
        task = load_task_row(connection, batch_id=str(batch_id), header=payload)
        for item in task.items:
            if item.result is not None and item.result.decision_id in superseded:
                raise ValueError(
                    f"batch task {task.batch_id!r} still references superseded decision "
                    f"{item.result.decision_id!r}"
                )
    for (payload,) in _rows_if_present(
        connection, "SELECT payload FROM research_reports", "research_reports"
    ):
        report = read_versioned(RESEARCH_REPORT_VERSIONS, payload)
        if report.decision_id in superseded:
            raise ValueError(
                f"research report {report.report_id!r} still references superseded decision "
                f"{report.decision_id!r}"
            )


def _rewrite_contract_identities(connection: sqlite3.Connection) -> None:
    """`V2-P4-001`/`V2-P4-025`'s identity rewrite: the migration roadmap section 8 requires.

    That section's conclusion, in its own words: a contract version bump changes a
    content-addressed identity, a transparent read-time upcast would therefore move a stored
    primary key while every reference to it kept the old value, and so P4 owes one explicit
    identity-rewrite migration -- read the old row, advance the version, recompute the ID, and
    update every row that references it inside the same transaction. This is that migration,
    and the passes below are that sentence in order.

    Two of the three bumped contracts move a key: `decisions.decision_id` (because
    `DecisionLedger` gains `run_manifest_id`) and `validation_results.validation_id` (because
    `ValidationResult` gains `unexplained_return`, and because its `decision_id` field points
    at a key the first pass moved). `runs` moves no key -- `run_id` is caller-supplied -- but
    its payload is still re-versioned, and its content address is what the decision pass
    needs. `research_reports` moves a key without being versioned at all, because `report_id`
    hashes a payload containing `decision_id`; that pass exists entirely because nothing about
    the reports contract changed and it is re-keyed anyway.

    `SignalFrame` is deliberately **not** in the list. Its `V2-P4-001` change is a domain
    narrowing that leaves every still-legal value byte-identical, so no `signal_id` moved --
    section 8's own measured correction, applied rather than re-litigated. That is not only a
    convenience: the aggregate `SignalFrame` a run produces is never persisted (only its ID
    is, in `decisions.signal_ids`), so a `signal-frame` version bump would move an identity
    this database cannot recompute, and the rewrite would be incomplete by construction.
    `_refuse_uncountable_stored_horizons` handles the one thing the narrowing does strand.

    Precondition-bound to `runs`, `decisions` and `validation_results` and merely tolerant of
    the other four tables it updates -- see `_rows_if_present` for why that asymmetry is the
    correct one. On a fresh install `runs` does not exist when migrations run (see the module
    docstring), so this defers exactly like `_demo_add_runs_archived_at` and applies on the
    next `build_storage()` call.
    """
    require_table(connection, "runs")
    require_table(connection, "decisions")
    require_table(connection, "validation_results")
    _refuse_uncountable_stored_horizons(connection)
    addresses = _rewrite_run_manifests(connection)
    identities = _rewrite_decision_ledgers(connection, addresses)
    _rewrite_validation_results(connection, identities)
    _rewrite_research_reports(connection, identities)
    _rewrite_decision_references(connection, identities)
    _audit_identity_rewrite(connection, identities)


class RunsModeProjectionError(ValueError):
    """`runs.mode` does not reproduce the `mode` stated by the payload stored beside it.

    Raised by `_audit_runs_mode_projection` inside the migration's own transaction, so the
    whole migration rolls back rather than leaving a column that answers `WHERE mode = ?` with
    something other than what the manifests say. The two ways this fires are the two ways a
    derived column fails silently: the generating expression names a path the payload does not
    have (every row projects `NULL`, every query returns nothing, and nothing else complains),
    or a stored payload states a mode the current `RunMode` no longer admits.
    """


def _audit_runs_mode_projection(connection: sqlite3.Connection) -> None:
    """Re-derive `runs.mode` in Python for every row and refuse to commit a column that lies.

    `V2-P4-001`'s `_audit_identity_rewrite` is the model, deliberately, but the property is a
    different one and the difference is worth stating: that audit reconciles *identities that
    moved* against the references that spell them, because a hand-written list of reference
    sites goes stale. This one reconciles a *derived projection* against the source it claims
    to project, because a generated column whose expression is subtly wrong is invisible --
    `json_extract(payload, '$.Mode')` is accepted by SQLite, yields `NULL` for every row,
    builds a perfectly valid index over those NULLs, and turns `list_runs(mode=paper)` into a
    confident, permanent, empty answer. No exception is raised anywhere on that path.

    So the check does not ask SQLite what the column contains and compare it to itself: it
    parses `payload` in Python, from `RUNS_MODE_PAYLOAD_PATH`, and requires the two independent
    readings to agree on every row. It additionally requires the value to be a declared
    `RunMode`, which is what makes `NULL` a failure rather than a tolerated absence.

    The third property is structural rather than per-row: the column must still be a *generated*
    one (`PRAGMA table_xinfo`'s hidden flag is 2 for `VIRTUAL`, 3 for `STORED`, 0 for an ordinary
    column). A plain `TEXT` column backfilled with today's correct values would pass both row
    checks on the day it was written and then be free to drift from the payload forever after,
    which is the entire failure mode `RUNS_MODE_COLUMN_DDL` exists to make unreachable.
    """
    hidden = {row[1]: row[-1] for row in connection.execute(f"PRAGMA table_xinfo({RUNS_TABLE})")}
    if hidden.get(RUNS_MODE_COLUMN) not in {2, 3}:
        raise RunsModeProjectionError(
            f"{RUNS_TABLE}.{RUNS_MODE_COLUMN} is not a generated column (PRAGMA table_xinfo "
            f"hidden "
            f"flag {hidden.get(RUNS_MODE_COLUMN)!r}); it would be a second, independently "
            "writable copy of a fact the payload already states"
        )
    key = RUNS_MODE_PAYLOAD_PATH.removeprefix("$.")
    declared = {mode.value for mode in RUN_MODES}
    for run_id, payload, projected in connection.execute(
        f"SELECT run_id, payload, {RUNS_MODE_COLUMN} FROM {RUNS_TABLE}"
    ).fetchall():
        stated = json.loads(payload).get(key)
        if projected != stated:
            raise RunsModeProjectionError(
                f"run {run_id!r} projects mode {projected!r} but its payload states "
                f"{stated!r}; the generating expression does not read the payload this "
                "database actually holds"
            )
        if projected not in declared:
            raise RunsModeProjectionError(
                f"run {run_id!r} states mode {projected!r}, which is not a declared RunMode "
                f"({sorted(declared)}); a run this database cannot classify would be invisible "
                "to every mode-filtered listing"
            )


def _add_runs_mode_projection(connection: sqlite3.Connection) -> None:
    """`V2-P4-002`: give `runs` the queryable `mode` column and index Finding F70 asked for.

    The DDL is not written here: `storage/sqlite.py::ensure_runs_mode_projection` is the one
    implementation, shared with `SQLiteRunRepository._initialize`, so "the migration and the
    store agree on the schema" is true by construction rather than by a comparison somebody has
    to remember to keep. What this migration adds on top of that call is everything the
    migration engine is for and a constructor cannot give: a pre-migration backup, one
    transaction, an audit row, a `PRAGMA user_version` bump, and the run-time audit below --
    which is why it exists even though the store would have established the column anyway.

    Ordered last, after `rewrite_contract_identities`, and that is the only correct place: that
    migration rewrites every `runs.payload` in place, and this column is a projection of exactly
    that payload. Running before it would index the pre-rewrite values and then quietly depend on
    SQLite re-deriving them (it does -- `UPDATE runs SET payload = ?` recomputes the column and
    maintains the index -- but "the audit ran against payloads that no longer exist" is not a
    property worth relying on). Ordered after, the audit below reads the final bytes.

    Not precondition-free: it alters `runs`, which `SQLiteRunRepository` owns and which does not
    exist when migrations run on a fresh install, so it defers exactly like the three
    table-altering migrations before it.
    """
    require_table(connection, RUNS_TABLE)
    ensure_runs_mode_projection(connection)
    _audit_runs_mode_projection(connection)


class BatchItemSplitError(ValueError):
    """A stored batch did not survive being split into a header and its item rows.

    The split moves data between two tables and rewrites the row it moved it out of, which
    is the shape of change that loses records quietly: a batch whose items ended up in the
    wrong order, or short by one, still reads back as a perfectly valid `BatchResearchTask`
    and simply reports the wrong work as done. So `_split_batch_task_items` re-reads every
    batch it touched, through the same reassembly `SQLiteBatchTaskStore.get()` uses, and
    requires the result to equal the task it started from -- not merely to parse.
    """


def _split_batch_task_items(connection: sqlite3.Connection) -> None:
    """`V2-P4-019`: move each batch's items out of its blob and into `batch_task_items`.

    Why the table shape had to change at all is `storage/batch.py`'s module docstring: with
    every item inside one JSON payload, recording one item's transition cost a full
    serialize-and-reparse of every item in the batch, so a batch cost O(N^2) and
    `V2-P4-004`'s measured 5,545-name market extrapolated to ~33 minutes of bookkeeping
    doing no research at all.

    Registered last, and that ordering is load-bearing in one direction:
    `_rewrite_decision_references` (version 5) reads `batch_tasks.payload` and rewrites the
    `BatchResultRef`s *nested inside its items*. It must therefore run while the items are
    still in the payload. Running before this migration, it does. Any future migration that
    wants to read a batch's items must go through `SQLiteBatchTaskStore` instead, because
    after this version the payload no longer has an `items` key.

    Idempotent in both of the ways this engine needs. A payload that has already been split
    has no `items` key and is skipped, so a database that reached this shape through
    `SQLiteBatchTaskStore.__init__` on a fresh install (where this migration defers, the
    table not existing yet, and applies as a no-op on the next open) is left alone. And the
    DDL is `CREATE TABLE IF NOT EXISTS`, shared verbatim with the constructor.
    """
    require_table(connection, "batch_tasks")
    connection.execute(BATCH_TASK_ITEMS_DDL)
    rows = connection.execute("SELECT batch_id, payload FROM batch_tasks").fetchall()
    split: dict[str, BatchResearchTask] = {}
    for batch_id, payload in rows:
        if "items" not in json.loads(payload):
            continue
        task = read_versioned(BATCH_RESEARCH_TASK_VERSIONS, payload)
        header, items = split_task_payload(task)
        connection.execute(
            "UPDATE batch_tasks SET payload = ? WHERE batch_id = ?", (header, batch_id)
        )
        connection.execute("DELETE FROM batch_task_items WHERE batch_id = ?", (batch_id,))
        connection.executemany(
            "INSERT INTO batch_task_items (batch_id, position, payload) VALUES (?, ?, ?)",
            [(batch_id, position, item) for position, item in enumerate(items)],
        )
        split[batch_id] = task
    _audit_batch_item_split(connection, split)


def _audit_batch_item_split(
    connection: sqlite3.Connection, split: Mapping[str, BatchResearchTask]
) -> None:
    """Re-read every batch this migration rewrote and require it to be unchanged.

    Reassembles from the two tables exactly as `SQLiteBatchTaskStore.get()` does -- header
    payload, item payloads in `position` order, spliced back together and validated as one
    task -- and compares against the task read before the split. Equality of the whole model
    is the check, rather than a count: a batch whose 5,545 items came back in a different
    order, or with one item's `result` dropped, has the right count and the wrong answer.
    """
    for batch_id, before in split.items():
        header = connection.execute(
            "SELECT payload FROM batch_tasks WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        items = connection.execute(
            "SELECT payload FROM batch_task_items WHERE batch_id = ? ORDER BY position",
            (batch_id,),
        ).fetchall()
        document = json.loads(header[0])
        if "items" in document:
            raise BatchItemSplitError(
                f"batch {batch_id!r} still carries an 'items' key in its header payload; "
                "the items would then exist in two places and the store reads only one"
            )
        document["items"] = [json.loads(item[0]) for item in items]
        after = read_versioned(BATCH_RESEARCH_TASK_VERSIONS, json.dumps(document))
        if after != before:
            raise BatchItemSplitError(
                f"batch {batch_id!r} does not read back as the task that was split: "
                f"{len(before.items)} items in, {len(after.items)} items out"
            )


class StrandedManifestReferenceError(ValueError):
    """A stored decision names a run-manifest address that no stored run produces.

    The failure `refuse_run_manifest_v2_upgrade` exists to keep out of a database, raised here
    if it got in anyway. It is silent by construction -- `DecisionLedger.run_manifest_id` is
    pattern-constrained but not foreign-keyed, so a stale address is a perfectly valid string
    that simply resolves to nothing, and every query keeps returning rows. Nothing in
    `_audit_identity_rewrite` looked for it, because at `V2-P4-001` the address had only just
    been created and could not yet be stale.
    """


def _repoint_decision_manifest_addresses(
    connection: sqlite3.Connection, addresses: Mapping[str, str]
) -> dict[str, str]:
    """Point every decision at its run's *current* address, re-keying it, and return the map.

    `V2-P4-010` adds two fields to `RunManifest`, so `run_manifest_id` moves for every stored
    run; `DecisionLedger.run_manifest_id` is a field of the ledger, so `decision_id` moves with
    it even though `decision-ledger` is not bumped by this issue. That second sentence is the
    whole reason this migration exists and is the part a reader of the domain diff would not
    see -- it is measured in
    `tests/unit/domain/test_contract_identity.py::test_the_component_planes_moved_the_addresses_the_migration_has_to_rewrite`.

    Rows already pointing at the right address are left untouched rather than rewritten to
    identical bytes, which is what makes this migration a no-op on a database that never held a
    v2 manifest -- the common case, since a fresh install's first `build_storage()` runs every
    migration before a single run exists.
    """
    identities: dict[str, str] = {}
    rows = connection.execute("SELECT decision_id, run_id, payload FROM decisions").fetchall()
    for decision_id, run_id, payload in rows:
        address = addresses.get(str(run_id))
        if address is None:
            raise StrandedManifestReferenceError(
                f"decision {decision_id!r} references run {run_id!r}, which has no manifest "
                "row; its run-level identity cannot be recomputed and it will not be re-keyed"
            )
        ledger = read_versioned(DECISION_LEDGER_VERSIONS, payload)
        if ledger.run_manifest_id == address:
            continue
        repointed = DecisionLedger.model_validate(
            {
                **ledger.model_dump(mode="python", exclude_computed_fields=True),
                "run_manifest_id": address,
            }
        )
        connection.execute(
            "UPDATE decisions SET decision_id = ?, payload = ? WHERE decision_id = ?",
            (
                repointed.decision_id,
                repointed.model_dump_json(exclude_computed_fields=True),
                decision_id,
            ),
        )
        identities[str(decision_id)] = repointed.decision_id
    return identities


def _repoint_validation_results(
    connection: sqlite3.Connection, identities: Mapping[str, str]
) -> None:
    """Re-point and re-key every validation result whose decision this migration moved.

    The sibling of `_rewrite_validation_results` and deliberately not a parameterisation of it:
    that one advances `validation-result/v1` rows and skips everything else, which is right for
    `V2-P4-001` and wrong here, where every row is already current and only its *reference* went
    stale. Folding the two into one function with a version flag would have made the v1 pass
    silently applicable to a v2 row, which is the mistake the `if ... != "validation-result/v1":
    continue` guard over there exists to prevent.
    """
    if not _table_exists(connection, "validation_results"):
        return
    rows = connection.execute("SELECT validation_id, payload FROM validation_results").fetchall()
    for validation_id, payload in rows:
        result = read_versioned(VALIDATION_RESULT_VERSIONS, payload)
        moved = identities.get(result.decision_id)
        if moved is None:
            continue
        repointed = ValidationResult.model_validate(
            {
                **result.model_dump(mode="python", exclude_computed_fields=True),
                "decision_id": moved,
            }
        )
        connection.execute(
            """
            UPDATE validation_results
            SET validation_id = ?, decision_id = ?, payload = ?
            WHERE validation_id = ?
            """,
            (
                repointed.validation_id,
                repointed.decision_id,
                repointed.model_dump_json(exclude_computed_fields=True),
                validation_id,
            ),
        )


def _audit_manifest_references_resolve(connection: sqlite3.Connection) -> None:
    """Refuse to commit a database in which a decision names a run address nothing produces.

    The audit `_audit_identity_rewrite` does not have, and could not have had. That one asks
    whether every content-addressed key equals the address of the payload beside it, which is a
    question about a row and its own contents; this one asks whether a *cross-table reference*
    still resolves, which only became a question that could be answered wrongly once
    `V2-P4-025` gave the ledger something to point at.

    Re-derived from the `runs` table rather than from this migration's own bookkeeping, for the
    reason `_audit_runs_mode_projection` states about itself: an audit that reads back the same
    dictionary the migration just wrote is checking its own arithmetic, not the database.

    **Its call site below is not observable from the current code, and that is stated rather
    than hidden.** Deleting `_audit_manifest_references_resolve(connection)` from
    `_rewrite_manifest_component_planes` leaves the whole suite green -- measured, not assumed.
    The reason is structural: `_repoint_decision_manifest_addresses` re-points every decision
    against an address it read out of `runs`, and refuses outright when the run row is gone, so
    no path through the passes above can reach this one with a stranded reference. The guard is
    therefore against a *later* pass, and it is kept on exactly the grounds
    `_audit_identity_rewrite` is kept on: a hand-written account of where an identity is
    referenced goes stale the moment somebody adds a table or reorders a pass, and "I updated
    all the references" is the sentence nothing checks. The behaviour itself is driven directly
    by `tests/integration/storage/test_manifest_component_plane_rewrite.py::
    test_a_decision_left_pointing_at_an_address_no_run_produces_refuses_the_whole_migration`,
    which builds the state the passes make unreachable and asserts the refusal.
    """
    produced = {
        _stored_run_manifest(payload).run_manifest_id
        for (payload,) in connection.execute("SELECT payload FROM runs").fetchall()
    }
    for decision_id, payload in connection.execute(
        "SELECT decision_id, payload FROM decisions"
    ).fetchall():
        ledger = read_versioned(DECISION_LEDGER_VERSIONS, payload)
        if ledger.run_manifest_id not in produced:
            raise StrandedManifestReferenceError(
                f"decision {decision_id!r} names run-manifest address "
                f"{ledger.run_manifest_id!r}, which no stored run produces; the component-plane "
                "rewrite is incomplete"
            )


def _rewrite_manifest_component_planes(connection: sqlite3.Connection) -> None:
    """`V2-P4-010`'s identity rewrite: the second one roadmap section 8's rule demands.

    The issue gives `RunManifest` an agent plane and a quantitative-model plane, and moves the
    agent ids out of `model_versions` so that slot can hold the vendor models it is named for.
    Every one of those is a hashed field, so every stored run's `run_manifest_id` moves -- and
    behind it `decisions.decision_id`, `validation_results.validation_id` and
    `research_reports.report_id`, none of whose contracts changed at all.

    The passes are `_rewrite_contract_identities`' in the same order and three of them are
    literally its functions, because the cascade below a moved decision is the same cascade
    whatever moved it. What differs is the top: that migration advanced two contracts' *versions*
    and derived a new decision id from the version bump; this one advances one contract's
    version and derives the new decision id from a reference that went stale underneath it. The
    tail is also different -- `_audit_manifest_references_resolve` asks a question about
    `runs`↔`decisions` that `_audit_identity_rewrite` never asks.

    Precondition-bound to the same three tables, so it defers on a fresh install exactly as
    version 5 does and applies on the next `build_storage()` call.
    """
    require_table(connection, "runs")
    require_table(connection, "decisions")
    require_table(connection, "validation_results")
    addresses = _rewrite_run_manifests(connection)
    identities = _repoint_decision_manifest_addresses(connection, addresses)
    _repoint_validation_results(connection, identities)
    _rewrite_research_reports(connection, identities)
    _rewrite_decision_references(connection, identities)
    _audit_identity_rewrite(connection, identities)
    _audit_manifest_references_resolve(connection)


BASELINE_VERSION = 1
CREATE_VALIDATION_RESULTS_VERSION = 2
DEMO_ADD_RUNS_ARCHIVED_AT_VERSION = 3
CREATE_QUERY_PATH_INDEXES_VERSION = 4
REWRITE_CONTRACT_IDENTITIES_VERSION = 5
ADD_RUNS_MODE_PROJECTION_VERSION = 6
SPLIT_BATCH_TASK_ITEMS_VERSION = 7
REWRITE_MANIFEST_COMPONENT_PLANES_VERSION = 8

MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=BASELINE_VERSION, name="baseline", apply=_baseline_apply),
    Migration(
        version=CREATE_VALIDATION_RESULTS_VERSION,
        name="create_validation_results",
        apply=_create_validation_results_table,
    ),
    Migration(
        version=DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        name="demo_add_runs_archived_at",
        apply=_demo_add_runs_archived_at,
    ),
    Migration(
        version=CREATE_QUERY_PATH_INDEXES_VERSION,
        name="create_query_path_indexes",
        apply=_create_query_path_indexes,
    ),
    Migration(
        version=REWRITE_CONTRACT_IDENTITIES_VERSION,
        name="rewrite_contract_identities",
        apply=_rewrite_contract_identities,
    ),
    Migration(
        version=ADD_RUNS_MODE_PROJECTION_VERSION,
        name="add_runs_mode_projection",
        apply=_add_runs_mode_projection,
    ),
    Migration(
        version=SPLIT_BATCH_TASK_ITEMS_VERSION,
        name="split_batch_task_items",
        apply=_split_batch_task_items,
    ),
    Migration(
        version=REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
        name="rewrite_manifest_component_planes",
        apply=_rewrite_manifest_component_planes,
    ),
)


def _current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _pending(current_version: int, migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    ordered = sorted(migrations, key=lambda migration: migration.version)
    return tuple(migration for migration in ordered if migration.version > current_version)


def _take_backup(path: Path, *, current_version: int, clock: Callable[[], datetime]) -> Path:
    """Back up `path` (via the SQLite backup API, safe under WAL) before migrating.

    Lands in `path.parent / "backups"` -- inside the runtime directory, which is
    gitignored -- named with both the pre-migration version and a timestamp so an
    operator can identify which backup corresponds to which failed or successful run.
    `.bak` is a SQLite binary dump just like `.sqlite3`/`.db`, so it is one of
    `scripts/verify_publication.py`'s `BLOCKED_SUFFIXES`: escaping the publication scan
    is the risk for a file like this, not a protection, and `.gitignore` alone stops
    covering it the moment someone passes a custom `--runtime-dir` outside the ignored
    tree. The filename is claimed with `os.O_CREAT | os.O_EXCL` (atomic at the OS level,
    unlike an exists-then-create check) and falls back to a numeric suffix on collision,
    so two callers backing up the same version within the same clock tick -- e.g. two
    concurrent processes, or a frozen test clock -- never silently overwrite each other.
    """
    backups_dir = path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = clock().strftime("%Y%m%dT%H%M%SZ")
    stem = f"{path.name}.v{current_version}.{timestamp}"
    suffix = 0
    while True:
        candidate = stem if suffix == 0 else f"{stem}-{suffix}"
        backup_path = backups_dir / f"{candidate}.bak"
        try:
            claimed_fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            suffix += 1
            continue
        os.close(claimed_fd)
        break
    source = sqlite3.connect(path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return backup_path


def read_status(path: Path, *, migrations: Sequence[Migration] = MIGRATIONS) -> MigrationStatus:
    """Return the current schema version, applied migrations, and pending migrations.

    Read-only: never takes a backup, never opens a write transaction, never creates
    `schema_migrations` if it is absent. Used by the `migrate status` CLI command and by
    `migrate run --dry-run`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        current_version = _current_version(connection)
        if _table_exists(connection, "schema_migrations"):
            rows = connection.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        else:
            rows = []
        applied = tuple(
            AppliedMigration(version=row[0], name=row[1], applied_at=row[2]) for row in rows
        )
    finally:
        connection.close()
    pending = _pending(current_version, migrations)
    return MigrationStatus(
        path=path, current_version=current_version, applied=applied, pending=pending
    )


def run_migrations(
    path: Path,
    *,
    clock: Callable[[], datetime],
    migrations: Sequence[Migration] = MIGRATIONS,
) -> MigrationRunResult:
    """Apply every pending migration, in order, each in its own transaction.

    Forward-only and idempotent: already-applied versions (per `PRAGMA user_version`) are
    never reconsidered. If nothing is pending, this is a fast no-op that opens no write
    transaction and takes no backup. Otherwise, backs up `path` once before applying
    anything, then applies each pending migration inside `BEGIN IMMEDIATE` / `COMMIT`: the
    migration's own DDL/DML, the `schema_migrations` audit row, and the `PRAGMA
    user_version` bump are one atomic unit. Any failure rolls the whole unit back --
    `user_version` does not move and no audit row is written for that migration -- and is
    re-raised as `MigrationFailedError` naming the pre-migration backup. A migration that
    raises `MigrationNotYetApplicable` stops the run (without error) and leaves it, and
    everything after it, pending.

    **A run that applied nothing removes the backup it took** (`V2-P4-111`). The copy has to be
    taken before the loop, because a migration announces an unmet precondition by raising from
    inside `apply()` and there is no way to know in advance which of the pending set will write;
    so the removal is at the end instead, and only when `applied` is empty. Before this, a store
    with a permanently-deferring migration wrote one full-size backup **per process start** with
    nothing ever applied. Measured on a real `state.sqlite3` stuck at `user_version=4` -- its
    history predates `create_validation_results`, so it has no `validation_results` table for
    `_rewrite_contract_identities` to alter -- that was 125 identical 139,264-byte files and no
    terminating condition. The removed file is provably this call's own (`_take_backup` claims
    its name with `O_CREAT | O_EXCL`) and provably redundant (nothing was applied, so it is
    byte-for-byte the database it sits beside). A **failed** migration keeps its backup, which is
    what `MigrationFailedError` points a caller at; that path raises rather than reaching the
    removal.

    Concurrency: `pending` is computed once, from a snapshot taken before any lock is
    held, so two callers racing the same database (e.g. the API and the CLI hitting the
    same `runtime_dir`) can both decide the same migration is pending. `BEGIN IMMEDIATE`
    correctly serializes the two writers, but without a re-check the loser would replay
    `migration.apply()` and then hit a primary-key collision on its own `INSERT INTO
    schema_migrations` -- a real `sqlite3.IntegrityError` that the generic `except
    Exception` below would report as `MigrationFailedError`, even though the database is
    healthy and the migration is in fact already applied. Each iteration therefore
    re-reads `PRAGMA user_version` *after* acquiring the write lock and skips migrations
    a concurrent winner already committed while this connection was blocked waiting for
    the lock, instead of attempting to reapply them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(_SCHEMA_MIGRATIONS_DDL)
        from_version = _current_version(connection)
        pending = _pending(from_version, migrations)
        if not pending:
            return MigrationRunResult(
                path=path,
                from_version=from_version,
                to_version=from_version,
                applied=(),
                backup_path=None,
            )
        backup_path: Path | None = _take_backup(path, current_version=from_version, clock=clock)
        logger.info(
            "migration_backup_created",
            extra={"backup_path": str(backup_path), "from_version": from_version},
        )
        applied: list[AppliedMigration] = []
        for migration in pending:
            connection.execute("BEGIN IMMEDIATE")
            if migration.version <= _current_version(connection):
                # A concurrent writer already applied this exact migration while this
                # connection was blocked waiting for the write lock above -- not a
                # fault, just a lost race. Skip it instead of replaying `apply()` and
                # colliding with the winner's `schema_migrations` row.
                connection.execute("ROLLBACK")
                continue
            try:
                migration.apply(connection)
                applied_at = clock().isoformat()
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, applied_at),
                )
                connection.execute(f"PRAGMA user_version = {migration.version:d}")
            except MigrationNotYetApplicable:
                connection.execute("ROLLBACK")
                break
            except Exception as error:
                connection.execute("ROLLBACK")
                # Never `str(error)`: `migration.apply()` is arbitrary caller code (a
                # future migration's precondition check, DDL/DML against a real
                # database) and its exception message is not vetted the way
                # `version`/`name`/`backup_path` are -- see the module-level logging
                # discipline note in `logging_setup.py`.
                logger.error(
                    "migration_failed",
                    extra={
                        "migration_version": migration.version,
                        "migration_name": migration.name,
                        "backup_path": str(backup_path),
                    },
                )
                raise MigrationFailedError(
                    f"migration {migration.version} ({migration.name}) failed and was rolled back",
                    version=migration.version,
                    name=migration.name,
                    backup_path=backup_path,
                ) from error
            else:
                connection.execute("COMMIT")
                applied.append(
                    AppliedMigration(
                        version=migration.version, name=migration.name, applied_at=applied_at
                    )
                )
                logger.info(
                    "migration_applied",
                    extra={
                        "migration_version": migration.version,
                        "migration_name": migration.name,
                    },
                )
        if not applied and backup_path is not None:
            # `V2-P4-111`. The backup still happens before the loop, because that is the only
            # place it can happen and still predate a write: a migration announces that its
            # precondition is unmet by raising from inside `apply()`, so there is no way to know
            # in advance which of the pending set will write. What changes is that a copy which
            # protected nothing is removed **by the call that took it**, before that call
            # returns.
            #
            # It is provably safe to remove and provably this call's own: `_take_backup` claims
            # its filename with `os.O_CREAT | os.O_EXCL`, so no other process shares it, and
            # nothing was applied, so the database is byte-for-byte what it was when the copy was
            # taken. It is only ever reached when `applied` is empty -- a failed migration keeps
            # its backup, which is what `MigrationFailedError` points a caller at, and that path
            # raises rather than arriving here at all.
            #
            # Measured cost of not doing this, on a store whose `_rewrite_contract_identities`
            # defers permanently: one 139,264-byte file per process start, forever. That was 125
            # of the 128 files in the user's own `runtime/backups/`.
            backup_path.unlink(missing_ok=True)
            backup_path = None
        to_version = _current_version(connection)
        return MigrationRunResult(
            path=path,
            from_version=from_version,
            to_version=to_version,
            applied=tuple(applied),
            backup_path=backup_path,
        )
    finally:
        connection.close()
