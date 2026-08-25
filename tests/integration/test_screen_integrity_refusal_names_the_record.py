"""`POST /api/v1/screen`'s 422 must name the record and the identifier (V2-P4-041).

`_parse_research_result` raises three *different* sentences -- `signal_id`, `decision_id` and
`run_manifest_id` each failing to match its own content -- and at `be262ea` `api/app.py` caught
all three into one flat string, `"Research result failed integrity validation."`. A caller
holding 5,545 results learned neither which record nor which of the three addresses moved.

**There is a model inside this same service.** The panel gate's refusal is
`{"detail": {"reason": ..., "message": ...}}` (`_panel_detail`, and `docs/api/http.md`'s "409
carries two body schemas; switch on `detail.reason`"), where `reason` is machine-readable and
`message` is a disclosable sentence that names the specific item and what to do about it. This
row is that shape arriving one route over.

**Why this fixture can tell the three answers apart, which is the thing that is easy to get
wrong.** Each tamper is chosen to move exactly one of the three addresses and leave the other
two intact:

- `signal.confidence` is a `SignalFrame` contract field, so it moves `signal_id` alone; the
  decision's payload still carries the *old* `signal_ids` tuple, so `decision_id` still matches
  its own content.
- `decision.risk_decision` is a `DecisionLedger` contract field that appears nowhere in the
  signal or the manifest, so it moves `decision_id` alone. It has to be moved to another
  *admissible* rung (`pass` -> `reduce`); an inadmissible one such as `"warn"` is rejected by
  `Literal["pass", "reduce", "block"]` before `_parse_research_result` ever compares an address,
  and this fixture originally used exactly that and would have reported a validation fault while
  claiming to prove `decision_id`.
- `manifest.random_seed` moves `run_manifest_id` alone -- and it has to be `random_seed` rather
  than a wall clock, because `RUN_MANIFEST_UNADDRESSED_FIELDS` keeps `started_at`/`finished_at`/
  `status`/`checkpoints`/`environment` out of the address entirely, so tampering one of those
  would move nothing and the test would pass against a route that always said `signal_id`.

`test_the_three_identifier_faults_are_three_different_reasons` asserts the three `reason` values
are *distinct*, which is the assertion a collapsed 422 cannot satisfy however good its wording.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.contracts import ResearchRunResult

AS_OF: Final[datetime] = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)
COMMIT: Final[str] = "0123456789abcdef"
EVIDENCE: Final[tuple[str, ...]] = ("evd_000000000000000000000001",)


def _serialized_result(*, subject: str) -> dict[str, Any]:
    signal = SignalFrame(
        subject=subject,
        as_of=AS_OF,
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
        as_of=AS_OF,
        code_commit=COMMIT,
        config_digest="b" * 64,
        random_seed=7,
        started_at=AS_OF,
        finished_at=AS_OF,
        status="succeeded",
    )
    decision = DecisionLedger(
        run_id=manifest.run_id,
        run_manifest_id=manifest.run_manifest_id,
        created_at=AS_OF,
        risk_decision="pass",
        final_action="watch",
        evidence_ids=EVIDENCE,
        signal_ids=(signal.signal_id,),
        code_commit=COMMIT,
    )
    return ResearchRunResult(
        signal=signal, decision=decision, manifest=manifest, agent_results=()
    ).model_dump(mode="json")


def _tampered(subject: str, *, fault: str) -> dict[str, Any]:
    """One serialized result with exactly one of the three addresses moved off its content."""
    payload = _serialized_result(subject=subject)
    if fault == "signal_id":
        payload["signal"]["confidence"] = 0.95
    elif fault == "decision_id":
        payload["decision"]["risk_decision"] = "reduce"
    elif fault == "run_manifest_id":
        payload["manifest"]["random_seed"] = 8
    else:  # pragma: no cover - a typo in a parametrisation must not pass silently
        raise AssertionError(f"unknown fault {fault!r}")
    return payload


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runtime_dir=tmp_path, clock=lambda: AS_OF))


@pytest.mark.parametrize(
    ("fault", "reason", "field"),
    [
        ("signal_id", "signal_id_mismatch", "signal.signal_id"),
        ("decision_id", "decision_id_mismatch", "decision.decision_id"),
        ("run_manifest_id", "run_manifest_id_mismatch", "manifest.run_manifest_id"),
    ],
)
def test_the_screen_refusal_names_which_identifier_moved(
    tmp_path: Path, fault: str, reason: str, field: str
) -> None:
    """Each of the three faults comes back under its own `reason`, naming its own field."""
    response = _client(tmp_path).post(
        "/api/v1/screen",
        json={"research": [_tampered("600000.SH", fault=fault)], "criteria": {}},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), "the refusal must carry the panel gate's object shape"
    assert detail["reason"] == reason
    assert detail["field"] == f"research[0].{field}"
    assert detail["subject"] == "600000.SH"
    assert detail["index"] == 0
    assert detail["claimed"] != detail["derived"]
    assert detail["claimed"] and detail["derived"]


def test_the_three_identifier_faults_are_three_different_reasons(tmp_path: Path) -> None:
    """The row's whole complaint: one sentence for three causes.

    A route that improved its wording but still answered one thing for all three fails here.
    """
    client = _client(tmp_path)
    reasons = []
    for fault in ("signal_id", "decision_id", "run_manifest_id"):
        response = client.post(
            "/api/v1/screen",
            json={"research": [_tampered("600000.SH", fault=fault)], "criteria": {}},
        )
        assert response.status_code == 422, response.text
        reasons.append(response.json()["detail"]["reason"])

    assert len(set(reasons)) == 3, f"three causes collapsed into {sorted(set(reasons))}"


def test_the_refusal_names_which_of_many_records_is_the_damaged_one(tmp_path: Path) -> None:
    """5,545 results is the scale the row measured; the refusal must point at the one.

    Kept at 40 records here -- the assertion is that the *index and subject* are the damaged
    record's, and that is no easier to satisfy at 40 than at 5,545. The damaged record is
    deliberately neither first nor last, so an implementation reporting either end fails.
    """
    research = [_serialized_result(subject=f"{index:06d}.SZ") for index in range(40)]
    research[17] = _tampered("000017.SZ", fault="decision_id")

    response = _client(tmp_path).post("/api/v1/screen", json={"research": research, "criteria": {}})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["index"] == 17
    assert detail["subject"] == "000017.SZ"
    assert detail["reason"] == "decision_id_mismatch"
    assert "research[17]" in detail["message"]
    assert "000017.SZ" in detail["message"]
    assert "decision_id" in detail["message"]


def test_the_message_carries_both_the_claimed_and_the_derived_address(tmp_path: Path) -> None:
    """The remedy this refusal can actually offer: the value this service derived.

    Nothing in this deployment serves a stored `ResearchRunResult` back, so there is no fetch
    command to name (`GET /api/v1/runs/{run_id}/recovery` is node progress, not a result). What
    *is* actionable is the address the content derives, which tells a caller whether the record
    was edited or the identifier was: the panel gate's remedy, in the only currency this route
    has.
    """
    response = _client(tmp_path).post(
        "/api/v1/screen",
        json={"research": [_tampered("600000.SH", fault="signal_id")], "criteria": {}},
    )

    detail = response.json()["detail"]
    assert detail["claimed"] in detail["message"]
    assert detail["derived"] in detail["message"]
    assert detail["derived"] != detail["claimed"]

    untampered = _serialized_result(subject="600000.SH")
    assert detail["claimed"] == untampered["signal"]["signal_id"], (
        "the claimed value must be the one the caller sent"
    )


def test_a_malformed_result_is_a_different_reason_from_a_moved_identifier(
    tmp_path: Path,
) -> None:
    """A body that does not validate at all must not borrow an identifier fault's `reason`.

    Both are 422, and before this row both were the same sentence; they need different remedies
    -- one is "your record is not a research result", the other is "your record was edited".
    """
    client = _client(tmp_path)

    missing = client.post("/api/v1/screen", json={"research": [{"signal": {}}], "criteria": {}})
    assert missing.status_code == 422, missing.text
    assert missing.json()["detail"]["reason"] == "malformed_research_result"

    moved = client.post(
        "/api/v1/screen",
        json={"research": [_tampered("600000.SH", fault="signal_id")], "criteria": {}},
    )
    assert moved.json()["detail"]["reason"] != missing.json()["detail"]["reason"]


def test_the_report_route_refuses_with_the_same_shape_and_no_index(tmp_path: Path) -> None:
    """`POST /api/v1/reports` parses one result, not a list, so it names no index.

    Same helper, so the two routes cannot drift: this is the third `_parse_research_result` call
    site and it was flattening the same three causes into the same one sentence.
    """
    response = _client(tmp_path).post(
        "/api/v1/reports", json={"research": _tampered("600000.SH", fault="run_manifest_id")}
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "run_manifest_id_mismatch"
    assert detail["index"] is None
    assert detail["field"] == "research.manifest.run_manifest_id"
    assert detail["subject"] == "600000.SH"


def test_an_untampered_result_still_screens(tmp_path: Path) -> None:
    """The guard against a refusal that got specific by refusing everything."""
    response = _client(tmp_path).post(
        "/api/v1/screen",
        json={
            "research": [_serialized_result(subject="600000.SH")],
            "criteria": {"min_confidence": 0.1},
        },
    )

    assert response.status_code == 200, response.text
    assert [item["subject"] for item in response.json()["items"]] == ["600000.SH"]
