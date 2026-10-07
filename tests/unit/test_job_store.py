"""The durable job table: lease, crash recovery, and the per-trading-day idempotency key.

`V2-P5-010`, closing audit `F98` ("**no scheduling primitive at all**"; `grep
cron|scheduler|apscheduler|celery` returns nothing repository-wide).

Every case here drives `SQLiteJobStore` against a **real SQLite file**, because every guarantee
under test is one SQLite provides and an in-memory stand-in would not: `PRIMARY KEY` uniqueness
on the idempotency key, `BEGIN IMMEDIATE` serialising two writers, and `rowcount` reporting who
won a conditional `UPDATE`. A fake store that asserted these would be asserting its own
implementation.

**Two independent `SQLiteJobStore` instances over one path is how "two processes" is expressed
here.** They hold separate connections to the same file, which is exactly the arrangement the
lease exists for; a single instance used twice would share a connection and prove nothing about
concurrency.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.job_contracts import CatchUpPolicy, ScheduledJob, trading_day_key
from openalpha_cn.storage.jobs import (
    JobAlreadyRanError,
    JobStoreError,
    LeaseNotHeldError,
    SQLiteJobStore,
)

NOW: Final[datetime] = datetime(2026, 8, 24, 8, 30, tzinfo=UTC)
SESSION: Final[date] = date(2026, 8, 24)
LEASE: Final[timedelta] = timedelta(minutes=15)


def _job(
    job_id: str = "daily-panel-build", *, catch_up: CatchUpPolicy | None = None
) -> ScheduledJob:
    return ScheduledJob(
        job_id=job_id,
        catch_up=catch_up if catch_up is not None else CatchUpPolicy.RUN_EACH_MISSED,
        created_at=NOW,
        updated_at=NOW,
    )


def _store(tmp_path: Path) -> SQLiteJobStore:
    return SQLiteJobStore(tmp_path / "state.sqlite3")


def test_a_second_registration_returns_the_stored_job_rather_than_resetting_its_progress(
    tmp_path: Path,
) -> None:
    """A process restart re-declares its schedule; that must not re-run a month of sessions."""
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)
    store.finish_session(
        "daily-panel-build", SESSION, owner="worker-a", now=NOW, status="succeeded"
    )

    again = store.register(_job())

    assert again.last_fired_session == SESSION, (
        "re-registering reset the job's progress, so every restart would re-run every session "
        "since the last one"
    )


def test_two_processes_cannot_hold_the_same_lease(tmp_path: Path) -> None:
    """The whole point of a lease. Two stores, two connections, one file."""
    first = _store(tmp_path)
    second = SQLiteJobStore(tmp_path / "state.sqlite3")
    first.register(_job())

    won = first.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    lost = second.claim("daily-panel-build", owner="worker-b", now=NOW, lease_for=LEASE)

    assert won is not None and won.lease_owner == "worker-a"
    assert lost is None, "both processes were told they hold the job"
    assert second.get("daily-panel-build").lease_owner == "worker-a"  # type: ignore[union-attr]


def test_an_expired_lease_is_reclaimed_without_anything_sweeping_for_it(tmp_path: Path) -> None:
    """Crash recovery, as a property of every claim rather than a maintenance task.

    A process that died holding the lease left `lease_owner` set and never released it. Nothing
    in this repository scans for that -- a sweeper would itself need scheduling -- so the
    recovery has to be that the next claim takes an expired lease as readily as an absent one.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="crashed", now=NOW, lease_for=LEASE)

    too_early = store.claim(
        "daily-panel-build",
        owner="worker-b",
        now=NOW + LEASE - timedelta(seconds=1),
        lease_for=LEASE,
    )
    recovered = store.claim("daily-panel-build", owner="worker-b", now=NOW + LEASE, lease_for=LEASE)

    assert too_early is None, "a live lease was taken from the process that still holds it"
    assert recovered is not None and recovered.lease_owner == "worker-b"


def test_a_second_run_of_the_same_trading_session_is_refused_by_the_primary_key(
    tmp_path: Path,
) -> None:
    """The per-trading-day idempotency guarantee, and it is structural rather than checked.

    `idempotency_key` is the `PRIMARY KEY`, so the refusal comes from SQLite and not from a
    `SELECT` this store performs first -- which two processes could both pass before either
    inserted. The two stores here are two processes, and the second one is holding a valid
    lease it took after the first one's expired: the *only* thing stopping it re-running the
    session is the key.
    """
    first = _store(tmp_path)
    second = SQLiteJobStore(tmp_path / "state.sqlite3")
    first.register(_job())
    first.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    first.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    later = NOW + LEASE
    second.claim("daily-panel-build", owner="worker-b", now=later, lease_for=LEASE)
    with pytest.raises(JobAlreadyRanError) as refusal:
        second.start_session("daily-panel-build", SESSION, owner="worker-b", now=later)

    assert refusal.value.session == SESSION
    assert "2026-08-24" in str(refusal.value)
    assert len(second.runs("daily-panel-build")) == 1


