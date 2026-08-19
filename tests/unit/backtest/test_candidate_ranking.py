"""The candidate ranking contract (`V2-P4-005`), held to D16's ten constituents and its one ban.

Six properties this file exists to hold, each of which is a place a candidate list silently
becomes a list of something else:

1. **The ban is structural, and it is the portfolio-order ban and not a wider one.**
   `test_the_ranking_contract_cannot_reach_the_three_modules_that_make_an_order` drives
   `lint-imports` over the real module and then over a copy of it carrying one `PortfolioOrder`
   import, and requires the second to be refused -- so "绝不直接创建组合订单" is a gate rather
   than a sentence. `V2-P4-035` measured what that gate does *not* cover:
   `backtest/execution.py`'s `ExecutionRequest` is an order intent too, this module reaches it
   through `cross_section`, and no contract forbids that because the edge is `V2-P4-004`'s
   tradeability filter. `test_this_ranking_grows_no_import_of_its_own_into_the_order_machinery`
   is the weaker, file-scoped guard that covers the step the acceptance probe actually took.
2. **The two addresses are split and each moves for exactly what it addresses.** The manifest's
   moves for every declared input and not for the wall clock; the content digest moves for a
   candidate and not for the manifest. Both directions on both, plus a `model_fields` meta-audit,
   because a one-directional identity test passes on a constant.
3. **"因子暴露" is two different things.** `test_the_score_decomposition_and_the_risk
   _characteristic_separate_in_both_directions` builds one pair with identical score terms and
   different exposures and one pair with identical exposures and different score terms, so
   neither can be recovered from the other -- and
   `test_a_neutralised_top_rank_is_an_industry_and_size_ordering_the_exposure_makes_readable`
   reproduces `V2-P4-004`'s whole-market finding through both real engines and requires the
   ranking to show it.
4. **Every risk flag separates.** Six candidates, one flag each, in a fixture where a flag that
   fired on the wrong candidate would change the answer -- and the boundary tie is given a
   fixture where the tie extends *outside* the cut, which is the only shape in which the funnel's
   `tied_at_the_cut` and the shortlist's own scores disagree.
5. **A horizon is one horizon.** `test_two_horizons_in_one_ranking_are_refused_by_name` is the
   first consumer of `V2-P4-001`'s narrowing that can fail, and it says which subject differs.
6. **The evidence plane's own flag vocabulary is measured rather than described.**
   `test_the_two_shipped_gates_read_disjoint_subsets_of_an_open_flag_set` reads `RiskGate` and
   `DeliberationCommittee` themselves.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import grimp
import pytest
from importlinter.cli import lint_imports
from pydantic import ValidationError

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.backtest.candidate_ranking import (
    KNOWN_RANKING_LIMITATIONS,
    RANKING_LIMITATION_CODES,
    RANKING_MANIFEST_UNADDRESSED_FIELDS,
    RANKING_RISK_FLAG_CODES,
    RANKING_RISK_FLAG_ORDER,
    CandidateExposure,
    CandidatePrediction,
    CandidateRanking,
    CandidateRankingError,
    CandidateRankingManifest,
    RankedCandidate,
    build_ranking_manifest,
    rank_candidates,
    ranking_content_digest,
)
from openalpha_cn.backtest.cross_section import (
    ComponentCrossSection,
    CrossSectionFunnel,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
)
from openalpha_cn.backtest.execution import AShareExecutionPolicy, MarketBar
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.domain.factor import (
    FactorBuildManifest,
    FactorDefinition,
    FactorField,
    FactorInputRef,
    FactorObservation,
    cross_section_digest,
    set_digest,
)
from openalpha_cn.domain.factor_neutralization import (
    IndustryMarketCapCrossSection,
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
)
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    REVERSAL_1D,
    FactorPanel,
    apply_factor_transform,
)
from openalpha_cn.panel_neutralization import INDUSTRY_AND_SIZE, apply_factor_neutralization

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
MODULE_PATH: Final[Path] = ROOT / "src" / "openalpha_cn" / "backtest" / "candidate_ranking.py"

AS_OF: Final[datetime] = datetime(2026, 6, 12, 4, 0, tzinfo=UTC)
SESSION: Final[date] = date(2026, 6, 12)
BUILT_AT: Final[datetime] = datetime(2026, 6, 13, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"
CONFIG: Final[str] = "c" * 64
CAPITAL: Final[Decimal] = Decimal("100000")
HORIZON: Final[str] = "5d"


def code(index: int) -> str:
    return f"{index:06d}.SZ"


TWELVE: Final[tuple[str, ...]] = tuple(code(index) for index in range(1, 13))


def _definition(key: str = "probe_alpha") -> FactorDefinition:
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


ALPHA: Final[FactorDefinition] = _definition()
BETA: Final[FactorDefinition] = _definition("probe_beta")


def _bar(subject: str, *, close: float = 10.0) -> MarketBar:
    price = Decimal(str(round(close, 2)))
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


def _bars(subjects: tuple[str, ...] = TWELVE) -> dict[str, MarketBar]:
    return {subject: _bar(subject) for subject in subjects}


def _component(
    definition: FactorDefinition,
    values: dict[str, float | None],
    *,
    clipped: frozenset[str] = frozenset(),
) -> ComponentCrossSection:
    return ComponentCrossSection(
        factor_id=definition.factor_id,
        values=tuple((subject, value, "processed") for subject, value in values.items()),
        clipped_subjects=clipped,
    )


def _spec(
    *components: tuple[FactorDefinition, float],
    tier: str = "processed",
    shortlist_size: int = 3,
) -> ShortlistSpec:
    declared = components or ((ALPHA, 1.0),)
    return ShortlistSpec(
        components=tuple(
            ScoreComponent(definition=definition, weight=weight) for definition, weight in declared
        ),
        tier=tier,  # type: ignore[arg-type]
        shortlist_size=shortlist_size,
        position_capital=CAPITAL,
    )


def _straight_market() -> dict[str, float | None]:
    """Twelve securities whose scores are distinct and descending in the code order."""
    return {subject: 12.0 - index for index, subject in enumerate(TWELVE)}


def _funnel(
    *,
    spec: ShortlistSpec | None = None,
    values: dict[str, float | None] | None = None,
    clipped: frozenset[str] = frozenset(),
    universe: tuple[str, ...] = TWELVE,
) -> CrossSectionFunnel:
    """A real `CrossSectionFunnel` off a real `CrossSectionScreen`, never a hand-built one."""
    declared = spec or _spec()
    market = values if values is not None else _straight_market()
    return CrossSectionScreen(declared, execution=AShareExecutionPolicy()).select(
        as_of=AS_OF,
        universe=universe,
        components=[_component(ALPHA, market, clipped=clipped)],
        bars=_bars(universe),
    )


def _signal(subject: str, *, direction: str = "bullish", horizon: str = HORIZON) -> SignalFrame:
    payload: dict[str, Any] = {
        "subject": subject,
        "as_of": AS_OF,
        "direction": direction,
        "strength": 0.4 if direction == "bullish" else -0.4 if direction == "bearish" else 0.0,
        "confidence": 0.7,
        "horizon": horizon,
        "evidence_ids": ("evd_000000000000000000000001",),
    }
    if direction == "abstain":
        payload["strength"] = 0.0
        payload["evidence_ids"] = ()
        payload["abstention_reason"] = "evidence insufficient"
    return SignalFrame(**payload)


def _run_manifest(subject: str) -> RunManifest:
    """A real `RunManifest`, so the id a candidate carries is `V2-P4-025`'s own output."""
    return RunManifest(
        run_id=f"run-{subject}",
        mode="backtest",
        as_of=AS_OF,
        code_commit=COMMIT,
        config_digest=CONFIG,
        random_seed=7,
        started_at=AS_OF,
        finished_at=BUILT_AT,
        status="succeeded",
    )


def _manifest(
    *,
    spec: ShortlistSpec | None = None,
    universe: tuple[str, ...] = TWELVE,
    as_of: datetime = AS_OF,
    horizon: str = HORIZON,
    code_commit: str = COMMIT,
    config_digest: str = CONFIG,
    built_at: datetime = BUILT_AT,
) -> CandidateRankingManifest:
    return build_ranking_manifest(
        as_of=as_of,
        horizon=horizon,
        universe=list(universe),
        scoring_policy=spec or _spec(),
        code_commit=code_commit,
        config_digest=config_digest,
        built_at=built_at,
    )


