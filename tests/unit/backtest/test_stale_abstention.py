"""`V2-P4-018`: what an expired fit does to a fold's numbers, and why that is not free skill.

`tests/unit/domain/test_alpha_model_staleness.py` drives the contract -- the comparison, the
vocabulary, the two mistakes it is not. This file drives the consequence, which is the half the
issue could have got wrong: **an abstention is not free.** `V2-P4-014` made
`FoldEvaluation.scored_ratio` the one statistic that is never `None` on the argument that
*"abstaining on the hard names is otherwise a free way to win"*, and a model that abstains on
everything has a perfect record unless something says so.

Nothing new was built to say so. `V2-P4-018` set out expecting to need a guard and measured that
it does not: an expired fit abstains on every row, so it reaches `scored_ratio` by exactly the
path an unrankable cross section already did, and `FoldEvaluation`'s own validator refuses to let
a fold carry a `mean_rank_ic` its coverage says it does not have. What is asserted below is that
this is true rather than plausible.

## The geometry this file rests on

`walk_forward_fixtures`' second fold is fitted through `2026-01-14 15:00+08:00` and asked on four
consecutive mornings, so the gap from the training cutoff **grows across the block** -- four days
and eighteen hours on the first test day, seven and eighteen on the last. That is what makes a
partially expired fold constructible at all, and it is a property of any walk-forward schedule
rather than of this fixture: a fold is fitted once and read forward.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import walk_forward_fixtures as wf

from openalpha_cn.backtest.alpha_baseline import (
    BASELINE_FAMILY,
    CrossSectionalRankModel,
    FoldEvaluation,
    evaluate_fold,
    evaluate_walk_forward,
)
from openalpha_cn.backtest.walk_forward import WalkForwardFold, walk_forward_folds
from openalpha_cn.domain.alpha_model import (
    ABSTAIN_STALE_MODEL,
    AlphaModelDeclaration,
    AlphaModelError,
)

FOLD_HORIZON = "5d"

FRESH_DAY_COUNT = 2
"""How many of the four test days a six-day shelf life leaves fresh -- see `SHELF_LIFE`."""

SHELF_LIFE = timedelta(days=6)
"""Wider than the first two test days' gaps and narrower than the last two.

The four gaps are 4d18h, 5d18h, 6d18h and 7d18h, so six days is the only whole-day span that
splits this block two and two. Chosen by measurement --
`test_the_gaps_this_file_splits_are_the_ones_the_fixture_produces` asserts all four -- because a
span that happened to leave every day fresh would turn every assertion below into a statement
about a model that never expired.
"""


def _model() -> CrossSectionalRankModel:
    """The rank baseline over `walk_forward_fixtures`' two columns.

    Declared here rather than through `wf.declaration()`, whose family is the single-feature
    reference's: `CrossSectionalRankModel` refuses a declaration naming a code path it never went
    through, which is the check that makes `family` a fact rather than a label.
    """
    return CrossSectionalRankModel(
        declaration=AlphaModelDeclaration(
            name="stale_rank_baseline",
            family=BASELINE_FAMILY,
            horizon=FOLD_HORIZON,
            feature_version="features/v1",
            seed=7,
            code_commit="0123456789abcdef",
        )
    )


def _fold() -> WalkForwardFold:
    """One fold of `V2-P4-013`'s adjacent corpus, purged and embargoed as that issue ships it."""
    return WalkForwardFold(
        panel=wf.panel(aligned_from=wf.ALIGNED_FROM_ADJACENT),
        calendar=wf.trading_calendar(),
        first_test_day=wf.prediction_days()[wf.FIRST_TEST_DAY_INDEX],
        test_day_count=wf.TEST_DAYS_PER_FOLD,
        embargo_sessions=wf.EMBARGO_SESSIONS,
    )


def test_the_gaps_this_file_splits_are_the_ones_the_fixture_produces() -> None:
    """The premise: the gap grows across a test block, and `SHELF_LIFE` cuts it two and two."""
    fold = _fold()
    cutoff = fold.training_set.training_cutoff

    gaps = [section.as_of - cutoff for section in fold.test_sections]

    assert gaps == [
        timedelta(days=4, hours=18),
        timedelta(days=5, hours=18),
        timedelta(days=6, hours=18),
        timedelta(days=7, hours=18),
    ]
    assert sum(1 for gap in gaps if gap <= SHELF_LIFE) == FRESH_DAY_COUNT


