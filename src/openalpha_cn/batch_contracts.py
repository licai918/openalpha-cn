"""Durable batch-orchestration state: `BatchResearchTask` and its progress events.

Split out of `runtime/batch.py` (V2-P0B-012) so `storage/batch.py` can persist
`BatchResearchTask`/`BatchProgressEvent` without importing anything under
`openalpha_cn.runtime` at all, forbidden by the `storage-no-upward-deps` import-linter
contract -- which forbids the whole `openalpha_cn.runtime` package, not just
`runtime/engine.py`'s SQLite-owning half the way `runtime/contracts.py` (V2-P0B-001) needed
to dodge. No submodule carved out of `runtime/` would have helped here, however thin: the
contract check is package-scoped, so this module lives at the top level instead, a sibling
of `runtime` rather than a child of it.

Why these four classes (`BatchResultRef`, `BatchTaskItem`, `BatchResearchTask`,
`BatchProgressEvent`) do not move into `openalpha_cn.domain` the way the task's other four
relocated contracts did: `BatchResearchTask`'s own shape (`status`, `max_concurrency`,
`cancellation_requested`) describes bounded-concurrency job-runner bookkeeping, not a
research-domain concept -- it is closer in kind to `storage/recovery.py`'s
`RunRecoveryState` (also durable orchestration state, also kept out of `domain`) than to
`MemoryEntry` or `PortfolioTransition`. That is a semantic judgment, not a structural
constraint: `BatchTaskItem.request` is typed `ResearchRunRequest`, which itself now lives in
`openalpha_cn.domain.run_request` (see that module's docstring), so importing it here
creates no edge into `openalpha_cn.runtime` at all.

An earlier version of this module imported `ResearchRunRequest` from
`openalpha_cn.runtime.contracts` instead, and `storage-no-upward-deps` carried
`allow_indirect_imports = true` to tolerate the resulting two-hop chain
(`storage.batch -> batch_contracts -> runtime.contracts`). A Critical review rejected that:
the flag weakens import-linter's default full-transitive-reachability check to a
direct-edges-only one, and a probe proved the gap was real -- a neutral top-level module
importing a behavioural `product` class, reached in turn from `storage/`, passed the
relaxed contract while remaining fully reachable to `grimp`
(`tests/unit/test_import_layering.py::test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module`
reproduces that exact probe). The reasoning that produced the relaxation had conflated
"move the whole `runtime/contracts.py` module into `domain`" (which *would* drag
`agents.base` in, via `ResearchRunResult.agent_results: tuple[AgentResult, ...]`, directly
violating `domain-purity`) with "move just `ResearchRunRequest`" (which does not --
`ResearchRunRequest` has never depended on `AgentResult`; only `ResearchRunResult`, a
separate class that stays behind in `runtime/contracts.py`, does). Moving
`ResearchRunRequest` alone removed the chain -- and the need for the relaxation -- entirely.

This module now legitimately depends only on `openalpha_cn.domain.*` -- no edge into
`openalpha_cn.runtime` remains at all. What it must never depend on is `openalpha_cn.storage`
(which would reintroduce a cycle with `storage/batch.py`) or `agents`/`product`/`backtest`
(which it never needed in the first place); see
`tests/unit/test_storage_contract_relocation.py` and
`tests/unit/test_batch_contracts_import_isolation.py` for the static and dynamic proofs.

`runtime/batch.py` re-exports every name defined here unchanged, alongside the
`BatchTaskStore` Protocol and `BatchResearchService` that stay behind (both are runtime
behavior: the service orchestrates work, it is not a stored value), so every existing
`from openalpha_cn.runtime.batch import BatchResearchTask` (and friends) keeps working.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version

BatchItemStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
"""The states one batch item can be in. Stated once here; `BatchTaskItem` and the listing's
per-status census both read it, so a sixth state cannot reach one and miss the other."""

BATCH_ITEM_STATUSES: Final[tuple[BatchItemStatus, ...]] = get_args(BatchItemStatus)
"""`BatchItemStatus` as a tuple, derived rather than retyped.

`BatchItemCensus` has to be **total** over these -- a listing that omitted a status would make
"zero" and "not counted" the same answer -- and deriving the set from the type is what keeps the
totality check from being a second hand-written list that can go stale against the first.
"""

MAX_BATCH_ITEMS = 10_000
"""Most items one batch may carry. Stated once here; `api/app.py` reads it, not a copy.

