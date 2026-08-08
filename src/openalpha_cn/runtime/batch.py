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
    BatchProgressEvent,
    BatchResearchTask,
    BatchResultRef,
    BatchTaskItem,
)
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_PROGRESS_EVENT_VERSIONS",
    "BATCH_RESEARCH_TASK_VERSIONS",
    "BatchProgressEvent",
    "BatchResearchService",
    "BatchResearchTask",
    "BatchResultRef",
    "BatchTaskItem",
    "BatchTaskStore",
]


class BatchTaskStore(Protocol):
    """Extension contract for durable batch-task storage consumed by BatchResearchService.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
    consumer side (`runtime/`), not in `storage/`. Its method set is exactly the three
    methods `BatchResearchService` calls on `self.store` -- not `SQLiteBatchTaskStore`'s
    full public surface, which also includes `list`, `list_events`, and
    `recover_interrupted` for callers (`sdk.py`, `api/app.py`) that hold the concrete
    store directly and never go through this service.
    """

    def get(self, batch_id: str) -> BatchResearchTask | None:
        """Return the latest batch state."""

    def save(self, task: BatchResearchTask) -> None:
        """Insert or atomically replace the latest state of one batch."""

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
        self._replace(task.model_copy(update={"status": "running", "updated_at": self.clock()}))
        self.store.append_event(batch_id=batch_id, kind="started", occurred_at=self.clock())
        logger.info("batch_run_started", extra={"batch_id": batch_id, "retry": is_retry})
        indexes = [
            index
            for index, item in enumerate(self._required(batch_id).items)
            if item.status in {"queued", "failed"}
        ]
        # Python executor contract:
        # https://docs.python.org/3.11/library/concurrent.futures.html#threadpoolexecutor
        with ThreadPoolExecutor(
            max_workers=self._required(batch_id).max_concurrency,
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
        with self._lock:
            task = self._required(batch_id)
            if task.cancellation_requested:
                return
            item = task.items[index].model_copy(update={"status": "running", "error_type": None})
            self._set_item(task, index, item)
            self.store.append_event(
                batch_id=batch_id,
                kind="item_started",
                occurred_at=self.clock(),
                run_id=item.request.run_id,
            )
        try:
            result = self.runner(item.request)
        except Exception as error:
            with self._lock:
                task = self._required(batch_id)
                failed = task.items[index].model_copy(
                    update={"status": "failed", "error_type": type(error).__name__}
                )
                self._set_item(task, index, failed)
                self.store.append_event(
                    batch_id=batch_id,
                    kind="item_failed",
                    occurred_at=self.clock(),
                    run_id=item.request.run_id,
                    detail=type(error).__name__,
                )
            return
        with self._lock:
            task = self._required(batch_id)
            completed = task.items[index].model_copy(
                update={
                    "status": "succeeded",
                    "result": BatchResultRef(
                        decision_id=result.decision.decision_id,
                        signal_id=result.signal.signal_id,
                        final_action=result.decision.final_action,
                    ),
                }
            )
            self._set_item(task, index, completed)
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
            self.store.save(completed)
            self.store.append_event(
                batch_id=batch_id,
                kind="finished",
                occurred_at=self.clock(),
                detail=status,
            )
            logger.info("batch_finished", extra={"batch_id": batch_id, "status": status})
            return completed

    def _set_item(self, task: BatchResearchTask, index: int, item: BatchTaskItem) -> None:
        items = list(task.items)
        items[index] = item
        self._replace(task.model_copy(update={"items": tuple(items), "updated_at": self.clock()}))

    def _replace(self, task: BatchResearchTask) -> None:
        self.store.save(task)

    def _required(self, batch_id: str) -> BatchResearchTask:
        task = self.store.get(batch_id)
        if task is None:
            raise KeyError(f"unknown batch: {batch_id}")
        return task
