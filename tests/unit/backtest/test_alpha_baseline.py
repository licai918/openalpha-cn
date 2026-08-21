"""`V2-P4-014`: the baseline D13 asks to be beaten, and the numbers that say whether it was.

The instrument is not the reference under `backtest/alpha_model.py` -- that model's whole learned
state is one bit and its own docstring says it is not a baseline. This file drives a fit that
reads **every** declared column, learns one coefficient per column from the training window, and
produces a score whose only claim is an order.

## The corpus, and the one number in it that was measured before anything was asserted

Eight securities, and the count is not tidiness. `factor_ic._pearson` sums products in the
argument's own order, so an unsorted population would make a fit a function of how the panel
happened to hand its rows over. Whether a corpus can *tell* depends on its size: over 400 random
cross sections a permutation changed the correlation 0/400 times at three names, 190/400 at six
and 347/400 at sixty. A three-name corpus -- which is what `tests/alpha_model_fixtures.py`
carries, correctly, for a contract test -- cannot separate a sorted fit from an unsorted one no
matter how the assertion is written, which is exactly the defect
`test_this_corpus_can_tell_a_sorted_fit_from_an_unsorted_one` exists to rule out here before
`test_a_permuted_training_set_produces_the_same_artifact_bit_for_bit` claims anything.

The targets rotate by three securities each day, so the three days' rank correlations are three
different numbers and a fit that returned any single day's answer instead of their mean would be
caught.

## What is measured on `V2-P4-013`'s corpus instead, and why

Everything about a **fold** -- the join to a walk-forward split, the leak, the two coefficients a
joint solve could not have produced. That corpus reads every label off one close series per
security, so its overlaps are a fact rather than a claim, and reusing it is what keeps this
issue's evaluation honest about a split it did not build. It has no noise model, and
`every_number_this_module_has_produced_was_measured_on_a_leak_fixture` says so.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pytest
from alpha_model_fixtures import training_example
from walk_forward_fixtures import (
    ALIGNED_FROM_ADJACENT,
    EMBARGO_SESSIONS,
    FIRST_TEST_DAY_INDEX,
    MOMENTUM_VALUES,
    TEST_DAYS_PER_FOLD,
    VALUE_VALUES,
    as_of_for,
    labels_for,
    panel,
    prediction_days,
    trading_calendar,
)
from walk_forward_fixtures import (
    FEATURE_IDS as FOLD_FEATURE_IDS,
)
from walk_forward_fixtures import (
    HORIZON as FOLD_HORIZON,
)
from walk_forward_fixtures import (
    SECURITIES as FOLD_SECURITIES,
)

from openalpha_cn.backtest.alpha_baseline import (
    ABSTAIN_INCOMPLETE_FEATURES,
    ABSTAIN_UNRANKABLE_CROSS_SECTION,
    BASELINE_FAMILY,
    KNOWN_BASELINE_LIMITATIONS,
    MINIMUM_FOLD_DAYS,
    MINIMUM_RANK_SECURITIES,
    BaselineScorePoint,
    CrossSectionalRankModel,
    FittedCrossSectionalRankModel,
    FoldEvaluation,
    _rank_ic,
    _rank_positions,
    _summarize,
    evaluate_fold,
    evaluate_walk_forward,
    rankable,
    score_point,
)
from openalpha_cn.backtest.alpha_model import (
    FEATURE_HYPERPARAMETER,
    REFERENCE_FAMILY,
    SingleFeatureAlphaModel,
)
from openalpha_cn.backtest.factor_ic import _pearson, average_ranks
from openalpha_cn.backtest.walk_forward import (
    LabelledCrossSection,
    PanelSection,
    WalkForwardFold,
    labelled_panel,
    walk_forward_folds,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelDeclaration,
    FeatureCrossSection,
    FeatureRow,
    FittedAlphaModel,
    TrainingExample,
    TrainingSet,
)

QUALITY = "quality_roe"
REVERSAL = "reversal_1d"
VALUE = "value_ep"
FEATURE_IDS = (QUALITY, REVERSAL, VALUE)
"""Three columns, strictly increasing: `quality_roe` < `reversal_1d` < `value_ep`."""

SECURITIES = tuple(f"{index:06d}.SZ" for index in range(1, 9))
FIRST_DAY = date(2026, 6, 1)
SECOND_DAY = date(2026, 6, 2)
THIRD_DAY = date(2026, 6, 3)
TRAINING_DAYS = (FIRST_DAY, SECOND_DAY, THIRD_DAY)

AS_OF = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)
"""After every fixture window's close, so `PredictionBatch`'s leakage floor is cleared."""

Row = tuple[str, tuple[float | None, ...], float]


def declaration(
    *, name: str = "rank_baseline", family: str = BASELINE_FAMILY, horizon: str = "1d"
) -> AlphaModelDeclaration:
    return AlphaModelDeclaration(
        name=name,
        family=family,
        horizon=horizon,
        feature_version="features/v1",
        seed=7,
        code_commit="0123456789abcdef",
    )


def rows_for(day_offset: int) -> list[Row]:
    """One day's eight rows: three distinct columns, and a target order that rotates daily.

    `quality_roe` rises with the security's position and `reversal_1d` falls with it, so the two
    are exactly rank-anticorrelated and their coefficients come out equal and opposite --
    which is also what makes them the pair a joint solve cannot separate. `value_ep` walks the
    positions in fives, so it is neither.

    **`quality_roe` is geometric rather than evenly spaced**, and that was a mutant's doing. A
    column laid out as `a + b * index` is an exact affine image of its own rank vector, so a
    correlation of the *levels* equals a correlation of the *ranks* to the last bit and a fit that
    skipped the ranking entirely passed every assertion here. Real factor distributions are skewed
    and this one now is, which is what lets the coefficient test below mean what its name says --
    a correlation of ranks and not of levels that happen to rank the same way.

    The target rotates by three securities per day. Without the rotation every day's correlation
    would be the same number and a fit that returned `points[0]` instead of `fmean(points)` would
    pass everything here.
    """
    rows: list[Row] = []
    for index, ts_code in enumerate(SECURITIES):
        quality = 0.03 * 1.7**index + 0.004 * day_offset
        reversal = 0.19 - 0.023 * index + 0.007 * day_offset
        value = 0.07 + 0.013 * ((index * 5) % len(SECURITIES))
        target = 0.004 * ((index + 3 * day_offset) % len(SECURITIES)) - 0.002 * day_offset
        rows.append((ts_code, (quality, reversal, value), target))
    return rows


def examples_for(day: date, rows: Sequence[Row]) -> list[TrainingExample]:
    return [
        training_example(ts_code=ts_code, prediction_day=day, features=features, target=target)
        for ts_code, features, target in rows
    ]


def training_set(*, days: Sequence[date] = TRAINING_DAYS) -> TrainingSet:
    examples: list[TrainingExample] = []
    for offset, day in enumerate(days):
        examples.extend(examples_for(day, rows_for(offset)))
    return TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples))


def cross_section(
    *,
    as_of: datetime = AS_OF,
    rows: Sequence[tuple[str, tuple[float | None, ...]]] | None = None,
) -> FeatureCrossSection:
    offered = (
        rows
        if rows is not None
        else [(ts_code, features) for ts_code, features, _target in rows_for(0)]
    )
    return FeatureCrossSection(
        as_of=as_of,
        feature_ids=FEATURE_IDS,
        rows=tuple(FeatureRow(ts_code=ts_code, values=values) for ts_code, values in offered),
    )


def fitted() -> FittedCrossSectionalRankModel:
    return CrossSectionalRankModel(declaration=declaration()).fit(training_set())


def day_rank_ics(rows: Sequence[Row]) -> tuple[float, ...]:
    """Each column's rank correlation against the targets, computed in the rows' own order.

    Written here rather than reached for inside the module so that a fit's coefficients are
    checked against `factor_ic`'s public rank rule instead of against themselves.
    """
    target_ranks = average_ranks([target for _code, _values, target in rows])
    return tuple(
        _pearson(average_ranks([values[column] for _code, values, _target in rows]), target_ranks)
        for column in range(len(FEATURE_IDS))
    )


# --------------------------------------------------------------------------------------
# The fit
# --------------------------------------------------------------------------------------


def test_the_fit_learns_one_coefficient_per_declared_feature() -> None:
    """The property the reference under `backtest/alpha_model.py` exists not to have.

    That model reads one column named in a hyperparameter and learns a centre and a sign. This
    one is handed a column list and learns a coefficient for every column in it, keyed by the
    column's own id -- so `parameters` and `feature_ids` are the same names in the same order.
    """
    assert tuple(key for key, _value in fitted().artifact.parameters) == FEATURE_IDS


def test_the_baseline_satisfies_both_alpha_model_protocols() -> None:
    """Structural typing, which is what lets `V2-P4-015` satisfy the same pair from elsewhere."""
    model = CrossSectionalRankModel(declaration=declaration())

    assert isinstance(model, AlphaModel)
    assert isinstance(model.fit(training_set()), FittedAlphaModel)


def test_the_baseline_declares_no_hyperparameter_and_still_reads_every_column() -> None:
    """The reference needs `feature_id` because it cannot infer which column it reads.

    This one reads all of them, so there is nothing left to be told -- and an empty
    `hyperparameters` tuple is what makes two folds of one declaration differ only in what
    `artifact_for` measured off the data.
    """
    model = CrossSectionalRankModel(declaration=declaration())

    assert model.declaration.hyperparameters == ()
    assert len(model.fit(training_set()).artifact.parameters) == len(FEATURE_IDS)


def test_a_declaration_of_another_family_is_refused() -> None:
    with pytest.raises(ValueError, match="single_feature_reference"):
        CrossSectionalRankModel(declaration=declaration(family="single_feature_reference"))


def test_a_coefficient_is_the_mean_of_the_training_days_rank_correlations() -> None:
    """Recomputed from `factor_ic`'s public rank rule, day by day, and averaged here.

    The three days' correlations are three different numbers because the corpus rotates its
    targets, so a fit that returned one day's answer instead of their mean produces a different
    float on every column.
    """
    per_day = [day_rank_ics(sorted(rows_for(offset))) for offset in range(len(TRAINING_DAYS))]
    expected = tuple(
        sum(day[column] for day in per_day) / len(per_day) for column in range(len(FEATURE_IDS))
    )

    learned = tuple(value for _key, value in fitted().artifact.parameters)

    assert learned == pytest.approx(expected, abs=1e-15)
    assert len({round(day[QUALITY_COLUMN], 12) for day in per_day}) > 1


QUALITY_COLUMN = 0


def test_two_exactly_anticorrelated_columns_learn_equal_and_opposite_coefficients() -> None:
    """`quality_roe` rises with the security and `reversal_1d` falls with it, on every day.

    So the two carry one piece of information twice, and a **marginal** fit counts it twice --
    `the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice`. This is the shape
    a joint solve exists to handle and the one it cannot handle here at all; see
    `test_a_joint_solve_is_singular_on_the_pair_the_marginal_fit_answers_about`.
    """
    learned = dict(fitted().artifact.parameters)

    assert learned[QUALITY] == pytest.approx(-learned[REVERSAL], abs=1e-15)
    assert learned[QUALITY] != 0.0


def test_a_joint_solve_is_singular_on_the_pair_the_marginal_fit_answers_about() -> None:
    """The measurement behind "expressible and declined", on `V2-P4-013`'s own corpus.

    Its two columns are exactly rank-anticorrelated, so the `2 x 2` Gram matrix a normal-equations
    solve would invert has determinant zero and there is no joint answer. The marginal fit has
    one, and both of its coefficients are finite -- which is the whole of why this baseline is
    marginal and why `V2-P4-015`'s tree model is the thing that adds joint structure.
    """
    section = panel(aligned_from=ALIGNED_FROM_ADJACENT).sections[0]
    population = rankable((row.ts_code, row.values) for row in section.cross_section.rows)
    left = average_ranks([values[0] for _code, values in population])
    right = average_ranks([values[1] for _code, values in population])
    centred = [[value - sum(column) / len(column) for value in column] for column in (left, right)]
    gram = [[sum(a * b for a, b in zip(x, y, strict=True)) for y in centred] for x in centred]
    determinant = gram[0][0] * gram[1][1] - gram[0][1] * gram[1][0]

    assert determinant == pytest.approx(0.0, abs=1e-9)
    assert gram[0][0] > 0.0

    learned = dict(fold_fit().artifact.parameters)
    assert set(learned) == set(FOLD_FEATURE_IDS)
    assert all(abs(value) <= 1.0 for value in learned.values())


def test_a_column_no_training_day_could_measure_is_refused_rather_than_zeroed() -> None:
    """A zero that was measured and a zero that was never measurable are one float, two facts.

    `MissingValuePolicy`'s rule, which `MINIMUM_USABLE_EXAMPLES` cites one module over: a decision
    that was never taken must not report as one that was. So the fit names the column and stops.
    """
    flat = [
        (ts_code, (values[0], values[1], 0.5), target) for ts_code, values, target in rows_for(0)
    ]
    only_flat = TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples_for(FIRST_DAY, flat)))

    with pytest.raises(ValueError, match=VALUE):
        CrossSectionalRankModel(declaration=declaration()).fit(only_flat)


def test_a_training_day_with_too_few_complete_rows_contributes_to_no_column() -> None:
    """And the days that do have enough still fit, so the refusal is per day rather than per set.

    Two of `MINIMUM_RANK_SECURITIES` rows on the first day, the whole cross section on the other
    two: the learned coefficients equal the mean of the second and third days alone.
    """
    thinned = [
        (
            ts_code,
            values if index < MINIMUM_RANK_SECURITIES - 1 else (None, values[1], values[2]),
            target,
        )
        for index, (ts_code, values, target) in enumerate(rows_for(0))
    ]
    examples = examples_for(FIRST_DAY, thinned)
    for offset, day in ((1, SECOND_DAY), (2, THIRD_DAY)):
        examples.extend(examples_for(day, rows_for(offset)))

    learned = tuple(
        value
        for _key, value in CrossSectionalRankModel(declaration=declaration())
        .fit(TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples)))
        .artifact.parameters
    )
    later = [day_rank_ics(sorted(rows_for(offset))) for offset in (1, 2)]

    assert learned == pytest.approx(
        tuple(sum(day[column] for day in later) / 2 for column in range(len(FEATURE_IDS))),
        abs=1e-15,
    )


def test_a_training_day_whose_targets_all_tie_contributes_to_no_column() -> None:
    """A day on which the market did nothing orders nothing, whatever the columns did.

    `factor_ic._degenerate_side` calls that `degenerate_returns` and this fit skips the day for
    the same reason: the correlation's denominator is zero on the outcome side.
    """
    flat_market = [(ts_code, values, 0.01) for ts_code, values, _target in rows_for(0)]
    examples = examples_for(FIRST_DAY, flat_market)
    for offset, day in ((1, SECOND_DAY), (2, THIRD_DAY)):
        examples.extend(examples_for(day, rows_for(offset)))

    learned = tuple(
        value
        for _key, value in CrossSectionalRankModel(declaration=declaration())
        .fit(TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples)))
        .artifact.parameters
    )
    later = [day_rank_ics(sorted(rows_for(offset))) for offset in (1, 2)]

    assert learned == pytest.approx(
        tuple(sum(day[column] for day in later) / 2 for column in range(len(FEATURE_IDS))),
        abs=1e-15,
    )


def test_a_row_missing_one_column_is_outside_every_columns_ranking() -> None:
    """The population rule, measured on the column the row still carries.

    A row short one value is not ranked on the others, because a rank is a position within a set
    and the set is the scored population. If the missing row were kept for the columns it did
    have, every other security's position on those columns would move.
    """
    complete = sorted(rows_for(0))
    holed = [
        (ts_code, (None, values[1], values[2]) if index == 0 else values, target)
        for index, (ts_code, values, target) in enumerate(complete)
    ]

    learned = tuple(
        value
        for _key, value in CrossSectionalRankModel(declaration=declaration())
        .fit(TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples_for(FIRST_DAY, holed))))
        .artifact.parameters
    )

    assert learned == pytest.approx(day_rank_ics(complete[1:]), abs=1e-15)
    assert learned != pytest.approx(day_rank_ics(complete), abs=1e-15)


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_this_corpus_can_tell_a_sorted_fit_from_an_unsorted_one() -> None:
    """The measurement without which the determinism assertion below would be vacuous.

    `_pearson` sums products in the argument's own order. Whether reordering a cross section
    changes the answer depends on its size, and a three-name corpus never notices -- so this
    asserts that *this* corpus does, on this exact permutation, before anything claims the sort
    is what prevents it. The difference is one bit and one bit is the whole point: a fit that
    silently moved in the last place would still break every content address `V2-P4-016` builds
    on it.
    """
    ordered = sorted(rows_for(0))

    assert day_rank_ics(list(reversed(ordered))) != day_rank_ics(ordered)


def test_a_permuted_training_set_produces_the_same_artifact_bit_for_bit() -> None:
    """Two `TrainingSet`s carrying one corpus in two orders are one fit.

    `TrainingSet` does not require its examples sorted, and `V2-P4-013` builds them in panel
    order, so a re-run that assembled the panel differently would otherwise re-address the model.
    The permutation is the reversal the test above proved this corpus can feel.
    """
    forward = training_set()
    backward = TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(reversed(forward.examples)))
    model = CrossSectionalRankModel(declaration=declaration())

    assert model.fit(backward).artifact == model.fit(forward).artifact


def test_two_fits_of_one_training_set_produce_equal_and_distinct_artifacts() -> None:
    """Equal by value and separate objects: `fit` returns a new model rather than mutating."""
    model = CrossSectionalRankModel(declaration=declaration())
    first = model.fit(training_set())
    second = model.fit(training_set())

    assert first.artifact == second.artifact
    assert first is not second


# --------------------------------------------------------------------------------------
# Predict
# --------------------------------------------------------------------------------------


def test_every_offered_security_is_scored_or_abstained() -> None:
    batch = fitted().predict(cross_section(), predicted_at=AS_OF)

    assert batch.subjects == tuple(sorted(SECURITIES))
    assert len(batch.scored) == len(SECURITIES)
    assert batch.abstained == ()


def test_a_security_missing_one_declared_column_abstains_with_the_stated_reason() -> None:
    """Rather than being scored on the columns it does carry, which is a different statistic."""
    offered = [(ts_code, values) for ts_code, values, _target in rows_for(0)]
    offered[2] = (offered[2][0], (offered[2][1][0], None, offered[2][1][2]))

    batch = fitted().predict(cross_section(rows=offered), predicted_at=AS_OF)

    assert [item.ts_code for item in batch.abstained] == [SECURITIES[2]]
    assert batch.abstained[0].abstention == ABSTAIN_INCOMPLETE_FEATURES


def test_a_cross_section_too_small_to_rank_abstains_on_every_security_including_the_complete() -> (
    None
):
    """A rank position among two names is `MINIMUM_IC_SECURITIES`' "magnitude one whatever the
    two securities did", so scoring the survivors would be a number with no information in it."""
    offered = [(ts_code, values) for ts_code, values, _target in rows_for(0)][
        : MINIMUM_RANK_SECURITIES - 1
    ]

    batch = fitted().predict(cross_section(rows=offered), predicted_at=AS_OF)

    assert len(batch.abstained) == MINIMUM_RANK_SECURITIES - 1
    assert {item.abstention for item in batch.abstained} == {ABSTAIN_UNRANKABLE_CROSS_SECTION}


