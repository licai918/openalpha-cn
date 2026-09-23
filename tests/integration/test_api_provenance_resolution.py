"""Server-side resolution of `code_commit`/`config_digest` on the web-facing REST API.

Critical review finding on task 17: `web/src/api/client.ts` POSTed the literal
fabricated values `code_commit: "web-development"` and `config_digest:
"0".repeat(64)` on every `/api/v1/research/run` and `/api/v1/backtests/replay` call,
because `ResearchApiRequest`/`ReplayApiRequest` (`api/app.py`) required both fields
with no server-side fallback -- so every run started from the actual product UI
minted a fabricated provenance record, exactly the failure `runtime/provenance.py`
(V2-P0B-009) existed to eliminate for the CLI/SDK path. A browser cannot know the
server's own git commit or effective config, so only the server can honestly fill
these in when a caller omits them.

Decision: an explicitly supplied value is *honoured*, not overridden -- mirrors
`cli.py`'s `_resolved_code_commit`/`_resolved_config_digest` (explicit wins,
resolution only runs for a genuine omission) and is required for REST/SDK parity:
`test_rest_sdk_clock_parity.py` feeds the same explicit `code_commit`/`config_digest`
through both `OpenAlphaSDK.run_research` and `POST /api/v1/research/run` and asserts
byte-identical output, including `decision_id`. If the API silently overrode an
explicit value, that parity test would fail (the SDK path never resolves).
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.storage.sqlite import SQLiteRunRepository

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "replay" / "a-share-v1-corpus.json"


def _assert_real_commit(code_commit: str) -> None:
    assert code_commit not in {"web-development", "development", "unknown-not-a-git-commit"}
    hex_part = code_commit.removesuffix("-dirty")
    assert len(hex_part) == 40
    assert all(char in "0123456789abcdef" for char in hex_part)


def _assert_real_digest(config_digest: str) -> None:
    assert config_digest != "0" * 64
    assert len(config_digest) == 64
    assert all(char in "0123456789abcdef" for char in config_digest)


def test_research_run_without_provenance_fields_resolves_a_real_commit_and_digest(
    tmp_path: Path,
    frozen_now: datetime,
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """The reviewer's experiment: POST without `code_commit`/`config_digest` -- exactly
    what `web/src/api/client.ts` must do once its fabricated literals are removed --
    and confirm the stored manifest carries a real commit and digest."""
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: frozen_now))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": "web-ui-run",
            "mode": "live",
            "subject": "000001.SZ",
            "as_of": frozen_now.isoformat(),
            "evidence": [item.model_dump(mode="json")],
            "random_seed": 7,
        },
    )

    assert response.status_code == 200, response.text
    manifest = response.json()["manifest"]
    _assert_real_commit(manifest["code_commit"])
    _assert_real_digest(manifest["config_digest"])


def test_research_run_with_explicit_provenance_fields_is_honoured_verbatim(
    tmp_path: Path,
    frozen_now: datetime,
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: frozen_now))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": "explicit-provenance-run",
            "mode": "live",
            "subject": "000001.SZ",
            "as_of": frozen_now.isoformat(),
            "evidence": [item.model_dump(mode="json")],
            "code_commit": "0123456789abcdef",
            "config_digest": "e" * 64,
            "random_seed": 7,
        },
    )

    assert response.status_code == 200, response.text
    manifest = response.json()["manifest"]
    assert manifest["code_commit"] == "0123456789abcdef"
    assert manifest["config_digest"] == "e" * 64


def test_replay_without_provenance_fields_resolves_a_real_commit_and_digest(
    tmp_path: Path,
) -> None:
    raw_corpus: dict[str, Any] = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    first_run_id = raw_corpus["cases"][0]["run_id"]
    client = TestClient(create_app(runtime_dir=tmp_path))

    response = client.post(
        "/api/v1/backtests/replay",
        json={"corpus": raw_corpus, "random_seed": 7},
    )

    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] > 0

    stored = SQLiteRunRepository(tmp_path / "api-replay.sqlite3").get_run(first_run_id)
    assert stored is not None
    _assert_real_commit(stored.code_commit)
    _assert_real_digest(stored.config_digest)


def test_replay_with_explicit_provenance_fields_is_honoured_verbatim(tmp_path: Path) -> None:
    raw_corpus: dict[str, Any] = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    first_run_id = raw_corpus["cases"][0]["run_id"]
    client = TestClient(create_app(runtime_dir=tmp_path))

    response = client.post(
        "/api/v1/backtests/replay",
        json={
            "corpus": raw_corpus,
            "code_commit": "0123456789abcdef",
            "config_digest": "f" * 64,
            "random_seed": 7,
        },
    )

    assert response.status_code == 200, response.text
    stored = SQLiteRunRepository(tmp_path / "api-replay.sqlite3").get_run(first_run_id)
    assert stored is not None
    assert stored.code_commit == "0123456789abcdef"
    assert stored.config_digest == "f" * 64
