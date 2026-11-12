"""`V2-P7-015`: no LLM-backed agent runs on a path that evaluates history.

The defect this file is written against, stated as the question a reader of a manifest cannot
answer: `RunManifest.agent_versions` records that an agent was `llm_backed` and
`RunManifest.model_versions` records which vendor model it called, but nothing records *what
it was shown*. A replay or a backtest re-runs history, so an LLM-backed agent on either is
handed inputs from the future being evaluated and no artefact pins what it did with them --
`replay`'s whole determinism claim and a backtest's honesty both stop holding, silently.
`domain/llm_contamination.py` is the refusal, and these tests are the three places it has to
happen at: the engine's choke point (the only place every cycle crosses), the replay runner's
composition, and the forward cut-off that keeps a stale live run from being one too.

## Why the engine tests drive `run_cycle` instead of the pure function

The plan's acceptance for this task is "refuses by name **and writes no recovery row**", and
the second half is a claim about *ordering*: `run_cycle` must not touch
`_load_or_start_recovery` before it refuses, or a refused cycle leaves a `running` row that a
later attempt at the same `run_id` is bound to. A test of the pure function cannot see that
ordering, so the refusal tests below build a real `ResearchEngine` over inert protocol doubles
(no disk, no sqlite3) and assert the recovery store is still empty afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.agents.baseline import baseline_agents
from openalpha_cn.backtest.replay import ReplayCase, ReplayCorpus, ReplayRunner
from openalpha_cn.backtest.replay import ResearchEngine as ReplayResearchEngine
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.llm_contamination import (
    LLM_FORWARD_MAX_LAG,
    HistoricalLLMRefusedError,
    refuse_llm_agents_in_history,
)
from openalpha_cn.domain.run import RunManifest, VersionRef
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import RunRecoveryState


class _InertRunRepository:
    """A minimal `RunRepository` double: no disk, no sqlite3, just enough to complete a cycle."""

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
    """A minimal `RecoveryStore` double that still keeps the per-agent slot order honest."""

    def __init__(self) -> None:
        self._states: dict[str, RunRecoveryState] = {}

    def get(self, run_id: str) -> RunRecoveryState | None:
        return self._states.get(run_id)

    def save(self, state: RunRecoveryState) -> None:
        self._states[state.run_id] = state

    def append_result(
        self,
        run_id: str,
        *,
        position: int,
        result: AgentResult,
        updated_at: datetime,
    ) -> None:
        state = self._states[run_id]
        assert position == len(state.completed_results)
        assert state.agent_ids[position] == result.agent_id
        self._states[run_id] = state.model_copy(
            update={
                "completed_results": (*state.completed_results, result),
                "next_agent_index": position + 1,
                "updated_at": updated_at,
            }
        )


class _DeclaringAgent:
    """The smallest thing that satisfies `ResearchAgent`, so the declaration is the variable."""

    def __init__(
        self,
        agent_id: str,
        *,
        kind: str = "llm_backed",
        evidence_families: frozenset[str] = frozenset({"market_event"}),
    ) -> None:
        self.agent_id = agent_id
        self.evidence_families = evidence_families
        self.feature_dependencies: frozenset[str] = frozenset()
        self.provenance = (
            AgentProvenance(
                kind="llm_backed",
                model=VersionRef(component="openai-compatible", version="deepseek-chat"),
            )
            if kind == "llm_backed"
            else AgentProvenance(kind="deterministic")
        )

    def analyze(self, context: AgentContext) -> AgentResult:  # pragma: no cover - refused first
        raise AssertionError("a refused cycle never runs an agent")


def _request(
    *,
    mode: RunMode,
    as_of: datetime,
    evidence_item: EvidenceSnapshot,
    run_id: str = "guard-run",
) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id=run_id,
        mode=mode,
        subject="000001.SZ",
        as_of=as_of,
        evidence=(evidence_item,),
        code_commit="0123456789abcdef",
        config_digest="d" * 64,
        random_seed=7,
    )


def _engine(
    *,
    clock: Callable[[], datetime],
    agents: tuple[Any, ...] | None = None,
) -> tuple[ResearchEngine, _InertRunRepository, _InertRecoveryStore]:
    repository = _InertRunRepository()
    recovery_store = _InertRecoveryStore()
    engine = ResearchEngine(
        repository=repository,
        memory=InMemoryResearchMemory(),
        clock=clock,
        recovery_store=recovery_store,
        agents=agents,
    )
    return engine, repository, recovery_store


def _limit_up(evidence: Callable[..., EvidenceSnapshot]) -> EvidenceSnapshot:
    return evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})


def test_a_replay_mode_run_refuses_an_llm_backed_agent_by_name_and_writes_no_recovery_row(
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """The engine's choke point, and the ordering that makes the refusal worth anything.

    Refused after `router.route` (so the message can name the agent the router actually
    selected) and before `_load_or_start_recovery` (so a refusal leaves **no** row). A
    `running` row left behind would bind the next attempt at this `run_id` to a digest of a
    cycle that never happened, and `_load_or_start_recovery` would then refuse the retry as a
    `RunConflictError` -- a refusal reported as a request conflict is a refusal nobody can act
    on.
    """
    engine, repository, recovery_store = _engine(
        clock=lambda: frozen_now,
        agents=(_DeclaringAgent("llm-agent"),),
    )

    with pytest.raises(HistoricalLLMRefusedError, match="llm-agent"):
        engine.run_cycle(
            _request(mode=RunMode.replay, as_of=frozen_now, evidence_item=_limit_up(evidence))
        )

    assert recovery_store.get("guard-run") is None
    assert repository.get_run("guard-run") is None


def test_a_backtest_mode_run_refuses_an_llm_backed_agent(
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """`backtest` is the other historical mode, and the one D5 refuses predictions on too."""
    engine, _repository, recovery_store = _engine(
        clock=lambda: frozen_now,
        agents=(_DeclaringAgent("llm-agent"),),
    )

    with pytest.raises(HistoricalLLMRefusedError, match=r"backtest.*llm-agent|llm-agent.*backtest"):
        engine.run_cycle(
            _request(mode=RunMode.backtest, as_of=frozen_now, evidence_item=_limit_up(evidence))
        )

    assert recovery_store.get("guard-run") is None


def test_a_live_run_more_than_the_lag_after_as_of_refuses_an_llm_backed_agent(
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """D4's forward half: a live cycle is not historical, but it can be *stale*.

    `LLM_FORWARD_MAX_LAG` is 72 hours because that is what covers Friday's close through
    Monday's catch-up; a model answering about an `as_of` three days behind it is answering
    about a moment it may have seen the outcome of, which is the replay problem reached by
    waiting instead of by asking.
    """
    late_clock = lambda: frozen_now + LLM_FORWARD_MAX_LAG + timedelta(seconds=1)  # noqa: E731
    engine, _repository, recovery_store = _engine(
        clock=late_clock,
        agents=(_DeclaringAgent("llm-agent"),),
    )

    with pytest.raises(HistoricalLLMRefusedError, match="llm-agent"):
        engine.run_cycle(
            _request(mode=RunMode.live, as_of=frozen_now, evidence_item=_limit_up(evidence))
        )

    assert recovery_store.get("guard-run") is None


def test_the_lag_is_a_maximum_so_a_forward_run_at_the_boundary_is_admitted(
    frozen_now: datetime,
) -> None:
    """72 hours exactly is inside D4's bound; one microsecond past it is not.

    Driven through the pure function rather than the engine because the property is the
    comparison and not the wiring: `<` versus `<=` is the whole of it.
    """
    agents = (("llm-agent", "llm_backed"),)
    refuse_llm_agents_in_history(
        path="live",
        mode=RunMode.live,
        as_of=frozen_now,
        ran_at=frozen_now + LLM_FORWARD_MAX_LAG,
        agents=agents,
    )

    with pytest.raises(HistoricalLLMRefusedError, match="llm-agent"):
        refuse_llm_agents_in_history(
            path="live",
            mode=RunMode.live,
            as_of=frozen_now,
            ran_at=frozen_now + LLM_FORWARD_MAX_LAG + timedelta(microseconds=1),
            agents=agents,
        )


def test_rule_based_agents_run_unchanged_in_every_mode(
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """The guard refuses a *kind*, not a mode: a deterministic roster is admitted everywhere.

    Every declared `RunMode` is driven, because a guard that accidentally branched on the
    mode set instead of on the roster would keep the replay test above green and stop
    `paper`/`daily` -- the two forward modes the product actually runs. The shipped roster is
    used rather than a double, so what is admitted is the real `MarketAgent` path.
    """
    for mode in RunMode:
        engine, _repository, recovery_store = _engine(
            clock=lambda: frozen_now,
            agents=baseline_agents(),
        )

        result = engine.run_cycle(
            _request(
                mode=mode,
                as_of=frozen_now,
                evidence_item=_limit_up(evidence),
                run_id=f"rule-{mode.value}",
            )
        )

        assert result.manifest.mode == mode
        assert [(item.agent_id, item.kind) for item in result.manifest.agent_versions] == [
            ("market-agent", "deterministic")
        ]
        assert recovery_store.get(f"rule-{mode.value}") is not None


def test_an_llm_agent_the_router_does_not_select_is_not_refused(
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """The guard reads the roster the router selected, not the one the engine was handed.

    An LLM-backed agent declaring only `theme` evidence is not selected on a limit-up run, so
    it runs nothing and contaminates nothing -- refusing it would make the deployment refuse
    every cycle merely for *composing* an agent, which is a different rule nobody asked for.
    """
    engine, _repository, recovery_store = _engine(
        clock=lambda: frozen_now,
        agents=(_DeclaringAgent("llm-agent", evidence_families=frozenset({"theme"})),),
    )

    result = engine.run_cycle(
        _request(mode=RunMode.replay, as_of=frozen_now, evidence_item=_limit_up(evidence))
    )

    assert result.manifest.agent_versions == ()
    assert result.signal.direction == "abstain"
    assert recovery_store.get("guard-run") is not None


def _replay_case(*, frozen_now: datetime, evidence_item: EvidenceSnapshot) -> ReplayCorpus:
    case = ReplayCase(
        run_id="replay-guard-case",
        trading_day=frozen_now.date(),
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(evidence_item,),
        outcome=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(hours=1),
            start_price=10.0,
            end_price=10.5,
            benchmark_return=0.01,
            transaction_cost=0.001,
        ),
    )
    return ReplayCorpus(
        schema_version="openalpha-replay-corpus/v1",
        trading_days=(frozen_now.date(),),
        cases=(case,),
    )


class _InMemoryValidationStore:
    """Only `append` is called by `ReplayRunner.run`; the reader half is another face's."""

    def __init__(self) -> None:
        self.appended: list[Any] = []

    def append(self, result: Any) -> None:
        self.appended.append(result)


