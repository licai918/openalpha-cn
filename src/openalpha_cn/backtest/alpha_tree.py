"""The tree half of Implementation Decision 13's floor, and the dependency it did not take.

The row is `LightGBM 基线 + 容器修复` and Implementation Decision 13 is what the first half is
short for: *"先交付可理解基线。线性/排序与树基线建立最低比较。更复杂模型仅在预定义样本外、扣成本
标准上改进…才被接受。"* Story S29 names the same pair. `V2-P4-014` delivered the rank half and
said this issue is *"the first model that has to beat this one"*.

## The dependency question, answered before the model

`V2-P4-011` measured that a LightGBM model cannot follow the reference implementation into
`backtest/` and wrote that this issue **must argue its own home**. That turned out to be the
second question. The first is whether this repository should take the dependency at all, and
the answer here is **no** -- so the home question dissolves and this module sits beside the
baseline it is compared against.

The argument is not "the standard library can express a tree", although it can. It is that the
three things ADR-0003 has spent eight sections weighing come out the same way again, and that a
fourth thing is true of a *tree* specifically:

- **A tree never needed the thing the standard library lacks.** `V2-P4-014` declined a joint
  least-squares fit because stdlib offers no QR, no SVD and no honest condition number, and
  this repository's columns are the adversarial case -- `V2-P4-013`'s corpus has two exactly
  rank-anticorrelated columns whose Gram determinant is zero. A regression tree's split search
  divides by a **count**, never by a Gram matrix. Two perfectly correlated columns make a tree
  pick one and condition the other away at the next node; they make a solve undefined. So the
  boundary `V2-P4-014` drew -- *"a failure mode that is loud"* against *"a large coefficient
  nobody can tell from a signal"* -- puts a tree on the near side of it without a library.
- **A tree's regularisation is declared and stored; a solve's conditioning is discovered and
  is not.** `max_depth`, `tree_count`, `learning_rate` and `min_leaf_securities` are four flat
  scalars in `AlphaModelDeclaration.hyperparameters`, so they travel into the artifact, into
  `V2-P4-016`'s address and into `V2-P4-017`'s store. A rank cutoff chosen inside `lstsq` does
  none of that.
- **Measured, at ADR-0002's whole-market scale**: 20 prediction days x 5,534 securities x 3
  columns = 110,680 pooled rows from real `OutcomeLabel`s, at `BASELINE_HYPERPARAMETERS`,
  **4.55 s** (76 ms/tree over 60 trees, 900 encoded nodes). Against the neighbours ADR-0003
  already measured that is **2.0x** one `compute_factor` over a 675,148-row partition (2.24 s),
  **1.26x** the 3.62 s it takes to build the 110,680 labels the fit consumes, and **8.0%** of
  the smallest of the five `write_panel_batch` measurements (56.7 s). It is **18.4x** the rank
  baseline's own fit, re-measured at 247.8 ms on the same corpus against `V2-P4-014`'s 216.4 ms.
  So the honest sentence is that a tree fit is the **same order** as the steps that already
  surround it and an order below the write path -- not, as a first draft of this paragraph
  claimed off a prototype, comfortably beneath the label build. See ADR-0003's `V2-P4-015`
  section for where that claim came from and what corrected it.
- **And a baseline that is a tuned gradient-boosting library is not a baseline.** D13 admits a
  more complex model only when it beats the floor on a pre-defined out-of-sample, net-of-cost
  criterion. A floor with several hundred tunable parameters and a leaf-wise growth policy is
  the thing that criterion exists to judge, not the thing it judges against.

What declining costs is written where a reader meets it:
`this_is_a_histogram_boosting_of_the_kind_lightgbm_does_and_not_lightgbm`. The honest bound is
that this implements one algorithm -- depth-wise growth, squared error, a fixed histogram, no
subsampling, no categorical handling, one thread -- and LightGBM implements a research program.

## Why the dependency was not taken as an optional extra either

`pyproject.toml` has an `akshare` extra, and it is a real precedent for *shipping something
behind a flag*. It is not a precedent for shipping a **baseline** behind one: a comparison floor
nobody can run by default is not a floor, and D13's acceptance gate would then be a gate that
half the installs cannot open. `tests/unit/test_repository_assets.py` also measures that the
extras table's guard is a **name** check over eight distributions and that `lightgbm` is not one
of them -- so an extra named after that wheel would have walked past a gate whose own docstring
says it exists to stop exactly that. The measurement that makes this concrete is not about
`lightgbm` at all, because a test here may not reach the network: it is that **`akshare`**, an
extra this repository has shipped since P1, reaches `pandas` and therefore `numpy` in `uv.lock`
and has been passing that guard the whole time.
`test_a_numerical_stack_cannot_arrive_through_an_extra_that_only_names_its_wheel` closes the hole
over the resolved graph rather than over the declared names. The runtime dependencies are
unchanged at **nine**.

## What this model is, in one paragraph

A gradient-boosted ensemble of depth-limited regression trees over **cross-sectional rank
positions**, fitted by squared error against the rank positions of the targets.
`CrossSectionalRankModel` earned that space and the reasons carry over unchanged: a score's only
consumer is an order, a level-space threshold means a different thing under one
`feature_version` than under another, and `V2-P4-004` measured 56 of 5,540 names sharing one
winsorized value that ranks give one shared position. One reason is **new** and belongs to a
tree: a threshold on a raw level is a number whose meaning drifts with the market, so a tree
pooling several days of raw levels would learn a boundary that means one thing in June and
another in December. A threshold on a rank position is a percentile, and a percentile is the
same statement on every cross section. That is what makes pooling legal here at all --
`CrossSectionalRankModel.fit` refuses to pool because *"pooling two days' values would rank a
Monday against a Tuesday"*, and mapping each day onto `[-1, 1]` first is precisely the repair
of that objection rather than an exception to it.

## What it adds over the rank baseline, measured on three corpora

`V2-P4-014`'s model scores `sum(coefficient * rank position)`. It is linear in the positions and
monotone in every column, and its coefficients are marginal. Three shapes separate the two
models, all read through `evaluate_fold` -- the **same** function, because `AlphaModel` is a
`Protocol` and this module and `alpha_baseline` import nothing from each other:

| corpus | rank baseline `mean_rank_ic` | this model |
| --- | --- | --- |
| the target rises with one column | **+1.0000** | +0.9995 |
| the target is the product of two columns | -0.0189 | **+0.8465** |
| two near-duplicate columns and one real one | +0.9735 | **+0.9992** |

The first row is the one worth reading twice, and it is asserted rather than mentioned
(`test_the_rank_baseline_beats_the_tree_where_its_own_assumption_holds`). Where the baseline's
assumption is exactly true, a tree is a step function approximating a straight line and it is
**worse**. D13 asks for two baselines and this is why: neither dominates, and a challenger has to
clear both.

The second row is what a marginal weighted sum cannot reach at all: each column's marginal rank
IC is zero by construction on a product, so there is nothing for a coefficient to be.

The third is `KNOWN_BASELINE_LIMITATIONS`'
`the_coefficients_are_marginal_so_two_redundant_columns_are_counted_twice` closing, and the
coefficients say it plainly. The rank baseline learns `[0.2941, 0.2934, 0.9468]` on that corpus:
the two near-duplicates earn all but identical coefficients and the pair therefore carries 0.588
against the real column's 0.947, when between them they deserve one column's worth. A split picks
one column at a node and the duplicate's information is conditioned away by the partition rather
than counted a second time.

Every number in the table is taken at `FAST_HYPERPARAMETERS`, the faster setting the test module
uses; `BASELINE_HYPERPARAMETERS` reads `+0.8042` on the interaction corpus against the same
`-0.0189`, which is the verdict this module ships at.

## The histogram, and what it costs

Splits are searched over `SPLIT_BIN_COUNT` equal-width bins of `[-1, 1]` rather than over every
distinct value, which is LightGBM's own algorithm at baseline scale and is what makes a whole-
market fit seconds rather than minutes: a node costs one pass over its rows plus one over the
bins, instead of a sort per column per node. What it costs is resolution -- two securities in one
bin are inseparable by any split, on every column at once, which
`a_split_sees_a_bin_and_not_a_rank_so_neighbours_inside_one_bin_share_every_split` states and a
test measures.

`SPLIT_BIN_COUNT` is a module constant and **not** a declared hyperparameter, for
`MINIMUM_RANK_SECURITIES`' own reason: it is the resolution this implementation searches at, not
a modelling choice, and a floor a challenger can tune is not a floor. `code_commit` is what
records it, at the granularity `code_commit` gives.

## The pooled row order, and the claim this module made about it and then withdrew

This section first said the opposite of what it says now, and the correction is the useful part.

`CrossSectionalRankModel.fit` **deleted** a `sorted(by_day)` after a mutation sweep, because
`statistics.fmean` is `math.fsum` underneath and exactly rounded, so the order its days were
averaged in could not change anything. The first draft here claimed this module *cannot* inherit
that deletion, on the grounds that a split's left and right sums accumulate with a plain `+`. The
mutation sweep disagreed and a direct measurement settled it: with the day sort removed the
pooled row order changes on **200 of 200** random permutations of the training set, and the
artifact changes on **0 of 200**.

The reason is `_grow`'s own `math.fsum`. A leaf value is the exactly-rounded mean of the
residuals reaching that node, so it does not move with their order at all -- the same property
`fmean` gave the baseline, arriving through a different function. What is *not* exactly rounded
is the per-bin histogram sum, so a split **gain** can differ in its last bit; for that to change
the tree, an argmax over two gains would have to fall within one unit in the last place, which no
corpus here comes near.

So three of this module's four sorts are gone. A `rows.sort()` over the assembled pool was
deleted outright, because the pool is already in `(prediction_day, ts_code)` order before it runs
-- `sorted(by_day)` orders the days and `rankable` orders the rows inside one. `sorted(by_day)`
itself is the one that is **kept**,
and it is kept for what it makes structural rather than for a floating-point effect nothing here
can observe: it makes the pool a function of the data instead of a function of the caller's
example order, which is what
`test_a_permuted_training_set_produces_the_same_artifact_bit_for_bit` then asserts by
construction rather than by luck.

The other two went the same way, and both are recorded where they were: `_encode`'s
`sorted(entries)` (the pre-order emission is already ascending, and
`AlphaModelArtifact.validate_parameters` is the louder place for that guarantee to live) and
`_decode`'s `sorted(by_tree)` (a validated parameter table is strictly increasing, so the
dictionary's insertion order is the sorted one). `_grow` also lost a `len(members) < 2 *
min_leaf_securities` gate, which was a second copy of a check the edge loop already makes.

`AlphaModelDeclaration.seed` is carried and unused, which is now the third model in this
repository to say so: there is no subsampling and no random initialisation here, and a
deterministic tie-break (lowest column index, then lowest bin edge) is what settles an equal
split gain.

## The artifact is the model

`AlphaModelArtifact.parameters` is a flat, strictly-increasing table of `(str, float)`, and the
whole ensemble is encoded into it: two entries per node, in pre-order, per tree. A leaf is
`feature = LEAF_FEATURE` plus `leaf = <value>`; a split is `feature = <column index>` plus
`edge = <bin>`. Pre-order plus the leaf marker determines the shape uniquely, so no child
pointers are stored and no dead slot exists for a node that did not split.

`FittedBoostedRankTreeModel.__post_init__` decodes the whole table before a prediction is ever
asked for, which is what makes a truncated or a foreign artifact a refusal rather than a wrong
number. That is the property `V2-P4-016` will address and `V2-P4-017` will store, and it is the
same property `FittedCrossSectionalRankModel` has for a much smaller table.

## What is deliberately left to a named issue

- **`V2-P4-016`** owns the artifact's content address. This ensemble is `parameters` and nothing
  else, so that issue adds a computed field rather than reopening anything -- and where D11's
  *metrics* field goes is still that issue's.
- **`V2-P4-017`** owns persistence. An encoded ensemble is several hundred `(str, float)` rows
  where a rank baseline's is three, and whether that shape wants a different storage form is
  that issue's measurement to take.
- **`V2-P4-018`** owns the abstention vocabulary. The two constants here are **imported** from
  `alpha_baseline` rather than re-spelled, `ICCoverage`'s own reason: a second spelling of "this
  security is outside the population" is a second code for one condition.
- **`V2-P4-021`** owns the model faces, and with them any re-export. Nothing outside this package
  drives a fit yet.
- **`V2-P4-022`** owns the corpus with a known signal-to-noise ratio and a known-null control.
  Every number in this module's tests is measured on a noiseless synthetic corpus built to make
  a direction flip, and nothing here is a claim about alpha.
- **A feature-importance report.** A column no split used is simply absent from the encoded
  ensemble, which is a fact a reader can recompute from `parameters` and is *not* a published
  number. D11's *metrics* field is `V2-P4-016`'s and a per-column attribution has no owner on
  this chain yet; `a_column_no_split_used_is_absent_rather_than_reported_as_unused` says so.
- **D13's threshold itself.** This module computes nothing about acceptance; *"新模型必须战胜基线"*
  is Decision 20's report and `backtest/validation.py`'s plane.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from openalpha_cn.backtest.alpha_baseline import (
    ABSTAIN_INCOMPLETE_FEATURES,
    ABSTAIN_UNRANKABLE_CROSS_SECTION,
    MINIMUM_RANK_SECURITIES,
    _rank_positions,
    _ties,
    rankable,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    Prediction,
    PredictionBatch,
    TrainingExample,
    TrainingSet,
    artifact_for,
    prediction_batch_for,
)

TREE_FAMILY: Final[str] = "boosted_rank_trees"
"""The `AlphaModelDeclaration.family` this model answers to.

