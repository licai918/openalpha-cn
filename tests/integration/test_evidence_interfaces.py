import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.evidence import parse_serialized_evidence
from openalpha_cn.providers.base import ProviderMetadata, ProviderRequest
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

runner = CliRunner()


def write_source(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "subject": "000001.SZ",
                    "kind": "limit_up",
                    "event_time": "2026-07-24T09:30:00+00:00",
                    "available_time": "2026-07-24T10:00:00+00:00",
                    "ingested_time": "2026-07-24T10:01:00+00:00",
                    "revision_time": "2026-07-24T10:00:00+00:00",
                    "source_uri": "fixture://limit-up/000001.SZ",
                    "summary": "Synthetic limit-up event.",
                    "payload": {
                        "close": 10.5,
                        "pct_change": 9.99,
                        "board_count": 1,
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_cli_and_api_return_the_same_evidence_snapshot(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """`V2-P5-048`: `--runtime-dir` here is not optional either, for the reason eight lines down.

    The comment on the `create_app(...)` call in this same test says the default is "the
    repository's own `runtime/`, so this line initialised real storage and took a migration
    backup on **every run of the suite**", and calls itself "the last executable `create_app()`
    in `tests/` with no runtime directory". It was -- and this `runner.invoke` immediately above
    it was doing the same thing through the other face, because `V2-P5-013` had since made
    `evidence build` *persist* what it prints. Measured on `94a0af2` with
    `OPENALPHA_RUNTIME_DIR` pointed at a probe directory: running this one file wrote
    `state.sqlite3`, a `backups/state.sqlite3.v0.….bak` and
    `evidence/part-dcaf81407e363fb937766ad0.parquet` into it, and against the real default it
    is the developer's own `runtime/` that grows -- one content-addressed evidence part and one
    migration backup, on every run.

    The printed payload is unaffected, which is what makes this a pure containment fix: the
    assertion below compares the CLI's document to the API's, and where the CLI happened to
    persist its copy was never part of that comparison.
    """
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_source(source)

    cli_result = runner.invoke(
        app,
        [
            "evidence",
            "build",
            str(source),
            "--as-of",
            AS_OF.isoformat(),
            "--source-id",
            "user.file",
            "--source-license",
            "user-supplied",
            "--redistribution",
            "restricted",
            "--runtime-dir",
            str(tmp_path / "cli"),
        ],
    )
    assert cli_result.exit_code == 0, cli_result.stdout
    cli_payload = json.loads(cli_result.stdout)

    provider = FileProvider(
        path=source,
        metadata=metadata,
        clock=lambda: AS_OF,
    )
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    # `runtime_dir` is not optional here even though `create_app` gives it a default: the
    # default is the repository's own `runtime/`, so this line initialised real storage and
    # took a migration backup on **every run of the suite**. Measured: that directory held
    # 135 files, and `V2-P4-111` fixed the backup that a no-op migration leaves behind
    # without touching the reason one was being taken at all. This was the last executable
    # `create_app()` in `tests/` with no runtime directory.
    response = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: AS_OF)).post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )

    assert response.status_code == 200
    assert response.json() == cli_payload
    assert response.json()["items"][0]["evidence_id"].startswith("ev_")


