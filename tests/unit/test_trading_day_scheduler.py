"""What a job owes, decided by the calendar rather than by a stored fire time (`V2-P5-010`).

The design claim under test is the one worth doubting: `due()` deliberately does **not** read
`next_fire_time`. It asks `panel_ingest.newest_published_session` -- the one function that owns
the 16:30 `DAILY_AVAILABILITY_TIME` rule, after `V2-P4-063` found that rule restated three times
with two of them disagreeing and `V2-P4-114` found a fourth -- and compares its answer against
the job's `last_fired_session`.

`test_a_stale_stored_fire_time_does_not_decide_anything` is the assertion that separates that
design from the conventional one: a fire time is written into the row that says the job is due
tomorrow, and the scheduler still owes today's session, because the calendar says so.

The calendar these tests hold is a weekday calendar with a deliberate four-day close over
2026-10-01..04, which is what makes "the missed sessions" and "the missed *days*" different
answers. A weekday-only fixture would let a scheduler that counted `timedelta(days=1)` pass.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.job_contracts import CatchUpPolicy, ScheduledJob
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.scheduler import (
    MAX_CATCH_UP_SESSIONS,
    ScheduleHorizonError,
    TradingDayScheduler,
)
from openalpha_cn.storage.jobs import SQLiteJobStore

EXCHANGE: Final[str] = "SSE"
HOLIDAY: Final[frozenset[date]] = frozenset(
    {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)}
)
"""A national-day close that swallows two whole trading weeks' worth of weekdays.

2026-10-01 is a Thursday, so with the weekend this closes Thursday through the following
Tuesday: six consecutive calendar days with no session, of which four are weekdays. A
scheduler that walked `timedelta(days=1)` instead of asking the calendar answers six here and
four is correct.
"""

NOW: Final[datetime] = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
"""20:00 Asia/Shanghai on Monday 2026-08-24: past 16:30, so that day's session has published."""


def _calendar():
    start = date(2026, 1, 1)
    return build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(
                calendar_date=start + timedelta(days=offset),
                is_trading=(start + timedelta(days=offset)).weekday() < 5
                and (start + timedelta(days=offset)) not in HOLIDAY,
            )
            for offset in range(365)
        ],
    )


def _scheduler(
    tmp_path: Path,
    *,
    catch_up: CatchUpPolicy,
    last_fired: date | None = None,
    next_fire_time: datetime | None = None,
    owner: str = "worker-a",
) -> TradingDayScheduler:
    store = SQLiteJobStore(tmp_path / "state.sqlite3")
    store.register(
        ScheduledJob(
            job_id="daily-panel-build",
            catch_up=catch_up,
            last_fired_session=last_fired,
            next_fire_time=next_fire_time,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return TradingDayScheduler(store=store, calendar=_calendar(), clock=lambda: NOW, owner=owner)


def test_a_job_that_has_run_today_owes_nothing(tmp_path: Path) -> None:
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2026, 8, 24)
    )

    due = scheduler.due("daily-panel-build")

    assert due.owed == ()
    assert not due
    assert due.published_through == date(2026, 8, 24)


def test_a_job_before_the_publication_instant_owes_the_previous_session(tmp_path: Path) -> None:
    """The calendar clock, not the wall clock: 09:00 Shanghai on a trading day owes Friday.

    A scheduler that fired on "a new calendar day has started" would ask for Monday's session
    seven and a half hours before it publishes, which is `V2-P4-077`'s defect wearing a
    scheduler's hat.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2026, 8, 20)
    )
    before_publication = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai

    due = scheduler.due("daily-panel-build", now=before_publication)

    assert due.published_through == date(2026, 8, 21), "Friday, because Monday has not published"
    assert due.owed == (date(2026, 8, 21),)


def test_run_each_missed_names_every_session_in_the_gap_and_not_every_day(
    tmp_path: Path,
) -> None:
    """A daily ingest that missed the national-day close has four holes, not six.

    The window is deliberately laid across `HOLIDAY`: 2026-09-30 through 2026-10-09 is ten
    calendar days and eight weekdays, of which the calendar opens four. Anything that counted
    days rather than asking the calendar gets a different answer here.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2026, 9, 30)
    )
    after_the_close = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    due = scheduler.due("daily-panel-build", now=after_the_close)

    assert due.owed == (
        date(2026, 10, 7),
        date(2026, 10, 8),
        date(2026, 10, 9),
    ), due.owed
    assert due.skipped == ()
    assert date(2026, 10, 1) not in due.owed, "a closed session was owed"


