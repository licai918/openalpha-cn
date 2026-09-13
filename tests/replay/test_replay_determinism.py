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

import pytest

import openalpha_cn.runtime.engine as engine_module
from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult, ResearchAgent
from openalpha_cn.agents.baseline import baseline_agents
from openalpha_cn.backtest.replay import ReplayCase, ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import Timeline
from openalpha_cn.storage.migrations import run_migrations
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