def test_the_cli_persists_what_it_built_the_way_the_other_two_faces_do(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """`V2-P5-013`, closing audit `F31`: one verb, one meaning, on all three faces.

    `openalpha evidence build` printed its snapshots and threw them away, while
    `OpenAlphaSDK.build_file_evidence` and `POST /api/v1/evidence/build` both appended to the
    evidence store. Two of three faces agreed and the command line was the odd one out, so a
    caller who built evidence from the terminal and then queried it found nothing and had no way
    to tell "the build produced nothing" from "the build discarded it".

    **The read-back is the assertion and the printed payload is not.** The old command already
    printed the right snapshots -- asserting on stdout was green before this change and after it,
    which is exactly the shape of test this repository has been caught writing. So the evidence is
    fetched back through a *second* face (`OpenAlphaSDK.query_evidence` over the same
    `--runtime-dir`), which is the only thing a store that was never written cannot satisfy.

    The `as_of` handed to the query is a second later than the build's, because
    `EvidenceSnapshot` visibility is point-in-time and a query at exactly `available_time` is a
    boundary question this test has no business being about.
    """
    source = tmp_path / "events.json"
    write_source(source)
    runtime_dir = tmp_path / "runtime"

    result = runner.invoke(
        app,
        [
            "evidence",
            "build",
            str(source),
            "--as-of",
            frozen_now.isoformat(),
            "--source-id",
            "user.file",
            "--source-license",
            "user-supplied",
            "--redistribution",
            "restricted",
            "--runtime-dir",
            str(runtime_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout

    held = OpenAlphaSDK(runtime_dir=runtime_dir).query_evidence(
        as_of=frozen_now + timedelta(seconds=1)
    )

    assert [item.evidence_id for item in held] == [
        item["evidence_id"] for item in json.loads(result.stdout)["items"]
    ], "the command printed evidence it did not store, so a later query cannot find it"
    assert held, "nothing was persisted"


def test_api_exposes_health_and_versioned_openapi(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    client = TestClient(
        create_app(runtime_dir=tmp_path / "runtime", clock=lambda: plain_frozen_now)
    )

    health = client.get("/health")
    assert health.json() == {"status": "ok", "version": "1.0.0"}
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["content-security-policy"].startswith("default-src 'self'")
    schema = client.get("/openapi.json").json()
    assert "/api/v1/evidence/build" in schema["paths"]


def test_api_serves_built_web_assets_without_shadowing_routes(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<h1>OpenAlpha CN</h1>", encoding="utf-8")
    client = TestClient(
        create_app(
            runtime_dir=tmp_path / "runtime", web_dir=web_dir, clock=lambda: plain_frozen_now
        )
    )

    assert client.get("/").text == "<h1>OpenAlpha CN</h1>"
    assert client.get("/health").json()["status"] == "ok"


def test_api_rejects_declared_oversized_request_body(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    client = TestClient(
        create_app(
            runtime_dir=tmp_path / "runtime",
            max_request_bytes=32,
            clock=lambda: plain_frozen_now,
        )
    )

    response = client.post(
        "/api/v1/evidence/build",
        content=b"x" * 33,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    # `V2-P4-043`: the refusal names the knob that raises it, and both sides of the comparison.
    detail = response.json()["detail"]
    assert detail["reason"] == "request_too_large"
    assert "OPENALPHA_MAX_REQUEST_BYTES" in detail["message"]
    assert detail["declared_bytes"] == 33
    assert detail["limit_bytes"] == 32


def test_api_persists_and_queries_built_evidence(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata, clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: AS_OF))

    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )
    queried = client.get(
        "/api/v1/evidence",
        params={"as_of": AS_OF.isoformat(), "subject": "000001.SZ"},
    )

    assert built.status_code == 200
    assert queried.status_code == 200
    assert queried.json() == built.json()


def test_api_runs_research_from_structured_evidence(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata, clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: AS_OF))
    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    ).json()

    response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": "api-golden-run",
            "mode": "live",
            "subject": "000001.SZ",
            "as_of": AS_OF.isoformat(),
            "evidence": built["items"],
            "code_commit": "0123456789abcdef",
            "config_digest": "e" * 64,
            "random_seed": 7,
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"]["final_action"] == "watch"
    assert response.json()["signal"]["evidence_ids"] == [built["items"][0]["evidence_id"]]

    memory = client.get("/api/v1/memory/000001.SZ")
    recovery = client.get("/api/v1/runs/api-golden-run/recovery")

    assert memory.status_code == 200
    assert memory.json()[0]["decision_id"] == response.json()["decision"]["decision_id"]
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "succeeded"
    assert recovery.json()["next_agent_index"] == 1

    validation = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": response.json(),
            "observation": {
                "observation_start": AS_OF.isoformat(),
                "observation_end": (AS_OF + timedelta(days=5)).isoformat(),
                "start_price": 10.0,
                "end_price": 11.0,
                "benchmark_return": 0.02,
                "transaction_cost": 0.005,
                "data_quality_notes": ["Synthetic outcome."],
            },
        },
    )

    assert validation.status_code == 200
    payload = validation.json()
    assert payload["signal_id"] == response.json()["signal"]["signal_id"]
    assert payload["decision_id"] == response.json()["decision"]["decision_id"]
    assert payload["net_active_return"] == pytest.approx(0.075)
    # `V2-P5-005`: the categories this used to name -- one `rule`, one `factor`, one `agent`,
    # worth a fixed 20/30/50 of the net -- were invented, and asserting the *set* of them could
    # not have noticed. The face now reports only what it measured, and says so as a number: a
    # held position leaves `realized - benchmark` unattributed rather than splitting it.
    assert [(term["category"], term["name"]) for term in payload["attribution"]] == [
        ("rule", "transaction-cost")
    ]
    assert payload["attribution"][0]["contribution"] == pytest.approx(-0.005)
    assert payload["unexplained_return"] == pytest.approx(0.08)
    assert sum(term["contribution"] for term in payload["attribution"]) + payload[
        "unexplained_return"
    ] == pytest.approx(payload["net_active_return"])

    tampered = response.json()
    tampered["signal"]["signal_id"] = "sig_tampered"
    rejected = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": tampered,
            "observation": {
                "observation_start": AS_OF.isoformat(),
                "observation_end": (AS_OF + timedelta(days=5)).isoformat(),
                "start_price": 10.0,
                "end_price": 11.0,
                "benchmark_return": 0.02,
                "transaction_cost": 0.005,
            },
        },
    )
    assert rejected.status_code == 422
    # `V2-P4-041`: the refusal names which of the three content addresses moved, and on which
    # record, instead of one sentence for all four causes.
    detail = rejected.json()["detail"]
    assert detail["reason"] == "signal_id_mismatch"
    assert detail["index"] is None
    assert detail["field"] == "research.signal.signal_id"
    assert detail["claimed"] != detail["derived"]