def _rank(
    funnel: CrossSectionFunnel | None = None,
    *,
    manifest: CandidateRankingManifest | None = None,
    directions: dict[str, str] | None = None,
    horizons: dict[str, str] | None = None,
    unresearched: tuple[str, ...] = (),
    exposures: IndustryMarketCapCrossSection | None = None,
    predictions: dict[str, CandidatePrediction] | None = None,
) -> CandidateRanking:
    cut = funnel if funnel is not None else _funnel()
    chosen = tuple(
        entry.subject for entry in cut.shortlist if entry.subject not in set(unresearched)
    )
    return rank_candidates(
        manifest=manifest or _manifest(),
        funnel=cut,
        signals={
            subject: _signal(
                subject,
                direction=(directions or {}).get(subject, "bullish"),
                horizon=(horizons or {}).get(subject, HORIZON),
            )
            for subject in chosen
        },
        run_manifest_ids={subject: _run_manifest(subject).run_manifest_id for subject in chosen},
        exposures=exposures,
        predictions=predictions or {},
    )


def _characteristics(
    assignments: dict[str, tuple[str, float]],
    *,
    backfilled: frozenset[str] = frozenset(),
    as_of: datetime = AS_OF,
    without_industry: tuple[str, ...] = (),
    industry_level: str = "L1",
    market_cap_measure: str = "total_mv",
) -> IndustryMarketCapCrossSection:
    return build_industry_market_cap_cross_section(
        as_of=as_of,
        taxonomy="SW2021",
        industry_level=industry_level,  # type: ignore[arg-type]
        market_cap_measure=market_cap_measure,  # type: ignore[arg-type]
        characteristics=[
            SecurityCharacteristic(
                subject=subject,
                industry_code=industry,
                market_cap=cap,
                is_backfilled=subject in backfilled,
            )
            for subject, (industry, cap) in assignments.items()
        ],
        without_industry=without_industry,
    )


# --------------------------------------------------------------------------------------------
# D16's prohibition, as a gate
# --------------------------------------------------------------------------------------------


def _lint(contract: str) -> int:
    """`lint_imports` limited to one contract, with the logging state it silently wrecks put back.

    **The restore is not optional and the first draft of this file learned that from the suite.**
    `importlinter.cli.lint_imports` calls `logging.config.dictConfig` with a config naming only
    `importlinter`, `grimp` and `_rustgrimp`, and `dictConfig` defaults to
    `disable_existing_loggers=True` -- which sets `.disabled = True` on **every** logger already
    in the process. That damage is process-wide, not module-local: this file captures no logs of
    its own, and a version of `_lint` without the restore took down three tests in three other
    directories on a full run --
    `tests/unit/runtime/test_composition_migrations.py::
    test_build_storage_logs_runtime_dir_and_schema_version_on_startup`,
    `tests/unit/test_cli.py::
    test_probe_report_logs_provider_failure_category_and_provider_id_not_the_message` and
    `tests/unit/test_import_layering.py::
    test_running_the_import_linter_leaves_an_existing_logger_enabled`, whose *precondition* is
    that no earlier test left that logger disabled. It is the least findable failure shape there
    is, which is exactly what `test_import_layering.py`'s own docstring says about it.

    Copied from `tests/unit/test_import_layering.py::_lint_imports` rather than imported. The
    alternative is `from tests.unit.test_import_layering import _lint_imports`, which makes one
    collected test module the import-time dependency of another and gives pytest two paths to the
    same file; the duplication is eight lines and
    `test_no_test_in_this_module_calls_lint_imports_without_restoring_logging` is what keeps a
    later bare call out of *this* file, which is the half a shared helper would not have covered
    anyway.
    """
    manager = logging.Logger.manager
    before = {
        name: existing.disabled
        for name, existing in manager.loggerDict.items()
        if isinstance(existing, logging.Logger)
    }
    try:
        return lint_imports(  # type: ignore[arg-type]
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=(contract,),
        )
    finally:
        for name, disabled in before.items():
            restored = manager.loggerDict.get(name)
            if isinstance(restored, logging.Logger):
                restored.disabled = disabled


def test_no_test_in_this_module_calls_lint_imports_without_restoring_logging() -> None:
    """`test_import_layering.py`'s guard, installed on this file too, because it had to be.

    That module's version of this test protects that module's own source and nothing else, so
    the second file in the repository to call the import linter arrived unguarded -- and the
    failure it produced was three unrelated tests in three other directories, which is the
    failure shape that test's docstring names as the least findable there is.

    Checked on this file's source: the only bare `lint_imports(` here is the one inside `_lint`.
    A backtick before the name means this file is talking about the call rather than making it.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    bare_calls = re.findall(r"(?<![_`])lint_imports\(", source)

    assert len(bare_calls) == 1, (
        f"expected exactly 1 bare `lint_imports(` call, the one inside `_lint`; found "
        f"{len(bare_calls)}. Every other call site must go through `_lint`, or the whole suite "
        "gains an order dependence that surfaces in another directory -- and unlike "
        "test_import_layering.py this file may not even keep one deliberate bare call, because "
        "it sorts before the caplog acceptances such a call disables"
    )


def test_the_lint_wrapper_leaves_an_already_enabled_logger_enabled() -> None:
    """The containment, driven without reproducing the pollution -- which this file may not do.

    `tests/unit/test_import_layering.py` proves the damage is real by calling the raw CLI once on
    purpose and re-enabling the one logger it names. That call leaves every *other* logger
    disabled, and the suite survives it only because `tests/unit/test_import_layering.py` sorts
    after the `caplog` acceptances it would break. This file sorts **before** them, so the same
    deliberate call here is not survivable and is not made: the pollution is proved once, where
    the collection order absorbs it.

    What is checked here instead is the property that matters and that a deleted `finally` would
    break -- a logger that existed and was enabled before `_lint` is enabled after it -- plus its
    other direction, that a logger which was already disabled stays disabled, because the wrapper
    restores a snapshot rather than blanket-enabling everything it can reach.
    """
    enabled = logging.getLogger("openalpha_cn.probe.ranking_lint_enabled")
    disabled = logging.getLogger("openalpha_cn.probe.ranking_lint_disabled")
    enabled.disabled = False
    disabled.disabled = True

    assert _lint("ranking-creates-no-portfolio-order") == 0

    assert not enabled.disabled, (
        "a logger that was enabled before the import linter ran must be enabled after it; "
        "importlinter's dictConfig(disable_existing_loggers=True) disables every logger in the "
        "process, and a `_lint` without its restore took down three caplog acceptances in three "
        "other directories on a full run"
    )
    assert disabled.disabled, (
        "and one that was already disabled stays disabled: `_lint` puts back the state it found "
        "rather than enabling whatever it can reach"
    )


def test_the_ranking_contract_cannot_reach_the_three_modules_that_make_an_order() -> None:
    """D16's `绝不直接创建组合订单`, driven in both directions rather than asserted.

    `V2-P4-004` enforced "creates no order" with the two study contracts, which forbid
    `openalpha_cn.storage` and `openalpha_cn.runtime`. Those stop this module *persisting* an
    order and reaching the engine that would place one, and they do not stop it *constructing*
    one: `domain/portfolio.py` declares `PortfolioOrder` and is a plain data module every
    `backtest/` study may import. `ranking-creates-no-portfolio-order` is the contract that
    closes that, and this drives the real module through it and then the same file with one
    import added -- so the pass cannot be vacuous.
    """
    assert _lint("ranking-creates-no-portfolio-order") == 0

    original = MODULE_PATH.read_text(encoding="utf-8")
    try:
        MODULE_PATH.write_text(
            original.replace(
                "from openalpha_cn.domain.run import RUN_MANIFEST_ID_PATTERN",
                "from openalpha_cn.domain.portfolio import PortfolioOrder\n"
                "from openalpha_cn.domain.run import RUN_MANIFEST_ID_PATTERN\n"
                "_ORDER = PortfolioOrder",
            ),
            encoding="utf-8",
        )
        assert _lint("ranking-creates-no-portfolio-order") == 1, (
            "lint-imports must reject candidate_ranking -> domain.portfolio; if this passes, "
            "D16's ban is a docstring again"
        )
    finally:
        MODULE_PATH.write_text(original, encoding="utf-8")

    assert _lint("ranking-creates-no-portfolio-order") == 0


def test_this_ranking_grows_no_import_of_its_own_into_the_order_machinery() -> None:
    """`V2-P4-035`. The pin `shortlist_gate.py` had incidentally and this module had not at all.

    `ranking-creates-no-portfolio-order` forbids the three modules where a **portfolio** order is
    declared or simulated, and that is all it forbids. It does not forbid
    `openalpha_cn.backtest.execution`, which declares `ExecutionRequest` -- "a simplified
    cash-equity order intent" -- and simulates a fill in `AShareExecutionPolicy.execute`; and it
    cannot, because `backtest/cross_section.py` imports that policy to decide tradeability, which
    is `V2-P4-004`'s hard filter and a shipped feature. This module therefore reaches an order
    intent transitively today, on purpose, by
    `candidate_ranking -> cross_section -> execution`.

    `V2-P4-035`'s acceptance probe added a *direct* import of that policy here plus a function
    calling `.execute(...)`, filled an order, and neither `lint-imports` (8 kept, 0 broken) nor
    this file noticed. The identical probe in `shortlist_gate.py` was caught -- but by
    `test_this_gate_cannot_measure_dataset_freshness_because_it_cannot_reach_the_panel`, whose
    three-name import list catches it incidentally, and not by the order contract. This module
    had no such list, so it was the one contract source with nothing pinning its import surface.

    This is deliberately weaker than a `lint-imports` contract and says so: it constrains the
    import lines of one file, not reachability, and it cannot stop a caller reaching
    `AShareExecutionPolicy` through `cross_section` -- that edge is the feature. What it does
    stop is this module quietly growing an edge of its own into the order machinery, which is the
    step the probe actually took.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and " import " in line
    ]

    assert sorted(line.split()[1] for line in imports if line.startswith("from openalpha_cn")) == [
        "openalpha_cn.backtest.cross_section",
        "openalpha_cn.backtest.factor_ic",
        "openalpha_cn.domain._identity",
        "openalpha_cn.domain.execution",
        "openalpha_cn.domain.factor",
        "openalpha_cn.domain.factor_neutralization",
        "openalpha_cn.domain.horizon",
        "openalpha_cn.domain.run",
        "openalpha_cn.domain.signal",
        "openalpha_cn.domain.time",
    ], (
        "candidate_ranking.py's first-party import list changed. If the new name is a route into "
        "the order machinery -- openalpha_cn.backtest.execution declares an order intent and "
        "this contract does not forbid it -- that is the V2-P4-035 defect arriving again. If it "
        "is not, add it to this list and say why in the same commit"
    )


