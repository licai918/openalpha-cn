"""`V2-P4-013`: the planted leak, and which rule is the one that removes it.

A split whose leak cannot be demonstrated is a split nobody has verified, so the leak here is
**planted** rather than hoped for. `tests/walk_forward_fixtures.py` builds one close series per
security and reads every label off it, so two windows that share sessions share the prices those
sessions printed; a single per-session coefficient decides which direction the `momentum_20d`
feature was rewarded in, and the corpus flips that coefficient at one chosen session.

The honest answer is the same in both corpora: over the sessions a fold may legitimately learn
from, a higher `momentum_20d` realized a **lower** return, while over the test block it realized
a higher one. So a fold that comes out predicting the test block's direction learned it from
sessions it should not have read, and the measurement is which rule takes that away.

`backtest/alpha_model.py`'s reference model is the instrument because its whole learned state is
one bit -- a `sign` in `AlphaModelArtifact.parameters` -- so "what did the fit absorb" is a
number read off a stored artifact rather than an opinion about a curve. It is **not** a baseline
and none of this is a claim about alpha; `V2-P4-014` and `V2-P4-015` own that, and `V2-P4-022`
owns the corpus with a known signal-to-noise ratio and a known-null control that an evaluation
would need before reporting anything.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from walk_forward_fixtures import (
    ALIGNED_FROM_ADJACENT,
    ALIGNED_FROM_OVERLAPPING,
    EMBARGO_SESSIONS,
    FIRST_TEST_DAY_INDEX,
    MOMENTUM_VALUES,
    SECURITIES,
    TEST_DAYS_PER_FOLD,
    declaration,
    panel,
    prediction_days,
    trading_calendar,
)

from openalpha_cn.backtest.alpha_model import SIGN_PARAMETER, SingleFeatureAlphaModel
from openalpha_cn.backtest.walk_forward import (
    PanelSection,
    WalkForwardFold,
    shared_sessions,
)
from openalpha_cn.domain.alpha_model import TrainingExample, TrainingSet

ALIGNED = 1.0
OPPOSED = -1.0
"""The two directions the corpus's session coefficient can take, in the reference's own units."""


def _fold(*, aligned_from: int, embargo: int) -> WalkForwardFold:
    return WalkForwardFold(
        panel=panel(aligned_from=aligned_from),
        calendar=trading_calendar(),
        first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=embargo,
    )


def _learned_direction(fold: WalkForwardFold, examples: tuple[TrainingExample, ...]) -> float:
    """The one bit the reference fit absorbed, read off the artifact it produced."""
    artifact = (
        SingleFeatureAlphaModel(declaration=declaration())
        .fit(TrainingSet(feature_ids=fold.panel.feature_ids, examples=examples))
        .artifact
    )
    return dict(artifact.parameters)[SIGN_PARAMETER]


def _concordance(scores: Mapping[str, float], realized: Mapping[str, float]) -> float:
    """The fraction of security pairs the scores order the way the outcomes did.

    Kendall's concordant-pair count without the tie machinery, which this corpus needs none of:
    both the feature and the realized targets are strictly increasing across the four names, so
    every pair is decided and the statistic can only come out `1.0` or `0.0`. That is the point
    -- a measurement whose two answers are the two extremes cannot be read as noise.
    """
    codes = sorted(scores)
    pairs = [(a, b) for index, a in enumerate(codes) for b in codes[index + 1 :]]
    agree = sum(1 for a, b in pairs if (scores[a] > scores[b]) == (realized[a] > realized[b]))
    return agree / len(pairs)


def _measured_skill(fold: WalkForwardFold, examples: tuple[TrainingExample, ...]) -> float:
    """Fit on `examples`, predict the fold's first test cross section, score it against truth."""
    fitted = SingleFeatureAlphaModel(declaration=declaration()).fit(
        TrainingSet(feature_ids=fold.panel.feature_ids, examples=examples)
    )
    section: PanelSection = fold.test_sections[0]
    batch = fitted.predict(section.cross_section, predicted_at=section.as_of, shelf_life=None)
    scores = {item.ts_code: item.score for item in batch.predictions if item.score is not None}
    realized = {item.ts_code: item.target for item in section.examples}
    assert set(scores) == set(realized)
    return _concordance(scores, realized)


def test_the_corpus_really_did_reward_a_higher_feature_over_the_test_block() -> None:
    """The plant, measured off the labels rather than restated from the fixture's docstring.

    Every assertion below reads "the honest fit learns the opposite direction", which is only
    meaningful if the test block's own direction is the aligned one. Asserted first, and for
    both corpora, so a fixture edit that flattened the plant fails here rather than turning the
    two tests below into two tautologies.
    """
    for aligned_from in (ALIGNED_FROM_OVERLAPPING, ALIGNED_FROM_ADJACENT):
        section = _fold(aligned_from=aligned_from, embargo=0).test_sections[0]
        realized = [
            next(item.target for item in section.examples if item.ts_code == ts_code)
            for ts_code in SECURITIES
        ]
        assert list(MOMENTUM_VALUES) == sorted(MOMENTUM_VALUES)
        assert realized == sorted(realized)
        assert realized[0] < 0.0 < realized[-1]


