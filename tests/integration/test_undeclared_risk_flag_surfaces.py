"""What every face does with an evidence payload naming a flag the build never declared.

`V2-P4-101` / `V2-P4-102`. `V2-P4-030` closed the risk-flag vocabulary and was right to:
`agents/baseline.py::_quality_flags` now refuses an undeclared string instead of scoring it
*above* the flag it was a misspelling of. What it did not do is give the refusal a delivery, and
`_quality_flags`' own docstring names five paths that reach it from outside the process.

## The hole this file closes, stated as the acceptance measured it

`tests/unit/agents/test_baseline_quality_flags.py` is the only file in the repository that
exercises an undeclared flag, and it contains zero occurrences of `TestClient`, `CliRunner` or
`OpenAlphaSDK` -- it drives `MarketAgent().analyze(...)` directly. So the refusal was held at the
one boundary nobody stands on, and every boundary a user *does* stand on was unheld. Measured on
`d748796`, with the evidence payload shaped
`{"schema", "family", "facts", "quality_flags"}` (the first two are required or the evidence is
dropped before it reaches this code at all):

| surface                         | `future_data` | `future-data`                                |
|---------------------------------|---------------|----------------------------------------------|
| `POST /api/v1/research/run`     | `200`         | **`500` `text/plain` `Internal Server Error`** |
| `openalpha research run`        | exit `0`      | **exit `1`, rich traceback, no message**     |
| `POST /api/v1/research/batches` | `succeeded`   | **`{"error_type": "ValueError"}`, nothing**  |
| `POST /api/v1/backtests/replay` | `succeeded`   | `failures[0]` carries the whole reason       |

**The fourth row is why this file drives four surfaces rather than three.** `ReplayRunner.run()`
already catches per case and records `f"{case.run_id}: {type(error).__name__}: {error}"`, so the
one route the `_quality_flags` docstring names alongside the other two was never broken -- it is
the *model* for what the other three now do, and `test_the_replay_route_already_carried_the_whole
_reason_and_still_does` is here to keep it that way rather than to fix it.

## Why a named exception and not `except ValueError`

`except ValueError` around a route is wide enough to swallow an unrelated arithmetic failure and
report it as a caller's spelling mistake -- the over-broad catch `V2-P4-045` booked as a defect
on the shortlist face. `UndeclaredRiskFlagError` is a `ValueError` subclass for
`LookAheadViolationError`'s reason (`domain/evidence.py`): every call site that already wrote
`except ValueError` keeps catching it unchanged -- which is exactly why the replay row above was
already correct -- while the three that need to say something specific can name it and nothing
else.

## The bar the REST refusal is held to

`POST /api/v1/research/deliberate` already refuses `signal.risk_flags = ["future-data"]` through
pydantic's own `422`, and that body is the quality bar rather than an analogy: it carries
`loc == ["body", "signal", "risk_flags", 0]`, `input == "future-data"` and a `msg` listing all
ten declared flags. `test_the_two_faces_of_one_vocabulary_refuse_the_same_string_the_same_way`
asserts the two bodies agree field for field, so the evidence-plane refusal cannot drift into a
second, thinner dialect of the same `422`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.risk_flag import RISK_FLAGS_BY_VALUE, RiskFlag
from openalpha_cn.domain.time import Timeline

runner = CliRunner()

NOW: Final[datetime] = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
"""One frozen instant for every surface here, so a `signal_id` is comparable across faces."""

COMMIT: Final[str] = "0123456789abcdef"
DIGEST: Final[str] = "a" * 64

MISSPELLED: Final[str] = "future-data"
"""The typo the open set scored *above* the flag it misspells. `V2-P4-030`'s own example."""

INVENTED: Final[str] = "totally_made_up"
"""A string that resembles no declared flag, so a refusal cannot be a near-match heuristic."""