def test_a_fold_stale_throughout_reports_no_headline_rather_than_a_flattering_one() -> None:
    """The whole answer to "a model that abstains on everything has a perfect record".

    It does not have one. It has **no** record: every day is scored at nothing, so no day is
    `measured`, `FoldEvaluation`'s validator refuses a `mean_rank_ic` beside a coverage that is
    not `measured`, and the headline a challenger would quote is `None`. The number that is *not*
    `None` is `scored_ratio`, and it reads `0.0` -- which is the reading that makes this
    comparable to anything else.
    """
    fold = _fold()
    evaluation = evaluate_fold(_model(), fold, shelf_life=timedelta(days=1))

    assert evaluation.coverage != "measured"
    assert evaluation.mean_rank_ic is None
    assert evaluation.rank_icir is None
    assert evaluation.measured_count == 0
    assert evaluation.scored_ratio == 0.0
    assert evaluation.test_day_count == wf.TEST_DAYS_PER_FOLD


def test_every_row_of_an_expired_fold_carries_the_coded_reason_and_none_is_missing() -> None:
    """S35's `显式`: a reader can see which of the three conditions produced each abstention.

    Driven on the batch rather than on the fold, because `BaselineScorePoint` carries counts and
    the *reason* only survives on the rows. `offered_count` and `scored_count` are asserted beside
    it so that "expired" is visibly different from "the cross section was empty".
    """
    fold = _fold()
    fitted = _model().fit(fold.training_set)

    for section in fold.test_sections:
        batch = fitted.predict(
            section.cross_section, predicted_at=section.as_of, shelf_life=timedelta(days=1)
        )
        assert batch.subjects == section.cross_section.subjects
        assert batch.scored == ()
        assert {item.abstention for item in batch.abstained} == {ABSTAIN_STALE_MODEL}


def test_a_fold_that_expires_partway_reports_the_fresh_headline_and_a_ratio_below_one() -> None:
    """The free-skill case, and the measurement that says the headline alone cannot catch it.

    A fold read under `SHELF_LIFE` scores its first two days and abstains on its last two. Its
    `mean_rank_ic` is therefore taken over an **easier population** than the fold that answered all
    four -- and it is not a worse-looking number for it. Both halves are asserted:

    - the expired fold's headline is exactly the mean of the fresh days' own `rank_ic`s, so a
      reader comparing two headlines is comparing two different questions;
    - `scored_ratio` and `measured_count` are what separate them, at `0.5` and `2` against `1.0`
      and `4`.

    This is `V2-P4-014`'s argument arriving with a second producer. That issue built
    `scored_ratio` for a model that declines the *names* it finds hard; this is a model that
    declines the *days*, and the same denominator catches both because it is the offered market
    over the whole block rather than a per-day verdict.
    """
    fold = _fold()
    full = evaluate_fold(_model(), fold, shelf_life=None)
    expired = evaluate_fold(_model(), fold, shelf_life=SHELF_LIFE)

    fresh_ics = [
        point.rank_ic for point in full.points[:FRESH_DAY_COUNT] if point.rank_ic is not None
    ]
    assert len(fresh_ics) == FRESH_DAY_COUNT

    assert expired.mean_rank_ic == pytest.approx(statistics.fmean(fresh_ics))
    assert expired.measured_count == FRESH_DAY_COUNT
    assert full.measured_count == wf.TEST_DAYS_PER_FOLD
    assert expired.scored_ratio == pytest.approx(FRESH_DAY_COUNT / wf.TEST_DAYS_PER_FOLD)
    assert full.scored_ratio == 1.0


