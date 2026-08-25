"""Durable bounded-concurrency batch research orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import RLock
from typing import Protocol

from openalpha_cn.batch_contracts import (
    BATCH_PROGRESS_EVENT_VERSIONS,
    BATCH_RESEARCH_TASK_VERSIONS,
    MAX_BATCH_ITEMS,
    MAX_BATCH_WORKERS,
    BatchProgressEvent,
    BatchResearchTask,
    BatchResultRef,
    BatchTaskItem,
)
from openalpha_cn.domain.risk_flag import UndeclaredRiskFlagError
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult

logger = logging.getLogger(__name__)

DISCLOSABLE_ITEM_FAULTS: tuple[type[Exception], ...] = (UndeclaredRiskFlagError,)
"""The faults whose own message may be written into a batch's durable progress record.

## Why the list exists rather than `str(error)` on everything (`V2-P4-102`)

A failed item used to record `type(error).__name__` in both places it can say anything, so a
batch reported `{"status": "failed", "error_type": "ValueError"}` -- the least informative name
in Python -- and an `item_failed` event whose `detail` was the same word again. For an
undeclared risk flag that discards the entire diagnostic `parse_risk_flag`'s docstring promises:
the producer of a whole-market batch learns that one of five thousand items failed and has no
way at all to find out which flag it spelled wrong.

The tempting repair is `detail=str(error)` unconditionally, and it is refused here for
`cli._panel_command`'s reason, stated at this module's own durability boundary: an
*unanticipated* exception carries whatever the frame it escaped was holding -- a filesystem path,
a query, a credential read out of the environment -- and a progress event is **append-only and
durable**, so a leak into one cannot be taken back. An allow-list inverts the default: a message
is written only where somebody has decided it is disclosable, and everything else still records
its type alone, exactly as before.

`UndeclaredRiskFlagError` qualifies because every part of its message is data the caller sent or
this build publishes: the offending string came out of their own request body, and the ten
declared flags are in `docs/api/schemas/signal-frame-v1.json`. Nothing in it is ours to leak.

A tuple rather than a `Protocol` or a marker base class because there is one entry and the check
is `isinstance`; a new entry is a deliberate line in this file, which is where the decision that
a message may cross this boundary should have to be written down.
"""

__all__ = [
    "BATCH_PROGRESS_EVENT_VERSIONS",
    "BATCH_RESEARCH_TASK_VERSIONS",
    "MAX_BATCH_ITEMS",
    "MAX_BATCH_WORKERS",
    "BatchProgressEvent",
    "BatchResearchService",
    "BatchResearchTask",
    "BatchResultRef",
    "BatchTaskItem",
    "BatchTaskStore",
]


def _item_failure_detail(error: Exception) -> str:
    """What one failed item writes into its append-only `item_failed` event.

    The whole message for a fault `DISCLOSABLE_ITEM_FAULTS` names, and the bare type for
    everything else. `BatchProgressEvent.detail` is a free `str | None` that already exists for
    exactly this, so making the reason durable needed no change to a stored contract and no
    migration -- which is the reason it is carried here rather than as a new field on
    `BatchTaskItem`, where `extra="forbid"` makes an added key a breaking change (AGENTS.md,
    v2 hard rule 3).

    `type(error).__name__` is prefixed even for a disclosed message, so a reader of the event
    stream gets the same word `BatchTaskItem.error_type` holds and can line the two up without
    parsing prose.
    """
    name = type(error).__name__
    if isinstance(error, DISCLOSABLE_ITEM_FAULTS):
        return f"{name}: {error}"
    return name


class BatchTaskStore(Protocol):
    """Extension contract for durable batch-task storage consumed by BatchResearchService.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
    consumer side (`runtime/`), not in `storage/`. Its method set is exactly the methods
    `BatchResearchService` calls on `self.store` -- not `SQLiteBatchTaskStore`'s full public
    surface, which also includes `list`, `list_events`, and `recover_interrupted` for callers
    (`sdk.py`, `api/app.py`) that hold the concrete store directly and never go through this
    service.

    It grew from three methods to seven at `V2-P4-019`, and the four new ones all say the
    same thing: the per-item hot path must not be able to express O(N) work. `get`/`save`
    read and write every item and stay, because `run`, `cancel` and `_finish` genuinely act
    on the whole task; `get_item`/`update_item`/`update_status`/`is_cancellation_requested`
    are what the 2N item transitions use, and every one of them is O(1). Before the split,
    the only way to record one item's transition was `get()` + `save()` -- which is exactly
    how a batch came to cost O(N^2); see `storage/batch.py`'s module docstring.
    """

    def get(self, batch_id: str) -> BatchResearchTask | None:
        """Return the latest batch state."""

    def save(self, task: BatchResearchTask) -> None:
        """Insert or atomically replace the latest state of one batch."""

    def get_item(self, *, batch_id: str, index: int) -> BatchTaskItem | None:
        """Return one item's latest state."""

    def update_item(
        self,
        *,
        batch_id: str,
        index: int,
        item: BatchTaskItem,
        updated_at: datetime,
    ) -> None:
        """Persist one item's transition without touching the other N-1."""

    def update_status(self, *, batch_id: str, status: str, updated_at: datetime) -> None:
        """Move one batch's aggregate status without rewriting its items."""

    def is_cancellation_requested(self, batch_id: str) -> bool:
        """Return whether cooperative cancellation was requested for this batch."""

    def append_event(
        self,
        *,
        batch_id: str,
        kind: str,
        occurred_at: datetime,
        run_id: str | None = None,
        detail: str | None = None,
    ) -> BatchProgressEvent:
        """Append and return one monotonic progress event."""