def test_the_score_orders_the_cross_section_the_learned_coefficients_ask_it_to() -> None:
    """Recomputed here from the artifact's coefficients and `factor_ic`'s public rank rule."""
    model = fitted()
    offered = sorted((ts_code, values) for ts_code, values, _target in rows_for(0))
    positions = {
        column: average_ranks([values[column] for _code, values in offered])
        for column in range(len(FEATURE_IDS))
    }
    middle = (len(offered) + 1) / 2.0
    half = (len(offered) - 1) / 2.0
    expected = {
        ts_code: sum(
            coefficient * (positions[column][index] - middle) / half
            for column, (_key, coefficient) in enumerate(model.artifact.parameters)
        )
        for index, (ts_code, _values) in enumerate(offered)
    }

    batch = model.predict(cross_section(rows=offered), predicted_at=AS_OF)

    assert {item.ts_code: item.score for item in batch.scored} == pytest.approx(expected)


def test_a_block_tied_on_one_column_shares_one_position_there_and_is_separated_by_the_others() -> (
    None
):
    """`V2-P4-004` measured 56 of 5,540 names on one winsorized value. This is that shape.

    The three clipped names carry one identical `value_ep`, so `average_ranks` gives them one
    shared position on that column -- and their scores still differ, because the other two
    columns still order them. A baseline that refused the whole cross section, or that ordered
    the block by whatever the sort returned, would both be wrong in a way a reader could not see.
    """
    clipped = SECURITIES[-3:]
    offered = [
        (ts_code, (values[0], values[1], 0.163) if ts_code in clipped else values)
        for ts_code, values, _target in rows_for(0)
    ]

    batch = fitted().predict(cross_section(rows=offered), predicted_at=AS_OF)
    scores = {item.ts_code: item.score for item in batch.scored}

    assert len({scores[ts_code] for ts_code in clipped}) == len(clipped)
    positions = average_ranks([values[2] for _code, values in sorted(offered)])
    assert len(set(positions[-3:])) == 1


