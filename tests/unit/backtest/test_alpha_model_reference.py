"""The reference `AlphaModel` (`V2-P4-011`): proof the contract can be satisfied and driven.

Four properties, and each is a claim `domain/alpha_model.py` makes that would otherwise be
unmeasured prose:

1. **`fit` changes what `predict` answers.** Reverse the training targets and the cross
   section's ordering reverses with them. Without this the contract could be satisfied by a
   model that ignored its training set entirely.
2. **`fit` returns a new object.** Two folds of one declaration produce two artifacts with two
   training cutoffs, which is what `V2-P4-013` needs and what `V2-P4-016` will address apart.
3. **The artifact is the whole model.** Serialize it, deserialize it, rebuild the fitted model
   from nothing else, and every prediction is identical.
4. **A security with no value abstains** rather than being scored at the learned centre, and the
   batch still answers about it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import alpha_model_fixtures as fixtures
import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.alpha_model import (
    ABSTAIN_NO_VALUE,
    CENTRE_PARAMETER,
    FEATURE_HYPERPARAMETER,
    MINIMUM_USABLE_EXAMPLES,
    REFERENCE_FAMILY,
    SIGN_PARAMETER,
    FittedSingleFeatureAlphaModel,
    SingleFeatureAlphaModel,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    FeatureRow,
    FittedAlphaModel,
)

AS_OF = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)


def _scores(batch: object) -> dict[str, float | None]:
    assert hasattr(batch, "predictions")
    return {row.ts_code: row.score for row in batch.predictions}


def test_the_reference_satisfies_both_protocols_without_subclassing_either() -> None:
    """Structural typing is the whole reason `V2-P4-015` can put numpy somewhere else."""
    model = SingleFeatureAlphaModel(declaration=fixtures.declaration())
    fitted = model.fit(fixtures.training_set())

    assert model.feature_id == fitted.feature_id == fixtures.MOMENTUM
    assert isinstance(model, AlphaModel)
    assert isinstance(fitted, FittedAlphaModel)
    assert not isinstance(model, FittedAlphaModel)
    assert SingleFeatureAlphaModel.__mro__[1:] == (object,)
    assert FittedSingleFeatureAlphaModel.__mro__[1:] == (object,)


def test_reversing_the_training_targets_reverses_the_predicted_ordering() -> None:
    """`fit` is load-bearing: the same cross section comes out ordered the other way.

    The features and the securities are identical between the two fits; only the realized
    returns differ. A model that ignored its training set -- or one whose learned sign never
    reached `predict` -- would produce the same ordering twice, which is the mutant this
    separates.
    """
    rising = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    falling = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set(reverse=True)
    )
    section = fixtures.cross_section(
        as_of=AS_OF,
        rows=(("000001.SZ", (0.05, 0.05)), ("000002.SZ", (0.45, 0.04))),
    )

    up = _scores(rising.predict(section, predicted_at=AS_OF))
    down = _scores(falling.predict(section, predicted_at=AS_OF))

    assert dict(rising.artifact.parameters)[SIGN_PARAMETER] == 1.0
    assert dict(falling.artifact.parameters)[SIGN_PARAMETER] == -1.0
    assert up["000002.SZ"] > up["000001.SZ"]
    assert down["000002.SZ"] < down["000001.SZ"]
    assert up["000001.SZ"] == pytest.approx(-down["000001.SZ"])


def test_the_learned_centre_is_the_training_mean_and_reaches_every_score() -> None:
    """The other half of "fit reaches predict": the centre, not only the sign.

    A security whose feature sits exactly at the learned centre scores `0.0`, and one a tenth
    above it scores `+0.1`. Both are arithmetic a caller can check by hand against
    `artifact.parameters`, which is what makes the artifact worth storing.
    """
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    centre = dict(fitted.artifact.parameters)[CENTRE_PARAMETER]

    assert centre == pytest.approx(0.21)
    section = fixtures.cross_section(
        as_of=AS_OF,
        rows=(("000001.SZ", (centre, 0.05)), ("000002.SZ", (centre + 0.1, 0.04))),
    )
    scores = _scores(fitted.predict(section, predicted_at=AS_OF))

    assert scores["000001.SZ"] == pytest.approx(0.0)
    assert scores["000002.SZ"] == pytest.approx(0.1)


def test_a_feature_that_did_not_vary_fits_to_a_plus_one_sign_rather_than_raising() -> None:
    """The branch a fixture with spread cannot reach: one side of the centre split is empty.

    Every training value equals the mean, so nothing sits strictly above it and the `above`
    list is empty. `fmean` of an empty list raises `StatisticsError` from inside `statistics`,
    which would be the wrong module answering for "this feature was flat", so the guard keeps
    the sign at `+1` -- the answer that adds nothing, because a comparison with nothing on one
    side is not evidence of a direction. Driven both ways: the fit succeeds and the resulting
    model still scores the distance from the flat centre.
    """
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set(flat_momentum=True)
    )
    parameters = dict(fitted.artifact.parameters)

    assert parameters[CENTRE_PARAMETER] == pytest.approx(0.20)
    assert parameters[SIGN_PARAMETER] == 1.0
    section = fixtures.cross_section(
        as_of=AS_OF, rows=(("000001.SZ", (0.30, 0.05)), ("000002.SZ", (0.10, 0.04)))
    )
    scores = _scores(fitted.predict(section, predicted_at=AS_OF))
    assert scores["000001.SZ"] == pytest.approx(0.10)
    assert scores["000002.SZ"] == pytest.approx(-0.10)


def test_two_folds_of_one_declaration_produce_two_artifacts() -> None:
    """`fit` returns a new object, which is what makes walk-forward addressable.

    If `fit` mutated and returned `self`, both folds would be one object with one artifact and
    `V2-P4-016` could not address them apart -- so `V2-P4-017` would store the second fold's
    predictions against the first fold's training cutoff.
    """
    model = SingleFeatureAlphaModel(declaration=fixtures.declaration())
    early = model.fit(fixtures.training_set(days=fixtures.FIRST_FOLD))
    late = model.fit(fixtures.training_set(days=fixtures.SECOND_FOLD))

    assert early is not late
    assert early.artifact != late.artifact
    assert early.artifact.training_cutoff < late.artifact.training_cutoff
    assert early.artifact.declaration == late.artifact.declaration


def test_a_fitted_model_rebuilt_from_its_serialized_artifact_predicts_identically() -> None:
    """The artifact is the whole model, and that is the premise `V2-P4-016`/`017` rest on.

    Nothing but `AlphaModelArtifact` crosses the round trip -- no centre held in a closure, no
    feature id passed to a constructor -- so a stored artifact is sufficient to reproduce every
    number, and a difference between the two batches would be state that lives only in memory.
    """
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    section = fixtures.cross_section(as_of=AS_OF)

    restored = FittedSingleFeatureAlphaModel(
        artifact=AlphaModelArtifact.model_validate_json(fitted.artifact.model_dump_json())
    )

    assert restored.artifact == fitted.artifact
    assert restored.predict(section, predicted_at=AS_OF) == fitted.predict(
        section, predicted_at=AS_OF
    )


def test_a_security_with_no_value_abstains_and_is_still_in_the_batch() -> None:
    """S35's shape, driven: the name is refused visibly rather than scored at the centre.

    Scoring a missing feature at the centre would put the name in the middle of the ordering on
    the strength of having no data, and dropping it would make the batch answer about two of the
    three securities the read offered. `prediction_batch_for` refuses the second; abstaining is
    how the model avoids the first.
    """
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    batch = fitted.predict(fixtures.cross_section(as_of=AS_OF), predicted_at=AS_OF)

    assert batch.subjects == ("000001.SZ", "000002.SZ", "000003.SZ")
    assert [row.ts_code for row in batch.abstained] == ["000003.SZ"]
    assert batch.abstained[0].abstention == ABSTAIN_NO_VALUE
    assert batch.abstained[0].score is None
    assert len(batch.scored) == 2


def test_the_driven_path_refuses_a_batch_dated_before_the_training_cutoff() -> None:
    """The leakage floor holds through `predict`, not only on a hand-built batch."""
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    cutoff = fitted.artifact.training_cutoff
    early = cutoff - timedelta(days=1)
    section = fixtures.cross_section(as_of=early, rows=(("000001.SZ", (0.3, 0.05)),))

    with pytest.raises(ValidationError, match="realized after the instant"):
        fitted.predict(section, predicted_at=early)


def test_predict_refuses_a_cross_section_whose_feature_list_is_not_the_fitted_one() -> None:
    """A narrower cross section shifts the columns and would score the wrong data."""
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    narrowed = FeatureCrossSection(
        as_of=AS_OF,
        feature_ids=(fixtures.MOMENTUM,),
        rows=(FeatureRow(ts_code="000001.SZ", values=(0.3,)),),
    )

    with pytest.raises(AlphaModelError, match=r"missing \['value_ep'\]"):
        fitted.predict(narrowed, predicted_at=AS_OF)


@pytest.mark.parametrize(
    ("family", "hyperparameters", "expected"),
    [
        ("linear", ((FEATURE_HYPERPARAMETER, fixtures.MOMENTUM),), "answers to"),
        (REFERENCE_FAMILY, (), "declares no 'feature_id'"),
        (REFERENCE_FAMILY, ((FEATURE_HYPERPARAMETER, 3),), "not a feature id"),
    ],
)
def test_the_reference_refuses_a_declaration_it_cannot_be_the_implementation_of(
    family: str, hyperparameters: tuple[tuple[str, object], ...], expected: str
) -> None:
    """`family` is checked because an artifact naming a code path it never went through is a lie.

    The feature id lives in `hyperparameters` and not in a constructor argument precisely so the
    artifact carries it; a declaration that omits it is one whose fitted artifact could not
    reproduce a prediction, which is why it is refused at construction rather than at `fit`.
    """
    declared = AlphaModelDeclaration(
        name="probe",
        family=family,
        horizon="1d",
        feature_version="features/v1",
        seed=0,
        code_commit="abcdef0",
        hyperparameters=hyperparameters,  # type: ignore[arg-type]
    )

    with pytest.raises(AlphaModelError, match=expected):
        SingleFeatureAlphaModel(declaration=declared)


def test_a_fit_refuses_a_training_set_that_does_not_carry_the_declared_feature() -> None:
    """Two ways the feature can be absent, and they are different refusals.

    A column that is not in `feature_ids` at all is a malformed question; a column that is
    present and empty is a real market shape -- an unbuilt feature over a short history -- and
    it is refused by count rather than by name, because a sign learned from one observation is
    the sign of one observation.

    Both `0` and `1` usable rows are driven, and the pair is the measurement: a floor written
    `< 1` instead of `< 2` still refuses zero, so only the one-row case separates them. Two
    usable rows fits, which is what keeps the floor from being read as "this never fits".
    """
    model = SingleFeatureAlphaModel(declaration=fixtures.declaration(feature_id="not_a_feature"))
    with pytest.raises(AlphaModelError, match="has nothing to learn from"):
        model.fit(fixtures.training_set())

    blanked = SingleFeatureAlphaModel(declaration=fixtures.declaration())
    for usable in (0, 1):
        with pytest.raises(
            AlphaModelError,
            match=rf"in {usable} of 6 training example\(s\); MINIMUM_USABLE_EXAMPLES is "
            rf"{MINIMUM_USABLE_EXAMPLES}",
        ):
            blanked.fit(fixtures.training_set(usable_momentum=usable))
    assert blanked.fit(fixtures.training_set(usable_momentum=2)).artifact.parameters


def test_a_fitted_model_refuses_an_artifact_that_carries_no_learned_parameter() -> None:
    """The fitted model is exactly its centre, its sign and its declared feature.

    Constructible directly -- `nothing_forces_an_implementation_through_the_builders` says so --
    so the one thing this class can check about a hand-built artifact, it checks at
    construction rather than at the first prediction.
    """
    fitted = SingleFeatureAlphaModel(declaration=fixtures.declaration()).fit(
        fixtures.training_set()
    )
    signless = fitted.artifact.model_copy(update={"parameters": ((CENTRE_PARAMETER, 0.2),)})

    with pytest.raises(AlphaModelError, match=f"no '{SIGN_PARAMETER}' parameter"):
        FittedSingleFeatureAlphaModel(artifact=signless)