def test_the_replay_runner_composes_no_llm_backed_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    migration_clock: Callable[[], datetime],
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """`ReplayRunner` builds its engines with no roster of its own, and the default is clean.

    Two facts, and both are needed: the runner passes no `agents=` to either engine it builds
    (asserted on the recorded constructions), and the roster a defaulted engine composes is
    every one `deterministic` (asserted on `baseline_agents`). Either alone would leave the
    composition claim open to a later edit of the other.
    """
    recorded: list[dict[str, Any]] = []

    class _RecordingEngine(ReplayResearchEngine):
        def __init__(self, **kwargs: Any) -> None:
            recorded.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(
        "openalpha_cn.backtest.replay.ResearchEngine", _RecordingEngine, raising=True
    )
    validation_store = _InMemoryValidationStore()

    report = ReplayRunner(
        code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7
    ).run(
        corpus=_replay_case(frozen_now=frozen_now, evidence_item=_limit_up(evidence)),
        state_path=tmp_path / "replay.sqlite3",
        validation_store=validation_store,  # type: ignore[arg-type]
        clock=migration_clock,
    )

    assert report.succeeded == 1
    assert report.failures == ()
    assert recorded, "the replay runner built no engine, so this test measured nothing"
    assert all("agents" not in kwargs for kwargs in recorded)
    assert [agent.provenance.kind for agent in baseline_agents()] == [
        "deterministic",
        "deterministic",
        "deterministic",
    ]


