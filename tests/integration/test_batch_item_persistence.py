"""The O(1) per-item persistence surface `V2-P4-019` added, and what it must not disturb.

`BatchTaskStore` grew four methods -- `get_item`, `update_item`, `update_status`,
`is_cancellation_requested` -- so that recording one item's transition stops costing a
serialize-and-reparse of every item in the batch (see `storage/batch.py`'s module docstring
for the measurements). Each of them replaced a line that used to read or write the whole
task, and each therefore needs a test that can tell "wrote the right one" from "wrote them
all" -- the two are indistinguishable from a batch where every item looks the same and ends
in the same state, which is exactly what the pre-existing batch tests use.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from openalpha_cn.batch_contracts import BatchResearchTask, BatchResultRef, BatchTaskItem
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.batch import BatchResearchService
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.storage.batch import SQLiteBatchTaskStore


@dataclass(frozen=True)
class _FakeDecision:
    decision_id: str
    final_action: str


@dataclass(frozen=True)
class _FakeSignal:
    signal_id: str


@dataclass(frozen=True)
class _FakeResult:
    """The three attributes `BatchResearchService._run_item` reads off a run result."""

    decision: _FakeDecision
    signal: _FakeSignal


@pytest.fixture
def research_request(frozen_now: datetime) -> Callable[[int], ResearchRunRequest]:
    def _make(index: int) -> ResearchRunRequest:
        subject = f"{index:06d}.SZ"
        evidence = EvidenceSnapshot(
            subject=subject,
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="item.fixture",
            source_uri=f"fixture://{subject}",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Item persistence fixture.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchRunRequest(
            run_id=f"item-{index:06d}",
            mode="replay",
            subject=subject,
            as_of=frozen_now,
            evidence=(evidence,),
            code_commit="0123456789abcdef",
            config_digest="b" * 64,
            random_seed=7,
        )

    return _make


def _task(
    research_request: Callable[[int], ResearchRunRequest], *, count: int, now: datetime
) -> BatchResearchTask:
    return BatchResearchTask(
        batch_id="items",
        items=tuple(BatchTaskItem(request=research_request(index)) for index in range(count)),
        status="queued",
        max_concurrency=1,
        created_at=now,
        updated_at=now,
    )


def test_update_item_moves_exactly_the_named_item(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """The item at `index` changes; its neighbours are byte-identical afterwards.

    Three items and only the middle one touched, because an implementation that rewrote
    every row -- the O(N) behaviour this method exists to replace -- would leave the batch
    in a state a one-item fixture cannot distinguish from the correct one.
    """
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    before = _task(research_request, count=3, now=frozen_now)
    store.save(before)
    moved = before.items[1].model_copy(
        update={
            "status": "succeeded",
            "result": BatchResultRef(decision_id="dec-1", signal_id="sig-1", final_action="avoid"),
        }
    )

    store.update_item(batch_id="items", index=1, item=moved, updated_at=frozen_now)

    after = store.get("items")
    assert after is not None
    assert [item.status for item in after.items] == ["queued", "succeeded", "queued"]
    assert after.items[1].result is not None
    assert after.items[1].result.decision_id == "dec-1"
    assert after.items[0] == before.items[0]
    assert after.items[2] == before.items[2]
    assert store.get_item(batch_id="items", index=1) == moved
    assert store.get_item(batch_id="items", index=0) == before.items[0]


def test_update_item_advances_the_batchs_updated_at(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """One item moving makes the batch's `updated_at` move, as the whole-task write did.

    Needs a second, *later* instant supplied by hand, and that is the whole reason this
    test exists separately from the ones above: every batch test in this suite runs on
    `frozen_now`, so `updated_at` is written with the value it already had and no assertion
    anywhere can tell a store that advances it from one that silently stopped. `create_at`
    is asserted unmoved alongside it, so "advanced the timestamp" cannot be satisfied by an
    implementation that rewrote both.
    """
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    before = _task(research_request, count=2, now=frozen_now)
    store.save(before)
    later = frozen_now + timedelta(minutes=3)

    store.update_item(
        batch_id="items",
        index=0,
        item=before.items[0].model_copy(update={"status": "running"}),
        updated_at=later,
    )

    after = store.get("items")
    assert after is not None
    assert before.updated_at == frozen_now
    assert after.updated_at == later
    assert after.created_at == frozen_now


def test_update_status_moves_the_header_and_leaves_every_item_alone(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """`_finish` records an aggregate outcome; it must not rewrite the items it summarises."""
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    before = _task(research_request, count=3, now=frozen_now)
    store.save(before)
    later = frozen_now + timedelta(minutes=5)

    store.update_status(batch_id="items", status="partial", updated_at=later)

    after = store.get("items")
    assert after is not None
    assert after.status == "partial"
    assert after.updated_at == later
    assert after.items == before.items
    assert after.created_at == before.created_at


def test_get_item_reports_a_position_that_does_not_exist_as_absent(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """Out of range is `None`, which is what `_required_item` turns into its `KeyError`."""
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    store.save(_task(research_request, count=2, now=frozen_now))

    assert store.get_item(batch_id="items", index=1) is not None
    assert store.get_item(batch_id="items", index=2) is None
    assert store.get_item(batch_id="no-such-batch", index=0) is None


def test_cancelling_mid_run_stops_items_that_have_not_started(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """A cancel that lands while `run()` is in flight must not be overtaken by the workers.

    This is the one path `is_cancellation_requested` exists for, and the reason it needs its
    own test rather than riding on the existing cancel test: cancelling a batch that is *not*
    running marks every pending item `cancelled`, and `run()` then never schedules them at
    all, so the flag is never consulted. It is only when the executor has already been handed
    the indexes -- as here, where the first item's runner does the cancelling -- that a worker
    can pick up an item that `cancel()` has just marked `cancelled`. Without the check it
    reads that item, copies it to `running`, and executes work on a cancelled batch;
    `model_copy` does not re-validate, so nothing else would object.

    Asserted on the events as well as the statuses, because the resurrected item would end
    `succeeded` -- a state `cancel()` never produces -- but only the event stream shows that
    it was *started* at all.
    """
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service: BatchResearchService

    def _cancel_on_the_first_item(request: ResearchRunRequest) -> ResearchRunResult:
        if request.run_id != "item-000000":
            raise RuntimeError("runner must never be reached for a cancelled item")
        service.cancel("items")
        # Succeed, so item 0 ends `succeeded` and the batch's aggregate is
        # {succeeded, cancelled} -> "cancelled". Raising here instead would end it
        # `partial`, which is also not "succeeded" -- and would therefore let this test
        # pass for the wrong reason if a later item *were* resurrected and failed.
        return cast(
            ResearchRunResult,
            _FakeResult(
                decision=_FakeDecision(decision_id="dec-0", final_action="watch"),
                signal=_FakeSignal(signal_id="sig-0"),
            ),
        )

    service = BatchResearchService(
        store=store, runner=_cancel_on_the_first_item, clock=lambda: frozen_now
    )
    service.submit(
        batch_id="items",
        requests=[research_request(index) for index in range(6)],
        max_concurrency=1,
    )

    finished = service.run("items")

    started = [event for event in store.list_events("items") if event.kind == "item_started"]
    assert [event.run_id for event in started] == ["item-000000"]
    assert [item.status for item in finished.items[1:]] == ["cancelled"] * 5
    assert finished.status == "cancelled"