def test_skip_missed_owes_only_the_newest_and_names_what_it_dropped(tmp_path: Path) -> None:
    """The dropped sessions are carried, not discarded.

    A caller that logs "ran 2026-10-09" and never mentions the two sessions it decided not to
    run has made the policy invisible at exactly the moment it mattered.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.SKIP_MISSED, last_fired=date(2026, 9, 30)
    )
    after_the_close = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    due = scheduler.due("daily-panel-build", now=after_the_close)

    assert due.owed == (date(2026, 10, 9),)
    assert due.skipped == (date(2026, 10, 7), date(2026, 10, 8))


def test_a_stale_stored_fire_time_does_not_decide_anything(tmp_path: Path) -> None:
    """The design claim, made falsifiable.

    The row is written with a `next_fire_time` a week in the future -- what a scheduler that
    trusted the stored value would wait for -- while the calendar says a session has published
    that this job has not run. A conventional fire-time scheduler answers "nothing due"; this
    one answers with the session, because the calendar is the only thing that can have changed
    since that fire time was computed.
    """
    scheduler = _scheduler(
        tmp_path,
        catch_up=CatchUpPolicy.RUN_EACH_MISSED,
        last_fired=date(2026, 8, 21),
        next_fire_time=datetime(2026, 8, 31, 8, 30, tzinfo=UTC),
    )

    due = scheduler.due("daily-panel-build")

    assert due.owed == (date(2026, 8, 24),), (
        "the stored fire time decided the answer, so a calendar change after it was written "
        "would be invisible"
    )


def test_a_job_that_has_never_run_owes_one_session_and_not_the_whole_horizon(
    tmp_path: Path,
) -> None:
    """Registering a schedule today is not a request to backfill the year the calendar covers."""
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=None)

    due = scheduler.due("daily-panel-build")

    assert due.owed == (date(2026, 8, 24),)


def test_a_catch_up_longer_than_a_trading_year_is_capped(tmp_path: Path) -> None:
    """A bound on the answer, not a policy about work.

    Told it owes 2,400 sessions, a poller waking at 16:35 would spend a day backfilling a
    schedule nobody meant to backfill. The cap names the newest sessions, because those are
    the ones a caller can still do something about.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2026, 1, 2)
    )
    late = datetime(2026, 12, 30, 12, 0, tzinfo=UTC)
    uncapped = len(
        [
            session
            for session in _calendar().trading_days_between(date(2026, 1, 2), date(2026, 12, 30))
            if session > date(2026, 1, 2)
        ]
    )
    assert uncapped == 254, (
        f"the fixture stopped exceeding the cap: {uncapped} sessions against a cap of "
        f"{MAX_CATCH_UP_SESSIONS}, so this test would pass with no cap at all"
    )

    due = scheduler.due("daily-panel-build", now=late)

    assert len(due.owed) == MAX_CATCH_UP_SESSIONS, len(due.owed)
    assert due.owed[-1] == due.published_through, "the cap dropped the newest sessions"
    assert due.owed[0] > date(2026, 1, 5), "the cap kept the oldest sessions instead"


