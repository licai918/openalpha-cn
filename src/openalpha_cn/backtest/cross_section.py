"""The two-stage cross-sectional funnel (`V2-P4-004`): panel scoring, then a hard tradeability
filter, and a shortlist that cannot enter `run_cycle`.

`V2-P3-005` to `V2-P3-007` answer questions about a factor *after the fact*: what did the
ordering correlate with, what would holding it have paid, could it have been held at all. This
answers the question a live `as_of` asks instead -- **which names are worth spending an evidence
run on today** -- and the whole of D3 is that the two are different paths. §3.2 of the PRD draws
it:

    whole market ~5,000    ->  panel plane: score, then filter        (no run_cycle)
    shortlist of N         ->  evidence plane: run_cycle, per subject (evidence closure)

## Two stages, and they are two rather than one

**Stage one scores.** A declared subset of factors, at one declared tier, is combined into one
number per security. Every component is a *declaration* -- the factor, its weight, and nothing
defaulted -- because a composite is a modelling judgement and this repository has no dataset
from which weights could be fitted. `V2-P4-011` to `V2-P4-017` are where a fitted prediction
comes from; until then `KNOWN_CROSS_SECTION_LIMITATIONS.the_shortlist_is_not_a_ranking_of
_expected_return` says what this number is and is not.

**Stage two refuses.** Every scored name is offered as a real buy to `AShareExecutionPolicy`
against the pricing session's bar, sized by `factor_portfolio.position_quantity` at the declared
capital. A name the market would not have sold is not a candidate, however it scored.

The two are not one stage because they fail for unrelated reasons and the remedies differ: a
name with no score is a gap in the factor's inputs, and a name with no fill is a fact about the
session. Folding them would report one attrition rate for two populations -- which is exactly
what `V2-P3-007`'s `CoverageFunnel` exists to stop one plane over.

## What this funnel is to `V2-P3-007`'s four-step funnel: two cuts, not one object

`factor_tradeability.CoverageFunnel` decomposes `universe -> valued -> admissible -> scored ->
held`. This one decomposes `universe -> scored -> tradeable -> shortlisted`. They share a
vocabulary and they are **not** the same measurement, and the reason is structural rather than
stylistic:

- **`V2-P3-007`'s third step cannot exist here.** Its `admissible -> scored` step is
  `domain/labels.py` -- can this name be given a *forward return* over the window. A forward
  return is priced on sessions that have not happened at a live `as_of`, so there is no such
  gate to apply. What this module calls `scored` is "has a composite factor value", which is
  the earlier of the two things that word has meant in this repository.
- **This funnel's last step cannot exist there.** A top-`N` cut selects; `V2-P3-007` measures a
  whole ordering and selecting from it would change the thing being measured.
- **`held` and `tradeable` are the same contract asked two questions.** `V2-P3-007` asks
  `AShareExecutionPolicy` for a round trip over an entry and an exit bar that are both in the
  past. This asks it for one buy against one bar, because the exit is in a future no dataset
  here holds. So `tradeable` is a **necessary condition** for `held` and not a forecast of it;
  `the_filter_answers_for_the_pricing_session_and_not_the_acting_one`
  states the gap, and the T+1 rule the policy itself enforces is why it cannot be closed.

So: a measurement instrument and a selection mechanism, over the same market, sharing three of
their gates and agreeing on none of their totals.

## Why this is a `backtest/` leaf, and how "not in `run_cycle`" is enforced rather than promised

The roadmap row says **not in `run_cycle`**, and D3 is the decision behind it: every path that
produces an evidence-closed conclusion goes through `ResearchEngine.run_cycle`, and a
cross-sectional pre-filter produces no conclusion, so it runs on the panel pipeline instead.
`run_cycle` is a per-subject path -- it seeds, routes agents, aggregates a `SignalFrame`, runs
the risk gate and writes a `RunManifest` and a `DecisionLedger` through `SQLiteRunRepository`
and `SQLiteRecoveryStore`. Three things break if a cross section is put through it:

1. **The unit is wrong.** `ResearchRunRequest` carries one `subject`. A cross section is not a
   subject, so a whole-market pass is 5,000 runs, and `V2-P4-020` measured what one of those
   costs today -- the recovery store saves the *cumulative* result after each agent and dumps
   and revalidates all of it, so twelve agents are 78 serialisations and 78 hashes for **one**
   name.
2. **The write amplification is the thing this issue exists to avoid**, and S95 says so in as
   many words: "without a 5000-subject write amplification". It is not a performance note; a
   single-writer SQLite taking 5,000 x 78 serialisations is the reason the whole-market
   throughput benchmark was demoted from a blocker to "the parameter that calibrates N".
3. **The evidence would be about nothing.** A run produces a `DecisionLedger` per subject. A
   cross-sectional filter has no per-subject decision to record -- being ranked 4,312th is not
   a conclusion about a security, and writing one would put 5,000 ledgers behind a number
   nobody reasoned about.

That is enforced by the import graph rather than by this paragraph. This module is named on
`backtest-studies-reach-no-composition-root`'s source list, which forbids it
`openalpha_cn.runtime`, and on `backtest-studies-touch-no-store`'s, which forbids it
`openalpha_cn.storage`. `run_cycle` lives in `runtime/engine.py`. So a diff that made this
funnel call it fails `lint-imports`.

**Named on the list, and that is the whole of the qualification.** Both contracts enumerate
their sources -- there is no "package except these" form -- so what covers a *new* file is the
pytest assertion `tests/unit/test_import_layering.py::
test_the_two_backtest_study_contracts_cover_every_module_in_the_package`, which holds the lists
against the directory in both directions. `V2-P4-093` measured the gap that leaves in between:
a probe module under `backtest/` importing `numpy` and `openalpha_cn.storage` passes
`lint-imports` at **8 kept, 0 broken**, and only the pytest run goes red. The gate is the CI
pipeline, not either half of it, and
`test_lint_imports_alone_does_not_stop_a_new_backtest_module_reaching_numpy_or_a_store` is that
measurement kept alive.

## Calibrating N, which is the acceptance criterion and is not a taste

The roadmap gives `N` a starting point of 100 and requires it to be **measured**. The
measurement that binds it is not the tradeability attrition and not the batch ceiling; it is the
shipped winsorization, and it is the same shape as `min_cross_section = 100 = 1 /
lower_quantile` one plane down.

`CROSS_SECTION_STANDARD` clips to the empirical 1st and 99th percentiles. `_quantile`
interpolates at position `(n - 1) q`, so the number of values strictly above the 99th percentile
is `(n - 1) - floor((n - 1) q)` -- `upper_clip_block` -- and **every one of them is assigned the
same bound**. On the processed tier those names therefore carry one identical value and one
identical z-score. A top-`N` cut with `N` at or below that count is not a selection by score at
all: it is whatever the tie-break returned.

Measured on real rows, whole market, 2026-08-14 (5,545 listed, 5,540 priced):

    column           n       upper_clip_block   clipped high   distinct values in the block
    turnover_rate    5,540   56                 56             1
    ps_ttm           5,538   56                 56             1
    total_mv         5,540   56                 56             1
    pb               5,498   55                 55             1
    pe_ttm           4,002   41                 41             1

The arithmetic is exact on all five. So on a whole-market cross section **the floor under N is
57**, the roadmap's starting point of 100 clears it by 44 names, and the PRD's suggested lower
end of **50 does not clear it** -- a top-50 taken off the processed tier of a price-based factor
is 50 names drawn from a 56-name tie. `shortlist_size` is refused below the block rather than
warned about, as the `cut_inside_the_clip_block` coverage code.

**The neutralised tier hides the block instead of removing it, and that is the sharper half.**
`INDUSTRY_AND_SIZE` regresses the processed value on industry dummies and log market cap. Run on
the same session's earnings yield (`1 / pe_ttm`, 4,002 participants with an SW2021 L1 industry
and a capitalisation), the 41 clipped names carry **one** processed value and **41 distinct**
neutralised residuals, spanning 71.2% of the whole cross section's residual range and landing at
ranks 1, 2, 3, 4, 7 ... 2,069. Seven of the neutralised top ten come out of that block. Those 41
residuals are ordered entirely by industry mean and log size, because the factor term they were
computed from was one number: an ordering of industries and capitalisations wearing the factor's
name. So the block has to be carried *in* rather than measured off the values -- `Component
CrossSection.clipped_subjects` is carried from the source transform's own upper block, and
`ComponentCensus.tied_at_the_top` is what the values themselves show, and the two reading 41 and
1 on the neutralised tier is the whole reason both are reported.

**The floor is a floor and not a sufficient condition, and that is measured too.** On a
two-factor composite of the same session (earnings yield and book-to-price, 4,001 shared
participants), the count of shortlisted names sitting inside at least one factor's clip block
runs 10 of the top 10, 25 of 25, 37 of 50, 50 of 100, 57 of 200 and 60 of 500. `N` above the
largest single block does not make the substitution go away; it makes it a minority. So each
`ShortlistEntry` carries `clipped_component_count`, and a caller reading a shortlist can see how
much of it the winsorization moved rather than being told it does not matter.

## Which tier a screen should declare, and where its boundary is

`tier` has no default, so this is the caller's decision and not this module's. What the module
does is make the consequences of each answer unavoidable:

- **`raw`** is the only tier on which the composite is monotone in the factor and no clip block
  exists, and it is restricted to **one** component because raw values carry each factor's own
  units. A single-factor screen on the raw tier is the honest cheap option and it is what a
  caller with no processed build should take.
- **`processed`** is the tier a multi-factor composite needs: standardization per cross section
  is what makes a weighted sum of two factors an addition of comparable quantities. It brings
  `min_cross_section = 100` and the clip block, both of which this module reports.
- **`neutralized`** is `processed` plus an industry mean and a size slope removed, and it brings
  one boundary that is not this module's to close. Its inputs are read at the run's `as_of`, and
  `V2-P4-026` has just made the valuation half readable inside its own year
  (`load_daily_valuations` now takes the visibility-filtered session read), which took the
  buildable within-year `as_of`s from one to five. The other half, `index_member_all`, used to
  be unsolved: `load_industry_histories` went through `read_if_ready`, which judges a partition
  by its `max_available_time`, so a membership year was unreadable until its last adjustment
  took effect -- 613 rows from 2021-07-30 and 255 from 2022-07-29 on the real corpus.
  `V2-P4-027`/`V2-P4-028` put the product path on a day-scoped door, so that is no longer the
  boundary. **What now bounds a neutralised screen is the shortlist face's own request
  contract** -- `shortlist_view`'s
  `a_neutralized_tier_screen_needs_exposures_this_face_does_not_load`: a shortlist request
  carries no membership years, no trading calendar and no neutralisation, and those three decide
  what the exposures *are*. That is still a boundary on the *caller* that assembles the cross
  section rather than on this leaf, which reads no partition at all: a screen that cannot get an
  industry cross section gets no neutralised values, and this module reports `no_scored_candidate`
  for the ordinary reason. SW2021's own 2021-12-13 availability floor sits outside both.

## What the hard filter is worth, measured, because the answer is surprising

The same session, run through the real `AShareExecutionPolicy` as a one-lot buy against the
published band:

    listed at the as_of      5,543
    tradeable                5,535
      one-price limit-up         5
      no bar                     3

**Eight names.** The filter removes 0.14% of the market on an ordinary session, so it is a
correctness gate and not a sizing gate: the 55x reduction from 5,540 to 100 is done entirely by
the cut. That is worth stating in the negative, because "hard tradeability filter" reads like
the thing that shrinks the market and it is not -- it is the thing that stops a shortlist
containing a name nobody could have bought.
`the_hard_filter_is_a_correctness_gate_and_not_a_sizing_gate` carries the numbers.

Its verdict is nonetheless the one this repository has an outstanding disclosure about.
`KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input` records that a `MarketBar`
says nothing about whether the registry stood behind that security then, and that "the defence
is that a caller filters its universe before it builds bars, which is a discipline this contract
cannot audit". **This module is that caller and the discipline is now audited**: `universe` is a
required argument with no default, and it bounds the funnel at stage *one* -- a security the
registry does not stand behind is not part of the cross section, so it is never scored, never
priced and never offered an order. It is not a stage-two verdict, because a cross-sectional score
computed over a set including delisted names is already the wrong number.

## Narrow samples, which this repository has taken eight Critical findings on

**No cross-section floor is declared here**, and the omission is deliberate for
`factor_tradeability`'s stated reason -- a second place to declare the same thing is a second
place for the two to disagree. The floor already exists and is upstream: both shipped derived
specs declare `min_cross_section = 100`, so on the processed and neutralised tiers a market
narrower than 100 arrives with a coverage code on every row and no value at all, and this funnel
reports `no_scored_candidate` without needing an opinion. On the raw tier there is no floor and
there deliberately is none, so a three-name `as_of` does score.

What a three-name `as_of` gets instead is a **code rather than a plausible answer**. If
`shortlist_size` is at or above the tradeable count the cut selects everybody, and a funnel that
selected everybody is not a funnel -- `cut_exceeds_the_cross_section`. Both rates are reported
beside every count, so `selection_rate == 1.0` is readable rather than inferable, and `universe
_count`, `scored_count`, `tradeable_count` and `shortlist_count` are all on the record whatever
the code.

## Layering, and what this leaf may not do

Standard library plus `backtest/execution.py`, `backtest/factor_ic.py`,
`backtest/factor_portfolio.py` and `domain/` -- the same edges `factor_tradeability` already
has, and no new dependency. It stores nothing, reads no partition, computes no return and
creates no order: an `ExecutionResult` is a verdict about a hypothetical buy, which is what
`V2-P3-006` already produces, and D16's "never creates portfolio orders" is about the ranking
contract `V2-P4-005` builds on this. **Runtime dependencies remain nine.**
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIERS,
    TIER_ADMITTED_CODES,
    TIER_COVERAGE_ORDER,
    TIER_VALUE_CODES,
    FactorTier,
)
from openalpha_cn.backtest.factor_portfolio import position_quantity
from openalpha_cn.domain.factor import FactorDefinition, FactorDirection
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "CROSS_SECTION_LIMITATION_CODES",
    "FUNNEL_COVERAGE_CODES",
    "FUNNEL_COVERAGE_ORDER",
    "KNOWN_CROSS_SECTION_LIMITATIONS",
    "MAXIMUM_SCORE_COMPONENTS",
    "MAXIMUM_SHORTLIST",
    "MINIMUM_SCORE_COMPONENTS",
    "MINIMUM_SHORTLIST",
    "REFUSED_VERDICT_CODES",
    "REFUSED_VERDICT_ORDER",
    "SCORE_COVERAGE_CODES",
    "SCORE_COVERAGE_ORDER",
    "TRADEABILITY_VERDICT_CODES",
    "TRADEABILITY_VERDICT_ORDER",
    "ComponentCensus",
    "ComponentCrossSection",
    "ComponentScore",
    "CrossSectionFunnel",
    "CrossSectionLimitation",
    "CrossSectionScreen",
    "FunnelCoverage",
    "RefusedSecurity",
    "ScoreCensus",
    "ScoreComponent",
    "ScoreCoverage",
    "ShortlistEntry",
    "ShortlistSpec",
    "TradeabilityCensus",
    "TradeabilityVerdict",
    "TwoStageFunnelError",
    "oriented_value",
    "upper_clip_block",
]


class TwoStageFunnelError(ValueError):
    """Raised for a malformed screen -- never for a fact about the market.

    `FactorPortfolioError`'s reason and its base class, so every call site already writing
    `except ValueError` keeps catching it. A market that scored nobody, a cut that selects
    everybody, a cut inside the winsorization's tie block and a session that refused every buy
    are **not** this: each is a `FunnelCoverage`, because a caller looping over a year of
    `as_of`s has to be able to keep going past them.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossSectionLimitation:
    """One named boundary on what a shortlist can be trusted to mean."""

    code: str
    detail: str