class BatchResearchService:
    """Execute durable batches with a bounded standard-library worker pool."""

    def __init__(
        self,
        *,
        store: BatchTaskStore,
        runner: Callable[[ResearchRunRequest], ResearchRunResult],
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.runner = runner
        self.clock = clock
        self._lock = RLock()

    def submit(
        self,
        *,
        batch_id: str,
        requests: Sequence[ResearchRunRequest],
        max_concurrency: int = 4,
    ) -> BatchResearchTask:
        """Persist one new batch without starting background work."""
        if self.store.get(batch_id) is not None:
            raise ValueError(f"batch already exists: {batch_id}")
        now = self.clock()
        task = BatchResearchTask(
            batch_id=batch_id,
            items=tuple(BatchTaskItem(request=request) for request in requests),
            status="queued",
            max_concurrency=max_concurrency,
            created_at=now,
            updated_at=now,
        )
        self.store.save(task)
        self.store.append_event(batch_id=batch_id, kind="submitted", occurred_at=now)
        logger.info(
            "batch_submitted",
            extra={
                "batch_id": batch_id,
                "item_count": len(requests),
                "max_concurrency": max_concurrency,
            },
        )
        return task

    def run(self, batch_id: str) -> BatchResearchTask:
        """Run queued/failed items and persist every item transition."""
        task = self._required(batch_id)
        if task.status == "cancelled":
            return task
        # A batch previously left "failed"/"partial" being run() again is, by
        # definition, a retry -- `run()` has no separate entry point for it (see
        # `api/app.py`'s `batch_retry` route, which just calls this same method).
        is_retry = task.status in {"failed", "partial"}
        self.store.update_status(batch_id=batch_id, status="running", updated_at=self.clock())
        self.store.append_event(batch_id=batch_id, kind="started", occurred_at=self.clock())
        logger.info("batch_run_started", extra={"batch_id": batch_id, "retry": is_retry})
        # Read from the `task` already in hand rather than re-reading. `update_status` moved
        # only the header, and no worker exists yet to move an item, so a second full read
        # here could not observe anything this one did not -- it would only pay O(N) again.
        indexes = [
            index for index, item in enumerate(task.items) if item.status in {"queued", "failed"}
        ]
        # Python executor contract:
        # https://docs.python.org/3.11/library/concurrent.futures.html#threadpoolexecutor
        with ThreadPoolExecutor(
            max_workers=task.max_concurrency,
            thread_name_prefix="openalpha-batch",
        ) as executor:
            tuple(executor.map(lambda index: self._run_item(batch_id, index), indexes))
        return self._finish(batch_id)

    def cancel(self, batch_id: str) -> BatchResearchTask:
        """Request cooperative cancellation and cancel work not yet running."""
        with self._lock:
            task = self._required(batch_id)
            items = tuple(
                item.model_copy(update={"status": "cancelled"})
                if item.status in {"queued", "failed"}
                else item
                for item in task.items
            )
            updated = task.model_copy(
                update={
                    "items": items,
                    "status": "cancelled",
                    "cancellation_requested": True,
                    "updated_at": self.clock(),
                }
            )
            self.store.save(updated)
            self.store.append_event(
                batch_id=batch_id,
                kind="cancellation_requested",
                occurred_at=self.clock(),
            )
            logger.info("batch_cancel_requested", extra={"batch_id": batch_id})
            return updated

    def _run_item(self, batch_id: str, index: int) -> None:
        """Run one item, persisting each of its transitions in O(1).

        Every store call here reads or writes exactly this item, or one boolean off the
        header -- never the whole task. That is the entire difference between a batch that
        costs O(N) and one that costs O(N^2), and it is why `is_cancellation_requested`
        exists instead of the `get()` this used to do just to read one flag.

        `self._lock` still wraps each transition, so an item's read-modify-write and the
        progress event that announces it cannot interleave with `cancel()`'s whole-task
        rewrite. It is now held for a fraction of a millisecond instead of for a full
        serialize-and-reparse of every item in the batch, which is what makes
        `max_concurrency` mean something at all.
        """
        with self._lock:
            if self.store.is_cancellation_requested(batch_id):
                return
            item = self._required_item(batch_id, index).model_copy(
                update={"status": "running", "error_type": None}
            )
            self.store.update_item(
                batch_id=batch_id, index=index, item=item, updated_at=self.clock()
            )
            self.store.append_event(
                batch_id=batch_id,
                kind="item_started",
                occurred_at=self.clock(),
                run_id=item.request.run_id,
            )
        try:
            result = self.runner(item.request)
        except Exception as error:
            # V2-P4-102: `error_type` is the *specific* class either way -- `ValueError` was
            # never this code's choice, it was the base class the risk-flag refusal happened to
            # raise, and naming the subclass costs nothing and separates a misspelled flag from
            # a bad price at a glance. `detail` carries the whole reason only for the faults
            # DISCLOSABLE_ITEM_FAULTS names; see that constant for why the default is the type
            # alone on an append-only durable record.
            with self._lock:
                failed = self._required_item(batch_id, index).model_copy(
                    update={"status": "failed", "error_type": type(error).__name__}
                )
                self.store.update_item(
                    batch_id=batch_id, index=index, item=failed, updated_at=self.clock()
                )
                self.store.append_event(
                    batch_id=batch_id,
                    kind="item_failed",
                    occurred_at=self.clock(),
                    run_id=item.request.run_id,
                    detail=_item_failure_detail(error),
                )
            return
        with self._lock:
            completed = self._required_item(batch_id, index).model_copy(
                update={
                    "status": "succeeded",
                    "result": BatchResultRef(
                        decision_id=result.decision.decision_id,
                        signal_id=result.signal.signal_id,
                        final_action=result.decision.final_action,
                    ),
                }
            )
            self.store.update_item(
                batch_id=batch_id, index=index, item=completed, updated_at=self.clock()
            )
            self.store.append_event(
                batch_id=batch_id,
                kind="item_succeeded",
                occurred_at=self.clock(),
                run_id=item.request.run_id,
            )

    def _finish(self, batch_id: str) -> BatchResearchTask:
        with self._lock:
            task = self._required(batch_id)
            statuses = {item.status for item in task.items}
            if statuses == {"succeeded"}:
                status = "succeeded"
            elif statuses <= {"cancelled", "succeeded"} and "cancelled" in statuses:
                status = "cancelled"
            elif statuses == {"failed"}:
                status = "failed"
            else:
                status = "partial"
            completed = task.model_copy(update={"status": status, "updated_at": self.clock()})
            # Only the aggregate status moved. `save()` here would rewrite all N item rows
            # with the values this `get()` just read back out of them.
            self.store.update_status(
                batch_id=batch_id, status=status, updated_at=completed.updated_at
            )
            self.store.append_event(
                batch_id=batch_id,
                kind="finished",
                occurred_at=self.clock(),
                detail=status,
            )
            logger.info("batch_finished", extra={"batch_id": batch_id, "status": status})
            return completed

    def _required(self, batch_id: str) -> BatchResearchTask:
        task = self.store.get(batch_id)
        if task is None:
            raise KeyError(f"unknown batch: {batch_id}")
        return task

    def _required_item(self, batch_id: str, index: int) -> BatchTaskItem:
        item = self.store.get_item(batch_id=batch_id, index=index)
        if item is None:
            raise KeyError(f"unknown batch item: {batch_id}[{index}]")
        return item
