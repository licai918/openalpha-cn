"""`V2-P4-043`'s own fix reproduced `V2-P4-040`'s defect shape on the route it changed.

`043` added `max_length=MAX_BATCH_ITEMS` to `ScreeningApiRequest.research` so that a caller one
name too far met a `422` naming the number rather than a `413` talking only about bytes. The
number arrived; so did a body the size of the request. Measured on `daaabf5` with the realistic
record `test_request_body_ceiling._screen_result` builds:

    POST /api/v1/screen           10,001 records, 14,771,528 bytes in
      -> 422, response body 13,821,594 bytes (13.18 MiB), 0.7 s
    POST /api/v1/research/batches 10,001 requests, 9,841,040 bytes in
      -> 422, response body  9,261,138 bytes (8.83 MiB), 0.6 s

`040`'s definition is a service emitting a body it would itself refuse to accept, and 13.18 MiB
to say "you sent one too many" is that. The cause is pydantic's `too_long` error carrying `input`
-- the entire rejected collection -- and FastAPI serialising it verbatim.

**It is a class of two routes and not one, and the sharpest instance is neither of them.** A
*misspelled* top-level key is two errors (`missing` for the real field, `extra_forbidden` for the
typo) and each one echoes the whole body, so the amplification is greater than one:

    POST /api/v1/screen  200 records under a misspelled key, 295,450 bytes in
      -> 422, response body 553,037 bytes -- 1.87x the request

That is the row's own defect at 2% of the record count, and nothing in the row's fix touches it,
because the fault is not the ceiling. It is that a validation refusal echoes what it refused.

## What is fixed and what is deliberately left alone

`docs/api/http.md` already documents three body shapes and `V2-P4-051` pinned the rule a client
switches on -- the detail's *shape*, not the presence of the `detail` key. Turning every `422`
into `{"reason", "message"}` would break that documented contract for the ordinary
"you sent a string where a float goes" case, where FastAPI's `loc` is the useful answer and the
echo is small. So the split is by *what the refusal is about*:

- a **ceiling this service declares** (`too_long`/`too_short`) is one of this service's own
  semantic refusals and takes the `{"reason", "message", ...}` object shape `_research_refusal`
  built for the same route in the same commit, naming the limit and the count received;
- everything else keeps FastAPI's list, with each entry's `input` elided when it does not fit
  `MAX_ECHOED_INPUT_BYTES` and the list itself truncated past `MAX_VALIDATION_ERRORS`, so the
  documented discriminator and every `loc` survive and the body cannot scale with the request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from test_request_body_ceiling import NOW, _batch_request, _screen_result

from openalpha_cn.api.app import (
    MAX_ECHOED_INPUT_BYTES,
    MAX_VALIDATION_ERRORS,
    create_app,
)
from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS

REFUSAL_CEILING_BYTES: Final[int] = 8 * 1024
"""How large a validation refusal is allowed to be, as an executable bound rather than a hope.

Eight kilobytes is roomy for `MAX_VALIDATION_ERRORS` entries with `MAX_ECHOED_INPUT_BYTES` of
echo each, and it is four orders of magnitude below the 13,821,594 bytes measured on `daaabf5`.
The number that matters is not its exact value: it is that the bound is a **constant** rather
than a function of the request, which is the whole difference between the two behaviours.
"""

OVER_COUNT_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("/api/v1/screen", "research"),
    ("/api/v1/research/batches", "requests"),
)
"""Both routes that declare `MAX_BATCH_ITEMS` on a collection, and the field each declares it on.

