"""SQLite storage for durable scheduled jobs, their leases and their per-session runs.

`V2-P5-010`, closing audit `F98`. Two tables, and each column is here because something
queries it.

## `scheduled_jobs` -- the schedule and the lease, in one row

Fully columnar rather than a JSON payload beside a few indexed columns, which is the shape
`storage/batch.py` and `storage/recovery.py` both arrived at from the other direction: they
split *out* of a blob everything a query touches. Every field of `ScheduledJob` is touched by a
query here, so there is nothing left to keep in a blob. `read_versioned` is still used on the
way back, through `SCHEDULED_JOB_VERSIONS`, because `domain/versioning.py` asks that every
stored-row read in this package go through it whether or not the model has a real
`schema_version` yet.

## `job_runs` -- the per-trading-day idempotency key *is* the primary key

`idempotency_key` is `job_id@YYYY-MM-DD` (`job_contracts.trading_day_key`) and it is the
`PRIMARY KEY`. That is the entire "at most once per trading session" guarantee, and it is
structural: a second `INSERT` for the same job and session is an `IntegrityError` from SQLite,
not a lost race between a `SELECT` and an `INSERT` that two processes can both win. It is the
same move `domain/_identity.py` makes for content addresses, and it means a caller cannot
forget to check.

## Why there is no migration

`_baseline_apply`'s docstring states the rule this package already follows: tables are created
by their owning store's `CREATE TABLE IF NOT EXISTS`, and `MIGRATIONS` exists to change
databases that already hold data. **Measured, that is not merely convention here but a
requirement**: on a fresh `state.sqlite3`, `create_app()` logs `schema_version: 2` -- migration
3 (`demo_add_runs_archived_at`) raises `MigrationNotYetApplicable` because `runs` does not
exist yet, `run_migrations` breaks out of the loop on that, and migrations 4 through 8 are
therefore **never applied to a new database**. A ninth migration adding these tables would
never run either. `CREATE TABLE IF NOT EXISTS` is the only construction that works on both a
new database and an old one.

## Crash recovery is lease expiry, not a sweeper

Nothing scans this table for abandoned work. `claim()` takes a row whose lease has expired
exactly as readily as one that was never leased, so a process that died mid-run is recovered by
the next process that asks for the job -- a property of every claim rather than a maintenance
task somebody has to schedule (which would want a scheduler, which is this module).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.job_contracts import (
    JOB_RUN_VERSIONS,
    SCHEDULED_JOB_VERSIONS,
    CatchUpPolicy,
    JobRun,
    ScheduledJob,
    trading_day_key,
)
from openalpha_cn.storage.connection import open_state_connection

SCHEDULED_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    catch_up TEXT NOT NULL,
    last_fired_session TEXT,
    next_fire_time TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

JOB_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS job_runs (
    idempotency_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE,
    session TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_type TEXT
)
"""

JOB_RUNS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_job_runs_job_session ON job_runs (job_id, session)
"""
"""The one query shape that is not the primary key: "what has this job done", newest first.