def test_a_block_tied_on_every_column_carries_one_identical_score() -> None:
    """And that is the honest answer rather than a failure: the market really did not separate
    them, so the model says so instead of ordering them by whatever the sort returned."""
    tied_values = (0.09, 0.09, 0.163)
    offered = [
        (ts_code, tied_values if index >= len(SECURITIES) - 3 else values)
        for index, (ts_code, values, _target) in enumerate(rows_for(0))
    ]

    batch = fitted().predict(cross_section(rows=offered), predicted_at=AS_OF)
    scores = {item.ts_code: item.score for item in batch.scored}

    assert len({scores[ts_code] for ts_code in SECURITIES[-3:]}) == 1


def test_a_securitys_score_moves_when_a_different_security_leaves_the_cross_section() -> None:
    """The limitation, measured rather than asserted -- and the first draft of this test could
    not measure it.

    A rank is a position within a set, so a stored batch is a statement about a cross section and
    never a per-security forecast --
    `a_score_is_a_position_in_this_cross_section_and_not_a_property_of_the_security`. But
    `_rank_positions` pins the **extremes** at exactly `-1` and `+1` for every population size, so
    the cheapest security to reach for -- the first, which is bottom on two columns and top on the
    third -- has the same score in a cross section of eight and of seven, and asserting on it
    passes whatever the model does. The interior is where the property lives, and both halves are
    asserted so the pin is stated rather than discovered again.
    """
    model = fitted()
    offered = [(ts_code, values) for ts_code, values, _target in rows_for(0)]

    whole = {
        item.ts_code: item.score
        for item in model.predict(cross_section(rows=offered), predicted_at=AS_OF).scored
    }
    fewer = {
        item.ts_code: item.score
        for item in model.predict(cross_section(rows=offered[:-1]), predicted_at=AS_OF).scored
    }

    assert whole[SECURITIES[3]] != fewer[SECURITIES[3]]
    assert whole[SECURITIES[0]] == fewer[SECURITIES[0]]