def test_the_ranking_contract_reaches_neither_a_store_nor_the_root_that_owns_run_cycle() -> None:
    """The two contracts `V2-P4-004` already had, asserted here against the live graph too.

    Read off `grimp` rather than off `lint-imports` so that a reader of this file sees the
    enforcement, and with a sentinel so the assertions cannot prove nothing.
    """
    graph = grimp.build_graph("openalpha_cn")

    for plane in (
        "openalpha_cn.runtime",
        "openalpha_cn.storage",
        "openalpha_cn.panel",
        "openalpha_cn.decisions",
        "openalpha_cn.agents",
        "openalpha_cn.product",
    ):
        assert not graph.direct_import_exists(
            importer="openalpha_cn.backtest.candidate_ranking", imported=plane, as_packages=True
        ), f"the candidate ranking contract must not reach {plane}"

    assert graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage", as_packages=True
    ), "sentinel: run_cycle's own module must reach the store, or the assertions above prove none"


def test_every_declared_limitation_code_is_the_registrys_own_set() -> None:
    """The twenty-second registry, as an equality against a set literal in executable code.

    Equality rather than membership, `KNOWN_ADJUSTMENT_LIMITATIONS`' form: a membership
    assertion can see a code that was renamed and never a code that was removed.
    """
    assert {
        "the_ranking_does_not_re_rank_and_inherits_every_caveat_on_the_funnels_order",
        "no_model_prediction_exists_in_this_build",
        "factor_exposure_here_is_a_characteristic_and_not_a_fitted_loading",
        "the_signals_own_risk_flags_are_an_open_set_and_two_gates_read_disjoint_subsets",
        "no_capacity_warning_is_derivable_here_so_none_is_flagged",
        "the_universe_is_addressed_by_digest_and_the_funnel_can_only_check_its_size",
        "this_contract_creates_no_order_because_of_where_it_lives_and_not_because_it_says_so",
    } == RANKING_LIMITATION_CODES
    assert len(KNOWN_RANKING_LIMITATIONS) == len(RANKING_LIMITATION_CODES)
    assert all(limitation.detail.strip() for limitation in KNOWN_RANKING_LIMITATIONS)


# --------------------------------------------------------------------------------------------
# Identity: the declaration's address and the answer's, split
# --------------------------------------------------------------------------------------------


def test_the_ranking_manifest_address_moves_for_every_declared_input() -> None:
    """Half of the identity claim, and the half a constant would pass.

    Every field except `built_at` is varied alone and required to move the address. The scoring
    policy is varied five ways -- weight, factor, tier, cut and capital -- because it is embedded
    rather than digested, so its own fields reach the identity without this manifest declaring
    one each; that is the property embedding buys and it is worth measuring.
    """
    base = _manifest()
    moved = {
        "as_of": _manifest(as_of=AS_OF.replace(day=13)),
        "horizon": _manifest(horizon="10d"),
        "universe": _manifest(universe=TWELVE[:-1]),
        "code_commit": _manifest(code_commit="9999999"),
        "config_digest": _manifest(config_digest="d" * 64),
        "policy.weight": _manifest(spec=_spec((ALPHA, 2.0))),
        "policy.factor": _manifest(spec=_spec((BETA, 1.0))),
        "policy.tier": _manifest(spec=_spec(tier="neutralized")),
        "policy.shortlist_size": _manifest(spec=_spec(shortlist_size=4)),
        "policy.capital": _manifest(
            spec=ShortlistSpec(
                components=(ScoreComponent(definition=ALPHA, weight=1.0),),
                tier="processed",
                shortlist_size=3,
                position_capital=Decimal("200000"),
            )
        ),
    }

    unmoved = sorted(
        name
        for name, variant in moved.items()
        if variant.ranking_manifest_id == base.ranking_manifest_id
    )
    assert unmoved == [], (
        "a declared input that does not move the address is one a caller can change without the "
        "ranking's identity noticing -- roadmap section 9's finding on config_digest and "
        "random_seed, arriving on a new contract"
    )
    assert base.ranking_manifest_id.startswith("rnk_")
    assert len(base.ranking_manifest_id) == len("rnk_") + 24


def test_the_wall_clock_this_ranking_was_assembled_on_does_not_move_its_address() -> None:
    """The other half, without which the test above is satisfied by hashing everything.

    Re-running one declaration has to reproduce its `ranking_manifest_id`, or the address cannot
    be used to recognise the same screen re-asked -- and the clock is the one field guaranteed to
    differ between two such runs.
    """
    base = _manifest()
    later = _manifest(built_at=BUILT_AT.replace(year=2027))

    assert later.built_at != base.built_at
    assert later.ranking_manifest_id == base.ranking_manifest_id


def test_every_ranking_manifest_field_is_addressed_or_excluded_by_name() -> None:
    """The meta-audit, so field *n+1* is red until somebody decides which side it is on.

    `RUN_MANIFEST_UNADDRESSED_FIELDS`' own audit, reused: the two tests above cover the fields
    that exist today and neither would notice a new one, because a new field silently joins the
    identity and nothing varies it.
    """
    declared = set(CandidateRankingManifest.model_fields)
    excluded = set(RANKING_MANIFEST_UNADDRESSED_FIELDS)

    assert excluded <= declared, sorted(excluded - declared)
    assert excluded == {"built_at"}
    addressed = declared - excluded
    assert "schema_version" in addressed, (
        "a v2 declaration shape is a different declaration and must not share an address with v1"
    )
    assert all(RANKING_MANIFEST_UNADDRESSED_FIELDS[name].strip() for name in excluded), (
        "an exclusion with no stated reason is indistinguishable from an oversight"
    )
    assert len(addressed) == 8


