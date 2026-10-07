"""The baseline D13 asks to be beaten, and the numbers that say whether it was (`V2-P4-014`).

The row is `线性/排序基线` and Implementation Decision 13 is what it is short for: *"先交付可理解
基线。线性/排序与树基线建立最低比较。更复杂模型仅在预定义样本外、扣成本标准上改进…才被接受。"*
Story S29 names the same pair. `V2-P4-013` handed this issue two things by name -- **the baseline
itself, and all metrics** -- and its own module says plainly that it fits nothing and scores
nothing.

## One model, and the slash in the row is where it resolves

`线性/排序` reads as two models and is delivered as one: a **linear combination of
cross-sectional ranks**. Three reasons, and the first two are about what this repository can
check rather than about taste.

- **The score's only consumer is an order.** `PredictionBatch` puts no units on `score`, and
  `backtest/candidate_ranking.py::rank_candidates` orders by it. A level-space linear model's one
  advantage over a rank one is that its output is a *forecast return* -- and the report that
  would check that claim is Story **S31**, which the PRD defers to v2.1. An unchecked units claim
  is worse than no claim, so this model makes none: `KNOWN_BASELINE_LIMITATIONS`'
  `a_rank_baseline_forecasts_no_return_and_its_score_carries_no_units` says so where a reader
  meets it.
- **A level-space fit depends on a policy this model cannot see.** `V2-P4-012` put winsorization,
  standardization and missing-value handling inside `FeatureSpec.feature_version`, and a model
  gets the *result*, never the recipe. A level-space coefficient means a different thing under
  `cross_section_standard` than under a raw tier; a rank is invariant to every monotone transform
  in that policy, so this baseline answers the same way whichever one the matrix declared. D13
  wants a comparison **floor**, and a floor that moves with the preprocessing is not one.
- **The clip block, measured.** `V2-P4-004` measured this market's shipped winsorization putting
  **56** of 5,540 names on one identical `turnover_rate` value (55 on `pb`, 41 on `pe_ttm`). In
  level space those 56 carry one artificial number chosen by the winsorizer and identical
  leverage on the fit. `factor_ic.average_ranks` gives them one shared average rank, which is the
  honest sentence: *these 56 are tied and this feature does not separate them.* The other columns
  still do, and
  `test_a_block_tied_on_one_column_shares_one_position_there_and_is_separated_by_the_others` is
  the drive.

**What the linear model would have needed**, stated rather than left implied: a declared target
scale and S31's calibration report to check it; a conditioning story for a joint solve that this
repository has no library for (below); and a preprocessing policy visible to the model rather
than sealed inside a `feature_version`. All three are somewhere else, and two of them are v2.1.

## Where the line falls without a numeric stack, and it is not where "expressible" falls

ADR-0003 ships no numerical stack and the runtime dependencies are exactly nine.
`backtest-no-numeric-stack-or-panel-plane` forbids `pandas`, `scipy` and `sklearn` to this whole
package and `backtest-studies-touch-no-store` forbids `numpy` to every module in its list, this
one included. So the question `V2-P4-011` left open -- whether a **real** fit is expressible here
-- gets an answer, and the answer has two halves.

**Expressible, and expressed:** cross-sectional average ranks with the tie rule this repository
already pinned element-wise (`factor_ic.average_ranks`); a product-moment correlation whose two
measured floating-point defects are already fixed (`factor_ic._pearson`, which
`backtest/factor_redundancy.py` imports for exactly this reason -- "there is no such boundary
between two modules of `backtest/`, so a second copy here would be unforced duplication");
`statistics.fmean` over the days; a bounded weighted sum. All of it is `O(n log n)` per cross
section, which is what ADR-0003's rank-IC entry already budgeted.

**Expressible and declined:** a **joint** least-squares fit. Gaussian elimination on a `p x p`
Gram matrix is thirty lines of stdlib and would run; what stdlib does not offer is the thing that
makes the answer trustworthy -- a QR or an SVD, or an honest condition number. And this
repository's own columns are the adversarial case: `backtest/factor_redundancy.py` exists because
its factors are correlated, and `V2-P4-012`'s grammar stores one factor's `raw`, `processed` and
`neutralized` tiers as three columns of one matrix, which are near-duplicates by construction.
`V2-P4-013`'s own corpus is the extreme: its two columns are **exactly** rank-anticorrelated, so
the normal equations there are singular and a joint fit has no answer at all, while the marginal
one answers `+1` and `-1`
(`test_a_joint_solve_is_singular_on_the_pair_the_marginal_fit_answers_about` measures the
singularity rather than asserting it).

So the coefficients here are **marginal**: each feature's own mean training rank IC, a number
bounded in `[-1, 1]` by construction, that cannot blow up and that a reader can recompute with
`factor_ic` alone. The line is therefore not "expressible / inexpressible" -- it is *"an answer
whose failure mode is loud"* against *"an answer whose failure mode is a large coefficient nobody
can tell from a signal"*. What marginality costs is stated as
`the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice`, and it is also
precisely what `V2-P4-015`'s tree model exists to add -- which is why D13 pairs the two rather
than asking for one.

## The population rule, which decides three things at once

A rank is a position **within a set**, and `factor_redundancy._correlate` measured what happens
when that is forgotten: restricting a 40-name rank vector to a 25-name intersection disagreed
with the honest answer on 200 of 200 random trials, by as much as 0.100. So the population is
fixed once, before anything is ranked, and it is the same rule on both sides of the fit:

> the securities that carry a value for **every** declared feature.

That one rule decides who is ranked, who is scored, and who abstains. A row missing one column is
not scored on the others, because a weighted sum missing a term is a different statistic -- which
is `require_features`' positional argument one layer down, and `rank_candidates`' "a list ordered
on two different statistics" one layer up.

## Abstention is an answer, not an error path

`V2-P4-011` requires a row for every security offered, scored or abstained. Two reasons, and both
are constants rather than interpolated sentences so that one code binds one condition without
anybody parsing a number back out of prose. **Both now live in `domain/alpha_model.py` and are
re-exported here**, which is `V2-P4-018` taking the vocabulary this module deferred to it: that
issue's third reason has to be produced inside `prediction_batch_for`, and `domain-purity`
forbids `domain/` from importing anything under `backtest`, so a vocabulary split across the two
layers would have been two sets neither of which was closed. Nothing about the two sentences
changed, and `alpha_tree.py` still reads them from here -- which is what makes the import a
**re-export**, spelled with a redundant `as` alias because strict mypy's `no_implicit_reexport`
requires an explicit one. That reason is measured rather than assumed, and twice: the re-export
was first written as an `__all__` on the stated ground that `ruff` would otherwise report the
names as unused, which is **false** -- both are used by `predict` below, so the import was never
unused -- and removing the `__all__` on that finding turned `uv run mypy` red with "does not
explicitly export attribute". The alias satisfies the real constraint without a second list of
names to keep in step with this file. The two sentences:

- `ABSTAIN_INCOMPLETE_FEATURES` -- this security carries no value for at least one declared
  column, so it is outside the population above.
- `ABSTAIN_UNRANKABLE_CROSS_SECTION` -- fewer than `MINIMUM_RANK_SECURITIES` securities carry
  every column at this `as_of`, so **nobody** is scored. `factor_ic.MINIMUM_IC_SECURITIES`' own
  argument is why the floor is three and not two: two points that tie on neither axis lie on one
  line, so a rank position among two names carries no information whatever the two did.

`V2-P4-018` filled the vocabulary in: `ABSTENTION_VOCABULARY` names all three conditions -- these
two and its own `ABSTAIN_STALE_MODEL` -- and `abstention_code` reads one back. **A third reason
does not reach this module's numbers by a third path.** An expired fit abstains on *every* row,
so it lands in `scored_ratio` exactly as an unrankable cross section does, and the statistics
below need no case for it. That is deliberate and it is the answer to "an abstention is free
skill": see `evaluate_fold`.

## The metrics, and why each one is here rather than three others

Every number below is per **fold**, because a fold is the unit `V2-P4-013` produces and the unit
D13's "beat the baseline" compares. Each answers a question the others cannot:

- **`rank_ic` per test day** -- the Spearman correlation of the day's scores against the day's
  realized targets. It is a correlation of *orders*, which is the only thing a score claims, and
  it is the one statistic that degrades gracefully on tied scores instead of inventing a
  separation: `average_ranks` gives a tied block one shared rank, while a metric built on sort
  position would order the 56 clipped names by whatever the sort returned.
  `test_the_rank_correlation_does_not_order_a_tied_block_the_way_sort_position_would` measures
  the two apart on a corpus that has such a block.
- **`mean_rank_ic`** -- the fold's headline, and the number a challenger has to beat.
- **`stdev_rank_ic` and `rank_icir`** -- a mean over a handful of days is not a claim. The ratio
  is what says whether the mean is larger than its own dispersion, and it is `None` rather than
  `math.inf` when there is none, which is `ICSummary.icir`'s decision taken rather than retaken.
- **`measured_count` against `len(points)`** -- a mean over two of twenty days and a mean over
  twenty are different claims that a single float cannot tell apart. `ICSummary` carries the same
  pair for the same reason.
- **`scored_ratio`** -- and this one is not decoration. Abstention is free skill: a model that
  declines every name it finds hard reports a better `mean_rank_ic` over an easier population. So
  two models' headlines are comparable only beside the fraction of the offered market each
  actually answered about, and this is the **one** field that is never `None` -- a cross section
  cannot be empty, so a fold always has a denominator.

Deliberately absent: a quantile long-short spread, which is `backtest/factor_portfolio.py`'s and
would need this module to grow a cost model; and a hit rate, which on a tied cross section is a
count of coin flips wearing a percentage. `ICCoverage` and `ICStabilityCoverage` are **imported**
rather than re-spelled: the four ways a cross section produces no correlation are the same four
whether the numbers came from a factor or from a fit, and a fifth spelling of "no answer" is what
that Literal's own docstring says it is closed against.

## What this issue expected of those metrics and measured wrong

`V2-P4-013` separated a leaked fold from a purged one with its reference model's single learned
bit -- purge alone scored `1.0` on the adjacent corpus, purge plus a two-session embargo scored
`0.0` -- and this module was written expecting a `mean_rank_ic` to separate the same pair. **It
does not.** Both folds read exactly `-1.0`, and the reason is a difference between the two
instruments rather than a defect in either:

- that reference **pools** its examples and compares two means, so the fixture's twenty-to-one
  coefficient ratio lets two leaked labels outweigh four honest ones and flip a bit;
- a rank correlation is invariant to magnitude, so each leaked day contributes `+1`, each honest
  day `-1`, and the fit averages them. Two of six leaked comes out at `-1/3` against `-1`.

So the leak is **visible and not invisible**: it is a threefold collapse in the coefficient, which
`FoldEvaluation.artifact` carries by value for exactly this kind of comparison. What it does not
reach on this corpus is the *ordering*, because the corpus's two columns are exactly
rank-anticorrelated and both coefficients therefore rescale together.
`test_the_embargo_moves_this_baselines_coefficient_and_not_its_ordering` states the whole of it
where a reader meets it, and
`a_minority_leak_moves_this_baselines_coefficient_and_not_the_order_it_produces` is the registry
entry. The **total** leak -- fitting on the test block itself -- is refused rather than reported,
by `V2-P4-011`'s floor and not by anything here, which is `V2-P4-013`'s own finding arriving at
this module unchanged.

## Determinism, and the fixture that can tell

`AlphaModelArtifact` carries a seed, and this fit does not draw a number -- the reference under
`backtest/alpha_model.py` made the same statement for the same reason, and `runtime/seeding.py`
is in a package `backtest-studies-reach-no-composition-root` forbids to this module anyway. What
is *not* free is order independence. `_pearson` sums products with a plain `sum`, so a fit that
ranked a day's rows in whatever order they arrived would answer a permuted training set with a
different last bit. Measured over 400 random cross sections per size, a permutation changed the
answer 0/400 times at three names, 190/400 at six and 347/400 at sixty -- so a three-name fixture
cannot tell a sorted fit from an unsorted one, which is why the corpus in
`tests/unit/backtest/test_alpha_baseline.py` has eight and asserts its own sensitivity before
asserting the property.

## Where this module lives, and the pointer that had to be corrected

Under `backtest/`, beside `walk_forward.py` and for its stated reason: everything here is stdlib
arithmetic over `domain/` contracts, so the panel-plane adapter reads the store and hands a study
a domain contract, and the study imports neither the adapter nor the store. It is the eighteenth
file under `backtest/` and joined both per-module study contracts on arrival, which
`tests/unit/test_import_layering.py` makes mandatory -- a probe confirms the layering gate goes
red without them. It is deliberately **not** in
`ranking-creates-no-portfolio-order`, for the reason `backtest/alpha_model.py` is not: that
contract's sources are the candidate list and its gate, and a model that answers about every
security in a cross section is neither.

`feature_matrix.py` says twice that this issue *"is the first caller `require_declared_features`
exists for"*. That is not achievable and is corrected rather than quietly dropped:
`backtest-no-numeric-stack-or-panel-plane` lists `openalpha_cn.feature_matrix` among the modules
forbidden to this whole package, so no `backtest/` study can call it. The check belongs where a
declaration and a matrix are first held together, which is a composition above both planes and
not a study on one of them. **`V2-P4-021`'s faces arrived there first**, through
`model_view._model_request`, which resolves the declaration and the recipe in one function and
runs the check between them.
`nothing_here_checks_that_the_declared_feature_version_is_the_matrix_it_was_fitted_on` is the
entry, and it remains true *of this module*: a fit driven from anywhere else still records a
`feature_version` nothing verified.

## Not re-exported from `openalpha_cn.backtest`

`V2-P4-021`'s faces drive a fit from outside this package and still need no re-export: they
import `CrossSectionalRankModel` and `evaluate_walk_forward` by name, off the module, which is
what a face is allowed to do. `backtest/alpha_model.py` declined a re-export for a different
reason -- it is deliberately inadequate -- and this one declines for the ordinary one: a
`__init__` name would be a second spelling nobody asked for.

## What is deliberately left to a named issue

- **`V2-P4-015`** owns the tree baseline, and is the first model that has to beat this one. It
  also inherits every metric here: `evaluate_fold` takes the `AlphaModel` **Protocol**, so a
  LightGBM model living wherever it argues its home into can be measured by this module without
  either importing the other.
- **`V2-P4-016` landed and this module did not move**, which is what "names the artifact by
  value" bought: `FoldEvaluation.artifact` simply gained a readable `artifact_id`. That issue
  also answered where Implementation Decision 11's *metrics* field goes, and the answer is
  **here**, not on the artifact: a metric is a judgement of a fit taken on rows it never trained
  on, so putting one inside the fit's address would make a model's identity depend on how it was
  later judged -- and since `FoldEvaluation` carries the artifact by value, the artifact would
  end up containing the numbers that contain it. `V2-P3-014`'s split, reused.
- **`V2-P4-017`** owns persistence, and with it the one thing an evaluation's `predicted_at`
  cannot mean. `evaluate_fold` dates every batch at the section's own `as_of`, because a
  simulated prediction is made at the instant it simulates and a wall clock would make every
  evaluation unreproducible. That is not evidence that anything was predicted before an outcome
  was known, which is Story S32's actual requirement.
- **`V2-P4-018`** owns the abstention vocabulary. The two constants here are free text on
  purpose.
- **`V2-P4-021` landed.** `openalpha model evaluate` drives `evaluate_walk_forward` over this
  model, and `openalpha model daily-run` fits it and registers what it predicted. What that issue
  did **not** take is the abstention vocabulary below or the corpus after it.
- **`V2-P4-022`** owns the corpus with a known signal-to-noise ratio and a known-null control.
  Every number this module produces in a test is measured on `V2-P4-013`'s leak fixture, which
  has no noise model and exists to make a direction flip. Nothing here is a claim about alpha.
- **D13's threshold itself.** This module computes numbers and compares nothing; *"新模型必须战胜
  基线"* is an acceptance gate over a pre-defined out-of-sample, net-of-cost criterion, which is
  Decision 20's report contents and `backtest/validation.py`'s plane.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.backtest.factor_ic import (
    MINIMUM_IC_AS_OFS,
    MINIMUM_IC_SECURITIES,
    ICCoverage,
    ICStabilityCoverage,
    _pearson,
    average_ranks,
)
from openalpha_cn.backtest.walk_forward import PanelSection, WalkForwardFold
from openalpha_cn.domain.alpha_model import (
    ABSTAIN_INCOMPLETE_FEATURES as ABSTAIN_INCOMPLETE_FEATURES,  # re-export, see the docstring
)
from openalpha_cn.domain.alpha_model import (
    ABSTAIN_UNRANKABLE_CROSS_SECTION as ABSTAIN_UNRANKABLE_CROSS_SECTION,  # re-export
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    FittedAlphaModel,
    Prediction,
    PredictionBatch,
    TrainingExample,
    TrainingSet,
    artifact_for,
    prediction_batch_for,
)

BASELINE_FAMILY: Final[str] = "cross_sectional_rank"
"""The `AlphaModelDeclaration.family` this baseline answers to.

