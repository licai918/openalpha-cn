"""Durable bounded-concurrency batch research orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult

if TYPE_CHECKING:
    from openalpha_cn.storage.batch import SQLiteBatchTaskStore


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
        from openalpha_cn.domain.time import ensure_aware

        return ensure_aware(value)


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
        from openalpha_cn.domain.time import ensure_aware

        return ensure_aware(value)


class BatchResearchService:
    """Execute durable batches with a bounded standard-library worker pool."""

    def __init__(
        self,
        *,
        store: SQLiteBatchTaskStore,
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
        return task

    def run(self, batch_id: str) -> BatchResearchTask:
        """Run queued/failed items and persist every item transition."""
        task = self._required(batch_id)
        if task.status == "cancelled":
            return task
        self._replace(task.model_copy(update={"status": "running", "updated_at": self.clock()}))
        self.store.append_event(batch_id=batch_id, kind="started", occurred_at=self.clock())
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