def test_the_headline_alone_cannot_tell_the_truncated_fold_from_an_honestly_short_one() -> None:
    """The defect this repository keeps meeting, stated where it applies to this issue.

    A fold read under `SHELF_LIFE` and a two-day fold over the same two days report the **same**
    `mean_rank_ic` -- so an assertion on the headline cannot separate a model that expired from a
    model that was only ever asked twice. It is `scored_ratio` and `test_day_count` that can, and
    that is exactly why `V2-P4-014` refused to let either be `None`.

    Written as a falsification rather than as a warning: `V2-P4-018` set out to assert that a
    stale fold "looks worse", measured that a partially stale one does not, and asserts the
    equality instead.

    **And this corpus cannot tell the two apart even in principle**, which was the second
    falsification. Every test day of the adjacent leak fixture reads a rank IC of exactly `-1.0`,
    so *any* subset of its block averages to `-1.0` and the equality below would hold however
    `evaluate_fold` treated a stale day -- the recurring defect of an assertion that cannot
    separate its own two answers, met head on. The corpus that can is `V2-P4-022`'s, and
    `tests/unit/backtest/test_known_signal_corpus.py::
    test_the_shelf_life_this_corpus_can_drive_moves_a_headline_the_leak_fixture_cannot` is the
    same comparison on daily readings that vary. Both are kept: this one states the shape, that
    one has the teeth.
    """
    fold = _fold()
    truncated = WalkForwardFold(
        panel=fold.panel,
        calendar=fold.calendar,
        first_test_day=fold.first_test_day,
        test_day_count=FRESH_DAY_COUNT,
        embargo_sessions=fold.embargo_sessions,
    )

    expired = evaluate_fold(_model(), fold, shelf_life=SHELF_LIFE)
    honest = evaluate_fold(_model(), truncated, shelf_life=None)
    full = evaluate_fold(_model(), fold, shelf_life=None)

    assert expired.mean_rank_ic == pytest.approx(honest.mean_rank_ic)
    assert expired.scored_ratio == pytest.approx(0.5)
    assert honest.scored_ratio == 1.0
    assert expired.test_day_count == 4
    assert honest.test_day_count == FRESH_DAY_COUNT
    assert {point.rank_ic for point in full.points} == {-1.0}, (
        "the reason this file's equality is weak evidence, asserted rather than left implied"
    )


def test_the_artifact_a_stale_fold_carries_is_the_one_it_would_have_carried_anyway() -> None:
    """The fit is unchanged; only the reading of it is. That is why the span is not addressed.

    `FoldEvaluation.artifact` is carried by value, and the two evaluations below carry a
    byte-identical one under the same `artifact_id` -- so a reader comparing a stale fold with a
    fresh one is comparing two readings of one fit rather than two fits. Had the shelf life gone
    on the declaration, these would be two addresses for one set of coefficients.
    """
    fold = _fold()
    expired = evaluate_fold(_model(), fold, shelf_life=timedelta(days=1))
    full = evaluate_fold(_model(), fold, shelf_life=None)

    assert expired.artifact == full.artifact
    assert expired.artifact.artifact_id == full.artifact.artifact_id


def test_one_shelf_life_covers_a_whole_schedule_rather_than_one_per_fold() -> None:
    """A schedule that read its early folds more leniently would not be a series.

    Driven on two folds whose training cutoffs are six days apart: the same span expires the older
    fold's late days and the younger fold's later ones, and both readings come out of one call.
    """
    folds = walk_forward_folds(
        wf.panel(aligned_from=wf.ALIGNED_FROM_ADJACENT),
        calendar=wf.trading_calendar(),
        folds=wf.FOLDS,
        test_days_per_fold=wf.TEST_DAYS_PER_FOLD,
        embargo_sessions=wf.EMBARGO_SESSIONS,
    )
    evaluations = evaluate_walk_forward(_model(), folds, shelf_life=timedelta(days=1))

    assert len(evaluations) == wf.FOLDS
    assert {evaluation.scored_ratio for evaluation in evaluations} == {0.0}
    assert all(evaluation.mean_rank_ic is None for evaluation in evaluations)
    assert len({evaluation.artifact.artifact_id for evaluation in evaluations}) == wf.FOLDS