Declared rather than spelled at each site, `REFERENCE_FAMILY`'s reason: a caller building a
declaration and this module's own refusal cannot disagree about the string. `family` is what
tells a reader which code path an artifact went through, and `code_commit` cannot -- one commit
carries every family.
"""

MINIMUM_RANK_SECURITIES: Final[int] = MINIMUM_IC_SECURITIES
"""How many securities must carry every declared column before any of them is scored.

`factor_ic.MINIMUM_IC_SECURITIES` rather than a three written here, and the alias is the point:
the argument is already written once and it is arithmetic rather than taste. Two points that tie
on neither axis lie on one line, so a position among two names carries no information whatever
the two securities did; three is the first size at which a rank vector has an interior.

**Not declarable.** `FactorICSpec.min_securities` is a field with no default because a study's
sample floor is the caller's choice. A *baseline* is the opposite thing: D13 wants a fixed
comparison floor, and a floor a challenger can move is not one.
"""

MINIMUM_FOLD_DAYS: Final[int] = MINIMUM_IC_AS_OFS
"""How many measured test days a fold needs before it reports stability statistics.

`factor_ic.MINIMUM_IC_AS_OFS` for its own stated reason: a sample standard deviation of one
number does not exist, so a ratio over a single day is undefined rather than merely weak.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class BaselineLimitation:
    """One named boundary on what this baseline and its numbers can be trusted to say."""

    code: str
    detail: str


