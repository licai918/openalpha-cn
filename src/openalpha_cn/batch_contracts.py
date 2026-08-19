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

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version

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
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
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