def test_the_purge_is_what_stops_the_fit_learning_the_test_blocks_direction() -> None:
    """Corpus one: the leak sits in sessions the test labels also read.

    Twenty-four of the forty-eight candidate rows carry a window that reaches into the test
    block, and their targets are dominated by it -- the aligned coefficient is twenty times the
    opposed one, so one aligned session outweighs five opposed ones. A fit given them learns the
    test block's own direction; the purged fit learns the opposite, which is what the sessions it
    was allowed to see actually did.

    The measured difference on this corpus is **not** two skill numbers, and that is the finding
    rather than a gap: every purged example, on its own, pushes the artifact's training cutoff
    past the instant the batch stands at, so `PredictionBatch` refuses the unpurged fold outright.
    `V2-P4-011`'s floor and this purge are the same comparison at two scopes.
    """
    fold = _fold(aligned_from=ALIGNED_FROM_OVERLAPPING, embargo=0)
    assert len(fold.purged) == 24
    assert len(fold.train_examples) == 24
    assert _learned_direction(fold, fold.candidates) == ALIGNED
    assert _learned_direction(fold, fold.train_examples) == OPPOSED

    section = fold.test_sections[0]
    unpurged = SingleFeatureAlphaModel(declaration=declaration()).fit(
        TrainingSet(feature_ids=fold.panel.feature_ids, examples=fold.candidates)
    )
    with pytest.raises(ValueError, match="the fit consumed an outcome"):
        unpurged.predict(section.cross_section, predicted_at=section.as_of, shelf_life=None)
    assert _measured_skill(fold, fold.train_examples) == 0.0


def test_the_embargo_is_what_stops_it_when_the_leak_shares_no_session_at_all() -> None:
    """Corpus two: the leak sits in the two sessions before the block, which nothing is shared with.

    The aligned regime starts two sessions earlier here, so the eight candidate rows on
    prediction days 4 and 5 carry windows that close on sessions 10 and 11 -- read by no test
    label, since the earliest of those starts on session 13. The purge cannot reach them at any
    formulation built on shared sessions, and it does not: the purged set is byte-for-byte the
    one the first corpus produced.

    So this is where the two rules are separable, and the measured difference is a skill number
    on both sides: the purge-only split scores a perfect **1.0** against the realized test
    outcomes and the purged-and-embargoed one scores **0.0**, the whole of it leakage.
    """
    purge_only = _fold(aligned_from=ALIGNED_FROM_ADJACENT, embargo=0)
    embargoed = _fold(aligned_from=ALIGNED_FROM_ADJACENT, embargo=EMBARGO_SESSIONS)

    assert len(embargoed.embargoed) == 8
    assert set(embargoed.purged) == set(purge_only.purged)
    assert _learned_direction(purge_only, purge_only.train_examples) == ALIGNED
    assert _learned_direction(embargoed, embargoed.train_examples) == OPPOSED

    assert _measured_skill(purge_only, purge_only.train_examples) == 1.0
    assert _measured_skill(embargoed, embargoed.train_examples) == 0.0


def test_nothing_the_embargo_removes_shares_a_session_with_any_test_label() -> None:
    """Why no purge can substitute for the embargo, stated structurally rather than by width.

    The purge is a comparison against the instant the fold is first asked at, and every example
    the embargo takes had already closed before it. There is no session in common to find, so a
    rule written on shared sessions -- `TrainingSet.overlaps`' own measurement, or this module's
    `shared_sessions` -- returns nothing about them however it is anchored.
    """
    fold = _fold(aligned_from=ALIGNED_FROM_ADJACENT, embargo=EMBARGO_SESSIONS)
    assert fold.embargoed
    assert shared_sessions(fold.embargoed, fold.test_examples) == ()
    deadline = fold.first_test_as_of
    for example in fold.embargoed:
        window = example.label.window
        assert window.close_instant(window.exit_day) < deadline


def test_the_two_rules_remove_two_different_things_on_one_corpus() -> None:
    """One fold, both rules, and the sets they take laid side by side.

    The pair of tests above each turn one rule off; this one leaves both on and asserts that
    what each removed is disjoint from the other and non-empty, so a future implementation that
    folded the two into one wider cut fails here rather than passing both of them.
    """
    fold = _fold(aligned_from=ALIGNED_FROM_ADJACENT, embargo=EMBARGO_SESSIONS)
    assert fold.purged and fold.embargoed
    assert set(fold.purged) & set(fold.embargoed) == set()
    assert len(fold.candidates) == len(fold.purged) + len(fold.embargoed) + len(fold.train_examples)
    assert fold.leaked_sessions == ()