def test_a_fitted_model_rebuilt_from_its_artifact_alone_reproduces_every_prediction() -> None:
    """`V2-P4-016` addresses the artifact and `V2-P4-017` stores a batch beside it, and both are
    worth doing only if the artifact is the whole model."""
    original = fitted()
    rebuilt = FittedCrossSectionalRankModel(artifact=original.artifact)

    assert rebuilt.predict(cross_section(), predicted_at=AS_OF) == original.predict(
        cross_section(), predicted_at=AS_OF
    )


def test_an_artifact_whose_coefficient_keys_are_not_its_columns_is_refused() -> None:
    """A missing key is a column silently scored at nothing; an extra one was never in the fit."""
    artifact = fitted().artifact
    short = artifact.model_copy(update={"parameters": artifact.parameters[:-1]})

    with pytest.raises(ValueError, match=VALUE):
        FittedCrossSectionalRankModel(artifact=short)


def test_a_cross_section_carrying_a_different_column_list_is_refused_by_name() -> None:
    """Through `prediction_batch_for`, which is the one place `require_features` runs on the
    driven path -- `V2-P4-011` deleted the second copy after measuring it was worth nothing."""
    widened = FeatureCrossSection(
        as_of=AS_OF,
        feature_ids=(*FEATURE_IDS, "zeta_extra"),
        rows=tuple(
            FeatureRow(ts_code=ts_code, values=(*values, 1.0))
            for ts_code, values, _target in rows_for(0)
        ),
    )

    with pytest.raises(ValueError, match="zeta_extra"):
        fitted().predict(widened, predicted_at=AS_OF)


# --------------------------------------------------------------------------------------
# The numbers
# --------------------------------------------------------------------------------------


def _fold(*, embargo: int, aligned_from: int = ALIGNED_FROM_ADJACENT) -> WalkForwardFold:
    return WalkForwardFold(
        panel=panel(aligned_from=aligned_from),
        calendar=trading_calendar(),
        first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=embargo,
    )


def fold_declaration() -> AlphaModelDeclaration:
    return declaration(name="fold_baseline", horizon=FOLD_HORIZON)


def fold_model() -> CrossSectionalRankModel:
    return CrossSectionalRankModel(declaration=fold_declaration())


def fold_fit() -> FittedCrossSectionalRankModel:
    return fold_model().fit(_fold(embargo=EMBARGO_SESSIONS).training_set)


