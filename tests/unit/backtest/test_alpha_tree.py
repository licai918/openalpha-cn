"""`V2-P4-015`: the tree baseline D13 pairs with the rank one, and what it adds.

The instrument is `V2-P4-014`'s own `evaluate_fold`, unchanged and un-imported by the model:
`AlphaModel` is a `Protocol`, so both models are measured by exactly one function and the
comparison below is one reading rather than two numbers from two harnesses.

## The corpus, and why it is three corpora rather than one

`V2-P4-014` measured everything on `V2-P4-013`'s leak fixture, whose two columns are exactly
rank-anticorrelated and whose target is a monotone function of one of them. That corpus cannot
answer this issue's question, because a tree beats a marginal rank-linear model exactly where the
target is *not* monotone in one column. So three shapes are built here, each deciding something
the others cannot, and the first is the one that could have falsified the whole issue:

- `monotone` -- the target rises with one column. The rank baseline's assumption holds exactly
  and the tree is measured to be **worse**. That is the honest half and it is asserted.
- `interaction` -- the target is the product of two columns, so each column's marginal rank IC
  is zero by construction and a weighted sum of ranks has nothing to weight.
- `redundant` -- two near-duplicate columns and one real one, which is the shape
  `KNOWN_BASELINE_LIMITATIONS`'
  `the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice` names.

A fourth, `twinned`, is not a comparison at all and is here because a mutation sweep asked for
it: two columns that are **exactly** equal are the only shape on which `_grow`'s tie-break is
observable, and without it a `>=` where the module writes `>` changed no number in this file.

None of the four has a noise model. `V2-P4-022` owns the corpus with a known signal-to-noise
ratio and a known-null control, and
`every_number_this_module_produced_was_measured_on_a_noiseless_synthetic_corpus` says so where a
reader meets it.

There is **no random number anywhere in this module**. Every column is a deterministic
permutation of one spread over `[-1, 1]`, skewed by `_skew` and rotated by the prediction day, so
a comparison here is a property of the shape rather than of a draw -- which is the objection
ADR-0003's own conditioning section raised against a seeded probe and answered by naming the
seed. Naming it is unnecessary when there is none.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from alpha_model_fixtures import outcome_label, training_example

from openalpha_cn.backtest.alpha_baseline import (
    ABSTAIN_INCOMPLETE_FEATURES,
    ABSTAIN_UNRANKABLE_CROSS_SECTION,
    BASELINE_FAMILY,
    CrossSectionalRankModel,
    FoldEvaluation,
    evaluate_fold,
)
from openalpha_cn.backtest.alpha_tree import (
    BASELINE_HYPERPARAMETERS,
    KNOWN_TREE_LIMITATIONS,
    LEAF_FEATURE,
    SPLIT_BIN_COUNT,
    TREE_FAMILY,
    BoostedRankTreeModel,
    FittedBoostedRankTreeModel,
    split_bin,
)
from openalpha_cn.backtest.walk_forward import (
    LabelledCrossSection,
    WalkForwardFold,
    labelled_panel,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    FeatureRow,
    FittedAlphaModel,
    TrainingExample,
    TrainingSet,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGE = "SZSE"

ALPHA = "alpha_one"
BETA = "beta_two"
GAMMA = "gamma_three"
FEATURE_IDS = (ALPHA, BETA, GAMMA)
"""Three columns, strictly increasing: `alpha_one` < `beta_two` < `gamma_three`."""

SECURITY_COUNT = 48
SECURITIES = tuple(f"{index:06d}.SZ" for index in range(1, SECURITY_COUNT + 1))
"""Forty-eight, which is a measurement and not a round number.

`SPLIT_BIN_COUNT` is 32, so a cross section has to be wider than that before two securities share
a bin -- which is what `test_two_securities_in_one_bin_cannot_be_separated_by_this_model` needs to
be able to see -- and wide enough that a root split at `min_leaf_securities` leaves an interior on
both sides.
"""

PANEL_DAYS = tuple(date(2026, 6, day) for day in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18))
"""Fourteen SZSE sessions in June 2026, which is what `alpha_model_fixtures`' calendar holds."""

FIRST_TEST_DAY = PANEL_DAYS[10]
TEST_DAY_COUNT = 4
AS_OF = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)
"""After every fixture window's close, so `PredictionBatch`'s leakage floor is cleared."""

FAST_HYPERPARAMETERS: tuple[tuple[str, bool | int | float | str], ...] = (
    ("learning_rate", 0.2),
    ("max_depth", 3),
    ("min_leaf_securities", 12),
    ("tree_count", 24),
)
"""Fewer, larger-stepping trees than the shipped floor, so this file runs in seconds.

Spelled apart from `BASELINE_HYPERPARAMETERS` on purpose: that constant is what D13's comparison
is taken at, and a test that quietly redefined it would be measuring something else.
`test_the_shipped_floor_reaches_the_same_verdict_as_this_files_faster_setting` drives the real
one on the corpus the verdict rests on.
"""

Row = tuple[str, tuple[float | None, ...], float]


def _spread(index: int, count: int) -> float:
    """`index` mapped onto `[-1, 1]` -- the shape a rank position already has."""
    return 2.0 * index / (count - 1) - 1.0


def _skew(position: float) -> float:
    """A strictly increasing, strictly non-affine image of a `[-1, 1]` position.

    **A mutant's doing**, and it is `V2-P4-014`'s own trap arriving one module later. A column
    laid out as `a + b * index` is an exact affine image of its own rank vector, so binning the
    *levels* and binning the *rank positions* land every security in the same bin -- and a mutant
    that skipped the ranking entirely in `_pooled` and again in `predict` passed the whole file.
    Real factor distributions are skewed; this one now is, so the two are different calculations
    and a test can tell them apart.
    """
    return math.copysign(abs(position) ** 2.3, position)


