import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.batch import BatchResearchService, BatchResearchTask, BatchTaskItem
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.batch import SQLiteBatchTaskStore


@pytest.fixture
def research_request(frozen_now: datetime) -> Callable[[str, str], ResearchRunRequest]:
    """Not named `request`: that is pytest's own reserved built-in fixture name. Not
    collapsed into a suite-wide fixture either -- this varies `run_id`/`subject` and
    always builds its own single canned evidence item, a different parameterization axis
    from `tests/integration/test_research_cycle.py`'s `research_request(items)`, which
    takes a pre-built evidence tuple with a fixed `run_id`. See task-13-report.md for the
    full comparison.
    """

    def _make(run_id: str, subject: str) -> ResearchRunRequest:
        evidence = EvidenceSnapshot(
            subject=subject,
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="batch.fixture",
            source_uri=f"fixture://{subject}",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Batch fixture.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchRunRequest(
            run_id=run_id,
            mode="replay",
            subject=subject,
            as_of=frozen_now,
            evidence=(evidence,),
            code_commit="0123456789abcdef",
            config_digest="b" * 64,
            random_seed=7,
        )

    return _make


def test_batch_runs_with_bounded_workers_and_persists_progress(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now)
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=sdk.run_research, clock=lambda: frozen_now)
    task = service.submit(
        batch_id="batch-1",
        requests=(
            research_request("batch-run-1", "000001.SZ"),
            research_request("batch-run-2", "600000.SH"),
        ),
        max_concurrency=2,
    )

    completed = service.run(task.batch_id)

    assert completed.status == "succeeded"
    assert {item.status for item in completed.items} == {"succeeded"}
    assert all(item.result is not None for item in completed.items)
    assert SQLiteBatchTaskStore(tmp_path / "state.sqlite3").get(task.batch_id) == completed
    assert [event.kind for event in store.list_events(task.batch_id)] == [
        "submitted",
        "started",
        "item_started",
        "item_started",
        "item_succeeded",
        "item_succeeded",
        "finished",
    ]


def test_batch_cancel_and_interrupted_recovery_are_explicit(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
) -> None:
    path = tmp_path / "state.sqlite3"
    store = SQLiteBatchTaskStore(path)
    service = BatchResearchService(
        store=store,
        runner=OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now).run_research,
        clock=lambda: frozen_now,
    )
    task = service.submit(
        batch_id="batch-cancel",
        requests=(research_request("cancel-run", "000001.SZ"),),
        max_concurrency=1,
    )
    cancelled = service.cancel(task.batch_id)

    assert cancelled.status == "cancelled"
    assert cancelled.items[0].status == "cancelled"

    interrupted = BatchResearchTask(
        batch_id="batch-recover",
        items=(
            BatchTaskItem(request=research_request("recover-run", "000001.SZ"), status="running"),
        ),
        status="running",
        max_concurrency=1,
        created_at=frozen_now,
        updated_at=frozen_now,
    )
    store.save(interrupted)
    recovered = SQLiteBatchTaskStore(path).recover_interrupted(now=frozen_now)

    assert recovered == ("batch-recover",)
    restored = store.get("batch-recover")
    assert restored is not None
    assert restored.status == "queued"
    assert restored.items[0].status == "queued"


# --- batch lifecycle logging (V2-P0B-007) --------------------------------------------------
#
# One of the four call sites the task brief names explicitly: submit / complete / cancel /
# retry / recover. Deliberately at the batch level, not per research item -- `_run_item`'s
# own per-item outcome is already durably recorded via `BatchTaskStore.append_event()`
# (unrelated to the stdlib `logging` module this task wires up), and logging every item
# would be exactly the "every agent call" hot-path noise the brief says not to add.


def test_batch_lifecycle_logs_submit_run_and_finish_events(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now)
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=sdk.run_research, clock=lambda: frozen_now)
    caplog.set_level(logging.INFO, logger="openalpha_cn.runtime.batch")

    task = service.submit(
        batch_id="batch-logged",
        requests=(research_request("batch-logged-run", "000001.SZ"),),
        max_concurrency=1,
    )
    completed = service.run(task.batch_id)

    assert completed.status == "succeeded"
    submitted = [r for r in caplog.records if r.message == "batch_submitted"]
    assert len(submitted) == 1
    assert submitted[0].batch_id == "batch-logged"  # type: ignore[attr-defined]
    assert submitted[0].item_count == 1  # type: ignore[attr-defined]

    started = [r for r in caplog.records if r.message == "batch_run_started"]
    assert len(started) == 1
    assert started[0].batch_id == "batch-logged"  # type: ignore[attr-defined]
    assert started[0].retry is False  # type: ignore[attr-defined]

    finished = [r for r in caplog.records if r.message == "batch_finished"]
    assert len(finished) == 1
    assert finished[0].batch_id == "batch-logged"  # type: ignore[attr-defined]
    assert finished[0].status == "succeeded"  # type: ignore[attr-defined]


def test_batch_run_started_reports_retry_true_when_rerun_after_a_failure(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts: list[int] = []

    def _fail_once(request: ResearchRunRequest) -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
        return OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now).run_research(request)

    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=_fail_once, clock=lambda: frozen_now)
    task = service.submit(
        batch_id="batch-retry",
        requests=(research_request("batch-retry-run", "000001.SZ"),),
        max_concurrency=1,
    )
    first = service.run(task.batch_id)
    assert first.status == "failed"

    caplog.set_level(logging.INFO, logger="openalpha_cn.runtime.batch")
    second = service.run(task.batch_id)

    assert second.status == "succeeded"
    started = [r for r in caplog.records if r.message == "batch_run_started"]
    assert len(started) == 1
    assert started[0].retry is True  # type: ignore[attr-defined]


