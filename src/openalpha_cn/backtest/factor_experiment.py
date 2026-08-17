"""The immutable factor experiment artifact and its raw/processed/neutralised report (`V2-P3-014`).

`V2-P3-005`..`008` each answer one question about one tier of one factor. This module binds their
four answers into **one record per experiment**, on all three tiers at once, and adds the one
thing none of them can produce alone: the *difference between the tiers*, computed once and
carried as a closed verdict rather than left to a reader holding two numbers.

The roadmap's annotation is the acceptance criterion: *否则分不清「因子有效」与「暴露没控住」*.
A factor whose raw IC is 0.08 and whose neutralised IC is 0.00 is earning the industry and the
market-capitalisation exposure, not its own edge. **That must be visible in the structure of the
report and not in the reader's arithmetic**, which is why `TierAttribution` is a first-class
record with its own vocabulary and its own declared line, and why a report of three tier rows
alone was rejected -- three rows still leave "did the number fall enough to matter" to whoever is
reading, and this repository has taken eight Critical findings on quantities nobody declared a
line for.

D8 is the decision underneath it: *原始因子值与预处理分离 ... 报告可比较 raw / processed /
neutralized 表现而不覆盖源观测*. The three tiers arrive here as three separate sets of stored
observations that were never written over each other, and this module never merges them -- it
reports each whole, beside its own coverage codes, and reports the *steps between* them
separately again.

It stores nothing and reads no partition. `backtest/factor_ic.py`, `factor_portfolio.py`,
`factor_redundancy.py` and `factor_tradeability.py` are all standard-library leaves over
`domain/`, and this is the fifth: it is their consumer, it re-derives none of their arithmetic,
and it imports their tables rather than restating any of them.

## What "immutable" means here, because the two readings are different

They are both delivered, and they close different failure modes:

1. **Content addressing.** `FactorExperimentSpec.experiment_id` is `stable_model_id` over the
   *declaration* -- the four upstream specs, the retention floor, the code commit, and digests of
   the as_ofs and of the stored builds each tier was read from. Editing any of those does not
   mutate an experiment; it mints a different one. `FactorExperimentArtifact.content_digest` is
   the same function over the *answers*.
2. **Write-once.** `panel_factors._refuse_to_drop_a_stored_build` is the precedent and its shape
   is what `refuse_a_restated_experiment` copies: an arriving record whose `experiment_id`
   matches a held one and whose `content_digest` does not is **refused**, naming both digests. An
   arriving record identical to a held one is a no-op and is accepted -- the same both-directions
   property `FactorBuildManifest` is pinned under, because a rebuild that cannot reproduce its own
   identity is a rebuild that can never be written.

The two are not redundant. Content addressing alone says "an edit is a different artifact" and
says nothing about the edited one replacing the original; the refusal alone would be satisfiable
by an identity that moved for a wall clock. Together they say: **the same declaration over the
same inputs has exactly one answer, and any other answer is a different experiment or a bug.**

**And the seal is the enforcement at the boundary a value object otherwise has none at.**
`FactorExperimentRecord.sealed_digest` is a stored field and its validator requires it to equal
the artifact's recomputed `content_digest`, so a record whose JSON was edited between two
processes does not merely differ -- it **fails to parse**.
`tests/unit/backtest/test_factor_experiment.py::
test_every_field_of_a_sealed_record_refuses_to_reopen_after_a_single_edit` walks every scalar leaf
of a serialised record, perturbs exactly one, and requires a refusal on each. What that is not is
a signature; see `KNOWN_EXPERIMENT_LIMITATIONS`'
`the_seal_detects_an_edit_and_does_not_authenticate_one`.

## What the key is, and what moves it

`experiment_id` covers the **declaration and the inputs**, and deliberately not the answers:

    FactorICSpec, QuantilePortfolioSpec, TradeabilitySpec, RedundancySpec   the four policies
    retention_floor                                                        the declared line
    code_commit                                                            FactorBuildManifest's
    horizon_sessions                                                       the window
    as_of_digest                                                           which days
    raw/processed/neutralized_source_digest                                which stored builds

`content_digest` covers the whole document, the spec included. So **one `experiment_id` under two
`content_digest`s is not two experiments, it is non-determinism**: the same policies over the same
builds at the same commit produced two different numbers. That is exactly the reading
`FactorBuildManifest.manifest_id` is built for ("usable as a *recovery* path rather than only as a
drift detector") arriving one plane up, and it is why the answers are out of the key rather than
in it. `test_two_records_that_disagree_under_one_experiment_id_are_refused` and
`test_a_recomputed_record_that_agrees_is_admitted_rather_than_refused` drive both directions.

**What does not move it**: `built_at` and `note`. Both are `build_factor_experiment` parameters,
both are carried on `FactorExperimentRecord`, and both are outside every digest -- the wall clock
for `FactorBuildManifest.built_at`'s reason (recomputing the same experiment must reproduce the
same identity) and the prose for `FactorNote`'s (a typo fix that moved a content address is the
defect `V2-P3-001`'s `summary` field was removed to correct).

**That list is audited rather than asserted.** `V2-P3-002` reads
`inspect.signature(compute_factor).parameters` and requires every parameter to be either measured
to move the identity or named in an exemption table with the reason it does not; a tenth parameter
fails until somebody classifies it.
`tests/unit/backtest/test_factor_experiment.py::
test_every_parameter_of_the_builder_moves_the_identity_or_is_exempted_by_name` is that audit on
this module's builder, with `IDENTITY_EXEMPT_PARAMETERS` as the table. A second audit runs the
other way: `test_a_measurement_that_changes_moves_the_content_and_not_the_identity` varies a tier's
statistics alone and requires `content_digest` to move and `experiment_id` to hold.

## The shape of the report: three rows **and** a grid, because three rows are not enough

`TierReport` is one tier's upstream answers, whole -- five objects from four studies:

    ic            ICSummary                mean IC, ICIR, sign consistency
    portfolio     QuantilePortfolioSummary per-group net and gross means, the oriented spread
    turnover      TurnoverSeries           the rolling portfolio's churn and its cost bracket
    tradeability  TradeabilitySummary      the coverage funnel, the per-group execution
                                           decomposition and the long group's capacity
    survival      RedundancySummary | None raw against this tier, cross-sectionally

Five objects from four modules: `V2-P3-007` publishes two, and they answer two questions with two
refusal vocabularies. **`tradeability` is the field `V2-P3-007`'s own annotation is delivered
through** -- *让统计上好看但不可实施的信号显形* -- and it is a per-tier row rather than one
per-`as_of` for `TradeabilitySummary`'s own stated reason: the other four fields are series-level,
and a collection growing with the sample inside a *sealed* document grows the tamper audit with it.

**The funnel and the capacity do not enter the attribution grid, and that is a decision rather
than an omission.** `AttributionStatistic` is closed on the two *performance* readings -- did the
ordering keep predicting, did the money survive -- and every rung of `AttributionVerdict` is built
for a statistic whose sign and scale are a factor's edge. An execution rate is neither: it lives
in `[0, 1]` where zero is a loud and legitimate answer rather than "there was nothing to keep", so
`no_baseline` would fire on the sharpest finding this module has; and `amplified` on a ratio of
two rates would say "the neutralisation made the factor easier to trade", which is a true sentence
about a different question. The three tiers' funnels are nonetheless directly comparable -- they
are three rows of one artifact over one sample -- so a reader who wants the step reads the two
rows, which is exactly what a grid cell exists to spare them **when the ladder means something**
and not otherwise. Carried as
`the_attribution_grid_is_over_two_performance_statistics_and_not_over_tradeability`.

`survival` is the fourth upstream module arriving where it belongs. `factor_redundancy` is a
*pair* analysis, and `_refuse_a_pair_that_is_neither_two_factors_nor_two_tiers` says outright that
"a cross-TIER pair of one factor is the supported reading and says how much of it survived the
transform". So the pair this artifact forms is one factor's `raw` against its own processed and
neutralised values -- and the raw tier carries `None`, because raw against raw is the self-pair
that same function refuses. The absence is forced by the upstream contract rather than chosen
here, which is why it is a `None` and not an omission.

**The grid is the part that answers the acceptance criterion.** `TierAttribution` is one
`(step, statistic)` cell:

    steps        (raw -> processed), (processed -> neutralized), (raw -> neutralized)
    statistics   mean_ic, mean_spread

Six cells, always all six, in declared order -- `ICCensus.excluded_by_coverage`' rule, for its
reason: a cell missing from the tuple and a cell present with `not_measured` are different claims.
Each cell carries the two tiers' own numbers, their ratio, and a verdict:

    retention = to_value / from_value

    not_measured   one of the two tiers has no statistic at all
    no_baseline    both measured and from_value <= 0: the earlier tier did not work, so there is
                   nothing for the later one to have kept
    reversed       to_value < 0: the step turned the bet around
    amplified      retention > 1: the step made the statistic larger
    removed        retention < the declared floor  <-- the acceptance criterion's cell
    survives       otherwise

`no_baseline` is split from `not_measured` rather than pooled, and the split is load-bearing
rather than tidy: a factor whose raw mean IC is `-0.05` and whose neutralised one is `0.00` has a
retention of zero and is **not** a factor whose edge was exposure -- it had no edge. Pooling the
two would put the loudest verdict this module can produce on a factor that never worked, which is
the exact shape of "only verified existence and not magnitude".
`test_a_factor_that_never_worked_reports_no_baseline_rather_than_removed` drives it.

**Both statistics arrive already oriented and are not oriented again.** `ICSummary.mean_ic` is
over `ICPoint.ic`, which `FactorICSpec.orient` already signed, and
`QuantilePortfolioSummary.mean_spread` is the oriented `long_short_spread`. A second orientation
would negate a `lower_is_better` factor twice and land back on the raw sign with nothing saying
so, which is the mistake `ICSeriesCorrelation` names in its own docstring.

**The verdict is not constructible if it is wrong.** `TierAttribution` carries the
`retention_floor` it was decided under -- a projection of the spec, like
`FactorBuildManifest.direction` is a projection of `factor_id` -- and its validator recomputes
both the ratio and the ladder and refuses a disagreement. `ICPoint`'s orientation validator is the
precedent, and the failure it closes is the same one: a report that could say `survives` about a
number that fell to zero.

## Four upstream specs, four sets of floors, and one artifact

Each of the four modules declares its own sample floor and each floor's *arithmetic* minimum is a
different number for a different reason -- `MINIMUM_IC_SECURITIES` is 3 because two points always
correlate perfectly, `MINIMUM_REDUNDANCY_SECURITIES` is 4 because a threshold over three ranks
decides nothing, `MINIMUM_GROUP_SECURITIES` is 1 because that is when a group return exists at
all, and `MINIMUM_REBALANCES` is 1 because that is when a transition exists. So this artifact
carries **all four specs whole** and declares no floor of its own. A single `min_securities` would
have to be one number standing in for four incompatible ones, and whichever it was would silently
move at least three of the four modules' verdicts away from what their own contracts decided.

The floors are in the identity precisely because they move the answers: two experiments over one
factor at two `min_securities` are two experiments, not one experiment reported twice.

## What a shortfall looks like in one artifact, and the vocabulary this module does **not** add

Six refusal vocabularies arrive here -- `ICStabilityCoverage`, `SeriesCoverage`,
`TurnoverCoverage`, `SummaryCoverage`, plus the per-`as_of` codes already folded into them -- and
**this module adds none of its own for them**. Every tier report carries its four upstream objects
with their own codes intact, and `TierReport.coverage_codes` reads the four out side by side
without collapsing them. The only place a shortfall is *synthesised* is the attribution grid, and
there it is one code, `not_measured`, with the two tiers' own codes still readable beside it.

That is a deliberate refusal to build a fifth "N/A". A single artifact-level "insufficient" would
make "the IC series was two as_ofs short" and "the rebalance schedule overlapped so no holdings
state exists" one finding with one remedy, and they are two findings with two remedies -- which is
the argument `FactorCoverage` spent five members on and `TurnoverCoverage` three.

## Where an artifact lives, and why `V2-P3-002`'s storage prohibition does not transfer

**Not the panel plane.** `panel_factors` files one row per `(security, as_of)` and one manifest row
per build, and the `factor_*` datasets' `subject` column already carries two meanings -- a security
on the observation partitions and a `manifest_id` on the manifest ones. An artifact keyed by
`experiment_id` would be a third, on a column two write guards already read.

**Not the evidence plane either, and the reason is layering rather than type.** `V2-P3-002`
established that a `FactorObservation` may not be handed to `ParquetEvidenceStore`, and its
argument is structural: that store takes `EvidenceSnapshot`s, there is no adapter anywhere in the
tree, and no `panel_*` module reaches `openalpha_cn.storage` or `openalpha_cn.evidence` on the live
import graph. **That argument does not transfer to a report.** `EvidenceSnapshot.kind` is a plain
bounded string, so `EvidenceSnapshot(kind="factor_experiment", ...)` *is* constructible and the
store would accept it -- the type boundary that holds for an observation simply is not there for a
document. What stops this module is that `backtest/` may not import `storage/` at all, and a
factor's public face is `V2-P3-015` rather than this issue.

So the artifact is delivered as a **document**: `experiment_payload` renders a record as canonical
JSON on `stable_model_id`'s own canonicalisation and `open_experiment` reads one back, refusing a
payload whose content no longer hashes to the seal it carries. Any byte store can hold it, and
`nothing_in_this_module_stores_an_artifact_or_can_be_made_to` records what having none costs.

## The one product constraint a reader of the neutralised tier has to be told

**A neutralised residual is read at a year-end snapshot, not at the `as_of` a caller asked for**,
and this artifact cannot fix it -- it can only refuse to hide it.
`panel_neutralization.neutralized_observation_batch` stamps all four clocks of every residual row
with the build's own `as_of`, and that `as_of` is at or after the `max_available_time` of the
year's `daily_basic` partition, which is the year's last session. Reads go through
`PanelStore.read_visible_at`, so **any `as_of` inside the covered year reads back nothing rather
than raising**: a neutralised tier assembled at in-year as_ofs is not an error, it is an
`ICSummary` whose `measured_count` is zero.

The residuals' *content* is clean -- each was regressed against the industry, the market
capitalisation and the processed value of its own day -- so a neutralised IC is not
forward-contaminated. What is lost is the honesty of the timestamps, so the neutralised row of
this report cannot claim to be point-in-time the way the raw and processed rows can, and **the
neutralised row is exactly the row the acceptance criterion turns on**. `V2-P4-026` (an
as-of-sensitive session-level read of `daily_basic`) is the fix and is a hard precondition of
`V2-P4-013`. Carried here as `neutralised_residuals_are_read_at_a_year_end_snapshot`, the same
code `V2-P3-005` and `V2-P3-006` carry, because it is the same fact and renaming it per module
would make it three.

## Layering, and why there is no numpy here

`backtest/` over `domain/` plus this package's own four `backtest/factor_*` leaves. Every number
in this module is a ratio of two floats a caller already computed and a digest of a JSON document;
ADR-0003's Update sections record six same-shape conclusions and there is no workload here that
argues with any of them. The one arithmetic decision is that `retention` is computed as a plain
quotient with no guard, because both operands are validated finite by their own contracts and the
denominator is ruled out as non-positive one branch above.
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIER_ORDER,
    FactorICSpec,
    FactorTier,
    ICSummary,
)
from openalpha_cn.backtest.factor_portfolio import QuantilePortfolioSpec, QuantilePortfolioSummary
from openalpha_cn.backtest.factor_redundancy import RedundancySpec, RedundancySummary
from openalpha_cn.backtest.factor_tradeability import (
    TradeabilitySpec,
    TradeabilitySummary,
    TurnoverSeries,
)
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.factor import FactorDefinition, FactorDirection, FactorNote, set_digest
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "ATTRIBUTION_CELL_ORDER",
    "ATTRIBUTION_STATISTIC_ORDER",
    "ATTRIBUTION_STEPS",
    "ATTRIBUTION_VERDICT_CODES",
    "ATTRIBUTION_VERDICT_ORDER",
    "EXPERIMENT_LIMITATION_CODES",
    "IDENTITY_EXEMPT_PARAMETERS",
    "KNOWN_EXPERIMENT_LIMITATIONS",
    "RETENTIONLESS_VERDICTS",
    "AttributionStatistic",
    "AttributionVerdict",
    "ExperimentLimitation",
    "FactorExperimentArtifact",
    "FactorExperimentError",
    "FactorExperimentRecord",
    "FactorExperimentSpec",
    "TierAttribution",
    "TierReport",
    "build_factor_experiment",
    "experiment_payload",
    "open_experiment",
    "refuse_a_restated_experiment",
]


class FactorExperimentError(ValueError):
    """Raised for a malformed experiment, artifact or ledger.

    A `ValueError` subclass for `FactorICError`'s reason: every call site that already writes
    `except ValueError` keeps catching it unchanged. It is deliberately **not** what a tier that
    measured nothing produces -- that is an upstream coverage code travelling intact, and the
    attribution grid reports it as `not_measured`, because an experiment over a year in which one
    tier never cleared its floor is a finding and not a malformed question.
    """


AttributionStatistic = Literal["mean_ic", "mean_spread"]
"""Which of the two performance readings a tier step is attributed on.

