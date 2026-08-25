"""The acceptance demonstration for the factor face, on a real stored panel (`V2-P3-015`).

`tests/integration/test_factor_interfaces.py` drives the three faces against the **shipped**
registry, where both derived specs declare `min_cross_section=100` and an eight-name market
therefore reports `not_measured` in every attribution cell. That is the honest answer for that
configuration and it is the wrong instrument for one question: whether the declared
`retention_floor` reaches the verdict.

This file answers that one, through the same `factor_view.run_factor_experiment` all three faces
call, with a **probe transform and a probe neutralisation** whose floors fit an eight-name cross
section. `factor_request` takes the three registries as parameters with the build's own as
defaults -- `compute_factor`'s `evaluators` arrangement -- so a probe spec is driven through the
one resolver rather than through a second one written for tests.

## What the fixture chooses, and why each choice is a choice

**The evaluator's sign.** `reversal_1d`'s formula is `close[t] / close[t-1] - 1` and its declared
direction is `lower_is_better`; the generated panel moves every close by the same half yuan a
session, so a one-session return is `0.5 / previous close` and orders the cross section exactly as
the *next* session's return does. On that grid the shipped factor's oriented IC is exactly `-1.0`
-- it anti-predicts perfectly -- and every cell of the grid is `no_baseline`, which is the correct
verdict for a factor that never worked and one on which no floor can decide anything.
`test_the_shipped_evaluator_on_this_panel_reports_no_baseline_rather_than_removed` pins that, so
the sign is a measured property of this fixture rather than a claim.

`_predicts` is the same formula negated. It produces the same magnitudes with the opposite sign,
so the same panel yields a raw and processed mean IC of exactly `1.0` and a neutralised one of
`2/7`, and the acceptance criterion's cell becomes reachable. Choosing a fixture so that a verdict
is *reachable* is what `test_factor_experiment.py` does with its four-element permutation; what
would be dishonest is choosing one so that a verdict is *favourable*, and the neutralised tier
here loses 71% of the IC and 94% of the spread.

**The market capitalisations.** The generator writes `total_mv = 1.0` on every row, which is a
size design with no within-industry dispersion at all -- `degenerate_design`, and every residual
would be coded. `test_factor_neutralizations.py` replaces the column for the same reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE
from test_factor_interfaces import (
    BASELINE,
    BUILT_AT,
    PREDICTION_DAYS,
    RUN_AS_OF,
    store_three_tiers,
)

from openalpha_cn.backtest.factor_experiment import experiment_payload, open_experiment
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformRegistry,
    FactorTransformSpec,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.factor_view import (
    FACTOR_RUN_LIMITATION_CODES,
    KNOWN_FACTOR_RUN_LIMITATIONS,
    FactorRunBlockedError,
    acceptance_rows,
    attribution_rows,
    everything_is_unmeasured,
    factor_request,
    panel_store,
    run_factor_experiment,
    tier_rows,
)
from openalpha_cn.panel_factors import FactorWindow
from openalpha_cn.storage.factor_experiments import FileExperimentStore

PROBE_TRANSFORM: Final[FactorTransformSpec] = FactorTransformSpec(
    key="probe_zscore",
    version=1,
    winsorization=WinsorizationPolicy(method="none"),
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
"""A transform whose floor fits eight names. `method="none"` because a 1% winsorization of eight
points clips one of them -- `min_cross_section = 1 / lower_quantile` is `CROSS_SECTION_STANDARD`'s
own derivation of why -- and this fixture is about the neutralisation step rather than the
clipping one."""

PROBE_NEUTRALIZATION: Final[FactorNeutralizationSpec] = FactorNeutralizationSpec(
    key="probe_neutral",
    version=1,
    industry_level="L1",
    market_cap_measure="total_mv",
    market_cap_scale="log",
    participation="measured_only",
    min_industry_members=2,
    min_cross_section=2,
)
"""`INDUSTRY_AND_SIZE`'s settings with the two floors lowered to what the contract itself allows:
`min_industry_members=2` is `FactorNeutralizationSpec`'s own floor, not a relaxation."""