def test_a_run_cannot_be_opened_under_a_lease_that_has_expired(tmp_path: Path) -> None:
    """The lease is checked in the insert's own transaction, not before it.

    A process that decided to run at 08:30 and got round to it at 09:00 no longer holds the
    job, and the session it was about to start may already be running somewhere else.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)

    with pytest.raises(LeaseNotHeldError):
        store.start_session(
            "daily-panel-build", SESSION, owner="worker-a", now=NOW + LEASE + timedelta(seconds=1)
        )

    assert store.runs("daily-panel-build") == ()


def test_a_succeeded_run_and_the_job_it_advances_move_together(tmp_path: Path) -> None:
    """One transaction, because a crash between two would strand the job one session behind.

    Split, the failure is specific and permanent: the run row says `succeeded` while
    `last_fired_session` still points at the day before, so the next wake-up asks for the same
    session and meets `JobAlreadyRanError` -- for ever.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    finished = store.finish_session(
        "daily-panel-build",
        SESSION,
        owner="worker-a",
        now=NOW + timedelta(minutes=1),
        status="succeeded",
        next_fire_time=datetime(2026, 8, 25, 8, 30, tzinfo=UTC),
    )
    job = store.get("daily-panel-build")

    assert finished.status == "succeeded"
    assert finished.finished_at == NOW + timedelta(minutes=1)
    assert job is not None
    assert job.last_fired_session == SESSION
    assert job.next_fire_time == datetime(2026, 8, 25, 8, 30, tzinfo=UTC)


