"""The replay determinism check has to be able to fail (D13, final review I3).

`ReplayRunner.run` executes every case twice and counts it as a deterministic replay only
when the two `ResearchRunResult`s are equal. Until D13 both passes went through one engine,
one `SQLiteRecoveryStore` and one `run_id`. The second `run_cycle` therefore found the first
pass's `succeeded` recovery state -- `_load_or_start_recovery` returns a stored state as it is
(`runtime/engine.py`), and a succeeded state has `next_agent_index == len(agent_ids)`
(`storage/recovery.py`) -- ran no agent at all, and rebuilt its result from what the first
pass had stored. The comparison was "computed" against "the same thing read back", and an
agent that answered differently on every call still scored a deterministic replay.

`tests/replay/test_frozen_corpus.py` cannot show that: it replays only the three
deterministic baselines, so it passed whether or not the check could fail. The tests below
put an agent into a replay through `ResearchEngine`'s own default roster -- the one name
`ReplayRunner` gives no way to override -- by patching `runtime.engine.baseline_agents`,
and count the agent's calls, which is the direct measure of whether the second pass ran.

What these tests do not cover: nondeterminism that `seed_everything(request.random_seed)`
controls is, correctly, reproduced by both passes and not reported, and so is an agent
whose output depends only on something constant across one process.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import pytest

import openalpha_cn.backtest.replay as replay_module
import openalpha_cn.runtime.engine as engine_module
from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult, ResearchAgent
from openalpha_cn.agents.baseline import baseline_agents
from openalpha_cn.backtest.replay import ReplayCase, ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.storage.migrations import run_migrations
from openalpha_cn.storage.recovery import (
    RecoveryConflictError,
    RunRecoveryState,
    SQLiteRecoveryStore,
)
from openalpha_cn.storage.validation import SQLiteValidationStore


class _DriftingAgent:
    """Reads market evidence and never gives the same answer twice.

    It declares itself `deterministic`, because that is the defect a determinism check exists
    for: an agent that claims reproducibility and does not have it. Only the rationale moves,
    call by call, so every result it returns is a valid one and nothing but the comparison of
    two passes can tell them apart.
    """

    agent_id = "drifting-agent"
    evidence_families = frozenset({"market_event"})
    feature_dependencies: frozenset[str] = frozenset()
    provenance = AgentProvenance(kind="deterministic")

    def __init__(self, calls: Iterator[int]) -> None:
        self._calls = calls

    def analyze(self, context: AgentContext) -> AgentResult:
        answer = next(self._calls)
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="bullish",
                strength=0.5,
                confidence=0.5,
                horizon="5d",
                evidence_ids=tuple(item.evidence_id for item in context.evidence),
            ),
            rationale=f"Answer number {answer} to the same question.",
        )


class _CountingAgent:
    """A real baseline agent, unchanged, with its calls counted by `agent_id`."""

    def __init__(self, inner: ResearchAgent, calls: Counter[str]) -> None:
        self._inner = inner
        self._calls = calls
        self.agent_id = inner.agent_id
        self.evidence_families = inner.evidence_families
        self.feature_dependencies = inner.feature_dependencies
        self.provenance = inner.provenance

    def analyze(self, context: AgentContext) -> AgentResult:
        self._calls[self.agent_id] += 1
        return self._inner.analyze(context)


def _limit_up(subject: str, at: datetime) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(event_time=at, available_time=at, ingested_time=at, revision_time=at),
        source_id="replay-determinism.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up for the determinism check.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )


def _two_case_corpus(frozen_now: datetime) -> ReplayCorpus:
    cases = []
    for offset, subject in enumerate(("000001.SZ", "000002.SZ")):
        as_of = frozen_now + timedelta(days=offset)
        cases.append(
            ReplayCase(
                run_id=f"determinism-case-{offset}",
                trading_day=as_of.date(),
                subject=subject,
                as_of=as_of,
                evidence=(_limit_up(subject, as_of),),
                outcome=OutcomeObservation(
                    observation_start=as_of,
                    observation_end=as_of + timedelta(days=5),
                    start_price=10.0,
                    end_price=10.5,
                    benchmark_return=0.01,
                    transaction_cost=0.001,
                ),
            )
        )
    return ReplayCorpus(
        schema_version="openalpha-replay-corpus/v1",
        trading_days=tuple(case.trading_day for case in cases),
        cases=tuple(cases),
    )


def _replay(corpus: ReplayCorpus, tmp_path: Path, clock: Callable[[], datetime]) -> ReplayReport:
    validation_path = tmp_path / "state.sqlite3"
    run_migrations(validation_path, clock=clock)
    return ReplayRunner(code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7).run(
        corpus=corpus,
        state_path=tmp_path / "replay.sqlite3",
        validation_store=SQLiteValidationStore(validation_path),
        clock=clock,
    )


def test_an_agent_that_answers_differently_each_call_is_not_counted_as_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frozen_now: datetime,
    migration_clock: Callable[[], datetime],
) -> None:
    """The acceptance test for `OA-BT-003`: the two passes are compared, and can disagree.

    Measured before the fix: `deterministic_replays == 2`, `succeeded == 2`, no failures,
    and the agent called twice -- once per case, by the first pass only.
    """
    calls = count()
    monkeypatch.setattr(engine_module, "baseline_agents", lambda: (_DriftingAgent(calls),))
    corpus = _two_case_corpus(frozen_now)

    report = _replay(corpus, tmp_path, migration_clock)

    assert report.total_cases == 2
    assert report.deterministic_replays == 0
    assert report.succeeded == 0
    assert report.validation_ids == ()
    assert report.failures == tuple(
        f"{case.run_id}: nondeterministic replay" for case in corpus.cases
    )
    assert next(calls) == 4, "each case's agent must run once per pass, twice in all"


def test_the_deterministic_baselines_replay_identically_when_both_passes_compute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frozen_now: datetime,
    migration_clock: Callable[[], datetime],
) -> None:
    """The control: the same comparison, with both passes computing, passes the baselines.

    Only `market-agent` is routed for limit-up evidence, and it runs once per pass per case.
    Before the fix it ran once per case: the second pass read the first one back.
    """
    calls: Counter[str] = Counter()
    monkeypatch.setattr(
        engine_module,
        "baseline_agents",
        lambda: tuple(_CountingAgent(agent, calls) for agent in baseline_agents()),
    )

    report = _replay(_two_case_corpus(frozen_now), tmp_path, migration_clock)

    assert calls == Counter({"market-agent": 4})
    assert report.deterministic_replays == 2
    assert report.succeeded == 2
    assert report.failures == ()
    assert len(report.validation_ids) == 2


def test_replaying_a_corpus_again_compares_the_stored_first_pass_with_a_fresh_second(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frozen_now: datetime,
    migration_clock: Callable[[], datetime],
) -> None:
    """A repeat against the same replay database still recomputes, and still agrees.

    The replay database keeps what the first `run()` stored, and the first pass of a repeat
    reuses it (`run_cycle` is idempotent by `run_id`). The second pass recomputes regardless,
    so a repeat compares yesterday's stored answer with one computed now: one agent call per
    case, and for the deterministic baselines the same result.
    """
    calls: Counter[str] = Counter()
    monkeypatch.setattr(
        engine_module,
        "baseline_agents",
        lambda: tuple(_CountingAgent(agent, calls) for agent in baseline_agents()),
    )
    corpus = _two_case_corpus(frozen_now)
    _replay(corpus, tmp_path, migration_clock)
    calls.clear()

    again = _replay(corpus, tmp_path, migration_clock)

    assert calls == Counter({"market-agent": 2})
    assert again.deterministic_replays == 2
    assert again.succeeded == 2
    assert again.failures == ()


class _RewordedAgent:
    """A real baseline agent whose rationale the next version of the code words differently."""

    def __init__(self, inner: ResearchAgent) -> None:
        self._inner = inner
        self.agent_id = inner.agent_id
        self.evidence_families = inner.evidence_families
        self.feature_dependencies = inner.feature_dependencies
        self.provenance = inner.provenance

    def analyze(self, context: AgentContext) -> AgentResult:
        result = self._inner.analyze(context)
        return result.model_copy(update={"rationale": f"{result.rationale} Reworded."})


def test_a_stored_run_the_code_no_longer_reproduces_is_not_reported_as_nondeterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frozen_now: datetime,
    migration_clock: Callable[[], datetime],
) -> None:
    """A repeat that disagrees with an earlier replay's stored run is named for that (Minor-5).

    The replay database outlives the call -- `sdk-replay.sqlite3`, `api-replay.sqlite3` -- and
    the first run of a repeated case is the stored one, so a repeat compares yesterday's result
    with what the code computes today. Here the code changed in between: every baseline agent
    words its rationale differently, with the same roster, so the request digest and graph
    signature still match and nothing refuses the reuse. Neither run is flaky; the stored one is
    simply no longer what this code produces, and a failure that said `nondeterministic replay`
    sent the reader after randomness that is not there. Measured before the change: both cases
    were reported as `nondeterministic replay`.
    """
    corpus = _two_case_corpus(frozen_now)
    _replay(corpus, tmp_path, migration_clock)
    monkeypatch.setattr(
        engine_module,
        "baseline_agents",
        lambda: tuple(_RewordedAgent(agent) for agent in baseline_agents()),
    )

    again = _replay(corpus, tmp_path, migration_clock)

    assert again.deterministic_replays == 0
    assert again.succeeded == 0
    assert again.failures == tuple(
        f"{case.run_id}: stored run differs from a fresh recomputation" for case in corpus.cases
    )


def test_both_runs_of_a_case_are_built_from_one_engine_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frozen_now: datetime,
    migration_clock: Callable[[], datetime],
) -> None:
    """The second run is configured by the first run's configuration, not beside it (Minor-6).

    Every construction of `ResearchEngine` during the replay is recorded. For each case the two
    must have been given the same keywords, and every keyword that is not storage must be the
    very same object -- so an `agents=`, `router=`, `risk_gate=` or `features=` given to one run
    reaches the other, instead of the second quietly recomputing with the defaults. Measured
    before the change: the keys agreed and no value was shared -- each run got its own
    `_fixed_clock(case.as_of)` -- so the two engines matched only because both spelled the same
    defaults.
    """
    built: list[dict[str, Any]] = []

    class _RecordingEngine(ResearchEngine):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(replay_module, "ResearchEngine", _RecordingEngine)

    report = _replay(_two_case_corpus(frozen_now), tmp_path, migration_clock)

    assert report.deterministic_replays == 2
    assert len(built) == 4, "two engines per case"
    storage = {"repository", "recovery_store", "memory"}
    for first, second in (built[0:2], built[2:4]):
        assert first.keys() == second.keys()
        configuration = sorted(first.keys() - storage)
        assert configuration, "the engines were given nothing but storage"
        unshared = [name for name in configuration if first[name] is not second[name]]
        assert unshared == [], f"the second run was configured on its own for {unshared}"


def _abstaining(agent_id: str, at: datetime) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        signal=SignalFrame(
            subject="000001.SZ",
            as_of=at,
            direction="abstain",
            strength=0,
            confidence=0,
            horizon="5d",
            abstention_reason="Nothing to read.",
        ),
        rationale="Nothing to read.",
    )


def test_the_second_runs_recovery_store_refuses_what_sqlite_refuses_and_a_skipped_slot(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """What `_EmptyRecoveryStore`'s docstring says about itself, measured against the SQLite store.

    A review found the docstring claiming the two refuse the same writes. They do not: the
    SQLite store's `_claim_slot` keys on the position, the agent and an unwritten payload, so it
    also accepts a result for a later slot while an earlier one is still empty -- and then cannot
    read the run back, because `validate_progress` refuses a completed set that is not a prefix of
    the graph. The in-memory store refuses that write outright. The engine only ever appends in
    order, so no replay can tell the difference; this pins the difference the docstring states.
    """

    def stores(name: str) -> tuple[SQLiteRecoveryStore, Any]:
        state = RunRecoveryState(
            run_id="run",
            request_digest="a" * 64,
            graph_signature="b" * 64,
            agent_ids=("first-agent", "second-agent"),
            next_agent_index=0,
            started_at=frozen_now,
            updated_at=frozen_now,
        )
        sqlite_store = SQLiteRecoveryStore(tmp_path / f"{name}.sqlite3")
        memory_store = replay_module._EmptyRecoveryStore()
        sqlite_store.save(state)
        memory_store.save(state)
        return sqlite_store, memory_store

    def write(store: Any, run_id: str, position: int, agent_id: str) -> None:
        store.append_result(
            run_id,
            position=position,
            result=_abstaining(agent_id, frozen_now),
            updated_at=frozen_now,
        )

    for store in stores("no-state"):
        with pytest.raises(RecoveryConflictError):
            write(store, "a-run-with-no-state", 0, "first-agent")
    for store in stores("wrong-agent"):
        with pytest.raises(RecoveryConflictError):
            write(store, "run", 0, "second-agent")
    for store in stores("rewrite"):
        write(store, "run", 0, "first-agent")
        with pytest.raises(RecoveryConflictError):
            write(store, "run", 0, "first-agent")

    sqlite_store, memory_store = stores("skip")
    write(sqlite_store, "run", 1, "second-agent")
    with pytest.raises(ValueError, match="graph prefix"):
        sqlite_store.get("run")
    with pytest.raises(RecoveryConflictError):
        write(memory_store, "run", 1, "second-agent")
