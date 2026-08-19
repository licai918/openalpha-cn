"""`V2-P4-047`. Neither ranking source fills an order -- however the import is written.

`V2-P4-035` disclosed a residual gap it could not close with `lint-imports`:
`openalpha_cn.backtest.execution` declares `ExecutionRequest` ("A simplified cash-equity order
intent") and fills it in `AShareExecutionPolicy.execute`, both ranking sources reach that module
through `cross_section`, and the module cannot be forbidden because `cross_section.py:227` needs
the fill policy for `V2-P4-004`'s tradeability filter -- measured at 7 kept / 1 broken when
tried. What `V2-P4-035` put in its place was a **file-scoped pin on each source's own import
list**, and that pin was inoperative in two independent ways:

**(a) It only saw column-zero imports.** Both pins filtered with
`line.startswith(("import ", "from "))`, so a *function-local*
`from openalpha_cn.backtest.execution import AShareExecutionPolicy` -- indented, and therefore
invisible to `str.startswith` -- slipped past. That import creates a real `grimp` edge; the
probe's sat at `candidate_ranking.py:1264`. It filled a real order from each source
(`filled buy 100 10.20 5.01` from the ranking, `filled sell 200 10.20 6.04` from the gate) with
`lint-imports` at 8 kept / 0 broken and this directory at 103 passed.

**(b) No new import is needed at all.** `cross_section` binds `AShareExecutionPolicy`,
`ExecutionRequest` and `MarketBar` in its own namespace, so it re-exports them; and
`cross_section` is *already first* on the ranking's allowlist. Adding those three names to the
existing `from openalpha_cn.backtest.cross_section import (...)` block leaves the pin's
`line.split()[1]` byte-identical and creates **no `grimp` edge whatsoever** -- measured:
`direct_import_exists(candidate_ranking, backtest.execution)` stays `False`. The gate then
reaches the same objects through `candidate_ranking`, which is first on *its* allowlist. Orders
filled from both sources, 8 kept / 0 broken, 103 passed.

Form (b) is why this file is not another import rule. **No import-graph contract can catch it,
because it adds no edge to the graph.** So the binding here is four layers, each of which covers
what the ones beside it cannot, and each of which was driven red by a probe that really did fill
an order before it was written:

1. `test_no_fill_during_a_real_ranking_and_gate_is_made_by_either_source` -- **behavioural**. The
   real fill policy is wrapped for the length of a real screen -> ranking -> gate run, and every
   fill is asked which files are on its call stack. Catches any spelling at all, including a name
   assembled at runtime, because it never looks at a name -- but only on code the pipeline runs.
2. `test_neither_source_holds_an_order_machinery_object_in_its_namespace` -- **identity**. Alias-
   and re-export-proof, because it compares objects rather than names, and it does not care
   whether the code that binds them is ever called. This is the layer form (b) fails.
3. `test_no_code_object_in_either_source_names_the_order_machinery` -- **names, in bytecode**.
   Every code object in each file, module level and function body alike, so indentation is not a
   hiding place. This is the layer form (a) fails.
4. `test_every_import_in_either_source_resolves_away_from_the_order_machinery` -- **resolution**.
   Layer 3 reads the names an import mentions; this one follows them to the objects they bind, so
   `from ...cross_section import AShareExecutionPolicy` is judged on what it hands back rather
   than on the module it names. Both forms fail here, which is deliberate: the two forms differ
   only in where the import sits, and one assertion that sees both is the one a reader trusts.

Layers 2-4 all catch form (b) today and only layer 1 catches a runtime-assembled name; the
overlap is the point, because each covers a different escape and the cheapest of them is the one
most likely to be edited away.

The order machinery is **discovered** off `openalpha_cn.backtest.execution` rather than listed
here, so a class added to that module is banned from both sources on the day it lands and no
table has to be remembered. `V2-P4-035`'s own defect was a table asserting it was exhaustive
while nothing measured it.

**The residual, stated rather than papered over**: a source could reach the policy through a name
built from string fragments at runtime (`getattr(cross_section, "AShare" + "ExecutionPolicy")`),
which layers 2 and 3 cannot see. Layer 1 sees it the moment the pipeline runs that code, and code
the pipeline never runs fills no order in production. That is the honest boundary, and it is
narrower than "a direct import, at column zero, spelled the obvious way", which is what
`V2-P4-035` actually had.
"""

from __future__ import annotations

import dis
import importlib
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any, Final

import pytest

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
from openalpha_cn.backtest.execution import AShareExecutionPolicy, MarketBar
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

MACHINERY_MODULE: Final[str] = "openalpha_cn.backtest.execution"


