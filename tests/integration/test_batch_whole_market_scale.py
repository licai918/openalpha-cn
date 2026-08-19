"""Whole-market batch scale: 5,545 items must be expressible *and* must finish (V2-P4-019).

`V2-P4-004` measured the real A-share market on 2026-08-14: **5,545 listed, 5,540 with
prices** (see `backtest/cross_section.py`'s measurement block and that roadmap row). Against
that number the pre-fix `max_length=1000` on `BatchSubmitRequest.requests` and on
`BatchResearchTask.items` was not a throttle -- it made a whole-market batch *inexpressible*.
A user asking to run the market got a 422 before a single item was scheduled.

The halves are separate tests because they fail for different reasons and are worth being
able to break independently: the API model is what returns 422, the durable contract is what
has to hold 5,545 items, and the service's persistence is what has to carry them to a
terminal state in finite time. All three were red at `5e18791`, at *validation*:

    tuple_type / too_long: Tuple should have at most 1000 items after validation, not 5545
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.batch_contracts import BatchResearchTask, BatchTaskItem
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.batch import (
    MAX_BATCH_ITEMS,
    MAX_BATCH_WORKERS,
    BatchResearchService,
)
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.storage.batch import SQLiteBatchTaskStore

# V2-P4-004's measurement, 2026-08-14. Not a round number on purpose: a ceiling chosen to
# clear a *measured* market is a different claim from one chosen because it looks big.
WHOLE_MARKET_LISTED = 5_545

# One whole-market batch's own bookkeeping, with a fake runner, on the machine that wrote
# this. Pre-fix it was ~33 minutes for this N (measured: 64.8s at N=1,000 with a no-op
# runner, growing quadratically); post-fix it is ~4s. The budget is generous against the
# latter and unreachable by the former, which is the point -- it is a regression tripwire
# for the O(N^2) whole-task rewrite, not a benchmark.
WHOLE_MARKET_SECONDS_BUDGET = 60.0


@dataclass(frozen=True)
class _FakeDecision:
    decision_id: str
    final_action: str


@dataclass(frozen=True)
class _FakeSignal:
    signal_id: str


@dataclass(frozen=True)
class _FakeResult:
    """The three attributes `BatchResearchService._run_item` reads off a run result.

    A fake rather than a real `ResearchRunResult` because the per-item work must stay
    trivial at this scale (the brief: make the per-item work trivial rather than reducing
    the scale). The values are still per-item *distinct* and still go through
    `BatchResultRef`'s real validation when persisted -- a single shared result object
    would let the round-trip assertions below pass without ever proving that each item's
    own outcome was stored against that item.
    """

    decision: _FakeDecision
    signal: _FakeSignal


def _fake_runner(request: ResearchRunRequest) -> ResearchRunResult:
    return cast(
        ResearchRunResult,
        _FakeResult(
            decision=_FakeDecision(decision_id=f"decision-{request.run_id}", final_action="watch"),
            signal=_FakeSignal(signal_id=f"signal-{request.run_id}"),
        ),
    )


@pytest.fixture
def research_request(frozen_now: datetime) -> Callable[[int], ResearchRunRequest]:
    """Build the i-th distinct whole-market request.

    Indexed by an int rather than by `(run_id, subject)` like
    `tests/integration/test_batch_research.py`'s same-named fixture: at 5,545 items the
    caller has no interesting per-item names to supply, only a count, while every item
    must still be distinct -- `run_id` is the batch's per-item recovery key.
    """

    def _make(index: int) -> ResearchRunRequest:
        subject = f"{index % 1_000_000:06d}.SZ"
        evidence = EvidenceSnapshot(
            subject=subject,
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="whole.market.fixture",
            source_uri=f"fixture://{subject}",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Whole-market scale fixture.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchRunRequest(
            run_id=f"whole-market-{index:06d}",
            mode="replay",
            subject=subject,
            as_of=frozen_now,
            evidence=(evidence,),
            code_commit="0123456789abcdef",
            config_digest="b" * 64,
            random_seed=7,
        )

    return _make


def test_whole_market_batch_is_expressible_at_the_http_boundary(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """A 5,545-item body must get past `BatchSubmitRequest`, i.e. must not be a 422.

    The batch id is deliberately claimed *before* the big POST, so the route validates the
    whole 5,545-item body and then fails on the duplicate id (409) rather than going on to
    run 5,545 real `ResearchEngine` cycles inside a test. That keeps this test on the thing
    that was broken -- the request model's ceiling -- and off the ~80s of real research work
    executing them would cost; `test_whole_market_batch_completes` is what proves a batch
    this size actually finishes.

    422-versus-409 is the entire assertion: at `5e18791` this body was rejected by
    `max_length=1000` before the route's own duplicate check could ever be reached.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: frozen_now))
    claimed = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "whole-market",
            "requests": [research_request(0).model_dump(mode="json", exclude_computed_fields=True)],
            "max_concurrency": 1,
        },
    )
    assert claimed.status_code == 202, claimed.text

    payloads: list[dict[str, Any]] = [
        research_request(index).model_dump(mode="json", exclude_computed_fields=True)
        for index in range(1, WHOLE_MARKET_LISTED + 1)
    ]
    response = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "whole-market",
            "requests": payloads,
            "max_concurrency": MAX_BATCH_WORKERS,
        },
    )

    # `response.text` echoes the whole rejected body, which is ~4MB at this scale, so the
    # failure message quotes only the head of it.
    detail = response.text[:400]
    assert len(payloads) == WHOLE_MARKET_LISTED
    assert response.status_code != 422, detail
    assert response.status_code == 409, detail