def test_the_refusal_names_the_path_the_mode_the_as_of_and_the_lag(frozen_now: datetime) -> None:
    """A refusal a caller cannot diagnose is a refusal a caller cannot act on.

    Names the `path` that refused (several call it), the agents (plural: a roster can hold
    two), the `mode`, the `as_of` the run claimed and the lag it was actually started at.
    """
    with pytest.raises(HistoricalLLMRefusedError) as refused:
        refuse_llm_agents_in_history(
            path="validation",
            mode=RunMode.replay,
            as_of=frozen_now,
            ran_at=frozen_now + timedelta(hours=1),
            agents=(("llm-a", "llm_backed"), ("llm-b", "llm_backed")),
        )

    message = str(refused.value)
    assert "validation" in message
    assert "llm-a" in message and "llm-b" in message
    assert "replay" in message
    assert frozen_now.isoformat() in message
    assert "1:00:00" in message


def test_a_forward_run_inside_the_lag_with_a_deterministic_roster_is_admitted(
    frozen_now: datetime,
) -> None:
    """The admitted branch, stated so it cannot be deleted while the refusals stay green."""
    refuse_llm_agents_in_history(
        path="engine",
        mode=RunMode.live,
        as_of=frozen_now,
        ran_at=frozen_now + timedelta(hours=1),
        agents=(("market-agent", "deterministic"),),
    )