def rows_for(shape: str, day_offset: int) -> list[Row]:
    """One prediction day's rows for one of the three corpora, rotated by the day.

    The three columns carry `_skew`ed values and the *targets* do not, because the ranking under
    test is the columns'. Ranks are unchanged by the skew -- it is strictly increasing -- so
    every claim below about what each corpus's target is remains exactly true.
    """
    rows: list[Row] = []
    count = len(SECURITIES)
    for index, ts_code in enumerate(SECURITIES):
        first = _spread((index + day_offset) % count, count)
        second = _spread((index * 7 + 3 * day_offset) % count, count)
        third = _spread((index * 11 + 5 * day_offset) % count, count)
        if shape == "monotone":
            columns = (_skew(first), _skew(second), _skew(third))
            target = 0.01 * first
        elif shape == "interaction":
            columns = (_skew(first), _skew(second), _skew(third))
            target = 0.01 * first * second
        elif shape == "redundant":
            columns = (_skew(first), _skew(first) + 0.001 * second, _skew(third))
            target = 0.01 * (0.35 * first + third)
        elif shape == "twinned":
            # Two columns that are *exactly* equal, which is the only shape on which the split
            # search's tie-break is observable at all.
            columns = (_skew(first), _skew(first), _skew(third))
            target = 0.01 * first
        else:  # pragma: no cover - a typo in a test is not a branch
            raise ValueError(shape)
        rows.append((ts_code, columns, target))
    return rows


def training_set(shape: str, *, days: Sequence[date] = PANEL_DAYS[:10]) -> TrainingSet:
    examples: list[TrainingExample] = []
    for offset, day in enumerate(days):
        examples.extend(
            training_example(ts_code=ts_code, prediction_day=day, features=columns, target=target)
            for ts_code, columns, target in rows_for(shape, offset)
        )
    return TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples))


def cross_section(
    shape: str,
    *,
    day_offset: int = 0,
    as_of: datetime = AS_OF,
    rows: Sequence[tuple[str, tuple[float | None, ...]]] | None = None,
) -> FeatureCrossSection:
    offered = (
        rows
        if rows is not None
        else [(ts_code, columns) for ts_code, columns, _target in rows_for(shape, day_offset)]
    )
    return FeatureCrossSection(
        as_of=as_of,
        feature_ids=FEATURE_IDS,
        rows=tuple(FeatureRow(ts_code=ts_code, values=values) for ts_code, values in offered),
    )


def declaration(
    *,
    name: str = "tree_baseline",
    family: str = TREE_FAMILY,
    horizon: str = "1d",
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...] = FAST_HYPERPARAMETERS,
) -> AlphaModelDeclaration:
    return AlphaModelDeclaration(
        name=name,
        family=family,
        horizon=horizon,
        feature_version="features/v1",
        seed=7,
        code_commit="0123456789abcdef",
        hyperparameters=hyperparameters,
    )


def rank_declaration(*, horizon: str = "1d") -> AlphaModelDeclaration:
    return AlphaModelDeclaration(
        name="rank_baseline",
        family=BASELINE_FAMILY,
        horizon=horizon,
        feature_version="features/v1",
        seed=7,
        code_commit="0123456789abcdef",
    )


def fitted(shape: str = "interaction") -> FittedBoostedRankTreeModel:
    return BoostedRankTreeModel(declaration=declaration()).fit(training_set(shape))


def fold(shape: str) -> WalkForwardFold:
    """A real `WalkForwardFold` over the whole panel: a real purge, a real embargo, real labels.

    Built through `labelled_panel` and `WalkForwardFold` rather than by hand, so the comparison
    below is taken across a split `V2-P4-013` cut rather than one this file invented. The
    fixtures' calendar is the same SZSE June 2026 one every label in this module is built on.
    """
    from alpha_model_fixtures import trading_calendar

    sections = tuple(
        LabelledCrossSection(
            cross_section=cross_section(
                shape,
                day_offset=offset,
                as_of=datetime.combine(day, time(9, 0), tzinfo=SHANGHAI),
            ),
            labels=tuple(
                outcome_label(ts_code=ts_code, prediction_day=day, target=target)
                for ts_code, _columns, target in rows_for(shape, offset)
            ),
        )
        for offset, day in enumerate(PANEL_DAYS)
    )
    return WalkForwardFold(
        panel=labelled_panel(sections),
        calendar=trading_calendar(),
        first_test_day=FIRST_TEST_DAY,
        test_day_count=TEST_DAY_COUNT,
        embargo_sessions=1,
    )


def verdicts(
    shape: str,
    *,
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...] = FAST_HYPERPARAMETERS,
) -> tuple[FoldEvaluation, FoldEvaluation]:
    """One fold, both models, one `evaluate_fold` -- the tree's evaluation first."""
    cut = fold(shape)
    tree = BoostedRankTreeModel(declaration=declaration(hyperparameters=hyperparameters))
    return (
        evaluate_fold(tree, cut, shelf_life=None),
        evaluate_fold(
            CrossSectionalRankModel(declaration=rank_declaration()), cut, shelf_life=None
        ),
    )


def headline(evaluation: FoldEvaluation) -> float:
    assert evaluation.mean_rank_ic is not None
    return evaluation.mean_rank_ic


# --------------------------------------------------------------------------------------
# The fit
# --------------------------------------------------------------------------------------


def test_the_tree_satisfies_both_alpha_model_protocols() -> None:
    model = BoostedRankTreeModel(declaration=declaration())

    assert isinstance(model, AlphaModel)
    assert isinstance(model.fit(training_set("interaction")), FittedAlphaModel)
    assert type(model).__mro__[1:] == (object,)


def test_a_declaration_of_another_family_is_refused() -> None:
    with pytest.raises(AlphaModelError, match=TREE_FAMILY):
        BoostedRankTreeModel(declaration=declaration(family=BASELINE_FAMILY))


def test_the_ensemble_is_stored_in_the_artifact_and_nowhere_else() -> None:
    model = fitted()

    assert tuple(type(model).__slots__) == ("artifact",)
    assert model.artifact.parameters != ()


