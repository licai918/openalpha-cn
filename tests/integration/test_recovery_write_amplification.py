"""`V2-P4-020`: what one research run costs the recovery store, counted rather than timed.

The defect this file measures: `ResearchEngine._run_agents_with_recovery` saved the
**accumulated** result set after every agent, and each save was a full serialisation of the
whole growing document -- twice over, because `_updated_recovery` round-tripped it through
`model_dump(mode="python")` and `model_validate` before the store's own
`model_dump_json` ever ran. `N` agents therefore cost `N(N+1)/2` result serialisations to
persist `N` results.

## Why the assertions count operations instead of wall clock

A timing assertion that passes on an idle machine and fails under load is worse than no
assertion, and this repository runs several agents concurrently on one box. So the two
counters below are exact integers derived from the work itself:

- `revalidated_results` -- how many `AgentResult`s the engine hands back through
  `RunRecoveryState.model_validate` over one run.
- `payload_chars` -- how many characters of JSON the store binds to SQLite over one run.

Both are deterministic: run the same `N` twice and get the same numbers.

## The shape of the assertion is a ratio, not a constant

Each test drives two sizes and compares them, because that is what separates the two
answers. An absolute bound ("under a million characters") can be met by a quadratic
implementation at a small `N` and says nothing; the *growth* between `N` and `2N` cannot.
Doubling `N` doubles a linear cost and quadruples a quadratic one, so the tests require the
larger run to cost less than **three** times the smaller -- a margin wide enough that neither
answer is near it from the wrong side.

Measured at `be262ea`, before the fix, on the sizes below (an idle-ish box, load average
~5-6 with sibling agents running -- which is exactly why none of this is a stopwatch):

| N   | saves | results serialised | JSON written |
|-----|-------|--------------------|--------------|
| 12  | 13    | 78                 | 0.05 MB      |
| 50  | 51    | 1,275              | 0.76 MB      |
| 100 | 101   | 5,050              | 2.97 MB      |
| 200 | 201   | 20,100             | 11.74 MB     |
| 400 | 401   | 80,200             | 46.68 MB     |

`N=12` reproduces the roadmap row's 78 exactly, which is where the row's number came from.
`results serialised` is `N(N+1)/2` at every size. The curve is invisible at 12 and plain at
200, which is why the tests below run at 60 and 120 rather than at the shipped agent count:
a fixture that cannot separate the two answers is the defect this project books most often.

The split between the two halves, measured the same day at `N=400`: the engine's
dump-and-revalidate cost 0.327 s and the store's JSON dump 0.082 s. Both halves are
quadratic, so both are counted here; fixing either alone leaves the other's curve standing
and one of these tests red.

After the fix, the same harness at the same sizes:

| N   | results serialised | JSON written | per result |
|-----|--------------------|--------------|------------|
| 12  | 12                 | 0.01 MB      | 1.0        |
| 100 | 100                | 0.06 MB      | 1.0        |
| 400 | 400                | 0.23 MB      | 1.0        |
| 800 | 800                | 0.45 MB      | 1.0        |

One serialisation per result at every size, and 0.23 MB against 46.68 MB at `N=400`. The run
loop's wall clock over the same range is 0.106 / 0.208 / 0.438 s at 200 / 400 / 800 -- flat
per agent, and now dominated by opening a connection per write rather than by anything that
grows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.storage import recovery as recovery_module
from openalpha_cn.storage.recovery import RunRecoveryState, SQLiteRecoveryStore

SMALL = 60
LARGE = 120
"""The two sizes every assertion below is a comparison between.

Chosen from the table in this module's docstring: at `N=12` a quadratic and a linear
implementation differ by 78 serialisations against 12 -- a gap a slow interpreter could
hide -- while `60 -> 120` puts 1,830 against 7,260, four times apart, and still runs in well
under a second either way. `LARGE == 2 * SMALL` exactly, because the assertions are about
what doubling costs.
"""

GROWTH_CEILING = 3.0
"""Cost at `LARGE` divided by cost at `SMALL` must stay under this.

