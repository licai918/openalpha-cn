"""Proves `ResearchEngine.run_cycle` calls `seed_everything` with `request.random_seed`.

This is the "make sure it's wired" half of task 17's seeding requirement, deliberately
without fabricating a consumer of the resulting seeded state: no baseline agent reads
`random` today (see `runtime/seeding.py`'s module docstring), so a test that asserted on
agent *output* changing with the seed would be testing a behavior that does not exist.
This test instead proves the one true thing at stake -- the value genuinely reaches the
seeding entry point at `run_cycle`'s deterministic boundary -- by substituting a spy for
`seed_everything` itself, independent of `tests/unit/runtime/test_seeding.py`'s proof that
the entry point, once called, really does reseed every registered source.
"""

from collections.abc import Callable
from datetime import datetime

import pytest

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.runtime import engine as engine_module
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import RunRecoveryState

DIGEST = "d" * 64


class _InertRunRepository:
    """A minimal `RunRepository` double: no disk, no sqlite3, just enough to let
    `run_cycle` complete once."""

    def __init__(self) -> None:
        self._runs: dict[str, RunManifest] = {}
        self._decisions: dict[str, DecisionLedger] = {}

    def append_run(self, manifest: RunManifest) -> None:
        self._runs[manifest.run_id] = manifest

    def get_run(self, run_id: str) -> RunManifest | None:
        return self._runs.get(run_id)

    def append_decision(self, decision: DecisionLedger) -> None:
        self._decisions[decision.run_id] = decision

    def get_decision_for_run(self, run_id: str) -> DecisionLedger | None:
        return self._decisions.get(run_id)


class _InertRecoveryStore:
    """A minimal `RecoveryStore` double: no disk, no sqlite3."""

    def __init__(self) -> None:
        self._states: dict[str, RunRecoveryState] = {}

    def get(self, run_id: str) -> RunRecoveryState | None:
        return self._states.get(run_id)

    def save(self, state: RunRecoveryState) -> None:
        self._states[state.run_id] = state


def _request(
    *, random_seed: int, frozen_now: datetime, evidence: EvidenceSnapshot
) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id="seed-wiring-run",
        mode="replay",
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=random_seed,
    )


def test_run_cycle_calls_seed_everything_with_the_requests_random_seed(
    monkeypatch: pytest.MonkeyPatch,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(engine_module, "seed_everything", calls.append)
    engine = ResearchEngine(
        repository=_InertRunRepository(),
        memory=InMemoryResearchMemory(),
        clock=lambda: frozen_now,
        recovery_store=_InertRecoveryStore(),
    )
    request = _request(
        random_seed=4242,
        frozen_now=frozen_now,
        evidence=evidence(
            kind="limit_up",
            facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
        ),
    )

    engine.run_cycle(request)

    assert calls == [4242]


def test_run_cycle_calls_seed_everything_exactly_once_per_call(
    monkeypatch: pytest.MonkeyPatch,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(engine_module, "seed_everything", calls.append)
    engine = ResearchEngine(
        repository=_InertRunRepository(),
        memory=InMemoryResearchMemory(),
        clock=lambda: frozen_now,
        recovery_store=_InertRecoveryStore(),
    )
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    engine.run_cycle(_request(random_seed=1, frozen_now=frozen_now, evidence=item))
    engine.run_cycle(_request(random_seed=1, frozen_now=frozen_now, evidence=item))

    assert calls == [1, 1]
