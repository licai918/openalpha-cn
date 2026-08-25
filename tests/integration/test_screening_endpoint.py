"""`POST /api/v1/screen` end to end, which nothing drove before `V2-P4-006`.

The endpoint has shipped since v1 and no test in this repository reached it: a grep for
`api/v1/screen` across `tests/` at `5e18791` returned nothing. That mattered here because this
issue changes the endpoint's response model -- `ScreeningItem` gains a governance reading and
`ScreeningResult` gains `excluded` -- and a unit test on `ResearchScreener` cannot see whether
those survive FastAPI's serialization or whether the `Literal` severities round-trip as JSON.

The request body is deliberately built by serializing a real `ResearchRunResult`, because
`_parse_research_result` re-derives and *verifies* all three content addresses; a
hand-assembled payload would fail integrity validation rather than exercise the screen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.contracts import ResearchRunResult

AS_OF: Final[datetime] = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)
COMMIT: Final[str] = "0123456789abcdef"
EVIDENCE: Final[tuple[str, ...]] = ("evd_000000000000000000000001",)


def _serialized_result(
    *, subject: str, confidence: float, risk_flags: tuple[str, ...]
) -> dict[str, Any]:
    signal = SignalFrame(
        subject=subject,
        as_of=AS_OF,
        direction="bullish",
        strength=0.4,
        confidence=confidence,
        horizon="5d",
        evidence_ids=EVIDENCE,
        risk_flags=risk_flags,
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
    result = ResearchRunResult(
        signal=signal, decision=decision, manifest=manifest, agent_results=()
    )
    return result.model_dump(mode="json")


def test_the_screen_endpoint_returns_the_governed_order_and_the_reasons(tmp_path: Path) -> None:
    """The shipped face of `V2-P4-006`, driven over HTTP rather than in process.

    Three names: one confident and blocked, one clean, one below the confidence floor. The
    response must put the clean name first despite the lower confidence, name the flag that
    demoted the other, and account for the third in `excluded` rather than dropping it.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: AS_OF))

    response = client.post(
        "/api/v1/screen",
        json={
            "research": [
                _serialized_result(
                    subject="000001.SZ", confidence=0.95, risk_flags=("future_data",)
                ),
                _serialized_result(subject="600000.SH", confidence=0.60, risk_flags=()),
                _serialized_result(subject="300001.SZ", confidence=0.05, risk_flags=()),
            ],
            "criteria": {"min_confidence": 0.1},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reviewed"] == 3
    assert [item["subject"] for item in body["items"]] == ["600000.SH", "000001.SZ"]
    assert [item["severity"] for item in body["items"]] == ["clear", "blocked"]
    assert body["items"][1]["driving_flags"] == ["future_data"]
    assert body["items"][1]["gate_decision"] == "block"
    assert body["items"][0]["driving_flags"] == []
    assert [entry["subject"] for entry in body["excluded"]] == ["300001.SZ"]
    assert body["excluded"][0]["reason"] == "below_min_confidence"
    assert body["reviewed"] == len(body["items"]) + len(body["excluded"])


def test_the_screen_endpoint_still_refuses_a_result_whose_identifiers_do_not_match(
    tmp_path: Path,
) -> None:
    """The integrity check the new fields must not have loosened: a tampered confidence moves
    `signal_id`, and the endpoint refuses rather than screening it.

    `V2-P4-041` made the refusal say *which* address moved and on which record; what this test
    is for is unchanged -- the check still fires -- so it asserts the `reason` rather than the
    flat sentence it used to be. The full shape is driven by
    `tests/integration/test_screen_integrity_refusal_names_the_record.py`.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: AS_OF))
    tampered = _serialized_result(subject="000001.SZ", confidence=0.5, risk_flags=())
    tampered["signal"]["confidence"] = 0.9

    response = client.post("/api/v1/screen", json={"research": [tampered], "criteria": {}})

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "signal_id_mismatch"
    assert response.json()["detail"]["subject"] == "000001.SZ"


def test_the_screen_endpoint_accepts_a_severity_cut_by_name(tmp_path: Path) -> None:
    """`worst_severity_admitted` is a new request field, so a caller can actually send it."""
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: AS_OF))

    response = client.post(
        "/api/v1/screen",
        json={
            "research": [
                _serialized_result(
                    subject="000001.SZ", confidence=0.95, risk_flags=("future_data",)
                ),
                _serialized_result(subject="600000.SH", confidence=0.60, risk_flags=()),
            ],
            "criteria": {"worst_severity_admitted": "clear"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["subject"] for item in body["items"]] == ["600000.SH"]
    assert [entry["reason"] for entry in body["excluded"]] == ["worse_than_admitted_severity"]
    assert body["excluded"][0]["driving_flags"] == ["future_data"]

    refused = client.post(
        "/api/v1/screen",
        json={"research": [], "criteria": {"worst_severity_admitted": "not-a-rung"}},
    )
    assert refused.status_code == 422