KNOWN_CROSS_SECTION_LIMITATIONS: Final[tuple[CrossSectionLimitation, ...]] = (
    CrossSectionLimitation(
        code="the_shortlist_is_not_a_ranking_of_expected_return",
        detail=(
            "The score is a declared weighted sum of direction-oriented factor values. The "
            "weights are the caller's judgement and are fitted to nothing: this repository "
            "holds no model artifact, no walk-forward split and no out-of-sample estimate "
            "until V2-P4-011 through V2-P4-017 land, so a composite here says 'these names "
            "sit high on the factors somebody declared' and not 'these names will pay more'. "
            "The shortlist's purpose is to decide which subjects are worth an evidence run, "
            "and that is a cheaper claim than a prediction on purpose."
        ),
    ),
    CrossSectionLimitation(
        code="the_hard_filter_is_a_correctness_gate_and_not_a_sizing_gate",
        detail=(
            "Measured on the whole market on 2026-08-14: 5,543 securities listed at the as_of, "
            "5,535 of them buyable for one board lot against the published band through the "
            "real AShareExecutionPolicy -- 5 refused as one-price limit-up bars and 3 carrying "
            "no bar. The filter removes 0.14% of an ordinary session. Every bit of the "
            "reduction from a whole market to a shortlist is done by the cut, and a reader who "
            "took 'hard tradeability filter' for the step that shrinks the market would be "
            "wrong by a factor of about seven hundred. What the filter buys is that no name in "
            "the shortlist is one the market would have refused."
        ),
    ),
    CrossSectionLimitation(
        code="the_filter_answers_for_the_pricing_session_and_not_the_acting_one",
        detail=(
            "One buy against one bar is what AShareExecutionPolicy judges, and the bar is the "
            "pricing session's -- at or before the as_of, because a later one is not knowable. "
            "A candidate is acted on afterwards, and A-share T+1 (which this same policy "
            "enforces on the sell side) guarantees the two are different sessions. So "
            "'tradeable' is a necessary condition for a position and not a forecast of one: a "
            "name that fills here can be halted or limit-locked on the session somebody buys "
            "it. Closing that would need a forward bar, which is the hindsight V2-P3-005's own "
            "limitation registry refuses to claim."
        ),
    ),
    CrossSectionLimitation(
        code="a_neutralised_tier_orders_the_clip_block_by_industry_and_size",
        detail=(
            "CROSS_SECTION_STANDARD assigns every name above its 99th percentile the same "
            "bound, so the processed tier's top upper_clip_block(n) names carry one value. "
            "INDUSTRY_AND_SIZE then subtracts an industry mean and a log-capitalisation slope "
            "from that one value, which produces distinct residuals -- measured on 2026-08-14's "
            "earnings yield over 4,002 participants, the 41 clipped names carry 41 distinct "
            "residuals spanning 71.2% of the whole cross section's residual range, at ranks 1 "
            "to 2,069, with 7 of the neutralised top 10 among them. Those 41 orderings carry no "
            "factor information at all: their factor term was identical. The block is therefore "
            "carried in on ComponentCrossSection.clipped_subjects rather than measured off "
            "the values, because on this tier the values no longer show it."
        ),
    ),
    CrossSectionLimitation(
        code="an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy",
        detail=(
            "KNOWN_EXECUTION_LIMITATIONS.an_absent_band_is_derived_rather_than_refused records "
            "that a MarketBar without up_limit/down_limit gets a band derived from board and "
            "is_st, which is wrong on 159 of the 5,338 priced names of 2024-06-28. This funnel "
            "codes such a bar `unbanded` and places no order, which is the opposite direction "
            "on the same input. That is deliberate and it is a divergence rather than a fix: "
            "the policy is fail-open because its verdicts are pinned by tests written against "
            "the derived rule, and a shortlist is fail-closed because a candidate admitted on "
            "an invented band is a candidate nobody can check. stk_limit serves no rows before "
            "2007-01-04, so a historical as_of will lose real names to this and the census "
            "cell is where that is visible."
        ),
    ),
    CrossSectionLimitation(
        code="no_capacity_constraint_is_applied_to_the_shortlist",
        detail=(
            "position_capital sizes one order so that below_board_minimum can be decided, and "
            "nothing here asks whether the session's turnover could absorb it. Capacity is "
            "factor_tradeability's subject and needs a declared participation_cap plus a "
            "session turnover in yuan, neither of which this funnel takes; that module "
            "measured 000569.SZ on 2001-01-02 absorbing 65,795.78 yuan at a 1% cap, a "
            "capital_multiple of 0.658 against V2-P3-006's own test capital. A shortlist can "
            "therefore contain a name that is tradeable for one lot and not implementable at "
            "the size a portfolio would want, and the remedy is to run that study rather than "
            "to add a second capacity model here."
        ),
    ),
    CrossSectionLimitation(
        code="the_cut_is_broken_by_subject_code_when_two_scores_tie",
        detail=(
            "The shortlist is the first N of the tradeable names ordered by descending score "
            "and then by ts_code, so a run is reproducible and a tie at the boundary resolves "
            "alphabetically -- which carries no information about either name. "
            "cut_inside_the_clip_block refuses the systematic case, where the winsorization "
            "tied a whole block; a coincidental tie between two distinct measurements is not "
            "refused, because a rule that killed a whole as_of for one coincidence would lose "
            "more than it protects. tied_at_the_cut is the count of names sharing the Nth "
            "score and is reported on every funnel, so a boundary decided alphabetically is "
            "visible instead of silent."
        ),
    ),
)
"""What a two-stage screen does not answer, as a closed registry rather than as prose.

Every entry is bound to the suite by `tests/unit/test_known_limitation_registries.py`, which
requires each `code` to appear as a string literal in *executable* test code -- the P2 review
measured that a code named only in docstrings can be renamed with the whole suite staying green.
"""