# --- serialized evidence handed back to the research route (`OA-EVID-003`) ---------------------

# `parse_serialized_evidence`'s own two sentences (`evidence/service.py`). `openalpha research run`
# has always printed them verbatim; the tests below hold the REST route to the same words.
EVIDENCE_ID_REFUSAL = "serialized evidence_id does not match evidence content"
CONTENT_HASH_REFUSAL = "serialized content_hash does not match evidence content"


def _built_items(
    client: TestClient, source: Path, metadata: ProviderMetadata, as_of: datetime
) -> list[dict[str, Any]]:
    """What `POST /api/v1/evidence/build` hands a client: items still carrying both identifiers."""
    provider = FileProvider(path=source, metadata=metadata, clock=lambda: as_of)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=as_of))
    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )
    assert built.status_code == 200, built.text
    items: list[dict[str, Any]] = built.json()["items"]
    assert all({"evidence_id", "content_hash"} <= set(item) for item in items), items
    return items


def _research_body(evidence: object, *, run_id: str, as_of: datetime) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": "live",
        "subject": "000001.SZ",
        "as_of": as_of.isoformat(),
        "evidence": evidence,
        "code_commit": "0123456789abcdef",
        "config_digest": "e" * 64,
        "random_seed": 7,
    }


def _edit_a_fact(item: dict[str, Any]) -> dict[str, Any]:
    """The content edited and both identifiers left as they were.

    A fact rather than `summary`, deliberately: `content_hash` digests `payload` alone and
    `evidence_id` adds only subject, kind, source_id and available_time, so an edited `summary`
    leaves both identifiers true and no recomputation can see it (measured:
    `parse_serialized_evidence` accepts one).
    """
    payload = item["payload"]
    return {**item, "payload": {**payload, "facts": {**payload["facts"], "close": 99.0}}}


