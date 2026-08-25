"""The two-stage cross-sectional funnel (`V2-P4-004`), held to numbers rather than to shapes.

Five properties this file exists to hold, each of which is a place a shortlist silently becomes
a list of something else:

1. **`shortlist_size` is calibrated against a measured quantity and not against taste.** The
   shipped 1% winsorization assigns `(n - 1) - floor((n - 1) * 0.99)` names one and the same
   bound, so a top-`N` cut at or below that count is drawn from a tie.
   `test_the_shipped_transform_ties_the_top_and_the_neutralisation_hides_the_same_block` drives
   the real engine and the real neutralisation over one cross section and requires the processed
   tier to carry **one** distinct value across the block and the neutralised tier to carry **as
   many as the block has** -- the offline reproduction of the whole-market measurement in
   `cross_section.py`'s docstring.
   `test_a_cut_inside_the_clip_block_is_refused_and_one_name_above_it_is_not` is the separating
   pair: the same market, one name's difference in `N`, and two different codes.
2. **The two stages are two.** Their censuses are built so that no scored security is missing
   from either, and `test_a_funnel_refused_before_stage_two_reports_every_scored_name_as
   _unoffered` pins the distinction between "the market refused everybody" and "nobody asked".
3. **The registry gate is the one `AShareExecutionPolicy` says a caller owes it.**
   `test_the_registry_gate_closes_the_gap_the_execution_policy_discloses` drives the same bar
   through both and requires the policy to fill it and this funnel to refuse it -- so the
   assertion cannot be satisfied by a fixture where the two agree anyway.
4. **A refusal is a code and never an exception, and the codes have a declared order.**
   `test_degenerate_scores_is_decided_before_the_clip_block` builds a market that satisfies both
   conditions, so the order is the only thing that decides it.
5. **Every number reported separates from its neighbour.** The census cells are given distinct
   values on purpose, and the composite fixture is built so that scoring over the components a
   security happens to have would put a *different* name first.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import grimp
import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.cross_section import (
    CROSS_SECTION_LIMITATION_CODES,
    FUNNEL_COVERAGE_ORDER,
    KNOWN_CROSS_SECTION_LIMITATIONS,
    MAXIMUM_SHORTLIST,
    MINIMUM_SHORTLIST,
    REFUSED_VERDICT_ORDER,
    SCORE_COVERAGE_ORDER,
    TRADEABILITY_VERDICT_ORDER,
    ComponentCrossSection,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
    TwoStageFunnelError,
    oriented_value,
    upper_clip_block,
)
from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import FactorICSpec
from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS, BatchResearchTask, BatchTaskItem
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
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformSpec,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    REVERSAL_1D,
    FactorPanel,
    apply_factor_transform,
)
from openalpha_cn.panel_neutralization import INDUSTRY_AND_SIZE, apply_factor_neutralization

AS_OF: Final[datetime] = datetime(2026, 6, 12, 4, 0, tzinfo=UTC)
SESSION: Final[date] = date(2026, 6, 12)
BUILT_AT: Final[datetime] = datetime(2026, 6, 13, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"
CAPITAL: Final[Decimal] = Decimal("100000")
"""Enough notional at a ¥10 close for the ¥5 minimum commission never to bind; the one test that
varies it says so."""


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def _definition(
    key: str = "probe_alpha", *, direction: str = "higher_is_better"
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction=direction,  # type: ignore[arg-type]
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


ALPHA: Final[FactorDefinition] = _definition()
BETA: Final[FactorDefinition] = _definition("probe_beta")
LOWER: Final[FactorDefinition] = _definition("probe_lower", direction="lower_is_better")


def _bar(
    subject: str,
    *,
    close: float = 10.0,
    previous_close: float = 10.0,
    low: float | None = None,
    high: float | None = None,
    board: str = "main",
    suspended: bool = False,
    published_band: tuple[float, float] | None = (11.0, 9.0),
) -> MarketBar:
    fields: dict[str, Any] = {}
    if published_band is not None:
        fields = {
            "up_limit": Decimal(str(published_band[0])),
            "down_limit": Decimal(str(published_band[1])),
        }
    price = Decimal(str(round(close, 2)))
    return MarketBar(
        subject=subject,
        trade_date=SESSION,
        board=board,  # type: ignore[arg-type]
        previous_close=Decimal(str(round(previous_close, 2))),
        open=price,
        high=price if high is None else Decimal(str(round(high, 2))),
        low=price if low is None else Decimal(str(round(low, 2))),
        close=price,
        suspended=suspended,
        is_st=False,
        **fields,
    )


def _component(
    definition: FactorDefinition,
    values: dict[str, float | None],
    *,
    coverage: str = "processed",
    codes: dict[str, str] | None = None,
    clipped: frozenset[str] = frozenset(),
) -> ComponentCrossSection:
    marks = codes or {}
    return ComponentCrossSection(
        factor_id=definition.factor_id,
        values=tuple(
            (subject, value, marks.get(subject, coverage)) for subject, value in values.items()
        ),
        clipped_subjects=clipped,
    )


def _spec(
    *components: tuple[FactorDefinition, float],
    tier: str = "processed",
    shortlist_size: int = 3,
    position_capital: Decimal = CAPITAL,
) -> ShortlistSpec:
    declared = components or ((ALPHA, 1.0),)
    return ShortlistSpec(
        components=tuple(
            ScoreComponent(definition=definition, weight=weight) for definition, weight in declared
        ),
        tier=tier,  # type: ignore[arg-type]
        shortlist_size=shortlist_size,
        position_capital=position_capital,
    )


def _screen(spec: ShortlistSpec | None = None) -> CrossSectionScreen:
    return CrossSectionScreen(spec or _spec(), execution=AShareExecutionPolicy())


TWELVE: Final[tuple[str, ...]] = tuple(code(index) for index in range(1, 13))
"""Twelve names, and the count is load-bearing: with a shortlist of three it leaves nine outside
the cut, so a census cell that swallowed the shortlist would not read like the total."""


def _straight_market() -> dict[str, float]:
    """A cross section whose scores are distinct and descending in the code order."""
    return {subject: 12.0 - index for index, subject in enumerate(TWELVE)}


def _bars(subjects: tuple[str, ...] = TWELVE) -> dict[str, MarketBar]:
    return {subject: _bar(subject) for subject in subjects}


# --------------------------------------------------------------------------------------------
# The declared policy
# --------------------------------------------------------------------------------------------


def test_a_raw_tier_screen_refuses_a_second_component() -> None:
    """Raw values carry each factor's own units, so summing two of them adds unlike quantities.

    The one rule in this contract that refuses a configuration a caller could otherwise write,
    and it is narrow on purpose: one raw component is legal, because the composite is then that
    factor and monotone in it.
    """
    with pytest.raises(ValidationError, match="raw factor values carry each factor's own units"):
        _spec((ALPHA, 1.0), (BETA, 1.0), tier="raw")

    single = _spec((ALPHA, 1.0), tier="raw")
    assert single.tier == "raw"


def test_a_repeated_factor_id_in_the_composite_is_refused() -> None:
    """A factor declared twice is a weight expressed in two places, and the two can disagree."""
    with pytest.raises(ValidationError, match="repeats a factor_id"):
        _spec((ALPHA, 1.0), (ALPHA, 2.0))


def test_the_shortlist_ceiling_is_the_batch_the_evidence_plane_will_accept() -> None:
    """`MAXIMUM_SHORTLIST` must be a shortlist `BatchResearchTask.items` will actually hold.

    A shortlist longer than the batch the second stage can be handed is one that cannot enter
    `run_cycle` whatever anybody intends by it. This test is the tripwire that fires when the
    batch cap moves, and `V2-P4-019` is the change it was written for -- it did fire, and this
    is the amended assertion.

    **Held as an equality again since `V2-P4-031`, and the equality is the point.** The two
    were equal at 1,000; `V2-P4-019` raised `MAX_BATCH_ITEMS` to 10,000 so a whole market
    (5,545 listed on 2026-08-14, per that same issue's measurement) could be expressed at all,
    was not permitted to touch `backtest/`, and left this side at 1,000 with the assertion
    weakened to `<=`. That weaker form is true of every number from 1 to 10,000, so it stopped
    being able to say the thing this test is named for -- the ceiling was no longer *restated*
    from the batch, it merely did not exceed it, and the wall blocking a whole-market shortlist
    had moved here without anything saying so.

    **Why 10,000 and not a smaller measured number.** The row asking for this warned against
    copying the batch cap the way the cross section's *floor* cannot be copied -- `V2-P4-004`
    measured `N >= 57` from the factory winsorization, so the lower bound is a fact about the
    statistics rather than about the plane above. The upper bound is the opposite kind of
    number, and the difference is measurable rather than argued: nothing in this module has a
    view about how long a shortlist should be, and the run-time rule that does is not a
    constant at all -- a `shortlist_size` at or above the tradeable count is answered with
    `cut_exceeds_the_cross_section`, a named coverage code, not a refusal (`MINIMUM_SHORTLIST`
    records the same reasoning for the floor). So the only thing this ceiling can honestly
    say is "a batch the evidence plane will accept", and that is `MAX_BATCH_ITEMS` exactly.
    Any smaller number would be this module inventing a limit it has no measurement for, and
    would put the wall back where `V2-P4-031` found it.
    """
    request = ResearchRunRequest(
        run_id="run-0001",
        subject="000001.SZ",
        as_of=AS_OF,
        mode="backtest",
        code_commit=COMMIT,
        config_digest="0" * 64,
        random_seed=1,
        evidence=(),
    )
    at_the_ceiling = BatchResearchTask(
        batch_id="batch-0001",
        items=tuple(BatchTaskItem(request=request) for _ in range(MAXIMUM_SHORTLIST)),
        status="queued",
        max_concurrency=1,
        created_at=AS_OF,
        updated_at=AS_OF,
    )
    assert len(at_the_ceiling.items) == MAXIMUM_SHORTLIST
    assert MAXIMUM_SHORTLIST == MAX_BATCH_ITEMS, (
        f"MAXIMUM_SHORTLIST is {MAXIMUM_SHORTLIST} and MAX_BATCH_ITEMS is {MAX_BATCH_ITEMS}. "
        "This ceiling is a restatement of the batch the evidence plane accepts, not a "
        "judgement of its own, so the two move together or the shortlist face becomes the "
        "wall (V2-P4-031)"
    )

    # The batch's own ceiling still refuses one item past itself. Asserted against the
    # constant rather than a literal, so this half keeps testing the batch's edge wherever
    # that edge moves to next, instead of pinning a number that has to be retyped.
    with pytest.raises(ValidationError, match="at most 10000 items"):
        BatchResearchTask(
            batch_id="batch-0002",
            items=tuple(BatchTaskItem(request=request) for _ in range(MAX_BATCH_ITEMS + 1)),
            status="queued",
            max_concurrency=1,
            created_at=AS_OF,
            updated_at=AS_OF,
        )

    assert MINIMUM_SHORTLIST == 1
    with pytest.raises(ValidationError):
        _spec(shortlist_size=MAXIMUM_SHORTLIST + 1)


def test_every_declared_limitation_code_is_the_registrys_own_set() -> None:
    """The registry, as an equality against a set literal in executable code.

    Equality rather than membership, `KNOWN_ADJUSTMENT_LIMITATIONS`' form: a membership assertion
    can see a code that was renamed and never a code that was removed.
    """
    assert {
        "the_shortlist_is_not_a_ranking_of_expected_return",
        "the_hard_filter_is_a_correctness_gate_and_not_a_sizing_gate",
        "the_filter_answers_for_the_pricing_session_and_not_the_acting_one",
        "a_neutralised_tier_orders_the_clip_block_by_industry_and_size",
        "an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy",
        "no_capacity_constraint_is_applied_to_the_shortlist",
        "the_cut_is_broken_by_subject_code_when_two_scores_tie",
    } == CROSS_SECTION_LIMITATION_CODES
    assert len(KNOWN_CROSS_SECTION_LIMITATIONS) == len(CROSS_SECTION_LIMITATION_CODES)
    assert all(limitation.detail.strip() for limitation in KNOWN_CROSS_SECTION_LIMITATIONS)


def test_the_two_stage_funnel_cannot_reach_the_composition_root_that_owns_run_cycle() -> None:
    """The row's "not in `run_cycle`", as a live import-graph question rather than a docstring.

    `ResearchEngine.run_cycle` is in `openalpha_cn.runtime.engine` and writes through
    `openalpha_cn.storage`. Both are forbidden to this module by `lint-imports`; this asserts the
    same fact against the graph so that a reader of this file sees the enforcement, and the
    sentinel below keeps it from being vacuous.
    """
    graph = grimp.build_graph("openalpha_cn")

    for plane in ("openalpha_cn.runtime", "openalpha_cn.storage", "openalpha_cn.panel"):
        assert not graph.direct_import_exists(
            importer="openalpha_cn.backtest.cross_section", imported=plane, as_packages=True
        ), f"the cross-sectional funnel must not reach {plane}"

    assert graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage", as_packages=True
    ), "sentinel: run_cycle's own module must reach the store, or the assertions above prove none"


# --------------------------------------------------------------------------------------------
# Calibrating N: the clip block, driven through the real engine
# --------------------------------------------------------------------------------------------


def _panel(values: dict[str, float | None]) -> FactorPanel:
    """A hand-built `FactorPanel` over `{subject: value}`, `test_factor_transform_rules.py`'s.

    Built here rather than through `compute_factor` for that file's stated reason: every use of
    it below is about what happens to a *cross section of numbers*, and the panel is still a real
    one -- every observation satisfies the contract and the manifest is a real
    `FactorBuildManifest` whose `manifest_id` every row carries.
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


def _distinct_market(size: int) -> dict[str, float | None]:
    """`size` securities with strictly increasing, distinct values."""
    return {code(index + 1): float(index + 1) for index in range(size)}


@pytest.mark.parametrize("size", [2, 3, 10, 100, 101, 120, 201, 301, 500])
def test_the_clip_block_arithmetic_is_the_transform_engines_own_clip_count(size: int) -> None:
    """`upper_clip_block` must reproduce what `apply_factor_transform` actually clipped.

    `cross_section.py` restates the arithmetic because it is a standard-library leaf over a
    module on the panel plane, and a restated rule that nobody drives is the drift this
    repository has paid for repeatedly. Driven at nine cross-section sizes, including the two
    either side of 100 and the two either side of 101, where `ceil` changes answer.
    """
    spec = FactorTransformSpec(
        key="probe",
        version=1,
        winsorization=WinsorizationPolicy(
            method="quantile", lower_quantile=0.01, upper_quantile=0.99
        ),
        standardization="zscore",
        missing_values=MissingValuePolicy(
            not_in_universe="exclude",
            insufficient_history="exclude",
            ambiguous_filing="exclude",
            input_missing="exclude",
            undefined_value="exclude",
        ),
        min_cross_section=1,
    )
    processed = apply_factor_transform(
        _panel(_distinct_market(size)), spec, code_commit=COMMIT, built_at=BUILT_AT
    )

    assert processed.statistics.participant_count == size
    assert processed.statistics.winsorized_high_count == upper_clip_block(size, 0.99)


def test_the_clip_block_is_one_percent_of_the_market_and_the_prd_lower_bound_is_inside_it() -> None:
    """The calibration in one assertion, at the whole-market size this repository measures at.

    5,540 priced names on 2026-08-14 gives a block of 56, so the roadmap's starting point of 100
    clears it and the PRD's suggested lower end of 50 does not. Held as arithmetic rather than as
    prose, because a docstring that quoted 56 could go stale against `_quantile`'s rule.
    """
    assert upper_clip_block(5_540, 0.99) == 56
    assert upper_clip_block(4_002, 0.99) == 41
    assert 50 <= upper_clip_block(5_540, 0.99) < 100

    assert upper_clip_block(1, 0.99) == 0
    assert upper_clip_block(0, 0.99) == 0
    assert upper_clip_block(500, 1.0) == 0
    with pytest.raises(TwoStageFunnelError, match=r"must be in \[0, 1\]"):
        upper_clip_block(10, 1.5)
    with pytest.raises(TwoStageFunnelError, match="count cannot be negative"):
        upper_clip_block(-1, 0.99)


NEUTRALISATION_SIZE: Final[int] = 120
"""Above both shipped floors of 100, and a size at which `ceil((n - 1) / 100)` is 2 -- small
enough to name both clipped securities in an assertion and large enough for the shipped specs to
run at all."""


def _industry(index: int) -> str:
    return f"80{10 + 10 * (index % 4)}.SI"


def test_the_shipped_transform_ties_the_top_and_the_neutralisation_hides_the_same_block() -> None:
    """The measurement `shortlist_size`'s floor rests on, reproduced offline through both engines.

    The whole-market version is in `cross_section.py`'s docstring: on 2026-08-14's earnings
    yield, 41 clipped names carry one processed value and 41 distinct neutralised residuals. This
    drives the same two shipped specs over one 120-name cross section and requires exactly that
    shape at this size -- **one** distinct processed value across the block and **two** distinct
    neutralised ones -- so the finding is executable rather than quoted.

    It is the reason `ComponentCrossSection.clipped_subjects` is carried in: on the neutralised
    tier nothing recoverable from the values identifies the block.
    """
    values = _distinct_market(NEUTRALISATION_SIZE)
    processed = apply_factor_transform(
        _panel(values), CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    block_size = upper_clip_block(NEUTRALISATION_SIZE, 0.99)
    assert block_size == 2
    assert processed.statistics.winsorized_high_count == block_size

    stored = processed.values()
    highest = max(stored.values())
    block = sorted(subject for subject, value in stored.items() if value == highest)
    assert len(block) == block_size, "the clip is what produces the tie on the processed tier"
    assert len({stored[subject] for subject in block}) == 1

    characteristics = build_industry_market_cap_cross_section(
        as_of=AS_OF,
        taxonomy="SW2021",
        industry_level="L1",
        market_cap_measure="total_mv",
        characteristics=[
            SecurityCharacteristic(
                subject=subject,
                industry_code=_industry(index),
                market_cap=1_000.0 * (index + 1),
                is_backfilled=False,
            )
            for index, subject in enumerate(values)
        ],
    )
    neutralized = apply_factor_neutralization(
        processed, INDUSTRY_AND_SIZE, characteristics, code_commit=COMMIT, built_at=BUILT_AT
    )
    residuals = {
        observation.subject: observation.value
        for observation in neutralized.observations
        if observation.value is not None
    }

    assert len({residuals[subject] for subject in block}) == block_size, (
        "the neutralisation gives each clipped name a distinct residual out of one identical "
        "factor term, so the ordering inside the block is by industry and size alone"
    )


def test_a_cut_inside_the_clip_block_is_refused_and_one_name_above_it_is_not() -> None:
    """The separating pair: one market, one name's difference in `N`, two different codes.

    A fixture where both sizes produced the same answer would pin nothing, which is the failure
    mode this repository has met more than ten times.
    """
    market = _straight_market()
    block = frozenset(TWELVE[:2])
    component = _component(ALPHA, dict(market), clipped=block)

    inside = _screen(_spec(shortlist_size=len(block))).select(
        as_of=AS_OF, universe=TWELVE, components=[component], bars=_bars()
    )
    outside = _screen(_spec(shortlist_size=len(block) + 1)).select(
        as_of=AS_OF, universe=TWELVE, components=[component], bars=_bars()
    )

    assert inside.coverage == "cut_inside_the_clip_block"
    assert inside.shortlist == ()
    assert inside.clip_block == 2
    assert outside.coverage == "shortlisted"
    assert len(outside.shortlist) == 3
    assert outside.clip_block == 2


def test_the_clip_block_is_the_largest_declared_components_and_not_the_smallest() -> None:
    """A composite is as clipped as its most clipped component, so the floor is the maximum."""
    market = _straight_market()
    components = [
        _component(ALPHA, dict(market), clipped=frozenset(TWELVE[:1])),
        _component(BETA, dict(market), clipped=frozenset(TWELVE[:4])),
    ]
    spec = _spec((ALPHA, 1.0), (BETA, 1.0), shortlist_size=4)

    funnel = _screen(spec).select(as_of=AS_OF, universe=TWELVE, components=components, bars=_bars())

    assert funnel.clip_block == 4
    assert funnel.coverage == "cut_inside_the_clip_block"
    assert [census.clipped_count for census in funnel.scores.components] == [1, 4]


def test_the_shortlisted_entries_say_how_many_of_their_terms_the_winsorization_moved() -> None:
    """`N` above the block makes the substitution a minority; it never removes it.

    Measured on the real market at 2026-08-14: 25 of the top 25 and 50 of the top 100 shortlisted
    names of a two-factor composite are clipped on at least one component. This is the field that
    makes the residual readable on any one run.
    """
    market = _straight_market()
    components = [
        _component(ALPHA, dict(market), clipped=frozenset(TWELVE[:2])),
        _component(BETA, dict(market), clipped=frozenset(TWELVE[1:3])),
    ]
    funnel = _screen(_spec((ALPHA, 1.0), (BETA, 1.0), shortlist_size=4)).select(
        as_of=AS_OF, universe=TWELVE, components=components, bars=_bars()
    )

    assert funnel.coverage == "shortlisted"
    assert [entry.clipped_component_count for entry in funnel.shortlist] == [1, 2, 1, 0]


# --------------------------------------------------------------------------------------------
# Stage one
# --------------------------------------------------------------------------------------------


def test_the_orientation_rule_is_the_ic_studys_own_orientation_rule() -> None:
    """`oriented_value` restates `FactorICSpec.orient`, so the two are driven on one number."""
    higher = FactorICSpec(definition=ALPHA, method="spearman", min_securities=3, min_as_ofs=2)
    lower = FactorICSpec(definition=LOWER, method="spearman", min_securities=3, min_as_ofs=2)

    for value in (-2.5, -0.0, 0.0, 0.25, 1e18):
        assert oriented_value(value, "higher_is_better") == higher.orient(value)
        assert oriented_value(value, "lower_is_better") == lower.orient(value)


def test_incomplete_components_are_excluded_rather_than_scored_on_the_ones_that_turned_up() -> None:
    """The separating fixture: the excluded name would rank **first** on a partial sum.

    A composite summed over whichever components a security happens to have is a different
    statistic per security, and the ordering then moves with the coverage pattern rather than
    with the factors. The market here is built so that reading it the other way changes who is
    shortlisted, which is what makes the assertion about behaviour rather than about a count.
    """
    market = _straight_market()
    partial = dict(market)
    partial[TWELVE[0]] = 1_000.0
    beta = {subject: value for subject, value in market.items() if subject != TWELVE[0]}

    funnel = _screen(_spec((ALPHA, 1.0), (BETA, 1.0))).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(partial)), _component(BETA, dict(beta))],
        bars=_bars(),
    )

    assert funnel.scores.scored_count == len(TWELVE) - 1
    assert dict(funnel.scores.excluded_by_coverage)["incomplete_components"] == 1
    assert TWELVE[0] not in {entry.subject for entry in funnel.shortlist}
    assert funnel.shortlist[0].subject == TWELVE[1], (
        "scoring the partial name over the one component it has would have put it first"
    )


