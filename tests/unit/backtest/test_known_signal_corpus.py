"""`V2-P4-022`: what a corpus with a known signal-to-noise ratio can and cannot separate.

`tests/known_signal_corpus.py` is the corpus; this file is the part that makes it a **benchmark**
rather than a fixture. Three claims are driven, and the third is the one this issue exists for:

1. The corpus realizes the returns it was designed to realize, and its closed-form IC is a
   prediction rather than a reading (`known_rank_ic` is arithmetic on `ALPHA_BETA` and touches no
   panel).
2. The known-alpha arm measures near that prediction and the **known-null control measures near
   zero**. A system that reported skill on pure noise is the failure this issue exists to catch,
   so the null arm is asserted as hard as the alpha arm.
3. The corpus can tell a **fitted** model from an **unfitted** one -- and a one-column version of
   the same draws cannot, which is the product acceptance's own measurement reproduced here and
   the reason the corpus carries two columns.

Every number below is an exact literal off `SEED`, so the tolerances are reserved for the one
comparison that needs one: measurement against closed form.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

import known_signal_corpus as corpus
import pytest

from openalpha_cn.backtest.alpha_baseline import (
    CrossSectionalRankModel,
    FittedCrossSectionalRankModel,
    evaluate_walk_forward,
    score_point,
)
from openalpha_cn.backtest.walk_forward import LabelledPanel, WalkForwardFold, walk_forward_folds
from openalpha_cn.domain.alpha_model import AlphaModelArtifact

FIRST_TEST_DAY_INDEX = 20
TEST_DAY_COUNT = 8
"""One fold wide enough to average eight daily rank correlations, leaving twenty to fit on."""

REALISTIC_IC = 0.03
"""The order of magnitude a real cross-sectional equity signal reaches.