def _edit_the_evidence_id(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "evidence_id": "ev_" + "0" * 24}


def _edit_the_content_hash(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "content_hash": "0" * 64}


@pytest.mark.parametrize(
    ("tamper", "refusal"),
    [
        pytest.param(_edit_a_fact, EVIDENCE_ID_REFUSAL, id="fact-edited-identifiers-kept"),
        pytest.param(_edit_the_evidence_id, EVIDENCE_ID_REFUSAL, id="evidence_id-edited"),
        pytest.param(_edit_the_content_hash, CONTENT_HASH_REFUSAL, id="content_hash-edited"),
    ],
)
def test_the_rest_research_route_recomputes_supplied_identifiers_before_accepting_them(
    tmp_path: Path,
    metadata: ProviderMetadata,
    frozen_now: datetime,
    tamper: Callable[[dict[str, Any]], dict[str, Any]],
    refusal: str,
) -> None:
    """`OA-EVID-003`: API output is taken back as input, and a tampered copy fails the recompute.

    `ResearchApiRequest.verify_serialized_evidence` wrapped `parse_serialized_evidence` in
    `except ValueError: return value`, and pydantic's `ValidationError` is itself a `ValueError`,
    so that one clause caught the parser's mismatch refusal along with every structural fault.
    The tampered item was still refused -- it fell back to field validation, where
    `EvidenceSnapshot`'s `extra="forbid"` rejected its `evidence_id` and `content_hash` -- but
    the client was told `Extra inputs are not permitted` about two fields this service writes
    itself, rather than that they do not describe the content beside them.

    The untampered round trip runs first in every case and is the control: a route refusing
    *every* serialized item would satisfy the refusal half on its own. The refusal is then held
    to exactly one fault -- the parser's sentence at `["body", "evidence"]`, not
    `extra_forbidden` -- and to having started no run.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    tampered = tamper(item)
    assert tampered != item

    accepted = client.post(
        "/api/v1/research/run",
        json=_research_body([item], run_id="verified-round-trip", as_of=frozen_now),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["signal"]["evidence_ids"] == [item["evidence_id"]]

    refused = client.post(
        "/api/v1/research/run",
        json=_research_body([tampered], run_id="tampered-round-trip", as_of=frozen_now),
    )
    assert refused.status_code == 422, refused.text
    faults = refused.json()["detail"]
    assert isinstance(faults, list), faults
    assert [(fault["type"], fault["loc"]) for fault in faults] == [
        ("value_error", ["body", "evidence"])
    ], faults
    assert faults[0]["msg"].removeprefix("Value error, ") == f"evidence[0]: {refusal}"
    assert client.get("/api/v1/runs/tampered-round-trip/recovery").status_code == 404


def test_the_rest_research_route_accepts_evidence_with_its_identifiers_or_without_them(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """The two shapes a client legitimately sends, pinned apart from the refusal above.

    With both identifiers is exactly what `POST /api/v1/evidence/build` returned. Without them is
    a record a caller wrote by hand: there is nothing to verify, and both identifiers are derived
    from its content -- which is what content addressing means, not a gap in the check. Both must
    reach the same cited evidence.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    bare = {key: value for key, value in item.items() if key not in {"evidence_id", "content_hash"}}

    for run_id, evidence in (("with-identifiers", [item]), ("without-identifiers", [bare])):
        response = client.post(
            "/api/v1/research/run", json=_research_body(evidence, run_id=run_id, as_of=frozen_now)
        )
        assert response.status_code == 200, (run_id, response.text)
        assert response.json()["signal"]["evidence_ids"] == [item["evidence_id"]], run_id