CROSS_SECTION_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_CROSS_SECTION_LIMITATIONS
)


MINIMUM_SHORTLIST: Final[int] = 1
"""The floor under `ShortlistSpec.shortlist_size`, and it is vacuous on purpose.

A one-name shortlist is a legal thing to ask for -- it is what a caller with one evidence run's
worth of budget wants -- and the floor that actually binds is not a constant at all: it is
`ComponentCrossSection.clipped_subjects`, measured per run against the cross section the scores
came from, and enforced as `cut_inside_the_clip_block`. Declaring a second constant floor here
would put a number in the contract that the run-time rule then contradicts in both directions --
too high on a thin cross section, too low on a whole-market one, and never the measured answer.
`FactorNeutralizationSpec.min_cross_section` admits a vacuous floor for the same reason and says
so in the same words.
"""

MAXIMUM_SHORTLIST: Final[int] = 10_000
"""The ceiling under the same field, restated from the evidence plane rather than chosen.

`batch_contracts.BatchResearchTask.items` is `Field(min_length=1, max_length=10000)`, so a batch
of more than ten thousand `ResearchRunRequest`s does not construct -- which makes a shortlist
longer than that one the second stage cannot accept, whatever anybody intends by it. Restated and
not imported for `factor_portfolio.BOARD_MINIMUM_QUANTITY`'s reason: `backtest/` is a
standard-library leaf and the number lives on a pydantic field one plane over. The two are held
together by `tests/unit/backtest/test_cross_section.py::
test_the_shortlist_ceiling_is_the_batch_the_evidence_plane_will_accept`, which builds a real
`BatchResearchTask` at this size and one item above it and requires the second to be refused --
so a change to the batch cap fails here rather than leaving a stale ceiling standing.

**`V2-P4-031` moved it from 1,000, and the delay is the point of the row.** `V2-P4-019` raised
`MAX_BATCH_ITEMS` tenfold so a whole market -- 5,545 listed on 2026-08-14 -- could be expressed
at all, and was not permitted to touch `backtest/`; the test above fired, and its amendment
weakened the assertion to `<=` and recorded the follow-up. For the interval between the two, the
thing blocking a whole-market shortlist was this constant rather than the batch it claims to
restate, and `<=` is true of every number from 1 to 10,000, so nothing was left saying so. It is
an equality again.

**Why the batch cap and not a smaller measured number.** The floor beside this one cannot be
copied from anywhere -- `V2-P4-004` measured `N >= 57` off the factory winsorization, a fact
about the statistics -- and the row asking for this change warned that the ceiling needs its own
evidence rather than the same tenfold. It does, and the evidence says the batch cap: nothing in
this module has a view about how long a shortlist should be, and the rule that *does* bind is not
a constant but `cut_exceeds_the_cross_section`, a coverage code answered per run against the
tradeable count (`MINIMUM_SHORTLIST` records the identical reasoning below its own vacuous
floor). A smaller number here would be a limit with no measurement behind it.

**Measured against the request-size wall `V2-P4-043` found, because that wall is on another
route.** That row measured a whole-market `POST /api/v1/screen` at 7.81 MB against an 8 MB
default cap, and the concern about raising this ceiling was that it would make an unreachable
path look reachable. It does not: `/api/v1/screen` carries the *already-researched results*
inline, while `POST /api/v1/shortlists/run` names a stored cross section and carries no names at
all. Measured on `ShortlistRunApiRequest` at this build: 450 bytes at `shortlist_size=1`, 453 at
1,000 and 5,545, **454 at 10,000** -- four bytes of growth across the whole range, because the
size is one integer. The answer grows and the request does not: measured on the eight-name
fixture panel, 53 bytes per shortlist entry and 191 per admitted candidate, so a shortlist at
this ceiling extrapolates to roughly 0.5 MB of shortlist and up to 1.9 MB of candidates -- the
same order as one of `V2-P4-040`'s single batches and an order below the 36.9 MB that row
reports for twenty of them.
"""

MINIMUM_SCORE_COMPONENTS: Final[int] = 1
"""A composite of nothing has no ordering; one factor is a legal screen and is the raw tier's
only legal shape (see `ShortlistSpec`)."""

MAXIMUM_SCORE_COMPONENTS: Final[int] = 100
"""A range check on the declared composite, comfortably above the 21 factors this build ships.

`MAXIMUM_PORTFOLIO_GROUPS`' reason: it refuses a caller who passed something that is not a list
of declared components at all, and it is not a judgement about how many factors a screen should
combine.
"""


ScoreCoverage = Literal[
    "scored",
    "incomplete_components",
    "not_admissible",
    "not_valued",
]
"""What became of one security offered to stage one, as a closed set.

- **`scored`** -- an admitted value on **every** declared component, so the composite is the sum
  the policy describes.
- **`incomplete_components`** -- an admitted value on at least one component and not on all of
  them. Its own code rather than folded into either neighbour, and this is the stage's one real
  decision: a composite summed over the components a security happens to have is a *different*
  statistic per security, so the ordering would move with the coverage pattern rather than with
  the factors. Excluding is the fail-closed direction and the count is where the cost shows.
- **`not_admissible`** -- no admitted value anywhere, and at least one component carries a value
  the tier does not admit. That is exactly one cell of the tier tables: `processed`'s `imputed`,
  a number this repository made up. `TIER_VALUE_CODES` minus `TIER_ADMITTED_CODES` is the whole
  of it, which is why both tables are imported and neither is restated.
- **`not_valued`** -- no admitted value and no value at all, on any component: the factor
  engine's own coverage codes, or a security the cross section never mentioned.

Closed for `PeriodCoverage`'s reason: `ScoreCensus` requires the four cells to total the
universe, and a fifth spelling of "not scored" would silently become a group of one.
"""

SCORE_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(ScoreCoverage))

SCORE_COVERAGE_ORDER: Final[tuple[ScoreCoverage, ...]] = get_args(ScoreCoverage)

EXCLUDED_SCORE_ORDER: Final[tuple[ScoreCoverage, ...]] = tuple(
    code for code in SCORE_COVERAGE_ORDER if code != "scored"
)
"""`SCORE_COVERAGE_ORDER` without `scored`: the cells stage one's exclusion table is keyed by."""