- **`mean_ic`** -- `ICSummary.mean_ic`, the mean **oriented** information coefficient. Answers
  "did the ordering keep predicting".
- **`mean_spread`** -- `QuantilePortfolioSummary.mean_spread`, the mean **oriented**
  long-short spread net of the fees an A-share round trip actually pays. Answers "did the money
  survive".

Both, and not one, because "因子有效" has two readings that come apart: a factor whose ordering
survives neutralisation and whose *spread* does not is a factor whose surviving names cost more
to trade, which `V2-P3-006`'s cost drag and `V2-P3-007`'s funnel are there to explain. Closed for
`ICMethod`'s reason: `ATTRIBUTION_CELL_ORDER` is a declared grid, and a third spelling of
"performance" would silently become a row of one.
"""

ATTRIBUTION_STATISTIC_ORDER: Final[tuple[AttributionStatistic, ...]] = get_args(
    AttributionStatistic
)
"""The declared order, which is the order the grid lays the two out in."""

ATTRIBUTION_STEPS: Final[tuple[tuple[FactorTier, FactorTier], ...]] = (
    ("raw", "processed"),
    ("processed", "neutralized"),
    ("raw", "neutralized"),
)
"""The three tier steps a report attributes over, in declared order.

The two links of the chain and then the whole chain, and all three are here because they answer
different questions with different remedies:

- **`raw -> processed`** -- what winsorization, standardization and the missing-value policy did.
  A statistic that vanishes here was in the tails or in the rows the transform imputed, and the
  remedy is a different `FactorTransformSpec`.
- **`processed -> neutralized`** -- what regressing out industry and market capitalisation did.
  **This is the step the roadmap's annotation is about**: a statistic that vanishes here was the
  exposure, and no transform setting recovers it.
- **`raw -> neutralized`** -- the end to end. Carried rather than left to be multiplied because
  the composition of two ratios is not the ratio of the composition once either link is
  `not_measured`, and a reader who multiplied two cells would get a number for a chain one of
  whose links has no number at all.

`raw -> neutralized` is not `raw -> processed` times `processed -> neutralized` even when all
three are measured, because the neutralised tier's admitted set is its own -- `imputed` rows carry
a number on the processed tier and are not admitted into any statistic, and the residual plane's
vocabulary is a third one again. `tests/unit/backtest/test_factor_experiment.py::
test_the_end_to_end_step_is_not_the_product_of_the_two_links` pins that the module does not
compute it that way.
"""

ATTRIBUTION_CELL_ORDER: Final[tuple[tuple[FactorTier, FactorTier, AttributionStatistic], ...]] = (
    tuple(
        (source, target, statistic)
        for source, target in ATTRIBUTION_STEPS
        for statistic in ATTRIBUTION_STATISTIC_ORDER
    )
)
"""Every cell of the grid, in declared order: step-major, statistic-minor.

`FactorExperimentArtifact` requires its `attributions` to be exactly this tuple of keys in exactly
this order -- `ICCensus.excluded_by_coverage`' rule one plane up, for its reason. A grid that
dropped the cells it had nothing to say about would make "the neutralised tier measured nothing"
and "nobody asked about the neutralised tier" one reading.
"""

AttributionVerdict = Literal[
    "not_measured",
    "no_baseline",
    "reversed",
    "amplified",
    "removed",
    "survives",
]
"""What one tier step did to one statistic, in the order `_attribution_verdict` decides them.

- **`not_measured`** -- one of the two tiers carries no statistic at all, because its own coverage
  code is not `measured`. Decided first: every question below is a question about two numbers.
- **`no_baseline`** -- both tiers measured, and the **earlier** one's statistic is at or below
  zero. There is nothing for the later tier to have kept, and reporting a retention here would put
  this module's loudest verdict on a factor that never worked. See this module's docstring.
- **`reversed`** -- the later tier's statistic is negative. The step turned the bet around, which
  is a different finding from shrinking it: a factor that predicts backwards after neutralisation
  is not a factor whose edge was exposure.
- **`amplified`** -- `retention > 1`. The step made the statistic larger, which is the reading a
  factor whose exposure was working *against* it produces.
- **`removed`** -- `retention` below the declared floor. **The acceptance criterion's cell**: on
  the `processed -> neutralized` step this is "what it earned was the industry and the size
  exposure", stated as a code rather than as a comparison the reader performs.
- **`survives`** -- otherwise, which is `retention` in `[floor, 1]`.

Closed rather than a float plus a boolean, for `RedundancyVerdict`'s reason: a report groups by
this and a seventh spelling of "it fell" would silently become a group of one.
"""

ATTRIBUTION_VERDICT_CODES: Final[frozenset[str]] = frozenset(get_args(AttributionVerdict))

ATTRIBUTION_VERDICT_ORDER: Final[tuple[AttributionVerdict, ...]] = get_args(AttributionVerdict)

RETENTIONLESS_VERDICTS: Final[frozenset[str]] = frozenset({"not_measured", "no_baseline"})
"""The two verdicts that carry no `retention`, declared rather than inferred from the code.

Both are "there is no ratio here", and they are two codes because the remedies differ -- one is
answered by measuring more as_ofs and the other is not answered at all. `TierAttribution`'s
validator makes membership here exactly the condition under which `retention` is `None`, so a cell
that reported a ratio it could not have computed is not constructible.
"""

_ROW_STUDIES: Final[tuple[str, ...]] = ("ic", "portfolio", "turnover", "tradeability")
"""The four fields of a tier report that declare a tier, a factor, a direction and a horizon.

A tuple rather than four literals repeated in four comprehensions, so that a fifth upstream
summary joins every one of `TierReport`'s cross-checks at once. `survival` is deliberately absent:
it names two factors and two tiers rather than one of each, and its own pair rule is
`_validate_the_survival_pair`.
"""

MINIMUM_EXPERIMENT_TIERS: Final[int] = 3
"""How many tier reports an artifact carries, which is every declared tier and not a choice.

D8 asks for a comparison of raw, processed **and** neutralised performance, and a report that
admitted two of the three would be one whose missing row could not be told from a tier that
measured nothing. `FACTOR_TIER_ORDER` is the authority on which three and in what order; this
constant exists so the arity is asserted against something other than the literal `3`, and
`tests/unit/backtest/test_factor_experiment.py::test_the_artifact_carries_every_declared_tier_once`
holds it against that tuple's own length.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentLimitation:
    """One named boundary on what a stored experiment artifact can be trusted to answer."""

    code: str
    detail: str