def test_a_structural_fault_in_serialized_evidence_keeps_its_field_level_address(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """Only the mismatch stopped falling back; every structural fault still gets its address.

    The parser builds one item at a time, so a fault it raises has already lost its index; the
    route hands the body back to the field's own validation, which names the item and the field.
    The broken item sits at index 1 behind a good one so that a constant `0` cannot pass, and a
    non-object item is included because narrowing the catch to pydantic's `ValidationError` --
    the other way to stop swallowing the mismatch -- would answer that one at
    `["body", "evidence"]`, with no index.

    Deliberately not asserted either way: the `extra_forbidden` the fallback also reports for the
    `evidence_id`/`content_hash` those items carry. The body goes back unchanged on purpose, since
    that is what keeps an item with an identifier from getting in by any road but the check, and
    stripping them to quiet the noise would trade that guarantee for a tidier message.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    unsummarised = {key: value for key, value in item.items() if key != "summary"}

    for evidence, address in (
        ([item, unsummarised], ["body", "evidence", 1, "summary"]),
        ([item, 7], ["body", "evidence", 1]),
        ("not-an-array", ["body", "evidence"]),
    ):
        response = client.post(
            "/api/v1/research/run",
            json=_research_body(evidence, run_id="structural-fault", as_of=frozen_now),
        )
        assert response.status_code == 422, response.text
        faults = response.json()["detail"]
        assert isinstance(faults, list), faults
        assert address in [fault["loc"] for fault in faults], (address, faults)
        assert not [fault for fault in faults if "does not match" in fault["msg"]], faults


def test_every_face_refuses_one_tampered_payload_and_those_that_verify_it_say_so_alike(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """One edited `content_hash`, handed to every door this repository has for serialized evidence.

    `openalpha research run` always reported the parser's own sentence: `cli.py` catches
    `ValidationError` and `ValueError` separately, and its docstring says why. The REST route now
    reports the same sentence on both of its doors, because `/research/batches` validates each
    request with the same `ResearchApiRequest`.

    The SDK has no door that takes a supplied identifier. `run_research` and `run_batch` are typed
    on `ResearchRunRequest`, whose `EvidenceSnapshot` derives both identifiers and refuses one it
    is handed, so a tampered identifier cannot reach the SDK and there is no check there to
    swallow. What the SDK side has is the helper `openalpha_cn.evidence` exports for callers
    holding serialized evidence -- the function the other two faces call -- and its sentence is
    asserted here so the three cannot drift apart.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    tampered = _edit_the_content_hash(item)
    body = _research_body([tampered], run_id="tampered-everywhere", as_of=frozen_now)

    run = client.post("/api/v1/research/run", json=body)
    batch = client.post(
        "/api/v1/research/batches",
        json={"batch_id": "tampered-everywhere", "requests": [body], "max_concurrency": 1},
    )
    assert run.status_code == 422, run.text
    assert batch.status_code == 422, batch.text
    said_by_run = [
        (fault["loc"], fault["msg"].removeprefix("Value error, ")) for fault in run.json()["detail"]
    ]
    said_by_batch = [
        (fault["loc"], fault["msg"].removeprefix("Value error, "))
        for fault in batch.json()["detail"]
    ]
    assert said_by_run == [(["body", "evidence"], f"evidence[0]: {CONTENT_HASH_REFUSAL}")], (
        said_by_run
    )
    assert (
        ["body", "requests", 0, "evidence"],
        f"evidence[0]: {CONTENT_HASH_REFUSAL}",
    ) in said_by_batch, said_by_batch

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"items": [tampered]}), encoding="utf-8")
    cli = runner.invoke(
        app,
        [
            "research",
            "run",
            str(evidence_path),
            "--runtime-dir",
            str(tmp_path / "cli"),
            "--run-id",
            "tampered-everywhere",
            "--mode",
            "live",
            "--subject",
            "000001.SZ",
            "--as-of",
            frozen_now.isoformat(),
            "--code-commit",
            "0123456789abcdef",
            "--config-digest",
            "e" * 64,
        ],
    )
    assert cli.exit_code == 1, cli.output
    assert CONTENT_HASH_REFUSAL in cli.output

    with pytest.raises(ValidationError) as typed_door:
        ResearchRunRequest.model_validate(body)
    assert {error["loc"][0] for error in typed_door.value.errors()} == {"evidence"}
    with pytest.raises(ValueError, match=re.escape(CONTENT_HASH_REFUSAL)):
        parse_serialized_evidence([tampered])