def test_the_content_digest_addresses_the_answer_and_not_the_declaration() -> None:
    """`V2-P3-014`'s split, measured on this contract: the two addresses move for different things.

    S49 asks to compare a candidate list with prior runs, so the digest has to move when a
    candidate's rank, score or conclusion moves, and must not move when the clock does -- while
    the manifest's address moves for the declaration and not for the answers. A single identity
    would have to do both and could do neither.
    """
    ranking = _rank()
    same = _rank(manifest=_manifest(built_at=BUILT_AT.replace(year=2027)))
    assert same.content_digest == ranking.content_digest
    assert same.manifest.ranking_manifest_id == ranking.manifest.ranking_manifest_id

    first = ranking.candidates[0]
    bearish = _rank(directions={first.subject: "bearish"})
    assert bearish.content_digest != ranking.content_digest, (
        "the signal_id is in the payload, so a different conclusion is a different answer"
    )
    assert bearish.manifest.ranking_manifest_id == ranking.manifest.ranking_manifest_id, (
        "and the declaration did not change, which is the whole point of splitting them"
    )

    shorter = _rank(unresearched=(first.subject,))
    assert shorter.content_digest != ranking.content_digest
    assert ranking.content_digest.startswith("rkc_")
    assert ranking_content_digest(()) == ranking_content_digest([])

    moved = RankedCandidate(
        subject=first.subject,
        rank=first.rank + 1,
        score=first.score,
        components=first.components,
        fill=first.fill,
        signal=first.signal,
        run_manifest_id=first.run_manifest_id,
        exposure=first.exposure,
        prediction=first.prediction,
        risk_flags=first.risk_flags,
    )
    assert ranking_content_digest((moved,)) != ranking_content_digest((first,)), (
        "the rank is in the payload on its own account: one security can hold the same score, "
        "the same conclusion and the same run declaration at two different positions across two "
        "as_ofs, and S49's whole question is whether it moved"
    )

    reseeded = RankedCandidate(
        subject=first.subject,
        rank=first.rank,
        score=first.score,
        components=first.components,
        fill=first.fill,
        signal=first.signal,
        run_manifest_id=_run_manifest(first.subject)
        .model_copy(update={"random_seed": 8})
        .run_manifest_id,
        exposure=first.exposure,
        prediction=first.prediction,
        risk_flags=first.risk_flags,
    )
    assert reseeded.run_manifest_id != first.run_manifest_id
    assert ranking_content_digest((reseeded,)) != ranking_content_digest((first,)), (
        "and so is the run declaration: the same conclusion reached under a different declared "
        "seed or config is a different answer, which is exactly what roadmap section 9 measured "
        "the old decision_id to be blind to"
    )


def test_a_derived_field_is_deliberately_outside_the_content_digest() -> None:
    """`risk_flags` and `exposure` are functions of what is already hashed, so they are not in it.

    A digest over derived quantities moves when a derivation changes and reports it as a changed
    candidate list, which is the opposite of what a caller diffing two runs is asking. Measured
    rather than promised: the same candidates with and without an exposure cross section carry
    different flags and one digest.
    """
    plain = _rank()
    with_exposure = _rank(
        exposures=_characteristics(
            {subject: ("801010.SI", 1_000.0) for subject in TWELVE}, backfilled=frozenset(TWELVE)
        )
    )

    assert plain.candidates[0].risk_flags != with_exposure.candidates[0].risk_flags
    assert plain.candidates[0].exposure is None
    assert with_exposure.candidates[0].exposure is not None
    assert plain.content_digest == with_exposure.content_digest


# --------------------------------------------------------------------------------------------
# 股票池 and as-of
# --------------------------------------------------------------------------------------------


def test_the_universe_is_addressed_by_a_digest_the_funnel_can_only_size_check() -> None:
    """The disclosure, made executable in both of its directions.

    `CrossSectionFunnel` carries a count and not the names, so the cross-check binds the digest
    to a population *size*. Two universes of equal size and different membership therefore
    produce two different digests and one identical cross-check -- which is exactly what
    `the_universe_is_addressed_by_digest_and_the_funnel_can_only_check_its_size` records, and it
    would be prose if nothing drove it.
    """
    other = tuple(f"{index:06d}.SH" for index in range(1, 13))
    assert len(other) == len(TWELVE)

    mine = _manifest()
    theirs = _manifest(universe=other)
    assert theirs.universe_digest != mine.universe_digest
    assert theirs.universe_count == mine.universe_count

    ranked = rank_candidates(
        manifest=theirs,
        funnel=_funnel(),
        signals={},
        run_manifest_ids={},
        exposures=None,
        predictions={},
    )
    assert ranked.manifest.universe_digest != mine.universe_digest, (
        "the cross-check passed on a universe the funnel never screened; that is the gap the "
        "limitation code discloses and this is where it is visible"
    )

    with pytest.raises(CandidateRankingError, match="the digest addresses a population"):
        rank_candidates(
            manifest=_manifest(universe=TWELVE[:-1]),
            funnel=_funnel(),
            signals={},
            run_manifest_ids={},
            exposures=None,
            predictions={},
        )


def test_a_universe_digest_is_the_repositorys_own_and_not_a_string_somebody_typed() -> None:
    """`UNIVERSE_DIGEST_PATTERN` is `set_digest`'s output, and `build_ranking_manifest` computes it.

    A content address that is only conventionally a content address stops being one the first
    time it is convenient -- `storage/factor_experiments.py`'s rule, applied to the stock pool.
    """
    manifest = _manifest()
    assert manifest.universe_digest == set_digest(TWELVE)
    assert _manifest(universe=(*TWELVE, TWELVE[0])).universe_digest == manifest.universe_digest
    assert _manifest(universe=(*TWELVE, TWELVE[0])).universe_count == len(TWELVE)

    with pytest.raises(ValidationError, match="universe_digest"):
        CandidateRankingManifest(
            as_of=AS_OF,
            horizon=HORIZON,
            universe_digest="the whole a-share market",
            universe_count=12,
            scoring_policy=_spec(),
            code_commit=COMMIT,
            config_digest=CONFIG,
            built_at=BUILT_AT,
        )
    with pytest.raises(CandidateRankingError, match="a ranking needs a universe"):
        _manifest(universe=())


def test_a_signal_at_another_as_of_is_refused_and_so_is_a_funnel_at_one() -> None:
    """A candidate list is one moment's answer, in all three of the places a moment enters it."""
    with pytest.raises(CandidateRankingError, match="a candidate list is one moment's answer"):
        _rank(manifest=_manifest(as_of=AS_OF.replace(day=11)))

    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    signals = {subject: _signal(subject) for subject in subjects}
    signals[subjects[0]] = SignalFrame(
        subject=subjects[0],
        as_of=AS_OF.replace(day=11),
        direction="bullish",
        strength=0.4,
        confidence=0.7,
        horizon=HORIZON,
        evidence_ids=("evd_000000000000000000000001",),
    )

    with pytest.raises(CandidateRankingError, match="'s signal is as of"):
        rank_candidates(
            manifest=_manifest(),
            funnel=funnel,
            signals=signals,
            run_manifest_ids={
                subject: _run_manifest(subject).run_manifest_id for subject in subjects
            },
            exposures=None,
            predictions={},
        )


# --------------------------------------------------------------------------------------------
# 周期: V2-P4-001's narrowing, consumed
# --------------------------------------------------------------------------------------------


def test_two_horizons_in_one_ranking_are_refused_by_name() -> None:
    """The first consumer of `V2-P4-001`'s narrowing that can fail, and it says which subject.

    A list holding a five-session and a ten-session conclusion is ordered on two different
    questions. `ResearchEngine._aggregate` stamps `5d` on every signal it writes whatever its
    agents declared, so a ranking built from `run_cycle` can never trip this; a caller assembling
    candidates from stored frames can, which is what this drives.
    """
    funnel = _funnel()
    odd = funnel.shortlist[1].subject

    with pytest.raises(CandidateRankingError, match=f"{odd}'s signal is over '10d'"):
        _rank(funnel, horizons={odd: "10d"})

    assert _rank(funnel, horizons=dict.fromkeys(TWELVE, HORIZON)).candidate_count == 3


def test_a_calendar_horizon_is_refused_by_the_grammar_a_signal_can_carry() -> None:
    """`3m` is a legal `HORIZON_PATTERN` value and has not been a legal `SignalFrame.horizon`
    since `V2-P4-001`, so a ranking declaring it could never be satisfied.

    Refused by `build_ranking_manifest` with the reason, as well as by the field's own `pattern`,
    so a caller is told which of the two grammars it fell out of rather than reading a regex.
    """
    with pytest.raises(CandidateRankingError, match="not a horizon a SignalFrame can carry"):
        _manifest(horizon="3m")
    with pytest.raises(ValidationError, match="String should match pattern"):
        CandidateRankingManifest(
            as_of=AS_OF,
            horizon="3m",
            universe_digest=set_digest(TWELVE),
            universe_count=12,
            scoring_policy=_spec(),
            code_commit=COMMIT,
            config_digest=CONFIG,
            built_at=BUILT_AT,
        )
    assert _manifest(horizon="999d").horizon == "999d"


# --------------------------------------------------------------------------------------------
# 因子暴露: the score's decomposition and the risk characteristic are two things
# --------------------------------------------------------------------------------------------