def test_a_failed_run_leaves_the_session_owed(tmp_path: Path) -> None:
    """A job that advanced past a session it failed has silently skipped it.

    For `RUN_EACH_MISSED` -- a daily panel ingest -- that is a permanent hole in a
    point-in-time panel that nothing would ever report.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    failed = store.finish_session(
        "daily-panel-build",
        SESSION,
        owner="worker-a",
        now=NOW + timedelta(minutes=1),
        status="failed",
        error_type="ProviderAuthError",
    )
    job = store.get("daily-panel-build")

    assert failed.status == "failed"
    assert failed.error_type == "ProviderAuthError"
    assert job is not None
    assert job.last_fired_session is None, (
        "a failed run advanced the job, so the session it failed will never be asked for again"
    )


def test_finishing_a_run_nobody_started_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)

    with pytest.raises(JobStoreError, match="start it before finishing it"):
        store.finish_session(
            "daily-panel-build", SESSION, owner="worker-a", now=NOW, status="succeeded"
        )


def test_skipping_a_session_advances_the_job_without_inventing_a_run(tmp_path: Path) -> None:
    """`SKIP_MISSED` must leave `runs()` meaning "what this job did".

    Implemented as "write a succeeded run for each skipped session", the ledger would say the
    job ran on days it deliberately did not -- and an operator asking, after something went
    wrong, which sessions were actually processed would be told the schedule instead.
    """
    store = _store(tmp_path)
    store.register(_job(catch_up=CatchUpPolicy.SKIP_MISSED))
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)

    advanced = store.advance_past("daily-panel-build", date(2026, 8, 21), owner="worker-a", now=NOW)

    assert advanced.last_fired_session == date(2026, 8, 21)
    assert store.runs("daily-panel-build") == ()


def test_a_job_cannot_be_rewound_past_a_session_it_has_already_run(tmp_path: Path) -> None:
    """Rewinding makes an already-run session owed again, and it can never be satisfied.

    The run row is already there, so the next `start_session` for it raises
    `JobAlreadyRanError`; the job would ask for that session on every wake-up for ever.
    """
    store = _store(tmp_path)
    store.register(_job(catch_up=CatchUpPolicy.SKIP_MISSED))
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.advance_past("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    with pytest.raises(JobStoreError, match="rewinding"):
        store.advance_past("daily-panel-build", date(2026, 8, 21), owner="worker-a", now=NOW)

    assert store.get("daily-panel-build").last_fired_session == SESSION  # type: ignore[union-attr]


def test_releasing_a_lease_somebody_else_now_holds_is_refused(tmp_path: Path) -> None:
    """A late release that succeeded would hand the job away from its current owner."""
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="crashed", now=NOW, lease_for=LEASE)
    store.claim("daily-panel-build", owner="worker-b", now=NOW + LEASE, lease_for=LEASE)

    with pytest.raises(LeaseNotHeldError):
        store.release("daily-panel-build", owner="crashed", now=NOW + LEASE)

    assert store.get("daily-panel-build").lease_owner == "worker-b"  # type: ignore[union-attr]


def test_the_idempotency_key_cannot_be_ambiguous_between_two_jobs(tmp_path: Path) -> None:
    """`@` is excluded from `job_id` so no two `(job, session)` pairs derive the same key.

    Without the exclusion, `job_id="a@2026-08-24"` on session `2026-08-25` and `job_id="a"` on
    session... would not collide for `@`, but they would for a separator that occurs in a date.
    The refusal is what makes the key's uniqueness a statement about jobs rather than about
    string concatenation.
    """
    with pytest.raises(ValueError, match="may not contain '@'"):
        _job("daily@build")

    assert trading_day_key("daily-build", SESSION) == "daily-build@2026-08-24"


def test_every_stored_column_comes_back_on_the_field_it_was_written_to(tmp_path: Path) -> None:
    """A column-order round trip, and it is here because a mutation sweep asked for it.

    `_job_from_row` and `_run_from_row` address a positional `SELECT` by index. Sweeping index
    constants over the store left four alive -- `job_id` reading `catch_up`'s column,
    `created_at` reading `updated_at`'s, `JobRun.owner` reading `started_at`'s, and the null
    branch of `next_fire_time` answering `0` (which pydantic parses as a 1970 timestamp rather
    than refusing) -- because every case in this file asserted the two or three fields it cared
    about and none asserted the row.

    So this asserts the row. The values are deliberately all *different* from each other: equal
    timestamps on `created_at` and `updated_at` would make swapping them invisible, which is the
    same shape as the defect.
    """
    store = _store(tmp_path)
    created = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    updated = datetime(2026, 8, 2, 3, 4, tzinfo=UTC)
    store.register(
        ScheduledJob(
            job_id="daily-panel-build",
            catch_up=CatchUpPolicy.SKIP_MISSED,
            created_at=created,
            updated_at=updated,
        )
    )

    job = store.get("daily-panel-build")

    assert job is not None
    assert job.job_id == "daily-panel-build"
    assert job.catch_up is CatchUpPolicy.SKIP_MISSED
    assert job.created_at == created
    assert job.updated_at == updated
    assert job.last_fired_session is None
    assert job.next_fire_time is None, (
        "an unset fire time came back as a value; a null read as 0 is a 1970 timestamp, and a "
        "poller comparing against it would think every job is overdue for ever"
    )
    assert job.lease_owner is None
    assert job.lease_expires_at is None

    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    started = NOW + timedelta(seconds=7)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=started)
    run = store.run_for("daily-panel-build", SESSION)

    assert run is not None
    assert run.idempotency_key == "daily-panel-build@2026-08-24"
    assert run.job_id == "daily-panel-build"
    assert run.session == SESSION
    assert run.status == "running"
    assert run.owner == "worker-a"
    assert run.started_at == started
    assert run.finished_at is None
    assert run.error_type is None


def test_advancing_a_job_this_process_does_not_hold_is_refused_as_a_lease_fault(
    tmp_path: Path,
) -> None:
    """`advance_past` distinguishes "not yours" from "that would rewind you".

    Both refusals are `JobStoreError`, so a test that only asserted the base class would let the
    two collapse into one -- and they need different remedies: claim the job, versus stop asking
    for a session you already ran. Sweeping the `is not None` guards in that branch left them
    alive until this existed.
    """
    store = _store(tmp_path)
    store.register(_job(catch_up=CatchUpPolicy.SKIP_MISSED))

    with pytest.raises(JobStoreError, match="nothing to advance"):
        store.advance_past("never-declared", SESSION, owner="worker-a", now=NOW)
    with pytest.raises(LeaseNotHeldError):
        store.advance_past("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    with pytest.raises(LeaseNotHeldError):
        store.advance_past("daily-panel-build", SESSION, owner="worker-b", now=NOW)
    with pytest.raises(LeaseNotHeldError):
        store.advance_past(
            "daily-panel-build", SESSION, owner="worker-a", now=NOW + LEASE + timedelta(seconds=1)
        )

    assert store.get("daily-panel-build").last_fired_session is None  # type: ignore[union-attr]


def test_a_failed_session_can_be_retried_rather_than_owed_for_ever(tmp_path: Path) -> None:
    """`V2-P5-013`. `finish_session`'s docstring names `retry_session`; it did not exist.

    The two halves of this store's design meet here and, until this test, they met in a dead
    end. A **failed** run deliberately does not advance `last_fired_session`, so `due()` keeps
    owing the session -- that is right, the work did not happen. But `idempotency_key` is the
    `PRIMARY KEY`, so the row is already there and `start_session` answers `JobAlreadyRanError`
    for that session **for ever**. A job whose first attempt at a session failed could never
    reach another one: not that session, and not the sessions after it, because
    `last_fired_session` is what `due()` counts forward from.

    Nothing had measured it because nothing outside these tests had ever called this store
    (`V2-P5-010` recorded exactly that: "没有 CLI 命令、没有 REST 路由、不在 `build_storage` 里").
    `openalpha jobs run` is the caller that reaches it on an ordinary Tuesday, because the work
    it runs -- a point-in-time panel health report -- legitimately fails on a session whose data
    has not landed.

    The last two assertions are what make this test able to fail for the right reason. Asserting
    only that `retry_session` returns a `running` run would pass against an implementation that
    simply deleted the row, which would throw away the record of the failed attempt *and* the
    idempotency guarantee with it. So the run's `started_at` is required to move to the retry's
    instant while the session it addresses does not, and the job must then be able to finish and
    advance past it.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)
    store.finish_session(
        "daily-panel-build",
        SESSION,
        owner="worker-a",
        now=NOW,
        status="failed",
        error_type="not_yet_knowable",
    )

    assert store.get("daily-panel-build").last_fired_session is None  # type: ignore[union-attr]
    with pytest.raises(JobAlreadyRanError):
        store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    later = NOW + timedelta(hours=1)
    store.claim("daily-panel-build", owner="worker-a", now=later, lease_for=LEASE)
    reopened = store.retry_session("daily-panel-build", SESSION, owner="worker-a", now=later)

    assert reopened.status == "running"
    assert reopened.started_at == later
    assert reopened.finished_at is None
    assert reopened.error_type is None
    assert reopened.idempotency_key == trading_day_key("daily-panel-build", SESSION)

    store.finish_session(
        "daily-panel-build", SESSION, owner="worker-a", now=later, status="succeeded"
    )
    assert store.get("daily-panel-build").last_fired_session == SESSION  # type: ignore[union-attr]