Linear work doubles (2.0), quadratic work quadruples (4.0), and 3.0 sits between them with
room on both sides. It is not a performance target: it is the number that makes the
assertion able to fail for the reason it names, which a bound closer to either answer would
not be.
"""


class _CountingAgent:
    """A `ResearchAgent` that does no research, so the run's cost is the bookkeeping.

    The rationale is padded to roughly the size of a real one. Padding matters to the
    `payload_chars` counter and not at all to `revalidated_results`, so the two counters
    disagree about what they are sensitive to -- which is why both are here.
    """

    evidence_families = frozenset({"market_event"})
    provenance = AgentProvenance(kind="deterministic")

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.calls = 0

    def analyze(self, context: AgentContext) -> AgentResult:
        self.calls += 1
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="bullish",
                strength=0.5,
                confidence=0.7,
                horizon="5d",
                evidence_ids=("ev-amplification",),
            ),
            rationale=f"{self.agent_id} completed. " + "detail " * 30,
        )


@dataclass
class _RunCost:
    """One run's bookkeeping cost, in two units that fail for different reasons."""

    agents: int
    saves: int = 0
    appends: int = 0
    results_handed_to_the_store: int = 0
    revalidated_results: int = 0
    payload_chars: int = 0
    statements: int = 0
    stored_result_ids: tuple[str, ...] = field(default=())


class _CountingConnection:
    """A `sqlite3.Connection` proxy that totals the TEXT it is asked to write.

    Counting the bound parameters rather than the statement text is the point: the store's
    cost is the payload it serialises, and every implementation of it -- one blob, one row
    per result, `json_set` in place -- binds that payload as a string here. So this counter
    is about the amount of JSON the design forces through SQLite and not about which
    statements a particular version happens to run.
    """

    def __init__(self, inner: sqlite3.Connection, cost: _RunCost) -> None:
        self._inner = inner
        self._cost = cost

    def _charge(self, parameters: Any) -> None:
        self._cost.statements += 1
        if isinstance(parameters, str | bytes):
            return
        for value in parameters or ():
            if isinstance(value, str):
                self._cost.payload_chars += len(value)

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        self._charge(parameters)
        return self._inner.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> sqlite3.Cursor:
        rows = list(parameters)
        for row in rows:
            self._charge(row)
        return self._inner.executemany(sql, rows)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        self._cost.statements += 1
        return self._inner.executescript(sql)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __enter__(self) -> sqlite3.Connection:
        self._inner.__enter__()
        return self  # type: ignore[return-value]

    def __exit__(self, *exc: Any) -> Any:
        return self._inner.__exit__(*exc)


class _CountingStore:
    """Wraps the real store, counting what the engine asks of it."""

    def __init__(self, inner: SQLiteRecoveryStore, cost: _RunCost) -> None:
        self._inner = inner
        self._cost = cost

    def get(self, run_id: str) -> RunRecoveryState | None:
        return self._inner.get(run_id)

    def save(self, state: RunRecoveryState) -> None:
        self._cost.saves += 1
        self._cost.results_handed_to_the_store += len(state.completed_results)
        self._inner.save(state)

    def append_result(
        self,
        run_id: str,
        *,
        position: int,
        result: AgentResult,
        updated_at: datetime,
    ) -> None:
        self._cost.appends += 1
        self._cost.results_handed_to_the_store += 1
        self._inner.append_result(run_id, position=position, result=result, updated_at=updated_at)


@pytest.fixture
def measured_run(
    tmp_path: Path,
    frozen_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[int], _RunCost]:
    """Drive one `N`-agent recovery run and return what it cost."""

    def _run(agents: int) -> _RunCost:
        cost = _RunCost(agents=agents)
        path = tmp_path / f"state-{agents}.sqlite3"
        real_connect = recovery_module.open_state_connection

        def counting_connect(target: Path) -> Any:
            return _CountingConnection(real_connect(target), cost)

        real_validate = RunRecoveryState.model_validate

        def counting_validate(payload: Any, *args: Any, **kwargs: Any) -> RunRecoveryState:
            if isinstance(payload, dict):
                cost.revalidated_results += len(payload.get("completed_results") or ())
            return real_validate(payload, *args, **kwargs)

        store = SQLiteRecoveryStore(path)
        monkeypatch.setattr(recovery_module, "open_state_connection", counting_connect)
        monkeypatch.setattr(RunRecoveryState, "model_validate", counting_validate)

        selected = tuple(_CountingAgent(f"agent-{index:04d}") for index in range(agents))
        engine = ResearchEngine.__new__(ResearchEngine)
        engine.clock = lambda: frozen_now
        engine.recovery_store = _CountingStore(store, cost)  # type: ignore[assignment]
        context = AgentContext(
            run_id="run-amplification",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(),
        )
        seed = RunRecoveryState(
            run_id="run-amplification",
            request_digest="a" * 64,
            graph_signature="b" * 64,
            agent_ids=tuple(agent.agent_id for agent in selected),
            completed_results=(),
            next_agent_index=0,
            started_at=frozen_now,
            updated_at=frozen_now,
        )
        engine.recovery_store.save(seed)

        results = engine._run_agents_with_recovery(
            context=context, selected=selected, recovery=seed
        )
        assert len(results) == agents

        monkeypatch.undo()
        stored = SQLiteRecoveryStore(path).get("run-amplification")
        assert stored is not None
        cost.stored_result_ids = tuple(item.agent_id for item in stored.completed_results)
        return cost

    return _run