DECLARED: Final[tuple[str, ...]] = tuple(sorted(RISK_FLAGS_BY_VALUE))
"""The whole vocabulary, read off the one declaration rather than written out again here.

Derived so that adding an eleventh flag makes the "every declared flag is shown" assertions
below cover it without anybody editing this file -- and so that a fix which listed nine of the
ten could not pass.
"""


def snapshot(flags: list[str]) -> EvidenceSnapshot:
    """One market-event snapshot whose only interesting field is `payload["quality_flags"]`.

    `schema` and `family` are load-bearing, not decoration: `_family` reads `payload["family"]`
    and `MarketAgent.analyze` keeps only `market_event` items, so a payload without them is
    filtered out *before* `_quality_flags` sees it and the undeclared string is never parsed.
    A fixture missing them would make every assertion below vacuously green.
    """
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(event_time=NOW, available_time=NOW, ingested_time=NOW, revision_time=NOW),
        source_id="synthetic.a-share",
        source_uri="fixture://quality-flags/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Quality-flag fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": flags,
        },
    )


def serialized(flags: list[str]) -> dict[str, Any]:
    return snapshot(flags).model_dump(mode="json", exclude_computed_fields=True)


def run_body(flags: list[str], *, run_id: str = "run-risk-flag-0001") -> dict[str, Any]:
    """The `POST /api/v1/research/run` body, which is also one item of a batch's `requests`."""
    return {
        "run_id": run_id,
        "mode": "live",
        "subject": "000001.SZ",
        "as_of": NOW.isoformat(),
        "evidence": [serialized(flags)],
        "code_commit": COMMIT,
        "config_digest": DIGEST,
        "random_seed": 7,
    }


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A `TestClient` that reports what a deployed server reports.

    `raise_server_exceptions=False` is the whole point of this fixture rather than a detail:
    the default re-raises inside the test, which would show a `ValueError` traceback and hide
    the fact that a *caller* receives `500 text/plain Internal Server Error`. The finding is
    about what crosses the wire, so the client must not be kinder than uvicorn.
    """
    with TestClient(
        create_app(runtime_dir=tmp_path / "api", clock=lambda: NOW),
        raise_server_exceptions=False,
    ) as started:
        yield started


def field_errors(body: object) -> list[dict[str, Any]]:
    """The `detail` list of a FastAPI-shaped `422`, refusing the other `422` body this app has.

    `api/app.py`'s module docstring records that `422` carries two schemas here and that
    `"detail" in body` does not separate them: a panel refusal is a `{"reason", "message"}`
    **object** and a field error is a **list**. Asserting the shape rather than assuming it is
    what makes "the evidence plane answers in the same dialect as pydantic" a real claim.
    """
    assert isinstance(body, dict), body
    detail = body.get("detail")
    assert isinstance(detail, list), f"expected a list of field errors, got {detail!r}"
    return [dict(entry) for entry in detail]


def test_a_declared_flag_still_reaches_the_signal_through_the_rest_route(
    client: TestClient,
) -> None:
    """The control, and it is what makes every refusal below a refusal rather than a breakage.

    Without this, a fix that made `POST /api/v1/research/run` refuse *every* payload carrying
    `quality_flags` would pass every other test in this file. The flag has to survive the round
    trip as a member, and the evidence has to still be attached to the signal.
    """
    response = client.post("/api/v1/research/run", json=run_body([RiskFlag.future_data.value]))

    assert response.status_code == 200, response.text
    signal = response.json()["signal"]
    assert signal["risk_flags"] == [RiskFlag.future_data.value]
    assert len(signal["evidence_ids"]) == 1


@pytest.mark.parametrize("offending", [MISSPELLED, INVENTED])
def test_the_rest_run_route_refuses_an_undeclared_flag_by_name_rather_than_crashing(
    client: TestClient, offending: str
) -> None:
    """`V2-P4-101`. The `500` becomes a `422` that names the string and the whole vocabulary.

    Three separate assertions, because three separate wrong answers were available and the
    acceptance found the suite unable to tell any of them apart:

    1. **Not a `500`.** A `text/plain` `Internal Server Error` tells a producer nothing at all
       and tells an operator that this repository has a defect, which is false -- the request
       was bad.
    2. **The offending string is echoed.** `V2-P4-030`'s whole argument for refusing rather
       than dropping is that "a producer learns which flag it spelled wrong". A `422` reading
       only "invalid risk flag" would satisfy point 1 and lose that entirely.
    3. **All ten declared flags are shown.** Naming the offender without naming the vocabulary
       leaves the producer guessing at the correct spelling, which for `future-data` versus
       `future_data` is precisely the guess they already got wrong.

    Both spellings are driven because they fail differently in principle: one is one character
    from a declared flag and the other resembles nothing, so a repair built on a near-match
    suggestion would answer one and not the other.
    """
    response = client.post("/api/v1/research/run", json=run_body([offending]))

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/json")

    entry = field_errors(response.json())[0]
    assert entry["input"] == offending
    assert entry["loc"] == ["body", "evidence", 0, "payload", "quality_flags", 0]
    for flag in DECLARED:
        assert flag in entry["msg"], f"{flag} is declared and the refusal does not show it"
    assert len(DECLARED) == 10


def test_the_refusal_points_at_the_evidence_item_and_flag_that_carried_the_string(
    client: TestClient,
) -> None:
    """The `loc` is an address, so it has to move when the offending flag moves.

    A refusal hard-coding `["body", "evidence", 0, ...]` passes the test above and is a lie on
    every request whose second evidence item is the bad one. This drives a good item first and
    a good flag before the bad one, so both indices are non-zero and a constant cannot pass.
    """
    body = run_body([RiskFlag.source_uri_missing.value])
    body["evidence"] = [
        serialized([RiskFlag.source_uri_missing.value]),
        serialized([RiskFlag.suspension.value, MISSPELLED]),
    ]
    body["evidence"][1]["source_uri"] = "fixture://quality-flags/000001.SZ#second"

    response = client.post("/api/v1/research/run", json=body)

    assert response.status_code == 422, response.text
    entry = field_errors(response.json())[0]
    assert entry["loc"] == ["body", "evidence", 1, "payload", "quality_flags", 1]
    assert entry["input"] == MISSPELLED


def test_the_two_faces_of_one_vocabulary_refuse_the_same_string_the_same_way(
    client: TestClient,
) -> None:
    """One vocabulary must not grow two dialects of `422`.

    `POST /api/v1/research/deliberate` puts `risk_flags` in the request body, so pydantic
    refuses an undeclared member itself and has always produced a well-formed field error. The
    evidence plane reaches the same vocabulary by a different road and used to produce a `500`.
    Both now answer, and this asserts they answer *alike* -- same keys, same `input`, same
    listed vocabulary -- so the evidence-plane body cannot drift into a thinner second shape
    that satisfies its own test and surprises a client switching routes.
    """
    deliberate = client.post(
        "/api/v1/research/deliberate",
        json={
            "signal": {
                "subject": "000001.SZ",
                "as_of": NOW.isoformat(),
                "direction": "bullish",
                "strength": 0.65,
                "confidence": 0.65,
                "horizon": "5d",
                "evidence_ids": ["ev_" + "0" * 24],
                "confirmation_conditions": ["Event strength persists."],
                "invalidation_conditions": ["Price closes below the event-day low."],
                "risk_flags": [MISSPELLED],
            },
            "agent_results": [],
        },
    )
    assert deliberate.status_code == 422, deliberate.text
    body_side = field_errors(deliberate.json())[0]

    evidence_side = field_errors(
        client.post("/api/v1/research/run", json=run_body([MISSPELLED])).json()
    )[0]

    assert set(body_side) <= set(evidence_side), (
        "the evidence-plane refusal must carry at least the keys pydantic's own does; "
        f"body face {sorted(body_side)}, evidence face {sorted(evidence_side)}"
    )
    assert evidence_side["input"] == body_side["input"] == MISSPELLED
    assert evidence_side["msg"] == body_side["msg"], (
        "both faces refuse the same vocabulary, so they must say so in the same words"
    )


def test_the_cli_names_the_flag_on_stderr_instead_of_rendering_a_traceback(
    tmp_path: Path,
) -> None:
    """`V2-P4-102`, the command-line half. The message was already good; the delivery was not.

    `create_app`'s own docstring states this repository's house rule for an operator-facing
    failure -- "naming the specific variable, never a bare traceback" -- and `research run`
    broke it: the `ValueError` escaped to Typer, which printed a rich, boxed Python stack trace
    of `openalpha_cn` frames and exited 1.

    The exit code stays 1 deliberately. The finding is about presentation, and changing the code
    at the same time would make a CI job that already branches on it fail for a second, unrelated
    reason. What changes is that the flag and the vocabulary reach stderr and the traceback does
    not: `assert "Traceback" not in output` is the assertion the old behaviour failed, and the
    vocabulary assertions are the ones a bare `typer.echo(type(error).__name__)` would fail.
    """
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"items": [serialized([MISSPELLED])]}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "run",
            str(evidence_path),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--run-id",
            "cli-undeclared-flag",
            "--mode",
            "live",
            "--subject",
            "000001.SZ",
            "--as-of",
            NOW.isoformat(),
            "--code-commit",
            COMMIT,
            "--config-digest",
            DIGEST,
        ],
    )

    assert result.exit_code == 1, result.output
    output = result.output
    assert "Traceback" not in output, "an operator refusal must not be a Python stack trace"
    assert "_quality_flags" not in output, "nor a frame of this repository's own source"
    assert MISSPELLED in output
    for flag in DECLARED:
        assert flag in output, f"{flag} is declared and the CLI refusal does not show it"

    # The address, which on this face is the *only* place it can appear: the REST route turns
    # `evidence_id`/`flag_index` into a structured `loc`, and stderr has no `loc` to put them
    # in, so they have to be in the sentence. A mutation sweep survived on this: dropping the
    # coordinates from the message left every assertion above green.
    assert snapshot([MISSPELLED]).evidence_id in output
    assert "quality_flags[0]" in output


def test_the_cli_still_runs_a_declared_flag_to_a_report(tmp_path: Path) -> None:
    """The command-line control, for the reason the REST control exists.

    A `research run` that printed the vocabulary and exited 1 on *every* payload would pass the
    test above. This is the same command with the flag spelled correctly, and it has to reach a
    parseable report on stdout with the flag carried onto the signal.
    """
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps({"items": [serialized([RiskFlag.source_uri_missing.value])]}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "research",
            "run",
            str(evidence_path),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--run-id",
            "cli-declared-flag",
            "--mode",
            "live",
            "--subject",
            "000001.SZ",
            "--as-of",
            NOW.isoformat(),
            "--code-commit",
            COMMIT,
            "--config-digest",
            DIGEST,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["signal"]["risk_flags"] == [RiskFlag.source_uri_missing.value]


def test_the_batch_record_says_which_flag_rather_than_only_that_a_ValueError_happened(
    client: TestClient,
) -> None:
    """`V2-P4-102`, the batch half. `{"error_type": "ValueError"}` discards the whole diagnostic.

    A batch item that failed used to record the bare class name of whatever escaped, and
    `ValueError` is the least informative name in Python -- it separates this from nothing. The
    producer of a 5,000-item batch learns that one item failed and has no way at all to find out
    which flag it spelled wrong, which is precisely the diagnostic `parse_risk_flag`'s docstring
    promises ("a producer learns which flag it spelled wrong at the point it wrote it").

    Two places carry it, and both are asserted because either alone is escapable:

    - `BatchTaskItem.error_type` names the **specific** exception rather than `ValueError`, so
      an operator scanning statuses can tell a bad flag from a bad price without opening
      anything.
    - the `item_failed` progress event's `detail` carries the **whole** reason -- the offending
      string and the vocabulary. `detail` is the field already published for this
      (`GET /api/v1/research/batches/{batch_id}/events`) and it is a free string, so nothing
      about the stored contract has to change to make the reason durable.
    """
    submitted = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "batch-undeclared-flag",
            "requests": [run_body([MISSPELLED])],
            "max_concurrency": 1,
        },
    )
    assert submitted.status_code == 202, submitted.text

    task = client.get("/api/v1/research/batches/batch-undeclared-flag").json()
    assert task["status"] == "failed"
    item = task["items"][0]
    assert item["status"] == "failed"
    assert item["error_type"] != "ValueError", (
        "the bare base-class name is the finding, not the fix"
    )
    assert "RiskFlag" in item["error_type"], item["error_type"]

    events = client.get("/api/v1/research/batches/batch-undeclared-flag/events").json()
    failures = [event for event in events if event["kind"] == "item_failed"]
    assert len(failures) == 1
    detail = failures[0]["detail"]
    assert detail is not None
    assert MISSPELLED in detail
    for flag in DECLARED:
        assert flag in detail, f"{flag} is declared and the batch record does not show it"
    assert "quality_flags[0]" in detail, (
        "the durable record has no structured `loc`, so the position has to be in the sentence"
    )


def test_a_batch_of_a_declared_flag_still_succeeds(client: TestClient) -> None:
    """The batch control. A batch that failed every item would pass the test above."""
    client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "batch-declared-flag",
            "requests": [run_body([RiskFlag.source_uri_missing.value])],
            "max_concurrency": 1,
        },
    )

    task = client.get("/api/v1/research/batches/batch-declared-flag").json()
    assert task["status"] == "succeeded", task
    assert task["items"][0]["error_type"] is None


def test_the_replay_route_already_carried_the_whole_reason_and_still_does(
    client: TestClient,
) -> None:
    """The surface the finding named as broken and measurement found correct.

    `_quality_flags`' docstring lists `POST /api/v1/backtests/replay` beside the two routes that
    really did crash, and the acceptance report inherited that list. It is wrong about this one:
    `ReplayRunner.run()` catches `(RuntimeError, ValueError)` per case and appends
    `f"{case.run_id}: {type(error).__name__}: {error}"`, so the offending string and the whole
    vocabulary have always reached the caller inside a `200` report -- one bad case does not
    fail the corpus, which is what a replay report is for.

    Kept as a regression guard rather than deleted, because the *reason* it works is that
    `UndeclaredRiskFlagError` subclasses `ValueError`. Narrowing that base class later would
    silently turn this `200`-with-a-failure back into an uncaught crash, and this is the only
    test that would notice.
    """
    corpus = {
        "schema_version": "replay-corpus/v1",
        "trading_days": [NOW.date().isoformat()],
        "cases": [
            {
                "run_id": "replay-undeclared-flag",
                "trading_day": NOW.date().isoformat(),
                "subject": "000001.SZ",
                "as_of": NOW.isoformat(),
                "evidence": [serialized([MISSPELLED])],
                "outcome": {
                    "observation_start": NOW.isoformat(),
                    "observation_end": (NOW + timedelta(days=5)).isoformat(),
                    "start_price": 10.0,
                    "end_price": 11.0,
                    "benchmark_return": 0.02,
                    "transaction_cost": 0.005,
                },
            }
        ],
    }

    response = client.post(
        "/api/v1/backtests/replay",
        json={
            "corpus": corpus,
            "code_commit": COMMIT,
            "config_digest": DIGEST,
            "random_seed": 7,
        },
    )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["succeeded"] == 0
    assert report["look_ahead_violations"] == 0, "a bad flag is not a point-in-time failure"
    assert len(report["failures"]) == 1
    failure = report["failures"][0]
    assert MISSPELLED in failure
    for flag in DECLARED:
        assert flag in failure, f"{flag} is declared and the replay failure does not show it"