def test_whole_market_batch_completes(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """5,545 items reach a terminal state, each item's own outcome durably persisted.

    What is under test is the batch's *persistence*, not research: at `5e18791` every one
    of the 2N item transitions re-parsed and re-serialized the entire task, which is
    O(N^2) and measured at ~33 minutes for this N even with a runner that does nothing.
    The wall-clock assertion is what stops that coming back silently.
    """
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=_fake_runner, clock=lambda: frozen_now)
    requests = [research_request(index) for index in range(WHOLE_MARKET_LISTED)]
    service.submit(
        batch_id="whole-market",
        requests=requests,
        max_concurrency=MAX_BATCH_WORKERS,
    )

    started = time.monotonic()
    completed = service.run("whole-market")
    elapsed = time.monotonic() - started

    assert len(completed.items) == WHOLE_MARKET_LISTED
    assert completed.status == "succeeded"
    assert {item.status for item in completed.items} == {"succeeded"}
    assert [item.result.decision_id for item in completed.items if item.result is not None] == [
        f"decision-{request.run_id}" for request in requests
    ]
    reopened = SQLiteBatchTaskStore(tmp_path / "state.sqlite3").get("whole-market")
    assert reopened is not None
    assert reopened == completed
    assert elapsed < WHOLE_MARKET_SECONDS_BUDGET, f"whole-market batch took {elapsed:.1f}s"


def test_the_declared_item_ceiling_is_one_the_durable_contract_actually_holds(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """A task at exactly `MAX_BATCH_ITEMS` must construct, persist, and read back intact.

    Three places state this ceiling and all three must agree, or a batch is rejected by
    whichever states the smallest: `BatchSubmitRequest.requests` (`api/app.py`),
    `BatchResearchTask.items` (`batch_contracts.py`), and the service. Asserting
    `MAX_BATCH_ITEMS >= WHOLE_MARKET_LISTED` alone would pass while the durable contract
    still capped at 1,000 -- so this builds a task *at* the declared ceiling and requires
    the contract and the store to admit it, i.e. the cap is a number that has been run
    rather than a number that has been typed.
    """
    assert MAX_BATCH_ITEMS >= WHOLE_MARKET_LISTED
    at_ceiling = BatchResearchTask(
        batch_id="ceiling",
        items=tuple(
            BatchTaskItem(request=research_request(index)) for index in range(MAX_BATCH_ITEMS)
        ),
        status="queued",
        max_concurrency=MAX_BATCH_WORKERS,
        created_at=frozen_now,
        updated_at=frozen_now,
    )

    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    store.save(at_ceiling)

    assert len(at_ceiling.items) == MAX_BATCH_ITEMS
    restored = store.get("ceiling")
    assert restored is not None
    assert restored == at_ceiling
