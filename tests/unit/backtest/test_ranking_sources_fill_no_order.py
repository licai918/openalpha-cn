"""`V2-P4-053`/`054`/`055`. Neither ranking source makes or fills an order -- and what still gets
past that.

`ranking-creates-no-portfolio-order` forbids `PortfolioOrder`, the `PortfolioSimulator` and the
multi-day runner to `candidate_ranking.py` and `shortlist_gate.py`, and four `lint-imports`
contracts carry it. The half no contract can carry is the **single-security** one:
`backtest/execution.py` declares `ExecutionRequest` -- "A simplified cash-equity order intent" --
and `AShareExecutionPolicy.execute` simulates a fill, and `cross_section.py:227` imports both for
`V2-P4-004`'s tradeability filter. Forbidding that module measures 7 kept / 1 broken, so it
cannot be forbidden, and `cross_section` re-exports all three names besides -- as does
`backtest/__init__.py` -- so a source can reach the fill policy through a module already on its
allowlist and **add no edge to the import graph at all**. This file is what stands in for the
contract there.

## The claim, which is exactly what is measured below and no wider

Within one Python process, with the hooks in `_watched` installed:

1. Neither source was **running, on any thread**, at the moment an `ExecutionRequest` was
   constructed or an order filled, across a real screen -> ranking -> gate run
   (`test_no_order_..._had_either_source_running`, two axes).
2. Neither source made or filled an order **while its own module body ran**
   (`test_neither_source_makes_or_fills_an_order_while_its_own_module_body_runs`).
3. Neither source **binds, names or resolves to** any object `backtest/execution.py` defines,
   private names included (layers 2-4).

Not claimed: that no code in either file can be made to fill an order. Two shapes measured below
do exactly that and are not caught; they are in the table.

## The three defects this replaces, each reproduced before it was closed

- **`053`, the dispatch hop.** The previous version walked `sys._getframe()` -- one thread. A
  probe using a runtime-assembled name and `pool.submit(policy.execute, intent, bar).result()`
  filled `filled buy 100 10.20 5.01` from `candidate_ranking.py` with `6 passed`, lint-imports at
  8 kept / 0 broken, `tests/unit` at 2373 passed. The isolating control -- same names, same fill,
  called synchronously -- went red and named the file, so the defeated thing was the walk. The
  walk is now `sys._current_frames()`: the submitter's frame is alive, blocked in `.result()`, on
  the thread that submitted. `test_the_witness_sees_a_submitter_that_is_blocked_on_another_thread`
  computes both walks on that shape every run, so the difference stays a measurement.
- **`053`, import time.** The same fill written at module level of `candidate_ranking.py` was
  recorded nowhere: pytest imports the module at collection, long before any hook exists. `6
  passed`, 8 kept / 0 broken. Each source file is now executed again from its own path under a
  throwaway module name **with the hooks already installed**, which needs no attribution at all --
  everything inside that window belongs to the file whose body is running.
- **`054`, the visibility rule.** `_order_machinery` kept `not name.startswith("_")` and layer 1
  wrapped `AShareExecutionPolicy.execute` alone, so a `class _SweepExecutionPolicy(
  AShareExecutionPolicy)` overriding `execute`, re-exported through `cross_section` and imported
  at column zero, was in no ban set and under no wrapper: `filled sell 200 10.20 5.00`, everything
  green. The ban set now has **no visibility rule** -- measured as adding `_price_band` and
  `_board_limit`, which neither source touches -- and the wrapper covers every class carrying an
  `execute`, transitively. Both halves are pinned by their own tests.
- **`055`, the vacuous sentinel.** `fills.append(...)` ran before `original(...)` and never read
  `result.status`, so it counted **calls**. Flipping `_bar()` to `suspended=True` gave 12 calls,
  0 fills, and the guard passed. The sentinel is now `status == "filled"`; flipping the same
  fixture makes it red, and `test_the_sentinel_separates_a_filled_order_from_a_refused_one` runs
  that mutation on every pass.

## Two axes, because they fail to different things

`_watched` hooks `ExecutionRequest.__init__` -- **who made the order** -- and `execute` on every
policy class -- **who was running when it filled, and whether it filled**. The first is captured
where the intent is constructed, so it survives whatever the intent is handed to afterwards, and
it is the axis a policy subclass cannot dodge, since an override that never delegates still needs
an intent. The second is what catches an intent built elsewhere and filled from here.

## The nine falsifications, run against this guard

Each patched the real tree and ran this file. `filled` below is the probe's own printed status.

| falsification                                    | outcome                                     |
| ------------------------------------------------ | ------------------------------------------ |
| a bare `threading.Thread`, joined                 | caught -- fill axis + intent axis          |
| a `ThreadPoolExecutor`, the `053` probe           | caught -- fill axis + intent axis          |
| a fill at import time, the `053` probe            | caught -- import-time window               |
| a `functools.partial` bound here, called there    | **not caught by either axis** -- see below |
| a generator consumed by another module            | caught -- its frame carries this file      |
| a callback registered here, invoked there         | caught -- its frame carries this file      |
| a private subclass overriding `execute`           | caught -- five layers                      |
| a fill through `__init_subclass__`                | caught -- import-time window + identity    |
| a `subprocess`                                    | **not caught** -- `12 passed`              |

**The `subprocess` is another interpreter.** Its fill printed `filled` and this file reported
`12 passed`. Nothing in-process can see it, and nothing here pretends to.

**The `partial` is the sharper one.** A `functools.partial` has no code object of its own, so a
partial over a helper declared in `execution.py`, registered at import time into a list an
unguarded module iterates, puts no guarded frame on any stack at fill time and constructs no
intent in either the guarded file's frames or its module body. Neither axis sees it. It went red
here only incidentally -- the injected fill also fires on the suspended-bar fixture, so the `055`
mutation test noticed a fill that should not have happened -- and an attacker who respected the
bar would have been green. Two things bound it rather than excuse it: the callable must carry no
code object of the guarded file (a `lambda` or a nested `def` does, and both are caught above),
and it needs an accomplice, because no module in `backtest/` has a callback registry today --
arranging this one meant adding `_SWEEP_HOOKS` to `cross_section.py`.

Smaller residuals, stated: `model_construct`, `model_validate` and `model_copy` bypass
`__init__`, so the intent axis sees 1 of those 4 construction paths and such an intent is caught
on the fill axis instead; `__new__` is the hook all four share and is **not** used, because it
cannot be removed again -- `del ExecutionRequest.__new__` leaves the class raising
`TypeError: object.__new__() takes exactly one argument` for the rest of the session, measured.
Discovery keeps only objects carrying `__module__`, so a module-level constant like `_CENT` is
not in the ban set. A name assembled at runtime
(`getattr(cross_section, "AShare" + "ExecutionPolicy")`) is still invisible to layers 2-4 by
construction; the behavioural layers see it, which is how the `053` probes were caught.

## What was rejected, and why

- **Ending `cross_section`'s re-export.** It would not remove the class of bypass it targets:
  `backtest/__init__.py` re-exports `AShareExecutionPolicy`, `ExecutionRequest`, `MarketBar` and
  `ExecutionResult` too, so `from openalpha_cn.backtest import ExecutionRequest` is a second route
  that likewise leaves `direct_import_exists(candidate_ranking, backtest.execution)` false, since
  `grimp` resolves `from X import name` to `X` when `name` is not a submodule. That one has to be
  written function-local rather than at column zero -- the package `__init__` imports both sources
  (lines 15 and 41) before it imports `execution` (line 26), so at module level the name is not
  bound yet -- but layers 3 and 4 are what refuse it either way, not the namespace. And
  `sys.modules` is a third door no namespace change can shut. The cost is real besides:
  `ExecutionResult` is a field type on `cross_section`'s own pydantic models, so private aliases
  would have to appear in annotations pydantic resolves.
- **A production audit field or origin census on `ExecutionRequest`.** It buys nothing the
  import-time window does not already give -- that window is bounded rather than attributed, so it
  is strictly stronger for import time -- and it would put `sys._getframe` on the path of every
  order intent for a property only a test reads, on a frozen model whose `__new__` cannot be
  restored.
- **`sys.setprofile` plus `threading.setprofile`.** Measured against the table above it catches
  nothing the all-thread walk misses, because both attribute through frames: the `partial` case
  has no guarded frame for either to find, and the `subprocess` case is not in this interpreter.
  A heavier mechanism that closes neither of the two open shapes is not worth its failure modes.
"""