KNOWN_EXPERIMENT_LIMITATIONS: Final[tuple[ExperimentLimitation, ...]] = (
    ExperimentLimitation(
        code="neutralised_residuals_are_read_at_a_year_end_snapshot",
        detail=(
            "The neutralised row of this report is not a point-in-time series the way the raw and "
            "processed rows are, and it is the row the acceptance criterion turns on. "
            "panel_neutralization.neutralized_observation_batch stamps all four clocks of every "
            "residual row with the build's own as_of, and that as_of is at or after the "
            "max_available_time of the year's daily_basic partition -- the year's last session. "
            "Reads go through PanelStore.read_visible_at, so an as_of inside the covered year "
            "reads back nothing rather than raising, which arrives here as a neutralised tier "
            "whose ICSummary coverage is insufficient_as_ofs and whose attribution cells are all "
            "not_measured. The residuals' CONTENT is clean -- each was regressed against the "
            "industry, the market capitalisation and the processed value of its own day -- so "
            "nothing here is forward-contaminated; what is lost is the honesty of the timestamps. "
            "V2-P4-026 (an as-of-sensitive session-level read of daily_basic) is the fix and is a "
            "hard precondition of V2-P4-013. The same code V2-P3-005 and V2-P3-006 carry, because "
            "it is the same fact."
        ),
    ),
    ExperimentLimitation(
        code="the_seal_detects_an_edit_and_does_not_authenticate_one",
        detail=(
            "FactorExperimentRecord.sealed_digest is a sha256 of the artifact's own canonical "
            "JSON and its validator refuses a record whose artifact no longer hashes to it. That "
            "catches an edit and a truncation; it does not catch a rewrite, because whoever edits "
            "the artifact can recompute the digest beside it. A digest is not a signature and "
            "this repository holds no key material, so the property claimed is integrity against "
            "accident and against a partial write, not authenticity against an author. The "
            "stronger property needs a signing key and a place to keep it, and neither exists "
            "here."
        ),
    ),
    ExperimentLimitation(
        code="an_attribution_is_a_difference_between_two_declared_tiers_and_not_a_controlled_test",
        detail=(
            "A `removed` verdict on the processed -> neutralized step says the statistic did not "
            "survive THAT STEP, and the step is the whole declared FactorNeutralizationSpec -- a "
            "cross-sectional regression on industry dummies and a size term, over the "
            "participation rule that spec declares, with its own admitted-code vocabulary. It is "
            "not a controlled experiment in which one exposure was varied and nothing else was. "
            "The raw -> processed step is broader still: winsorization, standardization and the "
            "missing-value policy move together and this module cannot say which of the three a "
            "vanished statistic went into. Reading a cell as 'industry and size explain this "
            "factor' is therefore a reading of the declared spec beside it, not of the verdict "
            "alone."
        ),
    ),
    ExperimentLimitation(
        code="the_retention_ratio_carries_no_test_of_the_difference_between_two_means",
        detail=(
            "retention is a quotient of two means, each taken over a series of overlapping "
            "windows. factor_ic publishes no t-statistic and no confidence interval because at "
            "horizon h two prediction days one session apart share h of the h + 1 sessions each "
            "spans, so the ICs are not independent draws; factor_portfolio inherits the same "
            "refusal for its spread. A ratio of two such means inherits it twice over and adds a "
            "second dependence the components do not have -- the numerator and the denominator "
            "are computed on THE SAME DAYS from three tiers of one factor, so they move together "
            "by construction. Nothing here says whether a retention of 0.4 is distinguishable "
            "from 1.0 at any sample size, and the declared floor is a policy line rather than a "
            "test. De-overlapping needs a purge, a purge needs a train/test split, and nothing in "
            "this repository defines one yet."
        ),
    ),
    ExperimentLimitation(
        code="the_attribution_grid_is_over_two_performance_statistics_and_not_over_tradeability",
        detail=(
            "ATTRIBUTION_CELL_ORDER is three steps by the two members of AttributionStatistic, "
            "and every tier report also carries a TradeabilitySummary whose funnel, per-group "
            "execution rates and capital multiple are NOT attributed. That is a decision and the "
            "grid would be wrong if they were. The verdict ladder is built for a statistic whose "
            "sign is a factor's edge: no_baseline fires when from_value <= 0, which for an "
            "execution rate is the sharpest finding this artifact can carry rather than 'there "
            "was nothing to keep'; amplified fires above a retention of 1, which on a ratio of "
            "two rates in [0, 1] says the neutralisation made the factor easier to trade -- a "
            "true sentence about a question the acceptance criterion is not asking; and removed, "
            "the criterion's own cell, would then mean two incompatible things depending on which "
            "column a reader was in. What is delivered instead is three comparable rows over one "
            "sample: raw, processed and neutralised funnels of the same factor over the same days, "
            "with the counts under every rate, so the step is a subtraction the reader performs "
            "deliberately rather than a verdict this module asserts. Nothing here says whether a "
            "long-group execution rate that fell between two tiers fell by an amount that matters, "
            "and no floor in FactorExperimentSpec governs it."
        ),
    ),
    ExperimentLimitation(
        code="nothing_in_this_module_stores_an_artifact_or_can_be_made_to",
        detail=(
            "refuse_a_restated_experiment is the write-once half of immutability and it is a "
            "function over a collection the CALLER holds, not a guard on a store. This module is "
            "a backtest/ leaf: it may not import panel/ or storage/, and "
            "tests/unit/panel/test_visible_read_callers.py already asserts on the live import "
            "graph that no panel_* module reaches the evidence plane either. So an artifact that "
            "is never offered to the refusal is never checked by it, exactly as a factor build "
            "that is never offered to write_factor_panels is never checked by "
            "_refuse_to_drop_a_stored_build. What this module delivers is the unit a write path "
            "can address -- one experiment_id, one content_digest, one sealed record -- and not "
            "the write path."
        ),
    ),
)
"""What a stored experiment artifact does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

EXPERIMENT_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_EXPERIMENT_LIMITATIONS
)

IDENTITY_EXEMPT_PARAMETERS: Final[dict[str, str]] = {
    "built_at": (
        "The wall clock at which an experiment was assembled is not part of what the experiment "
        "IS: recomputing the same tiers from the same builds at the same commit must reproduce "
        "the same experiment_id, or the identity cannot be used to detect drift. "
        "FactorBuildManifest.built_at is out of manifest_id for exactly this reason, and "
        "FactorInputProvenance is the precedent for recording a wall clock beside an identity "
        "rather than inside it. Carried on FactorExperimentRecord and out of both digests."
    ),
    "note": (
        "Prose decides nothing, so a typo fix in it must move no identity. FactorDefinition, "
        "FactorTransformSpec and FactorNeutralizationSpec each carried a `summary` inside their "
        "own content addresses until the change that landed the report-period axis, which made "
        "fixing a typo move the identity of every stored build without changing a number. "
        "FactorNote is where that prose went and this is the same arrangement: a note is carried "
        "on the record, beside the artifact, and is not a field of anything hashed."
    ),
}
"""The `build_factor_experiment` parameters that deliberately move no identity, with the reason.

The table `tests/unit/backtest/test_factor_experiment.py::
test_every_parameter_of_the_builder_moves_the_identity_or_is_exempted_by_name` partitions the
builder's own signature against. Every other parameter must be *measured* to move `experiment_id`,
so a tenth parameter fails the audit until somebody classifies it -- `V2-P3-002`'s shape, which
that issue's review installed against `compute_factor` after two determinants of the answers
turned out never to have reached `manifest_id` at all.