def test_a_test_day_reports_the_rank_correlation_of_the_rows_it_both_scored_and_labelled() -> None:
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))
    point = evaluation.points[0]

    assert point.coverage == "measured"
    assert point.offered_count == point.scored_count == point.paired_count
    assert point.rank_ic is not None


def test_the_fold_reads_the_honest_answer_the_corpus_says_it_should() -> None:
    """`V2-P4-013`'s corpus states its own ground truth, and this is the metric reproducing it.

    The plant is that over the sessions a fold may legitimately learn from, a higher
    `momentum_20d` realized a **lower** return, while over the test block it realized a higher
    one -- so the honest answer is always "this model has no skill and its learned direction is
    the opposite of the test period's". A fold that had absorbed the test period's direction
    would read `+1.0` here.
    """
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))

    assert evaluation.mean_rank_ic == -1.0
    assert dict(evaluation.artifact.parameters)["momentum_20d"] == -1.0


def test_the_embargo_moves_this_baselines_coefficient_and_not_its_ordering() -> None:
    """A finding, and a falsification of what this issue expected before it measured.

    `V2-P4-013` measured its reference model's one learned bit **flipping** on the adjacent
    corpus -- purge alone scored 1.0, purge plus a two-session embargo scored 0.0 -- and this
    issue expected the same separation to show up in a mean rank IC. It does not, and the reason
    is exactly why the two models are different instruments. That reference pools its examples and
    compares two means, so the fixture's twenty-to-one coefficient ratio lets two leaked labels
    outweigh four honest ones. A rank correlation is invariant to magnitude, so each leaked day
    contributes `+1` and each honest day `-1` and the fit averages them: two of six leaked comes
    out at `-1/3` against `-1`.

    The leak is therefore **visible and not invisible** -- it is a threefold collapse in the
    coefficient, which `FoldEvaluation.artifact` carries by value for exactly this kind of
    comparison. What it does not reach is the *ordering*, because this corpus has two columns that
    are exactly rank-anticorrelated, so both coefficients rescale together and the order they
    produce cannot move. That is a property of a four-name two-column fixture, and it is why
    `V2-P4-022` owns the corpus an evaluation would need before reporting a number at all.
    """
    honest = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))
    leaked = evaluate_fold(fold_model(), _fold(embargo=0))

    assert dict(honest.artifact.parameters)["momentum_20d"] == -1.0
    assert dict(leaked.artifact.parameters)["momentum_20d"] == pytest.approx(-1 / 3)
    assert honest.mean_rank_ic == leaked.mean_rank_ic == -1.0


def test_a_model_fitted_on_its_own_test_block_is_refused_before_it_can_report_a_number() -> None:
    """The total leak, and `V2-P4-011`'s floor is what answers it rather than this module.

    Fitting on `fold.test_set` learns the test block's own direction -- `momentum_20d` comes out
    at `+1.0` against the walk-forward fit's `-1.0`. The batch that would report it never exists:
    the artifact's training cutoff is after the instant the first test section is dated at, which
    is the same comparison `V2-P4-013`'s purge moved from a refusal to a removal. So a leak this
    total is not an inflated skill number here either.
    """
    fold = _fold(embargo=EMBARGO_SESSIONS)
    in_sample = fold_model().fit(fold.test_set)
    section = fold.test_sections[0]

    assert dict(in_sample.artifact.parameters)["momentum_20d"] == 1.0
    with pytest.raises(ValueError, match="realized after the instant"):
        in_sample.predict(section.cross_section, predicted_at=section.as_of)


def test_a_perfectly_ordered_day_reads_plus_one_and_a_reversed_one_reads_minus_one() -> None:
    """The metric's own arithmetic, without a fit or a corpus in the way."""
    targets = [0.01, 0.02, 0.03, 0.04]

    assert _point_over([1.0, 2.0, 3.0, 4.0], targets).rank_ic == 1.0
    assert _point_over([4.0, 3.0, 2.0, 1.0], targets).rank_ic == -1.0


def test_a_day_on_which_every_score_ties_reports_degenerate_scores_and_no_number() -> None:
    """Driven end to end: a cross section on which every column ties scores everybody the same.

    `_rank_positions` answers all zeros for a column that separates nobody, so the weighted sum
    is zero for every security -- the honest reading of a market the columns did not order, and
    the reason this is a coverage code rather than a correlation of zero.
    """
    section = _fold(embargo=EMBARGO_SESSIONS).test_sections[0]
    flat = FeatureCrossSection(
        as_of=section.as_of,
        feature_ids=FOLD_FEATURE_IDS,
        rows=tuple(
            FeatureRow(ts_code=row.ts_code, values=(0.2,) * len(FOLD_FEATURE_IDS))
            for row in section.cross_section.rows
        ),
    )

    batch = fold_fit().predict(flat, predicted_at=section.as_of)
    point = score_point(batch, section=section)

    assert len({item.score for item in batch.scored}) == 1
    assert point.coverage == "degenerate_scores"
    assert point.rank_ic is None
    assert point.scored_count == point.offered_count


def test_a_day_on_which_every_realized_return_ties_reports_degenerate_returns() -> None:
    scores = [0.4, 0.1, -0.2, -0.3]

    assert _point_over(scores, [0.02] * len(scores)).coverage == "degenerate_returns"


def test_a_day_with_fewer_pairs_than_a_rank_can_be_taken_over_reports_insufficient_sample() -> None:
    """One below the floor and exactly on it, so a check written `< 2` would still be caught."""
    scores = [0.4, 0.1]

    assert _point_over(scores, [0.03, 0.01]).coverage == "insufficient_sample"
    assert _point_over([*scores, -0.2], [0.03, 0.01, 0.05]).coverage == "measured"
    assert MINIMUM_RANK_SECURITIES == 3


def _point_over(scores: Sequence[float], targets: Sequence[float]) -> BaselineScorePoint:
    """One point built straight from paired numbers, without a fit in the way."""
    coverage, value = _rank_ic(list(scores), list(targets))
    return BaselineScorePoint(
        as_of=AS_OF,
        predicted_at=AS_OF,
        prediction_day=FIRST_DAY,
        offered_count=max(len(scores), 1),
        scored_count=len(scores),
        paired_count=len(scores),
        coverage=coverage,
        rank_ic=value,
    )