def test_a_fitted_tree_rebuilt_from_its_artifact_alone_reproduces_every_prediction() -> None:
    model = fitted()
    rebuilt = FittedBoostedRankTreeModel(artifact=model.artifact)

    assert rebuilt.predict(
        cross_section("interaction"), predicted_at=AS_OF, shelf_life=None
    ) == model.predict(cross_section("interaction"), predicted_at=AS_OF, shelf_life=None)


def test_every_encoded_node_is_a_leaf_or_a_split_on_a_declared_column() -> None:
    parameters = dict(fitted().artifact.parameters)
    features = {key: value for key, value in parameters.items() if key.endswith(".feature")}

    assert features
    for key, value in features.items():
        node = key.removesuffix(".feature")
        assert value == LEAF_FEATURE or value in range(len(FEATURE_IDS)), key
        if value == LEAF_FEATURE:
            assert f"{node}.leaf" in parameters
            assert f"{node}.edge" not in parameters
        else:
            assert f"{node}.edge" in parameters
            assert f"{node}.leaf" not in parameters


def test_the_encoded_table_is_exactly_two_entries_per_node() -> None:
    parameters = fitted().artifact.parameters
    nodes = {key.rpartition(".")[0] for key, _value in parameters}

    assert len(parameters) == 2 * len(nodes)


def test_a_permuted_training_set_produces_the_same_artifact_bit_for_bit() -> None:
    """Structurally, and this test's own first draft claimed the wrong reason for it.

    The claim was that a split's plain-`+` accumulation makes the pooled row order load-bearing,
    the way `factor_ic._pearson` makes it load-bearing for the rank baseline. Measured, that is
    false: with `_pooled`'s day sort removed the pooled order changes on 200 of 200 random
    permutations and the artifact changes on 0 of 200, because `_grow`'s node total is
    `math.fsum` and a leaf value is therefore exactly rounded.

    What `sorted(by_day)` does buy is what is asserted here: the pool is a function of the data
    and not of the caller's example order, so this holds by construction rather than because two
    floating-point accumulations happened to agree.
    """
    original = training_set("interaction")
    shuffled = TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(reversed(original.examples)))
    model = BoostedRankTreeModel(declaration=declaration())

    assert model.fit(shuffled).artifact == model.fit(original).artifact


def test_the_pool_is_already_in_day_and_security_order() -> None:
    """Why a third sort was deleted rather than kept, held as a measurement.

    `_pooled`'s first draft appended `rows.sort(key=lambda row: (day, ts_code))` to the assembled
    pool. `sorted(by_day)` already orders the days and `rankable` already orders the rows inside
    one, so that sort moved nothing; this reproduces the pool's construction and requires the
    keys to come out ascending on their own.
    """
    from openalpha_cn.backtest.alpha_baseline import rankable

    training = training_set("interaction")
    by_day: dict[date, list[TrainingExample]] = {}
    for example in training.examples:
        by_day.setdefault(example.label.window.prediction_day, []).append(example)

    keys = [
        (day, ts_code)
        for day in sorted(by_day)
        for ts_code, _values in rankable(
            (example.ts_code, example.features) for example in by_day[day]
        )
    ]

    assert keys == sorted(keys)
    assert len(keys) == len(training.examples)


def test_a_leaf_value_does_not_move_with_the_order_its_residuals_reach_it_in() -> None:
    """`math.fsum` rather than `sum`, asserted at `_grow`'s own boundary.

    This is a unit test of `_grow` and the residuals below are **adversarial rather than
    reachable**: `fit` chases rank positions in `[-1, 1]`, so it never hands a node a residual of
    `1e300`. That is stated rather than hidden, because the reachable half was measured first and
    could not tell the two functions apart -- no permutation of up to 110,680 realistic residuals
    separated `sum` from `math.fsum`, on any of several shapes tried. What separates them is
    catastrophic cancellation: `1e300` and `-1e300` annihilate each other exactly, and every tiny
    residual added *before* that cancellation is annihilated with them, while `math.fsum` keeps
    all of them whatever order they arrive in.

    The fixture used to be `[1e16, 1.0, -1e16, 1.0]`, and `V2-P5-062` is why it is not any more:
    Python 3.12 gave `sum()` Neumaier compensation for floats, which makes that list
    order-independent -- `2.0` either way, the same answer `fsum` gives. The guard at the end
    said so in its own words on the first 3.12 run this repository ever did. Neumaier is
    compensated but not exactly rounded, so a list it cannot recover still exists; this one is
    order-dependent on 3.11 and on 3.12 both, measured, and `math.fsum` is stable on both.

    So the property under test is `_grow`'s -- *a leaf value does not depend on the order of
    `members`* -- and `fsum` is what makes it a property rather than a habit.
    """
    from openalpha_cn.backtest.alpha_tree import _grow, _Leaf, _Settings

    residuals = [5e-08, 1e300, 5e-16, -1e300, 1e-16]
    binned = [(0, 0, 0) for _ in residuals]
    settings = _Settings(learning_rate=0.2, max_depth=0, min_leaf_securities=2, tree_count=1)

    order = list(range(len(residuals)))
    ascending = _grow(binned, residuals, order, depth=0, settings=settings, width=3)
    descending = _grow(
        binned, residuals, list(reversed(order)), depth=0, settings=settings, width=3
    )

    assert isinstance(ascending, _Leaf)
    assert isinstance(descending, _Leaf)
    assert ascending.value == descending.value
    assert sum(residuals) != sum(reversed(residuals)), (
        "this fixture cannot tell fsum from sum, so the assertion above proves nothing"
    )


def test_two_fits_of_one_training_set_produce_equal_and_distinct_artifacts() -> None:
    model = BoostedRankTreeModel(declaration=declaration())
    first = model.fit(training_set("interaction"))
    second = model.fit(training_set("interaction"))

    assert first is not second
    assert first.artifact == second.artifact