A `dict` rather than a `frozenset` because the reason is the point: an exemption with no argument
beside it is the shape roadmap section 9 measured, where `config_digest` was *believed* to feed
`decision_id` and did not.
"""


def _attribution_verdict(
    *, source_value: float | None, target_value: float | None, floor: float
) -> tuple[AttributionVerdict, float | None]:
    """The ladder in `AttributionVerdict`'s declared order, and the ratio it decided on.

    Returned together rather than computed twice, because the two are one decision: the ratio
    exists exactly under the four verdicts that are not in `RETENTIONLESS_VERDICTS`, and a
    function returning only the code would leave the caller to re-derive the number under a
    condition it would have to restate.

    The quotient is taken with no guard because the branch above it has ruled out the only two
    ways it could fail: `source_value` is not `None` and is strictly positive. Both operands are
    already finite -- `ICSummary` and `QuantilePortfolioSummary` each refuse a non-finite
    statistic at their own validators -- so the result is finite unless it overflows, which two
    correlations in `[-1, 1]` and two spreads bounded by their own arithmetic cannot do.
    """
    if source_value is None or target_value is None:
        return "not_measured", None
    if source_value <= 0.0:
        return "no_baseline", None
    retention = target_value / source_value
    if target_value < 0.0:
        return "reversed", retention
    if retention > 1.0:
        return "amplified", retention
    if retention < floor:
        return "removed", retention
    return "survives", retention


class TierReport(BaseModel):
    """One tier's upstream answers, whole, with nothing re-derived and nothing collapsed.

    A pydantic model rather than a dataclass, unlike the per-security carriers one plane down:
    there are three of these per experiment rather than one per `(security, as_of)`, so what is
    wanted at that scale is the validator -- and the validator is most of the point. Five objects
    produced by four separate studies can disagree about which factor, which tier, which horizon
    and which days they are about, and a report that put five such objects in one row would be
    five answers to five questions wearing one heading.

    Every cross-check below has a failure that was reachable before it, and `_ROW_STUDIES` is the
    table the first four run over so a sixth summary joins all of them at once:

    - **One tier.** Every summary in `_ROW_STUDIES` reports this report's own.
    - **One factor.** All four `factor_id`s agree, and `survival` names that factor on both
      sides -- it is a *cross-tier self-pair*, which is the one shape `factor_redundancy`
      supports for one factor.
    - **One horizon.** `ic.horizon_sessions` and the three portfolio-side ones agree. An IC at
      five sessions beside a spread at sixty is two windows in one row, and a coverage funnel at a
      third is a `label_rate` about a window neither of them measured.
    - **One sample.** All five carry `as_ofs`, and they must be the same tuple.
      `_refuse_rungs_over_different_samples` is the precedent one plane down and the argument is
      unchanged: a statistic that fell between two tiers is a finding only if the two tiers were
      asked about the same days.
    - **One cut, and one long group inside it.** `portfolio.group_count`,
      `turnover.group_count` and `tradeability.group_count` agree, the turnover series follows a
      group index inside that cut, and the tradeability summary follows the same one -- a capacity
      for one portfolio beside the churn of another is two trades in one row.

    `survival` is `None` on the raw tier and required on the other two, and that asymmetry is
    forced rather than chosen: `factor_redundancy._refuse_a_pair_that_is_neither_two_factors_nor
    _two_tiers` refuses one factor on one tier against itself, so a raw-against-raw summary is not
    constructible at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tier: FactorTier
    source_manifest_ids: tuple[str, ...] = Field(min_length=1)
    """The stored builds this tier was read from -- `manifest_id`s on the raw tier,
    `transform_manifest_id`s on the processed one, `neutralization_manifest_id`s on the
    neutralised one.

    In the identity through `FactorExperimentSpec`'s per-tier digest, because *which stored build
    was read* is a determinant of every number in this row and a spec that recorded only the
    policies would give two experiments over two different builds one identity -- which is the
    collision `FactorBuildManifest.subject_digest` was added to close, arriving one plane up.

    Distinct and ascending, so two callers who assembled the same set in two orders produce one
    digest and one identity rather than two.
    """
    ic: ICSummary
    portfolio: QuantilePortfolioSummary
    turnover: TurnoverSeries
    tradeability: TradeabilitySummary
    """`V2-P3-007`'s other half: the coverage funnel, the per-group execution decomposition and
    the long group's capacity, pooled over this tier's own periods.

    Carried beside `turnover` rather than folded into it, because the two answer different
    questions with different refusal vocabularies -- `TurnoverCoverage` is about whether a
    holdings state exists at all and `TradeabilityCoverage` is about whether any period reached
    the cut -- and a row that merged them would need a fifth "N/A" of this module's own, which is
    the thing this module's docstring refuses to build.

    **This is the field the roadmap's `V2-P3-007` annotation is delivered through.** Its brief --
    *make the signals that are statistically attractive and not implementable visible* -- is a
    statement about a report, and until this field existed the whole instrument was unreachable:
    `TradeabilityStudy.measure` had no caller anywhere in `src/`, so `CoverageFunnel`,
    `GroupCapacity` and `SessionLiquidity` reached no artifact and
    `TradeabilitySpec.participation_cap` was a required option that moved an experiment's identity
    and not one of its numbers.
    """
    survival: RedundancySummary | None
    """`raw` against this tier, cross-sectionally: how much of the ordering the transform left.

    `None` on the raw tier and required on the other two. Carried because a retention and a
    survival answer different questions and can disagree in the way that matters: a tier whose
    values still rank the market almost identically to raw and whose mean IC collapsed is a
    contradiction a reader should see, and one whose values were reordered wholesale is a tier
    whose lost IC is at least consistent with the transform having done something.
    """

    @model_validator(mode="after")
    def validate_the_four_studies_are_about_one_thing(self) -> Self:
        for name in _ROW_STUDIES:
            reported: FactorTier = getattr(self, name).tier
            if reported != self.tier:
                raise ValueError(
                    f"this report is filed under the {self.tier!r} tier and its {name} summary "
                    f"reports {reported!r}; a row that mixes tiers is the one thing a three-tier "
                    "report exists to keep apart"
                )
        factor_ids = {getattr(self, name).factor_id for name in _ROW_STUDIES}
        if len(factor_ids) != 1:
            raise ValueError(
                f"this tier report's studies name {sorted(factor_ids)}; one row is one factor, "
                "and four studies of two factors in one row is a comparison wearing a heading"
            )
        directions = {getattr(self, name).direction for name in _ROW_STUDIES}
        if len(directions) != 1:
            raise ValueError(
                f"this tier report's studies declare {sorted(directions)}; the direction decides "
                "the sign of the IC and which group is long, so two of them in one row make the "
                "row's numbers point in two ways"
            )
        horizons = {getattr(self, name).horizon_sessions for name in _ROW_STUDIES}
        if len(horizons) != 1:
            raise ValueError(
                f"this tier report's studies are at {sorted(horizons)} session(s); an IC at one "
                "horizon beside a spread at another is two windows in one row"
            )
        cuts = {self.portfolio.group_count, self.turnover.group_count}
        if len(cuts | {self.tradeability.group_count}) != 1:
            raise ValueError(
                f"the quantile summary cuts into {self.portfolio.group_count} group(s), the "
                f"turnover series into {self.turnover.group_count} and the tradeability summary "
                f"into {self.tradeability.group_count}; one row is one cut"
            )
        if not 0 <= self.turnover.group < self.portfolio.group_count:
            raise ValueError(
                f"the turnover series follows group {self.turnover.group} of a cut of "
                f"{self.portfolio.group_count}; a rolling portfolio has to be one of the groups "
                "the quantile study reports"
            )
        if self.tradeability.group != self.turnover.group:
            raise ValueError(
                f"the tradeability summary reports group {self.tradeability.group} and the "
                f"turnover series follows {self.turnover.group}; both are the long group this "
                "factor's declared direction picks, so two of them in one row is a capacity for "
                "one portfolio beside the churn of another"
            )
        self._validate_one_sample()
        self._validate_the_survival_pair()
        ids = list(self.source_manifest_ids)
        if len(set(ids)) != len(ids) or ids != sorted(ids):
            raise ValueError(
                f"source_manifest_ids is {ids}; the builds a tier was read from have to be "
                "distinct and ascending, or two callers who assembled one set in two orders would "
                "produce two experiment identities for one experiment"
            )
        if any(not build.strip() for build in ids):
            raise ValueError("a source build id cannot be blank")
        return self

    def _validate_one_sample(self) -> None:
        """Refuse a row whose four studies were asked about different days."""
        offered = self.ic.as_ofs
        for name in ("portfolio", "turnover", "tradeability"):
            other: tuple[datetime, ...] = getattr(self, name).as_ofs
            if other != offered:
                raise ValueError(
                    f"the ic summary was offered {len(offered)} as_of(s) and the {name} summary "
                    f"{len(other)}; the four studies in one tier report have to be asked about "
                    "the same days, or a statistic that moved between two tiers cannot be told "
                    "from a sample that moved underneath it"
                )
        if self.survival is not None and self.survival.as_ofs != offered:
            raise ValueError(
                f"the ic summary was offered {len(offered)} as_of(s) and the survival summary "
                f"{len(self.survival.as_ofs)}; a survival correlation over other days says "
                "nothing about the row it sits in"
            )

    def _validate_the_survival_pair(self) -> None:
        """Refuse a survival summary that is not this factor's raw tier against this one."""
        if (self.survival is None) != (self.tier == "raw"):
            raise ValueError(
                f"the {self.tier!r} tier report carries "
                f"{'no' if self.survival is None else 'a'} survival summary; exactly the raw tier "
                "carries none, because factor_redundancy refuses one factor on one tier against "
                "itself and every other tier's survival is that tier against raw"
            )
        if self.survival is None:
            return
        factor_id = self.ic.factor_id
        if self.survival.left_factor_id != factor_id or self.survival.right_factor_id != factor_id:
            raise ValueError(
                f"the survival summary pairs {self.survival.left_factor_id!r} with "
                f"{self.survival.right_factor_id!r} and this row is about {factor_id!r}; a "
                "survival correlation is one factor's raw tier against its own transformed one"
            )
        if self.survival.left_tier != "raw" or self.survival.right_tier != self.tier:
            raise ValueError(
                f"the survival summary correlates {self.survival.left_tier!r} against "
                f"{self.survival.right_tier!r} and this row is the {self.tier!r} tier; the pair "
                "has to be raw on the left and this tier on the right, or the number is not the "
                "share of the ordering this transform left"
            )

    @property
    def factor_id(self) -> str:
        return self.ic.factor_id

    @property
    def direction(self) -> FactorDirection:
        return self.ic.direction

    @property
    def horizon_sessions(self) -> int:
        return self.ic.horizon_sessions

    @property
    def as_ofs(self) -> tuple[datetime, ...]:
        """Every `as_of` this row's studies were offered, which is one tuple by the validator."""
        return self.ic.as_ofs

    @property
    def coverage_codes(self) -> tuple[tuple[str, str], ...]:
        """Each upstream study's own coverage code, side by side and **not** collapsed.

        Four cells on the processed and neutralised tiers and three on the raw one, keyed by the
        study rather than by a vocabulary of this module's own. That is the whole answer to how
        four refusal vocabularies coexist in one artifact: they are not reconciled, they are
        reported where they were decided. A single artifact-level "insufficient" would make "the
        IC series was two as_ofs short" (`insufficient_as_ofs`) and "the rebalance schedule
        overlapped so no holdings state exists" (`overlapping_schedule`) one finding with one
        remedy, and they are two findings with two remedies.
        """
        codes: list[tuple[str, str]] = [
            ("ic", self.ic.coverage),
            ("portfolio", self.portfolio.coverage),
            ("turnover", self.turnover.coverage),
            ("tradeability", self.tradeability.coverage),
        ]
        if self.survival is not None:
            codes.append(("survival", self.survival.coverage))
        return tuple(codes)

    def statistic(self, statistic: AttributionStatistic) -> float | None:
        """This tier's reading of one attributed statistic, already oriented by its own study.

        Not oriented again here, and that is stated rather than left implicit: `ICSummary.mean_ic`
        is a mean of `ICPoint.ic`, which `FactorICSpec.orient` signed, and
        `QuantilePortfolioSummary.mean_spread` is the mean oriented `long_short_spread`. A second
        orientation would negate a `lower_is_better` factor twice and land back on the raw sign
        with nothing saying so, which is the mistake `ICSeriesCorrelation` names in its own
        docstring.
        """
        if statistic == "mean_ic":
            return self.ic.mean_ic
        return self.portfolio.mean_spread


