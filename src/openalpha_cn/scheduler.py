"""Trading-day scheduling: what a job owes right now, and the lease it runs under.

`V2-P5-010`, the behavioural half. `job_contracts.py` holds the durable shapes and
`storage/jobs.py` holds the table; this is where they meet a `TradingCalendar` and a clock.

## The design decision worth arguing with: what "due" means

A conventional scheduler stores a next-fire-time and fires when the wall clock passes it. That
is wrong here for a reason specific to this repository, and getting it wrong is how a
point-in-time panel acquires a hole.

A stored fire time is a *derived* fact -- derived from a calendar that changes. The exchange
announces holidays, moves a session for a national holiday make-up day, and cancels one for
weather. A fire time computed under last month's calendar and stored is a restatement of a rule
whose inputs have since moved, and nothing would ever notice: the job fires, the session is
closed, the run succeeds on nothing.

So `due()` does not read `next_fire_time` at all. It asks the two questions whose answers can
only come from the calendar and the ledger:

1. **Which sessions have published by now** -- `panel_ingest.newest_published_session`, the one
   function that owns the 16:30 `DAILY_AVAILABILITY_TIME` rule (`V2-P4-063`, `V2-P4-114`).
2. **Which of them has this job already run** -- `last_fired_session` on the row.

The sessions strictly between those two, taken off the calendar, are what the job owes.
`next_fire_time` is still written -- `storage/jobs.py` explains why a poller wants an indexed
column -- but it is a hint recomputed from the calendar on every advance, never an answer.

## Catch-up

`CatchUpPolicy.RUN_EACH_MISSED` returns every owed session in ascending order; a daily panel
ingest that missed three sessions has three holes and must fill all three.
`CatchUpPolicy.SKIP_MISSED` returns only the newest, and advances the job past the rest, which
is right for a job whose output is a snapshot of *now*.

**`SKIP_MISSED` records the skipped sessions as skipped rather than pretending they ran.** The
job's `last_fired_session` moves to the newest owed session and the skipped ones simply have no
row in `job_runs` -- so "which sessions did this job actually run" stays answerable, which it
would not be if skipping were implemented by writing a succeeded run for each.

## Crash recovery

There is no recovery pass. A process that died holding a lease left `lease_owner` set and
`lease_expires_at` in the past, and `SQLiteJobStore.claim` takes an expired lease exactly as
readily as an absent one. A run it left `running` is still `running`, so `start_session` for
that session raises `JobAlreadyRanError` -- the correct answer, since re-running an unfinished
job is a decision (`retry`) and not a default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from openalpha_cn.domain.trading_calendar import CalendarHorizonError, TradingCalendar
from openalpha_cn.job_contracts import CatchUpPolicy, JobRun, ScheduledJob
from openalpha_cn.panel_ingest import newest_published_session, session_publication_instant
from openalpha_cn.storage.jobs import SQLiteJobStore

DEFAULT_LEASE = timedelta(minutes=15)
"""How long a claimed lease stands before another process may take it.

Long enough that an ordinary daily ingest finishes inside it, short enough that a machine that
lost power at 02:00 is not still nominally holding the job at 03:00. A lease is not a timeout on
the work -- nothing kills a slow run -- it is a bound on how long a *dead* process blocks one.
"""

MAX_CATCH_UP_SESSIONS: Final[int] = 250
"""Roughly one trading year: the most sessions `due()` will name in a single answer.