def test_the_score_decomposition_and_the_risk_characteristic_separate_in_both_directions() -> None:
    """Neither is recoverable from the other, driven as two pairs rather than argued.

    A fixture in which the two moved together would prove nothing, which is the failure this
    repository has met more than ten times. So: one pair with identical score terms and different
    exposures, and one pair with identical exposures and different score terms.
    """
    tied = dict.fromkeys(TWELVE, 5.0)
    tied[TWELVE[0]] = 9.0
    tied[TWELVE[1]] = 9.0
    tied[TWELVE[2]] = 8.0
    exposures = _characteristics(
        {
            TWELVE[0]: ("801010.SI", 1_000.0),
            TWELVE[1]: ("801020.SI", 9_000.0),
            TWELVE[2]: ("801010.SI", 1_000.0),
            **{subject: ("801030.SI", 500.0) for subject in TWELVE[3:]},
        }
    )
    ranking = _rank(_funnel(values=tied), exposures=exposures)

    first, second, third = ranking.candidates
    assert [item.score for item in (first, second, third)] == [9.0, 9.0, 8.0]

    assert first.components == second.components, "identical score terms ..."
    assert first.exposure != second.exposure, "... and different exposures"

    assert third.exposure == first.exposure, "identical exposures ..."
    assert third.components != first.components, "... and different score terms"

    assert first.exposure == CandidateExposure(
        industry_code="801010.SI",
        industry_level="L1",
        market_cap=1_000.0,
        market_cap_measure="total_mv",
        is_backfilled=False,
    )

    other_readings = _rank(
        _funnel(values=tied),
        exposures=_characteristics(
            {subject: ("801011.SI", 7.0) for subject in TWELVE},
            industry_level="L2",
            market_cap_measure="circ_mv",
        ),
    )
    assert other_readings.candidates[0].exposure == CandidateExposure(
        industry_code="801011.SI",
        industry_level="L2",
        market_cap=7.0,
        market_cap_measure="circ_mv",
        is_backfilled=False,
    ), (
        "the level and the measure are read off the cross section the characteristic came from, "
        "not restated here -- an industry code means nothing without the level it was read at, "
        "and a capitalisation nothing without the measure it came from"
    )


def test_a_neutralised_top_rank_is_an_industry_and_size_ordering_the_exposure_makes_readable() -> (
    None
):
    """`V2-P4-004`'s sharpest finding, carried into this contract and made readable on one record.

    That issue measured, whole-market, that `INDUSTRY_AND_SIZE` gives the clip block's identical
    processed value 41 *distinct* residuals ordered entirely by industry mean and log size, with
    seven of the neutralised top ten inside it. This drives both real engines over one 120-name
    cross section and requires the ranking to show the same shape on a single candidate: the top
    names carry `score_is_a_winsorization_bound`, their carried `value`s are the *residuals* and
    differ, and their exposures name the industries and capitalisations that produced the order.

    Without `CandidateExposure` those three facts are one number and a boolean.
    """
    size = 120
    values: dict[str, float | None] = {code(index + 1): float(index + 1) for index in range(size)}
    subjects = tuple(values)
    processed = apply_factor_transform(
        _panel(values), CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    stored = processed.values()
    highest = max(stored.values())
    block = frozenset(subject for subject, value in stored.items() if value == highest)
    assert len(block) == 2, "the shipped 1% clip ties two names at this cross-section size"

    assignments = {
        subject: (f"80{10 + 10 * (index % 4)}.SI", 1_000.0 * (index + 1))
        for index, subject in enumerate(subjects)
    }
    characteristics = _characteristics(assignments)
    neutralized = apply_factor_neutralization(
        processed, INDUSTRY_AND_SIZE, characteristics, code_commit=COMMIT, built_at=BUILT_AT
    )
    residuals: dict[str, float | None] = {
        observation.subject: observation.value for observation in neutralized.observations
    }

    spec = ShortlistSpec(
        components=(ScoreComponent(definition=ALPHA, weight=1.0),),
        tier="neutralized",
        shortlist_size=5,
        position_capital=CAPITAL,
    )
    funnel = CrossSectionScreen(spec, execution=AShareExecutionPolicy()).select(
        as_of=AS_OF,
        universe=subjects,
        components=[
            ComponentCrossSection(
                factor_id=ALPHA.factor_id,
                values=tuple(
                    (subject, value, "neutralized") for subject, value in residuals.items()
                ),
                clipped_subjects=block,
            )
        ],
        bars=_bars(subjects),
    )
    assert funnel.coverage == "shortlisted"

    ranking = rank_candidates(
        manifest=_manifest(spec=spec, universe=subjects),
        funnel=funnel,
        signals={entry.subject: _signal(entry.subject) for entry in funnel.shortlist},
        run_manifest_ids={
            entry.subject: _run_manifest(entry.subject).run_manifest_id
            for entry in funnel.shortlist
        },
        exposures=characteristics,
        predictions={},
    )

    clipped = ranking.flagged("score_is_a_winsorization_bound")
    assert {item.subject for item in clipped} == block, (
        "the block is carried in, because on this tier the values no longer show it"
    )
    assert len({item.components[0].value for item in clipped}) == len(block), (
        "one processed value became distinct residuals, so the ordering inside the block is by "
        "industry and size alone"
    )
    assert len({stored[item.subject] for item in clipped}) == 1

    exposed = [item.exposure for item in clipped]
    assert all(item is not None for item in exposed)
    assert len({item.industry_code for item in exposed if item is not None}) == len(block), (
        "and the exposure is what says so on one candidate rather than across a whole partition"
    )


def test_a_neutralized_tier_ranking_needs_the_cross_section_it_was_neutralised_against() -> None:
    """Refused on that tier, flagged on the other two, and the difference is the reason.

    On `neutralized` an industry mean and a size slope have already been subtracted out of every
    value, so a ranking that cannot say what was removed is one whose ordering has no readable
    explanation. On `raw` and `processed` nothing was projected out, so an absent cross section
    is an unmeasured characteristic and a per-candidate flag.
    """
    spec = _spec(tier="neutralized")
    funnel = CrossSectionScreen(spec, execution=AShareExecutionPolicy()).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[
            ComponentCrossSection(
                factor_id=ALPHA.factor_id,
                values=tuple(
                    (subject, value, "neutralized") for subject, value in _straight_market().items()
                ),
                clipped_subjects=frozenset(),
            )
        ],
        bars=_bars(),
    )

    with pytest.raises(CandidateRankingError, match="a neutralized-tier ranking needs"):
        rank_candidates(
            manifest=_manifest(spec=spec),
            funnel=funnel,
            signals={entry.subject: _signal(entry.subject) for entry in funnel.shortlist},
            run_manifest_ids={
                entry.subject: _run_manifest(entry.subject).run_manifest_id
                for entry in funnel.shortlist
            },
            exposures=None,
            predictions={},
        )

    processed_tier = _rank(exposures=None)
    assert "exposure_is_not_measured" in processed_tier.candidates[0].risk_flags


def test_an_exposure_cross_section_read_on_another_day_is_refused() -> None:
    """`IndustryMarketCapCrossSection` is stamped with the instant it was read at, and
    `apply_factor_neutralization` refuses one whose `as_of` is not the panel's. The same rule
    here, for the same reason: a characteristic read on another day is a different security's
    answer to this one's question."""
    with pytest.raises(CandidateRankingError, match="exposure cross section is as of"):
        _rank(
            exposures=_characteristics(
                {subject: ("801010.SI", 1_000.0) for subject in TWELVE},
                as_of=AS_OF.replace(day=11),
            )
        )


# --------------------------------------------------------------------------------------------
# 风险标记
# --------------------------------------------------------------------------------------------


def test_the_two_shipped_gates_read_disjoint_subsets_of_an_open_flag_set() -> None:
    """The measurement behind this contract having a closed flag set of its own.

    `SignalFrame.risk_flags` has no vocabulary. `RiskGate` reads five strings, `agents/committee`
    treats three others as severe, the two are disjoint, and `committee-disagreement` -- which
    the committee raises itself -- is in neither, so a signal it flagged passes the runtime gate.
    Read off the real classes rather than restated, so a change to either is red here.
    """
    gate_flags = RiskGate._blocking_flags | RiskGate._reducing_flags
    severe = {"regulatory", "data-quality", "suspension"}

    assert gate_flags == {
        "future_data",
        "look_ahead_violation",
        "redistribution_unknown",
        "source_uri_missing",
        "revised_after_initial_availability",
    }
    assert gate_flags & severe == set()
    assert "committee-disagreement" not in gate_flags | severe

    committee_source = Path(DeliberationCommittee.__module__.replace(".", "/") + ".py")
    assert (ROOT / "src" / committee_source).exists()
    assert (
        RiskGate().evaluate(
            SignalFrame(
                subject=TWELVE[0],
                as_of=AS_OF,
                direction="bullish",
                strength=0.4,
                confidence=0.7,
                horizon=HORIZON,
                evidence_ids=("evd_000000000000000000000001",),
                risk_flags=("committee-disagreement",),
            )
        )
        == "pass"
    ), "a flag the committee raises itself reaches the runtime gate and passes it"


