"""The shortlist-level gate (`V2-P4-023`), held to the five properties that make it a gate.

`tests/integration/test_shortlist_gate_refusal.py` is the acceptance: a real panel the
`V2-P1-013` gate clears, a real screen whose every shortlisted name fills, and a list the two of
them ship clean that this gate refuses. This file is the contract underneath it.

1. **Every declared bar is in the identity.** `test_the_gate_manifest_address_moves_for_every
   _declared_threshold` varies each field of `ShortlistGateSpec` and requires `gate_manifest_id`
   to move, and `test_two_gate_runs_of_one_declaration_share_an_address` requires the other
   direction -- a one-directional identity test passes on a constant. Bar *n+1* is red at
   `GATE_SPEC_THRESHOLDS`, which that test checks against `ShortlistGateSpec.model_fields`
   before it varies anything. `V2-P4-058` corrected this sentence: it used to credit "the
   `model_fields` meta-audit", which pins `ShortlistGateManifest` and not the spec, so a fourth
   bar arrived unaudited at 35 passed.
2. **Every block code separates.** Four codes, four fixtures, each of which raises exactly one --
   because a fixture that failed two bars at once could not tell a gate that reads one of them
   from a gate that reads neither.
3. **Blocked, admitted-with-candidates and admitted-with-none are three states.** Driven as a
   cross rather than a pair, and `bool()`/`len()`/iteration raise on all three.
4. **The refusal carries the whole list.** No truncation, no re-cut, no substitution.
5. **The ban is structural.** `test_the_gate_cannot_reach_the_three_modules_that_make_an_order`
   drives `lint-imports` over the real module and then over a copy carrying one `PortfolioOrder`
   import, and requires the second to be refused.

The fixtures here are hand-built cross sections rather than a panel, deliberately: the panel is
the integration test's job, and a unit fixture that had to write Parquet to vary a threshold
would make the threshold tests cost thirty seconds each. Every funnel below is still a **real**
`CrossSectionScreen.select` over a real `AShareExecutionPolicy` -- none is hand-constructed --
because the counts this gate divides are the funnel's own and a hand-built census could be made
to say anything.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest
from import_linter_containment import contained_lint_imports
from pydantic import ValidationError

from openalpha_cn.backtest.candidate_ranking import (
    CandidateRanking,
    CandidateRankingManifest,
    build_ranking_manifest,
    rank_candidates,
)
from openalpha_cn.backtest.cross_section import (
    ComponentCrossSection,
    CrossSectionFunnel,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
)
from openalpha_cn.backtest.execution import AShareExecutionPolicy, MarketBar
from openalpha_cn.backtest.shortlist_gate import (
    GATE_MANIFEST_UNADDRESSED_FIELDS,
    KNOWN_SHORTLIST_GATE_LIMITATIONS,
    RANKING_MANIFEST_ID_PATTERN,
    SHORTLIST_BLOCK_CODES,
    SHORTLIST_BLOCK_ORDER,
    SHORTLIST_GATE_LIMITATION_CODES,
    ShortlistClearance,
    ShortlistGateBlock,
    ShortlistGateError,
    ShortlistGateManifest,
    ShortlistGateSpec,
    ShortlistMeasurement,
    gate_shortlist,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
MODULE_PATH: Final[Path] = ROOT / "src" / "openalpha_cn" / "backtest" / "shortlist_gate.py"

AS_OF: Final[datetime] = datetime(2026, 6, 12, 4, 0, tzinfo=UTC)
SESSION: Final[date] = date(2026, 6, 12)
BUILT_AT: Final[datetime] = datetime(2026, 6, 13, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"
CONFIG: Final[str] = "c" * 64
CAPITAL: Final[Decimal] = Decimal("100000")
HORIZON: Final[str] = "5d"


def code(index: int) -> str:
    return f"{index:06d}.SZ"


TWENTY: Final[tuple[str, ...]] = tuple(code(index) for index in range(1, 21))

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
    price = Decimal("10.0")
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


def _spec(*, shortlist_size: int = 3) -> ShortlistSpec:
    return ShortlistSpec(
        components=(ScoreComponent(definition=ALPHA, weight=1.0),),
        tier="raw",
        shortlist_size=shortlist_size,
        position_capital=CAPITAL,
    )


def _funnel(
    *,
    universe: tuple[str, ...] = TWENTY,
    suspended: frozenset[str] = frozenset(),
    unvalued: frozenset[str] = frozenset(),
    shortlist_size: int = 3,
) -> CrossSectionFunnel:
    """A real funnel off a real screen and a real execution policy.

    `suspended` names securities the policy will refuse a buy for, which is how the tradeable
    count is moved without touching the scored count -- and `unvalued` names ones stage one never
    scores, which is how the two denominators are moved apart.
    """
    declared = _spec(shortlist_size=shortlist_size)
    return CrossSectionScreen(declared, execution=AShareExecutionPolicy()).select(
        as_of=AS_OF,
        universe=universe,
        components=[
            ComponentCrossSection(
                factor_id=ALPHA.factor_id,
                values=tuple(
                    (
                        subject,
                        None if subject in unvalued else float(len(universe) - index),
                        "computed",
                    )
                    for index, subject in enumerate(universe)
                ),
                clipped_subjects=frozenset(),
            )
        ],
        bars={subject: _bar(subject, suspended=subject in suspended) for subject in universe},
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
        code_commit=COMMIT,
        config_digest=CONFIG,
        random_seed=7,
        started_at=AS_OF,
        finished_at=BUILT_AT,
        status="succeeded",
    ).run_manifest_id


def _manifest(
    *,
    universe: tuple[str, ...] = TWENTY,
    shortlist_size: int = 3,
    built_at: datetime = BUILT_AT,
) -> CandidateRankingManifest:
    return build_ranking_manifest(
        as_of=AS_OF,
        horizon=HORIZON,
        universe=list(universe),
        scoring_policy=_spec(shortlist_size=shortlist_size),
        code_commit=COMMIT,
        config_digest=CONFIG,
        built_at=built_at,
    )


def _rank(
    *,
    universe: tuple[str, ...] = TWENTY,
    shortlist_size: int = 3,
    built_at: datetime = BUILT_AT,
    researched: int | None = None,
    **kwargs: Any,
) -> CandidateRanking:
    """One ranking off a real funnel. `researched` caps how many shortlisted names came back."""
    cut = _funnel(universe=universe, shortlist_size=shortlist_size, **kwargs)
    subjects = tuple(entry.subject for entry in cut.shortlist)
    chosen = subjects if researched is None else subjects[:researched]
    return rank_candidates(
        manifest=_manifest(universe=universe, shortlist_size=shortlist_size, built_at=built_at),
        funnel=cut,
        signals={subject: _signal(subject) for subject in chosen},
        run_manifest_ids={subject: _run_manifest_id(subject) for subject in chosen},
        exposures=None,
        predictions={},
    )


SHUT: Final[frozenset[str]] = frozenset(TWENTY[4:])
"""Sixteen of twenty suspended, so four are tradeable and `tradable_ratio` is 0.2.