Was 1,000 until `V2-P4-019`. That was not a throttle -- `V2-P4-004` measured the real
A-share market at **5,545 listed / 5,540 priced** on 2026-08-14, so a whole-market batch was
not slow, it was *inexpressible*: the request was rejected with a 422 before a single item
was scheduled. A ceiling below the market it exists to run is a functional ceiling.

10,000 rather than 5,545 exactly, because the market is a moving number and a cap that has
to be edited every time a few hundred companies list is the same defect on a delay; and
rather than "no cap at all", because the cap is also what keeps one request from being an
unbounded memory commitment. `tests/integration/test_batch_whole_market_scale.py` builds,
persists and reads back a task at exactly this number, so it is a size that has been run and
not merely typed -- and runs a batch of 5,545 to completion, which is the size that matters.
"""

MAX_BATCH_WORKERS = 8
"""Most worker threads one batch may run. Stated once here; `api/app.py` reads it.

**Lowered from 32 by `V2-P4-019`, deliberately, and said plainly here because lowering a
ceiling quietly is how a fix becomes a surprise.** 32 was advertising parallelism this
storage layer has never delivered. Every item transition is persisted, and every store call
in `BatchResearchService` happens under one process-wide `RLock`, so past a handful of
workers the extra threads do not write concurrently -- they queue. Measured after the O(1)
transition fix, N=600, a runner that sleeps 10ms to stand in for a model-provider call:

    concurrency   1     2     4     8    16    32
    items/sec    57   114   211   184   201   216

Near-linear to 4, then flat inside the noise band all the way to 32 -- the plateau is the
serialized persistence, not the workers. With the *real* `ResearchEngine` runner (N=400) it
plateaus even earlier, at 2, because that work is CPU-bound and holds the GIL: 66, 116, 123,
116, 124, 120 items/sec across the same six settings. 8 is one doubling above the highest
measured plateau, so a slower real-network runner has room while the number stops claiming
a 32x that does not exist.

Before the fix the higher setting was not merely useless but *harmful*: with a no-op runner
at N=1,000, `max_concurrency=1` took 64.8s and `max_concurrency=32` took **85.8s** -- 32x
the workers, 32% slower, all of it lock contention around the whole-task rewrite.

What 32 did *not* do is produce `database is locked`, and that is worth recording because it
is the failure this issue predicted. `open_state_connection` opens every connection with
`timeout=10` (a 10-second busy handler) and every store already sets
`PRAGMA journal_mode = WAL`, so neither was missing. Probed directly: 32 concurrent writer
threads against one `state.sqlite3` completed 36,504 transactions in 8s with zero errors,
and still zero when one of them held the write lock 200ms at a time. The error appears only
when a *single* write transaction outlives the busy timeout, and the boundary is exactly
that: behind a 9s hold 32/32 writers succeed, behind an 11s hold 32/32 fail with
`database is locked`. It is a duration failure, not a concurrency failure -- and the longest
write on this path is now well under a millisecond.

`tests/integration/test_batch_concurrency_ceiling.py` pins that a batch at this ceiling
completes, that the API refuses one above it, and that this number is the *only* place
either model states it.
"""


class BatchResultRef(BaseModel):
    """Compact immutable reference to one completed research result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    signal_id: str
    final_action: Literal["watch", "avoid", "abstain"]


class BatchTaskItem(BaseModel):
    """One independently recoverable request inside a batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ResearchRunRequest
    status: BatchItemStatus = "queued"
    result: BatchResultRef | None = None
    error_type: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> Self:
        if self.status == "succeeded" and self.result is None:
            raise ValueError("succeeded batch item requires result")
        if self.status != "succeeded" and self.result is not None:
            raise ValueError("only succeeded batch item may contain result")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("failed batch item requires error_type")
        if self.status != "failed" and self.error_type is not None:
            raise ValueError("only failed batch item may contain error_type")
        return self


BATCH_TASK_ITEM_VERSIONS: ContractVersions[BatchTaskItem] = single_version(
    "batch-task-item", BatchTaskItem
)
"""`V2-P4-019`: items became individually stored rows, so they became individually *read*.

