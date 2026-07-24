"""SQLite persistence for mutable watchlists and immutable reports."""

import sqlite3
from contextlib import closing
from pathlib import Path

from openalpha_cn.product.research import ResearchReport, WatchlistEntry


class SQLiteWatchlistStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    subject TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )

    def put(self, entry: WatchlistEntry) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO watchlist (subject, payload) VALUES (?, ?)
                ON CONFLICT(subject) DO UPDATE SET payload = excluded.payload
                """,
                (entry.subject, entry.model_dump_json()),
            )

    def list(self) -> tuple[WatchlistEntry, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload FROM watchlist ORDER BY subject").fetchall()
        return tuple(WatchlistEntry.model_validate_json(row[0]) for row in rows)

    def remove(self, subject: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM watchlist WHERE subject = ?",
                (subject,),
            )
        return cursor.rowcount == 1


class SQLiteReportStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_reports (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT UNIQUE NOT NULL,
                    subject TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def append(self, report: ResearchReport) -> None:
        payload = report.model_dump_json(exclude_computed_fields=True)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM research_reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError(f"report_id conflicts: {report.report_id}")
                return
            connection.execute(
                """
                INSERT INTO research_reports (report_id, subject, payload)
                VALUES (?, ?, ?)
                """,
                (report.report_id, report.subject, payload),
            )

    def get(self, report_id: str) -> ResearchReport | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM research_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        return None if row is None else ResearchReport.model_validate_json(row[0])

    def list(self, *, subject: str | None = None) -> tuple[ResearchReport, ...]:
        with closing(self._connect()) as connection:
            if subject is None:
                rows = connection.execute(
                    "SELECT payload FROM research_reports ORDER BY sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload FROM research_reports
                    WHERE subject = ?
                    ORDER BY sequence
                    """,
                    (subject,),
                ).fetchall()
        return tuple(ResearchReport.model_validate_json(row[0]) for row in rows)
