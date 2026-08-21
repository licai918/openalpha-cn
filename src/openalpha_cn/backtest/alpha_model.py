"""A reference `AlphaModel` (`V2-P4-011`): one feature, a learned centre, a learned sign.

`domain/alpha_model.py` declares the contract. This module is the proof that the contract can
be **satisfied and driven** -- fitted, addressed by value, asked about a cross section, made to
abstain, and rebuilt from its artifact alone -- and it is deliberately nothing more than that.

## What it is not

It is **not a baseline**. `SingleFeatureAlphaModel` reads one declared feature, centres it on
the training set's mean and scores a cross section by the signed distance from that centre. It
does no cross-sectional standardization, looks at no second feature, fits no coefficient, and
reports no evaluation. Story S29 and `V2-P4-014` own the linear/ranking baseline and
`V2-P4-015` the tree one; `KNOWN_ALPHA_MODEL_LIMITATIONS`'
`the_reference_implementation_is_not_a_baseline` says so where a reader will find it.

## Why it can live under `backtest/`, and why `V2-P4-015` cannot follow it here

Everything below is stdlib arithmetic over `domain/` contracts, so this module passes all four
`backtest/` `lint-imports` contracts, in three different ways. The whole-package one
(`backtest-no-numeric-stack-or-panel-plane`) covers it automatically -- there is no list to
join, which is the property the P3 technical acceptance added it for. The two per-module study
contracts have explicit source lists, and it **joined both on arrival**, which
`tests/unit/test_import_layering.py` is what makes mandatory. The fourth,
`ranking-creates-no-portfolio-order`, it is deliberately **not** in: that contract's two sources
are the candidate list and its gate, and a model that produces a number for every security in a
cross section is neither. That is exactly why it is a *reference*:
`V2-P4-015`'s LightGBM model needs `numpy`, which `backtest-studies-touch-no-store` forbids to
every module in this list, and `sklearn`/`scipy`/`pandas`, which the whole-package contract
forbids. So `V2-P4-015` must argue a home outside `backtest/`. Nothing here moves when it does:
`AlphaModel` and `FittedAlphaModel` are `Protocol`s and this module never subclasses them.

## Two properties this reference exists to demonstrate

- **`fit` returns a new object.** Two folds of one declaration produce two
  `FittedSingleFeatureAlphaModel`s with two different artifacts, so `V2-P4-013` can evaluate
  walk-forward and `V2-P4-016` can address the folds apart.
- **The artifact is the whole model.** `FittedSingleFeatureAlphaModel` carries no state beyond
  its `AlphaModelArtifact`: the centre and the sign are `artifact.parameters` and the feature is
  `declaration.hyperparameters`. Rebuilding one from a stored artifact reproduces every
  prediction, which is what makes `V2-P4-016`'s content address worth computing and
  `V2-P4-017`'s stored batch worth keeping.

## Not re-exported from `openalpha_cn.backtest`

`__init__.py`'s eight names are what a caller outside this package needs to drive the funnel,
and `ComponentCrossSection` is already left out of them on the argument that nothing outside
`shortlist_view` should be building one by hand. The same argument applies here with more force:
`V2-P4-033` measured that a missing re-export can make a shipped feature invisible, and this is
the converse case -- a re-export would put a deliberately inadequate model on the package's front
door, where the next reader would find it before finding `V2-P4-014`'s baseline. Importing it by
its full path is exactly the amount of deliberateness this should take.

`AlphaModelDeclaration.seed` is carried and unused: this arithmetic is deterministic, and
`runtime/seeding.py` -- the guarded `numpy.random` hook a stochastic fit would want -- is under
`runtime/`, which `backtest-studies-reach-no-composition-root` forbids to this module. The field
is on the declaration because Implementation Decision 11 requires the artifact to record one,
not because this model draws a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Final

from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    Prediction,
    PredictionBatch,
    TrainingSet,
    artifact_for,
    prediction_batch_for,
)

REFERENCE_FAMILY: Final[str] = "single_feature_reference"
"""The `AlphaModelDeclaration.family` this module answers to.