class TierAttribution(BaseModel):
    """One `(step, statistic)` cell of the grid: two tiers' numbers, their ratio, and a verdict.

    The validator is the contract, and it makes a wrong cell **not constructible** rather than
    merely discouraged -- `ICPoint`'s orientation validator is the precedent and the failure it
    closes is the same shape. Three relationships are held:

    1. `retention` is present under exactly the verdicts that are not in `RETENTIONLESS_VERDICTS`.
    2. When present it is exactly `to_value / from_value`, compared with `==` rather than with a
       tolerance because it is recomputed by the same expression on the same two floats.
    3. The verdict is the one `_attribution_verdict` gives for these two values under this cell's
       own `retention_floor`.

    Without the third, `from_value=0.08, to_value=0.0, verdict="survives"` builds, and a report
    reading the verdict would say the factor kept working when the measurement says its entire
    edge was the exposure. That is the acceptance criterion failing silently, so it is refused at
    the type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    from_tier: FactorTier
    to_tier: FactorTier
    statistic: AttributionStatistic
    retention_floor: float = Field(gt=0.0, le=1.0)
    """The line this cell's verdict was decided at, carried here as a projection of
    `FactorExperimentSpec.retention_floor`.

    A projection for readability and never a second source of truth:
    `FactorExperimentArtifact` requires every cell's floor to be the spec's own, so the two cannot
    disagree. `FactorBuildManifest.direction` is the same arrangement for the same reason -- a
    reader holding one cell should not have to resolve the spec to learn what `removed` meant.
    """
    from_value: float | None
    to_value: float | None
    retention: float | None
    verdict: AttributionVerdict

    @field_validator("from_value", "to_value", "retention")
    @classmethod
    def refuse_a_non_finite_number(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError(
                f"{value!r} is not a finite number; the two statistics arrive from contracts that "
                "each refuse a non-finite one, so a non-finite here is a cell computed off "
                "contract"
            )
        return value

    @model_validator(mode="after")
    def validate_the_verdict_is_the_one_the_ladder_gives(self) -> Self:
        if (self.from_tier, self.to_tier) not in ATTRIBUTION_STEPS:
            raise ValueError(
                f"({self.from_tier!r}, {self.to_tier!r}) is not a declared attribution step; the "
                f"declared ones are {[list(step) for step in ATTRIBUTION_STEPS]}"
            )
        if (self.retention is None) != (self.verdict in RETENTIONLESS_VERDICTS):
            raise ValueError(
                f"verdict {self.verdict!r} carries retention {self.retention!r}; exactly "
                f"{sorted(RETENTIONLESS_VERDICTS)} carry none, because those are the two codes "
                "under which there is no ratio to take"
            )
        verdict, retention = _attribution_verdict(
            source_value=self.from_value,
            target_value=self.to_value,
            floor=self.retention_floor,
        )
        if verdict != self.verdict or retention != self.retention:
            raise ValueError(
                f"this cell reports {self.from_value!r} -> {self.to_value!r} at a floor of "
                f"{self.retention_floor!r} as {self.verdict!r} with retention {self.retention!r}, "
                f"and the declared ladder gives {verdict!r} with {retention!r}; a verdict that "
                "contradicts its own two numbers is how a factor whose edge was an exposure comes "
                "to be reported as working"
            )
        return self

    @property
    def key(self) -> tuple[FactorTier, FactorTier, AttributionStatistic]:
        """This cell's position in `ATTRIBUTION_CELL_ORDER`."""
        return (self.from_tier, self.to_tier, self.statistic)


class FactorExperimentSpec(BaseModel):
    """The declaration an experiment was run under, as a content address.

    Every field here enters `experiment_id`, and the set is chosen against roadmap section 9's
    measured mistake in the same way `FactorBuildManifest` is: a field only reaches an identity if
    it is a field of the hashed model, and that has to be measured rather than assumed.
    `tests/unit/backtest/test_factor_experiment.py::
    test_every_spec_field_reaches_the_experiment_identity` varies each one alone and asserts the
    id moves.

    **The answers are deliberately not here.** `experiment_id` is the declaration and the inputs;
    `FactorExperimentArtifact.content_digest` is the whole document including them. So one
    `experiment_id` under two `content_digest`s is not two experiments, it is the same policies
    over the same builds at the same commit producing two different numbers -- which is a bug, and
    is the reading `refuse_a_restated_experiment` exists to make loud. See this module's docstring.

    The four upstream specs are carried **whole** rather than reduced to the floors they contain,
    for the reason this module declares no floor of its own: each carries a floor whose arithmetic
    minimum is a different number decided by a different contract, and a spec that recorded a
    projection of the four would be a second source of truth for four things.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-experiment/v1"] = "factor-experiment/v1"
    ic: FactorICSpec
    portfolio: QuantilePortfolioSpec
    tradeability: TradeabilitySpec
    survival: RedundancySpec
    retention_floor: float = Field(gt=0.0, le=1.0)
    """The `retention` at or above which a step is said to have left the statistic alone.

    **No default, and this module refuses to choose one.** `RedundancySpec.redundancy_threshold`
    is the precedent and the argument is the sharper one here: a floor chosen after seeing the
    shipped factors is a floor chosen to make them look separable, and this repository
    has taken a Critical finding on exactly that shape. What is bounded is the *declaration* --
    `gt=0` because a floor of zero calls every non-negative retention `survives` and would make
    the acceptance criterion's cell unreachable, `le=1` because 1.0 is attainable and is the
    strictest legal declaration ("only call a statistic surviving when the step cost it nothing").
    """
    code_commit: str = Field(min_length=7, max_length=64)
    """The commit the four studies ran at, mandatory with no default.

    `FactorBuildManifest.code_commit`'s field and its argument unchanged: different code may
    compute a different number from the same rows, so an identity that ignored it would claim a
    reproducibility it cannot deliver. It has no default for the reason `V2-P0B-009` removed
    `"development"` -- a placeholder that looks like a value is worse than an argument the caller
    has to supply.
    """
    horizon_sessions: int = Field(ge=1)
    as_of_digest: str = Field(min_length=1, max_length=64)
    """`set_digest` of the `as_of`s every tier was offered, in ISO-8601.

    `set_digest` rather than a count, for `FactorBuildManifest.subject_digest`'s measured reason:
    two studies over 60 as_ofs of two disjoint years share a count and nothing else, and while a
    manifest recorded only the count they shared an identity.
    """
    raw_source_digest: str = Field(min_length=1, max_length=64)
    processed_source_digest: str = Field(min_length=1, max_length=64)
    neutralized_source_digest: str = Field(min_length=1, max_length=64)
    """`set_digest` of each tier's `source_manifest_ids`. Three fields rather than one over the
    union, because a caller who read the processed tier from the wrong build and the other two
    from the right ones should move one digest and not a pooled one -- the pooled version cannot
    say which tier moved, and *which tier* is the actionable half."""

    @model_validator(mode="after")
    def validate_the_two_specs_declare_one_factor(self) -> Self:
        if self.ic.definition != self.portfolio.definition:
            raise ValueError(
                f"the IC study declares {self.ic.definition.qualified_key} and the quantile study "
                f"{self.portfolio.definition.qualified_key}; one experiment is one factor, and "
                "two declarations of it would let the sign of the IC and the choice of the long "
                "group come from two different definitions"
            )
        return self

    @property
    def definition(self) -> FactorDefinition:
        return self.ic.definition

    @property
    def factor_id(self) -> str:
        return self.ic.definition.factor_id

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def experiment_id(self) -> str:
        """The content address of this declaration. See this class's docstring for what is in it."""
        return stable_model_id(prefix="fxp", model=self)