def test_an_imputed_value_is_not_admissible_and_is_not_the_unvalued_cell() -> None:
    """The one cell in which the two tier tables differ, and both cells are separated.

    `processed`'s `imputed` carries a number the repository made up: `TIER_VALUE_CODES` counts
    it and `TIER_ADMITTED_CODES` does not. A screen using one table for both would put that
    median into the composite.
    """
    market = _straight_market()
    funnel = _screen().select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[
            _component(
                ALPHA,
                dict(market),
                codes={TWELVE[0]: "imputed", TWELVE[1]: "source_not_computed"},
            )
        ],
        bars=_bars(),
    )

    cells = dict(funnel.scores.excluded_by_coverage)
    assert cells["not_admissible"] == 1
    assert cells["not_valued"] == 1
    assert funnel.scores.scored_count == len(TWELVE) - 2
    assert funnel.scores.components[0].valued_count == len(TWELVE) - 1
    assert funnel.scores.components[0].admitted_count == len(TWELVE) - 2


def test_two_spellings_of_one_weighting_produce_the_same_scores() -> None:
    """Weights are normalised by their own total, so the score's scale is not a function of how
    many components were declared."""
    market = _straight_market()
    components = [_component(ALPHA, dict(market)), _component(BETA, dict(market))]

    ones = _screen(_spec((ALPHA, 1.0), (BETA, 1.0))).select(
        as_of=AS_OF, universe=TWELVE, components=components, bars=_bars()
    )
    halves = _screen(_spec((ALPHA, 0.5), (BETA, 0.5))).select(
        as_of=AS_OF, universe=TWELVE, components=components, bars=_bars()
    )

    assert [entry.score for entry in ones.shortlist] == [entry.score for entry in halves.shortlist]
    assert ones.shortlist[0].score == 12.0