Registered for the same reason `BATCH_RESEARCH_TASK_VERSIONS` is, and stated in
`domain/versioning.py`'s docstring: every stored-row read in this package goes through
`read_versioned`, versioned or not, so that giving this model a real `schema_version` later
is an edit here rather than an audit of the call sites that read it.
"""


class BatchResearchTask(BaseModel):
    """Latest durable state of a bounded research batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, max_length=128)
    items: tuple[BatchTaskItem, ...] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    max_concurrency: int = Field(ge=1, le=MAX_BATCH_WORKERS)
    cancellation_requested: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)


BATCH_RESEARCH_TASK_VERSIONS: ContractVersions[BatchResearchTask] = single_version(
    "batch-research-task", BatchResearchTask
)


DEFAULT_BATCH_PAGE_SIZE: Final[int] = 50
"""How many batch summaries `GET /api/v1/research/batches` answers when asked for no window.

Small enough that the default answer is a page a human reads, large enough that a deployment
running a batch a day does not paginate for a month. A default of "all of them" is what
`V2-P4-040` was.
"""

MAX_BATCH_PAGE_SIZE: Final[int] = 500
"""Most summaries one listing page may carry. Stated once here; `api/app.py` reads it.

A summary is bounded -- no field of it grows with the batch's item count -- so this ceiling
bounds the response in bytes as well as in rows, which is the property the pre-`V2-P4-040`
listing did not have at any page size.
"""


class BatchItemCensus(BaseModel):
    """How many of one batch's items are in each state.

    Total over `BATCH_ITEM_STATUSES` on purpose, and every field defaults to zero: a caller
    reading `items_by_status["failed"]` must never have to tell "no failures" apart from "this
    listing did not count failures", and a census that omitted empty states would make those the
    same answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    queued: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Every counted item. Equals `BatchTaskSummary.item_count` by construction."""
        return sum(getattr(self, status) for status in BATCH_ITEM_STATUSES)

    @classmethod
    def from_counts(cls, counts: Mapping[str, int]) -> "BatchItemCensus":
        """Build from whatever states the store actually saw, absent ones counted as zero.

        Unknown keys are refused rather than dropped (`extra="forbid"` does that), which is what
        turns a store row carrying a state this contract does not model into a loud failure
        instead of a silently short census.
        """
        return cls(**{status: counts.get(status, 0) for status in BATCH_ITEM_STATUSES})


class BatchTaskSummary(BaseModel):
    """One batch as a listing shows it: its bookkeeping, and counts instead of items.

    `V2-P4-040`. Everything here is O(1) in the batch's size, which is the whole contract: the
    listing this replaces inlined every item of every batch, so twenty whole-market batches came
    back as 36.9 MB -- a body larger than the 8 MiB this same service refuses on the way in. The
    items did not become unavailable, they moved to `GET /api/v1/research/batches/{batch_id}`,
    which returns the full `BatchResearchTask` unchanged.

    Deliberately not a `BatchResearchTask` with `items` omitted: a model that can be built by
    dropping a field can also be built by *forgetting* to drop it, and the per-status census is
    a fact the full task does not carry in any case.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, max_length=128)
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    max_concurrency: int = Field(ge=1, le=MAX_BATCH_WORKERS)
    cancellation_requested: bool = False
    created_at: datetime
    updated_at: datetime
    item_count: int = Field(ge=0)
    items_by_status: BatchItemCensus

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_census_totals(self) -> Self:
        if self.items_by_status.total != self.item_count:
            raise ValueError(
                f"batch item census sums to {self.items_by_status.total} against an item_count "
                f"of {self.item_count}"
            )
        return self


class BatchTaskPage(BaseModel):
    """One window onto the batch shelf, and how big the shelf is.

    `total` is the whole count rather than the window's, so a caller can size their paging
    without walking to the end; `limit`/`offset` are echoed so a response is interpretable
    without the request beside it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    batches: tuple[BatchTaskSummary, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_BATCH_PAGE_SIZE)
    offset: int = Field(ge=0)


class BatchProgressEvent(BaseModel):
    """Append-only progress event for polling, SSE, or audit consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    batch_id: str
    kind: Literal[
        "submitted",
        "started",
        "item_started",
        "item_succeeded",
        "item_failed",
        "cancellation_requested",
        "recovered",
        "finished",
    ]
    occurred_at: datetime
    run_id: str | None = None
    detail: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


BATCH_PROGRESS_EVENT_VERSIONS: ContractVersions[BatchProgressEvent] = single_version(
    "batch-progress-event", BatchProgressEvent
)