def test_a_run_still_in_flight_is_not_reopened_by_a_retry(tmp_path: Path) -> None:
    """A retry reopens a *terminal* run and never a `running` one.

    Without this the guarantee `start_session` provides leaks out of the side door: two
    processes could hold one session open at once by having the second call `retry_session` on
    the first's live run, and the `PRIMARY KEY` would not stop it because no second row is being
    inserted. The lease is checked too, and separately, because an expired lease and a run that
    is already running are different faults with different remedies.
    """
    store = _store(tmp_path)
    store.register(_job())
    store.claim("daily-panel-build", owner="worker-a", now=NOW, lease_for=LEASE)
    store.start_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    with pytest.raises(JobStoreError, match="already running"):
        store.retry_session("daily-panel-build", SESSION, owner="worker-a", now=NOW)

    store.finish_session(
        "daily-panel-build",
        SESSION,
        owner="worker-a",
        now=NOW,
        status="failed",
        error_type="boom",
    )
    with pytest.raises(LeaseNotHeldError):
        store.retry_session("daily-panel-build", SESSION, owner="worker-b", now=NOW)
    with pytest.raises(LeaseNotHeldError):
        # An EXPIRED lease, held by the right owner. Distinct from the case above and not a
        # duplicate of it: that one fails the `lease_owner = ?` half of the guard and this one
        # fails the `lease_expires_at > ?` half, and the second half is a *string* comparison
        # against an ISO-8601 column -- so it is also what fails if the instant is bound as a
        # `datetime` and SQLite's default adapter writes a space where the stored rows carry a
        # `T`, which sorts the other way round.
        store.retry_session(
            "daily-panel-build",
            SESSION,
            owner="worker-a",
            now=NOW + LEASE + timedelta(seconds=1),
        )
    with pytest.raises(JobStoreError, match="no run of"):
        store.retry_session("daily-panel-build", date(2026, 8, 21), owner="worker-a", now=NOW)