def test_a_lower_is_better_component_reverses_the_shortlist() -> None:
    """`direction` reaches which end the cut is taken from, and the fixture separates the two."""
    market = _straight_market()
    values = _component(ALPHA, dict(market))
    reversed_values = ComponentCrossSection(
        factor_id=LOWER.factor_id, values=values.values, clipped_subjects=frozenset()
    )

    higher = _screen(_spec((ALPHA, 1.0))).select(
        as_of=AS_OF, universe=TWELVE, components=[values], bars=_bars()
    )
    lower = _screen(_spec((LOWER, 1.0))).select(
        as_of=AS_OF, universe=TWELVE, components=[reversed_values], bars=_bars()
    )

    assert [entry.subject for entry in higher.shortlist] == list(TWELVE[:3])
    assert [entry.subject for entry in lower.shortlist] == list(reversed(TWELVE[-3:]))


def test_a_stored_value_outside_the_universe_is_not_in_the_cross_section() -> None:
    """A cross section is defined by the market that existed, not by whichever partitions have
    rows: an extra stored name neither scores nor appears in a census cell."""
    market = _straight_market()
    market["999999.SZ"] = 99.0

    funnel = _screen().select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(market))],
        bars=_bars(),
    )

    assert funnel.scores.universe_count == len(TWELVE)
    assert funnel.scores.components[0].subject_count == len(TWELVE)
    assert "999999.SZ" not in {entry.subject for entry in funnel.shortlist}


