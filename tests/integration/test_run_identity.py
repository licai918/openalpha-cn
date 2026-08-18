"""Roadmap section 9's experiment, re-run against `V2-P4-025` and expected to come out the
other way.

Section 9's method is reproduced deliberately rather than approximated: drive a real
`run_cycle`, fix the clock and the `run_id`, change **one** variable, and read the resulting
identity back. Its table was

| change | `decision_id` |
|---|---|
| no change, repeated run | unchanged |
| `code_commit` alone | changed |
| `config_digest` alone (`a*64` -> `b*64`) | **unchanged** |
| `random_seed` alone (7 -> 99999) | **unchanged** |

and the last two rows are what `V2-P4-025` exists to invert. The unit-level half lives in
`tests/unit/domain/test_contract_identity.py`; this file is the end-to-end half, because
section 9's own finding was that the contract-level reasoning ("all three feed the decision
ID") had been asserted without measurement and was wrong.

Each variant runs against its **own** `runtime_dir`. That is not tidiness: `ResearchEngine`
refuses to reuse a `run_id` whose stored request digest differs, so two differently-configured
runs cannot share a database at all -- which is itself worth knowing, and is asserted at the
bottom of this file rather than left as a reason for a fixture choice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult, RunConflictError
from openalpha_cn.runtime.engine import ResearchEngine

DIGEST: Final[str] = "a" * 64
OTHER_DIGEST: Final[str] = "b" * 64
RUN_ID: Final[str] = "run_section_nine"


@pytest.fixture
def run_once(
    tmp_path: Path,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
    frozen_clock: Callable[[], datetime],
) -> Callable[..., ResearchRunResult]:
    """Drive one real `run_cycle` in a fresh runtime directory, varying one input at a time."""
    counter = {"n": 0}

    def _run(
        *,
        config_digest: str = DIGEST,
        random_seed: int = 7,
        code_commit: str = "0123456789abcdef",
        mode: RunMode = RunMode.live,
    ) -> ResearchRunResult:
        counter["n"] += 1
        storage = build_storage(runtime_dir=tmp_path / f"runtime{counter['n']}", clock=frozen_clock)
        engine = ResearchEngine(
            repository=storage.repository,
            memory=storage.memory,
            clock=frozen_clock,
            recovery_store=storage.recovery_store,
        )
        item = evidence(
            kind="limit_up",
            facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
        )
        return engine.run_cycle(
            ResearchRunRequest(
                run_id=RUN_ID,
                mode=mode,
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(item,),
                code_commit=code_commit,
                config_digest=config_digest,
                random_seed=random_seed,
            )
        )

    return _run


def test_an_unchanged_run_reproduces_every_identity(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """Section 9's control row, and the one that has to keep holding.

    A `run_manifest_id` that moved between two identical runs would make the other four
    assertions in this file meaningless -- everything would "move", including the things that
    must not. Asserted for the manifest, the decision and the signal together, so a change
    that stabilised one by destabilising another cannot pass.
    """
    first = run_once()
    second = run_once()

    assert first.manifest.run_manifest_id == second.manifest.run_manifest_id
    assert first.decision.decision_id == second.decision.decision_id
    assert first.signal.signal_id == second.signal.signal_id


def test_changing_config_digest_alone_moves_the_run_level_id_and_the_decision_id(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """`V2-P4-025`'s acceptance, stated exactly as the roadmap states it.

    Section 9 measured `a*64 -> b*64` leaving `decision_id` untouched, because `config_digest`
    is a field of `RunManifest` and `RunManifest` had no content-addressed identity for it to
    reach. Both halves are asserted here -- the run-level ID (the roadmap's literal acceptance)
    and `decision_id` (PRD section 1.3 B6's actual complaint) -- because the first alone would
    leave "different configurations produce the same decision ID" standing.
    """
    baseline = run_once()
    varied = run_once(config_digest=OTHER_DIGEST)

    assert baseline.manifest.config_digest != varied.manifest.config_digest
    assert baseline.manifest.run_manifest_id != varied.manifest.run_manifest_id
    assert baseline.decision.decision_id != varied.decision.decision_id


def test_changing_random_seed_alone_moves_the_run_level_id_and_the_decision_id(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """Section 9's fourth row, `7 -> 99999`, inverted the same way.

    `random_seed` reaches exactly one thing at runtime today (`runtime/seeding.py`, whose own
    docstring says nothing downstream reads randomness yet), so before this change a seeded
    run and an unseeded one were indistinguishable *by identity* as well as by behaviour. Only
    the identity half is fixed here; the behavioural half is `V2-P4-016`'s.
    """
    baseline = run_once()
    varied = run_once(random_seed=99999)

    assert baseline.manifest.run_manifest_id != varied.manifest.run_manifest_id
    assert baseline.decision.decision_id != varied.decision.decision_id


def test_changing_code_commit_alone_still_moves_the_decision_id(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """Section 9's row that already worked, kept as the control for the two that did not.

    `code_commit` is a field of `DecisionLedger` in its own right, so it moved `decision_id`
    before this change and must still. Without it, a `decision_id` that moved for *every*
    variation -- including ones it should ignore -- would look like a pass.
    """
    baseline = run_once()
    varied = run_once(code_commit="fedcba9876543210")

    assert baseline.decision.decision_id != varied.decision.decision_id
    assert baseline.manifest.run_manifest_id != varied.manifest.run_manifest_id


def test_the_two_new_modes_run_the_same_cycle_and_take_their_own_identity(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """`V2-P4-001`'s mode additions, through the engine rather than through the contract.

    `paper` and `daily` have no behaviour attached yet, and that is the point of asserting the
    *signal* is identical while the manifest and decision addresses differ: the cycle did the
    same work, and the records say which mode asked for it.
    """
    live = run_once(mode=RunMode.live)
    paper = run_once(mode=RunMode.paper)
    daily = run_once(mode=RunMode.daily)

    assert live.signal.signal_id == paper.signal.signal_id == daily.signal.signal_id
    assert len({r.manifest.run_manifest_id for r in (live, paper, daily)}) == 3
    assert len({r.decision.decision_id for r in (live, paper, daily)}) == 3


def test_two_configurations_cannot_share_one_run_id_in_one_database(
    tmp_path: Path,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
    frozen_clock: Callable[[], datetime],
) -> None:
    """Why each variant above needs its own runtime directory, measured rather than assumed.

    This is also the reason section 9's finding was a latent defect rather than an active
    corruption: the engine already refused to write two differently-configured runs under one
    `run_id`, so the colliding `decision_id`s could not actually collide *inside* one database.
    They could collide across databases, in a research memory, in an export, or in any
    reference that treats a content address as one -- which is what `V2-P4-025` closes.
    """
    storage = build_storage(runtime_dir=tmp_path / "runtime", clock=frozen_clock)
    engine = ResearchEngine(
        repository=storage.repository,
        memory=storage.memory,
        clock=frozen_clock,
        recovery_store=storage.recovery_store,
    )
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    def _request(config_digest: str) -> ResearchRunRequest:
        return ResearchRunRequest(
            run_id=RUN_ID,
            mode=RunMode.live,
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
            code_commit="0123456789abcdef",
            config_digest=config_digest,
            random_seed=7,
        )

    engine.run_cycle(_request(DIGEST))

    with pytest.raises(RunConflictError, match="immutable request"):
        engine.run_cycle(_request(OTHER_DIGEST))


def test_the_api_refuses_a_research_result_whose_manifest_address_was_tampered_with(
    tmp_path: Path,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """`V2-P4-025` adds a third computed identifier to the round trip, so it needs a third
    integrity check.

    `POST /api/v1/backtests/validate` takes back the whole `ResearchRunResult` a client was
    handed, and every computed identifier in it has to be stripped before validation (the
    contracts are `extra="forbid"`) and re-derived afterwards. Stripping alone would let a
    caller hand back a manifest address that does not describe the manifest beside it -- an
    address that answers for nothing, which is exactly section 9's failure moved one level up.
    `signal_id` already had this test; this is its `run_manifest_id` counterpart.
    """
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})
    response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": RUN_ID,
            "mode": "live",
            "subject": "000001.SZ",
            "as_of": frozen_now.isoformat(),
            "evidence": [item.model_dump(mode="json", exclude_computed_fields=True)],
            "code_commit": "0123456789abcdef",
            "config_digest": DIGEST,
            "random_seed": 7,
        },
    )
    assert response.status_code == 200, response.text

    tampered = response.json()
    assert tampered["manifest"]["run_manifest_id"].startswith("run_")
    tampered["manifest"]["run_manifest_id"] = "run_" + "0" * 24

    rejected = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": tampered,
            "observation": {
                "observation_start": frozen_now.isoformat(),
                "observation_end": (frozen_now + timedelta(days=5)).isoformat(),
                "start_price": 10.0,
                "end_price": 11.0,
                "benchmark_return": 0.02,
                "transaction_cost": 0.005,
            },
        },
    )

    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "Research result failed integrity validation."}