Two rows because the defect is a class. `V2-P4-043` changed `/api/v1/screen` and measured
`/api/v1/screen`; `BatchSubmitRequest.requests` has carried the same `max_length` since
`V2-P4-019` and refuses the same way, at 8.83 MiB. A fix verified on one route and closed as if
it covered the class is the shape this repository keeps finding -- most recently in
`V2-P4-067(b)`, one wave earlier.
"""


def _client(tmp_path: Path) -> TestClient:
    """An app whose byte ceiling cannot be what refuses these bodies.

    `max_request_bytes` is raised past every body below on purpose: the question is what the
    *route* does with a body it accepted, and a `413` from `SecurityHeadersMiddleware` would
    answer a different one. `V2-P4-043`'s whole point was that the byte ceiling is the wrong
    instrument for a count.
    """
    return TestClient(
        create_app(runtime_dir=tmp_path, clock=lambda: NOW, max_request_bytes=64 * 1024 * 1024)
    )


def _over_count_body(path: str) -> bytes:
    if path == "/api/v1/screen":
        return json.dumps(
            {
                "research": [
                    _screen_result(f"{index % 1_000_000:06d}.SZ")
                    for index in range(MAX_BATCH_ITEMS + 1)
                ],
                "criteria": {"min_confidence": 0.1},
            }
        ).encode()
    return json.dumps(
        {
            "batch_id": "over-count",
            "requests": [_batch_request(index) for index in range(MAX_BATCH_ITEMS + 1)],
            "max_concurrency": 1,
        }
    ).encode()


def _post(client: TestClient, path: str, body: bytes) -> Any:
    return client.post(path, content=body, headers={"content-type": "application/json"})


@pytest.mark.parametrize(("path", "field"), OVER_COUNT_CASES, ids=[p for p, _ in OVER_COUNT_CASES])
def test_a_body_one_item_over_the_ceiling_is_refused_in_a_body_this_service_would_accept(
    tmp_path: Path, path: str, field: str
) -> None:
    """`V2-P4-040`'s definition, applied to the refusal rather than to the request.

    The assertion is on the *response* size and the request size is asserted beside it, because
    a response bound that happened to hold on a small fixture would say nothing: the two numbers
    together are what show the refusal stopped scaling with what it refused.
    """
    body = _over_count_body(path)

    response = _post(_client(tmp_path), path, body)

    assert response.status_code == 422
    assert len(body) > 8 * 1024 * 1024, (
        f"the fixture stopped reproducing the row: {len(body)} bytes is not a whole-market body"
    )
    assert len(response.content) < REFUSAL_CEILING_BYTES, response.text[:400]


@pytest.mark.parametrize(("path", "field"), OVER_COUNT_CASES, ids=[p for p, _ in OVER_COUNT_CASES])
def test_the_over_count_refusal_names_the_ceiling_the_field_and_the_count_received(
    tmp_path: Path, path: str, field: str
) -> None:
    """`V2-P4-043`'s actual objective, kept: a number to aim at rather than a fact about bytes.

    Shrinking the body is only half a fix if the number goes with it, so each of the three is
    asserted separately -- the field the ceiling is declared on, the ceiling, and what arrived --
    rather than through one substring that any of the three could satisfy.
    """
    response = _post(_client(tmp_path), path, _over_count_body(path))
    detail = response.json()["detail"]

    assert isinstance(detail, dict)
    assert detail["reason"] == "declared_ceiling_exceeded"
    assert detail["field"] == field
    assert detail["limit"] == MAX_BATCH_ITEMS
    assert detail["received"] == MAX_BATCH_ITEMS + 1
    assert str(MAX_BATCH_ITEMS) in detail["message"]


def test_the_over_count_refusal_is_the_shape_the_route_already_uses_for_its_own_refusals(
    tmp_path: Path,
) -> None:
    """One route, one status code, one discriminator for both of this service's own refusals.

    `V2-P4-041` built `/api/v1/screen`'s malformed-record `422` as `{"reason", "message", ...}`
    in the same commit that added the ceiling, and `docs/api/http.md` makes `detail.reason` the
    key a client switches on for a refusal this service decided. The two are compared to each
    other rather than each to a literal, because two independent shape assertions can both pass
    on two shapes that disagree.
    """
    client = _client(tmp_path)

    ceiling = _post(client, "/api/v1/screen", _over_count_body("/api/v1/screen")).json()["detail"]
    malformed = client.post(
        "/api/v1/screen", json={"research": [{"not": "a result"}], "criteria": {}}
    ).json()["detail"]

    assert isinstance(malformed, dict)
    assert set(ceiling) >= {"reason", "message"}
    assert set(malformed) >= {"reason", "message"}
    assert ceiling["reason"] != malformed["reason"]


def test_a_misspelled_key_on_a_large_body_no_longer_answers_with_a_copy_of_it(
    tmp_path: Path,
) -> None:
    """The sharper instance the row never named, at 2% of the record count.

    Two errors -- `missing` for `research` and `extra_forbidden` for the typo -- each echoing the
    whole body, so the refusal was **1.87x the request** at 200 records: 295,450 bytes in,
    553,037 bytes out. This is not a ceiling and the row's fix could not have touched it; what
    fixes it is that a refusal stops echoing what it refused.
    """
    body = json.dumps(
        {
            "reserch": [_screen_result(f"{index % 1_000_000:06d}.SZ") for index in range(200)],
            "criteria": {"min_confidence": 0.1},
        }
    ).encode()

    response = _post(_client(tmp_path), "/api/v1/screen", body)

    assert response.status_code == 422
    assert len(body) > 100_000
    assert len(response.content) < REFUSAL_CEILING_BYTES, response.text[:400]


def test_an_ordinary_validation_fault_keeps_the_list_shape_the_reference_documents(
    tmp_path: Path,
) -> None:
    """The direction that keeps the fix from being a rewrite of every `422` this service emits.

    `V2-P4-051` measured what happens when a client cannot tell two `422` bodies apart and
    `docs/api/http.md` now names the detail's *shape* as the discriminator. An ordinary parameter
    fault must therefore still be FastAPI's list with a `loc` on every entry -- and the small
    echo that makes `loc` useful must survive, which is the second assertion here.
    """
    response = _client(tmp_path).post(
        "/api/v1/screen", json={"research": [], "criteria": {"min_confidence": "not-a-number"}}
    )
    detail = response.json()["detail"]

    assert response.status_code == 422
    assert isinstance(detail, list)
    assert all("loc" in entry for entry in detail)
    assert any(entry.get("input") == "not-a-number" for entry in detail)


def test_a_body_with_more_faults_than_the_cap_says_how_many_it_did_not_list(
    tmp_path: Path,
) -> None:
    """The other half of the bound: the list itself cannot scale with the request either.

    `BatchSubmitRequest.requests` validates each item, so a body of a thousand malformed requests
    is a thousand errors. Eliding each entry's `input` bounds the entry; only truncating the list
    bounds the body. The count of what was dropped is carried rather than silently discarded --
    a truncated list that did not say it was truncated would tell a caller they had fixed
    everything when they had fixed twenty things.
    """
    body = {
        "batch_id": "many-faults",
        "requests": [{"run_id": f"r-{index}"} for index in range(1_000)],
        "max_concurrency": 1,
    }

    response = _client(tmp_path).post("/api/v1/research/batches", json=body)
    detail = response.json()["detail"]

    assert response.status_code == 422
    assert isinstance(detail, list)
    assert len(detail) == MAX_VALIDATION_ERRORS + 1
    assert all("loc" in entry for entry in detail)
    assert detail[-1]["type"] == "errors_elided"
    assert "further validation error" in detail[-1]["msg"]
    assert len(response.content) < REFUSAL_CEILING_BYTES


def test_a_small_echo_is_kept_and_a_large_one_is_replaced_by_its_own_measurement(
    tmp_path: Path,
) -> None:
    """`MAX_ECHOED_INPUT_BYTES` is a boundary, so both sides of it are driven.

    A test that only asserted large echoes are gone would pass on a handler that deleted every
    `input`, which would take the useful half of FastAPI's message with it. The elision names
    what it replaced -- the kind and the size -- so a caller can still tell "you sent a list of
    ten thousand" from "you sent a string".
    """
    client = _client(tmp_path)

    small = client.post(
        "/api/v1/screen", json={"research": [], "criteria": {"min_confidence": "abc"}}
    ).json()["detail"]
    large = client.post(
        "/api/v1/screen",
        json={"research": [], "criteria": {"min_confidence": "x" * (MAX_ECHOED_INPUT_BYTES * 2)}},
    ).json()["detail"]

    assert small[0]["input"] == "abc"
    assert "elided" in str(large[0]["input"])
    assert str(MAX_ECHOED_INPUT_BYTES * 2) in str(large[0]["input"])


def test_the_reference_documents_the_shape_a_declared_ceiling_answers_with() -> None:
    """The prose is half of this fix, because a body shape nobody documented is one nobody uses.

    `docs/api/http.md` already carries the three-shape table `V2-P4-051` wrote. A fourth
    behaviour that is not in it is a behaviour a client discovers in production.
    """
    root = Path(__file__).resolve().parents[2]
    reference = (root / "docs" / "api" / "http.md").read_text(encoding="utf-8")

    assert "declared_ceiling_exceeded" in reference
    assert "MAX_ECHOED_INPUT_BYTES" in reference