def test_the_score_census_accounts_for_every_security_offered() -> None:
    """Four cells and a total, `ICCensus`' un-fudgeable arithmetic on this plane."""
    market = _straight_market()
    funnel = _screen().select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(market), codes={TWELVE[0]: "imputed"})],
        bars=_bars(),
    )

    assert tuple(code for code, _count in funnel.scores.excluded_by_coverage) == tuple(
        entry for entry in SCORE_COVERAGE_ORDER if entry != "scored"
    )
    total = funnel.scores.scored_count + sum(
        count for _code, count in funnel.scores.excluded_by_coverage
    )
    assert total == funnel.scores.universe_count
    assert funnel.scores.scored_rate == (len(TWELVE) - 1) / len(TWELVE)


def test_a_screen_refuses_a_cross_section_it_did_not_declare_and_one_it_is_missing() -> None:
    """A composite over the components that turned up is a different statistic."""
    market = _straight_market()
    spec = _spec((ALPHA, 1.0), (BETA, 1.0))

    with pytest.raises(TwoStageFunnelError, match="no cross section was offered for"):
        _screen(spec).select(
            as_of=AS_OF,
            universe=TWELVE,
            components=[_component(ALPHA, dict(market))],
            bars=_bars(),
        )

    with pytest.raises(TwoStageFunnelError, match="which this screen does not declare"):
        _screen(_spec((ALPHA, 1.0))).select(
            as_of=AS_OF,
            universe=TWELVE,
            components=[_component(ALPHA, dict(market)), _component(BETA, dict(market))],
            bars=_bars(),
        )