class FactorExperimentArtifact(BaseModel):
    """One factor's three-tier report: the declaration, the three rows, and the grid between them.

    This is the record `V2-P3-014` delivers. It is frozen, every collection on it is a tuple, and
    its `content_digest` is a content address over exactly its declared fields -- so an artifact
    that differs from another in any way is a different address, and `FactorExperimentRecord` is
    where that address becomes an enforcement rather than an observation.

    The validators are the part that keeps the report from being three unrelated objects filed
    together:

    - **Every declared tier, once, in `FACTOR_TIER_ORDER`.** A report with two rows would have a
      missing row indistinguishable from a tier that measured nothing.
    - **The three rows are one factor, one direction, one horizon, one cut and one sample.** The
      cross-tier version of `TierReport`'s own rule, and the sample check is the one the
      acceptance criterion rests on: a raw IC of 0.08 and a neutralised IC of 0.00 measured over
      different days is not an attribution.
    - **The spec's digests are the rows' own.** `as_of_digest` and the three source digests are
      recomputed from the tiers and compared. This is the run-time audit that closes the gap
      between a declared table and the implementation that fills it, which this repository has
      measured is the only thing that closes it.
    - **The grid is exactly `ATTRIBUTION_CELL_ORDER`**, and every cell's two values are the two
      tiers' own readings rather than numbers a builder could have supplied. A cell that reported
      a `from_value` no tier carries would be a report of a factor nobody measured.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-experiment-artifact/v1"] = "factor-experiment-artifact/v1"
    spec: FactorExperimentSpec
    tiers: tuple[TierReport, ...] = Field(min_length=MINIMUM_EXPERIMENT_TIERS)
    attributions: tuple[TierAttribution, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_the_report_is_one_experiment(self) -> Self:
        if tuple(report.tier for report in self.tiers) != FACTOR_TIER_ORDER:
            raise ValueError(
                f"this artifact carries {[report.tier for report in self.tiers]} and the declared "
                f"tiers are {list(FACTOR_TIER_ORDER)} in that order; a report missing a row cannot "
                "be told from one whose row measured nothing, and an unordered one makes two "
                "identical experiments compare unequal"
            )
        self._validate_the_rows_are_one_factor()
        self._validate_the_spec_digests_are_the_rows_own()
        self._validate_the_grid_is_the_declared_one()
        return self

    def _validate_the_rows_are_one_factor(self) -> None:
        for name in ("factor_id", "direction", "horizon_sessions", "as_ofs"):
            values = {getattr(report, name) for report in self.tiers}
            if len(values) != 1:
                raise ValueError(
                    f"the three tier reports disagree about {name}; a three-tier comparison is "
                    "one factor's three readings of one question over one sample, and rows that "
                    "differ in any of those are three answers to three questions"
                )
        if self.tiers[0].factor_id != self.spec.factor_id:
            raise ValueError(
                f"the tier reports are about {self.tiers[0].factor_id!r} and the spec declares "
                f"{self.spec.factor_id!r}"
            )
        if self.tiers[0].horizon_sessions != self.spec.horizon_sessions:
            raise ValueError(
                f"the tier reports are at {self.tiers[0].horizon_sessions} session(s) and the "
                f"spec declares {self.spec.horizon_sessions}"
            )
        cuts = {report.portfolio.group_count for report in self.tiers}
        if len(cuts) != 1:
            raise ValueError(
                f"the three tier reports cut into {sorted(cuts)} groups; a spread compared across "
                "two cuts is two quantities"
            )
        if self.tiers[0].portfolio.group_count != self.spec.portfolio.group_count:
            raise ValueError(
                f"the tier reports cut into {self.tiers[0].portfolio.group_count} group(s) and "
                f"the declared quantile spec cuts into {self.spec.portfolio.group_count}"
            )

    def _validate_the_spec_digests_are_the_rows_own(self) -> None:
        offered = set_digest([instant.isoformat() for instant in self.tiers[0].as_ofs])
        if offered != self.spec.as_of_digest:
            raise ValueError(
                f"the spec records as_of_digest {self.spec.as_of_digest!r} and the rows were "
                f"offered a sample digesting to {offered!r}; the identity would then name days "
                "the report was not built over"
            )
        for report in self.tiers:
            field = f"{report.tier}_source_digest"
            recorded: str = getattr(self.spec, field)
            computed = set_digest(report.source_manifest_ids)
            if recorded != computed:
                raise ValueError(
                    f"the spec records {field} {recorded!r} and the {report.tier} row was read "
                    f"from builds digesting to {computed!r}; a stored experiment whose identity "
                    "names other builds than the ones it reports cannot be re-derived from either"
                )

    def _validate_the_grid_is_the_declared_one(self) -> None:
        keys = tuple(cell.key for cell in self.attributions)
        if keys != ATTRIBUTION_CELL_ORDER:
            raise ValueError(
                f"the attribution grid is keyed by {[list(key) for key in keys]} and the declared "
                f"grid is {[list(key) for key in ATTRIBUTION_CELL_ORDER]} in that order; a grid "
                "missing a cell cannot be told from one whose cell is not_measured"
            )
        rows = {report.tier: report for report in self.tiers}
        for cell in self.attributions:
            if cell.retention_floor != self.spec.retention_floor:
                raise ValueError(
                    f"the {cell.from_tier}->{cell.to_tier} {cell.statistic} cell was decided at a "
                    f"floor of {cell.retention_floor!r} and the spec declares "
                    f"{self.spec.retention_floor!r}; the floor on a cell is a projection of the "
                    "declared one and cannot be a second source of truth for it"
                )
            for side, tier in (("from", cell.from_tier), ("to", cell.to_tier)):
                reported: float | None = getattr(cell, f"{side}_value")
                measured = rows[tier].statistic(cell.statistic)
                if reported != measured:
                    raise ValueError(
                        f"the {cell.from_tier}->{cell.to_tier} {cell.statistic} cell reports "
                        f"{side}_value {reported!r} and the {tier} row measured {measured!r}; a "
                        "cell that carries a number no row carries is a comparison of something "
                        "nobody measured"
                    )

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def content_digest(self) -> str:
        """The content address of the whole document -- the declaration and every answer.

        `stable_model_id` over this model's own declared fields, so it excludes itself and every
        other computed field and there is no second canonicalisation to disagree with the first.
        """
        return stable_model_id(prefix="fxc", model=self)

    @property
    def experiment_id(self) -> str:
        return self.spec.experiment_id

    def tier_report(self, tier: FactorTier) -> TierReport:
        """The row for one tier, or a refusal naming what this artifact carries."""
        for report in self.tiers:
            if report.tier == tier:
                return report
        raise FactorExperimentError(
            f"{tier!r} is not a tier this artifact reports; it carries "
            f"{[report.tier for report in self.tiers]}"
        )

    def attribution(
        self, *, from_tier: FactorTier, to_tier: FactorTier, statistic: AttributionStatistic
    ) -> TierAttribution:
        """One cell of the grid, or a refusal naming the declared grid.

        The reading method the acceptance criterion is answered through: a caller asks for
        `("processed", "neutralized", "mean_ic")` and gets a verdict, rather than reading two
        tier rows and deciding for itself whether the fall was large enough to mean anything.
        """
        for cell in self.attributions:
            if cell.key == (from_tier, to_tier, statistic):
                return cell
        raise FactorExperimentError(
            f"({from_tier!r}, {to_tier!r}, {statistic!r}) is not a declared attribution cell; the "
            f"declared grid is {[list(key) for key in ATTRIBUTION_CELL_ORDER]}"
        )


class FactorExperimentRecord(BaseModel):
    """A sealed artifact: the report, the wall clock, the prose, and the digest it was sealed at.

    The split between this and `FactorExperimentArtifact` is the same split
    `FactorInputProvenance` makes against `FactorInputRef` and `FactorNote` makes against the
    three specs, and it is made for their reason rather than for tidiness: the property these
    contracts are under is "every field of the hashed model reaches the identity", and a field
    carved out of a hash with `exclude=True` is exactly the shape roadmap section 9's
    `config_digest` had. Splitting the model keeps the property literally true and auditable by
    asserting on the hashed payload's key set.

    So `built_at` and `note` live here, outside every digest, and are recorded rather than hashed.

    **`sealed_digest` is what makes immutability an enforcement rather than an observation.** It
    is a stored field and the validator below requires it to equal the artifact's recomputed
    `content_digest`, so a record whose serialised artifact was edited between two processes does
    not merely differ from the original -- it fails to parse. What that is and is not is recorded
    as `the_seal_detects_an_edit_and_does_not_authenticate_one`: a digest catches an accident and
    a partial write, and does not catch a rewrite, because whoever edits the artifact can
    recompute the digest beside it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-experiment-record/v1"] = "factor-experiment-record/v1"
    artifact: FactorExperimentArtifact
    sealed_digest: str = Field(min_length=1, max_length=64)
    built_at: datetime
    """The wall clock the experiment was assembled at. Out of both digests; see
    `IDENTITY_EXEMPT_PARAMETERS`."""
    note: FactorNote | None = None
    """Prose about this experiment, out of every content address. See `FactorNote`, which is where
    the three specs' `summary` fields went for exactly this reason."""

    @field_validator("built_at")
    @classmethod
    def normalize_built_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_the_seal(self) -> Self:
        if self.sealed_digest != self.artifact.content_digest:
            raise ValueError(
                f"this record was sealed at {self.sealed_digest!r} and its artifact now hashes to "
                f"{self.artifact.content_digest!r}; a sealed experiment whose content moved is an "
                "edited artifact, and an immutable artifact that can be edited in place is not one"
            )
        if self.note is not None and self.note.subject != self.artifact.spec.definition.key:
            raise ValueError(
                f"the note is about {self.note.subject!r} and this experiment is about "
                f"{self.artifact.spec.definition.key!r}; a note that names another subject is "
                "prose about nothing, which is how a note survives the thing it described"
            )
        return self

    @property
    def experiment_id(self) -> str:
        return self.artifact.experiment_id

    @property
    def content_digest(self) -> str:
        return self.artifact.content_digest


