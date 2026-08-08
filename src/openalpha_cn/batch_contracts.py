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


class BatchResearchTask(BaseModel):
    """Latest durable state of a bounded research batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, max_length=128)
    items: tuple[BatchTaskItem, ...] = Field(min_length=1, max_length=1000)
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    max_concurrency: int = Field(ge=1, le=32)
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