@pytest.fixture
def measured_pair(measured_run: Callable[[int], _RunCost]) -> Iterator[tuple[_RunCost, _RunCost]]:
    yield measured_run(SMALL), measured_run(LARGE)


def test_the_engine_does_not_revalidate_the_whole_result_set_once_per_agent(
    measured_pair: tuple[_RunCost, _RunCost],
) -> None:
    """The engine half: `_updated_recovery`'s dump-and-revalidate round trip.

    Before the fix this is `N(N+1)/2` -- 1,830 at `N=60` and 7,260 at `N=120`, a ratio of
    3.97. It has to become a cost that does not grow with the results already completed,
    because re-deriving a validated `RunRecoveryState` from a `model_dump` of itself is work
    proportional to everything the run has done so far, repeated once for every step it
    takes.
    """
    small, large = measured_pair

    ratio = large.revalidated_results / max(small.revalidated_results, 1)
    assert ratio < GROWTH_CEILING, (
        f"revalidating {small.revalidated_results} results at N={SMALL} and "
        f"{large.revalidated_results} at N={LARGE} is a factor of {ratio:.2f} for twice the "
        "work: the engine is re-validating the accumulated result set once per agent"
    )
    assert large.revalidated_results <= 2 * LARGE, (
        f"{large.revalidated_results} revalidated results for {LARGE} agents is more than "
        "two per agent, so something on the per-agent path still round-trips the whole state"
    )


def test_the_store_does_not_rewrite_every_completed_result_on_every_save(
    measured_pair: tuple[_RunCost, _RunCost],
) -> None:
    """The store half: the JSON that actually reaches SQLite.

    Before the fix the store dumps the whole `RunRecoveryState` on every `save()`, so the
    characters written grow with the square of the agent count -- 11.74 MB for 200 agents,
    46.68 MB for 400. Persisting `N` results should cost writing them about once.
    """
    small, large = measured_pair

    ratio = large.payload_chars / max(small.payload_chars, 1)
    assert ratio < GROWTH_CEILING, (
        f"writing {small.payload_chars} characters at N={SMALL} and {large.payload_chars} at "
        f"N={LARGE} is a factor of {ratio:.2f} for twice the work: every save is still "
        "serialising every result completed so far"
    )


def test_the_engine_hands_the_store_each_result_exactly_once(
    measured_pair: tuple[_RunCost, _RunCost],
) -> None:
    """The write path's shape, stated as exact integers rather than as a growth rate.

    **This test is here because it falsified the note it replaced.** That note argued that
    counting what the engine hands the store would prove nothing, since `save()` takes a whole
    `RunRecoveryState` and would be handed the accumulated set under any fix that kept the
    Protocol's shape. The fix did not keep it: `V2-P4-020` added `append_result`, which takes
    one result, so the count separates the two answers after all and the ratio tests above are
    not the only instrument. The note was an argument where a measurement was available.

    Measured at `be262ea`, before the fix: `N + 1` saves and `N(N+1)/2` results handed over --
    1,830 at `N=60` and 7,260 at `N=120`. Now the happy path makes **no** `save()` call at all
    (the only one is the seed this file's own fixture writes) and exactly one `append_result`
    per agent.
    """
    small, large = measured_pair

    for cost in (small, large):
        assert cost.saves == 1, (
            f"{cost.saves} saves for {cost.agents} agents: the per-agent path is writing whole "
            "states again, and a whole state carries every result completed so far"
        )
        assert cost.appends == cost.agents
        assert cost.results_handed_to_the_store == cost.agents


def test_making_the_write_cheaper_did_not_make_it_lose_a_result(
    measured_pair: tuple[_RunCost, _RunCost],
) -> None:
    """The correctness half, without which every assertion above is satisfied by a store that
    writes nothing.

    Read back through a **fresh** `SQLiteRecoveryStore` on the same file, so the answer comes
    off disk rather than out of whatever the writing instance still holds, and compared
    against the full graph in order -- `V2-P4-019`'s audit found that a split which loses one
    row or reorders two still reads back as a valid document reporting the wrong work done.
    """
    for cost in measured_pair:
        assert cost.stored_result_ids == tuple(f"agent-{index:04d}" for index in range(cost.agents))