def test_the_declared_risk_flags_are_the_closed_set_this_contract_reports() -> None:
    """The vocabulary as an equality, and the order as a tuple, because both are asserted on."""
    assert {
        "score_is_a_winsorization_bound",
        "rank_shares_its_score",
        "exposure_is_not_measured",
        "industry_exposure_is_backfilled",
        "evidence_plane_abstained",
        "evidence_plane_is_bearish",
    } == RANKING_RISK_FLAG_CODES
    assert RANKING_RISK_FLAG_ORDER == (
        "score_is_a_winsorization_bound",
        "rank_shares_its_score",
        "exposure_is_not_measured",
        "industry_exposure_is_backfilled",
        "evidence_plane_abstained",
        "evidence_plane_is_bearish",
    )
    assert "capacity" not in " ".join(RANKING_RISK_FLAG_ORDER), (
        "capacity needs a declared participation_cap and a session turnover this contract does "
        "not take; a flag every candidate carried would be a branch no input could fail"
    )


def test_each_risk_flag_separates_and_they_are_reported_in_the_declared_order() -> None:
    """One market, six candidates, and every flag decided by a different security's own facts.

    A fixture where two flags always fired together would let one of them be deleted with the
    suite green. So the clipped name, the tied pair, the unclassified name, the backfilled name,
    the abstainer and the bear are all different securities.
    """
    market: dict[str, float | None] = {
        subject: 20.0 - index for index, subject in enumerate(TWELVE)
    }
    market[TWELVE[1]] = market[TWELVE[2]] = 18.0
    funnel = _funnel(spec=_spec(shortlist_size=6), values=market, clipped=frozenset({TWELVE[0]}))
    assert [entry.subject for entry in funnel.shortlist] == list(TWELVE[:6])

    exposures = _characteristics(
        {
            subject: ("801010.SI", 1_000.0 * (index + 1))
            for index, subject in enumerate(TWELVE)
            if subject != TWELVE[3]
        },
        backfilled=frozenset({TWELVE[4]}),
        without_industry=(TWELVE[3],),
    )
    ranking = _rank(
        funnel,
        manifest=_manifest(spec=_spec(shortlist_size=6)),
        directions={TWELVE[5]: "abstain", TWELVE[4]: "bearish"},
        exposures=exposures,
    )

    flags = {item.subject: item.risk_flags for item in ranking.candidates}
    assert flags == {
        TWELVE[0]: ("score_is_a_winsorization_bound",),
        TWELVE[1]: ("rank_shares_its_score",),
        TWELVE[2]: ("rank_shares_its_score",),
        TWELVE[3]: ("exposure_is_not_measured",),
        TWELVE[4]: ("industry_exposure_is_backfilled", "evidence_plane_is_bearish"),
        TWELVE[5]: ("evidence_plane_abstained",),
    }
    assert ranking.flagged("rank_shares_its_score") == tuple(
        item for item in ranking.candidates if item.subject in {TWELVE[1], TWELVE[2]}
    )
    with pytest.raises(CandidateRankingError, match="is not a declared ranking risk flag"):
        ranking.flagged("capacity")  # type: ignore[arg-type]


def test_a_tie_the_cut_left_outside_still_flags_the_last_candidate() -> None:
    """The separating fixture for `rank_shares_its_score`'s second disjunct.

    Within the shortlist a tie is visible from the entries themselves. The **last** candidate can
    be tied with tradeable names the cut left outside, and only `CrossSectionFunnel
    .tied_at_the_cut` knows about those -- so a rule written from the shortlist alone reports a
    clean cut for exactly the boundary the funnel exists to warn about. Here the boundary score
    is shared by one shortlisted name and one excluded one, so the first disjunct is false and
    the flag must still fire.
    """
    market: dict[str, float | None] = {
        subject: 20.0 - index for index, subject in enumerate(TWELVE)
    }
    market[TWELVE[3]] = market[TWELVE[2]]
    funnel = _funnel(values=market)

    assert [entry.subject for entry in funnel.shortlist] == list(TWELVE[:3])
    assert funnel.tied_at_the_cut == 2, "the boundary score is shared with a name outside the cut"
    boundary = funnel.shortlist[-1]
    inside = [entry.score for entry in funnel.shortlist]
    assert inside.count(boundary.score) == 1, (
        "and it is shared with nobody inside, so only the funnel's own count can see it"
    )

    ranking = _rank(
        funnel,
        exposures=_characteristics({subject: ("801010.SI", 1_000.0) for subject in TWELVE}),
    )
    assert ranking.candidates[-1].risk_flags == ("rank_shares_its_score",)
    assert ranking.candidates[0].risk_flags == (), (
        "and no other candidate is flagged, so the fixture separates the two disjuncts"
    )


def test_the_signals_own_flags_travel_whole_beside_this_contracts_closed_set() -> None:
    """Neither vocabulary is a lossy summary of the other, so both are readable on one candidate."""
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    flagged = SignalFrame(
        subject=subjects[0],
        as_of=AS_OF,
        direction="bearish",
        strength=-0.4,
        confidence=0.7,
        horizon=HORIZON,
        evidence_ids=("evd_000000000000000000000001",),
        risk_flags=("committee-disagreement", "source_uri_missing"),
        confirmation_conditions=("volume confirms",),
        invalidation_conditions=("closes below the event-day low",),
    )
    signals = {subject: _signal(subject) for subject in subjects}
    signals[subjects[0]] = flagged

    ranking = rank_candidates(
        manifest=_manifest(),
        funnel=funnel,
        signals=signals,
        run_manifest_ids={subject: _run_manifest(subject).run_manifest_id for subject in subjects},
        exposures=None,
        predictions={},
    )
    candidate = ranking.candidates[0]

    assert candidate.signal_risk_flags == ("committee-disagreement", "source_uri_missing")
    assert candidate.risk_flags == ("exposure_is_not_measured", "evidence_plane_is_bearish")
    assert set(candidate.signal_risk_flags) & RANKING_RISK_FLAG_CODES == set()
    assert candidate.direction == "bearish"
    assert candidate.confidence == 0.7
    assert candidate.horizon == HORIZON
    assert candidate.evidence_ids == ("evd_000000000000000000000001",)
    assert candidate.signal.confirmation_conditions == ("volume confirms",)
    assert candidate.signal.invalidation_conditions == ("closes below the event-day low",)


# --------------------------------------------------------------------------------------------
# 预测
# --------------------------------------------------------------------------------------------


def test_predictions_are_all_or_nothing_across_one_ranking() -> None:
    """A list in which some names carry a model number and others do not is ordered on two
    statistics -- `ScoreCoverage.incomplete_components`' argument, one plane up.

    Refused by the builder and by the record, because a `CandidateRanking` is a public frozen
    dataclass a caller can construct directly.
    """
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    prediction = CandidatePrediction(
        model_artifact_id="mdl_pending_v2_p4_011", predicted_value=0.02, horizon=HORIZON
    )

    whole = _rank(funnel, predictions=dict.fromkeys(subjects, prediction))
    assert [item.prediction for item in whole.candidates] == [prediction] * 3

    with pytest.raises(CandidateRankingError, match="predictions are all or nothing"):
        _rank(funnel, predictions={subjects[0]: prediction})

    partial = [
        (
            item
            if index > 0
            else RankedCandidate(
                subject=item.subject,
                rank=item.rank,
                score=item.score,
                components=item.components,
                fill=item.fill,
                signal=item.signal,
                run_manifest_id=item.run_manifest_id,
                exposure=item.exposure,
                prediction=None,
                risk_flags=item.risk_flags,
            )
        )
        for index, item in enumerate(whole.candidates)
    ]
    with pytest.raises(CandidateRankingError, match="predictions are all or nothing"):
        CandidateRanking(
            manifest=whole.manifest,
            funnel=funnel,
            candidates=tuple(partial),
            unresearched=(),
        )