`BASELINE_FAMILY`'s reason, and the contrast with it is the point: two declarations that went
through two different code paths must not be able to name the same family, because `family` is
the only field that says which path an artifact came out of.
"""

SPLIT_BIN_COUNT: Final[int] = 32
"""How many equal-width bins of `[-1, 1]` a split is searched over.

LightGBM's histogram algorithm at baseline scale, and the reason a whole-market fit is seconds
rather than minutes: a node costs one pass over its rows plus one over the bins, instead of a
sort per column per node.

**Not declarable**, `MINIMUM_RANK_SECURITIES`' own argument: this is the resolution this
implementation searches at rather than a modelling choice, and a floor a challenger can tune is
not a floor. Thirty-two rather than a larger number because the whole point of the histogram is
to bound the per-node cost, and because a bin holding one security on a 5,534-name cross section
would be a lookup table rather than a partition.
"""

LEAF_FEATURE: Final[float] = -1.0
"""The `feature` value that marks an encoded node as a leaf.

A sentinel rather than a separate `kind` entry, which halves the encoded ensemble: a column
index is a non-negative integer by construction, so `-1` cannot collide with one, and the node's
second entry is then `leaf` for a leaf and `edge` for a split.
"""

LEARNING_RATE: Final[str] = "learning_rate"
MAX_DEPTH: Final[str] = "max_depth"
MIN_LEAF_SECURITIES: Final[str] = "min_leaf_securities"
TREE_COUNT: Final[str] = "tree_count"

HYPERPARAMETER_NAMES: Final[tuple[str, ...]] = (
    LEARNING_RATE,
    MAX_DEPTH,
    MIN_LEAF_SECURITIES,
    TREE_COUNT,
)
"""Exactly the four this model reads, in the sorted order the declaration stores them in.