TradeabilityVerdict = Literal[
    "tradeable",
    "unbarred",
    "unbanded",
    "below_board_minimum",
    "rejected",
]
"""What became of one scored security offered to stage two, as a closed set.

**There is no `not_in_registry` verdict, and its absence is a finding rather than an omission.**
The first draft had one, and the test written to separate it from the execution policy's own
answer measured that no input could reach it: `select` bounds the cross section by `universe`
before a single value is read, so a security the registry does not stand behind is never scored
and is never offered to stage two. A branch no input can take is not evidence of anything, which
is `position_quantity`'s own stated rule about a floor check it declines to write. The registry
gate is therefore *stage one's*, and
`tests/unit/backtest/test_cross_section.py::
test_the_registry_gate_closes_the_gap_the_execution_policy_discloses` is where it is proved
against the same bar the policy fills.

- **`tradeable`** -- `AShareExecutionPolicy` filled a buy of `position_quantity` shares.
- **`unbarred`** -- the caller offered no bar for the pricing session. `ICCensus.unmatched
  _count`'s reason for being its own code: a short read looks exactly like a refusal until the
  two are counted apart.
- **`unbanded`** -- a bar with no published band. Refused rather than derived; see
  `an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy`.
- **`below_board_minimum`** -- `position_capital` does not buy one lot off STAR, or 200 shares
  on it. There is no order: `ExecutionRequest.quantity` is `gt=0`, so this is decided before the
  policy rather than by it, which is `HoldingOutcome`'s own split.
- **`rejected`** -- the policy refused the buy, and its own `reason` string travels on the
  entry rather than being re-derived here.
"""

TRADEABILITY_VERDICT_CODES: Final[frozenset[str]] = frozenset(get_args(TradeabilityVerdict))

TRADEABILITY_VERDICT_ORDER: Final[tuple[TradeabilityVerdict, ...]] = get_args(TradeabilityVerdict)

REFUSED_VERDICT_ORDER: Final[tuple[TradeabilityVerdict, ...]] = tuple(
    code for code in TRADEABILITY_VERDICT_ORDER if code != "tradeable"
)
"""`TRADEABILITY_VERDICT_ORDER` without `tradeable`: the cells stage two's refusal table is
keyed by."""

REFUSED_VERDICT_CODES: Final[frozenset[str]] = frozenset(REFUSED_VERDICT_ORDER)
"""`REFUSED_VERDICT_ORDER` as a set, for `RefusedSecurity`'s membership check.

Derived from the tuple rather than restated, `TRADEABILITY_VERDICT_CODES`' own arrangement.
"""


FunnelCoverage = Literal[
    "shortlisted",
    "no_scored_candidate",
    "degenerate_scores",
    "cut_inside_the_clip_block",
    "no_tradeable_candidate",
    "cut_exceeds_the_cross_section",
]
"""Whether one `as_of` produced a shortlist, and if not, **why** -- never a bare `None`.

Read in the order `CrossSectionScreen.select` decides them, which is the order declared and is
the funnel's own order:

- **`no_scored_candidate`** -- stage one admitted nobody. Decided first because every question
  below is a question about an ordering, and there is nothing here to order. On the two derived
  tiers this is what a market narrower than `min_cross_section` produces, without this module
  needing a floor of its own.
- **`degenerate_scores`** -- names were scored and every composite ties. A defect in the declared
  policy at this `as_of`; `factor_portfolio` names the same fact under the same code.
- **`cut_inside_the_clip_block`** -- `shortlist_size` is at or below the largest declared
  component's clipped block, so the whole shortlist would come out of a block the
  winsorization tied. A stage-one fact, so it is decided before stage two runs.
- **`no_tradeable_candidate`** -- the ordering worked and the market refused every scored name.
  Separate from the code above it because the remedy is different: a different cut fixes one and
  nothing fixes the other.
- **`cut_exceeds_the_cross_section`** -- `shortlist_size` is at or above the tradeable count, so
  the cut selects everybody and the funnel is not a funnel. The narrow-sample answer, and it is
  a code rather than a shortlist of three names presented as a selection.
- **`shortlisted`** -- and only then does `shortlist` carry anything.
"""

FUNNEL_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(FunnelCoverage))

FUNNEL_COVERAGE_ORDER: Final[tuple[FunnelCoverage, ...]] = get_args(FunnelCoverage)


def oriented_value(value: float, direction: FactorDirection) -> float:
    """`value` signed so that larger means "the factor likes this security more".

    The one place a declaration reaches this module's arithmetic, and it is
    `FactorICSpec.orient`'s rule applied to a factor value rather than to a correlation: a
    `higher_is_better` factor is read as measured and a `lower_is_better` one is negated, because
    an accruals reading of `-0.03` is evidence *for* that security. Exact under IEEE negation.

    A free function rather than a method on `ScoreComponent`, because the pin that keeps it
    honest has to call it beside `FactorICSpec.orient` on the same numbers -- see
    `tests/unit/backtest/test_cross_section.py::
    test_the_orientation_rule_is_the_ic_studys_own_orientation_rule`.
    """
    return value if direction == "higher_is_better" else -value


def upper_clip_block(participant_count: int, upper_quantile: float) -> int:
    """How many values a quantile winsorization assigns its upper bound, at this cross section.

    `(n - 1) - floor((n - 1) * q)`: `panel_factors._quantile` puts the `q`-quantile at position
    `(n - 1) * q` among the order statistics, so the values strictly above it -- the ones a
    winsorizer clips to one number, and which therefore tie -- are the indices past `floor` of
    that position.

    **The obvious spelling of this, `ceil((n - 1) * (1 - q))`, is wrong and the pin caught it.**
    It is right in exact arithmetic and wrong in binary: `1 - 0.99` is
    `0.010000000000000009`, so at `n = 201`, where `(n - 1) * q` lands exactly on the integer
    `198`, it returns `3` where the engine clips `2`. The same disagreement appears at `n = 101`
    and `n = 301` and at every other size where the position is an integer. Written against
    `_quantile`'s own two quantities instead, there is one floating-point operation and it is the
    same one the engine performs.

    Restated rather than imported for `MAXIMUM_SHORTLIST`'s reason -- that function is on the
    panel plane -- and it is here as an *auditable derivation* rather than as this module's
    authority: what `ComponentCrossSection` actually carries is which securities the transform
    clipped, because a real cross section has ties in it and this arithmetic does not know about
    them. The two are held together by `tests/unit/backtest/test_cross_section.py::
    test_the_clip_block_arithmetic_is_the_transform_engines_own_clip_count`, which drives the real
    engine at nine cross-section sizes -- including the three that found the bug above.

    Returns `0` for a cross section of one, where there is nothing above the maximum, and for an
    `upper_quantile` of `1.0`, where the bound *is* the maximum.
    """
    if participant_count < 0:
        raise TwoStageFunnelError("a participant count cannot be negative")
    if not 0.0 <= upper_quantile <= 1.0:
        raise TwoStageFunnelError(
            f"an upper quantile must be in [0, 1]; got {upper_quantile!r}, which is not one"
        )
    if participant_count < 2:
        return 0
    last = participant_count - 1
    return last - math.floor(last * upper_quantile)


