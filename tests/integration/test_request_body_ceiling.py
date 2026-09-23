"""The declared ceilings must survive the transport that carries them (V2-P4-043).

The row measured two caps contradicting each other on `POST /api/v1/screen`: 5,545 whole-market
names is 7.81 MB and answers `200`, 6,000 names is 8.43 MB and answers `413 Request body exceeds
configured limit.`, against an 8 MiB `OPENALPHA_MAX_REQUEST_BYTES` default. `V2-P4-019` raised
batches to 10,000 *explicitly* because "the market is a moving number", and the only
market-to-shortlist route hit the ceiling at roughly 5,700 names -- a few hundred more A-share
listings away.

**A strictly sharper form of the same defect, measured here and not stated in the row.** At
`be262ea` a batch of exactly `MAX_BATCH_ITEMS` -- the ceiling this service declares on
`BatchSubmitRequest.requests`, states once in `batch_contracts.py`, and proves the durable
contract holds in `tests/integration/test_batch_whole_market_scale.py` -- is **9,840,054 bytes**
carrying one evidence snapshot per request, and was answered `413`. The declared ceiling was not
merely tight, it was **unreachable through the only surface that can express it**, and no test
caught it because every existing test at that scale builds the task in process rather than
posting it. A `MAX_BATCH_ITEMS` screen measured **14,770,051 bytes**, so 14.77 MB is the real
floor under any ceiling that lets this service's own two limits both be reached.

**Costs, because they decided the shape of these tests.** Posting 10,000 items and letting them
persist takes **113.8s** -- that path is already covered by `test_batch_whole_market_scale.py`
in process, so the batch test here claims a batch id first and asserts the second post is
refused by the *route* (`409`) rather than by the transport (`413`), which is exactly the
distinction under test and costs only the validation. The screen at 10,000 runs end to end in
**0.9s**, so that one is driven to a real `200`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS
from openalpha_cn.config import load_config
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunResult

NOW: Final[datetime] = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
COMMIT: Final[str] = "0123456789abcdef"
EVIDENCE: Final[tuple[str, ...]] = ("evd_000000000000000000000001",)


def _batch_request(index: int) -> dict[str, Any]:
    """One whole-market batch request, carrying the evidence a real one carries.

    The evidence snapshot is **not** padding and removing it would not make this test stricter --
    it would make it vacuous. With `evidence: []` a `MAX_BATCH_ITEMS` body is 6.9 MB and already
    fitted inside the 8 MiB default at `be262ea`; the defect only appears once the request
    carries the thing a research request exists to carry. This fixture sends exactly one
    snapshot, which is the *smallest* non-empty case, so a real caller's body is larger still.
    """
    subject = f"{index % 1_000_000:06d}.SZ"
    evidence = EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(event_time=NOW, available_time=NOW, ingested_time=NOW, revision_time=NOW),
        source_id="ceiling.fixture",
        source_uri=f"fixture://{subject}",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Whole-market ceiling fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )
    return {
        "run_id": f"ceiling-{index:06d}",
        "mode": "replay",
        "subject": subject,
        "as_of": NOW.isoformat(),
        "evidence": [evidence.model_dump(mode="json")],
        "code_commit": COMMIT,
        "config_digest": "b" * 64,
        "random_seed": 7,
    }


def _screen_result(subject: str) -> dict[str, Any]:
    """One verified `ResearchRunResult`, serialized exactly as this service hands it out.

    Built through the real contracts rather than hand-assembled, because
    `_parse_research_result` re-derives and verifies all three content addresses -- a
    hand-written payload would be refused for its identifiers and never reach the size question.
    """
    signal = SignalFrame(
        subject=subject,
        as_of=NOW,
        direction="bullish",
        strength=0.4,
        confidence=0.6,
        horizon="5d",
        evidence_ids=EVIDENCE,
        risk_flags=(),
    )
    manifest = RunManifest(
        run_id=f"run-{subject}",
        mode="replay",
        as_of=NOW,
        code_commit=COMMIT,
        config_digest="b" * 64,
        random_seed=7,
        started_at=NOW,
        finished_at=NOW,
        status="succeeded",
    )
    decision = DecisionLedger(
        run_id=manifest.run_id,
        run_manifest_id=manifest.run_manifest_id,
        created_at=NOW,
        risk_decision="pass",
        final_action="watch",
        evidence_ids=EVIDENCE,
        signal_ids=(signal.signal_id,),
        code_commit=COMMIT,
    )
    return ResearchRunResult(
        signal=signal, decision=decision, manifest=manifest, agent_results=()
    ).model_dump(mode="json")


def test_a_batch_at_the_declared_ceiling_reaches_the_route_that_declares_it(
    tmp_path: Path,
) -> None:
    """`MAX_BATCH_ITEMS` items must get past the transport to the route's own `max_length`.

    `409`-versus-`413` is the entire assertion, and it is the sibling of
    `test_batch_whole_market_scale.py`'s `422`-versus-`409`: the batch id is claimed first, so a
    body that arrives is refused by the route for a reason about *batches*, while a body that
    does not arrive is refused by the middleware for a reason about *bytes*. The app is built
    with the **default** ceiling -- no `max_request_bytes` override -- because the defect is in
    what a deployment gets without configuring anything.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))
    claimed = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "whole-market-ceiling",
            "requests": [_batch_request(0)],
            "max_concurrency": 1,
        },
    )
    assert claimed.status_code == 202, claimed.text

    body = json.dumps(
        {
            "batch_id": "whole-market-ceiling",
            "requests": [_batch_request(index) for index in range(MAX_BATCH_ITEMS)],
            "max_concurrency": 1,
        }
    ).encode()
    response = client.post(
        "/api/v1/research/batches",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code != 413, (
        f"a batch at the declared ceiling of {MAX_BATCH_ITEMS} items is {len(body)} bytes "
        f"and this service refuses to accept it: {response.text[:200]}"
    )
    assert response.status_code == 409, response.text[:400]
    assert len(body) > 8 * 1024 * 1024, (
        f"the fixture stopped reproducing the row: {len(body)} bytes is inside the old 8 MiB "
        "default, so this test would pass without the ceiling having moved"
    )
    assert len(body) < load_config().max_request_bytes


def test_a_whole_market_screen_is_expressible_with_room_for_the_market_to_move(
    tmp_path: Path,
) -> None:
    """The row's own measurement, driven to a real `200` at the ceiling this service declares.

    5,545 already fitted at `be262ea` -- by 198,592 bytes, about 134 names. What did not fit is a
    market that grew, so the assertion is made at `MAX_BATCH_ITEMS` names rather than at today's
    count: a ceiling that clears exactly today's market is the same defect on a delay.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))
    body = json.dumps(
        {
            "research": [
                _screen_result(f"{index % 1_000_000:06d}.SZ") for index in range(MAX_BATCH_ITEMS)
            ],
            "criteria": {"min_confidence": 0.1},
        }
    ).encode()

    response = client.post(
        "/api/v1/screen", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code != 413, (
        f"a whole-market screen of {MAX_BATCH_ITEMS} names is {len(body)} bytes "
        "and this service refuses to accept it"
    )
    assert response.status_code == 200, response.text[:400]
    assert response.json()["reviewed"] == MAX_BATCH_ITEMS
    assert len(body) > 8 * 1024 * 1024, (
        f"the fixture stopped reproducing the row: {len(body)} bytes is inside the old 8 MiB "
        "default"
    )


def test_the_screen_states_its_own_item_ceiling_rather_than_leaving_it_to_the_byte_cap(
    tmp_path: Path,
) -> None:
    """One name past the ceiling is a `422` that names the ceiling, not an opaque `413`.

    At `be262ea` `ScreeningApiRequest.research` declared no `max_length` at all, so the only
    thing bounding a screen was a byte count -- which is why a caller met `413` with no idea what
    number they had exceeded. The route now states the same ceiling the batch route states.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    response = client.post(
        "/api/v1/screen",
        json={
            "research": [{"unused": index} for index in range(MAX_BATCH_ITEMS + 1)],
            "criteria": {},
        },
    )

    assert response.status_code == 422, response.text[:200]
    assert f"at most {MAX_BATCH_ITEMS} items" in response.text


def test_the_413_names_the_variable_that_raises_the_ceiling(tmp_path: Path) -> None:
    """A refusal that does not name its own knob leaves the caller nothing to turn.

    Driven with an explicit tiny ceiling rather than by building a body past the default, so the
    message is under test and not the default's size. The shape is `_panel_detail`'s
    `{"reason", "message"}`, which is what the rest of this service's refusals already wear.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=512, clock=lambda: NOW))
    body = json.dumps(
        {
            "batch_id": "too-big",
            "requests": [_batch_request(index) for index in range(2)],
            "max_concurrency": 1,
        }
    ).encode()
    assert len(body) > 512

    response = client.post(
        "/api/v1/research/batches",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), "the refusal must carry this service's `reason`/`message`"
    assert detail["reason"] == "request_too_large"
    assert "OPENALPHA_MAX_REQUEST_BYTES" in detail["message"]
    assert detail["limit_bytes"] == 512
    assert detail["declared_bytes"] == len(body)
    assert str(len(body)) in detail["message"]
    assert "512" in detail["message"]


def test_the_ceiling_still_refuses_what_is_genuinely_too_large(tmp_path: Path) -> None:
    """The guard against fixing `413` by removing it.

    A body one byte over a configured ceiling is still refused, and refused off `Content-Length`
    -- which is what keeps an oversized body from being read into memory at all. Raising a
    default is not the same as having no default, and this is the assertion that keeps them
    apart.
    """
    client = TestClient(
        create_app(runtime_dir=tmp_path, max_request_bytes=1_024, clock=lambda: NOW)
    )

    under = client.post(
        "/api/v1/research/batches",
        content=b"x" * 1_024,
        headers={"content-type": "application/json"},
    )
    over = client.post(
        "/api/v1/research/batches",
        content=b"x" * 1_025,
        headers={"content-type": "application/json"},
    )

    assert over.status_code == 413, over.text
    assert over.json()["detail"]["declared_bytes"] == 1_025
    assert under.status_code != 413, "a body at exactly the ceiling must be admitted"


def test_the_default_ceiling_clears_both_ceilings_this_service_declares(tmp_path: Path) -> None:
    """The arithmetic behind the default, asserted rather than left in prose.

    Both numbers are built here rather than written down, so a fixture that drifted -- or a
    contract that grew a field -- moves this assertion with it instead of leaving a stale
    comment behind.
    """
    batch_bytes = len(
        json.dumps(
            {
                "batch_id": "measure",
                "requests": [_batch_request(index) for index in range(MAX_BATCH_ITEMS)],
                "max_concurrency": 1,
            }
        ).encode()
    )
    screen_bytes = len(
        json.dumps(
            {
                "research": [
                    _screen_result(f"{index % 1_000_000:06d}.SZ")
                    for index in range(MAX_BATCH_ITEMS)
                ],
                "criteria": {},
            }
        ).encode()
    )

    ceiling = load_config().max_request_bytes
    assert ceiling > batch_bytes, f"{ceiling} does not clear a {MAX_BATCH_ITEMS}-item batch"
    assert ceiling > screen_bytes, f"{ceiling} does not clear a {MAX_BATCH_ITEMS}-name screen"
    assert ceiling >= 2 * max(batch_bytes, screen_bytes), (
        "the default leaves no headroom for the richer evidence a real caller sends: "
        f"{ceiling} against a measured {max(batch_bytes, screen_bytes)}"
    )