def test_a_day_on_which_both_sides_tie_names_the_model_before_the_market() -> None:
    """`factor_ic._degenerate_side`'s stated precedence, kept rather than re-decided.

    A cross section in which both the scores and the returns tie is a defect in the model *and*
    an unusable day, and naming the model first puts the report on the half somebody can act on.
    Only a fixture where both tie can tell the two orders apart.
    """
    assert _point_over([0.3] * 4, [0.02] * 4).coverage == "degenerate_scores"


def test_the_rank_correlation_does_not_order_a_tied_block_the_way_sort_position_would() -> None:
    """The reason `average_ranks` is the rule and a sort position is not.

    Three of eight scores tie. `average_ranks` gives them one shared position; a metric built on
    sort position hands them 1, 2 and 3 in whatever order the sort returned, and the two answers
    are different numbers on the same data. On the whole market that block is 56 names.
    """
    scores = [0.9, 0.7, 0.5, 0.3, 0.2, 0.2, 0.2, 0.1]
    targets = [0.05, 0.01, 0.04, -0.01, 0.03, -0.02, 0.02, 0.0]
    target_ranks = average_ranks(targets)

    averaged = _pearson(average_ranks(scores), target_ranks)
    by_sort_position = _pearson(
        [
            float(position + 1)
            for position, _index in sorted(
                enumerate(sorted(range(len(scores)), key=lambda index: scores[index])),
                key=lambda pair: pair[1],
            )
        ],
        target_ranks,
    )

    assert averaged != by_sort_position
    assert _point_over(scores, targets).rank_ic == pytest.approx(averaged)


def test_the_scored_ratio_counts_every_offered_security_including_the_abstained() -> None:
    """Abstention is otherwise free skill: declining the hard names buys a better mean."""
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))

    assert evaluation.scored_ratio == 1.0
    assert sum(point.offered_count for point in evaluation.points) == len(evaluation.points) * len(
        _fold(embargo=EMBARGO_SESSIONS).test_sections[0].cross_section.rows
    )


def test_a_fold_with_fewer_measured_days_than_a_dispersion_needs_reports_no_stability() -> None:
    """`MINIMUM_IC_AS_OFS`' own reason: a sample standard deviation of one number does not exist.

    Cut from the **holed** panel rather than the whole one, because `scored_ratio` is the one
    statistic this branch still has to report and a fold at full coverage cannot tell a measured
    ratio from a hard-coded `1.0` -- a mutant that returned one here lived through the first
    sweep.
    """
    fold = _holed_panel_fold()
    single = WalkForwardFold(
        panel=fold.panel,
        calendar=fold.calendar,
        first_test_day=fold.first_test_day,
        test_day_count=MINIMUM_FOLD_DAYS - 1,
        embargo_sessions=EMBARGO_SESSIONS,
    )

    evaluation = evaluate_fold(fold_model(), single)

    assert evaluation.coverage == "insufficient_as_ofs"
    assert evaluation.mean_rank_ic is None
    assert evaluation.stdev_rank_ic is None
    assert evaluation.rank_icir is None
    assert evaluation.scored_ratio == pytest.approx(
        (len(FOLD_SECURITIES) - 1) / len(FOLD_SECURITIES)
    )


def test_a_fold_whose_measured_days_all_agree_reports_a_mean_and_no_ratio() -> None:
    """`ICSummary.icir`'s decision taken rather than retaken: `None`, never `math.inf`."""
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))

    assert evaluation.coverage == "measured"
    assert evaluation.stdev_rank_ic == 0.0
    assert evaluation.rank_icir is None
    assert evaluation.measured_count == evaluation.test_day_count == TEST_DAYS_PER_FOLD


def test_the_fold_evaluation_carries_the_artifact_it_measured_by_value() -> None:
    """`PredictionBatch`'s decision, so this module has nothing to move when `V2-P4-016` lands."""
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))

    assert evaluation.artifact == fold_fit().artifact
    assert evaluation.first_test_day == prediction_days()[FIRST_TEST_DAY_INDEX]


def test_a_point_read_against_another_instants_outcomes_is_refused() -> None:
    """The one mistake here that would otherwise produce a plausible number."""
    fold = _fold(embargo=EMBARGO_SESSIONS)
    first, second = fold.test_sections[0], fold.test_sections[1]
    batch = fold_fit().predict(first.cross_section, predicted_at=first.as_of)

    with pytest.raises(ValueError, match="two instants correlate to a number that means nothing"):
        score_point(batch, section=second)
    assert first.as_of != second.as_of


def test_each_fold_of_a_schedule_is_fitted_separately_and_carries_its_own_artifact() -> None:
    """`AlphaModel.fit` returns a new object, which is what lets `V2-P4-016` address folds apart."""
    folds = walk_forward_folds(
        panel(aligned_from=ALIGNED_FROM_ADJACENT),
        calendar=trading_calendar(),
        folds=2,
        test_days_per_fold=TEST_DAYS_PER_FOLD,
        embargo_sessions=EMBARGO_SESSIONS,
    )

    evaluations = evaluate_walk_forward(fold_model(), folds)

    assert len(evaluations) == 2
    assert evaluations[0].artifact != evaluations[1].artifact
    assert evaluations[0].artifact.training_cutoff < evaluations[1].artifact.training_cutoff


def test_a_schedule_of_no_fold_is_refused() -> None:
    with pytest.raises(ValueError, match="empty success"):
        evaluate_walk_forward(fold_model(), ())


def test_an_evaluation_dates_every_batch_at_the_instant_the_section_it_reads_is_dated() -> None:
    """No clock, so an evaluation is reproducible -- and therefore no evidence for Story S32."""
    fold = _fold(embargo=EMBARGO_SESSIONS)
    evaluation = evaluate_fold(fold_model(), fold)

    assert tuple(point.as_of for point in evaluation.points) == tuple(
        section.as_of for section in fold.test_sections
    )


def test_a_fold_evaluation_refuses_a_measured_count_its_own_points_disagree_with() -> None:
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))

    with pytest.raises(ValueError, match="cannot disagree"):
        evaluation.model_copy(
            update={"measured_count": evaluation.measured_count - 1}
        ).model_validate(
            {
                **evaluation.model_dump(exclude_computed_fields=True),
                "measured_count": evaluation.measured_count - 1,
            }
        )


def test_a_score_point_refuses_counts_that_do_not_narrow() -> None:
    with pytest.raises(ValueError, match="answers about the cross section it was given"):
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF,
            prediction_day=FIRST_DAY,
            offered_count=3,
            scored_count=4,
            paired_count=3,
            coverage="measured",
            rank_ic=0.5,
        )
    with pytest.raises(ValueError, match="a pair needs both halves"):
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF,
            prediction_day=FIRST_DAY,
            offered_count=4,
            scored_count=3,
            paired_count=4,
            coverage="measured",
            rank_ic=0.5,
        )