class ScoreComponent(BaseModel):
    """One factor's contribution to the composite: the definition, and a weight.

    `definition` rather than a bare `direction`, for `FactorICSpec`'s reason and it is the whole
    argument of this contract in one field. Which end of a factor a shortlist is taken from is
    decided by a *declared* property of the factor, and a screen taking the direction as its own
    argument is one a caller can hand the other value to.

    `weight` has no default, for `QuantilePortfolioSpec`'s reason: it moves the answers, and a
    default is a decision nobody recorded making. It is positive because a negative weight is a
    second way to express a direction and the factor already declares one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    definition: FactorDefinition
    weight: float = Field(gt=0.0, le=1_000.0)

    @property
    def factor_id(self) -> str:
        return self.definition.factor_id

    @property
    def direction(self) -> FactorDirection:
        return self.definition.direction


class ShortlistSpec(BaseModel):
    """The declared policy a two-stage screen applies: which factors, which tier, how many names.

    Nothing here has a default. Each field moves which securities come out, and this repository's
    rule since `V2-P3-005` is that a decision which moves the answers is one the caller records
    making.

    **A raw-tier screen may declare exactly one component**, and that rule is the only one in this
    contract that refuses a configuration a caller could otherwise write. Raw factor values are in
    each factor's own units -- an earnings yield in `1/CNY`, a turnover rate in per cent, an
    Amihud illiquidity in yuan per unit of return -- so a weighted sum of two of them is an
    addition of quantities that do not share a scale, and the largest-scaled factor decides the
    whole ordering whatever the weights say. A single raw component has no such problem: the
    composite is that factor, monotone in it, and no clip block exists on that tier at all. The
    processed and neutralised tiers are standardized cross section by cross section, which is what
    makes a sum of them meaningful, and that is `V2-P3-003`'s whole purpose.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    components: tuple[ScoreComponent, ...] = Field(
        min_length=MINIMUM_SCORE_COMPONENTS, max_length=MAXIMUM_SCORE_COMPONENTS
    )
    tier: FactorTier
    shortlist_size: int = Field(ge=MINIMUM_SHORTLIST, le=MAXIMUM_SHORTLIST)
    """How many names reach the evidence plane. See this module's docstring for the measurement
    that bounds it below and `MAXIMUM_SHORTLIST` for the one that bounds it above."""
    position_capital: Decimal = Field(gt=0, lt=Decimal(10) ** 26)
    """The notional budget stage two sizes one buy against, in yuan.

    It is here because `below_board_minimum` cannot be decided without it -- a name at 300 yuan a
    share does not sell a 100-share lot for 10,000 yuan -- and it is *not* a portfolio weight:
    nothing here allocates, and `V2-P4-006` and the construction issues are where a weight comes
    from.
    """

    @property
    def factor_ids(self) -> tuple[str, ...]:
        return tuple(component.factor_id for component in self.components)

    @property
    def total_weight(self) -> float:
        return math.fsum(component.weight for component in self.components)

    @model_validator(mode="after")
    def validate_the_declared_composite(self) -> Self:
        if len(set(self.factor_ids)) != len(self.components):
            raise ValueError(
                f"components name {sorted(self.factor_ids)}, which repeats a factor_id; a factor "
                "declared twice is a weight expressed in two places and the two can disagree"
            )
        if self.tier == "raw" and len(self.components) > 1:
            raise ValueError(
                f"a raw-tier screen declares {len(self.components)} components; raw factor "
                "values carry each factor's own units, so summing two of them is an addition "
                "of quantities that share no scale and the largest-scaled factor decides the "
                "ordering. Declare one raw component, or screen on the processed or neutralized "
                "tier, which are standardized per cross section for exactly this reason"
            )
        return self


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentCrossSection:
    """One factor's stored cross section at one `as_of`, projected the way this module reads it.

    `(subject, value, coverage)` triples -- the three columns all three observation contracts
    share, and the same projection `factor_ic.ic_cross_section` takes, so a caller holding typed
    rows for any tier can feed both without a second adapter.

    `clipped_subjects` is which of these securities the transform assigned its **upper** bound,
    and it is **carried in rather than measured off `values`** because on the neutralised tier the
    values no longer show it: the same 41 names that shared one processed number carry 41 distinct
    residuals once an industry mean and a size slope are subtracted, so nothing recoverable from a
    neutralised cross section identifies them. On the processed tier a caller can recover it as
    the names sharing the largest stored value, which is what `ComponentCensus.tied_at_the_top`
    reports independently -- the two agreeing there and disagreeing on the neutralised tier is the
    measurement, not an assertion. It is empty on the raw tier, which clips nothing, and empty for
    a `method="none"` transform.

    Only the *upper* block is carried. The lower one is real and is not this contract's business:
    a shortlist is a top-`N` cut, so the names the winsorization pushed *up* to its lower bound
    are at the wrong end of the ordering to reach it.
    """

    factor_id: str
    values: tuple[tuple[str, float | None, str], ...]
    clipped_subjects: frozenset[str]

    def __post_init__(self) -> None:
        if not self.factor_id.strip():
            raise TwoStageFunnelError("a component cross section must name a factor")
        offered = {subject for subject, _value, _coverage in self.values}
        unknown = sorted(self.clipped_subjects - offered)
        if unknown:
            raise TwoStageFunnelError(
                f"{self.factor_id} names {unknown} as clipped and carries no row for them; a "
                "winsorizer cannot move a value that was not in the cross section"
            )
        seen: set[str] = set()
        for subject, value, _coverage in self.values:
            if not subject.strip():
                raise TwoStageFunnelError(f"{self.factor_id} carries a row naming no subject")
            if subject in seen:
                raise TwoStageFunnelError(
                    f"{self.factor_id} carries {subject} twice at one as_of; two values for one "
                    "security is two builds, and which of them scored is not recoverable"
                )
            seen.add(subject)
            if value is not None and not math.isfinite(float(value)):
                raise TwoStageFunnelError(
                    f"{self.factor_id}'s {subject} carries {value!r}, which is not a finite "
                    "number; a non-finite term poisons every sum and every ordering built on it"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentCensus:
    """One component's own arithmetic, reported beside the composite's.

    Without it a composite is one number over an intersection, and a screen in which one factor
    supplied almost nothing looks exactly like one in which all of them did -- `ICCensus`'
    argument, applied to the place a composite hides it.

    `clipped_count` is how many of the securities this screen scored were carried in as clipped,
    and `tied_at_the_top` is the *observed* size of the block sharing the largest admitted value.
    They agree on the processed tier, where the clip is what produces the tie, and they do not on
    the neutralised tier, where the pair reads `(41, 1)` on the measurement in this module's
    docstring. Reporting both is what makes that visible rather than a claim, and it is why a
    caller cannot substitute one for the other.
    """

    factor_id: str
    subject_count: int
    valued_count: int
    admitted_count: int
    clipped_count: int
    tied_at_the_top: int

    def __post_init__(self) -> None:
        for name in (
            "subject_count",
            "valued_count",
            "admitted_count",
            "clipped_count",
            "tied_at_the_top",
        ):
            if int(getattr(self, name)) < 0:
                raise TwoStageFunnelError(f"{name} cannot be negative")
        if self.clipped_count > self.subject_count:
            raise TwoStageFunnelError(
                f"{self.factor_id} reports {self.clipped_count} clipped of "
                f"{self.subject_count} rows"
            )
        if self.admitted_count > self.valued_count:
            raise TwoStageFunnelError(
                f"{self.factor_id} admits {self.admitted_count} of {self.valued_count} valued "
                "rows; the admitted codes are a subset of the ones that carry a value"
            )
        if self.valued_count > self.subject_count:
            raise TwoStageFunnelError(
                f"{self.factor_id} values {self.valued_count} of {self.subject_count} rows"
            )
        if self.tied_at_the_top > self.admitted_count:
            raise TwoStageFunnelError(
                f"{self.factor_id} reports {self.tied_at_the_top} names tied at the top of "
                f"{self.admitted_count} admitted values"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreCensus:
    """What became of every security offered to stage one, as numbers that add up.

    `ICCensus`' instrument on this plane. Four cells, and `__post_init__` requires them to total
    `universe_count`, which is what makes any one of them un-fudgeable: a census that quietly
    dropped a security, or counted one under two headings, fails its own arithmetic rather than
    reporting a plausible total.

    `excluded_by_coverage` carries **every** non-`scored` code in the declared order, including
    the ones that did not occur, for `ICCensus.excluded_by_coverage`'s reason: "nobody was
    `incomplete_components`" and "nothing looked" are different claims.
    """

    tier: FactorTier
    universe_count: int
    scored_count: int
    excluded_by_coverage: tuple[tuple[ScoreCoverage, int], ...]
    components: tuple[ComponentCensus, ...]

    def __post_init__(self) -> None:
        if self.tier not in FACTOR_TIERS:
            raise TwoStageFunnelError(
                f"{self.tier!r} is not a declared tier; expected one of {sorted(FACTOR_TIERS)}"
            )
        for name in ("universe_count", "scored_count"):
            if int(getattr(self, name)) < 0:
                raise TwoStageFunnelError(f"{name} cannot be negative")
        keys = tuple(code for code, _count in self.excluded_by_coverage)
        if keys != EXCLUDED_SCORE_ORDER:
            raise TwoStageFunnelError(
                f"excluded_by_coverage is keyed by {list(keys)} and stage one excludes "
                f"{list(EXCLUDED_SCORE_ORDER)} in that order; a census missing a code cannot be "
                "told from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.excluded_by_coverage):
            raise TwoStageFunnelError("an excluded-coverage count cannot be negative")
        total = self.scored_count + sum(count for _code, count in self.excluded_by_coverage)
        if total != self.universe_count:
            raise TwoStageFunnelError(
                f"the census accounts for {total} securities and {self.universe_count} were "
                "offered; every subject is scored or excluded by one code, and a census that "
                "does not add up has lost one of them"
            )

    @property
    def scored_rate(self) -> float | None:
        """`scored / universe`, or `None` on an empty universe."""
        return None if self.universe_count == 0 else self.scored_count / self.universe_count


@dataclass(frozen=True, slots=True, kw_only=True)
class RefusedSecurity:
    """One scored security stage two did **not** admit, under the rule that decided it.

    `V2-P4-066`. `TradeabilityCensus` could already say that nine names went and under which four
    headings; it could not say *which nine*, and neither could any shipped surface, so a screen
    refused by `--min-tradable-ratio` told a user a number about a market they could not look at.
    A count is the answer to "how much coverage did this list have"; a name is the answer to "what
    do I do about it", and the two questions are not the same question.

    `reason` is the execution policy's own string and is present for **exactly** `rejected`, which
    `__post_init__` requires in both directions. The other three verdicts are decided by this
    module before `AShareExecutionPolicy.execute` is called at all -- there is no order to refuse
    -- so a reason beside one of them would be this module inventing a sentence the policy never
    said, which is `_rejection_reasons`' stated rule about not being a second authority.
    """

    subject: str
    verdict: TradeabilityVerdict
    reason: str | None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise TwoStageFunnelError("a refused security must name a subject")
        if self.verdict not in REFUSED_VERDICT_CODES:
            raise TwoStageFunnelError(
                f"{self.verdict!r} is not one of the verdicts stage two refuses under; "
                f"expected one of {list(REFUSED_VERDICT_ORDER)}"
            )
        if (self.reason is None) is (self.verdict == "rejected"):
            raise TwoStageFunnelError(
                f"{self.subject} is {self.verdict!r} carrying reason {self.reason!r}; the policy "
                "gives a reason for exactly the buys it refused, and the other three verdicts are "
                "decided before it is called"
            )
        if self.reason is not None and not self.reason.strip():
            raise TwoStageFunnelError(f"{self.subject}'s rejection reason is blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class TradeabilityCensus:
    """What became of every scored security offered to stage two, as numbers that add up.

    Keyed by `REFUSED_VERDICT_ORDER` and totalling `scored_count`, `ScoreCensus`' arrangement for
    `ScoreCensus`' reason. `rejection_reasons` carries the execution policy's own strings with
    their counts, in descending order and then alphabetically, because "the market refused 41
    names" and "the market refused 41 names for one reason" are different findings and the
    `rejected` cell alone cannot tell them apart.

    `unoffered_count` is `PortfolioCensus.unattempted_count` on this plane and is **all or
    nothing**: a funnel refused by one of the three stage-one codes never placed an order, and
    counting those securities under any member of `TradeabilityVerdict` would be a per-security
    verdict about an order nobody built. `__post_init__` requires it to be either zero or the
    whole scored count, so a census cannot report a market that half happened.
    """

    scored_count: int
    tradeable_count: int
    unoffered_count: int
    refused_by_verdict: tuple[tuple[TradeabilityVerdict, int], ...]
    rejection_reasons: tuple[tuple[str, int], ...]
    refused: tuple[RefusedSecurity, ...]
    """Every refused security by name, in `REFUSED_VERDICT_ORDER` and then by subject.

    `V2-P4-066`, and it is held to the counts above rather than trusted beside them: a census
    whose named list disagreed with its own cells would be two answers to one question, which is
    the arithmetic this class exists to make un-fudgeable. Grouped by verdict rather than sorted
    by subject alone, because the reading a refused screen needs first is *which rule*, and a
    reader scanning for one rule should not have to scan the whole list to be sure they have it.
    """

    def __post_init__(self) -> None:
        for name in ("scored_count", "tradeable_count", "unoffered_count"):
            if int(getattr(self, name)) < 0:
                raise TwoStageFunnelError(f"{name} cannot be negative")
        if self.unoffered_count not in (0, self.scored_count):
            raise TwoStageFunnelError(
                f"{self.unoffered_count} of {self.scored_count} scored securities were never "
                "offered to the market; stage two either runs for the whole cross section or "
                "does not run, so a partial count is a census of a market that half happened"
            )
        keys = tuple(code for code, _count in self.refused_by_verdict)
        if keys != REFUSED_VERDICT_ORDER:
            raise TwoStageFunnelError(
                f"refused_by_verdict is keyed by {list(keys)} and stage two refuses "
                f"{list(REFUSED_VERDICT_ORDER)} in that order; a census missing a verdict cannot "
                "be told from one whose count is zero"
            )
        if any(count < 0 for _code, count in self.refused_by_verdict):
            raise TwoStageFunnelError("a refused-verdict count cannot be negative")
        total = (
            self.tradeable_count
            + self.unoffered_count
            + sum(count for _code, count in self.refused_by_verdict)
        )
        if total != self.scored_count:
            raise TwoStageFunnelError(
                f"the census accounts for {total} securities and {self.scored_count} were "
                "scored; every scored subject is tradeable, refused under one verdict, or was "
                "never offered"
            )
        rejected = dict(self.refused_by_verdict)["rejected"]
        if sum(count for _reason, count in self.rejection_reasons) != rejected:
            raise TwoStageFunnelError(
                f"the rejection reasons total "
                f"{sum(count for _reason, count in self.rejection_reasons)} and {rejected} "
                "securities were rejected; the policy gives exactly one reason per refusal"
            )
        named = tuple(item.subject for item in self.refused)
        if len(set(named)) != len(named):
            raise TwoStageFunnelError(
                f"the refused list names {sorted({s for s in named if named.count(s) > 1})} twice; "
                "a scored security gets one verdict"
            )
        expected_order = tuple(
            sorted(
                self.refused,
                key=lambda item: (REFUSED_VERDICT_ORDER.index(item.verdict), item.subject),
            )
        )
        if self.refused != expected_order:
            raise TwoStageFunnelError(
                "the refused list is not in REFUSED_VERDICT_ORDER and then subject order; two "
                "runs of one screen must name the same securities in the same order or a reader "
                "diffing them is reading an iteration order as a change"
            )
        counted = {
            code: sum(1 for item in self.refused if item.verdict == code)
            for code in REFUSED_VERDICT_ORDER
        }
        if counted != dict(self.refused_by_verdict):
            raise TwoStageFunnelError(
                f"the refused list names {counted} and the verdict census reports "
                f"{dict(self.refused_by_verdict)}; the names and the counts are one answer"
            )
        named_reasons: dict[str, int] = {}
        for item in self.refused:
            if item.reason is not None:
                named_reasons[item.reason] = named_reasons.get(item.reason, 0) + 1
        if named_reasons != dict(self.rejection_reasons):
            raise TwoStageFunnelError(
                f"the refused list carries reasons {named_reasons} and the census reports "
                f"{dict(self.rejection_reasons)}"
            )

    @property
    def tradeable_rate(self) -> float | None:
        """`tradeable / scored`, or `None` when stage two did not run over anybody.

        `None` rather than `0.0` under the stage-one codes, because "the market refused every
        name" and "nobody was offered to the market" are the distinction `unoffered_count` exists
        to draw and a rate of zero would collapse them.
        """
        if self.scored_count == 0 or self.unoffered_count == self.scored_count:
            return None
        return self.tradeable_count / self.scored_count


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentScore:
    """One factor's term inside one security's composite, kept rather than summed away.

    `value` is what the tier stored; `oriented` is `value` under the factor's declared direction;
    `contribution` is `weight * oriented`. All three, because a shortlist whose reasons are one
    number is a shortlist nobody can check -- and `V2-P4-005`'s `CandidateRanking` owes a caller
    the factor exposures behind a candidate, which is this tuple.

    `clipped` says the transform assigned this value its upper bound, so the number is the bound
    and not the security's own. It is decided by comparing against the block the component
    carried rather than re-deriving a quantile here.
    """

    factor_id: str
    value: float
    oriented: float
    weight: float
    contribution: float
    clipped: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistEntry:
    """One name that reached the evidence plane's door, and everything behind it.

    Built so that `V2-P4-005` can be built on it without asking this module a second question:
    the subject and its rank, the composite and every term of it, and the execution verdict as
    `AShareExecutionPolicy`'s own `ExecutionResult` rather than as a re-derivation.

    `fill` is a **verdict about a hypothetical buy**, which is what `V2-P3-006` already produces
    for every position it measures. It is not an order and cannot become one: D16 requires a
    ranking never to create portfolio orders, and nothing under `backtest/` can reach a store to
    persist one anyway.
    """

    subject: str
    rank: int
    score: float
    components: tuple[ComponentScore, ...]
    fill: ExecutionResult

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise TwoStageFunnelError("a shortlist entry must name a subject")
        if self.rank < 1:
            raise TwoStageFunnelError(f"{self.subject} carries rank {self.rank}; ranks are 1-based")
        if not math.isfinite(self.score):
            raise TwoStageFunnelError(f"{self.subject}'s score is {self.score!r}, which is not one")
        if self.fill.status != "filled":
            raise TwoStageFunnelError(
                f"{self.subject} is shortlisted carrying a {self.fill.status} execution; the "
                "hard filter admits a name only when the policy filled its buy"
            )

    @property
    def clipped_component_count(self) -> int:
        """How many of this entry's terms are the winsorization's bound rather than a measurement.

        The residual this module's docstring measures rather than argues away: on a two-factor
        composite of 2026-08-14's market, 25 of the top 25 and 50 of the top 100 shortlisted names
        are clipped on at least one component, so a cut above the largest block makes the
        substitution a minority and never removes it.
        """
        return sum(1 for component in self.components if component.clipped)


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossSectionFunnel:
    """One `as_of`'s two-stage answer: the shortlist, or the code that says why there is none.

    Built by `CrossSectionScreen.select`; this constructor re-derives nothing, `ICCrossSection`'s
    precedent.

    The three rates are the two-stage analogue of `CoverageFunnel`'s four, and `shortlist_rate` is
    their product up to the rounding of three divisions -- deliberately the same shape, so a
    reader who has read `V2-P3-007` reads this without a second vocabulary and can see that the
    steps are not the same steps.
    """

    as_of: datetime
    tier: FactorTier
    coverage: FunnelCoverage
    shortlist_size: int
    scores: ScoreCensus
    tradeability: TradeabilityCensus
    shortlist: tuple[ShortlistEntry, ...]
    clip_block: int
    """The largest declared component's stored upper clip block. The floor `shortlist_size` has
    to clear, reported on every funnel including the ones that cleared it."""
    tied_at_the_cut: int
    """How many tradeable names share the score of the last shortlisted one.

    `1` on a clean cut. Above `1` the boundary was decided by `ts_code`, which carries nothing;
    `the_cut_is_broken_by_subject_code_when_two_scores_tie` is why that is reported instead of
    refused. `0` when there is no shortlist.
    """

    def __post_init__(self) -> None:
        if self.tier not in FACTOR_TIERS:
            raise TwoStageFunnelError(f"{self.tier!r} is not a declared tier")
        if self.coverage not in FUNNEL_COVERAGE_CODES:
            raise TwoStageFunnelError(f"{self.coverage!r} is not a declared funnel coverage code")
        if self.tier != self.scores.tier:
            raise TwoStageFunnelError(
                f"this funnel reports tier {self.tier!r} and its score census reports "
                f"{self.scores.tier!r}"
            )
        if self.scores.scored_count != self.tradeability.scored_count:
            raise TwoStageFunnelError(
                f"stage one scored {self.scores.scored_count} securities and stage two was "
                f"offered {self.tradeability.scored_count}; the second stage runs on exactly "
                "what the first admitted"
            )
        measured = self.coverage == "shortlisted"
        if measured != bool(self.shortlist):
            raise TwoStageFunnelError(
                f"coverage {self.coverage!r} carries {len(self.shortlist)} shortlisted names; "
                "exactly the 'shortlisted' code carries any, and every other code carries none"
            )
        if len(self.shortlist) > self.shortlist_size:
            raise TwoStageFunnelError(
                f"{len(self.shortlist)} names were shortlisted and {self.shortlist_size} were "
                "asked for"
            )
        if len(self.shortlist) > self.tradeability.tradeable_count:
            raise TwoStageFunnelError(
                f"{len(self.shortlist)} names were shortlisted and "
                f"{self.tradeability.tradeable_count} were tradeable"
            )
        ranks = tuple(entry.rank for entry in self.shortlist)
        if ranks != tuple(range(1, len(self.shortlist) + 1)):
            raise TwoStageFunnelError(
                f"the shortlist carries ranks {list(ranks)}; a shortlist is the first "
                "len(shortlist) ranks of one ordering, in order"
            )
        if any(
            later.score > earlier.score
            for earlier, later in zip(self.shortlist, self.shortlist[1:], strict=False)
        ):
            raise TwoStageFunnelError(
                "the shortlist is not ordered by descending score; a rank that contradicts the "
                "score beside it is a ranking of something else"
            )
        if self.tied_at_the_cut < 0 or (self.tied_at_the_cut == 0) != (not self.shortlist):
            raise TwoStageFunnelError(
                f"tied_at_the_cut is {self.tied_at_the_cut} beside {len(self.shortlist)} "
                "shortlisted names; a shortlist has at least the one name at its own cut"
            )

    @property
    def shortlist_count(self) -> int:
        return len(self.shortlist)

    @property
    def selection_rate(self) -> float | None:
        """`shortlisted / tradeable`, or `None` when nothing was tradeable.

        `1.0` means the cut selected everybody, which `cut_exceeds_the_cross_section` refuses --
        so on a `shortlisted` funnel this is strictly below one.
        """
        tradeable = self.tradeability.tradeable_count
        return None if tradeable == 0 else self.shortlist_count / tradeable

    @property
    def shortlist_rate(self) -> float | None:
        """`shortlisted / universe`: the product of the three steps, up to rounding."""
        universe = self.scores.universe_count
        return None if universe == 0 else self.shortlist_count / universe


class CrossSectionScreen:
    """Run one declared two-stage screen over one `as_of`'s cross section.

    A class holding a `ShortlistSpec` and an `AShareExecutionPolicy`, so that the declared
    composite, the tier, the cut and the capital are fixed once for a screen instead of being
    passed at each call with a chance to differ. `QuantilePortfolioStudy`'s precedent.

    **The execution policy is a required argument and is not defaulted**, for that study's stated
    reason: it carries the `CostSchedule`, and while a `filled`/`rejected` verdict does not move
    with the rates, the `ExecutionResult` this screen hands to `V2-P4-005` does.
    """

    def __init__(self, spec: ShortlistSpec, *, execution: AShareExecutionPolicy) -> None:
        self._spec = spec
        self._execution = execution

    @property
    def spec(self) -> ShortlistSpec:
        return self._spec

    @property
    def execution(self) -> AShareExecutionPolicy:
        return self._execution

    def select(
        self,
        *,
        as_of: datetime,
        universe: Iterable[str],
        components: Sequence[ComponentCrossSection],
        bars: Mapping[str, MarketBar],
    ) -> CrossSectionFunnel:
        """Score, filter, cut -- in that order, and report what each step cost.

        `universe` is the registry's own listed set at this `as_of` and is the argument
        `KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input` says a caller owes the
        execution policy. It bounds the whole funnel: a security with a stored factor value and no
        place in the universe is not offered to either stage, because a cross section is defined
        by the market that existed, not by whichever partitions happen to hold rows -- and a mean
        and a standard deviation taken over a set that includes delisted names are the wrong two
        numbers before any order is priced.

        `components` must be one cross section per declared component, in any order, and a
        component the spec did not declare is a malformed call rather than a market fact.

        `bars` is the pricing session's bar per security -- one session, at or before `as_of`,
        the caller's choice and the caller's to date. A missing key is `unbarred` and is counted,
        never inferred.
        """
        instant = ensure_aware(as_of)
        subjects = _refuse_a_universe_that_is_not_one(universe)
        offered = _refuse_components_that_are_not_the_declared_ones(self._spec, components)

        censuses, admitted, valued, clipped = _read_components(self._spec, offered, subjects)
        coded, scored = _score(
            self._spec, subjects, admitted=admitted, valued=valued, clipped=clipped
        )
        score_census = ScoreCensus(
            tier=self._spec.tier,
            universe_count=len(subjects),
            scored_count=len(scored),
            excluded_by_coverage=tuple(
                (code, sum(1 for verdict in coded.values() if verdict == code))
                for code in EXCLUDED_SCORE_ORDER
            ),
            components=censuses,
        )
        clip_block = max((census.clipped_count for census in censuses), default=0)

        early = _refusal_before_stage_two(self._spec, scored=scored, clip_block=clip_block)
        if early is not None:
            return CrossSectionFunnel(
                as_of=instant,
                tier=self._spec.tier,
                coverage=early,
                shortlist_size=self._spec.shortlist_size,
                scores=score_census,
                tradeability=_empty_tradeability(len(scored)),
                shortlist=(),
                clip_block=clip_block,
                tied_at_the_cut=0,
            )

        verdicts, fills = self._filter(scored, bars=bars)
        tradeable = sorted(
            (subject for subject, verdict in verdicts.items() if verdict == "tradeable"),
            key=lambda subject: (-scored[subject].score, subject),
        )
        tradeability = TradeabilityCensus(
            scored_count=len(scored),
            tradeable_count=len(tradeable),
            unoffered_count=0,
            refused_by_verdict=tuple(
                (code, sum(1 for verdict in verdicts.values() if verdict == code))
                for code in REFUSED_VERDICT_ORDER
            ),
            rejection_reasons=_rejection_reasons(fills, verdicts),
            refused=_refused_securities(verdicts, fills),
        )

        if not tradeable:
            coverage: FunnelCoverage = "no_tradeable_candidate"
        elif self._spec.shortlist_size >= len(tradeable):
            coverage = "cut_exceeds_the_cross_section"
        else:
            coverage = "shortlisted"
        if coverage != "shortlisted":
            return CrossSectionFunnel(
                as_of=instant,
                tier=self._spec.tier,
                coverage=coverage,
                shortlist_size=self._spec.shortlist_size,
                scores=score_census,
                tradeability=tradeability,
                shortlist=(),
                clip_block=clip_block,
                tied_at_the_cut=0,
            )

        cut = tradeable[: self._spec.shortlist_size]
        boundary = scored[cut[-1]].score
        return CrossSectionFunnel(
            as_of=instant,
            tier=self._spec.tier,
            coverage="shortlisted",
            shortlist_size=self._spec.shortlist_size,
            scores=score_census,
            tradeability=tradeability,
            shortlist=tuple(
                ShortlistEntry(
                    subject=subject,
                    rank=index,
                    score=scored[subject].score,
                    components=scored[subject].components,
                    fill=fills[subject],
                )
                for index, subject in enumerate(cut, start=1)
            ),
            clip_block=clip_block,
            tied_at_the_cut=sum(1 for subject in tradeable if scored[subject].score == boundary),
        )

    def _filter(
        self,
        scored: Mapping[str, _Composite],
        *,
        bars: Mapping[str, MarketBar],
    ) -> tuple[dict[str, TradeabilityVerdict], dict[str, ExecutionResult]]:
        """Stage two, one scored security at a time, in the verdict order this module declares.

        Every key of `scored` is in the universe by construction -- `_read_components` filters on
        it and `_score` iterates it -- so there is no registry check here; see
        `TradeabilityVerdict` for the measurement that removed the one this used to have.
        """
        verdicts: dict[str, TradeabilityVerdict] = {}
        fills: dict[str, ExecutionResult] = {}
        for subject in scored:
            bar = bars.get(subject)
            if bar is None:
                verdicts[subject] = "unbarred"
                continue
            if not bar.has_published_limits:
                verdicts[subject] = "unbanded"
                continue
            quantity = position_quantity(capital=self._spec.position_capital, market=bar)
            if quantity == 0:
                verdicts[subject] = "below_board_minimum"
                continue
            fill = self._execution.execute(ExecutionRequest(side="buy", quantity=quantity), bar)
            fills[subject] = fill
            verdicts[subject] = "tradeable" if fill.status == "filled" else "rejected"
        return verdicts, fills


@dataclass(frozen=True, slots=True, kw_only=True)
class _Composite:
    """One security's composite and its terms, before the cut decides anything about it."""

    score: float
    components: tuple[ComponentScore, ...]


def _refuse_a_universe_that_is_not_one(universe: Iterable[str]) -> tuple[str, ...]:
    """The registry's listed set, deduplicated and refused when it is empty or unnamed.

    Sorted rather than kept in the caller's order, because it is a *set* of securities and an
    iteration order that leaked into a census cell would make two identical universes report
    differently.
    """
    subjects = tuple(sorted(set(universe)))
    if not subjects:
        raise TwoStageFunnelError(
            "a screen needs a universe; an empty one would score nobody and report "
            "no_scored_candidate, which is a fact about a market rather than about the call"
        )
    if any(not subject.strip() for subject in subjects):
        raise TwoStageFunnelError("a universe cannot contain an unnamed security")
    return subjects


def _refuse_components_that_are_not_the_declared_ones(
    spec: ShortlistSpec, components: Sequence[ComponentCrossSection]
) -> tuple[ComponentCrossSection, ...]:
    """One cross section per declared component, in the spec's own order.

    A missing component, a repeated one and one the spec never declared are all malformed calls
    rather than market facts: a composite computed over the components that turned up would be a
    different statistic wearing the declared policy's name, which is exactly what
    `incomplete_components` refuses to do one level down for a single security.
    """
    offered = {component.factor_id: component for component in components}
    if len(offered) != len(components):
        raise TwoStageFunnelError(
            "two cross sections name one factor_id; which of them scored is not recoverable"
        )
    declared = set(spec.factor_ids)
    missing = sorted(declared - set(offered))
    if missing:
        raise TwoStageFunnelError(
            f"the screen declares {sorted(declared)} and no cross section was offered for "
            f"{missing}; a composite over the components that turned up is a different statistic"
        )
    extra = sorted(set(offered) - declared)
    if extra:
        raise TwoStageFunnelError(
            f"cross sections were offered for {extra}, which this screen does not declare; a "
            "factor with no weight has no place in the sum"
        )
    return tuple(offered[factor_id] for factor_id in spec.factor_ids)


def _read_components(
    spec: ShortlistSpec,
    components: Sequence[ComponentCrossSection],
    subjects: Sequence[str],
) -> tuple[
    tuple[ComponentCensus, ...],
    dict[str, dict[str, float]],
    dict[str, set[str]],
    dict[str, frozenset[str]],
]:
    """Split each component's rows into admitted values and valued-but-not-admitted subjects.

    The two tier tables are *imported* from `factor_ic` rather than restated, and both are
    load-bearing: `TIER_VALUE_CODES` decides `not_valued` and `TIER_ADMITTED_CODES` decides
    `not_admissible`, and they differ in exactly one cell across the three tiers (`processed`'s
    `imputed` carries a value and is not admitted). A screen that used one table for both would
    put a made-up median into every composite on the processed tier.

    Only securities in `subjects` are read: a stored row for a name the registry does not stand
    behind is not part of this cross section at all.
    """
    registry = set(subjects)
    vocabulary = frozenset(TIER_COVERAGE_ORDER[spec.tier])
    value_codes = TIER_VALUE_CODES[spec.tier]
    admitted_codes = TIER_ADMITTED_CODES[spec.tier]
    admitted: dict[str, dict[str, float]] = {}
    valued: dict[str, set[str]] = {}
    clipped: dict[str, frozenset[str]] = {}
    censuses: list[ComponentCensus] = []
    for component in components:
        rows = {
            subject: (value, coverage)
            for subject, value, coverage in component.values
            if subject in registry
        }
        for subject, (_value, coverage) in rows.items():
            if coverage not in vocabulary:
                raise TwoStageFunnelError(
                    f"{component.factor_id} codes {subject} {coverage!r}, which is not one of the "
                    f"{spec.tier} tier's codes {sorted(vocabulary)}"
                )
        component_admitted = {
            subject: float(value)
            for subject, (value, coverage) in rows.items()
            if coverage in admitted_codes and value is not None
        }
        component_valued = {
            subject
            for subject, (value, coverage) in rows.items()
            if coverage in value_codes and value is not None
        }
        component_clipped = component.clipped_subjects & registry
        admitted[component.factor_id] = component_admitted
        valued[component.factor_id] = component_valued
        clipped[component.factor_id] = frozenset(component_clipped)
        largest = max(component_admitted.values(), default=None)
        censuses.append(
            ComponentCensus(
                factor_id=component.factor_id,
                subject_count=len(rows),
                valued_count=len(component_valued),
                admitted_count=len(component_admitted),
                clipped_count=len(component_clipped),
                tied_at_the_top=(
                    0
                    if largest is None
                    else sum(1 for value in component_admitted.values() if value == largest)
                ),
            )
        )
    return tuple(censuses), admitted, valued, clipped


def _score(
    spec: ShortlistSpec,
    subjects: Sequence[str],
    *,
    admitted: Mapping[str, Mapping[str, float]],
    valued: Mapping[str, set[str]],
    clipped: Mapping[str, frozenset[str]],
) -> tuple[dict[str, ScoreCoverage], dict[str, _Composite]]:
    """Stage one: one composite per security with an admitted value on every declared component.

    The weights are normalised by their own total, so a screen declaring `(1.0, 1.0)` and one
    declaring `(0.5, 0.5)` produce the same ordering *and* the same numbers. That is not a
    convenience: an unnormalised sum makes the score's scale a function of how many components
    were declared, and a caller comparing two `as_of`s across a change of policy would be reading
    the change in scale as a change in the market.
    """
    total_weight = spec.total_weight
    coded: dict[str, ScoreCoverage] = {}
    scored: dict[str, _Composite] = {}
    for subject in subjects:
        present = [
            component for component in spec.components if subject in admitted[component.factor_id]
        ]
        if len(present) == len(spec.components):
            terms = tuple(
                _term(
                    component,
                    admitted[component.factor_id][subject],
                    total_weight,
                    clipped=subject in clipped[component.factor_id],
                )
                for component in spec.components
            )
            scored[subject] = _Composite(
                score=math.fsum(term.contribution for term in terms), components=terms
            )
            coded[subject] = "scored"
        elif present:
            coded[subject] = "incomplete_components"
        elif any(subject in valued[component.factor_id] for component in spec.components):
            coded[subject] = "not_admissible"
        else:
            coded[subject] = "not_valued"
    return coded, scored


def _term(
    component: ScoreComponent, value: float, total_weight: float, *, clipped: bool
) -> ComponentScore:
    """One component's term: the stored value, the declared orientation, the normalised weight.

    `clipped` comes from `ComponentCrossSection.clipped_subjects` rather than from a comparison
    against a bound recomputed here, because on the neutralised tier there is no bound in the
    values to compare against -- which is the whole of
    `a_neutralised_tier_orders_the_clip_block_by_industry_and_size`.
    """
    oriented = oriented_value(value, component.direction)
    weight = component.weight / total_weight
    return ComponentScore(
        factor_id=component.factor_id,
        value=value,
        oriented=oriented,
        weight=weight,
        contribution=weight * oriented,
        clipped=clipped,
    )


def _refusal_before_stage_two(
    spec: ShortlistSpec, *, scored: Mapping[str, _Composite], clip_block: int
) -> FunnelCoverage | None:
    """The two stage-one codes and the clip-block one, decided in the declared order.

    `None` means stage two may run. Decided here rather than inline so that the order is one
    readable table: `FunnelCoverage`'s docstring is the specification and this function is the
    whole of the implementation, which is what keeps a reordering from being invisible.
    """
    if not scored:
        return "no_scored_candidate"
    values = {composite.score for composite in scored.values()}
    if len(values) == 1:
        return "degenerate_scores"
    if spec.shortlist_size <= clip_block:
        return "cut_inside_the_clip_block"
    return None


def _empty_tradeability(scored_count: int) -> TradeabilityCensus:
    """The census stage two would have produced had it run: every scored name unoffered.

    `PortfolioCensus`' `unattempted_count` on this plane, and it is a zero-tradeable census rather
    than an absent one for the same reason -- a funnel refused before stage two still has to say
    how many securities were never offered to the market, and an absent census would make "the
    market refused everybody" and "nobody asked" the same reading. See
    `tests/unit/backtest/test_cross_section.py::
    test_a_funnel_refused_before_stage_two_reports_every_scored_name_as_unoffered`.
    """
    return TradeabilityCensus(
        scored_count=scored_count,
        tradeable_count=0,
        unoffered_count=scored_count,
        refused_by_verdict=tuple((code, 0) for code in REFUSED_VERDICT_ORDER),
        rejection_reasons=(),
        refused=(),
    )


def _refused_securities(
    verdicts: Mapping[str, TradeabilityVerdict], fills: Mapping[str, ExecutionResult]
) -> tuple[RefusedSecurity, ...]:
    """Every non-`tradeable` verdict as a named record, in the order the census requires.

    `V2-P4-066`. The names come out of the same mapping the counts are taken from and in one pass
    over it, so the two cannot describe different markets -- `TradeabilityCensus.__post_init__`
    then holds them to each other, which is what makes that a property rather than a convention.

    A `rejected` name's reason is `fills[subject].reason`, exactly as `_rejection_reasons` reads
    it and with the same `"unstated"` fallback: `ExecutionResult.reason` is optional, and a
    refusal with no sentence would otherwise be a `None` this record forbids.
    """
    return tuple(
        sorted(
            (
                RefusedSecurity(
                    subject=subject,
                    verdict=verdict,
                    reason=(fills[subject].reason or "unstated") if verdict == "rejected" else None,
                )
                for subject, verdict in verdicts.items()
                if verdict != "tradeable"
            ),
            key=lambda item: (REFUSED_VERDICT_ORDER.index(item.verdict), item.subject),
        )
    )


def _rejection_reasons(
    fills: Mapping[str, ExecutionResult], verdicts: Mapping[str, TradeabilityVerdict]
) -> tuple[tuple[str, int], ...]:
    """The policy's own refusal strings with their counts, most common first then alphabetically.

    Read off the `ExecutionResult`s rather than re-derived, because this module is not a second
    authority on why an order does not fill -- `PositionRejection`'s rule.
    """
    counted: dict[str, int] = {}
    for subject, verdict in verdicts.items():
        if verdict != "rejected":
            continue
        reason = fills[subject].reason or "unstated"
        counted[reason] = counted.get(reason, 0) + 1
    return tuple(sorted(counted.items(), key=lambda item: (-item[1], item[0])))
