"""Append-only SQLite portfolio order and execution ledger."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

from openalpha_cn.domain.portfolio import PORTFOLIO_TRANSITION_VERSIONS, PortfolioTransition
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.connection import open_state_connection

PAPER_LEDGER_MIGRATED_SCHEMA: Final[tuple[str, ...]] = (
    "CREATE INDEX IF NOT EXISTS portfolio_transitions_subject_idx "
    "ON portfolio_transitions(subject)",
)
"""What `storage/migrations.py` has added to `state.sqlite3`'s `portfolio_transitions` since the
store created it, repeated for a ledger file the migration engine never opens.

Today that is the one index `create_query_path_indexes` (migration 4) builds. Every statement is
idempotent, because it runs on every `open_paper_ledger`. A future migration of that table must be
repeated here too: `tests/unit/backtest/test_paper_state.py::
test_the_paper_ledger_file_has_the_schema_the_migrations_give_the_backtest_ledger` compares the
two files' table, columns and indexes after `build_storage` and goes red until it is."""


class SQLitePortfolioLedger:
    """Persist immutable order transitions without replacement."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE NOT NULL,
                    subject TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def append(self, transition: PortfolioTransition) -> None:
        """Append idempotently or reject conflicting reuse of an order ID."""
        payload = transition.model_dump_json(exclude_computed_fields=True)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM portfolio_transitions WHERE order_id = ?",
                (transition.order.order_id,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError(f"portfolio order_id conflicts: {transition.order.order_id}")
                return
            connection.execute(
                """
                INSERT INTO portfolio_transitions (order_id, subject, payload)
                VALUES (?, ?, ?)
                """,
                (
                    transition.order.order_id,
                    transition.order.subject,
                    payload,
                ),
            )

    def get(self, order_id: str) -> PortfolioTransition | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM portfolio_transitions WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return None if row is None else read_versioned(PORTFOLIO_TRANSITION_VERSIONS, row[0])

    def list(self, *, subject: str | None = None) -> tuple[PortfolioTransition, ...]:
        with closing(self._connect()) as connection:
            if subject is None:
                rows = connection.execute(
                    "SELECT payload FROM portfolio_transitions ORDER BY sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload FROM portfolio_transitions
                    WHERE subject = ?
                    ORDER BY sequence
                    """,
                    (subject,),
                ).fetchall()
        return tuple(read_versioned(PORTFOLIO_TRANSITION_VERSIONS, row[0]) for row in rows)


def open_paper_ledger(path: Path) -> SQLitePortfolioLedger:
    """The paper book's own ledger at `path`, with the schema `state.sqlite3`'s ledger has.

    `run_migrations` touches `state.sqlite3` only, so a second `SQLitePortfolioLedger` at another
    file gets the table its constructor creates and nothing a migration added since; this brings
    it level with `PAPER_LEDGER_MIGRATED_SCHEMA`. Separate files for separate books, one schema.
    """
    ledger = SQLitePortfolioLedger(path)
    with closing(open_state_connection(path)) as connection, connection:
        for statement in PAPER_LEDGER_MIGRATED_SCHEMA:
            connection.execute(statement)
    return ledger