def test_a_fit_that_cannot_take_one_split_is_refused_rather_than_scoring_everything_alike() -> None:
    """A leaf-only ensemble scores every security identically, which is a defect and not a model.

    Refused at the fit rather than reported at the evaluation: `BaselineScorePoint` would call it
    `degenerate_scores` and hand back a fold with no number, which is a true statement about a
    model that should never have been built.
    """
    model = BoostedRankTreeModel(
        declaration=declaration(
            hyperparameters=(
                ("learning_rate", 0.2),
                ("max_depth", 3),
                ("min_leaf_securities", SECURITY_COUNT * len(PANEL_DAYS)),
                ("tree_count", 4),
            )
        )
    )

    with pytest.raises(AlphaModelError, match="one split"):
        model.fit(training_set("interaction"))


def test_a_training_set_no_day_of_which_can_be_ranked_is_refused() -> None:
    model = BoostedRankTreeModel(declaration=declaration())
    tiny = TrainingSet(
        feature_ids=FEATURE_IDS,
        examples=tuple(
            training_example(
                ts_code=ts_code, prediction_day=PANEL_DAYS[0], features=columns, target=target
            )
            for ts_code, columns, target in rows_for("interaction", 0)[:2]
        ),
    )

    with pytest.raises(AlphaModelError, match="nothing to take a position inside"):
        model.fit(tiny)


def test_a_training_day_with_too_few_complete_rows_contributes_no_row_to_the_pool() -> None:
    holed = [
        training_example(
            ts_code=ts_code,
            prediction_day=PANEL_DAYS[0],
            features=(columns[0], None, columns[2]),
            target=target,
        )
        for ts_code, columns, target in rows_for("interaction", 0)
    ]
    intact = training_set("interaction", days=PANEL_DAYS[1:10])
    model = BoostedRankTreeModel(declaration=declaration())

    with_hole = model.fit(TrainingSet(feature_ids=FEATURE_IDS, examples=(*holed, *intact.examples)))

    assert with_hole.artifact.parameters == model.fit(intact).artifact.parameters
    assert with_hole.artifact.training_example_count > len(intact.examples)


def test_a_training_day_whose_targets_all_tie_contributes_no_row_to_the_pool() -> None:
    flat = [
        training_example(
            ts_code=ts_code, prediction_day=PANEL_DAYS[0], features=columns, target=0.01
        )
        for ts_code, columns, _target in rows_for("interaction", 0)
    ]
    intact = training_set("interaction", days=PANEL_DAYS[1:10])
    model = BoostedRankTreeModel(declaration=declaration())

    with_flat = model.fit(TrainingSet(feature_ids=FEATURE_IDS, examples=(*flat, *intact.examples)))

    assert with_flat.artifact.parameters == model.fit(intact).artifact.parameters


def test_a_column_that_ties_on_a_day_needs_no_special_case_and_costs_that_day_nothing() -> None:
    """The one condition `CrossSectionalRankModel.fit` has that this model does not need.

    That fit skips a tied column on a day because averaging a correlation that does not exist
    would be a measurement nobody took. A histogram finds nothing there on its own: a tied column
    is all zeros, lands in one bin, and no edge over it can leave `min_leaf_securities` on both
    sides. So the fit still succeeds and the tied day still contributes its *other* columns.
    """
    tied = [
        training_example(
            ts_code=ts_code,
            prediction_day=PANEL_DAYS[0],
            features=(columns[0], 0.5, columns[2]),
            target=target,
        )
        for ts_code, columns, target in rows_for("interaction", 0)
    ]
    intact = training_set("interaction", days=PANEL_DAYS[1:10])
    model = BoostedRankTreeModel(declaration=declaration())

    with_tie = model.fit(TrainingSet(feature_ids=FEATURE_IDS, examples=(*tied, *intact.examples)))

    assert with_tie.artifact.parameters != model.fit(intact).artifact.parameters


# --------------------------------------------------------------------------------------
# The hyperparameters, which are declared rather than fixed
# --------------------------------------------------------------------------------------


def test_the_four_hyperparameters_are_flat_scalars_and_need_no_widening() -> None:
    """`V2-P4-011` left this call to this issue by name; it is answered by not exercising it.

    `AlphaModelDeclaration.hyperparameters` refuses a nested structure and its docstring says
    `V2-P4-015` is the first issue that could need otherwise. It does not: a depth, a count, a
    rate and a leaf floor are four flat scalars that round-trip through the contract unchanged,
    so that field is not widened here.
    """
    declared = declaration(hyperparameters=BASELINE_HYPERPARAMETERS).hyperparameters

    assert declared == BASELINE_HYPERPARAMETERS
    assert [key for key, _value in declared] == sorted(key for key, _value in declared)
    assert all(isinstance(value, int | float) for _key, value in declared)


def test_the_declared_hyperparameters_travel_into_the_artifact_by_value() -> None:
    artifact = fitted().artifact

    assert artifact.declaration.hyperparameters == FAST_HYPERPARAMETERS


@pytest.mark.parametrize(
    ("hyperparameters", "expected"),
    [
        pytest.param((), "learning_rate", id="none-declared"),
        pytest.param(
            (("learning_rate", 0.2), ("max_depth", 3), ("tree_count", 4)),
            "min_leaf_securities",
            id="one-missing",
        ),
        pytest.param(
            (
                ("learning_rate", 0.2),
                ("max_depth", 3),
                ("min_leaf_securities", 12),
                ("subsample", 0.5),
                ("tree_count", 4),
            ),
            "subsample",
            id="one-extra",
        ),
    ],
)
def test_a_declaration_naming_the_wrong_hyperparameters_is_refused_by_name(
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...], expected: str
) -> None:
    with pytest.raises(AlphaModelError, match=expected):
        BoostedRankTreeModel(declaration=declaration(hyperparameters=hyperparameters))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("learning_rate", 0.0, id="rate-zero"),
        pytest.param("learning_rate", 1.5, id="rate-above-one"),
        pytest.param("max_depth", 0, id="depth-zero"),
        pytest.param("max_depth", 99, id="depth-above-cap"),
        pytest.param("min_leaf_securities", 1, id="leaf-of-one"),
        pytest.param("tree_count", 0, id="no-trees"),
        pytest.param("tree_count", 10_000, id="trees-above-cap"),
    ],
)
def test_a_hyperparameter_outside_its_range_is_refused(key: str, value: float) -> None:
    hyperparameters = tuple(
        (name, value if name == key else default) for name, default in FAST_HYPERPARAMETERS
    )

    with pytest.raises(AlphaModelError, match=key):
        BoostedRankTreeModel(declaration=declaration(hyperparameters=hyperparameters))