PROBE_TRANSFORMS: Final[FactorTransformRegistry] = FactorTransformRegistry((PROBE_TRANSFORM,))
PROBE_NEUTRALIZATIONS: Final[FactorNeutralizationRegistry] = FactorNeutralizationRegistry(
    (PROBE_NEUTRALIZATION,)
)

PROBE_PARAMETERS: Final[dict[str, Any]] = {
    **BASELINE,
    "transform": "probe_zscore/v1",
    "neutralization": "probe_neutral/v1",
}

RAW_MEAN_IC: Final[float] = 1.0
NEUTRALIZED_MEAN_IC: Final[float] = 0.28571428571428564
"""The neutralised tier's mean rank IC on this fixture: two sevenths, to the last two bits.

Written as the measured float rather than as `2 / 7`, and the difference is the point rather than
a nuisance. The neutralised cross section admits seven of the eight names -- one is alone in its
industry on these sessions and is coded `industry_missing` -- so the exact rank correlation is a
seventh-denominator rational; `2 / 7` in double precision is `0.2857142857142857` and this is
`0.28571428571428564`, because `factor_ic._pearson` computes a **scaled product-moment**
correlation over average ranks rather than the rank-difference formula. `test_factor_experiment.py`
makes the same distinction about its own exact zero: "these two agree at the last bit" is a claim
about floating point rather than about algebra, so the constant is the one the code produces and
the assertions compare it with `==`.
"""


def _predicts(window: FactorWindow) -> float | None:
    """`reversal_1d`'s formula, negated. See this module's docstring for why the sign is chosen."""
    closes = window.series("daily", "close")
    if closes[-2] == 0.0:
        return None
    return -(closes[-1] / closes[-2] - 1.0)


def _run(runtime_dir: Path, **overrides: Any) -> tuple[Any, str]:
    """One run through the shared resolver and the shared runner, with the probe registries."""
    parameters = {**PROBE_PARAMETERS, **overrides}
    request = factor_request(
        **parameters,
        transforms=PROBE_TRANSFORMS,
        neutralizations=PROBE_NEUTRALIZATIONS,
    )
    return run_factor_experiment(
        panel_store(runtime_dir),
        request,
        built_at=BUILT_AT,
        experiments=FileExperimentStore(runtime_dir / "experiments"),
    )


@pytest.fixture
def predictive(tmp_path: Path) -> Path:
    store_three_tiers(
        tmp_path,
        evaluator=_predicts,
        transform=PROBE_TRANSFORM,
        neutralization=PROBE_NEUTRALIZATION,
    )
    return tmp_path


def test_a_factor_whose_edge_is_its_exposure_reports_removed_on_the_neutralisation_step(
    predictive: Path,
) -> None:
    """The acceptance criterion, end to end, off a stored panel.

    *否则分不清「因子有效」与「暴露没控住」.* The raw and processed tiers rank the cross section
    perfectly against the realised forward returns; the neutralised residuals keep two sevenths of
    that ordering and a twentieth of the money. The reader's action is **one lookup by name**:
    `attribution(from_tier="processed", to_tier="neutralized", statistic="mean_ic").verdict` is
    `"removed"` at the declared floor of 0.4, and the two verdicts that would let a reader
    proceed (`survives`, `amplified`) are different strings.

    Nothing here compares two numbers to reach the verdict -- the artifact did that -- and the
    numbers it compared are asserted separately so that a cell agreeing with a wrong pair would
    still fail.
    """
    record, write = _run(predictive)
    artifact = record.artifact

    assert write == "created"
    assert artifact.tier_report("raw").ic.mean_ic == RAW_MEAN_IC
    assert artifact.tier_report("processed").ic.mean_ic == RAW_MEAN_IC
    assert artifact.tier_report("neutralized").ic.mean_ic == NEUTRALIZED_MEAN_IC
    cell = artifact.attribution(from_tier="processed", to_tier="neutralized", statistic="mean_ic")
    assert cell.verdict == "removed"
    assert cell.retention == NEUTRALIZED_MEAN_IC
    assert (cell.from_value, cell.to_value) == (RAW_MEAN_IC, NEUTRALIZED_MEAN_IC)
    # The step before it kept everything, so `removed` names the neutralisation and not the
    # transform -- which is the whole reason the grid has three steps rather than one.
    assert (
        artifact.attribution(from_tier="raw", to_tier="processed", statistic="mean_ic").verdict
        == "survives"
    )