def test_this_build_produces_no_model_prediction_and_says_so_rather_than_defaulting() -> None:
    """`V2-P4-011` through `V2-P4-017` are where a prediction comes from and none has landed.

    So `rank_candidates` takes the mapping with no default and every candidate this build can
    produce carries `None` -- stated by a caller rather than defaulted into.
    """
    ranking = _rank()
    assert [item.prediction for item in ranking.candidates] == [None, None, None]

    with pytest.raises(TypeError, match="predictions"):
        rank_candidates(  # type: ignore[call-arg]
            manifest=_manifest(),
            funnel=_funnel(),
            signals={},
            run_manifest_ids={},
            exposures=None,
        )


def test_a_prediction_over_another_window_is_refused() -> None:
    """A number about a different window is a number about a different question."""
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    with pytest.raises(CandidateRankingError, match="prediction is over '10d'"):
        _rank(
            funnel,
            predictions=dict.fromkeys(
                subjects,
                CandidatePrediction(
                    model_artifact_id="mdl_probe", predicted_value=0.02, horizon="10d"
                ),
            ),
        )
    with pytest.raises(ValidationError):
        CandidatePrediction(model_artifact_id="mdl_probe", predicted_value=0.02, horizon="3m")


# --------------------------------------------------------------------------------------------
# 可交易性 and manifest
# --------------------------------------------------------------------------------------------


def test_the_execution_verdict_is_the_policys_own_and_the_censuses_travel_with_the_funnel() -> None:
    """D16's 可交易性: the per-candidate `ExecutionResult` and the funnel's two censuses.

    Carried rather than re-derived, `ShortlistEntry.fill`'s own rule -- this contract is not a
    second authority on whether an order fills.
    """
    ranking = _rank()
    entry = ranking.funnel.shortlist[0]
    candidate = ranking.candidates[0]

    assert candidate.fill is entry.fill
    assert candidate.fill.status == "filled"
    assert candidate.fill.side == "buy"
    assert ranking.funnel.tradeability.tradeable_count == 12
    assert ranking.funnel.scores.universe_count == 12
    assert ranking.funnel.tradeability.scored_count == 12


def test_a_candidate_carrying_a_rejected_execution_is_not_constructible() -> None:
    """Only a name the market would have sold reaches a shortlist, so only one reaches a ranking."""
    ranking = _rank()
    good = ranking.candidates[0]
    refused = good.fill.model_copy(update={"status": "rejected", "reason": "limit-up"})

    with pytest.raises(CandidateRankingError, match="is ranked carrying a rejected execution"):
        RankedCandidate(
            subject=good.subject,
            rank=good.rank,
            score=good.score,
            components=good.components,
            fill=refused,
            signal=good.signal,
            run_manifest_id=good.run_manifest_id,
            exposure=None,
            prediction=None,
            risk_flags=(),
        )


def test_each_candidate_carries_the_content_address_v2_p4_025_gave_its_run() -> None:
    """`run_manifest_id` rather than a copy of `config_digest` and `random_seed`.

    Carrying the address inherits *every* declared run input at once, including ones added later
    -- `DecisionLedger.run_manifest_id`'s own argument -- and `RUN_MANIFEST_ID_PATTERN` is what
    stops a placeholder taking its place. Driven off a real `RunManifest` in both directions, and
    the sentinel is that changing a run's declared input moves the string the candidate holds.
    """
    ranking = _rank()
    candidate = ranking.candidates[0]
    manifest = _run_manifest(candidate.subject)

    assert candidate.run_manifest_id == manifest.run_manifest_id
    assert (
        manifest.model_copy(update={"random_seed": 8}).run_manifest_id != candidate.run_manifest_id
    ), "sentinel: roadmap section 9 measured that random_seed reached no identity at all"
    assert (
        manifest.model_copy(update={"finished_at": BUILT_AT.replace(year=2027)}).run_manifest_id
        == candidate.run_manifest_id
    )

    with pytest.raises(CandidateRankingError, match="which is not stable_model_id"):
        RankedCandidate(
            subject=candidate.subject,
            rank=candidate.rank,
            score=candidate.score,
            components=candidate.components,
            fill=candidate.fill,
            signal=candidate.signal,
            run_manifest_id="the run I did on Tuesday",
            exposure=None,
            prediction=None,
            risk_flags=(),
        )


def test_a_researched_name_with_no_run_manifest_id_is_refused() -> None:
    """A conclusion with no reproducible declaration behind it is what roadmap section 9 measured
    `RunManifest` to have been missing."""
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)

    with pytest.raises(CandidateRankingError, match="carry a signal and no run_manifest_id"):
        rank_candidates(
            manifest=_manifest(),
            funnel=funnel,
            signals={subject: _signal(subject) for subject in subjects},
            run_manifest_ids={subjects[0]: _run_manifest(subjects[0]).run_manifest_id},
            exposures=None,
            predictions={},
        )


# --------------------------------------------------------------------------------------------
# The record's own arithmetic
# --------------------------------------------------------------------------------------------


def test_the_ranking_does_not_re_rank_and_a_moved_rank_or_score_is_refused() -> None:
    """The ranks are `CrossSectionScreen.select`'s and are never recomputed.

    A ranking that re-sorted by confidence would be a third ordering wearing the funnel's name,
    and every measured caveat on the funnel's order would silently stop applying to it. Driven by
    a confidence that runs *against* the score, so a contract that re-sorted would produce a
    different first candidate.
    """
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    signals = {
        subject: SignalFrame(
            subject=subject,
            as_of=AS_OF,
            direction="bullish",
            strength=0.4,
            confidence=0.3 + 0.2 * index,
            horizon=HORIZON,
            evidence_ids=("evd_000000000000000000000001",),
        )
        for index, subject in enumerate(subjects)
    }
    ranking = rank_candidates(
        manifest=_manifest(),
        funnel=funnel,
        signals=signals,
        run_manifest_ids={subject: _run_manifest(subject).run_manifest_id for subject in subjects},
        exposures=None,
        predictions={},
    )

    assert [item.subject for item in ranking.candidates] == list(subjects)
    assert [item.rank for item in ranking.candidates] == [1, 2, 3]
    with pytest.raises(CandidateRankingError, match="not in the funnel's own order"):
        CandidateRanking(
            manifest=ranking.manifest,
            funnel=funnel,
            candidates=tuple(reversed(ranking.candidates)),
            unresearched=(),
        )
    assert [item.confidence for item in ranking.candidates] == [0.3, 0.5, 0.7], (
        "the confidences run against the score, so a contract that re-sorted would reverse this"
    )

    first = ranking.candidates[0]
    with pytest.raises(CandidateRankingError, match="is a ranking of something else"):
        CandidateRanking(
            manifest=ranking.manifest,
            funnel=funnel,
            candidates=(
                RankedCandidate(
                    subject=first.subject,
                    rank=first.rank,
                    score=first.score + 1.0,
                    components=first.components,
                    fill=first.fill,
                    signal=first.signal,
                    run_manifest_id=first.run_manifest_id,
                    exposure=None,
                    prediction=None,
                    risk_flags=(),
                ),
                *ranking.candidates[1:],
            ),
            unresearched=(),
        )


def test_every_shortlisted_name_is_ranked_or_named_unresearched() -> None:
    """A ranking that returned only the researched names could not be told from a shorter cut.

    So the two collections partition the shortlist exactly, and dropping one name fails the
    record's own arithmetic rather than reporting a plausible total.
    """
    funnel = _funnel()
    skipped = funnel.shortlist[1].subject
    ranking = _rank(funnel, unresearched=(skipped,))
    _refuse_an_unsorted_unresearched_list()

    assert ranking.unresearched == (skipped,)
    assert ranking.candidate_count == 2
    assert [item.rank for item in ranking.candidates] == [1, 3], (
        "the ranks are the funnel's, so an unresearched name leaves a gap rather than a renumber"
    )
    assert ranking.researched_rate == 2 / 3

    with pytest.raises(CandidateRankingError, match="a ranking that does not add up"):
        CandidateRanking(
            manifest=ranking.manifest,
            funnel=funnel,
            candidates=ranking.candidates,
            unresearched=(),
        )
    with pytest.raises(CandidateRankingError, match="it is ascending by subject"):
        CandidateRanking(
            manifest=ranking.manifest,
            funnel=funnel,
            candidates=(),
            unresearched=tuple(reversed([entry.subject for entry in funnel.shortlist])),
        )