@pytest.mark.parametrize("key", ["max_depth", "min_leaf_securities", "tree_count"])
def test_a_count_declared_as_a_float_is_refused_rather_than_truncated(key: str) -> None:
    hyperparameters = tuple(
        (name, 2.5 if name == key else default) for name, default in FAST_HYPERPARAMETERS
    )

    with pytest.raises(AlphaModelError, match=key):
        BoostedRankTreeModel(declaration=declaration(hyperparameters=hyperparameters))


@pytest.mark.parametrize("key", ["learning_rate", "max_depth"])
def test_a_hyperparameter_declared_as_a_bool_is_refused(key: str) -> None:
    """`bool` is an `int` in Python, so `max_depth=True` would otherwise grow a depth-one tree."""
    hyperparameters = tuple(
        (name, True if name == key else default) for name, default in FAST_HYPERPARAMETERS
    )

    with pytest.raises(AlphaModelError, match=key):
        BoostedRankTreeModel(declaration=declaration(hyperparameters=hyperparameters))


def _depth_of(node: object) -> int:
    from openalpha_cn.backtest.alpha_tree import _Split

    if not isinstance(node, _Split):
        return 0
    return 1 + max(_depth_of(node.left), _depth_of(node.right))


@pytest.mark.parametrize("declared", [1, 2, 3])
def test_no_tree_grows_past_the_depth_its_declaration_names(declared: int) -> None:
    """The declared depth, pinned to the tree rather than to a parameter count.

    `test_a_deeper_ensemble_carries_more_parameters_than_a_shallower_one` compares two settings
    and a mutation sweep measured what that misses: an off-by-one in `_grow`'s depth gate
    (`depth > max_depth` for `depth >= max_depth`) shifts *both* sides and the comparison holds.
    This reads the depth back out of the decoded ensemble.
    """
    from openalpha_cn.backtest.alpha_tree import _decode

    model = BoostedRankTreeModel(
        declaration=declaration(
            hyperparameters=(
                ("learning_rate", 0.2),
                ("max_depth", declared),
                ("min_leaf_securities", 12),
                ("tree_count", 6),
            )
        )
    ).fit(training_set("interaction"))

    depths = [_depth_of(tree) for tree in _decode(model.artifact)]

    assert max(depths) == declared, "a tree that never reaches the declared depth proves nothing"
    assert all(depth <= declared for depth in depths)


def test_a_deeper_ensemble_carries_more_parameters_than_a_shallower_one() -> None:
    def at_depth(depth: int) -> int:
        model = BoostedRankTreeModel(
            declaration=declaration(
                hyperparameters=(
                    ("learning_rate", 0.2),
                    ("max_depth", depth),
                    ("min_leaf_securities", 12),
                    ("tree_count", 6),
                )
            )
        )
        return len(model.fit(training_set("interaction")).artifact.parameters)

    assert at_depth(3) > at_depth(1)


def test_a_longer_ensemble_carries_more_parameters_than_a_shorter_one() -> None:
    def at_count(count: int) -> int:
        model = BoostedRankTreeModel(
            declaration=declaration(
                hyperparameters=(
                    ("learning_rate", 0.2),
                    ("max_depth", 3),
                    ("min_leaf_securities", 12),
                    ("tree_count", count),
                )
            )
        )
        return len(model.fit(training_set("interaction")).artifact.parameters)

    assert at_count(8) > at_count(4)


def test_the_learning_rate_scales_every_score_and_nothing_else() -> None:
    """A property of squared-error boosting worth pinning: the first tree is rate-independent.

    The first tree is fitted on the raw targets, so halving the rate halves the first tree's
    contribution and nothing about its shape. With one tree that makes the whole score exactly
    proportional -- which is what says the rate is a step size rather than a second model.
    """

    def scores(rate: float) -> list[float]:
        model = BoostedRankTreeModel(
            declaration=declaration(
                hyperparameters=(
                    ("learning_rate", rate),
                    ("max_depth", 3),
                    ("min_leaf_securities", 12),
                    ("tree_count", 1),
                )
            )
        ).fit(training_set("interaction"))
        batch = model.predict(cross_section("interaction"), predicted_at=AS_OF, shelf_life=None)
        return [item.score for item in batch.predictions if item.score is not None]

    halved = scores(0.1)
    full = scores(0.2)

    assert halved == pytest.approx([score / 2.0 for score in full])


# --------------------------------------------------------------------------------------
# The prediction
# --------------------------------------------------------------------------------------


def test_every_offered_security_is_scored_or_abstained() -> None:
    batch = fitted().predict(cross_section("interaction"), predicted_at=AS_OF, shelf_life=None)

    assert batch.subjects == tuple(sorted(SECURITIES))
    assert len(batch.scored) + len(batch.abstained) == len(SECURITIES)


def test_a_security_missing_one_declared_column_abstains_with_the_stated_reason() -> None:
    offered = [(ts_code, columns) for ts_code, columns, _t in rows_for("interaction", 0)]
    holed = [(offered[0][0], (None, *offered[0][1][1:])), *offered[1:]]

    batch = fitted().predict(
        cross_section("interaction", rows=holed), predicted_at=AS_OF, shelf_life=None
    )

    refused = next(item for item in batch.predictions if item.ts_code == offered[0][0])
    assert refused.abstention == ABSTAIN_INCOMPLETE_FEATURES
    assert refused.score is None


