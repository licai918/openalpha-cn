"""`V2-P4-018` at the product boundary: what a caller declares, and what refuses an expired run.

The contract abstains (`tests/unit/domain/test_alpha_model_staleness.py`) and the harness reports
the abstention as coverage (`tests/unit/backtest/test_stale_abstention.py`). This file is the
third link and the one that answers "so what stops a stale model looking skilful": **nothing on
this face refuses an all-abstaining run except the coverage floor the caller declared.**

That is a boundary rather than a hole, and it is the same shape `V2-P4-014` argued for
`scored_ratio` -- a headline is comparable only beside the fraction of the market it was taken
over -- so the two flags are one mechanism. It is stated here, driven both ways, and named in
`KNOWN_MODEL_VIEW_LIMITATIONS`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from openalpha_cn.backtest.alpha_baseline import BASELINE_FAMILY, FoldEvaluation
from openalpha_cn.domain.alpha_model import artifact_for
from openalpha_cn.feature_matrix import FeatureColumn
from openalpha_cn.model_view import (
    KNOWN_MODEL_VIEW_LIMITATIONS,
    EvaluationRequest,
    ModelEvaluation,
    ModelRequestError,
    evaluation_view,
    model_evaluation_request,
)
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS

DAY = date(2026, 1, 9)


def _request(*, minimum_scored_ratio: float, shelf_life_days: int | None) -> EvaluationRequest:
    return model_evaluation_request(
        columns=(FeatureColumn(definition=FACTOR_DEFINITIONS.get("reversal_1d/v1"), tier="raw"),),
        name="shelf_life_face",
        family=BASELINE_FAMILY,
        horizon="5d",
        seed=1,
        start=date(2026, 1, 6),
        end=DAY,
        as_of=datetime(2026, 1, 20, 4, 0, tzinfo=UTC),
        years=(2026,),
        exchange="SZSE",
        folds=1,
        test_days_per_fold=1,
        embargo_sessions=0,
        minimum_scored_ratio=minimum_scored_ratio,
        shelf_life_days=shelf_life_days,
        code_commit="abcdef1234567",
        config_digest="e" * 64,
    )


def _all_abstained_fold(request: EvaluationRequest) -> FoldEvaluation:
    """One fold whose every row abstained -- what an expired fit produces, in summary form.

    Constructed rather than evaluated: what is under test here is the *aggregation and the bar*,
    and `tests/unit/backtest/test_stale_abstention.py` is where a real expired fold is measured
    producing exactly these numbers. Building it here keeps this file off the panel plane.
    """
    import walk_forward_fixtures

    from openalpha_cn.backtest.alpha_baseline import BaselineScorePoint
    from openalpha_cn.domain.alpha_model import TrainingSet

    panel = walk_forward_fixtures.panel(aligned_from=walk_forward_fixtures.ALIGNED_FROM_OVERLAPPING)
    artifact = artifact_for(
        declaration=walk_forward_fixtures.declaration(),
        training_set=TrainingSet(
            feature_ids=panel.feature_ids, examples=panel.sections[0].examples
        ),
    )
    point = BaselineScorePoint(
        as_of=datetime(2026, 1, 9, 9, 0, tzinfo=UTC),
        predicted_at=datetime(2026, 1, 9, 9, 0, tzinfo=UTC),
        prediction_day=DAY,
        offered_count=40,
        scored_count=0,
        paired_count=0,
        coverage="insufficient_sample",
        rank_ic=None,
    )
    assert request.run.shelf_life is not None
    return FoldEvaluation(
        artifact=artifact,
        first_test_day=DAY,
        points=(point,),
        coverage="insufficient_as_ofs",
        measured_count=0,
        mean_rank_ic=None,
        stdev_rank_ic=None,
        rank_icir=None,
        scored_ratio=0.0,
    )


def _evaluation(request: EvaluationRequest) -> ModelEvaluation:
    return ModelEvaluation(
        request=request,
        prediction_days=(DAY,),
        excluded=(),
        folds=(_all_abstained_fold(request),),
    )


def test_an_expired_run_is_refused_only_by_the_coverage_floor_the_caller_declared() -> None:
    """Driven both ways on one evaluation, because a bar asserted in one direction is a constant.

    Above the floor the same all-abstaining fold is `is_blocked: true` with `admitted: null`;
    at a floor of `0.0` it is a clean success carrying an empty `admitted` list. Both are the
    correct behaviour of `minimum_scored_ratio` and together they are the whole of why an
    abstention is not free: the answer is refused by the *coverage* bar, never by the headline,
    which is `null` in both.
    """
    refused = _evaluation(_request(minimum_scored_ratio=0.5, shelf_life_days=1))
    admitted = _evaluation(_request(minimum_scored_ratio=0.0, shelf_life_days=1))

    assert refused.scored_ratio == admitted.scored_ratio == 0.0
    assert refused.is_blocked is True
    assert admitted.is_blocked is False
    assert evaluation_view(refused)["admitted"] is None
    assert evaluation_view(admitted)["admitted"] == [refused.folds[0].artifact.artifact_id]
    assert {item.code for item in KNOWN_MODEL_VIEW_LIMITATIONS} >= {
        "an_expired_run_is_refused_only_by_the_coverage_floor_the_caller_declared"
    }


def test_the_declared_span_is_rendered_on_the_answer_and_reads_null_when_none_was_declared() -> (
    None
):
    """`declared_feature_version`'s arrangement: a boundary a reader meets on the answer.

    An undeclared shelf life is not a silently-chosen one, and the difference has to be visible in
    the body rather than inferable from the command line. Both faces render one declaration, so
    one key covers `model evaluate` and `model daily-run` together.
    """
    declared = evaluation_view(_evaluation(_request(minimum_scored_ratio=0.0, shelf_life_days=7)))
    silent = _request(minimum_scored_ratio=0.0, shelf_life_days=None)

    assert declared["declaration"]["shelf_life_days"] == 7  # type: ignore[index]
    assert silent.run.shelf_life is None
    assert (
        evaluation_view(
            ModelEvaluation(
                request=silent,
                prediction_days=(DAY,),
                excluded=(),
                folds=_evaluation(_request(minimum_scored_ratio=0.0, shelf_life_days=1)).folds,
            )
        )["declaration"]["shelf_life_days"]  # type: ignore[index]
        is None
    )


def test_the_face_refuses_a_negative_span_before_a_store_is_opened() -> None:
    """`bad_request` and not `blocked`: nothing about the panel could make this question askable.

    The same reading as `embargo_sessions < 0` two fields over, and for the reason
    `AlphaModelArtifact.is_stale_at` gives: a negative span expires every fit at an instant the
    leakage floor already refuses, so no cross section would ever be scored.
    """
    with pytest.raises(ModelRequestError, match="is negative"):
        _request(minimum_scored_ratio=0.0, shelf_life_days=-1)

    assert _request(minimum_scored_ratio=0.0, shelf_life_days=0).run.shelf_life == timedelta(0)


def test_the_two_boundaries_this_issue_named_are_registered_where_a_reader_meets_them() -> None:
    """The codes cited in `domain/alpha_model.py`'s docstrings, held against the registry.

    `tests/unit/test_known_limitation_registries.py` requires every declared code to appear in
    executable test code; this is that requirement met with an assertion that also says what the
    two codes are for.
    """
    from openalpha_cn.domain.alpha_model import KNOWN_ALPHA_MODEL_LIMITATIONS

    codes = {item.code for item in KNOWN_ALPHA_MODEL_LIMITATIONS}

    assert "a_shelf_life_is_wall_time_and_a_horizon_is_sessions" in codes
    assert "a_stale_record_carries_the_verdict_and_not_the_bar_it_failed" in codes
    assert len(codes) == len(KNOWN_ALPHA_MODEL_LIMITATIONS) == 13
