"""REST and SDK must agree, field for field, under the same injected clock.

V2-P0B-008. `sdk.py`'s `OpenAlphaSDK` has taken an injectable `clock` since it was
written (`sdk.py:52`, threaded into `build_storage` and every `ResearchEngine` it
builds). `api/app.py`'s `create_app()` never did: four call sites each built their own
`lambda: datetime.now(UTC)` (`build_storage`, two `ResearchEngine`s, one
`BatchResearchService`). Task 10's reviewer diffed a REST research result against an
SDK research result for the identical input and found exactly one class of divergence
-- the wall-clock-derived timestamps -- traced to those hardcoded lambdas.

That divergence is not cosmetic: `DecisionLedger.created_at` is a computed-field input
to `decision_id` (`domain/decision.py`), a content-addressed identifier. So the same
input, run through REST vs. the SDK, used to mint two *different* decision IDs -- the
same research decision would not durably identify itself the same way depending on
which surface produced it.

This module is the acceptance test for the fix: given the same frozen clock and the
same input, REST and SDK must produce byte-identical `ResearchRunResult` JSON,
including `decision_id`. It was written and confirmed red (a bare `TypeError:
create_app() got an unexpected keyword argument 'clock'`, since the parameter did not
exist yet) before `create_app()` gained the `clock` parameter.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK


def _research_request(frozen_now: datetime, run_id: str) -> ResearchRunRequest:
    evidence = EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now,
            ingested_time=frozen_now,
            revision_time=frozen_now,
        ),
        source_id="clock-parity.fixture",
        source_uri="fixture://clock-parity/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Clock parity fixture.",
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
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest="c" * 64,
        random_seed=7,
    )


def _serialized_payload(request: ResearchRunRequest) -> dict[str, object]:
    """Mirror `test_batch_research.py::_serialized_payload`: a client naturally passes
    evidence items back through with their computed `evidence_id`/`content_hash` still
    attached, so this keeps those fields instead of stripping them."""
    payload = request.model_dump(mode="json", exclude_computed_fields=True)
    payload["evidence"] = [item.model_dump(mode="json") for item in request.evidence]
    return payload


def test_rest_and_sdk_produce_identical_research_results_under_the_same_frozen_clock(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The core acceptance criterion: same frozen clock, same input -> REST and SDK
    must agree on every field of `ResearchRunResult`, including `decision_id`."""
    request = _research_request(frozen_now, "clock-parity-run")

    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    sdk_result = sdk.run_research(request)

    client = TestClient(create_app(runtime_dir=tmp_path / "rest", clock=lambda: frozen_now))
    response = client.post("/api/v1/research/run", json=_serialized_payload(request))

    assert response.status_code == 200, response.text
    rest_payload = response.json()
    sdk_payload = sdk_result.model_dump(mode="json")

    assert rest_payload == sdk_payload
    assert rest_payload["decision"]["decision_id"] == sdk_payload["decision"]["decision_id"]


def test_two_rest_runs_under_the_same_frozen_clock_mint_the_same_decision_id(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Determinism proof: two independent `create_app()` instances, given the same
    frozen clock and the same request, must mint the identical `decision_id`. Before
    this task, each instance's `lambda: datetime.now(UTC)` made this impossible to
    observe -- two runs a millisecond apart would already disagree."""
    request = _research_request(frozen_now, "clock-parity-repeat")
    payload = _serialized_payload(request)

    first_client = TestClient(create_app(runtime_dir=tmp_path / "run-1", clock=lambda: frozen_now))
    second_client = TestClient(create_app(runtime_dir=tmp_path / "run-2", clock=lambda: frozen_now))

    first = first_client.post("/api/v1/research/run", json=payload)
    second = second_client.post("/api/v1/research/run", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert first.json()["decision"]["decision_id"] == second.json()["decision"]["decision_id"]


_MODULE_LEVEL_APP_PROBE = """
import json

from fastapi.testclient import TestClient

from openalpha_cn.api.app import app

client = TestClient(app)
response = client.get("/health")
print(json.dumps({"status_code": response.status_code, "body": response.json()}))
"""


def test_module_level_app_still_builds_and_serves_without_a_clock_argument(
    tmp_path: Path,
) -> None:
    """Regression guard: `api/app.py`'s module-scope `app = create_app()` takes no
    arguments at all, so it must keep constructing and serving successfully using
    `clock`'s default -- exactly as before this task added the parameter.

    Run in a fresh subprocess so `app = create_app()` genuinely executes at import
    time: in-process, `openalpha_cn.api.app` is already cached in `sys.modules` from
    earlier test collection, so an in-process check would not exercise that line at
    all. `OPENALPHA_RUNTIME_DIR` is pointed at `tmp_path` so this probe never touches
    the real repository-root `./runtime` directory that `api/app.py`'s documented
    default would otherwise resolve to.
    """
    result = subprocess.run(
        [sys.executable, "-c", _MODULE_LEVEL_APP_PROBE],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "OPENALPHA_RUNTIME_DIR": str(tmp_path)},
    )

    assert result.returncode == 0, (
        f"probe subprocess failed (exit {result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["status_code"] == 200
    assert payload["body"]["status"] == "ok"