def test_a_cross_section_too_small_to_rank_abstains_on_every_security() -> None:
    offered = [(ts_code, columns) for ts_code, columns, _t in rows_for("interaction", 0)][:2]

    batch = fitted().predict(
        cross_section("interaction", rows=offered), predicted_at=AS_OF, shelf_life=None
    )

    assert {item.abstention for item in batch.predictions} == {ABSTAIN_UNRANKABLE_CROSS_SECTION}


def test_the_two_abstention_sentences_are_the_rank_baselines_and_not_a_second_spelling() -> None:
    """`V2-P4-018` maps a code to a condition, and two spellings of one condition break that."""
    import openalpha_cn.backtest.alpha_baseline as baseline
    import openalpha_cn.backtest.alpha_tree as tree

    assert tree.ABSTAIN_INCOMPLETE_FEATURES is baseline.ABSTAIN_INCOMPLETE_FEATURES
    assert tree.ABSTAIN_UNRANKABLE_CROSS_SECTION is baseline.ABSTAIN_UNRANKABLE_CROSS_SECTION


def test_a_score_is_finite_and_can_step_outside_the_range_of_the_targets_it_chased() -> None:
    """This test's first draft asserted the opposite, and the measurement said otherwise.

    `FittedCrossSectionalRankModel.predict` can say its score is bounded by the number of columns
    because both factors of every term are bounded by one. The obvious analogue here -- that a
    fit chasing rank positions in `[-1, 1]` produces scores in `[-1, 1]` -- was written first and
    is **false**: a leaf value is a mean of *residuals*, a residual is a target minus the running
    fit, and each boosting step can carry the running fit a little past the target it is chasing.
    The measured maximum on this corpus at `FAST_HYPERPARAMETERS` is **1.0038**.

    So what is asserted is what is true: every score is finite because it is a sum of finitely
    many means of finitely many finite numbers, and the range is a measurement rather than a
    bound. `a_score_carries_no_units_and_the_ensembles_leaf_values_are_not_returns` is the entry.
    """
    scores = [
        item.score
        for item in fitted()
        .predict(cross_section("interaction"), predicted_at=AS_OF, shelf_life=None)
        .predictions
        if item.score is not None
    ]

    assert scores
    assert all(math.isfinite(score) for score in scores)
    assert max(abs(score) for score in scores) > 1.0
    assert max(abs(score) for score in scores) < 1.1


def test_the_score_orders_the_cross_section_the_way_the_fitted_ensemble_asks_it_to() -> None:
    """An interaction the rank baseline cannot express, read as an ordering on one held-out day.

    Not a comparison against the other model -- that is further down, through `evaluate_fold`.
    This is the narrower claim that the scores this model produces are about the target at all.
    """
    from openalpha_cn.backtest.factor_ic import _pearson, average_ranks

    day_offset = len(PANEL_DAYS) + 3
    rows = rows_for("interaction", day_offset)
    batch = fitted().predict(
        cross_section("interaction", day_offset=day_offset), predicted_at=AS_OF, shelf_life=None
    )
    targets = {ts_code: target for ts_code, _columns, target in rows}
    pairs = [
        (item.score, targets[item.ts_code]) for item in batch.predictions if item.score is not None
    ]

    assert (
        _pearson(
            average_ranks([score for score, _target in pairs]),
            average_ranks([target for _score, target in pairs]),
        )
        > 0.75
    )


def test_a_cross_section_missing_a_fitted_column_is_refused_as_a_value_error() -> None:
    """A narrower list, which is the direction `prediction_batch_for` never gets to see.

    `FittedCrossSectionalRankModel`'s own finding, inherited rather than re-argued: the columns
    are located by `.index` on the offered list, so a fitted column the cross section does not
    carry raises out of there -- a `ValueError`, which is the family every refusal on this plane
    belongs to and the one `AlphaModelError` is a subclass of.
    """
    narrowed = FeatureCrossSection(
        as_of=AS_OF,
        feature_ids=(ALPHA, BETA),
        rows=tuple(
            FeatureRow(ts_code=ts_code, values=columns[:2])
            for ts_code, columns, _target in rows_for("interaction", 0)
        ),
    )

    with pytest.raises(ValueError):
        fitted().predict(narrowed, predicted_at=AS_OF, shelf_life=None)


def test_a_cross_section_carrying_an_extra_column_is_refused_before_a_batch_exists() -> None:
    """The other direction, which `require_features` inside `prediction_batch_for` does catch."""
    widened = FeatureCrossSection(
        as_of=AS_OF,
        feature_ids=(*FEATURE_IDS, "zeta_four"),
        rows=tuple(
            FeatureRow(ts_code=ts_code, values=(*columns, 0.5))
            for ts_code, columns, _target in rows_for("interaction", 0)
        ),
    )

    with pytest.raises(AlphaModelError, match="positionally"):
        fitted().predict(widened, predicted_at=AS_OF, shelf_life=None)


# --------------------------------------------------------------------------------------
# The artifact is the model
# --------------------------------------------------------------------------------------


def test_an_artifact_from_another_family_is_refused_when_a_fitted_tree_is_rebuilt() -> None:
    artifact = fitted().artifact
    foreign = artifact.model_copy(
        update={"declaration": artifact.declaration.model_copy(update={"family": BASELINE_FAMILY})}
    )

    with pytest.raises(AlphaModelError, match=TREE_FAMILY):
        FittedBoostedRankTreeModel(artifact=foreign)


def test_an_artifact_whose_last_node_is_missing_is_refused_rather_than_walked() -> None:
    artifact = fitted().artifact
    truncated = artifact.model_copy(update={"parameters": artifact.parameters[:-2]})

    with pytest.raises(AlphaModelError):
        FittedBoostedRankTreeModel(artifact=truncated)


