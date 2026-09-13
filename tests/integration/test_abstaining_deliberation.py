"""`V2-P4-029`: the committee is asked to deliberate on an abstention, on both shipped faces.

`SignalFrame`'s own first line calls it "an immutable, evidence-linked research conclusion **or
abstention**", and `validate_conclusion` makes the second half concrete: `direction="abstain"`
requires an `abstention_reason`, requires `strength == 0`, and -- because the `else` branch is
what demands `evidence_ids` -- permits no evidence at all. An abstention with no evidence is not
a degenerate signal, it is what abstention *means* in this contract, and PRD S42 makes producing
one a guarantee rather than an accident.

`DeliberationCommittee.review` could not accept one. It recomputed `direction` from
`adjusted_strength` into `Literal["bullish", "bearish", "neutral"]` -- `abstain` was not in the
annotation and no branch could return it -- so an abstaining signal came back out directional,
carrying the empty `evidence_ids` it was built with, and `SignalFrame.validate_conclusion` killed
it while `DeliberationOutcome` was being constructed:

    ValidationError: 1 validation error for DeliberationOutcome
    adjusted_signal  Value error, directional signal requires evidence

Both shipped faces hand a caller-supplied signal straight in -- `api/app.py`'s
`POST /api/v1/research/deliberate` and `sdk.py`'s `OpenAlphaSDK.deliberate` -- so the failure was
reachable from outside the process, and on the REST face it was a **500** with a `text/plain`
body reading `Internal Server Error`: no `reason`, no field, nothing a client could branch on.

This module is driven through those two faces rather than through `DeliberationCommittee`
directly, because "the committee raises" and "the product answers 500" are different findings and
only the second is the defect a user met. The unit-level statement lives in
`tests/unit/agents/test_deliberation_committee.py`.

Written and confirmed red before the fix, on both faces: the REST assertion failed with
`assert 500 == 200` and the SDK assertion with the `ValidationError` quoted above.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.sdk import OpenAlphaSDK

ABSTENTION_REASON: Final[str] = "evidence is contradictory and neither side clears the bar"


def abstaining(frozen_now: datetime) -> SignalFrame:
    """The abstention `SignalFrame` declares first-class: no evidence, zero strength, a reason."""
    return SignalFrame(
        subject="000001.SZ",
        as_of=frozen_now,
        direction="abstain",
        strength=0.0,
        confidence=0.0,
        horizon="5d",
        abstention_reason=ABSTENTION_REASON,
    )


def request_body(signal: SignalFrame) -> dict[str, Any]:
    """`signal` as `POST /api/v1/research/deliberate` accepts it.

    `signal_id` is a `computed_field`, so it appears in `model_dump` and is refused on the way
    back in by `SignalFrame`'s `extra="forbid"`. Dropping it here keeps this test measuring the
    committee rather than re-measuring that.
    """
    payload = signal.model_dump(mode="json")
    payload.pop("signal_id", None)
    return {"signal": payload, "agent_results": []}


@pytest.fixture
def rest(tmp_path: Path, frozen_now: datetime) -> Iterator[TestClient]:
    """A REST face that surfaces the server's own status code rather than re-raising."""
    application = create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


def test_the_rest_face_deliberates_on_an_abstention_instead_of_answering_500(
    rest: TestClient, frozen_now: datetime
) -> None:
    """The acceptance measurement, and the one a caller actually met.

    `raise_server_exceptions=False` is what makes this the *product's* answer: with the default
    the client re-raises the committee's `ValidationError` and the test would report a Python
    exception, which is the library's finding and not this one. Before the fix this was
    `assert 500 == 200`, and the body was `text/plain` reading `Internal Server Error`.

    The abstention has to survive the round trip intact, not merely avoid raising: a committee
    that answered `200` by turning the abstention into a `neutral` signal would have converted a
    crash into a silently wrong conclusion, which is worse. `direction`, `strength`,
    `abstention_reason` and `evidence_ids` are therefore all asserted on the way out.
    """
    signal = abstaining(frozen_now)

    response = rest.post("/api/v1/research/deliberate", json=request_body(signal))

    assert response.status_code == 200, response.text
    adjusted = response.json()["adjusted_signal"]
    assert adjusted["direction"] == "abstain"
    assert adjusted["strength"] == 0.0
    assert adjusted["abstention_reason"] == ABSTENTION_REASON
    assert adjusted["evidence_ids"] == []


def test_the_sdk_face_deliberates_on_an_abstention_instead_of_raising(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The same signal through `OpenAlphaSDK.deliberate`, the other face passing one straight in.

    Both faces are driven because they fail for the same reason and *present* differently -- REST
    turns the exception into a status code and the SDK hands the caller the `ValidationError` --
    so a fix that only repaired the endpoint's envelope would leave this one red.

    `ablation` is asserted here rather than on the REST face because it is the committee's own
    claim about what it changed: an abstention it did not move must report that it did not move,
    and a `baseline_direction` of `abstain` beside an `adjusted_direction` of `neutral` is exactly
    the silent conversion the previous implementation would have produced had the validator not
    caught it first.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    signal = abstaining(frozen_now)

    outcome = sdk.deliberate(signal=signal, agent_results=())

    assert outcome.adjusted_signal.direction == "abstain"
    assert outcome.adjusted_signal.abstention_reason == ABSTENTION_REASON
    assert outcome.ablation.baseline_direction == "abstain"
    assert outcome.ablation.adjusted_direction == "abstain"
    assert outcome.ablation.strength_delta == 0.0
    assert outcome.risk_decision == "pass"