def test_an_answer_about_a_name_the_funnel_did_not_shortlist_is_a_malformed_call() -> None:
    """A ranking is the shortlist's own answers, in all three of the mappings that carry one."""
    funnel = _funnel()
    subjects = tuple(entry.subject for entry in funnel.shortlist)
    outsider = TWELVE[-1]
    assert outsider not in subjects

    with pytest.raises(CandidateRankingError, match=r"signals carries \['000012.SZ'\]"):
        rank_candidates(
            manifest=_manifest(),
            funnel=funnel,
            signals={outsider: _signal(outsider)},
            run_manifest_ids={},
            exposures=None,
            predictions={},
        )
    with pytest.raises(CandidateRankingError, match="run_manifest_ids carries"):
        rank_candidates(
            manifest=_manifest(),
            funnel=funnel,
            signals={},
            run_manifest_ids={outsider: _run_manifest(outsider).run_manifest_id},
            exposures=None,
            predictions={},
        )
    with pytest.raises(CandidateRankingError, match="predictions carries"):
        rank_candidates(
            manifest=_manifest(),
            funnel=funnel,
            signals={},
            run_manifest_ids={},
            exposures=None,
            predictions={
                outsider: CandidatePrediction(
                    model_artifact_id="mdl", predicted_value=0.0, horizon=HORIZON
                )
            },
        )


def test_a_funnel_with_no_shortlist_is_a_ranking_with_no_candidates_and_the_funnels_own_code() -> (
    None
):
    """A code rather than a refusal, `FunnelCoverage`'s stated rule.

    A caller looping over a year of `as_of`s has to be able to keep going past a market that
    scored nobody, and a ranking that raised would stop the loop on a fact about the market.
    """
    degenerate = _funnel(values=dict.fromkeys(TWELVE, 5.0))
    assert degenerate.coverage == "degenerate_scores"

    ranking = rank_candidates(
        manifest=_manifest(),
        funnel=degenerate,
        signals={},
        run_manifest_ids={},
        exposures=None,
        predictions={},
    )

    assert ranking.coverage == "degenerate_scores"
    assert ranking.candidates == ()
    assert ranking.unresearched == ()
    assert ranking.researched_rate is None, (
        "None rather than 0.0: 'every shortlisted name failed to research' and 'there was nothing "
        "to research' are different findings"
    )
    assert ranking.candidate("000001.SZ") is None
    assert ranking.manifest.tier == "processed"
    assert ranking.manifest.shortlist_size == 3


def test_a_ranking_whose_manifest_describes_another_screen_is_refused() -> None:
    """The four statements a manifest and a funnel make about one screen, held equal in both of
    the places a `CandidateRanking` can be built."""
    funnel = _funnel()
    for spec, message in (
        (_spec(tier="raw"), "declares the 'raw' tier"),
        (_spec(shortlist_size=4), "declares a shortlist of 4"),
    ):
        with pytest.raises(CandidateRankingError, match=message):
            rank_candidates(
                manifest=_manifest(spec=spec),
                funnel=funnel,
                signals={},
                run_manifest_ids={},
                exposures=None,
                predictions={},
            )

    ranking = _rank(funnel)
    with pytest.raises(CandidateRankingError, match="declares a shortlist of 4"):
        CandidateRanking(
            manifest=_manifest(spec=_spec(shortlist_size=4)),
            funnel=funnel,
            candidates=ranking.candidates,
            unresearched=(),
        )


def test_a_candidate_carrying_a_conclusion_about_another_security_is_refused() -> None:
    """A candidate's conclusion is a conclusion about that candidate."""
    ranking = _rank()
    first, second = ranking.candidates[0], ranking.candidates[1]

    with pytest.raises(CandidateRankingError, match="carries a signal about"):
        RankedCandidate(
            subject=first.subject,
            rank=first.rank,
            score=first.score,
            components=first.components,
            fill=first.fill,
            signal=second.signal,
            run_manifest_id=first.run_manifest_id,
            exposure=None,
            prediction=None,
            risk_flags=(),
        )


def test_risk_flags_out_of_the_declared_order_are_refused_rather_than_re_sorted() -> None:
    """Two candidates carrying one set carry it in one order, so a caller diffing two rankings is
    not reading an iteration order as a change."""
    ranking = _rank()
    first = ranking.candidates[0]
    fields: dict[str, Any] = {
        "subject": first.subject,
        "rank": first.rank,
        "score": first.score,
        "components": first.components,
        "fill": first.fill,
        "signal": first.signal,
        "run_manifest_id": first.run_manifest_id,
        "exposure": None,
        "prediction": None,
    }

    assert RankedCandidate(
        **fields, risk_flags=("exposure_is_not_measured", "evidence_plane_abstained")
    ).risk_flags == ("exposure_is_not_measured", "evidence_plane_abstained")

    with pytest.raises(CandidateRankingError, match="they are reported in"):
        RankedCandidate(
            **fields, risk_flags=("evidence_plane_abstained", "exposure_is_not_measured")
        )
    with pytest.raises(CandidateRankingError, match="which repeats"):
        RankedCandidate(
            **fields, risk_flags=("exposure_is_not_measured", "exposure_is_not_measured")
        )


# --------------------------------------------------------------------------------------------
# The panel used by the two-engine reproduction above
# --------------------------------------------------------------------------------------------


def _panel(values: dict[str, float | None]) -> FactorPanel:
    """A hand-built `FactorPanel` over `{subject: value}`, `test_cross_section.py`'s helper.

    Copied rather than imported, for that file's own stated reason for building one by hand: the
    panel is still a real one -- every observation satisfies the contract and the manifest is a
    real `FactorBuildManifest` whose `manifest_id` every row carries -- and importing a private
    helper across two test modules would make either file's fixture the other's dependency.
    """
    subjects = tuple(values)
    manifest = FactorBuildManifest(
        factor_id=REVERSAL_1D.factor_id,
        factor_key=REVERSAL_1D.key,
        factor_version=REVERSAL_1D.version,
        as_of=AS_OF,
        date_timezone="Asia/Shanghai",
        code_commit=COMMIT,
        direction=REVERSAL_1D.direction,
        lookback_sessions=REVERSAL_1D.lookback_sessions,
        max_window_sessions=REVERSAL_1D.max_window_sessions,
        lookback_periods=None,
        max_window_periods=None,
        subject_count=len(subjects),
        subject_digest=set_digest(subjects),
        universe_count=len(subjects),
        universe_digest=set_digest(subjects),
        observation_digest=cross_section_digest(
            (
                (name, "computed" if value is not None else "input_missing", value)
                for name, value in values.items()
            ),
            prefix="obs",
        ),
        inputs=(
            FactorInputRef(
                dataset="daily",
                year=2026,
                partition_content_hash="bb",
                visible_row_count=len(subjects) * 2,
                withheld_row_count=0,
            ),
        ),
    )
    return FactorPanel(
        definition=REVERSAL_1D,
        manifest=manifest,
        observations=tuple(
            FactorObservation(
                subject=name,
                as_of=AS_OF,
                value=value,
                coverage="computed" if value is not None else "input_missing",
                factor_id=REVERSAL_1D.factor_id,
                manifest_id=manifest.manifest_id,
                input_row_count=2 if value is not None else 1,
                input_session_first=None,
                input_session_last=None,
            )
            for name, value in values.items()
        ),
        built_at=BUILT_AT,
        input_provenance=(),
    )


def _refuse_an_unsorted_unresearched_list() -> None:
    """`rank_candidates` sorts the unresearched names, in a market where that is not the identity.

    Split out and driven off an **ascending** market on purpose: in the ordinary fixture the
    shortlist is already in ascending subject order, so the funnel's order and the sorted one are
    the same tuple and a builder that returned either would pass. Here the composite runs *with*
    the subject code, so the shortlist is `000012.SZ, 000011.SZ, 000010.SZ` and the two orders
    are different -- which is the only shape in which the `sorted()` call is measurable.

    Called from `test_every_shortlisted_name_is_ranked_or_named_unresearched` rather than being a
    test of its own, because it is the second half of that test's claim and not a second claim.
    """
    ascending: dict[str, float | None] = {
        subject: float(index + 1) for index, subject in enumerate(TWELVE)
    }
    funnel = _funnel(values=ascending)
    shortlisted = [entry.subject for entry in funnel.shortlist]
    assert shortlisted == [TWELVE[11], TWELVE[10], TWELVE[9]]
    assert shortlisted != sorted(shortlisted), (
        "the fixture has to separate the funnel's order from the sorted one, or the sort is "
        "unmeasurable"
    )

    ranking = _rank(funnel, unresearched=(TWELVE[11], TWELVE[10]))

    assert ranking.unresearched == (TWELVE[10], TWELVE[11])
    assert [item.subject for item in ranking.candidates] == [TWELVE[9]]