def test_a_negative_shelf_life_reaches_this_harness_as_a_refusal_and_not_as_a_silent_zero() -> None:
    """The contract's refusal is not swallowed by the evaluation loop."""
    with pytest.raises(AlphaModelError, match="which is negative"):
        evaluate_fold(_model(), _fold(), shelf_life=timedelta(seconds=-1))


def test_a_fold_cannot_say_which_condition_emptied_it_and_only_the_batch_can() -> None:
    """A boundary found while writing this file, and it is a `cannot` rather than a defect.

    `V2-P4-018`'s docstrings claim an expired fit reaches `scored_ratio` by exactly the path an
    unrankable cross section already did. That is true, and the price of it is here: neither
    `FoldEvaluation` nor `BaselineScorePoint` carries an abstention *reason*, so a fold whose every
    row expired and a fold whose every row was short a column are indistinguishable field by field
    once summarised. The three codes survive only on the batch a `PredictionRecord` stores, which
    is the surface where a reader can act on the difference and is where
    `tests/integration/test_model_interfaces.py` reads it.

    Asserted structurally rather than by building the second fixture, because what makes the two
    indistinguishable is the *field set*: a fold gains the ability to tell them apart only if
    somebody adds a field, and this fails on the day they do.
    """
    from openalpha_cn.backtest.alpha_baseline import BaselineScorePoint

    fields = set(FoldEvaluation.model_fields) | set(BaselineScorePoint.model_fields)

    assert not any("abstention" in name or "reason" in name for name in fields), sorted(fields)
    expired = evaluate_fold(_model(), _fold(), shelf_life=timedelta(days=1))
    assert expired.scored_ratio == 0.0
    assert {point.scored_count for point in expired.points} == {0}
    assert {point.offered_count for point in expired.points} != {0}, (
        "offered is what separates an emptied fold from one nobody was asked about"
    )


def test_the_expiry_check_exists_in_exactly_one_place_in_the_source_tree() -> None:
    """The claim `prediction_batch_for` makes about itself, held against the source.

    That function's docstring argues the check belongs at the one chokepoint every implementation
    goes through rather than at the top of each `predict` -- `require_features`' own argument, and
    the one `V2-P4-011` measured a second copy of being worth nothing. A second copy would not
    fail any test in this repository; it would just be a place a future implementation can skip.
    So the guarantee is a source count, and it is the same shape as
    `tests/unit/test_import_layering.py`'s probe: a structural rule a test rather than a linter
    enforces.
    """
    root = Path(__file__).resolve().parents[3] / "src" / "openalpha_cn"
    sources = {path: path.read_text(encoding="utf-8") for path in root.rglob("*.py")}

    def sites(anchor: str) -> list[str]:
        return sorted(
            f"{path.relative_to(root).as_posix()}:{text.count(anchor)}"
            for path, text in sources.items()
            if anchor in text
        )

    # The *value*, not the name: `backtest/alpha_baseline.py` names `ABSTAIN_STALE_MODEL` in prose
    # -- which is the point of that prose -- and naming it is not producing it.
    assert sites("abstention=ABSTAIN_STALE_MODEL") == ["domain/alpha_model.py:1"]
    assert sites("is_stale_at(") == ["domain/alpha_model.py:2"], (
        "the definition and the single call, and nothing else in the tree asks the question"
    )


def test_this_files_corpus_is_the_leak_fixture_and_not_a_benchmark() -> None:
    """Named where a reader meets it, because the numbers above invite the wrong reading.

    `walk_forward_fixtures` plants a leak and has no noise model, so the `mean_rank_ic`s compared
    here are properties of a deterministic construction. Nothing in this file is evidence that the
    baseline has skill; what it measures is that expiry moves coverage and coverage moves the
    headline's comparability. `V2-P4-022`'s corpus is where a known IC lives.
    """
    fold = _fold()
    full = evaluate_fold(_model(), fold, shelf_life=None)

    assert full.mean_rank_ic == -1.0, (
        "the adjacent corpus's honest reading, which is a construction rather than a measurement "
        "of skill -- V2-P4-014 recorded the same number for the same reason"
    )
    assert datetime(2026, 1, 19, 1, 0, tzinfo=UTC) == fold.test_sections[0].as_of