def test_an_artifact_missing_one_half_of_one_node_is_refused_by_the_name_it_lacks() -> None:
    artifact = fitted().artifact
    key = next(key for key, _value in artifact.parameters if key.endswith(".leaf"))
    holed = tuple((name, value) for name, value in artifact.parameters if name != key)

    with pytest.raises(AlphaModelError, match="two entries"):
        FittedBoostedRankTreeModel(artifact=artifact.model_copy(update={"parameters": holed}))


def test_an_artifact_carrying_no_ensemble_at_all_is_refused() -> None:
    artifact = fitted().artifact

    with pytest.raises(AlphaModelError, match="no ensemble"):
        FittedBoostedRankTreeModel(artifact=artifact.model_copy(update={"parameters": ()}))


def test_a_split_on_a_column_the_artifact_does_not_declare_is_refused() -> None:
    artifact = fitted().artifact
    key = next(
        key
        for key, value in artifact.parameters
        if key.endswith(".feature") and value != LEAF_FEATURE
    )
    broken = tuple(
        (name, float(len(FEATURE_IDS)) if name == key else value)
        for name, value in artifact.parameters
    )

    with pytest.raises(AlphaModelError, match="column"):
        FittedBoostedRankTreeModel(artifact=artifact.model_copy(update={"parameters": broken}))


def test_an_artifact_carrying_a_surplus_node_is_refused_as_unreachable() -> None:
    artifact = fitted().artifact
    tree = next(key for key, _value in artifact.parameters).partition(".")[0]
    surplus = (
        *artifact.parameters,
        (f"{tree}.n900.feature", LEAF_FEATURE),
        (f"{tree}.n900.leaf", 0.5),
    )

    with pytest.raises(AlphaModelError, match="unreachable"):
        FittedBoostedRankTreeModel(
            artifact=artifact.model_copy(update={"parameters": tuple(sorted(surplus))})
        )


def test_a_parameter_that_is_not_a_node_at_all_is_refused() -> None:
    artifact = fitted().artifact
    nonsense = tuple(sorted((*artifact.parameters, ("aaa.bbb.feature", LEAF_FEATURE))))

    with pytest.raises(AlphaModelError, match="not a node"):
        FittedBoostedRankTreeModel(artifact=artifact.model_copy(update={"parameters": nonsense}))


# --------------------------------------------------------------------------------------
# The histogram, and what its resolution costs
# --------------------------------------------------------------------------------------


def test_the_split_is_the_one_that_minimises_total_squared_error() -> None:
    """The gain formula, checked against the quantity it is shorthand for.

    `_grow` maximises `L**2/n_L + R**2/n_R` because the parent's own term is the same for every
    candidate. A mutation sweep measured that dropping both denominators -- a different and wrong
    criterion -- changed no number any other test in this file reads. This computes the total
    squared error of every admissible edge directly, from the definition, and requires `_grow` to
    have returned the smallest.

    The corpus is deliberately lopsided: the residuals are large on one short side and small
    across a long one, so the counted and uncounted criteria disagree, which
    `test_this_fixture_separates_the_counted_criterion_from_the_uncounted_one` asserts before
    this test claims anything.
    """
    from openalpha_cn.backtest.alpha_tree import _grow, _Settings, _Split

    binned, residuals = _lopsided()
    settings = _Settings(learning_rate=0.2, max_depth=1, min_leaf_securities=3, tree_count=1)

    members = list(range(len(residuals)))
    node = _grow(binned, residuals, members, depth=0, settings=settings, width=1)

    assert isinstance(node, _Split)
    assert node.edge == min(_admissible(binned, residuals, floor=3), key=lambda item: item[1])[0]


def test_this_fixture_separates_the_counted_criterion_from_the_uncounted_one() -> None:
    """The sensitivity check the test above needs before it is allowed to mean anything."""
    binned, residuals = _lopsided()
    admissible = _admissible(binned, residuals, floor=3)
    uncounted = {
        edge: sum(residuals[i] for i in range(len(residuals)) if binned[i][0] <= edge) ** 2
        + sum(residuals[i] for i in range(len(residuals)) if binned[i][0] > edge) ** 2
        for edge, _error in admissible
    }

    best_counted = min(admissible, key=lambda item: item[1])[0]
    best_uncounted = max(uncounted, key=lambda edge: uncounted[edge])

    assert best_counted != best_uncounted


def _lopsided() -> tuple[list[tuple[int, ...]], list[float]]:
    """A one-column pool whose residuals are large on a short side and small on a long one."""
    binned = [(index,) for index in range(SPLIT_BIN_COUNT) for _ in range(2)]
    residuals = [
        (6.0 if bins[0] < 4 else 0.05 * (bins[0] - 4)) * (1.0 if position % 2 else -0.9)
        for position, bins in enumerate(binned)
    ]
    return binned, residuals


def _admissible(
    binned: list[tuple[int, ...]], residuals: list[float], *, floor: int
) -> list[tuple[int, float]]:
    """Every edge leaving `floor` rows on both sides, with its total squared error."""
    out: list[tuple[int, float]] = []
    for edge in range(SPLIT_BIN_COUNT - 1):
        left = [residuals[i] for i in range(len(residuals)) if binned[i][0] <= edge]
        right = [residuals[i] for i in range(len(residuals)) if binned[i][0] > edge]
        if len(left) < floor or len(right) < floor:
            continue
        error = sum((value - statistics.fmean(left)) ** 2 for value in left) + sum(
            (value - statistics.fmean(right)) ** 2 for value in right
        )
        out.append((edge, error))
    return out