The fixture the refusal tests need and the default one cannot be: with nothing suspended every
name is tradeable, `tradable_ratio` is exactly 1.0, and `minimum_tradable_ratio` is `le=1.0` --
so there is no legal floor that a full market fails, and a refusal test written on the default
funnel would be asserting against a bar that cannot bind.
"""


def _bars(*, tradable: float = 0.0, researched: float = 0.0, age: int = 3_650) -> ShortlistGateSpec:
    """One declared set of bars, defaulting to the inert ones so a test names only what it means."""
    return ShortlistGateSpec(
        minimum_tradable_ratio=tradable,
        minimum_researched_ratio=researched,
        maximum_ranking_age_days=age,
    )


GATE_SPEC_THRESHOLDS: Final[Mapping[str, tuple[object, object]]] = MappingProxyType(
    {
        "minimum_tradable_ratio": (0.1, 0.2),
        "minimum_researched_ratio": (0.1, 0.2),
        "maximum_ranking_age_days": (30, 31),
    }
)
"""Every declared bar on `ShortlistGateSpec`, with a legal value and a different legal value.

The pair is what `test_the_gate_manifest_address_moves_for_every_declared_threshold` varies. It
is a mapping the audit reads rather than three names the audit recites, and it is checked against
`ShortlistGateSpec.model_fields` before it is used, so bar *n+1* is red until somebody adds a
value for it here -- which is the point at which they have to decide what "a different value of
this bar" even means.