def rankable(
    rows: Iterable[tuple[str, Sequence[float | None]]],
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """The population: rows carrying a value in **every** column, in ascending security order.

    One function and two callers, because the fit and the prediction have to draw the population
    the same way or the coefficients would be measured over one set and applied to another. It
    takes `(security, values)` pairs rather than either contract's row type for exactly that
    reason -- a `TrainingExample`'s values are already the declared columns, while a
    `FeatureRow`'s are the *offered* ones and have to be selected first.

    Sorted, and the sort is no longer what carries the property it was written to carry. It read:
    "`_pearson` sums products in the argument's own order, so an unsorted population makes an
    answer a function of how the panel happened to hand its rows over" -- measured over 400
    random cross sections per size, a permutation changed the correlation 0/400 times at three
    names, 190/400 at six and 347/400 at sixty. `V2-P5-062` moved `_pearson` to `math.fsum`,
    which is exactly rounded, and the same measurement is now **0/400 at every size on both
    supported interpreters**. A permuted population produces the same artifact because the
    arithmetic no longer has an order to depend on, not because a caller sorted.

    The sort stays, and not out of caution about that: this function exists so that the fit and
    the prediction draw the *same* population, and a shared, stated order is the cheapest way for
    two callers to be checked against each other. Whether anything else downstream is
    order-sensitive has not been measured, and removing the sort is that question rather than
    this one. `tests/unit/backtest/test_factor_ic.py::
    test_the_correlation_does_not_depend_on_the_order_the_cross_section_arrives_in` holds the
    arithmetic half, and
    `tests/unit/backtest/test_alpha_baseline.py::
    test_reordering_this_corpus_does_not_move_a_single_bit_of_its_ics` the composition.

    A repeated security would make the sort's tie-break load-bearing; neither caller can hand one
    over, because `TrainingSet` refuses one security twice on one prediction day and
    `FeatureCrossSection` refuses one twice at one `as_of`.
    """
    complete: list[tuple[str, tuple[float, ...]]] = []
    for ts_code, values in rows:
        picked = tuple(value for value in values if value is not None)
        if len(picked) == len(values):
            complete.append((ts_code, picked))
    return tuple(sorted(complete))


def _rank_positions(values: Sequence[float]) -> tuple[float, ...]:
    """Average ranks mapped onto `[-1, 1]`, with a mean of exactly zero.

    `(rank - (m + 1) / 2) / ((m - 1) / 2)`. Average ranks sum to `m (m + 1) / 2` whatever the ties
    are, so the mean is exactly zero on every cross section rather than approximately zero on the
    untied ones -- which is what makes two features' contributions to one weighted sum comparable
    without a second standardization this module would then have to version.

    A cross section on which every value ties comes out all zeros, which is the correct
    contribution of a column that separates nobody: it is not a refusal, because the *other*
    columns still order the market. `m >= MINIMUM_RANK_SECURITIES` is the caller's to have
    ensured, and is what keeps the denominator away from zero.
    """
    count = len(values)
    middle = (count + 1) / 2.0
    half = (count - 1) / 2.0
    return tuple((rank - middle) / half for rank in average_ranks(values))


def _ties(values: Sequence[float]) -> bool:
    """Whether a vector has nothing to order -- `factor_ic._degenerate_side`'s own test."""
    return min(values) == max(values)


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossSectionalRankModel:
    """An unfitted baseline: a declaration, and a fit that reads every column the data carries.

    Satisfies `domain/alpha_model.py`'s `AlphaModel` structurally -- it subclasses nothing --
    which is the property that lets `V2-P4-015` satisfy the same protocol from wherever it argues
    its home into.

    It declares no hyperparameter, and the absence is the contrast with the reference: that model
    needs `feature_id` because it reads exactly one column and cannot infer which, while this one
    reads the whole of `TrainingSet.feature_ids` and therefore has nothing left to be told.
    `AlphaModelDeclaration.seed` is carried and unused, `SingleFeatureAlphaModel`'s statement for
    the same reason -- this arithmetic draws no number.
    """

    declaration: AlphaModelDeclaration

    def __post_init__(self) -> None:
        if self.declaration.family != BASELINE_FAMILY:
            raise AlphaModelError(
                f"{self.declaration.name} declares family {self.declaration.family!r} and this "
                f"baseline answers to {BASELINE_FAMILY!r}; a declaration fitted by the wrong "
                "implementation produces an artifact whose family names a code path it never "
                "went through"
            )

    def fit(self, training_set: TrainingSet) -> FittedCrossSectionalRankModel:
        """Learn each column's mean training rank IC, and return a **new** fitted model.

        One prediction day at a time, because a rank is a position within one cross section and
        pooling two days' values would rank a Monday against a Tuesday. Within a day the
        population is the rows carrying every column (`rankable`); a day contributes to no
        column when it has fewer than `MINIMUM_RANK_SECURITIES` of them or when every target
        ties, and to no *particular* column when that column ties. The order the three are decided
        in is `FactorICStudy.measure`'s: sample size first, because the other two are questions
        about a cross section and there is no cross section here worth asking them of.

        The **days** are iterated in whatever order they arrive and only the rows inside a day are
        sorted, which is one sort where a first draft had two. A mutation sweep measured that a
        `sorted(by_day)` here could be deleted with everything green, and it is deleted rather
        than asserted because the difference between the two sorts is a real one worth being able
        to see: `_pearson` adds its products with a plain `sum`, so a day's row order changes its
        last bit, while `statistics.fmean` is `math.fsum` underneath and is exactly rounded, so
        the order the days are averaged in cannot change anything at all. A second sort would have
        been a rule with no rule behind it, sitting next to one that has.

        A column no day could measure is **refused** rather than given a zero coefficient. A zero
        that was measured and a zero that was never measurable are the same float and different
        facts, and reporting the second as the first is a decision that was never taken reporting
        as one that was -- `MissingValuePolicy`'s rule, which `MINIMUM_USABLE_EXAMPLES` cites one
        module over.

        Returns a new object rather than mutating `self`, `AlphaModel`'s requirement: a
        walk-forward fits one declaration once per fold, and folds sharing a mutable model would
        share one artifact.
        """
        by_day: dict[date, list[TrainingExample]] = {}
        for example in training_set.examples:
            by_day.setdefault(example.label.window.prediction_day, []).append(example)

        observed: dict[str, list[float]] = {
            feature_id: [] for feature_id in training_set.feature_ids
        }
        for day in by_day:
            targets_by_code = {example.ts_code: example.target for example in by_day[day]}
            rows = rankable((example.ts_code, example.features) for example in by_day[day])
            if len(rows) < MINIMUM_RANK_SECURITIES:
                continue
            targets = [targets_by_code[ts_code] for ts_code, _values in rows]
            if _ties(targets):
                continue
            target_ranks = average_ranks(targets)
            for column, feature_id in enumerate(training_set.feature_ids):
                values = [values_of[column] for _ts_code, values_of in rows]
                if _ties(values):
                    continue
                observed[feature_id].append(_pearson(average_ranks(values), target_ranks))

        unmeasured = sorted(feature_id for feature_id, points in observed.items() if not points)
        if unmeasured:
            raise AlphaModelError(
                f"{self.declaration.name} was fitted on {len(by_day)} prediction day(s) and "
                f"{unmeasured} carried no measurable rank correlation on any of them -- too few "
                "securities held every column, or the column or the targets tied. A coefficient "
                "of zero here would report a measurement nobody took"
            )
        return FittedCrossSectionalRankModel(
            artifact=artifact_for(
                declaration=self.declaration,
                training_set=training_set,
                parameters=tuple(
                    (feature_id, statistics.fmean(observed[feature_id]))
                    for feature_id in training_set.feature_ids
                ),
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FittedCrossSectionalRankModel:
    """A fitted baseline, whose entire state is its artifact.

    No field but `artifact`, `FittedSingleFeatureAlphaModel`'s decision and for its reason:
    `V2-P4-016` content-addresses that artifact and `V2-P4-017` will store a batch beside it,
    and both are worth doing only if the artifact is sufficient to reproduce the model. Here it
    is sufficient by construction -- the coefficients *are* `artifact.parameters`, keyed by the
    column each belongs to, and `__post_init__` refuses an artifact whose parameter keys are not
    its `feature_ids`.

    There is deliberately no `coefficients` property. A first draft had one and nothing read it:
    `dict(artifact.parameters)` is what a caller comparing two folds already writes, because a
    `FoldEvaluation` carries the artifact and never the model. A second spelling of a public tuple
    is surface with no reader and a place for the two to disagree.
    """

    artifact: AlphaModelArtifact

    def __post_init__(self) -> None:
        if self.artifact.declaration.family != BASELINE_FAMILY:
            raise AlphaModelError(
                f"{self.artifact.declaration.name}'s artifact declares family "
                f"{self.artifact.declaration.family!r} and this baseline answers to "
                f"{BASELINE_FAMILY!r}"
            )
        keys = tuple(key for key, _value in self.artifact.parameters)
        if keys != self.artifact.feature_ids:
            raise AlphaModelError(
                f"{self.artifact.declaration.name}'s artifact was fitted on "
                f"{list(self.artifact.feature_ids)} and carries coefficients for {list(keys)}; a "
                "fitted rank baseline is exactly one coefficient per column, so a missing key is "
                "a column silently scored at nothing and an extra one is a column that was never "
                "in the fit"
            )

    def predict(
        self,
        cross_section: FeatureCrossSection,
        *,
        predicted_at: datetime,
        shelf_life: timedelta | None,
    ) -> PredictionBatch:
        """Score every security by the coefficient-weighted sum of its cross-sectional positions.

        A security outside the population -- one missing any declared column -- abstains, and so
        does every security when the population is smaller than a rank can be taken over. The
        score is `sum(coefficient * position)` over the fitted columns; both factors are bounded
        by one, so the result is bounded by the number of columns and `Prediction`'s finiteness
        refusal is unreachable from here rather than merely unmet.

        `require_features` is **not** called here, `FittedSingleFeatureAlphaModel`'s decision:
        it runs inside `prediction_batch_for`, which every path out of this method goes through,
        and `V2-P4-011` deleted the second copy after a mutation sweep measured it was worth
        nothing. The column indices below are read the same way that model reads its one --
        through `.index` on the offered list -- so a cross section missing a fitted column raises
        `ValueError` from there, and one carrying an extra column produces numbers that never
        leave this method because `prediction_batch_for` refuses before a batch exists.
        """
        columns = tuple(
            cross_section.feature_ids.index(feature_id) for feature_id in self.artifact.feature_ids
        )
        population = rankable(
            (row.ts_code, [row.values[column] for column in columns]) for row in cross_section.rows
        )
        if len(population) < MINIMUM_RANK_SECURITIES:
            return prediction_batch_for(
                artifact=self.artifact,
                cross_section=cross_section,
                predicted_at=predicted_at,
                shelf_life=shelf_life,
                predictions=(
                    Prediction(ts_code=row.ts_code, abstention=ABSTAIN_UNRANKABLE_CROSS_SECTION)
                    for row in cross_section.rows
                ),
            )

        totals = {ts_code: 0.0 for ts_code, _values in population}
        for column, (_feature_id, coefficient) in enumerate(self.artifact.parameters):
            positions = _rank_positions([values[column] for _ts_code, values in population])
            for (ts_code, _values), position in zip(population, positions, strict=True):
                totals[ts_code] += coefficient * position

        return prediction_batch_for(
            artifact=self.artifact,
            cross_section=cross_section,
            predicted_at=predicted_at,
            shelf_life=shelf_life,
            predictions=(
                Prediction(ts_code=row.ts_code, score=totals[row.ts_code])
                if row.ts_code in totals
                else Prediction(ts_code=row.ts_code, abstention=ABSTAIN_INCOMPLETE_FEATURES)
                for row in cross_section.rows
            ),
        )


class BaselineScorePoint(BaseModel):
    """One test day's reading: how much of the market was answered about, and how well.

    A pydantic model rather than a dataclass, `ICPoint`'s reason: this is a reportable number
    that `V2-P4-016` may address and `V2-P4-017` may store, so it is validated when it is read
    back rather than only when it is built.

    `offered_count`, `scored_count` and `paired_count` narrow in that order and each drop means
    something different -- the market, the model's abstentions, and the labels the panel could
    build. Carrying all three is what lets a reader tell a model that declined names from a
    market whose names had no outcome.

    `predicted_at` is carried beside `as_of` for `PredictionBatch`'s reason: a reader can tell
    when a number was produced from what it was produced about. On this plane it is also the only
    place `evaluate_fold`'s choice of instant is **observable** -- a mutation sweep measured that
    without this field the whole of "an evaluation is dated at the instant it simulates" was a
    sentence nothing could contradict.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    as_of: datetime
    predicted_at: datetime
    prediction_day: date
    offered_count: int = Field(ge=1)
    scored_count: int = Field(ge=0)
    paired_count: int = Field(ge=0)
    coverage: ICCoverage
    rank_ic: float | None

    @model_validator(mode="after")
    def validate_the_counts_narrow_and_the_number_matches_the_coverage(self) -> Self:
        if self.predicted_at < self.as_of:
            raise ValueError(
                f"this point was produced at {self.predicted_at.isoformat()}, before the "
                f"{self.as_of.isoformat()} cross section it reads; PredictionBatch refuses the "
                "same shape and a reading of one cannot be looser than the batch it read"
            )
        if self.scored_count > self.offered_count:
            raise ValueError(
                f"{self.scored_count} securities were scored out of {self.offered_count} "
                "offered; a batch answers about the cross section it was given and no more"
            )
        if self.paired_count > self.scored_count:
            raise ValueError(
                f"{self.paired_count} scored securities carried a label out of "
                f"{self.scored_count} scored; a pair needs both halves"
            )
        if (self.rank_ic is None) == (self.coverage == "measured"):
            raise ValueError(
                f"coverage {self.coverage!r} carries rank_ic {self.rank_ic!r}; exactly the "
                "'measured' code carries a number, and every other code is a stated reason there "
                "is none"
            )
        if self.rank_ic is not None and not -1.0 <= self.rank_ic <= 1.0:
            raise ValueError(
                f"rank_ic {self.rank_ic!r} is outside [-1, 1]; a correlation of more than one is "
                "a number no reader can interpret"
            )
        return self


class FoldEvaluation(BaseModel):
    """One fold's whole reading: the artifact that produced it, and the numbers it produced.

    The artifact is carried **by value**, `PredictionBatch`'s decision: the fit that produced
    these numbers is recoverable without a lookup and without an address at all. `V2-P4-016`
    defined one and this field did not have to move for it, which was that issue's whole claim to
    being an addition: the artifact carried by value simply gained an `artifact_id` a reader can
    read off it. Two folds of one declaration still compare field by field, which is what makes a
    walk-forward schedule a series rather than a list of unrelated floats -- and `first_test_day`
    beside the artifact is where Implementation Decision 11's *split policy* ended up, because a
    test block is not an input to the fit and does not belong in the fit's address.

    `scored_ratio` is the only statistic that is never `None`. A cross section cannot be empty, so
    a fold always has a denominator -- and the fraction of the offered market a model answered
    about is exactly what makes two models' `mean_rank_ic` comparable, because abstaining on the
    hard names is otherwise a free way to win.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["alpha-baseline-fold/v1"] = "alpha-baseline-fold/v1"
    artifact: AlphaModelArtifact
    first_test_day: date
    points: tuple[BaselineScorePoint, ...]
    coverage: ICStabilityCoverage
    measured_count: int = Field(ge=0)
    mean_rank_ic: float | None
    stdev_rank_ic: float | None
    rank_icir: float | None
    scored_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator("points")
    @classmethod
    def validate_points(
        cls, value: tuple[BaselineScorePoint, ...]
    ) -> tuple[BaselineScorePoint, ...]:
        if not value:
            raise ValueError(
                "a fold evaluation carries no test day; a fold with no out-of-sample observation "
                "is refused by WalkForwardFold itself and cannot reach here"
            )
        days = [point.prediction_day for point in value]
        if days != sorted(set(days)):
            raise ValueError(
                f"a fold evaluation reads {days}, which is not strictly increasing; a repeated "
                "prediction day is one cross section counted twice and an unordered block is not "
                "the contiguous test block a fold declares"
            )
        return value

    @model_validator(mode="after")
    def validate_the_statistics_match_the_coverage(self) -> Self:
        measured = self.coverage == "measured"
        for name in ("mean_rank_ic", "stdev_rank_ic"):
            if (getattr(self, name) is None) == measured:
                raise ValueError(
                    f"coverage {self.coverage!r} carries {name} {getattr(self, name)!r}; exactly "
                    "the 'measured' code carries the statistics, and rank_icir is the one "
                    "exception -- it is None when the dispersion is zero"
                )
        if self.rank_icir is not None and not measured:
            raise ValueError(f"coverage {self.coverage!r} cannot carry a rank_icir")
        if self.measured_count != sum(1 for point in self.points if point.coverage == "measured"):
            raise ValueError(
                f"{self.measured_count} days are declared measured and "
                f"{sum(1 for point in self.points if point.coverage == 'measured')} points say "
                "they are; the count is a summary of the points and cannot disagree with them"
            )
        for name in ("mean_rank_ic", "stdev_rank_ic", "rank_icir"):
            statistic = getattr(self, name)
            if statistic is not None and not math.isfinite(statistic):
                raise ValueError(f"{name} carries {statistic!r}, which is not a finite statistic")
        return self

    @property
    def test_day_count(self) -> int:
        """How many test days were offered -- the denominator `measured_count` is out of."""
        return len(self.points)


def _rank_ic(scores: Sequence[float], targets: Sequence[float]) -> tuple[ICCoverage, float | None]:
    """One day's Spearman correlation, or which side of it had nothing to say.

    The three refusals are decided in `ICCoverage`'s own declared order, which is
    `FactorICStudy.measure`'s: the sample size first because the other two are questions about a
    cross section, then the scores because a tied model output is a defect in the model, then the
    targets because a tied market is a fact about the day. Naming the model first puts the report
    on the half somebody can act on.
    """
    if len(scores) < MINIMUM_RANK_SECURITIES:
        return "insufficient_sample", None
    if _ties(scores):
        return "degenerate_scores", None
    if _ties(targets):
        return "degenerate_returns", None
    return "measured", _pearson(average_ranks(scores), average_ranks(targets))


def score_point(batch: PredictionBatch, *, section: PanelSection) -> BaselineScorePoint:
    """Read one test day: pair the batch's scores with the labels the panel built at that instant.

    The pairs are read in the batch's own row order and are **not** sorted here, which is a sort a
    first draft had and a mutation sweep found nothing could miss. `rankable` needs one because it
    is handed a panel's rows in whatever order the panel had them; a `PredictionBatch` has already
    refused any row list that is not strictly increasing by `ts_code`, so a second sort would be
    that contract's guarantee stated twice with this copy the one free to go stale --
    `V2-P4-012`'s deleted `sorted()` around an already-ascending tuple, on a different plane.

    The batch and the section are required to be about the same instant. They are two objects
    from two different places -- one is what a model answered, the other is what the market did
    -- and a reading that silently correlated one day's scores against another day's outcomes is
    the one mistake here that would produce a plausible number.
    """
    if batch.as_of != section.as_of:
        raise AlphaModelError(
            f"a batch dated {batch.as_of.isoformat()} is being read against the section dated "
            f"{section.as_of.isoformat()}; scores and outcomes from two instants correlate to a "
            "number that means nothing and looks like a measurement"
        )
    targets = {example.ts_code: example.target for example in section.examples}
    pairs = [
        (item.ts_code, score, targets[item.ts_code])
        for item in batch.predictions
        for score in (item.score,)
        if score is not None and item.ts_code in targets
    ]
    coverage, value = _rank_ic(
        [score for _code, score, _target in pairs],
        [target for _code, _score, target in pairs],
    )
    return BaselineScorePoint(
        as_of=batch.as_of,
        predicted_at=batch.predicted_at,
        prediction_day=section.prediction_day,
        offered_count=len(batch.predictions),
        scored_count=len(batch.scored),
        paired_count=len(pairs),
        coverage=coverage,
        rank_ic=value,
    )


def evaluate_fold(
    model: AlphaModel, fold: WalkForwardFold, *, shelf_life: timedelta | None
) -> FoldEvaluation:
    """Fit one fold's training set and read its test block, one prediction day at a time.

    `model` is typed as the `AlphaModel` **Protocol** rather than as this module's baseline, and
    that is what makes every number here `V2-P4-015`'s too: a tree model satisfying the same
    protocol from a package this one may not import is measured by exactly this function.

    Every batch is dated `predicted_at = section.as_of`. A simulated prediction is made at the
    instant it simulates, and a wall clock would make an evaluation unreproducible and every test
    order-dependent -- but it is therefore *not* evidence that anything was predicted before an
    outcome was known, which is Story S32's requirement and `V2-P4-017`'s to meet.

    **`shelf_life` is why a stale model cannot win here, and the mechanism is one this module
    already had.** A fold's fit is dated at its own `training_cutoff` and its test block runs
    forward from there, so the *later* days of a long block stand further past the cutoff than the
    earlier ones -- and an expired fit abstains on every row of those days. Nothing below needs a
    case for it:

    - A fold that is stale **throughout** scores nothing, so no day is `measured`, `coverage` is
      not `measured`, and `mean_rank_ic` is `None` by `FoldEvaluation`'s own validator. It reports
      no headline rather than a flattering one.
    - A fold that expires **partway** reports a `mean_rank_ic` over only the days it survived --
      which is exactly the free-skill case, and exactly what `scored_ratio` was made never-`None`
      for. `V2-P4-018` measured that the headline alone cannot tell the truncated fold from a
      short honest one and that the pair can:
      `test_a_fold_that_expires_partway_reports_the_fresh_headline_and_a_ratio_below_one`.
    """
    fitted: FittedAlphaModel = model.fit(fold.training_set)
    points = tuple(
        score_point(
            fitted.predict(
                section.cross_section, predicted_at=section.as_of, shelf_life=shelf_life
            ),
            section=section,
        )
        for section in fold.test_sections
    )
    return _summarize(fitted.artifact, first_test_day=fold.first_test_day, points=points)


def evaluate_walk_forward(
    model: AlphaModel, folds: Sequence[WalkForwardFold], *, shelf_life: timedelta | None
) -> tuple[FoldEvaluation, ...]:
    """Every fold of a schedule, each fitted separately and each carrying its own artifact.

    One fit per fold rather than one shared fit, which `AlphaModel.fit`'s "returns a new object"
    is what makes possible: folds that shared a mutable model would share one artifact, and
    `V2-P4-016` could not address them apart.

    One `shelf_life` for the whole schedule rather than one per fold: it is a property of the
    *ask*, and a schedule that read its early folds more leniently than its late ones would not be
    a series at all.
    """
    if not folds:
        raise AlphaModelError(
            "a walk-forward evaluation was given no fold; an evaluation over nothing is an empty "
            "success, and walk_forward_folds refuses to build a schedule of none"
        )
    return tuple(evaluate_fold(model, fold, shelf_life=shelf_life) for fold in folds)


def _summarize(
    artifact: AlphaModelArtifact,
    *,
    first_test_day: date,
    points: tuple[BaselineScorePoint, ...],
) -> FoldEvaluation:
    """Reduce a block's points to the fold's headline, `ICSummary`'s shape on one fold."""
    measured = [point.rank_ic for point in points if point.rank_ic is not None]
    offered = sum(point.offered_count for point in points)
    scored = sum(point.scored_count for point in points)
    if len(measured) < MINIMUM_FOLD_DAYS:
        return FoldEvaluation(
            artifact=artifact,
            first_test_day=first_test_day,
            points=points,
            coverage="insufficient_as_ofs",
            measured_count=len(measured),
            mean_rank_ic=None,
            stdev_rank_ic=None,
            rank_icir=None,
            scored_ratio=scored / offered,
        )
    mean = statistics.fmean(measured)
    dispersion = statistics.stdev(measured)
    return FoldEvaluation(
        artifact=artifact,
        first_test_day=first_test_day,
        points=points,
        coverage="measured",
        measured_count=len(measured),
        mean_rank_ic=mean,
        stdev_rank_ic=dispersion,
        rank_icir=None if dispersion == 0.0 else mean / dispersion,
        scored_ratio=scored / offered,
    )


KNOWN_BASELINE_LIMITATIONS: Final[tuple[BaselineLimitation, ...]] = (
    BaselineLimitation(
        code="a_score_is_a_position_in_this_cross_section_and_not_a_property_of_the_security",
        detail=(
            "Every column is ranked inside the population of the cross section it was handed, so "
            "one security's score moves when a different security joins or leaves -- adding a "
            "name below it raises its position on every column. That is a correctness "
            "requirement rather than a defect: factor_redundancy._correlate measured that "
            "restricting a 40-name rank vector to a 25-name intersection disagreed with the "
            "honest answer on 200 of 200 random trials, by as much as 0.100, so a rank has to be "
            "taken inside the set it is about. What it costs is that a stored PredictionBatch is "
            "a statement about a cross section and not a per-security forecast, and two batches "
            "over different universes are not comparable row by row. V2-P4-017 owns what a store "
            "may put beside a batch. Whether a universe version belongs in the artifact was "
            "V2-P4-016's and it declined: a universe version addresses the matrix that was read "
            "rather than the fit, and what the universe cost a fit is already in the artifact as "
            "training_example_count -- see "
            "the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it for what that "
            "leaves open."
        ),
    ),
    BaselineLimitation(
        code="the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice",
        detail=(
            "A coefficient is that column's own mean training rank IC and is computed with no "
            "reference to any other column. So two columns carrying the same information "
            "contribute twice, and V2-P4-012's grammar makes that the ordinary case rather than "
            "the pathological one: a factor's raw, processed and neutralized tiers are three "
            "columns of one matrix and are near-duplicates by construction. A joint least-squares "
            "fit is what would down-weight them, it is expressible in stdlib -- Gaussian "
            "elimination on a p x p Gram matrix -- and it is declined because what stdlib does "
            "not offer is the QR, the SVD or the honest condition number that makes the answer "
            "trustworthy, and this repository's own columns are the adversarial case. "
            "V2-P4-013's corpus is the extreme: its two columns are exactly rank-anticorrelated, "
            "so a joint solve there is singular while the marginal fit answers. Handling joint "
            "structure is precisely what V2-P4-015's tree model adds, which is why D13 asks for "
            "both rather than for one."
        ),
    ),
    BaselineLimitation(
        code="a_rank_baseline_forecasts_no_return_and_its_score_carries_no_units",
        detail=(
            "The score is a coefficient-weighted sum of cross-sectional positions, bounded by "
            "the number of columns, and it is not a return, a probability or an expected value. "
            "Nothing downstream reads it as one -- PredictionBatch declares no units and "
            "rank_candidates orders by it -- and the report that would check a units claim is "
            "Story S31's calibration report, which the PRD defers to v2.1. So a level-space "
            "linear baseline was not delivered: its one advantage over this model is an output "
            "with units, and this repository cannot yet cash it, while its cost is a solve with "
            "no conditioning story. What it would additionally have needed is a preprocessing "
            "policy visible to the model, and V2-P4-012 sealed that inside "
            "FeatureSpec.feature_version on purpose."
        ),
    ),
    BaselineLimitation(
        code="a_tie_this_baseline_can_see_is_honest_and_the_neutralised_tier_hides_the_one_that_matters",
        detail=(
            "V2-P4-004 measured this market's shipped winsorization putting 56 of 5,540 names on "
            "one identical turnover_rate value, 55 on pb and 41 on pe_ttm. average_ranks gives "
            "such a block one shared position, so this baseline neither separates them nor "
            "pretends to. What it cannot see is the same block after INDUSTRY_AND_SIZE: that "
            "issue measured the 41 clipped names carrying one processed value and 41 distinct "
            "neutralised residuals, ordered entirely by industry mean and log size and taking "
            "seven of the neutralised top ten. A FeatureCrossSection carries no "
            "ComponentCrossSection.clipped_subjects, so a neutralised column reaches this fit as "
            "41 ordinary distinct numbers and every metric here reports an ordering of "
            "industries and capitalisations wearing the factor's name. Carrying the clip block "
            "across the feature-matrix seam is V2-P4-012's plane, not this one's."
        ),
    ),
    BaselineLimitation(
        code="the_two_abstention_reasons_are_sentences_and_not_the_vocabulary_story_35_asks_for",
        detail=(
            "Prediction.abstention is free text until V2-P4-018, and this module produces exactly "
            "two constants: ABSTAIN_INCOMPLETE_FEATURES for a security outside the scored "
            "population, and ABSTAIN_UNRANKABLE_CROSS_SECTION when nobody is. Neither "
            "interpolates a count, so that issue can bind one code to one condition. What is "
            "absent is the rest of S35: nothing here abstains because a model is stale, because "
            "a feature version has moved, or because an upstream refused -- this fit has no clock "
            "and no store and cannot tell. It is also worth stating what one abstention costs "
            "downstream: rank_candidates enforces all-or-nothing per ranking, so a single "
            "abstained name on a shortlist means that shortlist can carry no CandidatePrediction "
            "at all, which is KNOWN_ALPHA_MODEL_LIMITATIONS' "
            "an_abstention_can_empty_a_ranking_of_predictions, still unresolved and still not "
            "this issue's."
        ),
    ),
    BaselineLimitation(
        code="an_evaluation_is_dated_at_the_instant_it_simulates_and_proves_nothing_about_when",
        detail=(
            "evaluate_fold dates every batch at predicted_at = section.as_of, because a "
            "simulated prediction is made at the instant it simulates and a wall clock would "
            "make an evaluation unreproducible and every test order-dependent. The consequence "
            "is that a batch produced here satisfies PredictionBatch's predicted_at >= as_of "
            "trivially and is no evidence at all for Story S32's actual requirement -- that a "
            "batch was produced before its observation window closed. That needs a trading "
            "calendar this module deliberately does not own and a store that refuses an "
            "overwrite, and Implementation Decision 14's second half -- a backfilled "
            "recomputation is its own artifact and may not replace the original -- is a rule "
            "about a store. Both are V2-P4-017's."
        ),
    ),
    BaselineLimitation(
        code="nothing_here_checks_that_the_declared_feature_version_is_the_matrix_it_was_fitted_on",
        detail=(
            "feature_matrix.py says twice that V2-P4-014 'is the first caller "
            "require_declared_features exists for'. It cannot be: "
            "backtest-no-numeric-stack-or-panel-plane lists openalpha_cn.feature_matrix among "
            "the modules forbidden to the whole backtest package, so no study under it can call "
            "that function, and this baseline lives there for the same reason walk_forward.py "
            "does. The check belongs where a declaration and a matrix are first held together, "
            "which is a composition above both planes rather than a study on one. V2-P4-021's "
            "model_view._model_request arrived there first and runs the check. It does not make "
            "this entry false: a fit driven from anywhere but that face still records an "
            "AlphaModelDeclaration.feature_version this module never verifies, which is what "
            "KNOWN_ALPHA_MODEL_LIMITATIONS' the_feature_version_is_a_name_this_contract_cannot"
            "_check said would remain true of any declaration that never meets a matrix."
        ),
    ),
    BaselineLimitation(
        code="decision_13s_threshold_is_computed_by_nothing_here_and_gated_by_nothing_anywhere",
        detail=(
            "D13's operative clause is that a more complex model is accepted only when it "
            "improves on a pre-defined out-of-sample, net-of-cost criterion without violating "
            "stability and capacity thresholds, reduced by the PRD to the single threshold "
            "'a new model must beat the baseline'. This module produces the numbers that "
            "comparison needs -- mean_rank_ic beside scored_ratio and measured_count -- and "
            "compares nothing, declares no threshold and refuses no model. Net-of-cost is not "
            "even measurable here: a cost model is backtest/execution.py's and a quantile "
            "long-short spread is backtest/factor_portfolio.py's, and reaching either would make "
            "this module a portfolio study. V2-P4-015 is the first model the threshold binds, "
            "and a gate over a report is Decision 20's plane."
        ),
    ),
    BaselineLimitation(
        code="a_minority_leak_moves_this_baselines_coefficient_and_not_the_order_it_produces",
        detail=(
            "V2-P4-013 separated a leaked fold from a purged one with its reference model's "
            "single learned bit, and this issue expected a mean rank IC to separate the same "
            "pair. It does not: on that corpus both read exactly -1.0. A rank correlation is "
            "invariant to magnitude, so each leaked training day contributes +1 and each honest "
            "one -1 and the fit averages them -- two of six leaked comes out at -1/3 against -1 "
            "-- while that reference pools its examples and compares two means, which lets the "
            "fixture's twenty-to-one coefficient ratio flip a bit outright. The leak is "
            "therefore visible in the coefficient, which FoldEvaluation.artifact carries by "
            "value, and invisible in the fold's own number. Whether that generalises is exactly "
            "what this corpus cannot say: its two columns are rank-anticorrelated, so both "
            "coefficients rescale together and the order they produce cannot move at all. "
            "V2-P4-022 owns the corpus that could answer it. This entry and its neighbour "
            "every_number_this_module_has_produced_was_measured_on_a_leak_fixture contradicted "
            "each other for two issues, and V2-P4-092 measured that this one is the true half."
        ),
    ),
    BaselineLimitation(
        code="every_number_this_module_has_produced_was_measured_on_a_leak_fixture",
        detail=(
            "The corpus behind the tests is tests/walk_forward_fixtures.py, whose own docstring "
            "says it has no noise model and exists to make one bit of a reference model flip. So "
            "every fold number in this module is a statement about a split, not about alpha: "
            "four securities whose returns are a single per-session coefficient times a fixed "
            "offset order perfectly or reverse perfectly and can do nothing else, and a "
            "mean_rank_ic of exactly -1.0 is what that corpus can produce rather than a "
            "measurement of skill. V2-P4-092 corrected this entry rather than appending around "
            "it: it said those numbers were '+1.0 and -1.0 that separate a leaked fold from a "
            "purged one', which contradicted its own neighbour "
            "a_minority_leak_moves_this_baselines_coefficient_and_not_the_order_it_produces "
            "and is false. Measured across all four configurations of both corpora, mean_rank_ic "
            "reads -1.0 every time and the leak shows only in the coefficient (-1/3 against "
            "-1.0); the 1.0 and 0.0 that do separate the two are V2-P4-013's concordance "
            "numbers, whose own helper says it 'can only come out 1.0 or 0.0', read into a "
            "metric that is not it. test_no_configuration_of_either_corpus_lets_a_rank_ic_"
            "separate_a_leak_from_a_purge is what would now fail. V2-P4-022 owns the corpus with "
            "a known signal-to-noise ratio and a known-null control, which is what an evaluation "
            "needs before any number it reports is a claim, and no number here should be read as "
            "one."
        ),
    ),
)
"""Ten named boundaries on what this baseline and its numbers say, `KNOWN_IC_LIMITATIONS`' form.

Every `code` is required to appear as a string literal in executable test code by
`tests/unit/test_known_limitation_registries.py`, which is what keeps a rename from silently
orphaning every citation of it.
"""