def test_the_declared_floor_decides_that_verdict_and_nothing_else_about_the_run(
    predictive: Path,
) -> None:
    """Move the floor across the measured retention; the verdict moves and the numbers do not.

    The falsification the acceptance test above needs beside it. Two sevenths is `0.2857...`, so a
    floor of `0.2` calls the same cell `survives` and a floor of `0.4` calls it `removed` -- on
    identical tier reports. Without this, `removed` could be what this fixture reports at every
    floor, and the declared line would be a field nobody had shown reaches a decision.

    The two runs are also two experiments: `retention_floor` is a field of `FactorExperimentSpec`,
    so moving it mints a new `experiment_id` rather than restating the old one. That is the
    property `refuse_a_restated_experiment` rests on, and it is why both runs can be stored.
    """
    lenient, _ = _run(predictive, retention_floor=0.2)
    strict, _ = _run(predictive, retention_floor=0.4)

    def verdict(record: Any) -> str:
        return record.artifact.attribution(
            from_tier="processed", to_tier="neutralized", statistic="mean_ic"
        ).verdict

    assert verdict(lenient) == "survives"
    assert verdict(strict) == "removed"
    assert lenient.experiment_id != strict.experiment_id
    assert (
        lenient.artifact.tier_report("neutralized").ic.mean_ic
        == strict.artifact.tier_report("neutralized").ic.mean_ic
        == NEUTRALIZED_MEAN_IC
    )


def test_the_two_statistics_come_apart_on_this_fixture(predictive: Path) -> None:
    """`mean_ic` and `mean_spread` are two readings of "the factor worked" and they disagree.

    At a floor of `0.2` the ordering survives neutralisation and the money does not: the IC keeps
    two sevenths and the net spread keeps about a twentieth, because the surviving names are the
    ones an A-share round trip charges most to hold. That is why the grid attributes on two
    statistics rather than one, and a fixture on which the two agreed would leave the second
    column unexercised.
    """
    record, _ = _run(predictive, retention_floor=0.2)

    ordering = record.artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_ic"
    )
    money = record.artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_spread"
    )

    assert ordering.verdict == "survives"
    assert money.verdict == "removed"
    assert money.retention is not None and ordering.retention is not None
    assert money.retention < ordering.retention


def test_the_shipped_evaluator_on_this_panel_reports_no_baseline_rather_than_removed(
    tmp_path: Path,
) -> None:
    """The sign this fixture negates, measured rather than asserted in prose.

    With the shipped formula the same panel gives an oriented mean IC of exactly `-1.0`, and the
    grid says `no_baseline` -- not `removed`. The two are kept apart deliberately by
    `V2-P3-014`: a factor whose raw statistic is at or below zero had no edge for the
    neutralisation to have taken, and reporting this module's loudest verdict about it would be
    the "only verified existence and not magnitude" shape.
    """
    store_three_tiers(tmp_path, transform=PROBE_TRANSFORM, neutralization=PROBE_NEUTRALIZATION)

    record, _ = _run(tmp_path)

    assert record.artifact.tier_report("raw").ic.mean_ic == -RAW_MEAN_IC
    assert {cell.verdict for cell in record.artifact.attributions} == {"no_baseline"}
    assert all(cell.retention is None for cell in record.artifact.attributions)