def test_an_empty_universe_is_a_malformed_screen_and_not_a_market_fact() -> None:
    with pytest.raises(TwoStageFunnelError, match="a screen needs a universe"):
        _screen().select(
            as_of=AS_OF,
            universe=(),
            components=[_component(ALPHA, _straight_market())],
            bars=_bars(),
        )


def test_a_component_carrying_one_security_twice_is_refused() -> None:
    with pytest.raises(TwoStageFunnelError, match=r"carries \S+ twice at one as_of"):
        ComponentCrossSection(
            factor_id="fct_probe",
            values=(("000001.SZ", 1.0, "processed"), ("000001.SZ", 2.0, "processed")),
            clipped_subjects=frozenset(),
        )


def test_a_clipped_name_with_no_row_in_its_own_cross_section_is_refused() -> None:
    with pytest.raises(TwoStageFunnelError, match="as clipped and carries no row for them"):
        ComponentCrossSection(
            factor_id="fct_probe",
            values=(("000001.SZ", 1.0, "processed"),),
            clipped_subjects=frozenset({"000002.SZ"}),
        )


def test_a_coverage_code_from_another_tier_is_refused_rather_than_read_as_unvalued() -> None:
    """A `neutralized` code on a processed screen would otherwise be silently `not_valued`."""
    market = _straight_market()
    with pytest.raises(TwoStageFunnelError, match="which is not one of the processed tier's"):
        _screen().select(
            as_of=AS_OF,
            universe=TWELVE,
            components=[_component(ALPHA, dict(market), codes={TWELVE[0]: "neutralized"})],
            bars=_bars(),
        )


# --------------------------------------------------------------------------------------------
# Stage two
# --------------------------------------------------------------------------------------------