Required rather than defaulted, and that is `AlphaModelArtifact`'s decision rather than this
module's taste: the declaration travels inside the artifact, so a hyperparameter with a default
is one that a stored artifact does not record and that a reader comparing two folds cannot see.
"""

MAX_DEPTH_CAP: Final[int] = 6
"""The deepest tree this module will grow: 2**6 leaves, 127 encoded nodes.

A cap rather than a warning, because depth is what bounds both the artifact's size and the fit's
cost, and because a `max_depth` a caller can raise without limit turns `parameters` into a table
whose canonical JSON `V2-P4-016` has to hash.
"""

MAX_TREE_COUNT: Final[int] = 500
"""The longest ensemble this module will fit, for `MAX_DEPTH_CAP`'s reason."""

BASELINE_HYPERPARAMETERS: Final[tuple[tuple[str, bool | int | float | str], ...]] = (
    (LEARNING_RATE, 0.05),
    (MAX_DEPTH, 3),
    (MIN_LEAF_SECURITIES, 20),
    (TREE_COUNT, 60),
)
"""The setting D13's comparison floor is taken at.

Published as a constant rather than as a default, and the distinction is the whole of why the
four are declared: a caller may fit any setting inside the ranges below, and a model fitted at a
different one is a **challenger** rather than the floor. `MINIMUM_RANK_SECURITIES` is not
declarable for the same reason this is not defaulted -- there, a floor a challenger can move is
not one; here, a floor a stored artifact does not record is not one either.

Depth three because the corpus this module exists to separate needs a two-column interaction and
depth two is the first that can express one, with one level of margin. Sixty trees at a rate of
0.05 because the product is what a boosted fit's total step is, and 3.0 is a conventional and
unremarkable place to put it -- neither number is measured against a held-out criterion here,
because the corpus that could measure one is `V2-P4-022`'s.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class TreeLimitation:
    """One named boundary on what this model and its numbers can be trusted to say."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class _Settings:
    """The four declared hyperparameters, read once and checked once."""

    learning_rate: float
    max_depth: int
    min_leaf_securities: int
    tree_count: int