def test_the_survival_row_corroborates_the_grid_independently(predictive: Path) -> None:
    """The fourth upstream study, which the grid cannot see and a reader should.

    A retention and a survival answer different questions and can disagree in the way that
    matters. Here they agree, and that agreement is evidence: the processed tier is in rank
    lockstep with raw (`undeclared_lockstep`, the rounding-boundary code, so the transform
    reordered nothing) while the neutralised tier is `distinct` -- so the IC that vanished
    vanished because the *values were reordered*, not because a statistic drifted.
    """
    record, _ = _run(predictive)

    processed = record.artifact.tier_report("processed").survival
    neutralized = record.artifact.tier_report("neutralized").survival

    assert record.artifact.tier_report("raw").survival is None
    assert processed is not None and processed.verdict == "undeclared_lockstep"
    assert neutralized is not None and neutralized.verdict == "distinct"


def test_the_terminal_rendering_shows_every_cell_including_the_ones_with_no_number(
    predictive: Path,
) -> None:
    """`attribution_rows` and `tier_rows` are what `openalpha factor run` prints without `--json`.

    Six rows always, in `ATTRIBUTION_CELL_ORDER`, and three tier rows in `FACTOR_TIER_ORDER`: a
    grid that dropped the cells it had nothing to say about would make "the neutralised tier
    measured nothing" and "nobody asked about the neutralised tier" one reading, on the one face
    where a human is doing the reading.
    """
    record, _ = _run(predictive)

    cells = attribution_rows(record)
    rows = tier_rows(record)

    assert len(cells) == 6
    assert [step for step, _statistic, _retention, _verdict in cells] == [
        "raw->processed",
        "raw->processed",
        "processed->neutralized",
        "processed->neutralized",
        "raw->neutralized",
        "raw->neutralized",
    ]
    assert ("processed->neutralized", "mean_ic", repr(NEUTRALIZED_MEAN_IC), "removed") in cells
    assert [tier for tier, _coverage, _ic, _spread in rows] == [
        "raw",
        "processed",
        "neutralized",
    ]
    assert rows[0][1] == "measured"


def test_a_second_run_of_one_declaration_is_a_no_op_and_returns_the_stored_document(
    predictive: Path,
) -> None:
    """`refuse_a_restated_experiment`'s admitted direction, at the boundary that holds artifacts.

    A re-derivation that reproduces its own content must be writable, or the identity makes a
    rebuild impossible and its predecessor unreproducible. Driven at two different `built_at`
    clocks, because `built_at` is a field of the document and outside every digest -- so the two
    payloads differ in bytes and agree in content, which is exactly the case a store comparing
    bytes would have refused.
    """
    first, first_write = _run(predictive)
    second, second_write = run_factor_experiment(
        panel_store(predictive),
        factor_request(
            **PROBE_PARAMETERS,
            transforms=PROBE_TRANSFORMS,
            neutralizations=PROBE_NEUTRALIZATIONS,
        ),
        built_at=BUILT_AT.replace(year=2027),
        experiments=FileExperimentStore(predictive / "experiments"),
    )

    assert (first_write, second_write) == ("created", "unchanged")
    assert second.content_digest == first.content_digest
    assert second.built_at == first.built_at == BUILT_AT
    assert FileExperimentStore(predictive / "experiments").list_ids() == (first.experiment_id,)


def test_the_run_is_refused_when_the_range_names_a_day_with_no_stored_cross_section(
    predictive: Path,
) -> None:
    """An empty range is a refusal and not an experiment over nothing.

    `V2-P1-013`'s empty success, on this plane: a report over zero prediction days satisfies every
    per-day check vacuously, and `ICSummary` would refuse it four frames later in a vocabulary
    about a sample rather than about a request.
    """
    with pytest.raises(FactorRunBlockedError, match="no stored reversal_1d/v1 cross section"):
        _run(
            predictive,
            start=PREDICTION_DAYS[0].replace(day=5),
            end=PREDICTION_DAYS[0].replace(day=5),
        )