def test_a_score_point_refuses_a_number_its_coverage_says_is_absent() -> None:
    with pytest.raises(ValueError, match="exactly the 'measured' code carries a number"):
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF,
            prediction_day=FIRST_DAY,
            offered_count=4,
            scored_count=4,
            paired_count=4,
            coverage="degenerate_scores",
            rank_ic=0.5,
        )


def test_a_fold_evaluation_refuses_an_unordered_or_repeated_block() -> None:
    evaluation = evaluate_fold(fold_model(), _fold(embargo=EMBARGO_SESSIONS))
    payload = evaluation.model_dump(exclude_computed_fields=True)
    payload["points"] = list(reversed(payload["points"]))

    with pytest.raises(ValueError, match="strictly increasing"):
        FoldEvaluation.model_validate(payload)


def test_the_positions_of_a_tied_cross_section_still_sum_to_zero() -> None:
    """The property that separates an average rank from a sort position, and nothing else did.

    A first draft asserted only that a tied block shares *one* position, which a metric built on
    `sorted(values).index(value)` satisfies too -- it gives every tied name the block's **lowest**
    rank. Average ranks keep the rank sum at `m (m + 1) / 2` whatever the ties are, so the
    positions centre exactly on zero; the lowest-rank rule pulls the whole vector negative, and
    the tied block's own position is `0.714` against `0.428` on this cross section.
    """
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.9, 0.9, 0.9]
    positions = _rank_positions(values)

    assert sum(positions) == pytest.approx(0.0, abs=1e-15)
    assert len(set(positions[-3:])) == 1
    assert positions[-1] == pytest.approx(0.7142857142857143)


def test_a_fitted_baseline_rebuilt_from_another_familys_artifact_is_refused() -> None:
    """The rebuild path `V2-P4-016` and `V2-P4-017` will drive is where this can actually happen.

    The unfitted model refuses a foreign declaration, so nothing reaches `fit` wrongly. An
    artifact arrives from somewhere else entirely, and one whose parameter keys happen to be its
    feature ids would otherwise be rebuilt as a rank baseline and asked to score.
    """
    foreign = SingleFeatureAlphaModel(
        declaration=AlphaModelDeclaration(
            name="reference",
            family=REFERENCE_FAMILY,
            horizon=FOLD_HORIZON,
            feature_version="features/v1",
            seed=7,
            code_commit="0123456789abcdef",
            hyperparameters=((FEATURE_HYPERPARAMETER, FOLD_FEATURE_IDS[0]),),
        )
    ).fit(_fold(embargo=EMBARGO_SESSIONS).training_set)

    with pytest.raises(ValueError, match=REFERENCE_FAMILY):
        FittedCrossSectionalRankModel(artifact=foreign.artifact)


def test_a_cross_section_missing_a_fitted_column_is_refused_as_a_value_error() -> None:
    """A narrower list, which is the direction `prediction_batch_for` never gets to see.

    The columns are located by `.index` on the offered list, `FittedSingleFeatureAlphaModel`'s
    own idiom, so a fitted column the cross section does not carry raises out of there -- a
    `ValueError`, which is the family every refusal on this plane belongs to. Reading them
    positionally instead would index past the end of a row and raise `IndexError`, which a caller
    writing `except ValueError` around a contract boundary does not catch.
    """
    narrowed = FeatureCrossSection(
        as_of=AS_OF,
        feature_ids=FEATURE_IDS[:2],
        rows=tuple(
            FeatureRow(ts_code=ts_code, values=values[:2])
            for ts_code, values, _target in rows_for(0)
        ),
    )

    with pytest.raises(ValueError):
        fitted().predict(narrowed, predicted_at=AS_OF)


def _holed_panel_fold(*, embargo: int = EMBARGO_SESSIONS) -> WalkForwardFold:
    """`V2-P4-013`'s corpus with the first security's `momentum_20d` deleted on every day.

    The fixture never produces a `None`, and without one nothing in an evaluation ever abstains --
    which left `scored_ratio` pinned at `1.0` in every test and three mutants alive underneath it.
    Three of the four securities still carry every column, which is exactly
    `MINIMUM_RANK_SECURITIES`, so the fit and the scoring both still run.
    """
    sections = tuple(
        LabelledCrossSection(
            cross_section=FeatureCrossSection(
                as_of=as_of_for(day),
                feature_ids=FOLD_FEATURE_IDS,
                rows=tuple(
                    FeatureRow(
                        ts_code=ts_code,
                        values=(None, value) if index == 0 else (momentum, value),
                    )
                    for index, (ts_code, momentum, value) in enumerate(
                        zip(FOLD_SECURITIES, MOMENTUM_VALUES, VALUE_VALUES, strict=True)
                    )
                ),
            ),
            labels=labels_for(day, aligned_from=ALIGNED_FROM_ADJACENT),
        )
        for day in prediction_days()
    )
    return WalkForwardFold(
        panel=labelled_panel(sections),
        calendar=trading_calendar(),
        first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=embargo,
    )


def test_a_fold_the_model_abstains_inside_reports_a_scored_ratio_below_one() -> None:
    """The metric that makes two models' headlines comparable, driven where it is not `1.0`.

    One of four securities carries no `momentum_20d`, so it abstains on every test day. Its row
    is still in `offered_count` -- that is the whole point, because a model that declined the
    names it found hard would otherwise report a better `mean_rank_ic` over an easier population
    with nothing beside it to say so.
    """
    evaluation = evaluate_fold(fold_model(), _holed_panel_fold())
    point = evaluation.points[0]

    assert point.offered_count == len(FOLD_SECURITIES)
    assert point.scored_count == point.paired_count == len(FOLD_SECURITIES) - 1
    assert evaluation.scored_ratio == pytest.approx(
        (len(FOLD_SECURITIES) - 1) / len(FOLD_SECURITIES)
    )
    assert evaluation.mean_rank_ic is not None


def test_a_scored_security_the_panel_could_not_label_is_not_a_pair() -> None:
    """`paired_count` is what separates a model that declined a name from a market that had no
    outcome for it, and only a section short one example can tell the two counts apart."""
    fold = _fold(embargo=EMBARGO_SESSIONS)
    section = fold.test_sections[0]
    batch = fold_fit().predict(section.cross_section, predicted_at=section.as_of)

    trimmed = PanelSection(
        as_of=section.as_of,
        prediction_day=section.prediction_day,
        cross_section=section.cross_section,
        examples=section.examples[1:],
    )
    point = score_point(batch, section=trimmed)

    assert point.scored_count == len(section.examples)
    assert point.paired_count == len(section.examples) - 1