from __future__ import annotations

import dis
import importlib
import importlib.util
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import CodeType, FrameType, ModuleType
from typing import Any, Final

from openalpha_cn.backtest import execution as execution_module
from openalpha_cn.backtest.candidate_ranking import (
    build_ranking_manifest,
    rank_candidates,
)
from openalpha_cn.backtest.cross_section import (
    ComponentCrossSection,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
)
from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
)
from openalpha_cn.backtest.shortlist_gate import (
    ShortlistClearance,
    ShortlistGateSpec,
    gate_shortlist,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SOURCE_PATHS: Final[dict[str, Path]] = {
    "openalpha_cn.backtest.candidate_ranking": (
        ROOT / "src" / "openalpha_cn" / "backtest" / "candidate_ranking.py"
    ),
    "openalpha_cn.backtest.shortlist_gate": (
        ROOT / "src" / "openalpha_cn" / "backtest" / "shortlist_gate.py"
    ),
}
"""The two `source_modules` of `ranking-creates-no-portfolio-order`, by name and by file.

Held against the contract itself in `test_the_two_files_guarded_here_are_the_contracts_own
_source_modules`, so widening the contract's source list without widening this one is red.
"""

GUARDED: Final[frozenset[Path]] = frozenset(SOURCE_PATHS.values())

MACHINERY_MODULE: Final[str] = "openalpha_cn.backtest.execution"


# --------------------------------------------------------------------------------------------
# What counts as the order machinery -- discovered, and with no visibility rule (`V2-P4-054`)
# --------------------------------------------------------------------------------------------


def _machinery_names(namespace: Mapping[str, object]) -> dict[str, object]:
    """Every object in `namespace` that `backtest/execution.py` itself **defines**.

    Two filters and deliberately not three. `__module__` is what keeps the names that module
    merely imports out -- `ExecutionResult` lives in `domain/execution.py` and is a plain result
    record both ranking sources legitimately read. There is **no filter on the leading
    underscore**, which is what `V2-P4-054` measured as a hole: the previous version kept only
    `not name.startswith("_")`, so a `class _SweepExecutionPolicy(AShareExecutionPolicy)`
    overriding `execute`, re-exported through `cross_section` and imported at column zero, was
    absent from every ban set below and filled `filled sell 200 10.20 5.00` with the whole gate
    green.

    Taking a `Mapping` rather than reading the module directly is what lets
    `test_the_ban_set_has_no_visibility_rule_in_it` drive a private class through this function
    on every run, instead of trusting that the filter that is not there stays not there.
    """
    return {
        name: obj
        for name, obj in namespace.items()
        if getattr(obj, "__module__", None) == MACHINERY_MODULE
    }


def _order_machinery() -> dict[str, object]:
    """`_machinery_names` over the real module, with the sanity check that it found something."""
    found = _machinery_names(vars(execution_module))
    assert {"AShareExecutionPolicy", "ExecutionRequest", "MarketBar"} <= set(found), (
        f"{MACHINERY_MODULE} no longer defines the policy, the order intent and the bar that "
        f"together make a fill; found {sorted(found)}. If they moved, this guard is pointing at "
        "an empty module and every assertion below would pass on nothing"
    )
    return found


def _policy_classes() -> tuple[type, ...]:
    """Every class that can turn an order intent into a fill, private and inherited alike.

    The machinery module's own classes that carry an `execute`, plus every transitive subclass of
    them wherever it is declared, keeping only those that define `execute` in their **own**
    `__dict__` -- an inheritor that does not override it is already covered by the base.

    This is the set `_watched` wraps, and it exists because wrapping `AShareExecutionPolicy
    .execute` alone is what `V2-P4-054` defeated: a subclass's override shadows the base
    attribute, so the wrapper on the base is never reached and the fill is unobserved.
    """
    roots = [
        obj
        for obj in _order_machinery().values()
        if isinstance(obj, type) and callable(getattr(obj, "execute", None))
    ]
    reached: dict[int, type] = {}
    queue: list[type] = list(roots)
    while queue:
        cls = queue.pop()
        if id(cls) in reached:
            continue
        reached[id(cls)] = cls
        queue.extend(cls.__subclasses__())
    return tuple(cls for cls in reached.values() if "execute" in vars(cls))


# --------------------------------------------------------------------------------------------
# The ledger: who was running when an order intent was made, and when one filled
# --------------------------------------------------------------------------------------------


def _live_files() -> frozenset[str]:
    """Every file with a live frame on **any** thread, right now.

    `sys._current_frames()` rather than `sys._getframe()`, which is the whole of `V2-P4-053`'s
    first half: `pool.submit(policy.execute, intent, bar).result()` runs the fill on a worker
    thread whose stack is `ThreadPoolExecutor` internals all the way down, so the submitting file
    is on no frame the fill can walk backwards to -- while its frame is still very much alive,
    blocked in `.result()`, on the thread that submitted. One dispatch hop defeated the
    single-thread walk; it does not defeat this one, and
    `test_the_witness_sees_a_submitter_that_is_blocked_on_another_thread` drives exactly that
    shape through both to keep the difference measured rather than asserted.
    """
    found: set[str] = set()
    for frame in sys._current_frames().values():
        cursor: FrameType | None = frame
        while cursor is not None:
            found.add(cursor.f_code.co_filename)
            cursor = cursor.f_back
    return frozenset(found)


def _guarded_among(running: frozenset[str]) -> set[Path]:
    return {
        resolved for resolved in (Path(name).resolve() for name in running) if resolved in GUARDED
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class _OrderEvent:
    """One order intent coming into existence, or one order being decided by a policy."""

    kind: str
    status: str | None
    running: frozenset[str]

    @property
    def culprits(self) -> set[Path]:
        return _guarded_among(self.running)


@dataclass(slots=True)
class _Ledger:
    events: list[_OrderEvent] = field(default_factory=list)

    @property
    def intents(self) -> list[_OrderEvent]:
        return [event for event in self.events if event.kind == "intent"]

    @property
    def decisions(self) -> list[_OrderEvent]:
        return [event for event in self.events if event.kind == "decision"]

    @property
    def fills(self) -> list[_OrderEvent]:
        """Only the decisions that actually **filled** (`V2-P4-055`).

        `decisions` counts calls. A call that returned `rejected` bought this guard nothing, and
        a sentinel written on `decisions` is green on a fixture where every bar is suspended --
        measured at 12 calls, 0 fills, with the whole file passing.
        """
        return [event for event in self.decisions if event.status == "filled"]


@contextmanager
def _watched() -> Iterator[_Ledger]:
    """Record every order intent made and every order decided, for the length of a block.

    Two hooks on two different objects, because they fail to different things:

    - `ExecutionRequest.__init__` -- **who made the order**. Captured where the intent is
      constructed, so it is indifferent to which thread, executor, partial or callback later
      turns it into a fill. Direct construction only; `model_construct`, `model_validate` and
      `model_copy` all bypass `__init__` and are not seen here (measured: 1 of those 4 paths).
      `__new__` is the one hook all four pass through and it is **not** used: it cannot be
      removed again afterwards -- `del ExecutionRequest.__new__` leaves the class with
      `slot_tp_new` and every later `ExecutionRequest(...)` in the session raises
      `TypeError: object.__new__() takes exactly one argument`, measured. A guard that corrupts a
      shared pydantic class for the rest of the suite is not a guard.
    - `execute` on **every** policy class -- **who was running when it filled**, plus the status
      that says whether it filled at all.
    """
    ledger = _Ledger()
    originals: dict[type, Any] = {cls: vars(cls)["execute"] for cls in _policy_classes()}
    had_own_init = "__init__" in vars(ExecutionRequest)
    original_init = ExecutionRequest.__init__

    def recording_execute(original: Callable[..., Any]) -> Callable[..., Any]:
        def recorded(policy: Any, request: Any, market: Any) -> Any:
            running = _live_files()
            result = original(policy, request, market)
            ledger.events.append(
                _OrderEvent(
                    kind="decision",
                    status=getattr(result, "status", None),
                    running=running,
                )
            )
            return result

        return recorded

    def recorded_init(intent: Any, **data: Any) -> None:
        ledger.events.append(_OrderEvent(kind="intent", status=None, running=_live_files()))
        original_init(intent, **data)

    for cls, original in originals.items():
        setattr(cls, "execute", recording_execute(original))  # noqa: B010
    setattr(ExecutionRequest, "__init__", recorded_init)  # noqa: B010
    try:
        yield ledger
    finally:
        if had_own_init:
            setattr(ExecutionRequest, "__init__", original_init)  # noqa: B010
        else:
            delattr(ExecutionRequest, "__init__")
        for cls, original in originals.items():
            setattr(cls, "execute", original)  # noqa: B010


# --------------------------------------------------------------------------------------------
# A real screen, a real policy, a real ranking, a real gate -- fills actually happen below
# --------------------------------------------------------------------------------------------

AS_OF: Final[datetime] = datetime(2026, 6, 12, 4, 0, tzinfo=UTC)
SESSION: Final[date] = date(2026, 6, 12)
BUILT_AT: Final[datetime] = datetime(2026, 6, 13, 9, 0, tzinfo=UTC)
CAPITAL: Final[Decimal] = Decimal("100000")
HORIZON: Final[str] = "5d"
UNIVERSE: Final[tuple[str, ...]] = tuple(f"{index:06d}.SZ" for index in range(1, 13))

ALPHA: Final[FactorDefinition] = FactorDefinition(
    key="probe_alpha",
    version=1,
    family="momentum_reversal",
    direction="higher_is_better",
    required_fields=(FactorField(dataset="daily", column="close"),),
    lookback_sessions=1,
    max_window_sessions=1,
    lookback_periods=None,
    max_window_periods=None,
)


def _bar(subject: str, *, suspended: bool = False) -> MarketBar:
    price = Decimal("10.00")
    return MarketBar(
        subject=subject,
        trade_date=SESSION,
        board="main",
        previous_close=price,
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=suspended,
        is_st=False,
        up_limit=Decimal("11.0"),
        down_limit=Decimal("9.0"),
    )


def _signal(subject: str) -> SignalFrame:
    return SignalFrame(
        subject=subject,
        as_of=AS_OF,
        direction="bullish",
        strength=0.4,
        confidence=0.7,
        horizon=HORIZON,
        evidence_ids=("evd_000000000000000000000001",),
    )


def _run_manifest_id(subject: str) -> str:
    return RunManifest(
        run_id=f"run-{subject}",
        mode="backtest",
        as_of=AS_OF,
        code_commit="a1b2c3d",
        config_digest="c" * 64,
        random_seed=7,
        started_at=AS_OF,
        finished_at=BUILT_AT,
        status="succeeded",
    ).run_manifest_id


def _clearance(*, suspended: bool = False) -> ShortlistClearance:
    """One real screen -> ranking -> gate run. Every buy below goes through the real policy.

    `suspended` is not decoration and is not a variant anybody ships: it is the fixture mutation
    `test_the_sentinel_separates_a_filled_order_from_a_refused_one` needs, and it is a parameter
    rather than an edit so that the mutation runs on every pass.
    """
    spec = ShortlistSpec(
        components=(ScoreComponent(definition=ALPHA, weight=1.0),),
        tier="processed",
        shortlist_size=3,
        position_capital=CAPITAL,
    )
    funnel = CrossSectionScreen(spec, execution=AShareExecutionPolicy()).select(
        as_of=AS_OF,
        universe=UNIVERSE,
        components=[
            ComponentCrossSection(
                factor_id=ALPHA.factor_id,
                values=tuple(
                    (subject, 12.0 - index, "processed") for index, subject in enumerate(UNIVERSE)
                ),
                clipped_subjects=frozenset(),
            )
        ],
        bars={subject: _bar(subject, suspended=suspended) for subject in UNIVERSE},
    )
    chosen = tuple(entry.subject for entry in funnel.shortlist)
    ranking = rank_candidates(
        manifest=build_ranking_manifest(
            as_of=AS_OF,
            horizon=HORIZON,
            universe=list(UNIVERSE),
            scoring_policy=spec,
            code_commit="a1b2c3d",
            config_digest="c" * 64,
            built_at=BUILT_AT,
        ),
        funnel=funnel,
        signals={subject: _signal(subject) for subject in chosen},
        run_manifest_ids={subject: _run_manifest_id(subject) for subject in chosen},
        exposures=None,
        predictions={},
    )
    return gate_shortlist(
        ranking=ranking,
        spec=ShortlistGateSpec(
            minimum_tradable_ratio=0.0,
            minimum_researched_ratio=0.0,
            maximum_ranking_age_days=3_650,
        ),
    )


# --------------------------------------------------------------------------------------------
# Layer 1 -- behavioural, on two axes. No name is inspected.
# --------------------------------------------------------------------------------------------


def test_no_order_filled_during_a_real_ranking_and_gate_run_had_either_source_running() -> None:
    """Who was executing, on any thread, at the moment an order actually filled.

    Three things separate this from what `V2-P4-053` defeated: the walk is over
    `sys._current_frames()` rather than one thread's stack, the wrapper is on every policy class
    rather than on `AShareExecutionPolicy` alone, and the sentinel counts `status == "filled"`
    rather than counting calls.
    """
    with _watched() as ledger:
        _clearance()

    assert ledger.fills, (
        "sentinel: no order **filled** on a real screen -> ranking -> gate run, so this test "
        "cannot tell a source that fills orders from one that does not. Note that a count of "
        "calls would not have caught this: with every bar suspended the same fixture makes 12 "
        "calls and 0 fills, which is V2-P4-055's defect exactly"
    )

    culprits = sorted(str(path) for fill in ledger.fills for path in fill.culprits)
    assert not culprits, (
        f"{culprits} was running when an order filled. Neither ranking source may fill one: "
        "`ranking-creates-no-portfolio-order` cannot forbid openalpha_cn.backtest.execution -- "
        "cross_section.py needs the policy for V2-P4-004's tradeability filter, measured at "
        "7 kept / 1 broken when forbidden -- so this is one of the two assertions that carry the "
        "claim, and it does not care how the import was spelled or which thread ran the fill"
    )


def test_no_order_intent_made_during_a_real_ranking_and_gate_run_had_either_source_running() -> (
    None
):
    """Who **made** the order, which is a different question from who was running when it filled.

    Captured at `ExecutionRequest.__init__`, so it holds wherever the intent goes **afterwards**:
    a thread, a pool, a `functools.partial` handed to another module, a generator resumed
    elsewhere, a callback another module invokes. Those are the shapes where the fill-side
    assertion above can lose the trail, because by the time the fill happens the source's frame
    may have returned -- and this one never had a trail to lose, because it asked at
    construction.

    What it does **not** reach is an intent constructed somewhere else on the source's behalf --
    a helper in `execution.py` that builds its own. There the fill-side assertion is what
    remains, and the module docstring's `partial` row is the measured case where both are silent.

    It is also the axis a policy subclass cannot dodge. An override of `execute` that never
    delegates is invisible to a wrapper on `execute`; it is not invisible here, because it still
    needs an `ExecutionRequest` to have an order at all.
    """
    with _watched() as ledger:
        _clearance()

    assert ledger.intents, (
        "sentinel: no order intent was constructed at all on a real screen -> ranking -> gate "
        "run, so this assertion is being made about an empty list. Either the hook is not on "
        "ExecutionRequest or the fixture stopped exercising V2-P4-004's tradeability filter"
    )

    culprits = sorted(str(path) for intent in ledger.intents for path in intent.culprits)
    assert not culprits, (
        f"{culprits} was running when an ExecutionRequest -- 'a simplified cash-equity order "
        "intent' -- was constructed. D16's 绝不直接创建组合订单 is about creating orders, and "
        "creating one is exactly this: a source that never constructs an intent cannot fill an "
        "order however it dispatches the fill"
    )


def test_neither_source_makes_or_fills_an_order_while_its_own_module_body_runs() -> None:
    """`V2-P4-053`'s second half: import-time code is code, and it runs in production.

    The two assertions above install their hooks and then call the pipeline -- but by then both
    source modules were imported long ago, at collection, so a fill written at **module level**
    happens before any hook exists and is recorded nowhere. A probe that did exactly that filled
    `filled buy 100 10.20 5.01` at import of `candidate_ranking.py` with all six tests passing.

    So each source file is executed again, from its own path, under a throwaway module name, with
    the hooks already installed. That makes this the one assertion here that needs no attribution
    at all: everything recorded inside the window belongs to the file whose body is running, so
    no thread, executor or dispatch hop can point the finger somewhere else.
    """
    for module_name, path in SOURCE_PATHS.items():
        probe_name = f"_import_time_probe_{path.stem}"
        spec = importlib.util.spec_from_file_location(probe_name, path)
        assert spec is not None and spec.loader is not None, (
            f"{path} cannot be loaded from its own path, so the import-time window below would "
            "not run and this assertion would pass on nothing"
        )
        module = importlib.util.module_from_spec(spec)
        with _watched() as ledger:
            sys.modules[probe_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(probe_name, None)

        # Sentinel. `ledger.events` is empty both when the body made no order and when the body
        # never ran, and those are the two answers this assertion exists to separate -- so the
        # re-execution is required to have produced the same public surface as the real module
        # before its silence is allowed to mean anything.
        real = importlib.import_module(module_name)
        assert getattr(module, "__all__", None) == real.__all__, (
            f"re-executing {path} did not reproduce {module_name}'s __all__, so its module body "
            "did not run to completion and the emptiness asserted below would mean 'nothing "
            "happened here' when it means 'nothing happened at all'"
        )

        assert not ledger.events, (
            f"{module_name} made {len(ledger.intents)} order intent(s) and decided "
            f"{len(ledger.decisions)} order(s) while its own module body was running. Import-time "
            "code runs in production on every import, so a fill there is a fill; and it is "
            "invisible to any hook installed by a test, because the module was already imported "
            "at collection when the test began"
        )


# --------------------------------------------------------------------------------------------
# Layer 2 -- identity. Objects, not names, so an alias or a re-export is not a hiding place.
# --------------------------------------------------------------------------------------------


def test_neither_source_holds_an_order_machinery_object_in_its_namespace() -> None:
    """The re-export form, which adds no `grimp` edge, fails here and can fail nowhere else.

    Compared by identity against the objects `backtest/execution.py` defines, so it makes no
    difference whether the source wrote `from openalpha_cn.backtest.execution import
    AShareExecutionPolicy`, `from openalpha_cn.backtest.cross_section import
    AShareExecutionPolicy` (which adds no edge at all, because `cross_section` is already on the
    allowlist), `from openalpha_cn.backtest import ExecutionRequest` (the package's own
    `__init__` re-exports the same three names), or any of those with `as _alias`.
    """
    machinery = _order_machinery()
    banned = {id(obj): name for name, obj in machinery.items()}
    banned[id(execution_module)] = MACHINERY_MODULE

    for module_name in SOURCE_PATHS:
        module = importlib.import_module(module_name)
        held = {
            f"{bound} -> {banned[id(obj)]}"
            for bound, obj in vars(module).items()
            if id(obj) in banned
        }
        assert not held, (
            f"{module_name} binds the order machinery in its own namespace: {sorted(held)}. "
            f"Nothing in a candidate ranking needs to hold {MACHINERY_MODULE}'s fill policy, "
            "its order intent or its bar -- reaching the tradeability verdict through "
            "cross_section's own census is the supported route, and it is already there. Note "
            "that importing these through cross_section adds no import-graph edge, so no "
            "lint-imports contract can refuse it and this assertion is the only structural one "
            "that can"
        )


# --------------------------------------------------------------------------------------------
# Layer 3 -- names, in every code object. Indentation is not a hiding place.
# --------------------------------------------------------------------------------------------


def _code_objects(code: CodeType) -> list[CodeType]:
    """`code` and every code object nested in it -- functions, classes, comprehensions, lambdas.

    Compiled from source rather than walked off the imported module, so a function-local import
    inside a method of a nested class is reached exactly like a module-level one. `str.startswith`
    on source lines, which is what `V2-P4-035` shipped, sees only the last of those.
    """
    found = [code]
    for const in code.co_consts:
        if isinstance(const, CodeType):
            found.extend(_code_objects(const))
    return found


def test_no_code_object_in_either_source_names_the_order_machinery() -> None:
    """The indented function-local import fails here, at any nesting depth.

    Every name each code object touches, which is `co_names` plus the free and local variables an
    `import` inside a function writes to. The pin this replaces filtered source lines with
    `line.startswith(("import ", "from "))`, so one level of indentation defeated it while
    leaving a real `grimp` edge behind.
    """
    machinery = _order_machinery()
    banned = set(machinery) | {MACHINERY_MODULE}

    for module_name, path in SOURCE_PATHS.items():
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        offenders: set[str] = set()
        for block in _code_objects(code):
            touched = set(block.co_names) | set(block.co_varnames) | set(block.co_freevars)
            offenders |= touched & banned
            for instruction in dis.get_instructions(block):
                if instruction.opname == "IMPORT_NAME" and instruction.argval in banned:
                    offenders.add(str(instruction.argval))

        assert not offenders, (
            f"{module_name} names {sorted(offenders)}, which {MACHINERY_MODULE} defines. A "
            "ranking source that can reach the fill policy can fill an order, and no "
            "lint-imports contract will stop it -- openalpha_cn.backtest.execution cannot be "
            "forbidden, because cross_section.py needs it for V2-P4-004's tradeability filter. "
            "This assertion reads every code object in the file, so an import indented inside a "
            "function is caught exactly like one at column zero, and the ban set carries the "
            "module's private names too"
        )


def test_every_import_in_either_source_resolves_away_from_the_order_machinery() -> None:
    """Each `from X import a, b` in each file, resolved to the objects it actually binds.

    The layer above reads names; this one follows them. `from openalpha_cn.backtest.cross_section
    import AShareExecutionPolicy` names `cross_section`, which is legitimate and on every
    allowlist -- what makes it a bypass is the object it hands back, and only resolution shows
    that.
    """
    machinery = _order_machinery()
    banned = {id(obj) for obj in machinery.values()} | {id(execution_module)}

    for module_name, path in SOURCE_PATHS.items():
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        reached: set[str] = set()
        for block in _code_objects(code):
            pending: ModuleType | None = None
            for instruction in dis.get_instructions(block):
                if instruction.opname == "IMPORT_NAME":
                    pending = _import_or_none(str(instruction.argval))
                    if pending is not None and id(pending) in banned:
                        reached.add(str(instruction.argval))
                elif instruction.opname == "IMPORT_FROM" and pending is not None:
                    bound = getattr(pending, str(instruction.argval), None)
                    if bound is not None and id(bound) in banned:
                        reached.add(f"{pending.__name__}.{instruction.argval}")

        assert not reached, (
            f"{module_name} imports {sorted(reached)}, which resolve to objects "
            f"{MACHINERY_MODULE} defines. Re-exporting the fill policy through a module that is "
            "already on the allowlist is the bypass that adds no import-graph edge at all"
        )


def _import_or_none(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


# --------------------------------------------------------------------------------------------
# The guard's own scope, held to the contract rather than restated
# --------------------------------------------------------------------------------------------


def test_the_two_files_guarded_here_are_the_contracts_own_source_modules() -> None:
    """`SOURCE_PATHS` is the contract's `source_modules`, read off `pyproject.toml`.

    Without this, widening `ranking-creates-no-portfolio-order` to a third module would leave the
    new module unguarded and every assertion in this file still green -- which is the shape of
    the defect this file exists to close, one level up.
    """
    from importlinter import api as importlinter_api

    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contract = next(
        item
        for item in config["contracts_options"]
        if item.get("id") == "ranking-creates-no-portfolio-order"
    )

    assert set(contract["source_modules"]) == set(SOURCE_PATHS), (  # type: ignore[call-overload]
        f"ranking-creates-no-portfolio-order's source_modules are "
        f"{sorted(contract['source_modules'])} but this file guards {sorted(SOURCE_PATHS)}. "  # type: ignore[index]
        "Every source of that contract has to be guarded here too, because the contract itself "
        "cannot forbid the fill policy and this file is what stands in for it"
    )
    for module_name, path in SOURCE_PATHS.items():
        assert path.is_file(), f"{module_name} is guarded by path and {path} does not exist"
        assert importlib.import_module(module_name).__file__ == str(path), (
            f"{module_name} does not live at {path}, so the checks above guard nothing"
        )


# --------------------------------------------------------------------------------------------
# The failing branches, exercised. Each of these is one of the three defeats, kept as a test.
# --------------------------------------------------------------------------------------------


def test_a_function_local_import_of_the_fill_policy_is_caught() -> None:
    """The mutation that made the pin this file replaces go red, kept as a test.

    The indented function-local import written into a copy of the module rather than the module
    itself, so the assertion that catches it is exercised on every run instead of being trusted.
    A guard whose failing branch nothing ever takes is half of this repository's recurring defect.
    """
    probe = (
        "def fill():\n"
        "    from openalpha_cn.backtest.execution import AShareExecutionPolicy\n"
        "    return AShareExecutionPolicy\n"
    )
    code = compile(probe, "<probe>", "exec")
    banned = set(_order_machinery()) | {MACHINERY_MODULE}

    touched: set[str] = set()
    for block in _code_objects(code):
        touched |= (set(block.co_names) | set(block.co_varnames)) & banned

    assert "AShareExecutionPolicy" in touched, (
        "the bytecode walk cannot see a function-local import, which is the exact step "
        "V2-P4-035's probe took and the exact step its str.startswith pin could not see"
    )


def test_the_ban_set_has_no_visibility_rule_in_it() -> None:
    """`V2-P4-054`, as the mutation rather than as a promise.

    A private class whose `__module__` is the machinery module is driven through the real
    discovery function. The version this replaces filtered on `not name.startswith("_")` and
    dropped it, which is why `_SweepExecutionPolicy` was in no ban set and in no wrapper.
    """

    class _SweepExecutionPolicy:
        pass

    _SweepExecutionPolicy.__module__ = MACHINERY_MODULE
    found = _machinery_names({"_SweepExecutionPolicy": _SweepExecutionPolicy, "re": sys})

    assert "_SweepExecutionPolicy" in found, (
        "the discovery dropped a private class the machinery module defines, so an underscore is "
        "again a hiding place -- which is the whole of V2-P4-054"
    )
    assert "re" not in found, (
        "the discovery kept a name the machinery module merely imports, which would sweep in "
        "ExecutionResult and make both ranking sources red for reading a plain result record"
    )


def test_the_policy_sweep_wraps_a_private_subclass_that_overrides_execute() -> None:
    """The other half of `V2-P4-054`: the ban set is static, and this one is behavioural.

    A subclass's own `execute` shadows the base attribute, so a wrapper installed on
    `AShareExecutionPolicy.execute` alone never runs. The subclass below is declared here, not in
    the machinery module, which is the harder case: it is reached only through
    `__subclasses__`, and it is private, so both of `V2-P4-054`'s halves are exercised at once.
    """

    unpatched = vars(AShareExecutionPolicy)["execute"]

    class _SweepExecutionPolicy(AShareExecutionPolicy):
        def execute(self, request: ExecutionRequest, market: MarketBar) -> Any:
            # Refuses to delegate, exactly as the probe's did: it holds the function from before
            # any wrap, so a wrapper reached through `super()` would not see this fill either.
            return unpatched(self, request, market)

    assert _SweepExecutionPolicy in _policy_classes(), (
        "a private subclass overriding execute is outside the set of classes the witness wraps, "
        "so its fills are unobserved -- which is how V2-P4-054's probe filled "
        "'filled sell 200 10.20 5.00' with every assertion green"
    )
    assert AShareExecutionPolicy in _policy_classes(), (
        "the base policy dropped out of the wrapped set, so the ordinary path is unobserved"
    )

    with _watched() as ledger:
        outcome = _SweepExecutionPolicy().execute(
            ExecutionRequest(side="sell", quantity=200), _bar("000001.SZ")
        )

    assert outcome.status == "filled", (
        "the private subclass did not fill, so the observation below is not about a real fill"
    )
    assert len(ledger.fills) == 1, (
        f"the private subclass's own execute filled an order and the witness recorded "
        f"{len(ledger.fills)} fill(s). Wrapping AShareExecutionPolicy.execute alone leaves this "
        "at 0, because the override shadows the base attribute and never delegates to it"
    )


def test_the_witness_sees_a_submitter_that_is_blocked_on_another_thread() -> None:
    """`V2-P4-053`'s first half, as the mutation: one dispatch hop, measured both ways.

    The fill below is submitted to a worker thread by a function in **this** file and waited on.
    The single-thread walk the previous version used cannot see this file, because the worker's
    stack is `ThreadPoolExecutor` internals down to the bottom; the all-thread walk can, because
    the submitting frame is alive and blocked in `.result()`. Both are computed here, so the
    difference is a measurement on every run rather than a sentence in a docstring.
    """
    from concurrent.futures import ThreadPoolExecutor

    policy = AShareExecutionPolicy()
    bar = _bar("000001.SZ")
    intent = ExecutionRequest(side="buy", quantity=100)
    single: list[frozenset[str]] = []

    with _watched() as ledger:
        original = vars(AShareExecutionPolicy)["execute"]

        def also_one_thread(self: Any, request: Any, market: Any) -> Any:
            # Byte-for-byte the walk the version this replaces did: this frame's caller, then
            # backwards, on this thread only.
            found: set[str] = set()
            cursor: FrameType | None = sys._getframe().f_back
            while cursor is not None:
                found.add(cursor.f_code.co_filename)
                cursor = cursor.f_back
            single.append(frozenset(found))
            return original(self, request, market)

        setattr(AShareExecutionPolicy, "execute", also_one_thread)  # noqa: B010
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                outcome = pool.submit(policy.execute, intent, bar).result()
        finally:
            setattr(AShareExecutionPolicy, "execute", original)  # noqa: B010

    assert outcome.status == "filled", (
        "the probe below did not fill, so neither walk is being asked about a real fill"
    )
    assert single and __file__ not in single[0], (
        "the single-thread walk saw this file across a ThreadPoolExecutor hop, so the defeat "
        "V2-P4-053 measured no longer reproduces and the assertion below proves nothing"
    )
    assert ledger.fills, "the witness recorded no fill at all across the executor hop"
    assert any(__file__ in fill.running for fill in ledger.fills), (
        "the all-thread walk lost the submitter too, so `pool.submit(policy.execute, ...)` is "
        "still a way to fill an order from a guarded file without being seen"
    )


def test_the_sentinel_separates_a_filled_order_from_a_refused_one() -> None:
    """`V2-P4-055`, as the mutation: the fixture that makes calls and no fills.

    Every bar suspended, so the real policy is called exactly as often and rejects every time.
    The previous sentinel was `assert fills` over a list appended to **before** the call, so it
    was green here -- 12 calls, 0 fills, and a guard that could no longer tell a source that
    fills orders from one that does not. This is the repository's recurring defect named in this
    file's own docstring, and it had it.
    """
    with _watched() as ledger:
        _clearance(suspended=True)

    assert ledger.decisions, (
        "the suspended fixture stopped reaching the policy at all, so it no longer reproduces "
        "the shape V2-P4-055 measured and the separation below is untested"
    )
    assert not ledger.fills, (
        f"{len(ledger.fills)} order(s) filled on a fixture where every bar is suspended, so the "
        "fixture no longer separates a call from a fill"
    )
    assert len(ledger.decisions) == 12, (
        f"the suspended fixture made {len(ledger.decisions)} calls, not the 12 V2-P4-055 "
        "measured; if the funnel stopped offering every name a buy, this mutation is no longer "
        "the one that defeated the old sentinel"
    )