def test_the_declared_limitations_are_the_ones_this_face_carries() -> None:
    """The registry, held as a set literal so a rename or a deletion fails here.

    `tests/unit/test_known_limitation_registries.py` requires every declared code to appear as a
    string literal in executable test code; this is that literal for this registry, compared for
    equality rather than membership because a membership assertion cannot see a removal.

    **One entry has been replaced twice rather than edited, and this literal is how each was
    noticed.** `V2-P3-015` declared
    `nothing_in_this_repository_builds_a_factor_panel_from_a_command_line`, and `V2-P3-019`
    shipped `openalpha factor build`, which makes the sentence false. A false disclosure is worse
    than no disclosure -- it is the shape this whole registry mechanism exists to stop -- so the
    entry became `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`, the part
    of it that was still true. `V2-P4-028` made *that* sentence false in turn: the industry read
    is day-scoped now, so the third tier is not bounded by any year's stored horizon, and the
    entry became `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`, which
    is the one-session bound that survives. Nothing here was weakened to accommodate either
    change; the equality went red both times, which is the mechanism working.

    **`V2-P4-067(b)` added a ninth and it arrived from another registry.**
    `the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered` replaces
    `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_unreadable_factor_refusal_names_the_`
    `command_that_builds_it`, whose stated reason -- `neutralized` having two partition
    spellings -- was measured false. The boundary it recorded (a tier asymmetry) is gone; the
    boundary that survives (the one store state the remedy fires on) belongs to both faces, and
    it is declared here because this is the face whose reproduction command the row names.
    """
    assert {
        "the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered",
        "a_closed_day_and_an_unclosed_session_share_one_exit_code",
        "the_three_tiers_must_have_been_built_at_the_same_instants",
        "the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed",
        "the_document_store_holds_bytes_and_re_derives_no_number",
        "a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it",
        "the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market",
        "a_name_never_announced_inside_the_requested_years_is_priced_as_ordinary",
        "a_security_with_no_stored_adjustment_history_is_counted_unmatched_not_refused",
        "the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine",
    } == FACTOR_RUN_LIMITATION_CODES
    assert all(limitation.detail.strip() for limitation in KNOWN_FACTOR_RUN_LIMITATIONS)


def test_a_grid_with_a_real_verdict_is_not_reported_as_unmeasured(predictive: Path) -> None:
    """`everything_is_unmeasured` is `False` on the one fixture that reaches a real verdict.

    The other half of `tests/integration/test_factor_interfaces.py::
    test_the_acceptance_row_is_marked_and_an_unmeasured_grid_is_warned_about`, and it is what stops
    that test from passing on a predicate that returns `True` unconditionally. This file's probe
    registry is the only configuration in the suite whose attribution grid carries a decided
    verdict on an eight-name market, which makes it the only place this direction can be driven.

    The `removed` cell is asserted beside it, so "not everything is unmeasured" is grounded in the
    verdict the acceptance criterion is actually read off rather than in any non-`not_measured`
    cell at all.
    """
    record, _ = _run(predictive)

    assert not everything_is_unmeasured(record)
    assert ("processed->neutralized", "mean_ic", repr(NEUTRALIZED_MEAN_IC), "removed") in (
        attribution_rows(record)
    )
    assert acceptance_rows(record) == (("mean_ic", "removed"), ("mean_spread", "removed"))


def test_the_run_reads_the_panel_at_the_stated_instant_and_not_at_a_wall_clock(
    predictive: Path,
) -> None:
    """`a_run_is_evaluated_at_one_as_of_and_the_labels_are_read_at_it`, as a driven property.

    The run is given an `as_of` after every stored session and a `built_at` a year later, and the
    experiment it produces names the first: `built_at` is recorded on the record and reaches no
    digest, so two clocks cannot make two experiments -- and the panel was read at the `as_of` the
    caller stated rather than at the moment the process happened to run.
    """
    record, _ = _run(predictive)

    assert record.built_at == BUILT_AT
    assert record.artifact.tiers[0].as_ofs == tuple(
        datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC) for day in PREDICTION_DAYS
    )
    assert all(instant < RUN_AS_OF for instant in record.artifact.tiers[0].as_ofs)
    assert record.artifact.spec.ic.definition.qualified_key == "reversal_1d/v1"
    assert EXCHANGE == "SZSE"