# --- Minor-1 (`D10` review, `.superpowers/sdd/d10-review.md`): order independence ---------------


def test_a_structural_fault_before_a_tampered_item_no_longer_hides_the_mismatch(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """`parse_serialized_evidence` builds one item at a time and used to stop at the first
    fault it met, whatever kind it was. A structurally invalid item ahead of a tampered one
    meant the *structural* fault was raised first, `ResearchApiRequest.verify_serialized_evidence`
    fell back to field validation on the untouched body, and the tampered item -- now judged only
    by `EvidenceSnapshot`'s own `extra="forbid"` -- was reported as `extra_forbidden` on its
    `evidence_id`/`content_hash` instead of by the mismatch sentence. That is exactly the
    fallback `OA-EVID-003` exists to close off; it was only reopened when a structural fault
    happened to sit earlier in the array.

    `[missing-summary item, tampered item]` must still find the mismatch and report only it,
    naming the tampered item's own position in `evidence` (`1`), and must not also report the
    missing-summary item as `extra_forbidden` or anything else.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    unsummarised = {key: value for key, value in item.items() if key != "summary"}
    tampered = _edit_the_content_hash(item)

    response = client.post(
        "/api/v1/research/run",
        json=_research_body(
            [unsummarised, tampered], run_id="structural-then-tampered", as_of=frozen_now
        ),
    )

    assert response.status_code == 422, response.text
    faults = response.json()["detail"]
    assert isinstance(faults, list), faults
    assert [(fault["type"], fault["loc"]) for fault in faults] == [
        ("value_error", ["body", "evidence"])
    ], faults
    assert (
        faults[0]["msg"].removeprefix("Value error, ") == f"evidence[1]: {CONTENT_HASH_REFUSAL}"
    ), faults
    assert client.get("/api/v1/runs/structural-then-tampered/recovery").status_code == 404


def test_a_tampered_item_before_a_structural_fault_now_names_its_own_index(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """The reverse order already reported the mismatch sentence -- the tampered item is the
    first one `parse_serialized_evidence` meets, so the structural fault behind it was never
    reached -- but the sentence named no index. `[tampered item, missing-summary item]` must
    keep reporting exactly the mismatch, now naming the tampered item's own index (`0`), so a
    caller does not have to guess which of several items was the one that failed to verify.
    """
    source = tmp_path / "events.json"
    write_source(source)
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: frozen_now))
    [item] = _built_items(client, source, metadata, frozen_now)
    unsummarised = {key: value for key, value in item.items() if key != "summary"}
    tampered = _edit_the_content_hash(item)

    response = client.post(
        "/api/v1/research/run",
        json=_research_body(
            [tampered, unsummarised], run_id="tampered-then-structural", as_of=frozen_now
        ),
    )

    assert response.status_code == 422, response.text
    faults = response.json()["detail"]
    assert isinstance(faults, list), faults
    assert [(fault["type"], fault["loc"]) for fault in faults] == [
        ("value_error", ["body", "evidence"])
    ], faults
    assert (
        faults[0]["msg"].removeprefix("Value error, ") == f"evidence[0]: {CONTENT_HASH_REFUSAL}"
    ), faults
    assert client.get("/api/v1/runs/tampered-then-structural/recovery").status_code == 404