def test_the_fold_dispersion_is_the_sample_standard_deviation() -> None:
    """`ICSummary.stdev_ic`'s own wording -- the **sample** deviation, `n - 1` -- taken rather
    than retaken. Every fold in this file reads the same number on every day, so the two
    divisors agree there and only points built by hand can separate them."""
    points = tuple(
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF,
            prediction_day=day,
            offered_count=4,
            scored_count=4,
            paired_count=4,
            coverage="measured",
            rank_ic=value,
        )
        for day, value in zip(TRAINING_DAYS, (0.2, 0.5, -0.1), strict=True)
    )

    summary = _summarize(fitted().artifact, first_test_day=FIRST_DAY, points=points)

    assert summary.mean_rank_ic == pytest.approx(0.2)
    assert summary.stdev_rank_ic == pytest.approx(0.3)
    assert summary.stdev_rank_ic != pytest.approx(statistics.pstdev([0.2, 0.5, -0.1]))
    assert summary.rank_icir == pytest.approx(0.2 / 0.3)


def test_every_point_carries_the_instant_the_batch_it_read_was_produced_at() -> None:
    """Without this field `evaluate_fold`'s choice of `predicted_at` is unobservable, and a
    mutation sweep measured exactly that: dating every batch at the panel's last instant left the
    whole suite green. It is legal -- `PredictionBatch` only refuses `predicted_at < as_of` -- and
    it is not what an evaluation means, so the choice is carried where it can be contradicted."""
    fold = _fold(embargo=EMBARGO_SESSIONS)
    evaluation = evaluate_fold(fold_model(), fold)

    assert tuple(point.predicted_at for point in evaluation.points) == tuple(
        section.as_of for section in fold.test_sections
    )
    assert all(point.predicted_at == point.as_of for point in evaluation.points)


def test_a_point_carries_the_batchs_own_timestamp_and_not_a_second_copy_of_its_as_of() -> None:
    """Every batch an *evaluation* builds is dated at its own `as_of`, so that path cannot tell
    the two fields apart. A batch produced later -- which is the ordinary production shape
    `V2-P4-017` will store -- can, and a reading of one must not claim it stood earlier than it
    did."""
    fold = _fold(embargo=EMBARGO_SESSIONS)
    section = fold.test_sections[0]
    later = section.as_of + timedelta(hours=6)

    point = score_point(
        fold_fit().predict(section.cross_section, predicted_at=later), section=section
    )

    assert point.as_of == section.as_of
    assert point.predicted_at == later


def test_a_score_point_refuses_a_correlation_outside_the_range_a_correlation_lives_in() -> None:
    """`_pearson` clamps, so this is a read-back guard rather than a driven one -- and
    `V2-P4-017` reading a stored point is where a `1.5` would otherwise arrive."""
    with pytest.raises(ValueError, match=r"outside \[-1, 1\]"):
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF,
            prediction_day=FIRST_DAY,
            offered_count=4,
            scored_count=4,
            paired_count=4,
            coverage="measured",
            rank_ic=1.5,
        )


def test_a_score_point_refuses_a_reading_produced_before_the_cross_section_it_reads() -> None:
    with pytest.raises(ValueError, match="before the"):
        BaselineScorePoint(
            as_of=AS_OF,
            predicted_at=AS_OF - timedelta(minutes=1),
            prediction_day=FIRST_DAY,
            offered_count=4,
            scored_count=4,
            paired_count=4,
            coverage="measured",
            rank_ic=0.5,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"points": []}, "carries no test day"),
        ({"coverage": "insufficient_as_ofs"}, "exactly\nthe 'measured' code|exactly the"),
        ({"mean_rank_ic": None}, "exactly the"),
        ({"stdev_rank_ic": None}, "exactly the"),
        ({"mean_rank_ic": float("inf")}, "not a finite statistic"),
        ({"rank_icir": float("nan")}, "not a finite statistic"),
    ],
)
def test_a_stored_fold_evaluation_is_refused_when_its_summary_contradicts_itself(
    mutation: dict[str, object], message: str
) -> None:
    """Every one of these is unreachable from `evaluate_fold` and reachable from a stored
    payload, which is the boundary a pydantic model on this plane exists for -- `ICSummary`
    carries the same four refusals for the same reason."""
    evaluation = evaluate_fold(fold_model(), _holed_panel_fold())
    payload = {**evaluation.model_dump(exclude_computed_fields=True), **mutation}

    with pytest.raises(ValueError, match=message):
        FoldEvaluation.model_validate(payload)


def test_a_stored_fold_evaluation_is_refused_when_it_carries_a_ratio_without_a_mean() -> None:
    fold = _fold(embargo=EMBARGO_SESSIONS)
    single = WalkForwardFold(
        panel=fold.panel,
        calendar=fold.calendar,
        first_test_day=fold.first_test_day,
        test_day_count=MINIMUM_FOLD_DAYS - 1,
        embargo_sessions=EMBARGO_SESSIONS,
    )
    payload = {
        **evaluate_fold(fold_model(), single).model_dump(exclude_computed_fields=True),
        "rank_icir": 0.4,
    }

    with pytest.raises(ValueError, match="cannot carry a rank_icir"):
        FoldEvaluation.model_validate(payload)


def test_the_known_limitation_registry_carries_the_codes_this_module_argues() -> None:
    assert {item.code for item in KNOWN_BASELINE_LIMITATIONS} == {
        "a_score_is_a_position_in_this_cross_section_and_not_a_property_of_the_security",
        "the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice",
        "a_rank_baseline_forecasts_no_return_and_its_score_carries_no_units",
        "a_tie_this_baseline_can_see_is_honest_and_the_neutralised_tier_hides_the_one_that_matters",
        "the_two_abstention_reasons_are_sentences_and_not_the_vocabulary_story_35_asks_for",
        "an_evaluation_is_dated_at_the_instant_it_simulates_and_proves_nothing_about_when",
        "nothing_here_checks_that_the_declared_feature_version_is_the_matrix_it_was_fitted_on",
        "decision_13s_threshold_is_computed_by_nothing_here_and_gated_by_nothing_anywhere",
        "a_minority_leak_moves_this_baselines_coefficient_and_not_the_order_it_produces",
        "every_number_this_module_has_produced_was_measured_on_a_leak_fixture",
    }