# --------------------------------------------------------------------------------------------
# V2-P3-007's own acceptance criterion, off a stored panel and out of a sealed document
# --------------------------------------------------------------------------------------------


def _by_price(window: FactorWindow) -> float | None:
    """A factor whose ordering is the last close, so the cut is a cut by price."""
    return window.series("daily", "close")[-1]


def _by_cheapness(window: FactorWindow) -> float | None:
    """The same factor negated, so the same cut puts the other end of the price range long."""
    closes = window.series("daily", "close")
    return -closes[-1]


SMALL_POSITION: Final[Decimal] = Decimal("2000")
"""A declared position capital two of the sixteen `(security, as_of)` pairs cannot buy a lot at.

`AShareExecutionPolicy` refuses a position whose declared budget does not reach one board lot's
cost and codes it `below_board_minimum`, and this panel's closes span far enough that ¥2,000 buys
a lot of most of its names and of neither of the two dearest. `BASELINE`'s own ¥100,000 refuses
nobody, which is what every other test in this file runs at and is why this constant is declared
here rather than moved into the baseline.
"""


def test_the_long_groups_execution_rate_is_readable_in_the_stored_document(tmp_path: Path) -> None:
    """**`V2-P3-007`'s acceptance criterion, end to end.** *让统计上好看但不可实施的信号显形.*

    One market, two orderings of it, at a declared position capital the two dearest names cannot
    buy a lot at. Both runs report **the same funnel** -- 16 offered, 16 valued, 16 admissible, 15
    scored, 13 held, an execution rate of `13 / 15` -- because the same two securities are refused
    in both and a funnel does not know which quantile they landed in. What separates them is one
    row of one table: the long group holds 5 of 7 where the signal loads on the names the study
    cannot afford and 7 of 7 where it does not, so `mean_top_group_execution_shortfall` is
    `+0.158` against `-0.134`.

    **How this differs from the unit fixture, stated rather than glossed.**
    `tests/unit/backtest/test_factor_tradeability.py::
    test_the_same_ic_and_the_same_funnel_are_told_apart_by_the_top_groups_execution_rate` drives
    two markets against *one* cross section, so the IC there is the same object's and cannot
    separate them at all. Here the two runs share a market and differ in the ordering, so the IC
    is `-1.0` against `+1.0` and does separate them. That is asserted below rather than left out,
    because the claim this test makes is the narrower one: **the finding lives at a level the
    period-level coverage report averages away, and it is readable out of a sealed artifact
    produced by the real face.** Every number here is taken off the reopened document.
    """
    documents = {}
    for name, evaluator in (("unimplementable", _by_cheapness), ("implementable", _by_price)):
        runtime = tmp_path / name
        runtime.mkdir()
        store_three_tiers(
            runtime,
            evaluator=evaluator,
            transform=PROBE_TRANSFORM,
            neutralization=PROBE_NEUTRALIZATION,
        )
        record, _write = _run(runtime, position_capital=SMALL_POSITION)
        documents[name] = open_experiment(experiment_payload(record))

    bad = documents["unimplementable"].artifact.tier_report("raw")
    good = documents["implementable"].artifact.tier_report("raw")

    assert bad.tradeability.funnel == good.tradeability.funnel
    assert bad.tradeability.funnel.scored_count == 15
    assert bad.tradeability.funnel.held_count == 13
    assert bad.tradeability.funnel.execution_rate == good.tradeability.funnel.execution_rate
    assert bad.tradeability.group == good.tradeability.group == 0
    assert [row.held_count for row in bad.tradeability.by_group] == [5, 8]
    assert [row.held_count for row in good.tradeability.by_group] == [7, 6]
    assert bad.tradeability.mean_top_group_execution_rate == 0.7083333333333333
    assert good.tradeability.mean_top_group_execution_rate == 1.0
    assert bad.tradeability.mean_top_group_execution_shortfall > 0.0
    assert good.tradeability.mean_top_group_execution_shortfall < 0.0
    # The refusal is named as well as counted, so a reader knows the remedy is a bigger position
    # rather than a different universe.
    assert dict(bad.tradeability.by_group[0].excluded_by_outcome)["below_board_minimum"] == 2
    assert dict(good.tradeability.by_group[0].excluded_by_outcome)["below_board_minimum"] == 0
    # Stated rather than glossed: on this fixture the IC separates them too. See the docstring.
    assert (bad.ic.mean_ic, good.ic.mean_ic) == (-1.0, 1.0)