`V2-P4-058` is why it exists. The module docstring above claimed "the `model_fields` meta-audit
is what makes bar *n+1* red until somebody argues for it", and that was false: the meta-audit
pins `ShortlistGateManifest`, not `ShortlistGateSpec`, and the threshold test varied three
hard-coded names. Measured: a fourth bar, `minimum_probe_ratio: float = Field(default=0.0,
ge=0.0, le=1.0)`, added to `ShortlistGateSpec` left this file at **35 passed**. It does move
`gate_manifest_id` -- `stable_model_id` dumps the whole model, so a new field reaches the
address for free -- which is exactly why nothing went red and exactly why that is not evidence:
the property held automatically, so no test measured it, and a bar whose identity contribution
is accidental is one nobody has argued for.
"""


# --------------------------------------------------------------------------------------------
# The registry, and the codes the suite has to agree on
# --------------------------------------------------------------------------------------------


def test_the_limitation_registry_is_exactly_the_seven_boundaries_this_gate_declares() -> None:
    """Equality rather than membership, `KNOWN_ADJUSTMENT_LIMITATIONS`' form since `V2-P1-005`:
    a membership assertion is additive and cannot see an entry that was deleted."""
    assert {
        "the_tradable_ratio_divides_by_the_universe_because_the_funnels_own_denominator_shrinks",
        "the_freshness_clock_is_a_field_the_rankings_own_identity_excludes",
        "freshness_is_counted_in_calendar_days_because_this_leaf_reaches_no_calendar",
        "this_gate_refuses_a_list_and_can_never_repair_one",
        "an_admitted_clearance_is_a_coverage_and_age_verdict_and_not_a_quality_one",
        "dataset_level_staleness_is_v2_p1_013s_and_is_not_restated_or_re_measured_here",
        "a_bar_of_zero_is_legal_and_switches_its_own_check_off",
    } == SHORTLIST_GATE_LIMITATION_CODES
    assert len(KNOWN_SHORTLIST_GATE_LIMITATIONS) == 7
    assert all(entry.detail.strip() for entry in KNOWN_SHORTLIST_GATE_LIMITATIONS)


def test_the_block_codes_are_the_four_this_gate_can_issue_and_no_fifth() -> None:
    """The closed set, and the order it is reported in, asserted as a literal.

    `SHORTLIST_BLOCK_ORDER` is `get_args` of the same `Literal`, so this pins both at once and a
    reordering that a `sorted()` would have hidden fails here.
    """
    assert SHORTLIST_BLOCK_ORDER == (
        "tradable_ratio_below_floor",
        "researched_ratio_not_measurable",
        "researched_ratio_below_floor",
        "ranking_is_stale",
    )
    assert set(SHORTLIST_BLOCK_ORDER) == SHORTLIST_BLOCK_CODES


def test_there_is_no_tradable_ratio_not_measurable_code_because_no_input_reaches_it() -> None:
    """The absent fifth code, driven rather than asserted.

    `universe_count` is `ge=1` on `CandidateRankingManifest` and both builders below refuse an
    empty universe, so `tradeable / universe` is always a number and a `not_measurable` branch
    for it could not be reached. That is `TradeabilityVerdict.not_in_registry`'s own measurement,
    and this is the reproduction of it: the two ways to get an empty universe both raise before
    a gate could be asked.
    """
    assert "tradable_ratio_not_measurable" not in SHORTLIST_BLOCK_CODES

    with pytest.raises(ValueError, match="a ranking needs a universe"):
        build_ranking_manifest(
            as_of=AS_OF,
            horizon=HORIZON,
            universe=[],
            scoring_policy=_spec(),
            code_commit=COMMIT,
            config_digest=CONFIG,
            built_at=BUILT_AT,
        )
    with pytest.raises(ValueError):
        _funnel(universe=())

    ranking = _rank()
    clearance = gate_shortlist(ranking=ranking, spec=_bars())
    assert clearance.measurement.tradable_ratio is not None
    with pytest.raises(ShortlistGateError, match="not a bar this gate can refuse on"):
        clearance.block_with_code("tradable_ratio_not_measurable")


# --------------------------------------------------------------------------------------------
# The thresholds, and the identity they enter
# --------------------------------------------------------------------------------------------


def test_the_gate_manifest_address_moves_for_every_declared_threshold() -> None:
    """The property the roadmap row turns on: two runs under different bars are different runs.

    Each bar is varied **alone**, so a manifest that hashed only one of them -- or that hashed
    the ranking id and nothing else -- fails on the two it ignored rather than passing on the one
    it happened to read.

    **`V2-P4-058`: the bars are discovered off `ShortlistGateSpec.model_fields`, not listed.**
    They used to be three names written into this function, so a fourth declared bar was varied
    by nothing and this file stayed at 35 passed. The first assertion below is the one that
    makes bar *n+1* red, and it is red at the useful moment -- before anything is measured --
    with a message saying what the author has to supply. A new bar cannot be auto-varied,
    because "a different value of this bar" is a question about the bar's meaning and not about
    its type, so the audit asks rather than guesses.
    """
    declared = set(ShortlistGateSpec.model_fields) - {"schema_version"}

    assert declared == set(GATE_SPEC_THRESHOLDS), (
        f"ShortlistGateSpec declares {sorted(declared)} and GATE_SPEC_THRESHOLDS varies "
        f"{sorted(GATE_SPEC_THRESHOLDS)}. Every declared bar has to be varied here, because "
        "V2-P4-023's whole property is that two runs under different bars are different runs -- "
        "give the new bar two legal values in GATE_SPEC_THRESHOLDS and this test will measure "
        "it. A bar that moves the address only because stable_model_id dumps the whole model is "
        "not a bar anybody has argued for; that is V2-P4-058"
    )

    ranking = _rank()
    base = {name: pair[0] for name, pair in GATE_SPEC_THRESHOLDS.items()}
    baseline = gate_shortlist(
        ranking=ranking, spec=ShortlistGateSpec(**base)
    ).manifest.gate_manifest_id

    addresses = {}
    for name, (_, varied) in GATE_SPEC_THRESHOLDS.items():
        spec = ShortlistGateSpec(**{**base, name: varied})
        addresses[name] = gate_shortlist(ranking=ranking, spec=spec).manifest.gate_manifest_id

    for name, address in addresses.items():
        assert address != baseline, (
            f"changing {name} left gate_manifest_id at {baseline}; a threshold that does not "
            "reach the address is a module constant wearing a field's name, which is exactly "
            "what V2-P4-023 asks this contract not to be"
        )
    assert len(set(addresses.values()) | {baseline}) == len(GATE_SPEC_THRESHOLDS) + 1, (
        "two different bars produced the same gate_manifest_id, so the address cannot tell the "
        "two runs apart even though it moved for each of them separately"
    )


def test_two_gate_runs_of_one_declaration_share_an_address() -> None:
    """The other direction, without which the test above passes on a random number.

    Two separate `gate_shortlist` calls over two separately built rankings of one declaration,
    under one set of bars. They also differ in `built_at`, which is the field the ranking's own
    identity excludes -- so this asserts that the *declaration* address is stable across exactly
    the difference the freshness bar is sensitive to.
    """
    bars = _bars(tradable=0.1, researched=0.1, age=3_650)
    first = gate_shortlist(ranking=_rank(built_at=BUILT_AT), spec=bars)
    second = gate_shortlist(ranking=_rank(built_at=BUILT_AT + timedelta(days=200)), spec=bars)

    assert first.manifest.gate_manifest_id == second.manifest.gate_manifest_id
    assert first.manifest.gate_manifest_id.startswith("sgt_")
    assert re.fullmatch(r"^sgt_[0-9a-f]{24}$", first.manifest.gate_manifest_id)


def test_the_gate_address_moves_when_the_ranking_declaration_does() -> None:
    """One set of bars over two different screens is two different gate runs."""
    bars = _bars(tradable=0.1)
    wide = gate_shortlist(ranking=_rank(), spec=bars).manifest.gate_manifest_id
    narrow = gate_shortlist(
        ranking=_rank(universe=TWENTY[:15]), spec=bars
    ).manifest.gate_manifest_id

    assert wide != narrow


def test_every_gate_manifest_field_is_addressed_or_excluded_by_name() -> None:
    """The meta-audit that makes field *n+1* red, `V2-P3-002`'s shape reused.

    `GATE_MANIFEST_UNADDRESSED_FIELDS` is empty on purpose -- this manifest records no wall clock
    and observes no host -- so the partition says every field reaches the address. A field added
    later is red here until it is either measured to move the address or given a reason there.

    **`V2-P4-058`: this pins the manifest, and the manifest is not where the bars live.** The
    file's own docstring credited it with making bar *n+1* red; a fourth field on
    `ShortlistGateSpec` is not a field on `ShortlistGateManifest`, whose three fields are
    unchanged by it, so this test was green through the whole probe. The spec's field set is
    pinned by `test_the_gate_manifest_address_moves_for_every_declared_threshold` against
    `GATE_SPEC_THRESHOLDS`, which is where a bar can actually be varied; the two halves are
    named here so the next reader does not have to rediscover which model each covers.
    """
    fields = set(ShortlistGateManifest.model_fields)

    assert set(GATE_MANIFEST_UNADDRESSED_FIELDS) == set()
    assert set(GATE_MANIFEST_UNADDRESSED_FIELDS) <= fields
    assert fields == {"schema_version", "ranking_manifest_id", "spec"}
    assert "gate_manifest_id" not in fields


def test_the_ranking_id_pattern_is_the_one_a_real_ranking_manifest_actually_produces() -> None:
    """`RANKING_MANIFEST_ID_PATTERN` is restated here, so it is bound by measurement.

    `MAXIMUM_SHORTLIST`'s obligation: a constant restated from another module is held against
    that module's real output rather than against a memory of it. The three other prefixes this
    repository mints are required to fail, so the pattern is not simply permissive.
    """
    real = _manifest().ranking_manifest_id

    assert re.fullmatch(RANKING_MANIFEST_ID_PATTERN, real), (
        f"{real!r} is what stable_model_id(prefix='rnk', ...) produces today and this module's "
        "pattern does not match it"
    )
    for foreign in (
        _run_manifest_id(TWENTY[0]),
        _rank().content_digest,
        gate_shortlist(ranking=_rank(), spec=_bars()).manifest.gate_manifest_id,
    ):
        assert not re.fullmatch(RANKING_MANIFEST_ID_PATTERN, foreign)

    with pytest.raises(ValidationError):
        ShortlistGateManifest(ranking_manifest_id="rnk_not_a_digest", spec=_bars())


def test_a_ranking_and_a_gate_manifest_do_not_share_an_address_space() -> None:
    """Distinct prefixes, so a caller cannot store one where the other belongs and be told."""
    ranking = _rank()
    clearance = gate_shortlist(ranking=ranking, spec=_bars())

    assert clearance.manifest.gate_manifest_id.startswith("sgt_")
    assert ranking.manifest.ranking_manifest_id.startswith("rnk_")
    assert ranking.content_digest.startswith("rkc_")


def test_a_spec_declares_every_bar_because_none_of_them_has_a_default() -> None:
    """`ShortlistSpec`'s rule, applied to a contract whose whole point is the declaration.

    A bar with a default is a bar somebody did not decide, and it would still enter the address
    -- so two callers who never thought about it would share an identity that claims they did.
    """
    for omitted in (
        {"minimum_researched_ratio": 0.5, "maximum_ranking_age_days": 3},
        {"minimum_tradable_ratio": 0.5, "maximum_ranking_age_days": 3},
        {"minimum_tradable_ratio": 0.5, "minimum_researched_ratio": 0.5},
    ):
        with pytest.raises(ValidationError):
            ShortlistGateSpec(**omitted)  # type: ignore[arg-type]

    for rejected in ({"minimum_tradable_ratio": 1.5}, {"minimum_researched_ratio": -0.1}):
        with pytest.raises(ValidationError):
            ShortlistGateSpec(
                **{
                    "minimum_tradable_ratio": 0.5,
                    "minimum_researched_ratio": 0.5,
                    "maximum_ranking_age_days": 3,
                    **rejected,
                }  # type: ignore[arg-type]
            )

    with pytest.raises(ValidationError):
        ShortlistGateSpec(
            minimum_tradable_ratio=0.5,
            minimum_researched_ratio=0.5,
            maximum_ranking_age_days=-1,
        )


# --------------------------------------------------------------------------------------------
# Every block code, separated
# --------------------------------------------------------------------------------------------


def test_the_tradable_ratio_is_the_tradeable_count_over_the_universe_and_not_over_the_scored() -> (
    None
):
    """The measurement that chose the denominator, reproduced on a fixture that can show it.

    Twenty names: five are never scored at all and eight more are suspended, so 7 of 20 can be
    bought. `tradeable / universe` is 0.35 and the funnel's own `tradeable / scored` is 7/15 =
    0.4667. A gate reading the funnel's rate would report this market as *better* covered
    precisely because five securities fell out one stage earlier.
    """
    ranking = _rank(
        unvalued=frozenset(TWENTY[:5]),
        suspended=frozenset(TWENTY[5:13]),
    )
    measurement = gate_shortlist(ranking=ranking, spec=_bars()).measurement

    assert measurement.universe_count == 20
    assert measurement.scored_count == 15
    assert measurement.tradeable_count == 7
    assert measurement.tradable_ratio == 7 / 20
    assert ranking.funnel.tradeability.tradeable_rate == 7 / 15
    assert measurement.tradable_ratio < ranking.funnel.tradeability.tradeable_rate
    assert measurement.scored_ratio == 15 / 20


def test_a_market_mostly_shut_is_refused_for_the_tradable_ratio_alone() -> None:
    """Sixteen of twenty suspended: 4 tradeable, a cut of 3, and every shortlisted name filled."""
    ranking = _rank(suspended=SHUT)

    assert ranking.funnel.coverage == "shortlisted"
    assert ranking.candidate_count == 3
    assert all(candidate.fill.status == "filled" for candidate in ranking.candidates)

    clearance = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["tradable_ratio_below_floor"]
    assert clearance.measurement.tradable_ratio == 0.2
    assert clearance.block_with_code("tradable_ratio_below_floor") is clearance.blocks[0]
    assert clearance.block_with_code("ranking_is_stale") is None


def test_the_same_market_clears_the_bar_it_actually_met() -> None:
    """The direction that makes the floor a threshold rather than a refusal in disguise."""
    ranking = _rank(suspended=SHUT)

    clearance = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.2))

    assert clearance.is_blocked is False
    assert len(clearance.admitted) == 3
    assert clearance.blocking_codes() == frozenset()


def test_a_shortlist_two_thirds_of_which_never_came_back_is_refused_for_the_research_rate() -> None:
    """The second coverage bar, on a market that met the first.

    One of three shortlisted names produced a signal, so `researched_ratio` is 1/3 and the two
    others are `unresearched` -- a fact about which runs finished rather than about the market,
    which is why it is a separate bar over a separate denominator.
    """
    ranking = _rank(researched=1)

    assert ranking.candidate_count == 1
    assert len(ranking.unresearched) == 2
    assert ranking.researched_rate == 1 / 3

    clearance = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.9, researched=0.5))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["researched_ratio_below_floor"]
    assert clearance.blocks[0].measured == 1 / 3
    assert clearance.blocks[0].required == 0.5


def test_a_funnel_that_shortlisted_nobody_is_refused_even_under_a_floor_of_zero() -> None:
    """`researched_ratio_not_measurable`, and the reason it is not a zero.

    A cut of 20 over a universe of 20 is `cut_exceeds_the_cross_section`, so `researched_rate` is
    `None` rather than `0.0`. A floor of zero admits every *number*, and this is not one -- a
    ratio that could not be computed has not met a bar of zero, it has not met anything. The
    funnel's own code travels in the detail rather than being restated as a fifth block code.
    """
    ranking = _rank(shortlist_size=20)

    assert ranking.funnel.coverage == "cut_exceeds_the_cross_section"
    assert ranking.researched_rate is None

    clearance = gate_shortlist(ranking=ranking, spec=_bars(researched=0.0))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["researched_ratio_not_measurable"]
    assert clearance.blocks[0].measured is None
    assert "cut_exceeds_the_cross_section" in clearance.blocks[0].detail
    assert clearance.measurement.researched_ratio is None
    assert clearance.measurement.shortlist_count == 0


def test_the_two_researched_codes_are_never_raised_together() -> None:
    """They are one `if`/`elif` over one quantity, and the pairing is asserted rather than
    assumed: a clearance carrying both would be one claiming a ratio was and was not a number."""
    for ranking in (_rank(researched=0), _rank(shortlist_size=20)):
        clearance = gate_shortlist(ranking=ranking, spec=_bars(researched=1.0))
        raised = clearance.blocking_codes()
        both = {"researched_ratio_below_floor", "researched_ratio_not_measurable"}
        assert len(raised & both) == 1


def test_a_list_assembled_too_long_after_its_session_is_refused_for_its_age_alone() -> None:
    """The freshness bar, with both coverage bars inert."""
    ranking = _rank(built_at=AS_OF + timedelta(days=90))

    clearance = gate_shortlist(ranking=ranking, spec=_bars(age=30))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["ranking_is_stale"]
    assert clearance.measurement.ranking_age_days == 90
    assert clearance.blocks[0].required == 30


def test_the_age_is_whole_calendar_days_floored_and_the_boundary_is_inclusive() -> None:
    """The rounding the limitation discloses, driven at the two places it can be seen.

    47 hours is 1 day and not 2, because the floor is taken; and a list exactly at the ceiling
    clears, because the bar is a maximum rather than a strict one.
    """
    assert (
        gate_shortlist(
            ranking=_rank(built_at=AS_OF + timedelta(hours=47)), spec=_bars()
        ).measurement.ranking_age_days
        == 1
    )
    assert (
        gate_shortlist(ranking=_rank(built_at=AS_OF), spec=_bars()).measurement.ranking_age_days
        == 0
    )

    at_the_bar = _rank(built_at=AS_OF + timedelta(days=5))
    assert gate_shortlist(ranking=at_the_bar, spec=_bars(age=5)).is_blocked is False
    assert gate_shortlist(ranking=at_the_bar, spec=_bars(age=4)).is_blocked is True


def test_a_list_assembled_before_the_session_it_is_about_is_malformed_and_not_stale() -> None:
    """A negative age is a look-ahead, so it raises rather than becoming a block code.

    `ShortlistGateError` and not a `ShortlistGateBlock`: a caller walking a year of `as_of`s has
    to keep going past a stale list, and must not keep going past one that could not have been
    built when it says it was.
    """
    with pytest.raises(ShortlistGateError, match="look-ahead"):
        gate_shortlist(ranking=_rank(built_at=AS_OF - timedelta(days=1)), spec=_bars())


def test_every_bar_is_evaluated_so_a_refusal_names_all_three_at_once() -> None:
    """No early return. A caller told only about the coverage would fix it and meet the
    staleness on the next run, and the ordering is the declared one rather than the fired one."""
    ranking = _rank(suspended=SHUT, researched=1, built_at=AS_OF + timedelta(days=90))

    clearance = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5, researched=0.9, age=30))

    assert [block.code for block in clearance.blocks] == [
        "tradable_ratio_below_floor",
        "researched_ratio_below_floor",
        "ranking_is_stale",
    ]
    assert clearance.blocking_codes() == {
        "tradable_ratio_below_floor",
        "researched_ratio_below_floor",
        "ranking_is_stale",
    }


def test_a_clearance_reporting_its_blocks_out_of_the_declared_order_is_refused() -> None:
    """The invariant `_blocks_for` is held to, driven against a hand-built record.

    A frozen dataclass with `slots=True` is still constructible directly, and the ordering is
    exactly the property a hand-built one would skip -- `CandidateRanking.__post_init__`'s own
    two-call-site reason.
    """
    ranking = _rank(suspended=SHUT)
    real = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5, age=0))
    reversed_blocks = tuple(reversed(real.blocks))

    assert len(reversed_blocks) == 2
    with pytest.raises(ShortlistGateError, match="blocks are reported in"):
        ShortlistClearance(
            manifest=real.manifest,
            ranking_content_digest=real.ranking_content_digest,
            measurement=real.measurement,
            blocks=reversed_blocks,
            admitted_or_none=None,
        )


def test_a_clearance_that_both_blocked_and_admitted_is_refused() -> None:
    """The pairing that would be a refusal which shipped, and its mirror."""
    ranking = _rank(suspended=SHUT)
    real = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5))

    with pytest.raises(ShortlistGateError, match="admitted exactly when nothing blocked"):
        ShortlistClearance(
            manifest=real.manifest,
            ranking_content_digest=real.ranking_content_digest,
            measurement=real.measurement,
            blocks=real.blocks,
            admitted_or_none=ranking.candidates,
        )
    with pytest.raises(ShortlistGateError, match="admitted exactly when nothing blocked"):
        ShortlistClearance(
            manifest=real.manifest,
            ranking_content_digest=real.ranking_content_digest,
            measurement=real.measurement,
            blocks=(),
            admitted_or_none=None,
        )


def test_a_clearance_repeating_one_bar_is_refused() -> None:
    ranking = _rank(suspended=SHUT)
    real = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5))

    with pytest.raises(ShortlistGateError, match="repeats a bar"):
        ShortlistClearance(
            manifest=real.manifest,
            ranking_content_digest=real.ranking_content_digest,
            measurement=real.measurement,
            blocks=(real.blocks[0], real.blocks[0]),
            admitted_or_none=None,
        )


# --------------------------------------------------------------------------------------------
# Blocked, admitted-with-candidates, admitted-with-none: three states
# --------------------------------------------------------------------------------------------


def test_the_three_states_are_reached_by_name_and_never_by_a_length() -> None:
    """The failure mode this issue exists to remove, as a **cross**.

    The blocked clearance has three candidates underneath it and the admitted-empty one has
    none, so neither can be read as the other -- and `bool()`, `len()` and iteration raise on
    all three, including on the two that cleared, which is the half `PanelReadOutcome` measured
    that two return values alone do not buy.
    """
    blocked = gate_shortlist(ranking=_rank(suspended=SHUT), spec=_bars(tradable=0.5))
    full = gate_shortlist(ranking=_rank(), spec=_bars(tradable=0.1))
    empty = gate_shortlist(ranking=_rank(researched=0), spec=_bars(researched=0.0))

    assert (blocked.is_blocked, full.is_blocked, empty.is_blocked) == (True, False, False)

    assert len(full.admitted) == 3
    assert empty.admitted == ()
    with pytest.raises(ShortlistGateError, match="tradable_ratio_below_floor"):
        _ = blocked.admitted

    assert blocked.admitted_or_none is None
    assert empty.admitted_or_none == ()
    assert full.admitted_or_none is not None

    for clearance in (blocked, full, empty):
        for attempt in (bool, len, list):
            with pytest.raises(ShortlistGateError, match="verdict, not a collection"):
                attempt(clearance)  # type: ignore[arg-type,operator]


def test_the_refusal_message_names_every_bar_that_failed_and_points_at_the_merged_shape() -> None:
    """A caller that reads `admitted` on a blocked clearance is told what failed and what to
    call instead, rather than receiving an empty tuple it would read as a clean market."""
    clearance = gate_shortlist(
        ranking=_rank(suspended=SHUT, built_at=AS_OF + timedelta(days=90)),
        spec=_bars(tradable=0.5, age=30),
    )

    with pytest.raises(ShortlistGateError) as raised:
        _ = clearance.admitted

    message = str(raised.value)
    assert "tradable_ratio_below_floor" in message
    assert "ranking_is_stale" in message
    assert "admitted_or_none" in message


def test_an_admitted_clearance_still_reports_what_it_measured() -> None:
    """ "Cleared" is a verdict rather than silence, `DependencyClearance.notices`' reason: a list
    that scraped over a bar and one that sailed over it are different and both are readable."""
    scraped = gate_shortlist(ranking=_rank(suspended=SHUT), spec=_bars(tradable=0.2))
    sailed = gate_shortlist(ranking=_rank(), spec=_bars(tradable=0.2))

    assert scraped.is_blocked is False
    assert sailed.is_blocked is False
    assert scraped.measurement.tradable_ratio == 0.2
    assert sailed.measurement.tradable_ratio == 1.0
    assert scraped.measurement != sailed.measurement


def test_a_refusal_carries_the_whole_ranking_and_truncates_nothing() -> None:
    """`this_gate_refuses_a_list_and_can_never_repair_one`, driven.

    The blocked clearance's measurement reports the ranking's real counts and its digest is the
    ranking's own, so a caller can say *which* list was refused -- and there is no shorter list
    anywhere on the record for one to be confused with.
    """
    ranking = _rank(suspended=SHUT)
    clearance = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.5))

    assert clearance.ranking_content_digest == ranking.content_digest
    assert clearance.measurement.candidate_count == ranking.candidate_count == 3
    assert clearance.measurement.shortlist_count == ranking.funnel.shortlist_count == 3
    assert clearance.manifest.ranking_manifest_id == ranking.manifest.ranking_manifest_id

    admitted = gate_shortlist(ranking=ranking, spec=_bars(tradable=0.2)).admitted
    assert [candidate.subject for candidate in admitted] == [
        entry.subject for entry in ranking.funnel.shortlist
    ]


def test_the_gate_adds_no_risk_flag_and_removes_none() -> None:
    """`an_admitted_clearance_is_a_coverage_and_age_verdict_and_not_a_quality_one`.

    Every admitted candidate is the ranking's own record, identical object for identical object,
    so a reader of an admitted list gets `V2-P4-005`'s flags unaltered and this gate has added
    no reassurance of its own.
    """
    ranking = _rank()
    admitted = gate_shortlist(ranking=ranking, spec=_bars()).admitted

    assert admitted == ranking.candidates
    assert all(admitted[index] is ranking.candidates[index] for index in range(len(admitted)))


def test_a_measurement_is_offered_whole_rather_than_as_a_ratio_a_reader_has_to_trust() -> None:
    """The four counts beside the two ratios: 0.35 from a universe of 20 that scored 15 and one
    from a universe of 20 that scored 7 are different markets and the ratio alone is equal."""
    thin_at_stage_one = gate_shortlist(
        ranking=_rank(unvalued=frozenset(TWENTY[:13])), spec=_bars()
    ).measurement
    thin_at_stage_two = gate_shortlist(
        ranking=_rank(suspended=frozenset(TWENTY[7:])), spec=_bars()
    ).measurement

    assert thin_at_stage_one.tradeable_count == thin_at_stage_two.tradeable_count == 7
    assert thin_at_stage_one.tradable_ratio == thin_at_stage_two.tradable_ratio == 7 / 20
    assert thin_at_stage_one.scored_count == 7
    assert thin_at_stage_two.scored_count == 20
    assert thin_at_stage_one.scored_ratio != thin_at_stage_two.scored_ratio
    assert isinstance(thin_at_stage_one, ShortlistMeasurement)


# --------------------------------------------------------------------------------------------
# D16's prohibition, extended to the gate
# --------------------------------------------------------------------------------------------


def _lint(contract: str) -> int:
    """`contained_lint_imports` limited to one contract -- this file's whole use of the linter.

    The containment is `tests/import_linter_containment.py` rather than eight lines copied here.
    An earlier version of this docstring argued for the copy: importing would make "one collected
    test module the import-time dependency of another and give pytest two paths to the same file",
    and each copy came with a private `re.findall` over its own source to keep a later bare call
    out. `V2-P4-089` measured what that convention is worth -- a fourth file imported the raw CLI
    under the wrapper's own name, every private regex was keyed on a spelling it did not have, and
    six logging guards went hollow. The objection was about importing from a *collected* module
    and it stays true; the containment module is not one, exactly as `tests/offline_guard.py` and
    `tests/panel_fixtures.py` are not.
    """
    return contained_lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=(contract,),
    )


def test_the_lint_wrapper_leaves_the_logging_state_it_found() -> None:
    """`_lint` above goes through the containment, in both directions.

    A logger that was enabled before `_lint` is enabled after it, and one that was already
    disabled stays disabled -- because `contained_lint_imports` puts back a snapshot rather than
    blanket-enabling whatever it can reach. That the containment itself works is
    `tests/unit/test_import_layering.py::
    test_running_the_import_linter_leaves_every_existing_logger_enabled`'s job; what is checked
    here is that this file's own helper reaches it rather than the raw CLI.
    """
    enabled = logging.getLogger("openalpha_cn.probe.shortlist_gate_lint_enabled")
    disabled = logging.getLogger("openalpha_cn.probe.shortlist_gate_lint_disabled")
    enabled.disabled = False
    disabled.disabled = True

    assert _lint("ranking-creates-no-portfolio-order") == 0

    assert not enabled.disabled
    assert disabled.disabled


def test_the_gate_cannot_reach_the_three_modules_that_make_an_order() -> None:
    """D16's `绝不直接创建组合订单` one step further down the same path, driven in both directions.

    `V2-P4-005` added `ranking-creates-no-portfolio-order` for `candidate_ranking.py`;
    `V2-P4-023` widened its source list to this module, because a gate that could construct a
    `PortfolioOrder` would be one place in which "this list was refused" and "an order was made
    from it" could both be true. The real module passes and the same file with one import added
    fails, so the pass cannot be vacuous.
    """
    assert _lint("ranking-creates-no-portfolio-order") == 0

    original = MODULE_PATH.read_text(encoding="utf-8")
    try:
        MODULE_PATH.write_text(
            original.replace(
                "from openalpha_cn.domain._identity import stable_model_id",
                "from openalpha_cn.domain.portfolio import PortfolioOrder\n"
                "from openalpha_cn.domain._identity import stable_model_id\n"
                "_ORDER = PortfolioOrder",
            ),
            encoding="utf-8",
        )
        assert _lint("ranking-creates-no-portfolio-order") == 1, (
            "lint-imports must reject shortlist_gate -> domain.portfolio; if this passes, the "
            "gate can build the order it just refused to let anybody publish"
        )
    finally:
        MODULE_PATH.write_text(original, encoding="utf-8")

    assert _lint("ranking-creates-no-portfolio-order") == 0


def test_the_gate_reaches_neither_a_store_nor_the_root_that_owns_run_cycle() -> None:
    """The two study contracts this module joined on arrival, driven over it by name.

    `tests/unit/test_import_layering.py` holds the source lists against the directory; this is
    the other half -- that the module the lists now name actually passes them.
    """
    assert _lint("backtest-studies-touch-no-store") == 0
    assert _lint("backtest-studies-reach-no-composition-root") == 0
    assert _lint("backtest-no-numeric-stack-or-panel-plane") == 0


def test_this_gate_cannot_measure_dataset_freshness_because_it_cannot_reach_the_panel() -> None:
    """`dataset_level_staleness_is_v2_p1_013s_and_is_not_restated_or_re_measured_here`.

    Asserted off the module's own source rather than off a graph, because the claim is that the
    name does not appear at all: a gate that imported `panel_gate` to "double check" would be
    restating a verdict it has no inputs for, and `backtest-no-numeric-stack-or-panel-plane`
    would refuse it one line later.

    `V2-P4-035` also leaned on this list as the gate's half of the order-machinery pin, and
    `V2-P4-047` measured that it could not carry the weight: the filter read
    `line.startswith(("import ", "from "))` and therefore saw only column-zero imports, so an
    indented `from openalpha_cn.backtest.execution import AShareExecutionPolicy` inside a
    function filled a real order here (`filled sell 200 10.20 6.04`) with `lint-imports` at
    8 kept / 0 broken. The filter is indentation-blind now. The order claim itself has moved to
    `tests/unit/backtest/test_ranking_sources_fill_no_order.py`, which binds it behaviourally,
    because a second bypass -- adding the re-exported names to the `candidate_ranking` block
    below -- keeps every line here byte-identical and adds no import-graph edge at all.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    imports = [
        stripped
        for line in source.splitlines()
        if (stripped := line.strip()).startswith(("import ", "from ")) and " import " in stripped
    ]

    assert not any("openalpha_cn.panel" in line for line in imports)
    assert not any("openalpha_cn.storage" in line for line in imports)
    assert not any("openalpha_cn.runtime" in line for line in imports)
    assert sorted(line.split()[1] for line in imports if line.startswith("from openalpha_cn")) == [
        "openalpha_cn.backtest.candidate_ranking",
        "openalpha_cn.domain._identity",
        "openalpha_cn.domain.time",
    ]


def test_a_block_is_a_plain_record_a_caller_can_log_without_asking_this_module_anything() -> None:
    """Both halves of the comparison ride on the block, so one log line is self-contained."""
    clearance = gate_shortlist(ranking=_rank(suspended=SHUT), spec=_bars(tradable=0.5))
    block = clearance.blocks[0]

    assert isinstance(block, ShortlistGateBlock)
    assert block.measured == 0.2
    assert block.required == 0.5
    assert "0.2000" in block.detail
    assert "0.5000" in block.detail