Declared rather than spelled at each site so that a caller building a declaration for this
model and this module's own refusal cannot disagree about the string.
"""

FEATURE_HYPERPARAMETER: Final[str] = "feature_id"
"""Which hyperparameter names the one feature this model reads.

It is a hyperparameter and not a constructor argument so that the fitted artifact carries it:
an artifact that recorded a centre and a sign without saying which column they belong to could
not reproduce a single prediction, which is the property this reference exists to demonstrate.
"""

CENTRE_PARAMETER: Final[str] = "centre"
SIGN_PARAMETER: Final[str] = "sign"

ABSTAIN_NO_VALUE: Final[str] = "the declared feature carries no value for this security"
"""Why this model declines to score a security.

Free text, because `V2-P4-018` owns the abstention vocabulary that Story S35 asks for. What is
load-bearing here is that it abstains at all rather than scoring the centre: a missing feature
scored at `0.0` is a name sitting in the middle of the cross section on the strength of having
no data, which is `OutcomeLabel.realized_return`'s objection on the label side.
"""

MINIMUM_USABLE_EXAMPLES: Final[int] = 2
"""How many labelled examples with a value this fit needs before it will learn a sign.

One example has no spread to split on, so the sign it produced would be the sign of a single
observation. Refused rather than defaulted to `+1`: a defaulted direction is a decision that
was never taken reporting as one that was, which is `MissingValuePolicy`'s rule.
"""


def _declared_feature(declaration: AlphaModelDeclaration) -> str:
    """The one feature this declaration reads, or `AlphaModelError`."""
    if declaration.family != REFERENCE_FAMILY:
        raise AlphaModelError(
            f"{declaration.name} declares family {declaration.family!r} and this reference "
            f"answers to {REFERENCE_FAMILY!r}; a declaration fitted by the wrong implementation "
            "produces an artifact whose family names a code path it never went through"
        )
    for key, value in declaration.hyperparameters:
        if key == FEATURE_HYPERPARAMETER:
            if not isinstance(value, str) or not value.strip():
                raise AlphaModelError(
                    f"{declaration.name} declares {FEATURE_HYPERPARAMETER}={value!r}, which is "
                    "not a feature id"
                )
            return value
    raise AlphaModelError(
        f"{declaration.name} declares no {FEATURE_HYPERPARAMETER!r} hyperparameter; this "
        "reference reads exactly one feature and cannot infer which"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SingleFeatureAlphaModel:
    """An unfitted reference model: a declaration naming one feature, and a `fit`.

    Satisfies `domain/alpha_model.py`'s `AlphaModel` structurally -- it subclasses nothing --
    which is the property that lets `V2-P4-015`'s numeric implementation satisfy the same
    protocol from a package `domain/` may not import from.
    """

    declaration: AlphaModelDeclaration

    def __post_init__(self) -> None:
        _declared_feature(self.declaration)

    @property
    def feature_id(self) -> str:
        """The one feature this model reads, from its declaration's hyperparameters."""
        return _declared_feature(self.declaration)

    def fit(self, training_set: TrainingSet) -> FittedSingleFeatureAlphaModel:
        """Learn a centre and a sign, and return a **new** fitted model.

        The centre is the mean of the declared feature over every example that carries a value.
        The sign is `+1` when the examples above the centre realized a higher mean target than
        those at or below it, and `-1` when they realized a lower one -- the crudest possible
        reading of "which direction of this feature was rewarded", and enough to make `fit`
        observably change what `predict` answers, which is the property being demonstrated.

        It is **also `+1` when one side of the split is empty**, which happens whenever the
        declared feature did not vary over the training window -- every value equals the mean,
        so nothing sits above it. That is a real market shape, not a degenerate one, and `+1` is
        the answer that adds nothing: a comparison with nothing on one side is not evidence of a
        direction, and flipping the sign on it would be inventing one. The alternative -- taking
        `fmean` of an empty list -- raises `StatisticsError` from inside `statistics`, which is
        the wrong module's error for "this feature was flat".

        Returns a new object rather than mutating `self`: a walk-forward evaluation fits one
        declaration once per fold, and folds sharing a mutable model would share one artifact.
        """
        feature_id = self.feature_id
        if feature_id not in training_set.feature_ids:
            raise AlphaModelError(
                f"{self.declaration.name} reads {feature_id!r} and its training set carries "
                f"{list(training_set.feature_ids)}; a fit over a feature the rows do not hold "
                "has nothing to learn from"
            )
        column = training_set.feature_ids.index(feature_id)
        observed = [
            (float(value), example.target)
            for example in training_set.examples
            for value in (example.features[column],)
            if value is not None
        ]
        if len(observed) < MINIMUM_USABLE_EXAMPLES:
            raise AlphaModelError(
                f"{self.declaration.name} reads {feature_id!r}, which carries a value in "
                f"{len(observed)} of {len(training_set.examples)} training example(s); "
                f"MINIMUM_USABLE_EXAMPLES is {MINIMUM_USABLE_EXAMPLES}, below which the learned "
                "sign is the sign of one observation"
            )
        centre = fmean(value for value, _target in observed)
        above = [target for value, target in observed if value > centre]
        below = [target for value, target in observed if value <= centre]
        sign = 1.0
        if above and below and fmean(above) < fmean(below):
            sign = -1.0
        return FittedSingleFeatureAlphaModel(
            artifact=artifact_for(
                declaration=self.declaration,
                training_set=training_set,
                parameters=((CENTRE_PARAMETER, centre), (SIGN_PARAMETER, sign)),
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FittedSingleFeatureAlphaModel:
    """A fitted reference model, whose entire state is its artifact.

    No field but `artifact`, deliberately. `V2-P4-016` content-addresses that artifact
    (`AlphaModelArtifact.artifact_id`) and `V2-P4-017` will store a batch beside it, and both are
    worth doing only if the artifact is sufficient to reproduce the model -- so here it is made
    sufficient by construction rather than by discipline.
    """

    artifact: AlphaModelArtifact

    def __post_init__(self) -> None:
        self._parameter(CENTRE_PARAMETER)
        self._parameter(SIGN_PARAMETER)
        _declared_feature(self.artifact.declaration)

    def _parameter(self, name: str) -> float:
        for key, value in self.artifact.parameters:
            if key == name:
                return value
        raise AlphaModelError(
            f"{self.artifact.declaration.name}'s artifact carries no {name!r} parameter; a "
            "fitted reference model is exactly its centre, its sign and its declared feature"
        )

    @property
    def feature_id(self) -> str:
        """The one feature this fitted model reads."""
        return _declared_feature(self.artifact.declaration)

    def predict(
        self, cross_section: FeatureCrossSection, *, predicted_at: datetime
    ) -> PredictionBatch:
        """Score every security by the signed distance of its feature from the learned centre.

        A security whose declared feature carries no value **abstains** rather than being
        scored at the centre, and `prediction_batch_for` is what makes the batch answer about
        every security the cross section offered -- so a name this model has nothing to say
        about is visible as a refusal instead of missing.

        `require_features` is **not** called here, and its absence is deliberate. It runs inside
        `prediction_batch_for`, which every path out of this method goes through, and a mutation
        sweep measured what the second copy was worth: deleting the one in `prediction_batch_for`
        left the whole suite green, because this call had already refused every mismatched cross
        section a test drove. Two copies of a check are one check plus a place for a future
        implementation to skip it, so the surviving copy is the one on the shared path. The
        column index below is read before the check runs; a mismatched list either raises
        `ValueError` from `.index` or produces numbers that never leave this method, because
        `prediction_batch_for` refuses before a batch exists.
        """
        centre = self._parameter(CENTRE_PARAMETER)
        sign = self._parameter(SIGN_PARAMETER)
        column = cross_section.feature_ids.index(self.feature_id)
        predictions = []
        for row in cross_section.rows:
            value = row.values[column]
            if value is None:
                predictions.append(Prediction(ts_code=row.ts_code, abstention=ABSTAIN_NO_VALUE))
            else:
                predictions.append(
                    Prediction(ts_code=row.ts_code, score=sign * (float(value) - centre))
                )
        return prediction_batch_for(
            artifact=self.artifact,
            cross_section=cross_section,
            predicted_at=predicted_at,
            predictions=predictions,
        )