def test_an_instant_outside_the_calendars_horizon_is_named_rather_than_answered(
    tmp_path: Path,
) -> None:
    """Answering the earliest session the calendar happens to hold would be a look-ahead.

    `newest_published_session` raises `CalendarHorizonError` for exactly this, and the
    scheduler renames it rather than letting a caller believe a calendar it never loaded said
    something.
    """
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED)

    with pytest.raises(ScheduleHorizonError, match="cannot say which session had published"):
        scheduler.due("daily-panel-build", now=datetime(2025, 6, 1, 12, 0, tzinfo=UTC))


def test_an_unregistered_job_is_a_key_error_and_not_an_empty_answer(tmp_path: Path) -> None:
    """ "Nothing is due" and "I have never heard of that job" must not look the same."""
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED)

    with pytest.raises(KeyError, match="no job is registered"):
        scheduler.due("a-job-nobody-declared")


def test_succeeding_a_session_advances_the_job_to_the_next_sessions_publication_instant(
    tmp_path: Path,
) -> None:
    """The stored hint is recomputed from the calendar, and it lands on a session.

    Over the national-day close the next fire time is six calendar days later, which is the
    property that says it came from `next_trading_day` and `session_publication_instant`
    rather than from adding a day.
    """
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED)
    at = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    scheduler.claim("daily-panel-build", now=at)
    scheduler.start("daily-panel-build", date(2026, 9, 30), now=at)

    scheduler.succeed("daily-panel-build", date(2026, 9, 30), now=at)
    job = scheduler.store.get("daily-panel-build")

    assert job is not None
    assert job.next_fire_time == session_publication_instant(date(2026, 10, 7))
    assert job.next_fire_time.date() == date(2026, 10, 7)  # type: ignore[union-attr]


def test_the_full_loop_leaves_exactly_one_run_per_owed_session(tmp_path: Path) -> None:
    """Claim, run each owed session, release -- and the ledger matches what was owed.

    The end-to-end shape a caller writes, driven once so the pieces are proven to compose
    rather than only to work alone.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2026, 9, 30)
    )
    at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    owed = scheduler.due("daily-panel-build", now=at).owed
    assert scheduler.claim("daily-panel-build", now=at) is not None
    for session in owed:
        scheduler.start("daily-panel-build", session, now=at)
        scheduler.succeed("daily-panel-build", session, now=at)
    scheduler.release("daily-panel-build", now=at)

    assert tuple(run.session for run in scheduler.store.runs("daily-panel-build")) == owed
    assert scheduler.due("daily-panel-build", now=at).owed == ()
    assert scheduler.store.get("daily-panel-build").lease_owner is None  # type: ignore[union-attr]


def test_a_job_older_than_the_loaded_calendar_is_refused_rather_than_partly_answered(
    tmp_path: Path,
) -> None:
    """The sessions before the calendar's horizon are unknown, not absent.

    A job that last ran two years ago against a calendar covering one is the case where
    answering "you owe the 250 sessions I can see" silently drops a year of them -- and for
    `RUN_EACH_MISSED`, a year of holes in a point-in-time panel that nothing would report.
    `TradingCalendar.trading_days_between` already refuses a partly-outside range for exactly
    this reason; this test pins that the scheduler passes the refusal on rather than widening
    it into an answer.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED, last_fired=date(2024, 6, 3)
    )

    with pytest.raises(ScheduleHorizonError, match="does not reach back that far"):
        scheduler.due("daily-panel-build")