def test_batch_cancel_logs_a_cancel_requested_event(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(
        store=store,
        runner=OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now).run_research,
        clock=lambda: frozen_now,
    )
    task = service.submit(
        batch_id="batch-cancel-logged",
        requests=(research_request("cancel-logged-run", "000001.SZ"),),
        max_concurrency=1,
    )
    caplog.set_level(logging.INFO, logger="openalpha_cn.runtime.batch")

    service.cancel(task.batch_id)

    cancelled = [r for r in caplog.records if r.message == "batch_cancel_requested"]
    assert len(cancelled) == 1
    assert cancelled[0].batch_id == "batch-cancel-logged"  # type: ignore[attr-defined]


def test_batch_recover_interrupted_logs_a_recovered_event_per_batch(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "state.sqlite3"
    store = SQLiteBatchTaskStore(path)
    interrupted = BatchResearchTask(
        batch_id="batch-recover-logged",
        items=(
            BatchTaskItem(
                request=research_request("recover-logged-run", "000001.SZ"), status="running"
            ),
        ),
        status="running",
        max_concurrency=1,
        created_at=frozen_now,
        updated_at=frozen_now,
    )
    store.save(interrupted)
    caplog.set_level(logging.INFO, logger="openalpha_cn.storage.batch")

    recovered = SQLiteBatchTaskStore(path).recover_interrupted(now=frozen_now)

    assert recovered == ("batch-recover-logged",)
    recovered_events = [r for r in caplog.records if r.message == "batch_recovered"]
    assert len(recovered_events) == 1
    assert recovered_events[0].batch_id == "batch-recover-logged"  # type: ignore[attr-defined]


def test_batch_lifecycle_logs_never_leak_a_failed_items_exception_message(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
    frozen_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sentinel-driven leak proof: a research run can fail with any exception --
    including one whose message embeds a model provider's credential (see
    `agents/model.py#ModelProviderFailure`). Batch-level lifecycle logs
    (`batch_submitted`/`batch_run_started`/`batch_finished`) only ever carry
    `batch_id`, item counts, and the aggregate status string -- never a per-item
    exception -- so the sentinel must never reach them.
    """
    secret = "sk-batch-lifecycle-log-must-not-leak-55221"

    def _always_fails(request: ResearchRunRequest) -> object:
        raise RuntimeError(f"upstream rejected token={secret}")

    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=_always_fails, clock=lambda: frozen_now)
    caplog.set_level(logging.INFO, logger="openalpha_cn")

    task = service.submit(
        batch_id="batch-leak-check",
        requests=(research_request("leak-check-run", "000001.SZ"),),
        max_concurrency=1,
    )
    completed = service.run(task.batch_id)

    assert completed.status == "failed"
    assert secret not in caplog.text


def _serialized_payload(
    run_id: str,
    subject: str,
    research_request: Callable[[str, str], ResearchRunRequest],
) -> dict[str, object]:
    """Build a research-run payload whose evidence carries its computed IDs.

    This mirrors what a client naturally has after calling `/api/v1/evidence/build`
    (or `/api/v1/evidence`) and feeding the response straight back in as a research
    request: each evidence item's serialized dict includes `evidence_id` and
    `content_hash`. Only `ResearchApiRequest`'s lenient `verify_serialized_evidence`
    validator (`api/app.py`) knows how to accept those extra computed fields; a bare
    `ResearchRunRequest` (`extra="forbid"` all the way down through `EvidenceSnapshot`)
    rejects them.
    """
    built = research_request(run_id, subject)
    payload = built.model_dump(mode="json", exclude_computed_fields=True)
    payload["evidence"] = [item.model_dump(mode="json") for item in built.evidence]
    return payload


def test_single_and_batch_endpoints_accept_the_same_serialized_evidence_payload(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
) -> None:
    """`POST /api/v1/research/run` and `POST /api/v1/research/batches` both ultimately
    validate a `ResearchRunRequest`, so the same serialized payload -- evidence items
    still carrying their `evidence_id`/`content_hash` computed fields, exactly as a
    client would naturally pass through from a prior evidence response -- must be
    accepted by both. Before the fix, `BatchSubmitRequest.requests` used a bare
    `ResearchRunRequest` while `/research/run` used the lenient `ResearchApiRequest`,
    so the identical payload succeeded on one endpoint and failed 422 on the other
    (audit F32)."""
    client = TestClient(create_app(runtime_dir=tmp_path))
    single_payload = _serialized_payload("serialized-payload-single", "000001.SZ", research_request)
    batch_payload = _serialized_payload(
        "serialized-payload-batch-item", "000001.SZ", research_request
    )

    single_response = client.post("/api/v1/research/run", json=single_payload)
    batch_response = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "serialized-payload-batch",
            "requests": [batch_payload],
            "max_concurrency": 1,
        },
    )

    assert single_response.status_code == 200, single_response.text
    assert batch_response.status_code == 202, batch_response.text


def test_batch_http_surface_submits_runs_and_exposes_events(
    tmp_path: Path,
    research_request: Callable[[str, str], ResearchRunRequest],
) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path))
    response = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "batch-api",
            "requests": [
                research_request("batch-api-run", "000001.SZ").model_dump(
                    mode="json", exclude_computed_fields=True
                )
            ],
            "max_concurrency": 1,
        },
    )

    assert response.status_code == 202
    task = client.get("/api/v1/research/batches/batch-api").json()
    assert task["status"] == "succeeded"
    events = client.get("/api/v1/research/batches/batch-api/events").json()
    assert events[-1]["kind"] == "finished"
