"""SQLite persistence for immutable outcome-validation results (V2-P0B-010)."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.domain.validation import VALIDATION_RESULT_VERSIONS, ValidationResult
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.connection import open_state_connection


class SQLiteValidationStore:
    """Persist immutable `ValidationResult` rows without replacement.

    Unlike this package's other SQLite stores, this constructor does *not* create its own
    table: `validation_results` is created exclusively by the `create_validation_results`
    migration in `storage/migrations.py`. The nine tables that predate the migration
    engine (V2-P0B-004) stay grandfathered onto a `CREATE TABLE IF NOT EXISTS` constructor
    side effect (see that module's docstring); every table added after the migration
    engine existed -- this one included -- goes through it instead, per this task's brief.
    A caller that constructs this store before `run_migrations()` has created the table
    gets a real `sqlite3.OperationalError: no such table` on first use, not a silent
    fallback -- exactly the same "explicit failure over silent misbehavior" discipline
    `storage/versioning.py` uses for an unknown schema version.

    Append is idempotent by `validation_id` (content-derived, like `ResearchReport.report_id`
    and `PortfolioTransition.order_id`'s uniqueness constraint): writing the identical
    result twice is a no-op, and writing a different payload under a validation_id already
    on file is an explicit `ValueError` -- the same append-only, content-addressed-identity
    contract as `SQLiteReportStore.append` / `SQLitePortfolioLedger.append`.

    Precedent note: this task's original brief instructed matching `SQLiteRunRepository`'s
    semantics instead; that precedent does not apply here and was not followed. `run_id` on
    `SQLiteRunRepository.append_run` is caller-supplied, not content-derived, so that store
    has no idempotent-replay path at all -- reusing a `run_id`, even for a byte-identical
    replay, raises `DuplicateRecordError` unconditionally (`storage/sqlite.py`). This store's
    `validation_id`, like `report_id`/`order_id`, is content-derived, so the correct precedent
    -- the one actually implemented above -- is `SQLiteReportStore`/`SQLitePortfolioLedger`,
    both of which compare the stored payload before rejecting a reused ID and treat a
    byte-identical replay as a no-op rather than an error.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def append(self, result: ValidationResult) -> None:
        """Append idempotently by validation ID; reject a conflicting reuse of the ID."""
        payload = result.model_dump_json(exclude_computed_fields=True)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM validation_results WHERE validation_id = ?",
                (result.validation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError(f"validation_id conflicts: {result.validation_id}")
                return
            connection.execute(
                """
                INSERT INTO validation_results (validation_id, decision_id, signal_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (result.validation_id, result.decision_id, result.signal_id, payload),
            )

    def list_by_decision(self, decision_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one decision, in append order.

        Served by `validation_results_decision_id_idx` -- see `storage/migrations.py` --
        so this stays an index seek even as the table grows (Finding F69's precedent:
        three pre-existing tables query a column with no matching index and full-scan).
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM validation_results
                WHERE decision_id = ?
                ORDER BY sequence
                """,
                (decision_id,),
            ).fetchall()
        return tuple(read_versioned(VALIDATION_RESULT_VERSIONS, row[0]) for row in rows)

    def list_by_signal(self, signal_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one signal, in append order.

        Served by `validation_results_signal_id_idx` -- see the sibling docstring on
        `list_by_decision` above.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM validation_results
                WHERE signal_id = ?
                ORDER BY sequence
                """,
                (signal_id,),
            ).fetchall()
        return tuple(read_versioned(VALIDATION_RESULT_VERSIONS, row[0]) for row in rows)
