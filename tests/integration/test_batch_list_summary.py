"""`GET /api/v1/research/batches` must answer with summaries, not inlined items (V2-P4-040).

At `be262ea` the route was one line -- `return batch_store.list()` -- so every batch came back
with every one of its items inlined. `V2-P4-019` raised `MAX_BATCH_ITEMS` from 1,000 to 10,000
and this route did not follow, which turned a listing into a bulk export: the P4 product
acceptance measured 20 whole-market batches at `items: 115,355, bytes: 36,857,096` (36.9 MB) in
2.35s, and **8.7 MB at three batches** -- past the 8 MB this same service refuses on the way *in*
(`config.max_request_bytes`). A service that emits a body it would not accept has two ceilings
that disagree.

Reproduced here at `be262ea` with heavier per-item evidence: three batches of 5,545 items came
back as **17,693,518 bytes (17.7 MB) in 1.43s**.

**What separates a fix from a coincidence.** Dropping `items` makes the body small no matter what
the fix is, so "the body is small" cannot on its own tell a summary apart from a route that
silently lost data. The load-bearing assertion is therefore
`test_the_listing_size_does_not_follow_the_item_count`: the same batch count at a **forty-fold**
item count must produce a body of the same order, *while* the per-status counts still add up to
the real item total. A route that dropped the items without counting them fails the second half;
a route that kept them fails the first.

Nothing consumed this route before this issue -- no test, no `sdk.py` method, no page under
`web/` -- which is why the defect shipped, and is also why changing its projection breaks no
caller in this repository.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openalpha_cn.api.app import create_app
from openalpha_cn.batch_contracts import (
    BatchItemCensus,
    BatchResearchTask,
    BatchResultRef,
    BatchTaskItem,
    BatchTaskSummary,
)
from openalpha_cn.config import load_config
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import Timeline
from openalpha_cn.storage.batch import SQLiteBatchTaskStore

NOW: Final[datetime] = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _request(index: int) -> ResearchRunRequest:
    """One distinct request carrying the evidence a real batch item carries.

    The evidence payload is what makes an item expensive to inline, so a fixture without one
    would under-state the very thing this issue is about.
    """
    subject = f"{index % 1_000_000:06d}.SZ"
    evidence = EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(event_time=NOW, available_time=NOW, ingested_time=NOW, revision_time=NOW),
        source_id="batch.listing.fixture",
        source_uri=f"fixture://{subject}",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Batch listing fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )
    return ResearchRunRequest(
        run_id=f"listing-{index:06d}",
        mode="replay",
        subject=subject,
        as_of=NOW,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest="b" * 64,
        random_seed=7,
    )


def _item(index: int, *, status: str) -> BatchTaskItem:
    if status == "succeeded":
        return BatchTaskItem(
            request=_request(index),
            status="succeeded",
            result=BatchResultRef(
                decision_id=f"decision-{index:06d}",
                signal_id=f"signal-{index:06d}",
                final_action="watch",
            ),
        )
    if status == "failed":
        return BatchTaskItem(request=_request(index), status="failed", error_type="ProviderTimeout")
    return BatchTaskItem(request=_request(index), status=status)  # type: ignore[arg-type]


@pytest.fixture
def seed_batches(tmp_path: Path) -> Callable[..., SQLiteBatchTaskStore]:
    """Persist `batches` batches of `per_batch` items through the real store."""

    def _seed(
        *, batches: int, per_batch: int, statuses: tuple[str, ...] = ("succeeded",)
    ) -> SQLiteBatchTaskStore:
        store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
        for batch in range(batches):
            items = tuple(
                _item(batch * per_batch + index, status=statuses[index % len(statuses)])
                for index in range(per_batch)
            )
            store.save(
                BatchResearchTask(
                    batch_id=f"batch-{batch:03d}",
                    items=items,
                    status="succeeded",
                    max_concurrency=8,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        return store

    return _seed


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))


def test_the_listing_answers_summaries_and_never_inlines_an_item(
    tmp_path: Path, seed_batches: Callable[..., SQLiteBatchTaskStore]
) -> None:
    """Three batches, mixed item states, and the listing carries counts rather than items.

    The per-status counts are asserted against the seeded mix rather than against a total, so a
    route that answered a plausible-looking constant would fail.
    """
    seed_batches(batches=3, per_batch=9, statuses=("succeeded", "failed", "cancelled"))

    response = _client(tmp_path).get("/api/v1/research/batches")

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict), "the listing must be an envelope, not a bare array"
    assert body["total"] == 3
    assert [entry["batch_id"] for entry in body["batches"]] == [
        "batch-000",
        "batch-001",
        "batch-002",
    ]
    first = body["batches"][0]
    assert "items" not in first, "the listing must not inline a batch's items"
    assert first["status"] == "succeeded"
    assert first["max_concurrency"] == 8
    assert first["cancellation_requested"] is False
    assert first["item_count"] == 9
    assert first["items_by_status"] == {
        "queued": 0,
        "running": 0,
        "succeeded": 3,
        "failed": 3,
        "cancelled": 3,
    }
    assert sum(first["items_by_status"].values()) == first["item_count"]


def test_the_listing_size_does_not_follow_the_item_count(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The measurement this row is: a fortyfold item count must not move the body's size.

    Two whole app instances rather than one store reseeded, so the two bodies are two real
    responses. The counts are asserted on both, which is what stops "small because it dropped
    the data" from passing as "small because it summarised it".
    """
    sizes: dict[int, int] = {}
    counts: dict[int, int] = {}
    for per_batch in (50, 2_000):
        root = tmp_path_factory.mktemp(f"listing-{per_batch}")
        store = SQLiteBatchTaskStore(root / "state.sqlite3")
        for batch in range(3):
            store.save(
                BatchResearchTask(
                    batch_id=f"batch-{batch:03d}",
                    items=tuple(
                        _item(batch * per_batch + index, status="succeeded")
                        for index in range(per_batch)
                    ),
                    status="succeeded",
                    max_concurrency=8,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        response = TestClient(create_app(runtime_dir=root, clock=lambda: NOW)).get(
            "/api/v1/research/batches"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        sizes[per_batch] = len(response.content)
        counts[per_batch] = sum(entry["item_count"] for entry in body["batches"])
        assert all(entry["items_by_status"]["succeeded"] == per_batch for entry in body["batches"])

    assert counts == {50: 150, 2_000: 6_000}, "the summary must still have counted every item"
    assert sizes[2_000] < 4_096, f"listing of 6,000 items was {sizes[2_000]} bytes"
    assert sizes[2_000] - sizes[50] < 256, (
        "the listing grew with the item count: "
        f"{sizes[50]} bytes at 150 items, {sizes[2_000]} bytes at 6,000"
    )


def test_the_listing_stays_inside_the_inbound_ceiling_this_service_enforces(
    tmp_path: Path, seed_batches: Callable[..., SQLiteBatchTaskStore]
) -> None:
    """The self-consistency claim the row makes: what this service emits, it would also accept.

    The bound is read off `config.max_request_bytes` rather than written down, so raising or
    lowering that ceiling moves this assertion with it.
    """
    seed_batches(batches=20, per_batch=500)

    response = _client(tmp_path).get("/api/v1/research/batches")

    assert response.status_code == 200, response.text
    inbound_ceiling = load_config().max_request_bytes
    assert len(response.content) < inbound_ceiling, (
        f"listing was {len(response.content)} bytes against an inbound ceiling of {inbound_ceiling}"
    )
    assert sum(entry["item_count"] for entry in response.json()["batches"]) == 10_000


def test_the_listing_paginates_and_says_what_it_paginated(
    tmp_path: Path, seed_batches: Callable[..., SQLiteBatchTaskStore]
) -> None:
    """`limit`/`offset` select a window, and `total` still reports the whole shelf."""
    seed_batches(batches=7, per_batch=3)
    client = _client(tmp_path)

    window = client.get("/api/v1/research/batches", params={"limit": 2, "offset": 3})

    assert window.status_code == 200, window.text
    body = window.json()
    assert body["total"] == 7
    assert body["limit"] == 2
    assert body["offset"] == 3
    assert [entry["batch_id"] for entry in body["batches"]] == ["batch-003", "batch-004"]

    past_the_end = client.get("/api/v1/research/batches", params={"offset": 99})
    assert past_the_end.status_code == 200
    assert past_the_end.json()["batches"] == []
    assert past_the_end.json()["total"] == 7

    refused = client.get("/api/v1/research/batches", params={"limit": 0})
    assert refused.status_code == 422


def test_the_items_are_still_reachable_one_batch_at_a_time(
    tmp_path: Path, seed_batches: Callable[..., SQLiteBatchTaskStore]
) -> None:
    """The row's own remedy: items move to `GET /batches/{id}`, which must still carry them."""
    seed_batches(batches=2, per_batch=4)
    client = _client(tmp_path)

    listed = client.get("/api/v1/research/batches").json()
    assert "items" not in listed["batches"][0]

    single = client.get("/api/v1/research/batches/batch-001")

    assert single.status_code == 200, single.text
    task = single.json()
    assert task["batch_id"] == "batch-001"
    assert len(task["items"]) == 4
    assert [item["request"]["run_id"] for item in task["items"]] == [
        f"listing-{index:06d}" for index in range(4, 8)
    ]


def test_an_empty_shelf_is_an_empty_page_and_not_a_missing_one(tmp_path: Path) -> None:
    """A deployment that has run no batch answers the envelope, not `404` and not `null`."""
    response = _client(tmp_path).get("/api/v1/research/batches")

    assert response.status_code == 200, response.text
    assert response.json() == {"batches": [], "total": 0, "limit": 50, "offset": 0}
    assert json.loads(response.content)["batches"] == []


def test_a_cancelled_batch_reports_its_cancellation_on_the_summary(
    tmp_path: Path,
) -> None:
    """`cancellation_requested` is on the summary, so a listing can find a stuck batch.

    Asserted against a batch that carries it `True` *and* one that carries it `False`, because a
    summary that hardcoded either would otherwise pass.

    The batches are stored `partial` rather than `running`, and that is not cosmetic: creating
    the app runs `recover_interrupted`, which requeues the items of every *running* task, so a
    fixture seeded `running` would have its census rewritten underneath the assertion by real
    startup behaviour. Two mixed terminal states still exercise the census.
    """
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    for batch_id, requested in (("live", False), ("stopping", True)):
        store.save(
            BatchResearchTask(
                batch_id=batch_id,
                items=(_item(0, status="failed"), _item(1, status="succeeded")),
                status="partial",
                max_concurrency=2,
                cancellation_requested=requested,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    body = _client(tmp_path).get("/api/v1/research/batches").json()

    by_id: dict[str, Any] = {entry["batch_id"]: entry for entry in body["batches"]}
    assert by_id["live"]["cancellation_requested"] is False
    assert by_id["stopping"]["cancellation_requested"] is True
    assert by_id["stopping"]["status"] == "partial"
    assert by_id["stopping"]["items_by_status"]["failed"] == 1
    assert by_id["stopping"]["items_by_status"]["succeeded"] == 1
    assert by_id["stopping"]["items_by_status"]["running"] == 0


def test_a_summary_whose_census_disagrees_with_its_count_is_refused() -> None:
    """`BatchTaskSummary` will not hold a count and a census that contradict each other.

    Driven on the contract rather than through a route, because no route can currently produce
    the disagreement -- `summarize_task` derives `item_count` from the census it just built. That
    is exactly why the invariant is worth stating and worth testing here: it is what stops a
    *second* summary builder, added later against a store that does keep a counter, from
    reporting a total that its own per-status breakdown does not support. A mutation sweep found
    this validator unkilled without this test.
    """
    census = BatchItemCensus(succeeded=3, failed=1)
    assert census.total == 4

    with pytest.raises(ValidationError, match="census sums to 4 against an item_count of 9"):
        BatchTaskSummary(
            batch_id="mismatched",
            status="partial",
            max_concurrency=2,
            created_at=NOW,
            updated_at=NOW,
            item_count=9,
            items_by_status=census,
        )

    honest = BatchTaskSummary(
        batch_id="honest",
        status="partial",
        max_concurrency=2,
        created_at=NOW,
        updated_at=NOW,
        item_count=4,
        items_by_status=census,
    )
    assert honest.item_count == honest.items_by_status.total


def test_a_batch_still_stored_in_the_pre_split_shape_is_counted_and_not_reported_empty(
    tmp_path: Path,
) -> None:
    """A header carrying its own inline `items` must be counted from those.

    `reassemble_task` handles this shape and says why: a database whose
    `split_batch_task_items` migration has not run, because an earlier-ordered migration
    deferred on a table it happens not to have. `tests/integration/storage/
    test_batch_item_split_migration.py` drives the same shape through `get()`/`list()`; this is
    the listing's half of it, and without it a store in that state reports **every batch as
    empty** -- a wrong answer rather than a refused one, and the worst kind for a listing whose
    only content is counts.

    Read through the store rather than over HTTP on purpose: `create_app` runs the migrations,
    so the API cannot be shown this shape at all. That is `test_a_pre_split_batch_reads_back_
    unchanged_through_the_store`'s arrangement, one method over.
    """
    path = tmp_path / "state.sqlite3"
    store = SQLiteBatchTaskStore(path)
    task = BatchResearchTask(
        batch_id="pre-split",
        items=tuple(
            _item(index, status="succeeded" if index % 2 == 0 else "failed") for index in range(5)
        ),
        status="partial",
        max_concurrency=2,
        created_at=NOW,
        updated_at=NOW,
    )
    store.save(task)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE batch_tasks SET payload = ? WHERE batch_id = ?",
            (task.model_dump_json(exclude_computed_fields=True), "pre-split"),
        )
        connection.execute("DELETE FROM batch_task_items WHERE batch_id = ?", ("pre-split",))

    summaries = store.list_summaries(limit=50, offset=0)

    assert len(summaries) == 1
    assert summaries[0].item_count == 5, "a pre-split batch was reported empty"
    assert summaries[0].items_by_status.succeeded == 3
    assert summaries[0].items_by_status.failed == 2
    assert store.get("pre-split") == task
