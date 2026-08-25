"""Durable scheduling state: a trading-day job, its lease, and its per-session runs.

`V2-P5-010`. Audit `F98` recorded that this repository has **no scheduling primitive at all**
-- `grep cron|scheduler|apscheduler|celery` returns nothing -- and that a `daily` mode needs a
persistent job table with a next-fire-time, a lease, a per-trading-day idempotency key, a
catch-up policy, a calendar dependency and crash recovery.

## Why a neutral top-level module rather than `domain/` or `storage/`

Exactly `batch_contracts.py`'s reasoning, and for exactly its reason. `storage/jobs.py` has to
persist these, and the `storage-no-upward-deps` import-linter contract forbids the whole of
`agents`/`runtime`/`product`/`backtest` as a target. Meanwhile a *lease* is not a
research-domain concept the way `MemoryEntry` or `PortfolioTransition` is -- it is durable
orchestration bookkeeping, kin to `batch_contracts.BatchResearchTask` and
`storage/recovery.py`'s `RunRecoveryState`, both of which are deliberately outside `domain/`.
So this module sits beside `batch_contracts.py`: it imports `openalpha_cn.domain.*` and nothing
else from this package.

## Why there is no calendar in this module

The trading calendar is the one thing a scheduler for A-shares cannot do without, and it is
also the thing this module must not touch. `V2-P4-063` found the 16:30 publication rule
(`DAILY_AVAILABILITY_TIME`) restated in three places with two of them disagreeing, and
`V2-P4-114` found a fourth restatement a row later. So the rule is asked, never restated:
`panel_ingest.newest_published_session` maps an instant to the newest session that had
published at it, `panel_ingest.session_publication_instant` maps a session back to the instant
it publishes at, and `scheduler.py` is where those two meet a `TradingCalendar`. Nothing here
knows what time of day anything happens.

What this module contributes instead is the **shape of the idempotency key**, and that shape
is the whole per-trading-day guarantee: `job_runs.idempotency_key` is a `PRIMARY KEY`, so "at
most once per trading session" is enforced by SQLite's own uniqueness rather than by a check
some caller might forget -- the same move `domain/_identity.py`'s content addresses make.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version

MAX_JOB_ID_LENGTH: Final[int] = 128
"""Matches `BatchResearchTask.batch_id`. A job id is an operator-chosen name, not an address."""

MAX_OWNER_LENGTH: Final[int] = 128
"""How long a lease owner's name may be. A hostname plus a pid fits comfortably."""


class CatchUpPolicy(StrEnum):
    """What a job owes when it wakes to find sessions it never ran.

    Two members, and the second is not a convenience: a daily ingest that skipped three
    sessions has a **gap in a point-in-time panel**, and `RUN_EACH_MISSED` is the only policy
    under which that gap gets filled. `SKIP_MISSED` is right for a job whose output is a
    snapshot of now (a health report, a shortlist rebuild) where running yesterday's twice is
    worse than not running it at all.

    There is deliberately no third member. A `FAIL_ON_MISSED` policy would be a policy about
    alerting, and this table has no alerting; adding a member nothing can act on would be a
    contract that lies about what the scheduler does.
    """

    SKIP_MISSED = "skip_missed"
    RUN_EACH_MISSED = "run_each_missed"


def trading_day_key(job_id: str, session: date) -> str:
    """The idempotency key for one job on one trading session.

    `job_id@YYYY-MM-DD`. `@` because a `job_id` may contain `-` and `:` (both appear in the
    session date and in ordinary operator names), and a separator that can occur on both sides
    of itself makes two different `(job_id, session)` pairs collide -- which, on a `PRIMARY
    KEY`, is a silently skipped run rather than an error. `@` is excluded from `job_id` by
    `ScheduledJob.job_id`'s own validator for exactly that reason.
    """
    return f"{job_id}@{session.isoformat()}"


class ScheduledJob(BaseModel):
    """One durable schedule: what it is, how far it has got, and who holds it right now.

    `next_fire_time` is a **derived hint and not the source of truth**, which is the one design
    decision in this class worth arguing with. The truth about whether a job owes work is
    `last_fired_session` compared against the newest session that has published, because that
    is a question only the calendar can answer and the calendar changes (a holiday is announced,
    a session is added). A stored fire time computed under last month's calendar would be a
    fourth restatement of the publication rule, drifting silently. It is stored anyway because
    a poller needs something to `WHERE` on so that waking up costs one indexed comparison
    rather than a calendar load per job; `scheduler.py` recomputes it from the calendar every
    time it advances a job, and never reads it as an answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(min_length=1, max_length=MAX_JOB_ID_LENGTH)
    catch_up: CatchUpPolicy
    last_fired_session: date | None = None
    next_fire_time: datetime | None = None
    lease_owner: str | None = Field(default=None, max_length=MAX_OWNER_LENGTH)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("job_id")
    @classmethod
    def refuse_the_key_separator(cls, value: str) -> str:
        if "@" in value:
            raise ValueError(
                "job_id may not contain '@': it separates the id from the session date in "
                "the per-trading-day idempotency key, and an id carrying one would let two "
                "different (job, session) pairs derive the same key"
            )
        return value

    @field_validator("next_fire_time", "lease_expires_at", "created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)

    @model_validator(mode="after")
    def a_lease_has_both_halves_or_neither(self) -> Self:
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError(
                "a lease is an owner and an expiry together: an owner with no expiry never "
                "releases after a crash, and an expiry with no owner names nobody to reclaim "
                "it from"
            )
        return self

    def lease_is_held_at(self, instant: datetime) -> bool:
        """Whether a live lease stands at `instant`.

        Expiry is what crash recovery is made of: a process that died holding a lease leaves
        `lease_owner` set forever, and nothing sweeps the table. What makes the row reclaimable
        is that the expiry has passed, so recovery is a property of every read rather than a
        scan somebody has to remember to run.
        """
        return self.lease_expires_at is not None and instant < self.lease_expires_at


SCHEDULED_JOB_VERSIONS: ContractVersions[ScheduledJob] = single_version(
    "scheduled-job", ScheduledJob
)


class JobRun(BaseModel):
    """One job's attempt at one trading session, addressed by its idempotency key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=1)
    job_id: str = Field(min_length=1, max_length=MAX_JOB_ID_LENGTH)
    session: date
    status: Literal["running", "succeeded", "failed"]
    owner: str = Field(min_length=1, max_length=MAX_OWNER_LENGTH)
    started_at: datetime
    finished_at: datetime | None = None
    error_type: str | None = Field(default=None, max_length=256)

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)

    @model_validator(mode="after")
    def the_key_addresses_this_run(self) -> Self:
        expected = trading_day_key(self.job_id, self.session)
        if self.idempotency_key != expected:
            raise ValueError(
                f"idempotency_key {self.idempotency_key!r} does not address this run: "
                f"{self.job_id!r} on {self.session.isoformat()} derives {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def a_terminal_run_has_finished(self) -> Self:
        if self.status == "running" and self.finished_at is not None:
            raise ValueError("a running job run has not finished")
        if self.status != "running" and self.finished_at is None:
            raise ValueError(f"a {self.status} job run must carry finished_at")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("a failed job run must name what failed")
        if self.status != "failed" and self.error_type is not None:
            raise ValueError("only a failed job run may carry error_type")
        return self


JOB_RUN_VERSIONS: ContractVersions[JobRun] = single_version("job-run", JobRun)