def experiment_payload(record: FactorExperimentRecord) -> str:
    """A sealed record's transport form: canonical JSON of its **declared** fields.

    This is the whole answer to where an experiment artifact lives. It is not a panel dataset --
    `panel_factors` files one row per `(security, as_of)` and a manifest row per build, and an
    experiment is neither; the `factor_*` datasets' subject column is a security or a
    `manifest_id`, and an artifact keyed by `experiment_id` would be a third meaning of one
    column. It is not the evidence plane either: `V2-P3-002` established that a
    `FactorObservation` may not be handed to `ParquetEvidenceStore` because there is no adapter
    and no import edge, and **that argument does not transfer to a report** -- an
    `EvidenceSnapshot` with an experiment payload in it is constructible and the store would take
    it, so the reason this module does not do so is not a type boundary but a layering one:
    `backtest/` may not import `storage/`, and `V2-P3-015` is where a public face for a factor
    lives. So the artifact is delivered as a **document**, and any byte store can hold it.

    Computed fields are excluded, which is not a stylistic choice: every content-addressed model
    in this repository is `extra="forbid"` with its identity as a `computed_field`, so a dump that
    kept them cannot be re-validated at all -- `FactorDefinition.factor_id` is rejected as an
    extra input by the very model that produced it. The declared fields are the whole of what the
    identity hashes anyway (`stable_model_id` excludes computed fields), so a payload of them is a
    payload of exactly what a digest is over.

    The canonicalisation is `stable_model_id`'s own -- sorted keys, fixed separators,
    `ensure_ascii=False`, `allow_nan=False` -- for that function's stated reason: a second
    spelling of "canonical" is a second thing that can disagree about one.
    """
    return json.dumps(
        record.model_dump(mode="json", exclude_computed_fields=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def open_experiment(payload: str) -> FactorExperimentRecord:
    """Reopen a serialised record, refusing one whose content no longer hashes to its seal.

    The boundary at which immutability stops being a property of an object in one process and
    becomes an enforcement across two. `FactorExperimentRecord`'s validator recomputes
    `FactorExperimentArtifact.content_digest` from the parsed content and compares it against the
    `sealed_digest` the document carries, so a payload with one number edited does not merely
    differ from the original -- it does not parse.

    Refuses with `FactorExperimentError` rather than letting a `ValidationError` out, because a
    caller reading a stored document wants one exception type for "this document is not a valid
    sealed experiment" whether the fault was a missing field, a broken seal or malformed JSON.
    Every one of the three is a `ValueError`, so this narrows the type rather than widening it --
    `validate_notes`' argument for taking the caller's own error class.

    What the seal is and is not is recorded as
    `the_seal_detects_an_edit_and_does_not_authenticate_one`.
    """
    try:
        return FactorExperimentRecord.model_validate_json(payload)
    except ValueError as error:
        raise FactorExperimentError(
            f"this payload is not a sealed factor experiment: {error}"
        ) from error


def refuse_a_restated_experiment(
    *, held: Sequence[FactorExperimentRecord], arriving: FactorExperimentRecord
) -> None:
    """Refuse an arriving record that answers a held `experiment_id` differently.

    `panel_factors._refuse_to_drop_a_stored_build` for the experiment plane, and the same shape in
    both directions:

    - **A different answer under one identity is refused.** The `experiment_id` covers the four
      declared policies, the retention floor, the code commit, the sample and the stored builds
      each tier was read from. Two records that share it and disagree about a number are not two
      experiments -- they are one experiment that did not reproduce, and the honest reading is a
      refusal naming both digests rather than a second row a reader has to choose between.
    - **An identical answer is admitted.** A re-derivation that reproduces its own content is a
      no-op, exactly as a re-fetch of byte-identical rows must not move a `manifest_id`. That
      direction is the one `FactorInputRef` lost and had to be given back: an identity that moves
      for nothing makes a rebuild unwritable and its predecessor unreproducible.

    `held` is whatever collection the caller has, because this module has no store and cannot
    acquire one -- `backtest/` may not import `panel/` or `storage/`. What that costs is recorded
    as `nothing_in_this_module_stores_an_artifact_or_can_be_made_to`: an artifact never offered
    here is never checked here, exactly as a factor build never offered to `write_factor_panels`
    is never checked by the guard that function runs.

    Refuses a `held` collection that is already inconsistent with itself before judging the
    arrival, because a guard that reported the newcomer for a contradiction it inherited would
    name the wrong record.
    """
    seen: dict[str, str] = {}
    for record in held:
        previous = seen.setdefault(record.experiment_id, record.content_digest)
        if previous != record.content_digest:
            raise FactorExperimentError(
                f"the held records already carry two answers for {record.experiment_id}: "
                f"{previous} and {record.content_digest}. That contradiction predates this "
                "arrival and is reported against the collection rather than against the newcomer"
            )
    existing = seen.get(arriving.experiment_id)
    if existing is not None and existing != arriving.content_digest:
        raise FactorExperimentError(
            f"{arriving.experiment_id} is already held at content {existing} and this record "
            f"carries {arriving.content_digest}; the same four specs over the same builds at the "
            "same commit have one answer, so a second one is a build that did not reproduce and "
            "not a second experiment. Change a declared field -- which mints a new experiment_id "
            "-- or find out why the numbers moved"
        )


def build_factor_experiment(
    *,
    ic_spec: FactorICSpec,
    portfolio_spec: QuantilePortfolioSpec,
    tradeability_spec: TradeabilitySpec,
    survival_spec: RedundancySpec,
    retention_floor: float,
    code_commit: str,
    raw: TierReport,
    processed: TierReport,
    neutralized: TierReport,
    built_at: datetime,
    note: FactorNote | None = None,
) -> FactorExperimentRecord:
    """Bind three tier reports into one sealed, content-addressed experiment record.

    The one assembly point, and it re-derives none of the four upstream studies' arithmetic: every
    number in the artifact was computed by `FactorICStudy`, `QuantilePortfolioStudy`,
    `RedundancyStudy` or `TradeabilityStudy` and is carried whole. What this function computes is
    the six attribution cells, the four digests and the seal.

    **The three tiers are positional keywords rather than a mapping**, so a caller cannot offer
    two rows or none, and each row's own `tier` field is checked against the argument it arrived
    under -- a `processed` row passed as `neutralized` would otherwise produce an artifact whose
    grid attributes the wrong step.

    Every parameter here is either measured to move `experiment_id` or named in
    `IDENTITY_EXEMPT_PARAMETERS` with the reason it does not, and
    `tests/unit/backtest/test_factor_experiment.py::
    test_every_parameter_of_the_builder_moves_the_identity_or_is_exempted_by_name` reads this
    signature rather than a hand-written list -- `V2-P3-002`'s audit against `compute_factor`,
    which is the shape that catches a twelfth parameter nobody classified.
    """
    tiers = (raw, processed, neutralized)
    for expected, report in zip(FACTOR_TIER_ORDER, tiers, strict=True):
        if report.tier != expected:
            raise FactorExperimentError(
                f"the {expected!r} argument carries a {report.tier!r} tier report; the three rows "
                "are named arguments so that a row cannot be attributed to a step it did not come "
                "from, and swapping two would put a transform's verdict on another transform"
            )
    if raw.as_ofs != processed.as_ofs or raw.as_ofs != neutralized.as_ofs:
        raise FactorExperimentError(
            f"the three tiers were offered {len(raw.as_ofs)}, {len(processed.as_ofs)} and "
            f"{len(neutralized.as_ofs)} as_of(s); an attribution is a difference between two "
            "readings of the same days, and three tiers over three samples produce a fall in a "
            "statistic that cannot be told from a sample that moved underneath it"
        )
    # The horizon is checked here rather than only by the artifact's own validator, and the
    # asymmetry it removes is the reason. `as_of_digest` above and `horizon_sessions` below are
    # both read off `raw` alone, and both are only sound because the three tiers agree -- but only
    # the sample had a check saying so. The horizon's agreement was left to
    # `_validate_the_rows_are_one_factor`, three constructions later, so the value the spec is
    # built from was a positional pick from an unchecked set: swapping `raw` for `processed` in the
    # `horizon_sessions=` below changed nothing any test could see. Unpacked rather than indexed
    # for that reason -- after this refusal there is no slot left to read the wrong one of.
    horizons = {report.horizon_sessions for report in tiers}
    if len(horizons) != 1:
        raise FactorExperimentError(
            f"the three tiers are at {sorted(horizons)} session(s); an attribution is a difference "
            "between two readings of one forward window, and an IC at five sessions minus an IC "
            "at sixty is not a difference the transform made"
        )
    (horizon_sessions,) = horizons
    spec = FactorExperimentSpec(
        ic=ic_spec,
        portfolio=portfolio_spec,
        tradeability=tradeability_spec,
        survival=survival_spec,
        retention_floor=retention_floor,
        code_commit=code_commit,
        horizon_sessions=horizon_sessions,
        as_of_digest=set_digest([instant.isoformat() for instant in raw.as_ofs]),
        raw_source_digest=set_digest(raw.source_manifest_ids),
        processed_source_digest=set_digest(processed.source_manifest_ids),
        neutralized_source_digest=set_digest(neutralized.source_manifest_ids),
    )
    rows = {report.tier: report for report in tiers}
    attributions = tuple(
        _attribute(
            source=rows[source],
            target=rows[target],
            statistic=statistic,
            floor=retention_floor,
        )
        for source, target, statistic in ATTRIBUTION_CELL_ORDER
    )
    artifact = FactorExperimentArtifact(spec=spec, tiers=tiers, attributions=attributions)
    return FactorExperimentRecord(
        artifact=artifact,
        sealed_digest=artifact.content_digest,
        built_at=built_at,
        note=note,
    )


def _attribute(
    *,
    source: TierReport,
    target: TierReport,
    statistic: AttributionStatistic,
    floor: float,
) -> TierAttribution:
    """One cell, with both numbers read off the two rows rather than passed in.

    Read rather than supplied is the load-bearing half: a builder that accepted the two values
    would be a builder that could be handed a pair no tier report carries, and
    `FactorExperimentArtifact` would then be validating a grid against itself. The artifact
    re-reads both from the rows and compares, so this function's output and that validator are two
    independent statements of one rule.
    """
    source_value = source.statistic(statistic)
    target_value = target.statistic(statistic)
    verdict, retention = _attribution_verdict(
        source_value=source_value, target_value=target_value, floor=floor
    )
    return TierAttribution(
        from_tier=source.tier,
        to_tier=target.tier,
        statistic=statistic,
        retention_floor=floor,
        from_value=source_value,
        to_value=target_value,
        retention=retention,
        verdict=verdict,
    )


def builder_parameters() -> tuple[str, ...]:
    """`build_factor_experiment`'s own parameter names, in declaration order.

    Public and reading `inspect.signature` rather than a literal tuple, because the audit that
    consumes it exists to catch a parameter nobody classified and a hand-written list is the thing
    that would go stale. `V2-P3-002` reads `compute_factor`'s signature the same way and for the
    same reason; this is that shape with the function it is about one plane up.
    """
    return tuple(inspect.signature(build_factor_experiment).parameters)