Not a measurement of any market -- it is the number the "cannot" claim is stated against, and it
is deliberately generous to the corpus: a plant this small would be at the optimistic end of what
published cross-sectional research reports, and this corpus still could not certify it.
"""


def _model() -> CrossSectionalRankModel:
    return CrossSectionalRankModel(declaration=corpus.declaration())


def _fold(panel: LabelledPanel) -> WalkForwardFold:
    return WalkForwardFold(
        panel=panel,
        calendar=corpus.trading_calendar(),
        first_test_day=corpus.prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAY_COUNT,
        embargo_sessions=0,
    )


def _read(fitted: FittedCrossSectionalRankModel, fold: WalkForwardFold) -> float:
    """The mean daily rank IC of one fitted model over one fold's test block.

    Through `score_point`, which is `V2-P4-014`'s own instrument, rather than through a second
    correlation written here: a benchmark that measured itself with its own arithmetic would be
    grading the corpus and not the system.
    """
    points = [
        score_point(
            fitted.predict(section.cross_section, predicted_at=section.as_of, shelf_life=None),
            section=section,
        )
        for section in fold.test_sections
    ]
    return statistics.fmean([point.rank_ic for point in points if point.rank_ic is not None])


def _with_parameters(
    fitted: FittedCrossSectionalRankModel, parameters: tuple[tuple[str, float], ...]
) -> FittedCrossSectionalRankModel:
    """The same fit with its coefficients replaced -- the counterfactual "unfitted" model.

    Everything else about the artifact is held: the declaration, the feature list, the training
    cutoff and the example count. So the difference between this model's reading and the fitted
    one's is attributable to the **coefficients** and to nothing else, which is what makes the
    comparison a measurement of the fit rather than of two unrelated models.
    """
    held = fitted.artifact
    return FittedCrossSectionalRankModel(
        artifact=AlphaModelArtifact(
            declaration=held.declaration,
            feature_ids=held.feature_ids,
            training_cutoff=held.training_cutoff,
            training_example_count=held.training_example_count,
            parameters=parameters,
        )
    )


# --- 1. the corpus realizes what it planted -----------------------------------------------------


def test_every_realized_label_is_the_return_the_corpus_designed_for_that_cell() -> None:
    """The premise. If the price path did not realize the draw, nothing below means anything.

    Checked against `targets()` -- the designed numbers -- rather than against a second reading of
    the same labels, and to a tolerance that is floating-point compounding rather than modelling
    slack: a close path is built by multiplying and a return is read back by dividing.
    """
    designed = corpus.targets(beta=corpus.ALPHA_BETA)
    panel = corpus.alpha_panel()

    assert panel.excluded == ()
    assert len(panel.sections) == corpus.PREDICTION_DAY_COUNT
    for index, section in enumerate(panel.sections):
        assert len(section.examples) == corpus.SECURITY_COUNT
        for position, example in enumerate(section.examples):
            assert example.target == pytest.approx(designed[index][position], rel=1e-9, abs=1e-12)


def test_the_two_arms_are_one_draw_and_differ_only_in_the_coefficient() -> None:
    """What makes the null a control rather than a second corpus.

    The features are the same numbers in both arms, so any difference between the two readings is
    attributable to the plant. Asserted on the cross sections, which is where a divergence would
    be invisible: a fixture that redrew its features for the control would look identical in every
    summary statistic and would be answering a different question.
    """
    alpha = corpus.alpha_panel()
    null = corpus.null_panel()

    assert [section.cross_section for section in alpha.sections] == [
        section.cross_section for section in null.sections
    ]
    assert corpus.targets(beta=corpus.NULL_BETA) != corpus.targets(beta=corpus.ALPHA_BETA)
    assert corpus.targets(beta=corpus.NULL_BETA) == tuple(
        tuple(corpus.RETURN_SCALE * noise for _signal, _decoy, noise in row)
        for row in corpus.draws()
    )


def test_the_corpus_is_reproducible_from_its_seed_and_from_nothing_else() -> None:
    """Two draws from one seed are one corpus, and the first cell is written down.

    The literal is what turns "seeded" into "reproducible": a cached generator that had been
    advanced by an earlier import would still satisfy an equality between two calls of `draws()`.
    """
    first = corpus.draws()[0][0]

    assert first == pytest.approx(
        (-1.4245294672055753, 0.4100894648583841, -0.88073036185325), rel=1e-12
    )
    assert corpus.draws() is corpus.draws()


def test_the_closed_form_ic_is_computed_from_the_plant_and_reads_nothing() -> None:
    """A "known" IC measured on the corpus by the code under test would be a tautology.

    `known_rank_ic` is arithmetic on `beta` alone: Pearson's `beta / sqrt(beta**2 + 1)` through the
    bivariate normal's `rho_s = (6 / pi) * asin(rho / 2)`. The null arm's is exactly zero rather
    than approximately zero, because `asin(0)` is.
    """
    assert corpus.known_rank_ic(corpus.NULL_BETA) == 0.0
    assert corpus.known_rank_ic(corpus.ALPHA_BETA) == pytest.approx(0.31691376862986, rel=1e-12)
    assert corpus.known_rank_ic(-corpus.ALPHA_BETA) == -corpus.known_rank_ic(corpus.ALPHA_BETA)


# --- 2. the alpha arm measures the plant, and the null arm reports nothing -----------------------


def test_the_known_alpha_arm_measures_near_the_ic_it_was_planted_with() -> None:
    """The benchmark claim: what the system reports on a corpus whose answer is known.

    Three folds, each fitted on its own history, each reading its own block. The tolerance is the
    sampling error a rank correlation over sixty names and six days actually has -- roughly
    `1 / sqrt(59) / sqrt(6)`, about `0.053` -- so `0.08` is a shade over one standard error and is
    a claim rather than a formality.
    """
    evaluations = evaluate_walk_forward(
        _model(),
        walk_forward_folds(
            corpus.alpha_panel(),
            calendar=corpus.trading_calendar(),
            folds=3,
            test_days_per_fold=6,
            embargo_sessions=0,
        ),
        shelf_life=None,
    )
    headlines = [evaluation.mean_rank_ic for evaluation in evaluations]

    assert headlines == [
        pytest.approx(0.285542280263036, rel=1e-12),
        pytest.approx(0.2935630267666945, rel=1e-12),
        pytest.approx(0.3724090025006946, rel=1e-12),
    ]
    known = corpus.known_rank_ic(corpus.ALPHA_BETA)
    for headline in headlines:
        assert headline is not None
        assert abs(headline - known) < 0.08
    assert all(evaluation.scored_ratio == 1.0 for evaluation in evaluations)


def test_the_known_null_control_reports_no_skill_on_pure_noise() -> None:
    """The arm that matters as much as the plant: a system reporting skill on noise is the failure.

    Every fold's headline is inside `REALISTIC_IC` of zero and every fitted coefficient is a small
    number of either sign. The coefficients are asserted beside the headline because they are the
    half a headline cannot show: `V2-P4-014` measured that a leak is visible in the coefficient and
    not in the rank IC, and the converse reading is what says the model found nothing rather than
    that it found something the metric could not see.
    """
    evaluations = evaluate_walk_forward(
        _model(),
        walk_forward_folds(
            corpus.null_panel(),
            calendar=corpus.trading_calendar(),
            folds=3,
            test_days_per_fold=6,
            embargo_sessions=0,
        ),
        shelf_life=None,
    )

    assert [evaluation.mean_rank_ic for evaluation in evaluations] == [
        pytest.approx(-0.008872835046772255, rel=1e-12),
        pytest.approx(-0.008474576271186448, rel=1e-12),
        pytest.approx(-0.03342595165323698, rel=1e-12),
    ]
    for evaluation in evaluations:
        assert evaluation.mean_rank_ic is not None
        assert abs(evaluation.mean_rank_ic) <= REALISTIC_IC + 0.005
        for _name, coefficient in evaluation.artifact.parameters:
            assert abs(coefficient) < 0.05


def test_the_two_arms_never_overlap_so_the_corpus_separates_its_own_control() -> None:
    """The property that makes it a benchmark at all, stated as a gap rather than as two numbers.

    The alpha arm's worst fold is far above the null arm's best. A corpus whose arms overlapped
    would be one where a measured headline is evidence of nothing, which is exactly the state the
    existing deterministic fixtures are in -- they have no null arm to overlap with.
    """
    schedule = {"folds": 5, "test_days_per_fold": 4, "embargo_sessions": 0}
    alpha = evaluate_walk_forward(
        _model(),
        walk_forward_folds(corpus.alpha_panel(), calendar=corpus.trading_calendar(), **schedule),
        shelf_life=None,
    )
    null = evaluate_walk_forward(
        _model(),
        walk_forward_folds(corpus.null_panel(), calendar=corpus.trading_calendar(), **schedule),
        shelf_life=None,
    )
    lowest_alpha = min(item.mean_rank_ic for item in alpha if item.mean_rank_ic is not None)
    highest_null = max(item.mean_rank_ic for item in null if item.mean_rank_ic is not None)

    assert lowest_alpha == pytest.approx(0.270505696026674, rel=1e-12)
    assert highest_null == pytest.approx(0.0807863295359822, rel=1e-12)
    assert lowest_alpha - highest_null > 0.15


def test_this_corpus_cannot_certify_an_ic_the_size_a_real_signal_would_have() -> None:
    """The boundary, measured rather than guessed, and the reason `ALPHA_BETA` is implausible.

    The null arm -- where the answer is exactly zero -- produces folds reading as far as `0.113`
    from it. So over `PREDICTION_DAY_COUNT` days and `SECURITY_COUNT` names, a headline the size a
    real signal would produce is inside this corpus's own noise, and a version of it planted at
    `REALISTIC_IC` could not be told from its own control. That is a statement about sample size
    and not about the construction: widening the corpus is the fix, and nothing in this issue
    needed it.

    Written as a `cannot` because the alternative was to plant a plausible-looking `beta` and
    assert a headline that the null arm also produces -- which is precisely the recurring defect
    of an assertion that cannot separate its two answers.
    """
    null = evaluate_walk_forward(
        _model(),
        walk_forward_folds(
            corpus.null_panel(),
            calendar=corpus.trading_calendar(),
            folds=5,
            test_days_per_fold=4,
            embargo_sessions=0,
        ),
        shelf_life=None,
    )
    wander = max(abs(item.mean_rank_ic) for item in null if item.mean_rank_ic is not None)

    assert wander == pytest.approx(0.11287857738260627, rel=1e-12)
    assert wander > REALISTIC_IC, (
        "a fold of the known-null arm reads further from zero than a real signal's whole IC, so "
        "this corpus cannot certify one"
    )
    assert corpus.known_rank_ic(corpus.ALPHA_BETA) > wander


# --- 3. fitted against unfitted, and why the second column is load-bearing -----------------------


def test_a_model_fitted_on_the_null_arm_reads_the_alpha_arm_as_no_signal_at_all() -> None:
    """The first of three separations: the corpus can tell a fit from a non-fit.

    Same model, same declaration, same cross sections and the same test block -- only the panel
    the coefficients were learned on differs. The fit that saw the plant reads `+0.344`; the fit
    that saw only noise reads `-0.246` on the *same* block. A corpus on which those two came out
    equal would be one where a reported headline says nothing about whether a model was fitted.
    """
    alpha_fold = _fold(corpus.alpha_panel())
    null_fold = _fold(corpus.null_panel())

    fitted_on_alpha = _model().fit(alpha_fold.training_set)
    fitted_on_null = _model().fit(null_fold.training_set)

    assert _read(fitted_on_alpha, alpha_fold) == pytest.approx(0.3441580994720756, rel=1e-12)
    assert _read(fitted_on_null, alpha_fold) == pytest.approx(-0.24588080022228398, rel=1e-12)
    assert dict(fitted_on_alpha.artifact.parameters)[corpus.SIGNAL] > 0.25
    assert abs(dict(fitted_on_null.artifact.parameters)[corpus.SIGNAL]) < 0.05


def test_replacing_the_coefficients_with_equal_weights_costs_a_tenth_of_the_ic() -> None:
    """The second separation, and the one that isolates the coefficients from everything else.

    The counterfactual model shares the fitted one's declaration, feature list, training cutoff and
    example count; only `parameters` differs, and it weights the decoy exactly as heavily as the
    signal. That costs `0.12` of rank IC, which is the whole of what the fit bought.
    """
    fold = _fold(corpus.alpha_panel())
    fitted = _model().fit(fold.training_set)
    flat = _with_parameters(fitted, ((corpus.DECOY, 1.0), (corpus.SIGNAL, 1.0)))

    assert _read(fitted, fold) == pytest.approx(0.3441580994720756, rel=1e-12)
    assert _read(flat, fold) == pytest.approx(0.22249574460456895, rel=1e-12)
    assert _read(fitted, fold) - _read(flat, fold) > 0.1


def test_a_one_column_corpus_cannot_tell_a_fitted_model_from_a_flat_one() -> None:
    """The third, and it is a **negative** result that justifies the corpus's second column.

    The model-face product acceptance measured that with a single feature the reported statistics
    are mathematically invariant to the fit -- sweeping the embargo from 0 to 15 moved the training
    set from 780 examples to 2,640 and left `mean_rank_ic` identical to twelve decimals. The reason
    is arithmetic: one column's score is `coefficient * rank`, a positive scalar multiple of the
    ranks, so the ordering -- and therefore every rank statistic -- does not depend on the
    coefficient at all.

    Driven here on the *same draws*, so the contrast is attributable to the column count and to
    nothing else: one column gives **bit-identical** readings for a fitted model and a flat one,
    two columns give readings `0.12` apart. That is why `FEATURE_IDS` has two entries, and it is
    why a known-alpha corpus built one column wide would not have been a benchmark.
    """
    one = (corpus.SIGNAL,)
    fold = _fold(corpus.alpha_panel(feature_ids=one))
    fitted = _model().fit(fold.training_set)
    flat = _with_parameters(fitted, ((corpus.SIGNAL, 1.0),))

    assert dict(fitted.artifact.parameters)[corpus.SIGNAL] != 1.0
    assert _read(fitted, fold) == _read(flat, fold), "bit-identical, not merely close"
    assert _read(fitted, fold) == pytest.approx(0.35012503473187, rel=1e-12)


# --- what this corpus is not ---------------------------------------------------------------------


def test_two_neighbouring_windows_share_a_session_and_still_carry_independent_targets() -> None:
    """A claim of this corpus's own design, **falsified by this test and rewritten**.

    The module was built believing that a `1d` horizon leaves no two prediction days sharing a
    session, and that is false: `build_label_window` puts a `1d` window's sessions at
    `(k + 1, k + 2)`, so day `k` and day `k + 1` share session `k + 2` as one's exit and the
    other's entry. `TrainingSet.overlaps` reports those pairs and `V2-P4-013`'s purge removes
    them -- **two prediction days' worth at every fold boundary**, measured below.

    What survives the correction is the property the closed form actually needs, which is about
    *returns* and not about sessions: a `1d` window realizes the return **of its exit session**,
    and no two prediction days share an exit. So the targets are independent draws even though the
    windows touch, and the entry session a pair shares contributes to neither one's number.

    None of this makes the corpus a purge fixture, and the reason is `V2-P4-014`'s rather than
    this module's: a fold's `mean_rank_ic` reads exactly `-1.0` on a leaked split *and* on a
    purged one, because a rank correlation is invariant to magnitude and a leak lives in the
    coefficient. A corpus that validated a purge by watching a fold statistic would validate
    nothing however it was built. `walk_forward_fixtures` plants a leak and is the fixture for it.
    """
    panel = corpus.alpha_panel()
    mine = [
        example.label.window
        for example in panel.examples
        if example.ts_code == corpus.SECURITIES[0]
    ]

    assert all(len(window.sessions) == 2 for window in mine)
    assert len({window.exit_day for window in mine}) == len(mine), "one exit session each"
    assert mine[0].exit_day == mine[1].entry_day, "and the neighbour shares it as an entry"

    fold = _fold(panel)
    assert len(fold.purged) == 2 * corpus.SECURITY_COUNT
    assert len({example.label.window.prediction_day for example in fold.purged}) == 2


def test_the_shelf_life_this_corpus_can_drive_moves_a_headline_the_leak_fixture_cannot() -> None:
    """`V2-P4-018` and `V2-P4-022` meeting, and the reason both were needed.

    On `walk_forward_fixtures`' corpus every test day's rank IC is exactly `-1.0`, so a fold that
    expires halfway reports the same headline as one that did not -- an assertion there cannot
    separate the two answers even though the abstentions are real. Here the daily readings vary,
    so expiring the back half of a block moves the headline by a number worth asserting, and the
    pair `(mean_rank_ic, scored_ratio)` is measurably a pair rather than a formality.
    """
    fold = _fold(corpus.alpha_panel())
    cutoff = fold.training_set.training_cutoff
    gaps = [section.as_of - cutoff for section in fold.test_sections]
    shelf_life = gaps[3]

    full = evaluate_walk_forward(_model(), (fold,), shelf_life=None)[0]
    expired = evaluate_walk_forward(_model(), (fold,), shelf_life=shelf_life)[0]

    assert sum(1 for gap in gaps if gap <= shelf_life) == 4
    assert full.measured_count == TEST_DAY_COUNT
    assert expired.measured_count == 4
    assert full.scored_ratio == 1.0
    assert expired.scored_ratio == pytest.approx(0.5)
    assert full.mean_rank_ic is not None and expired.mean_rank_ic is not None
    assert abs(full.mean_rank_ic - expired.mean_rank_ic) > 0.02, (
        "the headline has to move, or this file would be repeating the leak fixture's problem"
    )


def test_the_tree_model_reads_the_same_two_arms_apart_and_overfits_the_null_further() -> None:
    """The corpus grades a *system*, not one baseline, and the second model is what says so.

    `evaluate_fold` takes the `AlphaModel` **Protocol**, so the tree from a package the baseline
    may not import is measured by exactly the same function. It reads the alpha arm at `0.292`
    against a closed form of `0.317` and the null arm at `-0.061`, which is the answer this issue
    wanted: a second, more flexible model finds the plant and does not find a signal in noise.

    And the null reading is **further from zero than the rank baseline's** `-0.030`, on the same
    draws and the same block. That is the corpus doing the thing a noiseless fixture cannot: a
    model with more freedom fits more of the noise, the overfit is visible as a larger spurious
    IC, and the *size* of it is a number rather than an intuition. It is not a defect in either
    model -- it is what a known-null control is for.
    """
    from openalpha_cn.backtest.alpha_tree import TREE_FAMILY, BoostedRankTreeModel
    from openalpha_cn.domain.alpha_model import AlphaModelDeclaration

    tree = BoostedRankTreeModel(
        declaration=AlphaModelDeclaration(
            name="known_signal_tree",
            family=TREE_FAMILY,
            horizon=corpus.HORIZON,
            feature_version="features/v1",
            seed=corpus.SEED,
            code_commit="0123456789abcdef",
            hyperparameters=(
                ("learning_rate", 0.2),
                ("max_depth", 3),
                ("min_leaf_securities", 12),
                ("tree_count", 24),
            ),
        )
    )
    alpha = evaluate_walk_forward(tree, (_fold(corpus.alpha_panel()),), shelf_life=None)[0]
    null = evaluate_walk_forward(tree, (_fold(corpus.null_panel()),), shelf_life=None)[0]
    baseline_null = evaluate_walk_forward(_model(), (_fold(corpus.null_panel()),), shelf_life=None)[
        0
    ]

    assert alpha.mean_rank_ic == pytest.approx(0.2916503641796574, rel=1e-12)
    assert null.mean_rank_ic == pytest.approx(-0.06147086645590597, rel=1e-12)
    assert baseline_null.mean_rank_ic == pytest.approx(-0.029522089469297023, rel=1e-12)
    assert alpha.mean_rank_ic is not None and null.mean_rank_ic is not None
    assert abs(alpha.mean_rank_ic - corpus.known_rank_ic(corpus.ALPHA_BETA)) < 0.05
    assert baseline_null.mean_rank_ic is not None
    assert abs(null.mean_rank_ic) > abs(baseline_null.mean_rank_ic)


def test_a_column_this_corpus_never_drew_is_refused_and_the_other_two_shapes_are_not_its_job() -> (
    None
):
    """A mutation survivor, resolved by deleting two thirds of the check rather than asserting it.

    `cross_section_for` used to refuse an unknown column, an unsorted list *and* an empty one, and
    a mutant deleting the whole guard survived. Measured, `FeatureCrossSection` already refuses the
    last two by name -- so restating them was one check plus a place for the two to disagree. What
    is left is the one thing the contract cannot know: this corpus draws two columns, and a name
    outside them has no values at all.
    """
    day = corpus.prediction_days()[0]

    with pytest.raises(ValueError, match="is not a column this corpus draws"):
        corpus.cross_section_for(day, feature_ids=("signal_column", "unplanted"))

    from openalpha_cn.domain.alpha_model import AlphaModelError

    with pytest.raises(AlphaModelError, match="not strictly increasing"):
        corpus.cross_section_for(day, feature_ids=(corpus.SIGNAL, corpus.DECOY))
    with pytest.raises(AlphaModelError, match="names no feature"):
        corpus.cross_section_for(day, feature_ids=())


def test_the_corpus_says_in_its_own_docstring_what_it_is_not_evidence_about() -> None:
    """Findings live where a reader meets them, and a fixture's reader meets its docstring.

    There is no `KNOWN_*` registry for a module under `tests/` -- the audit in
    `tests/unit/test_known_limitation_registries.py` scans `src/openalpha_cn` -- so the boundary
    has to be carried in prose and held by an assertion, which is what this is.
    """
    text = corpus.__doc__ or ""

    assert "What this corpus cannot separate" in text
    assert "V2-P4-013" in text
    assert "no price was ever quoted" in text
    assert str(timedelta(0)) not in text