Not a policy about work, a bound on an answer. A job registered with no `last_fired_session`
against a decade-long calendar would otherwise be told it owes 2,400 sessions, and the caller
that acted on that would spend a day catching up a schedule nobody meant to backfill. A job
that genuinely owes more than a year is a decision for an operator, not for a poller waking at
16:35.
"""


class ScheduleHorizonError(ValueError):
    """The calendar this scheduler holds cannot answer for the instant it was asked about."""


@dataclass(frozen=True, slots=True)
class DueSessions:
    """What one job owes at one instant, and what it will be advanced past.

    `owed` is what the caller should run, in ascending order. `skipped` is what
    `CatchUpPolicy.SKIP_MISSED` is dropping, carried rather than discarded because a caller
    that logs "ran 2026-08-24" and never mentions the three sessions it decided not to run has
    made the policy invisible at exactly the moment it mattered.
    """

    job_id: str
    owed: tuple[date, ...]
    skipped: tuple[date, ...]
    published_through: date

    def __bool__(self) -> bool:
        return bool(self.owed)


class TradingDayScheduler:
    """Binds a durable job table to a trading calendar and a clock."""

    def __init__(
        self,
        *,
        store: SQLiteJobStore,
        calendar: TradingCalendar,
        clock: Callable[[], datetime],
        owner: str,
        lease_for: timedelta = DEFAULT_LEASE,
    ) -> None:
        self.store = store
        self.calendar = calendar
        self.clock = clock
        self.owner = owner
        self.lease_for = lease_for

    # --- what is owed ---------------------------------------------------------------------

    def due(self, job_id: str, *, now: datetime | None = None) -> DueSessions:
        """The trading sessions `job_id` owes at `now`, under its own catch-up policy.

        Raises `KeyError` for an unregistered job -- a scheduler asked about a schedule it does
        not hold has been given the wrong name, and answering "nothing is due" would look
        identical to a correctly idle job.
        """
        instant = now if now is not None else self.clock()
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(f"no job is registered under {job_id!r}")
        try:
            published_through = newest_published_session(self.calendar, as_of=instant)
        except CalendarHorizonError as error:
            raise ScheduleHorizonError(
                f"the calendar this scheduler holds ({self.calendar.horizon}) cannot say which "
                f"session had published at {instant.isoformat()}; load a calendar that covers "
                "the instant before asking what is due"
            ) from error

        candidates = self._sessions_after(job.last_fired_session, published_through)
        if not candidates:
            return DueSessions(
                job_id=job_id, owed=(), skipped=(), published_through=published_through
            )
        if job.catch_up is CatchUpPolicy.RUN_EACH_MISSED:
            return DueSessions(
                job_id=job_id,
                owed=candidates,
                skipped=(),
                published_through=published_through,
            )
        return DueSessions(
            job_id=job_id,
            owed=candidates[-1:],
            skipped=candidates[:-1],
            published_through=published_through,
        )

    def _sessions_after(self, last_fired: date | None, published_through: date) -> tuple[date, ...]:
        """Open sessions in `(last_fired, published_through]`, newest-bounded and capped.

        A job with no `last_fired_session` has never run, and the session it owes is the newest
        published one and not the calendar's first -- registering a schedule today is not a
        request to backfill the decade the calendar happens to cover. That is why the `None`
        case returns one session rather than every session in the horizon.

        `published_through` is not re-checked for being an open session: `newest_published_
        session` walks back to one, so a guard here would be unreachable code claiming to
        handle a case its own caller has already handled.
        """
        if last_fired is None:
            return (published_through,)
        if last_fired >= published_through:
            return ()
        try:
            after = self.calendar.trading_days_between(last_fired, published_through)
        except CalendarHorizonError as error:
            raise ScheduleHorizonError(
                f"this job last ran on {last_fired.isoformat()} and the calendar this "
                f"scheduler holds ({self.calendar.horizon}) does not reach back that far, so "
                "the sessions between then and now are unknown rather than absent. Answering "
                "with only the sessions this calendar can see would silently skip the rest; "
                "load a calendar covering the gap"
            ) from error
        owed = tuple(session for session in after if session > last_fired)
        return owed[-MAX_CATCH_UP_SESSIONS:]

    # --- running one -----------------------------------------------------------------------

    def claim(self, job_id: str, *, now: datetime | None = None) -> ScheduledJob | None:
        instant = now if now is not None else self.clock()
        return self.store.claim(job_id, owner=self.owner, now=instant, lease_for=self.lease_for)

    def release(self, job_id: str, *, now: datetime | None = None) -> ScheduledJob:
        instant = now if now is not None else self.clock()
        return self.store.release(job_id, owner=self.owner, now=instant)

    def start(self, job_id: str, session: date, *, now: datetime | None = None) -> JobRun:
        instant = now if now is not None else self.clock()
        return self.store.start_session(job_id, session, owner=self.owner, now=instant)

    def succeed(self, job_id: str, session: date, *, now: datetime | None = None) -> JobRun:
        """Close `session` as succeeded and advance the job to the next session's fire time."""
        instant = now if now is not None else self.clock()
        return self.store.finish_session(
            job_id,
            session,
            owner=self.owner,
            now=instant,
            status="succeeded",
            next_fire_time=self.next_fire_time_after(session),
        )

    def fail(
        self, job_id: str, session: date, *, error_type: str, now: datetime | None = None
    ) -> JobRun:
        instant = now if now is not None else self.clock()
        return self.store.finish_session(
            job_id,
            session,
            owner=self.owner,
            now=instant,
            status="failed",
            error_type=error_type,
        )

    def retry(self, job_id: str, session: date, *, now: datetime | None = None) -> JobRun:
        """Reopen a finished attempt at `session` so it can be run again (`V2-P5-013`).

        A failed run leaves the session owed and its row in place, so without this the job is
        stuck on it -- and on every session after it, because `due()` counts forward from
        `last_fired_session`. Stated by a caller rather than taken automatically: a session that
        fails for a reason time does not fix would otherwise be retried on every wake-up.
        """
        instant = now if now is not None else self.clock()
        return self.store.retry_session(job_id, session, owner=self.owner, now=instant)

    def skip_to(self, job_id: str, session: date, *, now: datetime | None = None) -> ScheduledJob:
        """Advance a `SKIP_MISSED` job past `session` without recording a run for it.

        Deliberately not implemented as "write a succeeded run": a skipped session that looked
        like a run would make `runs()` -- the only record of what this job actually did -- a
        record of what it was scheduled for instead.
        """
        instant = now if now is not None else self.clock()
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(f"no job is registered under {job_id!r}")
        return self.store.advance_past(
            job_id,
            session,
            owner=self.owner,
            now=instant,
            next_fire_time=self.next_fire_time_after(session),
        )

    def next_fire_time_after(self, session: date) -> datetime | None:
        """When the session after `session` publishes, or `None` past the calendar's horizon.

        `None` rather than an extrapolation: the calendar is a *published* window, and guessing
        that the next session is the next weekday is the same class of mistake as restating the
        16:30 rule. A job whose next fire time is unknown is one whose calendar needs extending,
        and `due()` will still answer correctly for it because `due()` never reads this field.
        """
        try:
            following = self.calendar.next_trading_day(session)
        except CalendarHorizonError:
            return None
        return session_publication_instant(following)