def test_the_skip_missed_loop_advances_past_the_dropped_sessions_without_running_them(
    tmp_path: Path,
) -> None:
    """The other policy's full loop, and the ledger that must not lie about it afterwards.

    `skip_to` the last dropped session, then run the one that is owed. What makes this worth a
    test of its own rather than a variation of the `RUN_EACH_MISSED` one is the assertion at the
    end: `runs()` holds exactly one row, for the session that ran, and nothing for the two the
    policy dropped. Implemented as "write a succeeded run for each", every assertion above would
    still pass and this one would not.
    """
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.SKIP_MISSED, last_fired=date(2026, 9, 30)
    )
    at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    due = scheduler.due("daily-panel-build", now=at)
    assert scheduler.claim("daily-panel-build", now=at) is not None

    scheduler.skip_to("daily-panel-build", due.skipped[-1], now=at)
    scheduler.start("daily-panel-build", due.owed[0], now=at)
    scheduler.succeed("daily-panel-build", due.owed[0], now=at)

    assert tuple(run.session for run in scheduler.store.runs("daily-panel-build")) == (
        date(2026, 10, 9),
    ), "a session the policy dropped was recorded as having run"
    assert scheduler.due("daily-panel-build", now=at).owed == ()
    assert scheduler.store.list_jobs()[0].last_fired_session == date(2026, 10, 9)


def test_skipping_a_job_nobody_registered_is_a_key_error(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.SKIP_MISSED)

    with pytest.raises(KeyError, match="no job is registered"):
        scheduler.skip_to("a-job-nobody-declared", date(2026, 8, 24))


def test_an_explicit_instant_reaches_the_store_rather_than_the_scheduler_s_own_clock(
    tmp_path: Path,
) -> None:
    """Every method takes `now` and every one of them must honour it.

    A mutation sweep is what asked for this. `now if now is not None else self.clock()` inverted
    to `now if now is None else self.clock()` survived on `release`, `start`, `succeed`, `fail`
    and `skip_to` -- every case in this file passed an explicit instant and then asserted only
    things the *fixture* clock also satisfied, so a scheduler that silently ignored the argument
    was indistinguishable from one that honoured it.

    The instants asserted here are all different from `NOW` (the fixture clock) and from each
    other, which is what makes the argument observable at all.
    """
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED)
    claimed_at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    started_at = claimed_at + timedelta(seconds=11)
    finished_at = claimed_at + timedelta(seconds=42)
    session = date(2026, 10, 9)

    leased = scheduler.claim("daily-panel-build", now=claimed_at)
    assert leased is not None
    assert leased.lease_expires_at == claimed_at + scheduler.lease_for

    run = scheduler.start("daily-panel-build", session, now=started_at)
    assert run.started_at == started_at, "the scheduler used its own clock, not the instant given"

    done = scheduler.succeed("daily-panel-build", session, now=finished_at)
    assert done.finished_at == finished_at

    released = scheduler.release("daily-panel-build", now=finished_at)
    assert released.updated_at == finished_at
    assert released.lease_owner is None


def test_a_failed_run_records_the_instant_and_the_error_it_was_given(tmp_path: Path) -> None:
    """`fail`'s own arguments, for the reason above -- and its `error_type` reaching the row."""
    scheduler = _scheduler(tmp_path, catch_up=CatchUpPolicy.RUN_EACH_MISSED)
    at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    failed_at = at + timedelta(seconds=33)
    session = date(2026, 10, 9)
    scheduler.claim("daily-panel-build", now=at)
    scheduler.start("daily-panel-build", session, now=at)

    failed = scheduler.fail(
        "daily-panel-build", session, error_type="ProviderRateLimit", now=failed_at
    )

    assert failed.status == "failed"
    assert failed.finished_at == failed_at
    assert failed.error_type == "ProviderRateLimit"
    assert scheduler.due("daily-panel-build", now=at).owed == (session,), (
        "a failed session stopped being owed"
    )


def test_skipping_records_the_instant_it_was_given(tmp_path: Path) -> None:
    scheduler = _scheduler(
        tmp_path, catch_up=CatchUpPolicy.SKIP_MISSED, last_fired=date(2026, 9, 30)
    )
    at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    skipped_at = at + timedelta(seconds=5)
    scheduler.claim("daily-panel-build", now=at)

    advanced = scheduler.skip_to("daily-panel-build", date(2026, 10, 8), now=skipped_at)

    assert advanced.updated_at == skipped_at
    assert advanced.last_fired_session == date(2026, 10, 8)