def test_two_exactly_equal_columns_are_split_on_the_lower_index_every_time() -> None:
    """The tie-break, on the one shape that can observe it.

    `_grow` compares with a strict `>`, so on an exact tie the first candidate in iteration order
    wins -- lowest column index, then lowest bin edge. That is the determinism this module claims
    in place of a seed, and nothing in the ordinary corpora can see it because no two candidates
    there tie to the last bit. The `twinned` corpus makes columns zero and one *identical*.
    """
    from openalpha_cn.backtest.alpha_tree import _decode, _Split

    model = BoostedRankTreeModel(declaration=declaration()).fit(training_set("twinned"))

    used: set[int] = set()
    stack: list[object] = list(_decode(model.artifact))
    while stack:
        node = stack.pop()
        if isinstance(node, _Split):
            used.add(node.feature)
            stack.extend((node.left, node.right))

    assert 0 in used, "the twinned pair was never split on, so the tie-break is not observed"
    assert 1 not in used


def test_every_position_falls_in_a_bin_and_neither_closed_end_lands_off_the_grid() -> None:
    assert split_bin(-1.0) == 0
    assert split_bin(1.0) == SPLIT_BIN_COUNT - 1
    assert split_bin(0.0) == SPLIT_BIN_COUNT // 2
    assert sorted({split_bin(_spread(index, 512)) for index in range(512)}) == list(
        range(SPLIT_BIN_COUNT)
    )


def test_two_securities_in_one_bin_cannot_be_separated_by_this_model() -> None:
    """The resolution the histogram costs, asserted rather than left in a docstring.

    `SPLIT_BIN_COUNT` bins over `[-1, 1]` means a cross section wider than that many securities
    has neighbours no split can tell apart, on every column at once. That is what
    `a_split_sees_a_bin_and_not_a_rank_so_neighbours_inside_one_bin_share_every_split` costs.
    """
    width = SPLIT_BIN_COUNT * 4
    bins = [split_bin(_spread(index, width)) for index in range(width)]

    assert len(set(bins)) == SPLIT_BIN_COUNT
    assert bins[0] == bins[1]


def test_a_cross_section_narrower_than_the_grid_still_gives_every_name_its_own_bin() -> None:
    bins = [split_bin(_spread(index, SPLIT_BIN_COUNT)) for index in range(SPLIT_BIN_COUNT)]

    assert len(set(bins)) == SPLIT_BIN_COUNT


# --------------------------------------------------------------------------------------
# The comparison D13 asks for, through V2-P4-014's own harness
# --------------------------------------------------------------------------------------


def test_the_tree_reads_a_signal_the_marginal_rank_baseline_cannot_see() -> None:
    tree, rank = verdicts("interaction")

    assert headline(rank) == pytest.approx(0.0, abs=0.1)
    assert headline(tree) > 0.75


def test_the_rank_baseline_beats_the_tree_where_its_own_assumption_holds() -> None:
    """The honest half, and the reason D13 asks for two baselines rather than one.

    On a target that is a monotone function of one column the rank baseline's model *is* the
    truth, and a boosted step function approximating a straight line is worse. Neither model
    dominates, so a challenger has to clear both.
    """
    tree, rank = verdicts("monotone")

    assert headline(rank) > 0.99
    assert headline(tree) < headline(rank)


def test_the_tree_closes_the_double_counting_the_rank_baselines_registry_names() -> None:
    """`the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice`, closing.

    Two near-duplicate columns each earn nearly the same marginal coefficient, so the pair
    carries twice the weight one of them should, against a third column that is the larger part
    of the target. A split picks one column at a node and the duplicate's information is
    conditioned away by the partition rather than counted a second time.
    """
    tree, rank = verdicts("redundant")

    assert headline(tree) > headline(rank)


def test_the_shipped_floor_reaches_the_same_verdict_as_this_files_faster_setting() -> None:
    """`BASELINE_HYPERPARAMETERS` is what D13's comparison is taken at, so it is driven once."""
    tree, rank = verdicts("interaction", hyperparameters=BASELINE_HYPERPARAMETERS)

    assert headline(rank) == pytest.approx(0.0, abs=0.1)
    assert headline(tree) > 0.75


def test_both_models_are_measured_by_one_function_neither_of_them_imports() -> None:
    import openalpha_cn.backtest.alpha_baseline as baseline
    import openalpha_cn.backtest.alpha_tree as tree

    assert evaluate_fold.__module__ == baseline.__name__
    assert not hasattr(baseline, "BoostedRankTreeModel")
    assert isinstance(BoostedRankTreeModel(declaration=declaration()), AlphaModel)
    assert tree.__name__ not in {
        getattr(value, "__module__", "") for value in vars(baseline).values()
    }


def test_the_fold_this_comparison_is_taken_on_is_a_real_purged_split() -> None:
    """The split is `V2-P4-013`'s, not this file's: a purge and an embargo both actually cut."""
    cut = fold("interaction")

    assert cut.test_days == PANEL_DAYS[10 : 10 + TEST_DAY_COUNT]
    assert cut.purged != ()
    assert cut.embargoed != ()
    assert len(cut.train_examples) < len(cut.candidates)


def test_the_evaluation_carries_the_tree_artifact_it_measured_by_value() -> None:
    tree, _rank = verdicts("interaction")

    assert tree.artifact.declaration.family == TREE_FAMILY
    assert tree.artifact.parameters != ()
    assert tree.scored_ratio == 1.0


# --------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------


def test_the_registry_names_every_limitation_this_module_has() -> None:
    assert {entry.code for entry in KNOWN_TREE_LIMITATIONS} == {
        "this_is_a_histogram_boosting_of_the_kind_lightgbm_does_and_not_lightgbm",
        "a_split_sees_a_bin_and_not_a_rank_so_neighbours_inside_one_bin_share_every_split",
        "neither_baseline_dominates_the_other_and_the_monotone_corpus_is_where_this_one_loses",
        "a_column_no_split_used_is_absent_rather_than_reported_as_unused",
        "every_number_this_module_produced_was_measured_on_a_noiseless_synthetic_corpus",
        "the_hyperparameters_are_declared_and_nothing_here_selects_them",
        "a_score_carries_no_units_and_the_ensembles_leaf_values_are_not_returns",
    }
