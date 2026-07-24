"""Append-only SQLite portfolio order and execution ledger."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.backtest.portfolio import PortfolioTransition


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
        return sqlite3.connect(self.path, timeout=10)

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
        return None if row is None else PortfolioTransition.model_validate_json(row[0])

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
        return tuple(PortfolioTransition.model_validate_json(row[0]) for row in rows)