def test_the_registry_gate_closes_the_gap_the_execution_policy_discloses() -> None:
    """One bar, two contracts, two answers -- which is what makes the assertion mean anything.

    `KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input` records that a `MarketBar`
    carries nothing saying whether the registry stood behind that security, and that "the defence
    is that a caller filters its universe before it builds bars, which is a discipline this
    contract cannot audit". This funnel is that caller.
    """
    market = _straight_market()
    delisted = TWELVE[0]
    listed = tuple(subject for subject in TWELVE if subject != delisted)
    bar = _bar(delisted)

    direct = AShareExecutionPolicy().execute(ExecutionRequest(side="buy", quantity=100), bar)
    assert direct.status == "filled", "sentinel: the policy itself has no objection to this bar"

    funnel = _screen().select(
        as_of=AS_OF,
        universe=listed,
        components=[_component(ALPHA, dict(market))],
        bars=_bars(),
    )

    assert delisted not in {entry.subject for entry in funnel.shortlist}
    assert funnel.scores.universe_count == len(listed)
    assert funnel.scores.scored_count == len(listed)
    assert funnel.tradeability.scored_count == len(listed)
    assert "not_in_registry" not in set(TRADEABILITY_VERDICT_ORDER), (
        "the gate is stage one's: a name outside the universe is not in the cross section at "
        "all, so there is no stage-two verdict for it and a branch nothing reaches is not "
        "written"
    )
    assert funnel.shortlist[0].subject == listed[0]


def test_a_bar_without_a_published_band_is_refused_here_and_filled_by_the_policy() -> None:
    """The fail-closed divergence, driven in both directions on one bar.

    `an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy` is the entry;
    this is the pair of answers it is about.
    """
    market = _straight_market()
    bars = _bars()
    bars[TWELVE[0]] = _bar(TWELVE[0], published_band=None)

    direct = AShareExecutionPolicy().execute(
        ExecutionRequest(side="buy", quantity=100), bars[TWELVE[0]]
    )
    assert direct.status == "filled", "sentinel: the policy derives a band and fills"

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=bars
    )

    assert dict(funnel.tradeability.refused_by_verdict)["unbanded"] == 1
    assert funnel.shortlist[0].subject == TWELVE[1]


def test_a_limit_up_bar_is_rejected_with_the_policys_own_reason() -> None:
    """The `rejected` cell carries the policy's string rather than a re-derivation."""
    market = _straight_market()
    bars = _bars()
    bars[TWELVE[0]] = _bar(TWELVE[0], close=11.0, low=11.0, high=11.0, published_band=(11.0, 9.0))

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=bars
    )

    assert dict(funnel.tradeability.refused_by_verdict)["rejected"] == 1
    assert funnel.tradeability.rejection_reasons == (
        ("buy cannot fill on a one-price limit-up bar", 1),
    )
    assert funnel.shortlist[0].subject == TWELVE[1]


def test_capital_below_one_lot_is_decided_before_the_policy() -> None:
    """`ExecutionRequest.quantity` is `gt=0`, so there is no order to place at all."""
    market = _straight_market()
    spec = _spec(shortlist_size=3, position_capital=Decimal("500"))

    funnel = _screen(spec).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(market))],
        bars=_bars(),
    )

    assert dict(funnel.tradeability.refused_by_verdict)["below_board_minimum"] == len(TWELVE)
    assert funnel.coverage == "no_tradeable_candidate"
    assert funnel.tradeability.rejection_reasons == ()


def test_an_unbarred_name_and_a_rejected_one_are_counted_apart() -> None:
    """A short read looks exactly like a refusal until the two are counted apart."""
    market = _straight_market()
    bars = _bars()
    del bars[TWELVE[0]]
    bars[TWELVE[1]] = _bar(TWELVE[1], suspended=True)

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=bars
    )

    cells = dict(funnel.tradeability.refused_by_verdict)
    assert cells["unbarred"] == 1
    assert cells["rejected"] == 1
    assert funnel.tradeability.rejection_reasons == (("security is suspended", 1),)
    assert [entry.subject for entry in funnel.shortlist] == list(TWELVE[2:5])


def test_the_tradeability_census_accounts_for_every_scored_security() -> None:
    """Six verdicts and a total; the four refusal shapes are given four distinct counts."""
    market = _straight_market()
    bars = _bars()
    del bars[TWELVE[0]]
    bars[TWELVE[1]] = _bar(TWELVE[1], published_band=None)
    bars[TWELVE[2]] = _bar(TWELVE[2], suspended=True)
    bars[TWELVE[3]] = _bar(TWELVE[3], suspended=True)

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=bars
    )

    keys = tuple(entry for entry, _count in funnel.tradeability.refused_by_verdict)
    assert keys == REFUSED_VERDICT_ORDER
    assert dict(funnel.tradeability.refused_by_verdict) == {
        "unbarred": 1,
        "unbanded": 1,
        "below_board_minimum": 0,
        "rejected": 2,
    }
    assert funnel.tradeability.tradeable_count == len(TWELVE) - 4
    assert funnel.tradeability.unoffered_count == 0
    assert funnel.tradeability.tradeable_rate == (len(TWELVE) - 4) / len(TWELVE)
    assert set(TRADEABILITY_VERDICT_ORDER) - set(REFUSED_VERDICT_ORDER) == {"tradeable"}


# --------------------------------------------------------------------------------------------
# The coverage codes, and the order they are decided in
# --------------------------------------------------------------------------------------------


def test_degenerate_scores_is_decided_before_the_clip_block() -> None:
    """A market satisfying both conditions, so the declared order is the only thing deciding it.

    An all-tied cross section is degenerate *and* has a cut inside its clip block. A fixture in
    which only one condition held would pin nothing about the order.
    """
    flat = dict.fromkeys(TWELVE, 4.0)
    component = _component(ALPHA, dict(flat), clipped=frozenset(TWELVE[:5]))

    funnel = _screen(_spec(shortlist_size=3)).select(
        as_of=AS_OF, universe=TWELVE, components=[component], bars=_bars()
    )

    assert funnel.coverage == "degenerate_scores"
    assert funnel.clip_block == 5, "the other condition held too, and lost on the declared order"
    assert FUNNEL_COVERAGE_ORDER.index("degenerate_scores") < FUNNEL_COVERAGE_ORDER.index(
        "cut_inside_the_clip_block"
    )


def test_no_scored_candidate_is_what_a_derived_tiers_own_floor_produces() -> None:
    """Below `min_cross_section` both shipped specs store a code on every row and no value, so
    this funnel needs no floor of its own to report the right thing."""
    thin = dict.fromkeys(TWELVE, None)

    funnel = _screen().select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[
            _component(
                ALPHA,
                dict(thin),
                codes=dict.fromkeys(TWELVE, "insufficient_cross_section"),
            )
        ],
        bars=_bars(),
    )

    assert funnel.coverage == "no_scored_candidate"
    assert funnel.scores.scored_count == 0
    assert dict(funnel.scores.excluded_by_coverage)["not_valued"] == len(TWELVE)


