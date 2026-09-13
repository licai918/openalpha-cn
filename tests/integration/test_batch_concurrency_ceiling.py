"""The batch concurrency ceiling is enforced, survivable, and stated in one place.

`V2-P4-019` lowered `MAX_BATCH_WORKERS` from 32 to 8 -- see that constant's docstring in
`batch_contracts.py` for the throughput measurements behind the number, and for the probe
showing that 32 concurrent writers never actually produced `database is locked` (a 10-second
busy handler and WAL were already in place; the error needs a single write transaction to
outlive the timeout, not many short ones).

The third test here is the one that would have caught the original defect. The 1,000-item
ceiling was written twice, independently, in `api/app.py` and in `batch_contracts.py`, and a
fix that raised only one of them would have moved the 422 into a `ValidationError` a layer
deeper without anybody noticing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from annotated_types import Le, MaxLen
from fastapi.testclient import TestClient

from openalpha_cn.api.app import BatchSubmitRequest, create_app
from openalpha_cn.batch_contracts import (
    MAX_BATCH_ITEMS,
    MAX_BATCH_WORKERS,
    BatchResearchTask,
)
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.batch import BatchResearchService
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.batch import SQLiteBatchTaskStore


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
            source_id="ceiling.fixture",
            source_uri=f"fixture://{subject}",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Concurrency ceiling fixture.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchRunRequest(
            run_id=f"ceiling-{index:06d}",
            mode="replay",
            subject=subject,
            as_of=frozen_now,
            evidence=(evidence,),
            code_commit="0123456789abcdef",
            config_digest="b" * 64,
            random_seed=7,
        )

    return _make


def test_a_batch_at_the_declared_ceiling_completes_without_a_locked_database(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """`MAX_BATCH_WORKERS` real research runs, all writing `state.sqlite3` at once.

    The runner is the real `ResearchEngine`, deliberately: it is what actually puts
    `MAX_BATCH_WORKERS` concurrent writers on the one SQLite file the batch store is also
    writing to, which is the configuration this issue predicted would fail. A worker that
    hit `database is locked` would not raise out of `run()` -- `_run_item` catches every
    exception and records its class name on the item -- so asserting only `status ==
    "succeeded"` is not enough on its own; `error_type` is where that failure would surface,
    and it is asserted to be absent by name rather than by implication.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now)
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=sdk.run_research, clock=lambda: frozen_now)
    service.submit(
        batch_id="ceiling",
        requests=[research_request(index) for index in range(4 * MAX_BATCH_WORKERS)],
        max_concurrency=MAX_BATCH_WORKERS,
    )

    completed = service.run("ceiling")

    assert completed.status == "succeeded"
    assert [item.error_type for item in completed.items if item.error_type is not None] == []
    assert sqlite3.OperationalError.__name__ not in {
        item.error_type for item in completed.items if item.error_type
    }
    assert {item.status for item in completed.items} == {"succeeded"}


def test_the_api_refuses_a_concurrency_above_the_declared_ceiling(
    tmp_path: Path,
    research_request: Callable[[int], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    """One over the ceiling is a 422; the ceiling itself is accepted.

    Both directions, because a ceiling asserted only from the rejecting side is equally
    satisfied by a build that rejects everything.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: frozen_now))
    body = {
        "batch_id": "at-ceiling",
        "requests": [research_request(0).model_dump(mode="json", exclude_computed_fields=True)],
        "max_concurrency": MAX_BATCH_WORKERS,
    }

    accepted = client.post("/api/v1/research/batches", json=body)
    refused = client.post(
        "/api/v1/research/batches",
        json={**body, "batch_id": "over-ceiling", "max_concurrency": MAX_BATCH_WORKERS + 1},
    )

    assert accepted.status_code == 202, accepted.text
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"][0]["loc"] == ["body", "max_concurrency"]


def test_both_ceilings_are_stated_exactly_once() -> None:
    """The API request model and the durable task must read the same two constants.

    Read off pydantic's own field metadata rather than off the source text, so a literal
    typed back into either `Field(...)` fails here even though it would still be a number
    of the same shape. This is the anti-drift check for the exact defect `V2-P4-019` fixed:
    `max_length=1000` and `le=32` were each written twice, in two modules, with nothing
    tying them together.
    """
    api_items = [m for m in BatchSubmitRequest.model_fields["requests"].metadata]
    api_workers = [m for m in BatchSubmitRequest.model_fields["max_concurrency"].metadata]
    task_items = [m for m in BatchResearchTask.model_fields["items"].metadata]
    task_workers = [m for m in BatchResearchTask.model_fields["max_concurrency"].metadata]

    assert [m.max_length for m in api_items if isinstance(m, MaxLen)] == [MAX_BATCH_ITEMS]
    assert [m.max_length for m in task_items if isinstance(m, MaxLen)] == [MAX_BATCH_ITEMS]
    assert [m.le for m in api_workers if isinstance(m, Le)] == [MAX_BATCH_WORKERS]
    assert [m.le for m in task_workers if isinstance(m, Le)] == [MAX_BATCH_WORKERS]