def test_the_declared_participation_cap_moves_a_capacity_in_the_stored_document(
    predictive: Path,
) -> None:
    """**The required option that decided nothing, measured on the document it now decides.**

    `--participation-cap` has no default and its help text calls it a policy the four upstream
    studies refuse to choose for the caller. Until the tradeability summary reached a tier row,
    two runs at two caps differed in `experiment_id`, `content_digest`, `built_at` and the write
    verdict -- four identity leaves -- and in **no measured quantity anywhere in the artifact**.

    At the fixture's flat ¥10,000,000 of session turnover and `BASELINE`'s ¥100,000 position, a 1%
    cap gives a `capital_multiple` of exactly `1.0` -- the boundary at which the declared capital
    is exactly the capacity -- and a 50% cap gives `50.0`. The document diff is taken leaf by leaf
    so the claim is "these leaves moved and no others" rather than "something moved": six capacity
    leaves across three tiers, plus the four identity leaves that always move.
    """
    cheap, _one = _run(predictive, participation_cap=Decimal("0.01"))
    rich, _two = _run(predictive, participation_cap=Decimal("0.5"))

    for tier in ("raw", "processed", "neutralized"):
        left = cheap.artifact.tier_report(tier).tradeability
        right = rich.artifact.tier_report(tier).tradeability
        assert left.binding_capital_multiple == 1.0
        assert right.binding_capital_multiple == 50.0
        assert right.mean_capital_multiple == left.mean_capital_multiple * 50.0
        assert left.mean_concentration == right.mean_concentration == 1.0
        assert left.funnel == right.funnel
        # Which session the capacity was sized on is carried rather than assumed: this face asks
        # the entry session, which is the diagnostic reading the contract admits and names.
        assert left.sized_at_the_entry_session_count == len(left.as_ofs)
        assert left.sized_before_the_entry_session_count == 0
    differing = _differing_leaves(
        json.loads(experiment_payload(cheap)), json.loads(experiment_payload(rich))
    )
    capacity = {path for path in differing if "capital_multiple" in path}
    identity = {path for path in differing if "capital_multiple" not in path}
    assert capacity == {
        f"/artifact/tiers/{index}/tradeability/{name}"
        for index in range(3)
        for name in ("binding_capital_multiple", "mean_capital_multiple")
    }
    assert identity == {
        "/artifact/spec/tradeability/participation_cap",
        "/sealed_digest",
    }
    assert cheap.experiment_id != rich.experiment_id


def _differing_leaves(left: Any, right: Any, trail: str = "") -> list[str]:
    """Every JSON path at which two rendered documents carry different scalars.

    Written here rather than imported from the unit suite because the two files are two
    independent statements of the same audit -- `tests/unit/backtest/test_factor_experiment.py`
    diffs two records built in one process and this diffs two documents produced by the face off a
    stored panel, and a shared helper would make one failure hide the other.
    """
    if isinstance(left, dict) and isinstance(right, dict) and set(left) == set(right):
        return [
            path
            for key in left
            for path in _differing_leaves(left[key], right[key], f"{trail}/{key}")
        ]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            path
            for index, (one, other) in enumerate(zip(left, right, strict=True))
            for path in _differing_leaves(one, other, f"{trail}/{index}")
        ]
    return [] if left == right else [trail]