def test_cut_exceeds_the_cross_section_is_what_a_three_name_as_of_gets() -> None:
    """The narrow-sample answer is a code, not three names presented as a selection.

    This repository has taken eight Critical findings on narrow samples. A funnel that selected
    everybody is not a funnel, and `selection_rate == 1.0` is the reading it would otherwise
    produce.
    """
    three = TWELVE[:3]
    market = {subject: 3.0 - index for index, subject in enumerate(three)}

    funnel = _screen(_spec(shortlist_size=3)).select(
        as_of=AS_OF,
        universe=three,
        components=[_component(ALPHA, dict(market))],
        bars=_bars(three),
    )

    assert funnel.coverage == "cut_exceeds_the_cross_section"
    assert funnel.shortlist == ()
    assert funnel.tradeability.tradeable_count == 3
    assert funnel.selection_rate == 0.0
    assert funnel.shortlist_rate == 0.0


def test_no_tradeable_candidate_when_the_market_refused_every_scored_name() -> None:
    market = _straight_market()
    bars = {subject: _bar(subject, suspended=True) for subject in TWELVE}

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=bars
    )

    assert funnel.coverage == "no_tradeable_candidate"
    assert funnel.tradeability.tradeable_rate == 0.0
    assert funnel.tradeability.unoffered_count == 0


def test_a_funnel_refused_before_stage_two_reports_every_scored_name_as_unoffered() -> None:
    """The two readings "the market refused everybody" and "nobody asked" are different facts.

    Under a stage-one code no order was built, so a `tradeable_rate` of zero would be a claim
    about a market nobody consulted. `unoffered_count` is that distinction and the rate is
    `None`.
    """
    flat = dict.fromkeys(TWELVE, 4.0)

    funnel = _screen().select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(flat))], bars=_bars()
    )

    assert funnel.coverage == "degenerate_scores"
    assert funnel.tradeability.scored_count == len(TWELVE)
    assert funnel.tradeability.unoffered_count == len(TWELVE)
    assert funnel.tradeability.tradeable_count == 0
    assert funnel.tradeability.tradeable_rate is None
    assert dict(funnel.tradeability.refused_by_verdict) == dict.fromkeys(REFUSED_VERDICT_ORDER, 0)


def test_a_census_reporting_a_market_that_half_happened_is_refused() -> None:
    """`unoffered_count` is all or nothing, `PeriodPortfolio.__post_init__`'s rule."""
    from openalpha_cn.backtest.cross_section import TradeabilityCensus

    with pytest.raises(TwoStageFunnelError, match="stage two either runs for the whole cross"):
        TradeabilityCensus(
            scored_count=4,
            tradeable_count=2,
            unoffered_count=2,
            refused_by_verdict=tuple((code, 0) for code in REFUSED_VERDICT_ORDER),
            rejection_reasons=(),
            refused=(),
        )


def test_a_census_whose_named_refusals_disagree_with_its_own_cells_is_refused() -> None:
    """`V2-P4-066`'s names are held to `V2-P4-005`'s counts, in every direction that can drift.

    The names were added so a refused screen could say *which* securities went; a named list that
    could disagree with the census beside it would be a second answer to the one question the
    census exists to answer once, which is `ScoreCensus`' own arithmetic rule. Four ways to
    disagree, because each is a different edit a later contributor makes: a count with no name, a
    name under a verdict the cells did not report, a reason the census never counted, and a list
    in some order other than the declared one.
    """
    from openalpha_cn.backtest.cross_section import RefusedSecurity, TradeabilityCensus

    def census(**overrides: object) -> TradeabilityCensus:
        fields: dict[str, object] = {
            "scored_count": 4,
            "tradeable_count": 2,
            "unoffered_count": 0,
            "refused_by_verdict": tuple(
                (code, 1 if code in {"unbarred", "rejected"} else 0)
                for code in REFUSED_VERDICT_ORDER
            ),
            "rejection_reasons": (("security is suspended", 1),),
            "refused": (
                RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),
                RefusedSecurity(
                    subject="000002.SZ", verdict="rejected", reason="security is suspended"
                ),
            ),
        }
        fields.update(overrides)
        return TradeabilityCensus(**fields)  # type: ignore[arg-type]

    assert census().refused[0].subject == "000001.SZ"

    with pytest.raises(TwoStageFunnelError, match="the names and the counts are one answer"):
        census(refused=(RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),))

    with pytest.raises(TwoStageFunnelError, match="the names and the counts are one answer"):
        census(
            refused=(
                RefusedSecurity(subject="000001.SZ", verdict="unbanded", reason=None),
                RefusedSecurity(
                    subject="000002.SZ", verdict="rejected", reason="security is suspended"
                ),
            )
        )

    with pytest.raises(TwoStageFunnelError, match="carries reasons"):
        census(
            refused=(
                RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),
                RefusedSecurity(subject="000002.SZ", verdict="rejected", reason="something else"),
            )
        )

    with pytest.raises(TwoStageFunnelError, match="REFUSED_VERDICT_ORDER and then subject order"):
        census(
            refused=(
                RefusedSecurity(
                    subject="000002.SZ", verdict="rejected", reason="security is suspended"
                ),
                RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),
            )
        )

    with pytest.raises(TwoStageFunnelError, match="a scored security gets one verdict"):
        census(
            refused_by_verdict=tuple(
                (code, 2 if code == "unbarred" else 0) for code in REFUSED_VERDICT_ORDER
            ),
            rejection_reasons=(),
            refused=(
                RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),
                RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason=None),
            ),
        )