Without it, `runs()` and the `last_fired_session` repair path are table scans over every run of
every job -- which is one row per job per trading session, so roughly 250 rows per job per year
and growing without bound. `idempotency_key` is a covering key for a *single* session and
answers none of these.
"""


class JobStoreError(RuntimeError):
    """A scheduling write this store refuses, named rather than surfaced as a SQLite fault."""


class JobAlreadyRanError(JobStoreError):
    """This job already has a run for this trading session.

    The per-trading-day guarantee, arriving as an exception rather than as a silently ignored
    `INSERT OR IGNORE`. A caller that wakes twice for the same session must be able to tell
    "I already did this" from "I did it now", because the two lead to different logs and, for
    `RUN_EACH_MISSED`, to different next steps.
    """

    def __init__(self, *, job_id: str, session: date) -> None:
        super().__init__(
            f"{job_id} already has a run for the {session.isoformat()} session; the "
            "per-trading-day idempotency key is a primary key, so this is refused rather "
            "than duplicated"
        )
        self.job_id = job_id
        self.session = session


class LeaseNotHeldError(JobStoreError):
    """The caller tried to act on a job it does not currently hold the lease for."""

    def __init__(self, *, job_id: str, owner: str) -> None:
        super().__init__(
            f"{owner!r} does not hold the lease on {job_id!r}: it expired and was taken, or "
            "it was never claimed. Claim the job again rather than writing through a lease "
            "someone else now owns"
        )
        self.job_id = job_id
        self.owner = owner


class SQLiteJobStore:
    """Durable job table with next-fire-time, lease/lock and per-trading-day idempotency."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(SCHEDULED_JOBS_DDL)
            connection.execute(JOB_RUNS_DDL)
            connection.execute(JOB_RUNS_INDEX_DDL)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    # --- the schedule -------------------------------------------------------------------

    def register(self, job: ScheduledJob) -> ScheduledJob:
        """Declare `job`, or return the one already stored under that id untouched.

        Idempotent by *declaration*, never by progress: re-registering a job that has already
        run does not reset `last_fired_session`, because a process restart re-declaring its
        schedule is the ordinary case and re-running a month of sessions is not what it meant.
        Changing a stored job's policy is deliberately not something `register` does -- that is
        an operator decision with a catch-up consequence, and silently applying it on the next
        restart is how a `SKIP_MISSED` job quietly becomes a `RUN_EACH_MISSED` one.
        """
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._read(connection, job.job_id)
            if existing is not None:
                connection.execute("ROLLBACK")
                return existing
            connection.execute(
                "INSERT INTO scheduled_jobs (job_id, catch_up, last_fired_session, "
                "next_fire_time, lease_owner, lease_expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.catch_up.value,
                    _as_text(job.last_fired_session),
                    _as_text(job.next_fire_time),
                    job.lease_owner,
                    _as_text(job.lease_expires_at),
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")
        return job

    def get(self, job_id: str) -> ScheduledJob | None:
        with closing(self._connect()) as connection:
            return self._read(connection, job_id)

    def list_jobs(self) -> tuple[ScheduledJob, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT job_id, catch_up, last_fired_session, next_fire_time, lease_owner, "
                "lease_expires_at, created_at, updated_at FROM scheduled_jobs ORDER BY job_id"
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    # --- the lease ----------------------------------------------------------------------

    def claim(
        self, job_id: str, *, owner: str, now: datetime, lease_for: timedelta
    ) -> ScheduledJob | None:
        """Take the lease on `job_id` if it is free or expired; `None` if someone else holds it.

        One `UPDATE ... WHERE` under `BEGIN IMMEDIATE` rather than a read followed by a write.
        The guard is in the `WHERE` clause, so SQLite's own write lock decides the race and
        `rowcount` reports who won -- a read-then-write would let two processes both see a free
        lease and both take it.

        `lease_expires_at <= now` is the expired case and is what makes this crash recovery:
        the row a dead process left behind is claimable by the next caller, with no sweep.
        """
        expires_at = now + lease_for
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE scheduled_jobs SET lease_owner = ?, lease_expires_at = ?, "
                "updated_at = ? WHERE job_id = ? AND (lease_owner IS NULL "
                "OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (
                    owner,
                    expires_at.isoformat(),
                    now.isoformat(),
                    job_id,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                return None
            claimed = self._read(connection, job_id)
            connection.execute("COMMIT")
        return claimed

    def release(self, job_id: str, *, owner: str, now: datetime) -> ScheduledJob:
        """Give the lease back. Refuses if `owner` no longer holds it."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE scheduled_jobs SET lease_owner = NULL, lease_expires_at = NULL, "
                "updated_at = ? WHERE job_id = ? AND lease_owner = ?",
                (now.isoformat(), job_id, owner),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise LeaseNotHeldError(job_id=job_id, owner=owner)
            released = self._read(connection, job_id)
            connection.execute("COMMIT")
        assert released is not None  # the UPDATE above matched it
        return released

    # --- the per-session runs ------------------------------------------------------------

    def start_session(self, job_id: str, session: date, *, owner: str, now: datetime) -> JobRun:
        """Open a run for `session`, refusing a second one for the same trading day.

        The lease is checked in the same transaction as the insert, so a process whose lease
        expired while it was deciding to run cannot open a run under it.
        """
        run = JobRun(
            idempotency_key=trading_day_key(job_id, session),
            job_id=job_id,
            session=session,
            status="running",
            owner=owner,
            started_at=now,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            held = connection.execute(
                "SELECT 1 FROM scheduled_jobs WHERE job_id = ? AND lease_owner = ? "
                "AND lease_expires_at > ?",
                (job_id, owner, now.isoformat()),
            ).fetchone()
            if held is None:
                connection.execute("ROLLBACK")
                raise LeaseNotHeldError(job_id=job_id, owner=owner)
            try:
                connection.execute(
                    "INSERT INTO job_runs (idempotency_key, job_id, session, status, owner, "
                    "started_at, finished_at, error_type) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (
                        run.idempotency_key,
                        run.job_id,
                        run.session.isoformat(),
                        run.status,
                        run.owner,
                        run.started_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise JobAlreadyRanError(job_id=job_id, session=session) from error
            connection.execute("COMMIT")
        return run

    def finish_session(
        self,
        job_id: str,
        session: date,
        *,
        owner: str,
        now: datetime,
        status: Literal["succeeded", "failed"],
        error_type: str | None = None,
        next_fire_time: datetime | None = None,
    ) -> JobRun:
        """Close the run for `session` and, on success, advance the job past it.

        The run's terminal state and `last_fired_session` move in **one** transaction. Split
        across two, a crash between them leaves a succeeded run the job does not know it ran,
        and the next wake-up asks for the same session and meets `JobAlreadyRanError` -- a job
        permanently stuck one session behind, which is precisely the failure a durable
        scheduler exists to not have.

        A failed run deliberately does **not** advance `last_fired_session`, so the session is
        still owed; what stops it looping is that the run row is already there, so a retry is
        an explicit `retry_session` rather than an accident.
        """
        finished = JobRun(
            idempotency_key=trading_day_key(job_id, session),
            job_id=job_id,
            session=session,
            status=status,
            owner=owner,
            started_at=now,
            finished_at=now,
            error_type=error_type,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE job_runs SET status = ?, finished_at = ?, error_type = ? "
                "WHERE idempotency_key = ? AND owner = ? AND status = 'running'",
                (
                    finished.status,
                    now.isoformat(),
                    error_type,
                    finished.idempotency_key,
                    owner,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise JobStoreError(
                    f"no running run of {job_id!r} for {session.isoformat()} is held by "
                    f"{owner!r}: start it before finishing it"
                )
            if finished.status == "succeeded":
                connection.execute(
                    "UPDATE scheduled_jobs SET last_fired_session = ?, next_fire_time = ?, "
                    "updated_at = ? WHERE job_id = ?",
                    (
                        session.isoformat(),
                        _as_text(next_fire_time),
                        now.isoformat(),
                        job_id,
                    ),
                )
            connection.execute("COMMIT")
        stored = self.run_for(job_id, session)
        assert stored is not None  # the UPDATE above matched it
        return stored

    def advance_past(
        self,
        job_id: str,
        session: date,
        *,
        owner: str,
        now: datetime,
        next_fire_time: datetime | None = None,
    ) -> ScheduledJob:
        """Move `last_fired_session` to `session` without recording a run for it.

        What `CatchUpPolicy.SKIP_MISSED` is made of. It writes no `job_runs` row on purpose, so
        `runs()` keeps meaning "what this job did" rather than "what this job was scheduled
        for" -- the distinction an operator needs at the moment a skip turns out to have
        mattered.

        Guarded by the lease and by `session` moving the job **forward**: rewinding
        `last_fired_session` would make an already-run session owed again, and it would then
        meet `JobAlreadyRanError` for ever.
        """
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE scheduled_jobs SET last_fired_session = ?, next_fire_time = ?, "
                "updated_at = ? WHERE job_id = ? AND lease_owner = ? AND lease_expires_at > ? "
                "AND (last_fired_session IS NULL OR last_fired_session < ?)",
                (
                    session.isoformat(),
                    _as_text(next_fire_time),
                    now.isoformat(),
                    job_id,
                    owner,
                    now.isoformat(),
                    session.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                stored = self.get(job_id)
                if stored is None:
                    raise JobStoreError(
                        f"no job is registered under {job_id!r}, so there is nothing to advance"
                    )
                if not stored.lease_is_held_at(now) or stored.lease_owner != owner:
                    raise LeaseNotHeldError(job_id=job_id, owner=owner)
                raise JobStoreError(
                    f"cannot advance {job_id!r} to {session.isoformat()}: it is not later than "
                    "the session already recorded, and rewinding would make a session that has "
                    "already run owed again"
                )
            advanced = self._read(connection, job_id)
            connection.execute("COMMIT")
        assert advanced is not None  # the UPDATE above matched it
        return advanced

    def run_for(self, job_id: str, session: date) -> JobRun | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT idempotency_key, job_id, session, status, owner, started_at, "
                "finished_at, error_type FROM job_runs WHERE idempotency_key = ?",
                (trading_day_key(job_id, session),),
            ).fetchone()
        return None if row is None else _run_from_row(row)

    def runs(self, job_id: str) -> tuple[JobRun, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT idempotency_key, job_id, session, status, owner, started_at, "
                "finished_at, error_type FROM job_runs WHERE job_id = ? ORDER BY session",
                (job_id,),
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    # --- reading -------------------------------------------------------------------------

    def _read(self, connection: sqlite3.Connection, job_id: str) -> ScheduledJob | None:
        row = connection.execute(
            "SELECT job_id, catch_up, last_fired_session, next_fire_time, lease_owner, "
            "lease_expires_at, created_at, updated_at FROM scheduled_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return None if row is None else _job_from_row(row)


def _as_text(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _job_from_row(row: Sequence[object]) -> ScheduledJob:
    return read_versioned(
        SCHEDULED_JOB_VERSIONS,
        ScheduledJob(
            job_id=str(row[0]),
            catch_up=CatchUpPolicy(str(row[1])),
            last_fired_session=None if row[2] is None else date.fromisoformat(str(row[2])),
            next_fire_time=None if row[3] is None else datetime.fromisoformat(str(row[3])),
            lease_owner=None if row[4] is None else str(row[4]),
            lease_expires_at=None if row[5] is None else datetime.fromisoformat(str(row[5])),
            created_at=datetime.fromisoformat(str(row[6])),
            updated_at=datetime.fromisoformat(str(row[7])),
        ).model_dump_json(),
    )


def _run_from_row(row: Sequence[object]) -> JobRun:
    return read_versioned(
        JOB_RUN_VERSIONS,
        JobRun(
            idempotency_key=str(row[0]),
            job_id=str(row[1]),
            session=date.fromisoformat(str(row[2])),
            status=str(row[3]),  # type: ignore[arg-type]
            owner=str(row[4]),
            started_at=datetime.fromisoformat(str(row[5])),
            finished_at=None if row[6] is None else datetime.fromisoformat(str(row[6])),
            error_type=None if row[7] is None else str(row[7]),
        ).model_dump_json(),
    )