def _order_machinery() -> dict[str, object]:
    """Every public object `openalpha_cn.backtest.execution` **defines**, discovered not listed.

    Filtered on `__module__` so the names that module merely imports -- `ExecutionResult` lives in
    `domain/execution.py` and is a plain result record both ranking sources legitimately read --
    are not swept in. Discovery rather than a literal table is the whole point: `V2-P4-035`'s
    defect was a comment claiming three modules "are the whole of where an order intent is
    declared or simulated" with nothing measuring the claim.
    """
    found = {
        name: obj
        for name, obj in vars(execution_module).items()
        if not name.startswith("_") and getattr(obj, "__module__", None) == MACHINERY_MODULE
    }
    assert {"AShareExecutionPolicy", "ExecutionRequest", "MarketBar"} <= set(found), (
        f"{MACHINERY_MODULE} no longer defines the policy, the order intent and the bar that "
        f"together make a fill; found {sorted(found)}. If they moved, this guard is pointing at "
        "an empty module and every assertion below would pass on nothing"
    )
    return found


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


def _bar(subject: str) -> MarketBar:
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
        suspended=False,
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


def _clearance() -> ShortlistClearance:
    """One real screen -> ranking -> gate run. Every buy below goes through the real policy."""
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
        bars={subject: _bar(subject) for subject in UNIVERSE},
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
# Layer 1 -- behavioural. No name is inspected; a fill is asked who made it.
# --------------------------------------------------------------------------------------------


def test_no_fill_during_a_real_ranking_and_gate_is_made_by_either_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim `V2-P4-035` wanted and could not enforce, as behaviour rather than as a name.

    `AShareExecutionPolicy.execute` is wrapped for the length of one real
    screen -> ranking -> gate run, and each call walks its own stack for the two source files.
    Nothing here reads an import line, an alias or a module name, so **every** spelling of the
    bypass is covered at once -- the function-local import of form (a), the re-export of form
    (b), and a name assembled at runtime, which no static rule can see.

    The count assertion is the sentinel and is not decoration. Fills **do** happen on this run --
    that is `V2-P4-004`'s tradeability filter doing its job inside `cross_section` -- so a
    version of this test whose pipeline quietly stopped filling would be asserting nothing at
    all, which is this repository's recurring defect: the assertion exists but on that fixture it
    cannot separate the two answers.
    """
    fills: list[tuple[str, ...]] = []
    guarded = set(SOURCE_PATHS.values())
    original = AShareExecutionPolicy.execute

    def recording(self: AShareExecutionPolicy, request: Any, market: Any) -> Any:
        stack: list[str] = []
        frame = sys._getframe().f_back
        while frame is not None:
            stack.append(frame.f_code.co_filename)
            frame = frame.f_back
        fills.append(tuple(stack))
        return original(self, request, market)

    monkeypatch.setattr(AShareExecutionPolicy, "execute", recording)
    _clearance()

    assert fills, (
        "sentinel: no order was filled at all on a real screen -> ranking -> gate run, so this "
        "test cannot tell a source that fills orders from one that does not. Either the wrapper "
        "is not on the policy the screen uses, or the fixture stopped exercising the "
        "tradeability filter"
    )

    culprits = {
        Path(filename).resolve()
        for stack in fills
        for filename in stack
        if Path(filename).resolve() in guarded
    }
    assert not culprits, (
        f"{sorted(str(path) for path in culprits)} is on the call stack of a filled order. "
        "Neither ranking source may fill one: `ranking-creates-no-portfolio-order` cannot "
        "forbid openalpha_cn.backtest.execution -- cross_section.py needs the policy for "
        "V2-P4-004's tradeability filter, measured at 7 kept / 1 broken when forbidden -- so "
        "this is the assertion that carries the claim, and it does not care how the import was "
        "spelled"
    )


# --------------------------------------------------------------------------------------------
# Layer 2 -- identity. Objects, not names, so an alias or a re-export is not a hiding place.
# --------------------------------------------------------------------------------------------


def test_neither_source_holds_an_order_machinery_object_in_its_namespace() -> None:
    """Form (b), which adds no `grimp` edge, fails here and can fail nowhere else structurally.

    Compared by `is` against the objects `backtest/execution.py` defines, so it makes no
    difference whether the source wrote `from openalpha_cn.backtest.execution import
    AShareExecutionPolicy`, `from openalpha_cn.backtest.cross_section import
    AShareExecutionPolicy` (which is what the probe did, adding no edge), or
    `... import AShareExecutionPolicy as _policy`. All three bind the same object, and this sees
    the object.
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
    """Form (a) -- the indented import -- fails here, at any nesting depth.

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
            "function is caught exactly like one at column zero"
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
            f"{module_name} does not live at {path}, so the stack check above guards nothing"
        )


def test_a_function_local_import_of_the_fill_policy_is_caught() -> None:
    """The mutation that made the pin this file replaces go red, kept as a test.

    Form (a) written into a copy of the module rather than the module itself, so the assertion
    that catches it is exercised on every run instead of being trusted. A guard whose failing
    branch nothing ever takes is the other half of this repository's recurring defect.
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