def test_a_refused_security_carries_a_reason_for_exactly_the_policys_own_refusal() -> None:
    """`RefusedSecurity`'s both-directions rule: `rejected` has a sentence, the other three do not.

    Both directions because only one of them is the obvious one. A missing reason on a `rejected`
    name loses the half of the answer a user acts on; a reason attached to `below_board_minimum`
    is this module inventing a sentence `AShareExecutionPolicy` never said, about an order that
    was never built -- `_rejection_reasons`' stated rule about not being a second authority.
    """
    from openalpha_cn.backtest.cross_section import RefusedSecurity

    with pytest.raises(TwoStageFunnelError, match="the policy gives a reason for exactly"):
        RefusedSecurity(subject="000001.SZ", verdict="rejected", reason=None)

    with pytest.raises(TwoStageFunnelError, match="the policy gives a reason for exactly"):
        RefusedSecurity(subject="000001.SZ", verdict="unbarred", reason="security is suspended")

    with pytest.raises(TwoStageFunnelError, match="is not one of the verdicts stage two refuses"):
        RefusedSecurity(subject="000001.SZ", verdict="tradeable", reason=None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# The shortlist itself
# --------------------------------------------------------------------------------------------


def test_the_shortlist_is_the_top_of_the_ordering_and_carries_the_policys_own_fill() -> None:
    """Everything `V2-P4-005` needs is on the entry, and the fill is the policy's own object."""
    market = _straight_market()

    funnel = _screen(_spec((ALPHA, 3.0), (BETA, 1.0), shortlist_size=3)).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(market)), _component(BETA, dict(market))],
        bars=_bars(),
    )

    assert funnel.coverage == "shortlisted"
    assert [entry.rank for entry in funnel.shortlist] == [1, 2, 3]
    assert [entry.subject for entry in funnel.shortlist] == list(TWELVE[:3])
    first = funnel.shortlist[0]
    assert [component.factor_id for component in first.components] == [
        ALPHA.factor_id,
        BETA.factor_id,
    ]
    assert math.isclose(first.components[0].weight, 0.75)
    assert math.isclose(first.score, 12.0)
    assert first.fill.status == "filled"
    assert first.fill.side == "buy"
    assert first.fill.quantity == 10_000
    assert first.fill.filled_price == Decimal("10.00")
    assert first.fill.total_cost > 0


def test_a_tie_at_the_cut_is_reported_and_broken_by_ascending_subject_code() -> None:
    """`the_cut_is_broken_by_subject_code_when_two_scores_tie`, on a pair that separates it.

    **The tied pair is `000003.SZ` and `000012.SZ`, and that choice is the whole test.** The
    first version of this tied two *adjacent* codes, and a mutation replacing the tie-break with
    `subject[::-1]` left the suite green -- the reversed strings happen to order the same way for
    an adjacent pair, so the fixture could not tell two answers apart. That is the failure this
    repository has met more than ten times, arriving here. Reversed, `000012.SZ` becomes
    `ZS.210000` and `000003.SZ` becomes `ZS.300000`, so any tie-break that is not the ascending
    `ts_code` returns the other name and this test goes red.
    """
    market = _straight_market()
    tied = (TWELVE[2], TWELVE[11])
    market[tied[1]] = market[tied[0]]

    funnel = _screen(_spec(shortlist_size=3)).select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=_bars()
    )

    assert funnel.tied_at_the_cut == 2
    assert [entry.subject for entry in funnel.shortlist] == list(TWELVE[:3])
    assert funnel.shortlist[-1].subject == min(tied)
    assert max(tied) not in {entry.subject for entry in funnel.shortlist}
    assert min(tied)[::-1] > max(tied)[::-1], (
        "sentinel: the pair must order oppositely under a reversed key, or the assertions above "
        "would hold for a tie-break this test is not pinning"
    )

    clean = _screen(_spec(shortlist_size=3)).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, _straight_market())],
        bars=_bars(),
    )
    assert clean.tied_at_the_cut == 1


def test_the_three_rates_multiply_to_the_shortlist_rate() -> None:
    """The two-stage analogue of `CoverageFunnel`'s four-step product, up to rounding."""
    market = _straight_market()
    bars = _bars()
    del bars[TWELVE[11]]

    funnel = _screen(_spec(shortlist_size=4)).select(
        as_of=AS_OF,
        universe=TWELVE,
        components=[_component(ALPHA, dict(market), codes={TWELVE[10]: "imputed"})],
        bars=bars,
    )

    assert funnel.scores.scored_rate is not None
    assert funnel.tradeability.tradeable_rate is not None
    assert funnel.selection_rate is not None
    assert funnel.shortlist_rate is not None
    product = funnel.scores.scored_rate * funnel.tradeability.tradeable_rate * funnel.selection_rate
    assert math.isclose(product, funnel.shortlist_rate)
    assert funnel.shortlist_count == 4


def test_a_shortlist_carrying_a_rejected_execution_is_not_constructible() -> None:
    """The invariant that keeps `shortlisted` meaning what it says."""
    from openalpha_cn.backtest.cross_section import ComponentScore, ShortlistEntry

    with pytest.raises(TwoStageFunnelError, match="carrying a rejected execution"):
        ShortlistEntry(
            subject="000001.SZ",
            rank=1,
            score=1.0,
            components=(
                ComponentScore(
                    factor_id="fct_probe",
                    value=1.0,
                    oriented=1.0,
                    weight=1.0,
                    contribution=1.0,
                    clipped=False,
                ),
            ),
            fill=AShareExecutionPolicy().execute(
                ExecutionRequest(side="buy", quantity=100), _bar("000001.SZ", suspended=True)
            ),
        )


def test_the_hard_filter_removes_almost_nothing_and_the_cut_does_the_reduction() -> None:
    """`the_hard_filter_is_a_correctness_gate_and_not_a_sizing_gate`, as the shape of one run.

    The measurement is whole-market and lives in the registry entry: 5,543 listed, 5,535 buyable
    on 2026-08-14. What is executable here is the consequence -- on a market the filter leaves
    intact, every bit of the reduction is the cut's, and `selection_rate` is the only rate below
    one.
    """
    market = _straight_market()

    funnel = _screen(_spec(shortlist_size=2)).select(
        as_of=AS_OF, universe=TWELVE, components=[_component(ALPHA, dict(market))], bars=_bars()
    )

    assert funnel.scores.scored_rate == 1.0
    assert funnel.tradeability.tradeable_rate == 1.0
    assert funnel.selection_rate == 2 / len(TWELVE)
    assert funnel.shortlist_rate == funnel.selection_rate