@dataclass(frozen=True, slots=True)
class _Leaf:
    """A terminal node: the mean residual of the rows that reached it."""

    value: float


@dataclass(frozen=True, slots=True)
class _Split:
    """An internal node: a column, the highest bin that goes left, and two children."""

    feature: int
    edge: int
    left: _Node
    right: _Node


_Node = _Leaf | _Split


def split_bin(position: float) -> int:
    """The histogram bin a rank position in `[-1, 1]` falls in.

    Public because a reader checking what the resolution costs needs to be able to ask, and
    because `predict` and `fit` must map identically -- one function is how that is guaranteed
    rather than promised. The clamp is what puts the two closed endpoints inside the grid: a
    position of exactly `1.0` would otherwise index one past the last bin.
    """
    index = int((position + 1.0) * 0.5 * SPLIT_BIN_COUNT)
    return min(max(index, 0), SPLIT_BIN_COUNT - 1)


def _settings_of(declaration: AlphaModelDeclaration) -> _Settings:
    """Read the four hyperparameters, or refuse the declaration by name.

    Named refusals rather than a single "bad hyperparameters", because a declaration is written
    by a person and the four failures a person makes are different: naming none of them, naming
    three of four, naming a fifth this model would silently ignore, and naming one outside the
    range where it means anything.
    """
    declared = dict(declaration.hyperparameters)
    missing = [name for name in HYPERPARAMETER_NAMES if name not in declared]
    if missing:
        raise AlphaModelError(
            f"{declaration.name} declares no {missing}; this model reads exactly "
            f"{list(HYPERPARAMETER_NAMES)} and none of them has a default, because a "
            "hyperparameter with a default is one a stored artifact does not record"
        )
    extra = sorted(set(declared) - set(HYPERPARAMETER_NAMES))
    if extra:
        raise AlphaModelError(
            f"{declaration.name} declares {extra}, which this model does not read; a "
            "hyperparameter that changes nothing is one a reader of the artifact will believe "
            "changed something"
        )
    rate = declared[LEARNING_RATE]
    if not isinstance(rate, int | float) or isinstance(rate, bool) or not 0.0 < rate <= 1.0:
        raise AlphaModelError(
            f"{declaration.name} declares {LEARNING_RATE}={rate!r}; a boosted fit's step must "
            "lie in (0, 1] -- at zero no tree contributes anything and above one each tree "
            "overshoots the residual it was fitted to"
        )
    counts: dict[str, int] = {}
    for name, low, high in (
        (MAX_DEPTH, 1, MAX_DEPTH_CAP),
        (MIN_LEAF_SECURITIES, 2, None),
        (TREE_COUNT, 1, MAX_TREE_COUNT),
    ):
        value = declared[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise AlphaModelError(
                f"{declaration.name} declares {name}={value!r}, which is not a whole number; "
                "truncating it here would give two declarations one fitted model and two "
                "addresses"
            )
        if value < low or (high is not None and value > high):
            raise AlphaModelError(
                f"{declaration.name} declares {name}={value}, outside "
                f"[{low}, {'unbounded' if high is None else high}]"
            )
        counts[name] = value
    return _Settings(
        learning_rate=float(rate),
        max_depth=counts[MAX_DEPTH],
        min_leaf_securities=counts[MIN_LEAF_SECURITIES],
        tree_count=counts[TREE_COUNT],
    )


def _pooled(training_set: TrainingSet) -> tuple[tuple[tuple[int, ...], ...], tuple[float, ...]]:
    """Every usable training row as `(binned positions, target position)`, in canonical order.

    One prediction day at a time, because a rank is a position within one cross section --
    and then **pooled**, because a rank position is the same statement on every day. That is the
    repair of `CrossSectionalRankModel.fit`'s objection to pooling rather than an exception to
    it: it declines to pool *values*, and these are not values.

    A day contributes nothing when fewer than `MINIMUM_RANK_SECURITIES` of its rows carry every
    column, or when every target ties. Both are `CrossSectionalRankModel.fit`'s conditions in the
    same order and for the same reasons: a cross section too small for a rank to have an interior
    has no position to report, and a day on which nothing happened has no order to learn.

    A column that ties on one day needs **no** special case here, unlike in the rank baseline: a
    tied column comes out all zeros, lands in one bin, and no split over it can satisfy the leaf
    floor. The baseline has to skip it because it would otherwise average a correlation that does
    not exist; a histogram simply finds nothing there.

    The pool comes out in `(prediction_day, ts_code)` order and **nothing here sorts it into
    that order**: `sorted(by_day)` puts the days in order and `rankable` already returns each
    day's rows in ascending security order, so the order is a property of the construction. A
    first draft appended `rows.sort(key=...)` over the assembled pool; it was measured to be a
    no-op on every row and deleted, which is `CrossSectionalRankModel.fit`'s second sort arriving
    here and leaving the same way. `test_the_pool_is_already_in_day_and_security_order` is what
    keeps that true rather than remembered.

    `sorted(by_day)` **survived** the mutation sweep, and it is kept anyway with the reason
    written down rather than the assertion invented. Replacing it with `by_day` changes the
    pooled row order on 200 of 200 random permutations of a training set and changes the fitted
    artifact on 0 of 200, so no corpus this contract admits can tell it apart: `_grow`'s node
    total is `math.fsum` and a leaf value is exactly rounded, while the only order-dependent
    accumulation left -- the per-bin histogram -- would have to move two candidate gains within
    one unit in the last place before an argmax over them flipped. What the sort buys is that the
    pool is a function of the **data** rather than of the caller's example order, which is a
    property worth having even where it is not observable, and it costs one `sorted()` over at
    most a few thousand dates. That is a judgement, and it is written as one.
    """
    by_day: dict[date, list[TrainingExample]] = {}
    for example in training_set.examples:
        by_day.setdefault(example.label.window.prediction_day, []).append(example)

    rows: list[tuple[tuple[int, ...], float]] = []
    width = len(training_set.feature_ids)
    for day in sorted(by_day):
        targets_by_code = {example.ts_code: example.target for example in by_day[day]}
        complete = rankable((example.ts_code, example.features) for example in by_day[day])
        if len(complete) < MINIMUM_RANK_SECURITIES:
            continue
        targets = [targets_by_code[ts_code] for ts_code, _values in complete]
        if _ties(targets):
            continue
        target_positions = _rank_positions(targets)
        columns = [
            [split_bin(position) for position in _rank_positions([v[column] for _c, v in complete])]
            for column in range(width)
        ]
        for index, (_ts_code, _values) in enumerate(complete):
            rows.append(
                (
                    tuple(columns[column][index] for column in range(width)),
                    target_positions[index],
                )
            )
    return tuple(row[0] for row in rows), tuple(row[1] for row in rows)


def _grow(
    binned: Sequence[tuple[int, ...]],
    residuals: Sequence[float],
    members: Sequence[int],
    *,
    depth: int,
    settings: _Settings,
    width: int,
) -> _Node:
    """One node: the best squared-error split over the histogram, or a leaf.

    The gain is the standard reduction in squared error for a constant fit, written as
    `L^2/n_L + R^2/n_R` because the parent's own term is constant across the candidates and
    dropping it removes an addition from the inner loop without changing which edge wins.

    **The only division is by a count.** That is the whole of why this model needs no numerical
    stack where `V2-P4-014`'s declined joint solve would have: `min_leaf_securities >= 2` keeps
    both denominators at two or more, so there is no conditioning question to ask and no rank
    cutoff to choose.

    Ties are settled by the iteration order -- lowest column index, then lowest bin edge -- via a
    strict `>`. Two columns that are exactly equal therefore always yield the same tree, which is
    the determinism a seed would otherwise have to buy.

    `math.fsum` rather than `sum` for the node total, and it is what makes a leaf value
    order-independent: the residuals reaching one node arrive in whatever order the partition
    left them in, and an exactly-rounded total cannot move with that. A first draft of this
    module sorted the pooled rows for this reason and the sort was deleted instead -- see
    `_pooled` and the module docstring.

    There is deliberately **no** `len(members) < 2 * min_leaf_securities` gate above the search.
    A first draft had one and a mutation sweep found nothing could tell it apart, because it is
    a second copy of a check the loop already makes: if the node holds fewer than twice the leaf
    floor then every candidate edge leaves one side below it, `best` stays `None`, and the leaf
    below is returned anyway.
    """
    total = math.fsum(residuals[index] for index in members)
    value = total / len(members)
    if depth >= settings.max_depth:
        return _Leaf(value)

    best: tuple[float, int, int] | None = None
    for feature in range(width):
        counts = [0] * SPLIT_BIN_COUNT
        sums = [0.0] * SPLIT_BIN_COUNT
        for index in members:
            bucket = binned[index][feature]
            counts[bucket] += 1
            sums[bucket] += residuals[index]
        left_count = 0
        left_sum = 0.0
        for edge in range(SPLIT_BIN_COUNT - 1):
            left_count += counts[edge]
            left_sum += sums[edge]
            right_count = len(members) - left_count
            if left_count < settings.min_leaf_securities:
                continue
            if right_count < settings.min_leaf_securities:
                break
            right_sum = total - left_sum
            gain = left_sum * left_sum / left_count + right_sum * right_sum / right_count
            if best is None or gain > best[0]:
                best = (gain, feature, edge)
    if best is None:
        return _Leaf(value)

    _gain, feature, edge = best
    left = [index for index in members if binned[index][feature] <= edge]
    right = [index for index in members if binned[index][feature] > edge]
    return _Split(
        feature=feature,
        edge=edge,
        left=_grow(binned, residuals, left, depth=depth + 1, settings=settings, width=width),
        right=_grow(binned, residuals, right, depth=depth + 1, settings=settings, width=width),
    )


def _score_node(node: _Node, row: Sequence[int]) -> float:
    """Walk one tree, iteratively -- a depth-6 recursion per row per tree is a needless frame."""
    while isinstance(node, _Split):
        node = node.left if row[node.feature] <= node.edge else node.right
    return node.value


def _encode(trees: Sequence[_Node]) -> tuple[tuple[str, float], ...]:
    """The ensemble as a flat, strictly-increasing `(str, float)` table.

    Pre-order, two entries per node, no child pointers: the leaf marker is what makes the shape
    recoverable, so a node that did not split leaves no unreachable sibling behind. The indices
    are zero-padded so the table's *sorted* order and its *pre-order* agree, which is what lets a
    reader of a stored artifact follow one tree down the page.

    **Not sorted here**, and that is the padding earning its keep. `_emit` visits nodes in
    strictly increasing index order and emits each node's two names in ascending order
    (`.edge` < `.feature` < `.leaf`), so the table comes out sorted. A first draft wrapped this
    in `sorted()`; a mutation sweep found nothing could tell, because nothing could. Removing it
    also moves the guarantee somewhere louder: `AlphaModelArtifact.validate_parameters` refuses a
    table whose keys are not strictly increasing, so a future `_emit` that got the order wrong
    fails at the contract instead of being tidied up on the way past.
    """
    entries: list[tuple[str, float]] = []
    for position, tree in enumerate(trees):
        _emit(tree, prefix=f"t{position:03d}", index=0, entries=entries)
    return tuple(entries)


def _emit(node: _Node, *, prefix: str, index: int, entries: list[tuple[str, float]]) -> int:
    """Append one node's two entries and its subtree's, and return the next free index.

    The index is threaded through as a return value rather than held in a mutable cell, which
    is the difference between a pre-order position a reader can follow and a closure over a
    loop variable.
    """
    name = f"{prefix}.n{index:03d}"
    if isinstance(node, _Leaf):
        entries.append((f"{name}.feature", LEAF_FEATURE))
        entries.append((f"{name}.leaf", node.value))
        return index + 1
    entries.append((f"{name}.edge", float(node.edge)))
    entries.append((f"{name}.feature", float(node.feature)))
    after_left = _emit(node.left, prefix=prefix, index=index + 1, entries=entries)
    return _emit(node.right, prefix=prefix, index=after_left, entries=entries)


def _decode(artifact: AlphaModelArtifact) -> tuple[_Node, ...]:
    """Rebuild the ensemble, or refuse the artifact.

    Called from `__post_init__` rather than lazily from `predict`, which is what makes a
    truncated, foreign or hand-edited artifact a refusal at construction instead of a wrong
    number at the first prediction. `FittedCrossSectionalRankModel` checks its own much smaller
    table the same way and for the same reason.

    The trees come out in order without a `sorted()` over `by_tree`, and that is
    `score_point`'s deleted sort arriving here: `AlphaModelArtifact.validate_parameters` has
    already refused a parameter table whose keys are not strictly increasing, so this dictionary
    is populated in ascending key order and its insertion order **is** the sorted one. A first
    draft sorted anyway and a mutation sweep found nothing could tell it apart.
    """
    table = dict(artifact.parameters)
    by_tree: dict[str, dict[int, tuple[float, str, float]]] = {}
    for key, value in artifact.parameters:
        head, _, tail = key.rpartition(".")
        tree, _, position = head.partition(".")
        if tail != "feature":
            continue
        try:
            index = int(position.removeprefix("n"))
        except ValueError as error:
            raise AlphaModelError(
                f"{artifact.declaration.name}'s artifact carries the parameter {key!r}, which "
                f"is not a node of an encoded {TREE_FAMILY} ensemble"
            ) from error
        second = f"{head}.leaf" if value == LEAF_FEATURE else f"{head}.edge"
        if second not in table:
            raise AlphaModelError(
                f"{artifact.declaration.name}'s artifact declares node {head!r} and carries no "
                f"{second!r}; every encoded node is exactly two entries, so a missing one is a "
                "tree that cannot be walked"
            )
        if value != LEAF_FEATURE and not 0 <= value < len(artifact.feature_ids):
            raise AlphaModelError(
                f"{artifact.declaration.name}'s artifact splits node {head!r} on column "
                f"{value}, and it was fitted on {len(artifact.feature_ids)} column(s); a split "
                "on a column the artifact does not declare has no value to read"
            )
        by_tree.setdefault(tree, {})[index] = (value, head, table[second])
    if not by_tree:
        raise AlphaModelError(
            f"{artifact.declaration.name}'s artifact carries no ensemble; a fitted "
            f"{TREE_FAMILY} model whose parameters encode no tree would score every security "
            "alike, which is a defect wearing a model's shape"
        )

    trees: list[_Node] = []
    for tree in by_tree:
        nodes = by_tree[tree]
        root, consumed = _read(artifact, tree=tree, nodes=nodes, index=0)
        if consumed != len(nodes):
            raise AlphaModelError(
                f"{artifact.declaration.name}'s artifact encodes {len(nodes)} node(s) for tree "
                f"{tree!r} and its pre-order consumes {consumed}; the surplus is unreachable and "
                "the shortfall is a tree that stops mid-branch"
            )
        trees.append(root)
    return tuple(trees)


def _read(
    artifact: AlphaModelArtifact,
    *,
    tree: str,
    nodes: dict[int, tuple[float, str, float]],
    index: int,
) -> tuple[_Node, int]:
    """One pre-order node and the index after it, or `AlphaModelError`."""
    if index not in nodes:
        raise AlphaModelError(
            f"{artifact.declaration.name}'s artifact stops tree {tree!r} at node {index}; a "
            "pre-order encoding that runs out of nodes is a branch with no leaf under it"
        )
    feature, _name, second = nodes[index]
    if feature == LEAF_FEATURE:
        return _Leaf(second), index + 1
    left, after_left = _read(artifact, tree=tree, nodes=nodes, index=index + 1)
    right, after_right = _read(artifact, tree=tree, nodes=nodes, index=after_left)
    return _Split(feature=int(feature), edge=int(second), left=left, right=right), after_right


@dataclass(frozen=True, slots=True, kw_only=True)
class BoostedRankTreeModel:
    """An unfitted tree baseline: a declaration whose four hyperparameters are read once.

    Satisfies `domain/alpha_model.py`'s `AlphaModel` structurally -- it subclasses nothing, and
    it imports nothing from `alpha_baseline` but the population rule and the two abstention
    sentences. That is what lets `evaluate_fold` measure both models with one function and no
    edge between them.
    """

    declaration: AlphaModelDeclaration

    def __post_init__(self) -> None:
        if self.declaration.family != TREE_FAMILY:
            raise AlphaModelError(
                f"{self.declaration.name} declares family {self.declaration.family!r} and this "
                f"model answers to {TREE_FAMILY!r}; a declaration fitted by the wrong "
                "implementation produces an artifact whose family names a code path it never "
                "went through"
            )
        _settings_of(self.declaration)

    def fit(self, training_set: TrainingSet) -> FittedBoostedRankTreeModel:
        """Boost `tree_count` depth-limited trees on the pooled rank positions.

        The base prediction is exactly `0.0` and there is no intercept to fit, which is a
        property of the space rather than a simplification: `_rank_positions` maps average ranks
        so that they sum to zero on **every** cross section, ties included, so the pooled target
        mean is zero by construction and a fitted intercept would be a parameter whose value is
        known in advance.

        Returns a new object rather than mutating `self`, `AlphaModel`'s requirement.
        """
        settings = _settings_of(self.declaration)
        binned, targets = _pooled(training_set)
        if not binned:
            raise AlphaModelError(
                f"{self.declaration.name} was fitted on a training set no prediction day of "
                f"which carried {MINIMUM_RANK_SECURITIES} rows with every column and a target "
                "that did not tie; there is nothing to take a position inside"
            )

        width = len(training_set.feature_ids)
        members = list(range(len(binned)))
        current = [0.0] * len(binned)
        trees: list[_Node] = []
        for _step in range(settings.tree_count):
            residuals = [targets[index] - current[index] for index in members]
            tree = _grow(binned, residuals, members, depth=0, settings=settings, width=width)
            if isinstance(tree, _Leaf):
                raise AlphaModelError(
                    f"{self.declaration.name} could not take one split over "
                    f"{len(binned)} pooled row(s) with min_leaf_securities="
                    f"{settings.min_leaf_securities}; a leaf-only ensemble scores every "
                    "security alike, and that is a defect rather than a model. The condition is "
                    "structural: a split's row counts do not read the residuals, so a root that "
                    "cannot split can never split"
                )
            for index in members:
                current[index] += settings.learning_rate * _score_node(tree, binned[index])
            trees.append(tree)

        return FittedBoostedRankTreeModel(
            artifact=artifact_for(
                declaration=self.declaration,
                training_set=training_set,
                parameters=_encode(trees),
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FittedBoostedRankTreeModel:
    """A fitted tree baseline, whose entire state is its artifact.

    No field but `artifact`, `FittedCrossSectionalRankModel`'s decision and for its reason: the
    ensemble *is* `artifact.parameters`, so the artifact is sufficient to reproduce every
    prediction and `V2-P4-016` has something worth addressing. The decoded ensemble is rebuilt on
    every construction rather than cached in a second field, because a second copy of a public
    tuple is a place for the two to disagree.
    """

    artifact: AlphaModelArtifact

    def __post_init__(self) -> None:
        if self.artifact.declaration.family != TREE_FAMILY:
            raise AlphaModelError(
                f"{self.artifact.declaration.name}'s artifact declares family "
                f"{self.artifact.declaration.family!r} and this model answers to "
                f"{TREE_FAMILY!r}"
            )
        _settings_of(self.artifact.declaration)
        _decode(self.artifact)

    def predict(
        self, cross_section: FeatureCrossSection, *, predicted_at: datetime
    ) -> PredictionBatch:
        """Score every security by the ensemble's sum over its binned cross-sectional positions.

        A security outside the population -- one missing any declared column -- abstains, and so
        does every security when the population is smaller than a rank can be taken over. Both
        sentences are `alpha_baseline`'s constants rather than this module's, which is what stops
        `V2-P4-018` from having to map two codes onto one condition.

        `require_features` is **not** called here, `FittedCrossSectionalRankModel`'s decision: it
        runs inside `prediction_batch_for`, which every path out of this method goes through, and
        `V2-P4-011` deleted the second copy after a mutation sweep measured it was worth nothing.
        """
        settings = _settings_of(self.artifact.declaration)
        trees = _decode(self.artifact)
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
                predictions=(
                    Prediction(ts_code=row.ts_code, abstention=ABSTAIN_UNRANKABLE_CROSS_SECTION)
                    for row in cross_section.rows
                ),
            )

        positions = [
            [
                split_bin(position)
                for position in _rank_positions([values[column] for _code, values in population])
            ]
            for column in range(len(self.artifact.feature_ids))
        ]
        totals = {
            ts_code: settings.learning_rate
            * math.fsum(
                _score_node(tree, [positions[column][index] for column in range(len(positions))])
                for tree in trees
            )
            for index, (ts_code, _values) in enumerate(population)
        }

        return prediction_batch_for(
            artifact=self.artifact,
            cross_section=cross_section,
            predicted_at=predicted_at,
            predictions=(
                Prediction(ts_code=row.ts_code, score=totals[row.ts_code])
                if row.ts_code in totals
                else Prediction(ts_code=row.ts_code, abstention=ABSTAIN_INCOMPLETE_FEATURES)
                for row in cross_section.rows
            ),
        )


KNOWN_TREE_LIMITATIONS: Final[tuple[TreeLimitation, ...]] = (
    TreeLimitation(
        code="this_is_a_histogram_boosting_of_the_kind_lightgbm_does_and_not_lightgbm",
        detail=(
            "The row this module answers names LightGBM and this module does not use it. What "
            "is implemented is one algorithm -- depth-wise growth, squared error, a fixed "
            "equal-width histogram over SPLIT_BIN_COUNT bins, no row or column subsampling, no "
            "L1/L2 leaf penalty, no native categorical handling, no missing-value branch "
            "direction, one thread -- and LightGBM implements a research programme around that "
            "algorithm. The measured cost of declining it is a fit of 4.55 s over 20 prediction "
            "days x 5,534 securities x 3 columns at BASELINE_HYPERPARAMETERS, which is 18.4x "
            "the rank baseline's 247.8 ms on the same corpus, 1.26x the 3.62 s it takes to "
            "build the 110,680 real OutcomeLabels the fit consumes, and 8.0% of the smallest "
            "of ADR-0003's five write_panel_batch measurements. What is NOT "
            "claimed is that this reaches LightGBM's accuracy on any real dataset: no such "
            "comparison was run, because running one requires the dependency the decision "
            "declined -- and could not be run, because no test here reaches the network. See "
            "ADR-0003's V2-P4-015 section for the whole argument and for why an optional "
            "extra was measured to be worse than either taking the dependency or not."
        ),
    ),
    TreeLimitation(
        code="a_split_sees_a_bin_and_not_a_rank_so_neighbours_inside_one_bin_share_every_split",
        detail=(
            "Splits are searched over SPLIT_BIN_COUNT equal-width bins of [-1, 1] rather than "
            "over every distinct rank position, which is what bounds a node's cost to one pass "
            "over its rows. On a cross section wider than SPLIT_BIN_COUNT securities that means "
            "adjacent names share a bin on a column and no split can separate them there; at "
            "ADR-0002's whole-market 5,534 that is roughly 173 securities per bin. Two "
            "securities identical in every column's bin therefore receive exactly the same "
            "score, which a rank correlation reads as a tie and a top-N cut resolves by "
            "whatever order the cross section arrived in -- the same exposure "
            "KNOWN_CROSS_SECTION_LIMITATIONS records for the winsorizer's clip block, arriving "
            "here from a different direction. Raising the bin count is not a caller's choice: "
            "see the constant's own docstring for why it is not declarable."
        ),
    ),
    TreeLimitation(
        code="neither_baseline_dominates_the_other_and_the_monotone_corpus_is_where_this_one_loses",
        detail=(
            "Implementation Decision 13 asks for a linear/rank baseline AND a tree baseline, and "
            "the measurement says why both: on a corpus whose target is a monotone function of "
            "one column, CrossSectionalRankModel's model IS the truth and this one is a step "
            "function approximating a straight line, so it reads a LOWER mean_rank_ic -- "
            "asserted, not mentioned, by "
            "test_the_rank_baseline_beats_the_tree_where_its_own_assumption_holds. A challenger "
            "that beats this model and loses to the rank baseline has beaten nothing. Nothing "
            "in this repository composes the two into a single floor, and doing so would be a "
            "model selection rather than a baseline."
        ),
    ),
    TreeLimitation(
        code="a_column_no_split_used_is_absent_rather_than_reported_as_unused",
        detail=(
            "CrossSectionalRankModel.fit REFUSES a column no training day could measure, "
            "because a coefficient of zero there would report a measurement nobody took. This "
            "model cannot make that refusal and does not try: a tree legitimately ignores a "
            "column, and ignoring one is a finding rather than a failure. What it costs is that "
            "the fact is not published -- an unused column simply never appears as a feature "
            "index in the encoded ensemble, so a reader who wants to know must recompute it "
            "from AlphaModelArtifact.parameters. A per-column attribution is a metric, "
            "Implementation Decision 11's metrics field is V2-P4-016's, and no issue on this "
            "chain owns a feature-importance report yet."
        ),
    ),
    TreeLimitation(
        code="every_number_this_module_produced_was_measured_on_a_noiseless_synthetic_corpus",
        detail=(
            "The three corpora in tests/unit/backtest/test_alpha_tree.py are deterministic "
            "permutations of one spread with no noise model, no cross-sectional correlation "
            "structure and no known signal-to-noise ratio; they exist to make a direction flip "
            "and they succeed at that and at nothing else. V2-P4-022 owns the corpus with a "
            "known SNR and a known-null control, and until it lands no number here is a claim "
            "about alpha. This is V2-P4-014's own "
            "every_number_this_module_has_produced_was_measured_on_a_leak_fixture arriving at "
            "the second model for the same reason."
        ),
    ),
    TreeLimitation(
        code="the_hyperparameters_are_declared_and_nothing_here_selects_them",
        detail=(
            "The four hyperparameters travel in AlphaModelDeclaration and therefore into the "
            "artifact, which is what makes a fit reproducible -- and BASELINE_HYPERPARAMETERS "
            "is a published setting rather than a measured one. Neither the depth, the tree "
            "count, the rate nor the leaf floor was chosen against a held-out criterion, "
            "because the corpus that could choose one is V2-P4-022's. There is no inner "
            "validation loop, no early stopping and no search; a caller who tunes these against "
            "the same block they later report on has leaked, and nothing in this module can "
            "detect that. V2-P4-013's purge governs the split and says nothing about "
            "hyperparameter selection, which is a second and unaddressed leakage surface."
        ),
    ),
    TreeLimitation(
        code="a_score_carries_no_units_and_the_ensembles_leaf_values_are_not_returns",
        detail=(
            "The fit chases cross-sectional rank POSITIONS of the targets, not the targets, so "
            "a leaf value is a mean position and a score is a sum of mean positions. It is "
            "ordinal and nothing else -- CrossSectionalRankModel's "
            "a_rank_baseline_forecasts_no_return_and_its_score_carries_no_units, inherited "
            "unchanged, and Story S31's calibration report is still v2.1. This model is also "
            "the weaker of the two on bounds: a rank baseline's score is bounded by its column "
            "count in closed form, while a boosted score can step past the range of the targets "
            "it chased (measured at 1.0038 against targets in [-1, 1]), so only finiteness is "
            "structural here."
        ),
    ),
)
"""What this model and its numbers cannot be trusted to say -- the thirtieth registry."""
