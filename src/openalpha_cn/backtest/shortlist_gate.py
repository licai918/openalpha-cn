"""The shortlist-level gate (`V2-P4-023`): the third gate, and the one that refuses a *list*.

Two gates already stand between a market and a candidate list, and neither of them can see the
list. The roadmap row says so in one line -- 现有只有个股级过滤 `V2-P4-004` 与数据集级
fail-closed `V2-P1-013`, 榜单层无闸门 -- and the shape of the hole is worth stating exactly,
because every part of it is something that is working correctly:

- **Per-name.** `CrossSectionScreen._filter` asks `AShareExecutionPolicy` about one security at a
  time and admits it only when the policy filled a buy. `ShortlistEntry.__post_init__` and
  `RankedCandidate.__post_init__` then each refuse a non-`filled` execution outright. So every
  name on a shortlist has individually passed, and that is a per-*security* verdict about a
  per-*security* question.
- **Per-dataset.** `panel_gate.require_datasets` refuses to let a downstream read a dataset that
  is stale, short of a session, or damaged, and `DependencyClearance` carries the scope of the
  permission it grants. So every dataset behind those securities has individually passed, and
  that is a per-*dataset* verdict about a per-*dataset* question.
- **Per-list.** Nothing. `CrossSectionFunnel`'s five refusal codes are all about the extremes --
  nobody scored, every score tied, the cut inside the clip block, nobody tradeable, the cut
  selecting everybody -- and a market where 62.5% of the names were unbuyable but 3 remained,
  cut to 2, is none of the five. `CandidateRanking` then reports `researched_rate` and
  `coverage` faithfully and refuses nothing, because a ranking is a record and not a gate.

`tests/integration/test_shortlist_gate_refusal.py` builds exactly that: a real `PanelStore` the
real `require_datasets` clears without a block, a real screen over its real bars in which every
shortlisted name fills, and a two-name shortlist drawn from a market where three of eight
securities could be bought. Both existing gates say go. This module is what says stop.

## The thresholds are declared, and they are declared *into the identity*

`ShortlistGateSpec` has no defaults, which is this repository's rule since `V2-P3-005`: a
decision that moves the answer is one the caller records making. But "no default" is only half
of it, and the other half is the half that has bitten before -- a bar that lives as a module
constant makes two runs under two different bars indistinguishable, because nothing in either
run's identity mentions the bar.

So `ShortlistGateManifest` embeds the whole `ShortlistGateSpec` (rather than digesting it, which
is `CandidateRankingManifest.scoring_policy`'s own choice and for its reason: a digest of a model
this manifest can simply hold would be a second canonicalisation of one object, and the two can
disagree), beside the `ranking_manifest_id` of the declaration it is gating. `gate_manifest_id`
is `stable_model_id` over all of it, so **every threshold is in the address by construction** --
including a threshold added in a later version, without this class needing a field per bar.
`test_the_gate_manifest_address_moves_for_every_declared_threshold` varies each of them.

**Nothing is excluded from that address, and the empty exclusion set is a finding rather than an
omission.** `RANKING_MANIFEST_UNADDRESSED_FIELDS` has one entry, `built_at`, because a manifest
that recorded its own assembly clock could not be recognised as the same screen re-asked. This
manifest records no clock: the only time it is about is `as_of` and `built_at`, both of which
live on the ranking it points at, and it reaches them through `ranking_manifest_id` and through
the `CandidateRanking` the gate is handed. `GATE_MANIFEST_UNADDRESSED_FIELDS` is therefore empty
and `test_every_gate_manifest_field_is_addressed_or_excluded_by_name` partitions `model_fields`
against it, so field *n+1* is red until it is either measured to move the address or given a
reason there -- the audit shape `V2-P3-002`, `V2-P3-014`, `V2-P3-015`, `V2-P4-025` and
`V2-P4-005` each reused.

## `built_at` is the freshness clock, and the interaction with `V2-P4-005` is deliberate

A ranking's inputs are all at one instant by construction: `CandidateRanking.__post_init__`
requires every constituent signal's `as_of` to equal the manifest's, `rank_candidates` requires
the exposure cross section's to equal it too, and the funnel carries the same one. So "the
freshest input" has exactly one age -- `built_at - as_of`, the wall clock the list was assembled
on minus the moment it is about -- and there is no second candidate for the measurement.

`built_at` is the one `CandidateRankingManifest` field that `ranking_manifest_id` deliberately
excludes. That means two rankings can share a declaration address and get **different** verdicts
here, and that is the `V2-P3-014` split working rather than a hole in it: the manifest addresses
what was *asked for* and this clearance answers what was *measured*, exactly as
`ranking_content_digest` addresses the answer beside `ranking_manifest_id` addressing the
declaration. `the_freshness_clock_is_a_field_the_rankings_own_identity_excludes` says so, and
`test_the_freshness_bar_moves_with_the_wall_clock_the_ranking_was_assembled_on` drives it.

A live shortlist wants a tight bar; a backtest legitimately assembles a ranking about a 2020
session today and declares a wide one. Both are recorded in `gate_manifest_id`, which is the
whole point of the bar being an input.

## The tradable ratio divides by the universe, and that was measured rather than chosen

`CrossSectionFunnel` already publishes `tradeability.tradeable_rate`, which is
`tradeable_count / scored_count`, and reusing it would have been the obvious move. The
acceptance fixture measured why it is the wrong denominator: `601318.SH` is halted on the
screened session, so it has no bar, so a price factor has no value for it, so **stage one** files
it under `not_valued` and it never reaches the market at all. `tradeable / scored` is then 3/7 =
0.4286 while `tradeable / universe` is 3/8 = 0.375 -- the funnel's own rate is *raised* by
precisely the security a coverage bar exists to notice, because that security left the
denominator. A gate reading it would be relieved by its own bad news.

So `ShortlistMeasurement.tradable_ratio` is `tradeable_count / universe_count`, the end-to-end
number, and `scored_count` is carried beside it so a reader can see which of the two stages the
loss happened at. `the_tradable_ratio_divides_by_the_universe_because_the_funnels_own_denominator
_shrinks` carries both figures.

**And that choice removes a block code rather than adding one.** `universe_count` is `ge=1` on
`CandidateRankingManifest`, `build_ranking_manifest` refuses an empty universe outright and
`CrossSectionScreen.select` refuses one too, so `tradeable / universe` is *always* measurable and
a `tradable_ratio_not_measurable` code would be a branch no input can take. That is exactly the
measurement that removed `TradeabilityVerdict`'s `not_in_registry`, and it is not written here
for the same reason. `researched_ratio_not_measurable` **is** written, because `researched_rate`
is genuinely `None` whenever the funnel shortlisted nobody.

## A verdict, not a collection

`ShortlistClearance` is `DependencyClearance`'s shape, and it is copied deliberately rather than
re-invented, because `V2-P1-013` already paid for the design and the roadmap row for this issue
asks for the same property in the same words (返回显式阻塞态). `bool()`, `len()` and iteration all
raise -- **including on an admitted clearance**. `PanelReadOutcome` established one plane down
that two different values are only half a fix: `if not clearance:` and `clearance or []` merge
blocked with admitted-and-empty at run time while type-checking clean, and those are the lines
people actually write. An accessor that answered on an admitted clearance and raised on a blocked
one would be worse than either -- every test written against a healthy market would pass and the
line would fail only in production.

The three states are therefore reached by name and are all three genuinely reachable:

- **blocked** -- `is_blocked` is `True`, `admitted` raises naming every code that failed, and the
  ranking underneath is untouched and un-truncated. `ranking_content_digest` is carried so a
  caller can say *which* list was refused.
- **admitted, with candidates** -- `admitted` returns them, in the funnel's own order.
- **admitted, with none** -- a shortlist every name of which came back unresearched, under a
  `minimum_researched_ratio` of zero. `admitted` returns `()`. This is a real answer and it is
  the one a caller must not confuse with the first, which is why the two are produced by
  different code paths and never by a length.

`tests/integration/test_shortlist_gate_refusal.py::
test_a_blocked_clearance_and_an_empty_one_are_reached_by_different_code_paths` drives the first
and third as a **cross** -- the blocked one has two candidates underneath and the admitted one
has none -- so neither can be read as the other.

## What this gate does not do

It does not re-cut, re-rank, substitute, or truncate. A refusal is the whole list withheld and
the reason named; there is no partial list, and `this_gate_refuses_a_list_and_can_never_repair
_one` says so. `V2-P4-006` is where a governed screen re-orders one.

## Layering

This is a `backtest/` leaf -- the twelfth -- and the placement is again the enforcement, made by
exclusion the way `V2-P4-005`'s was:

- **`domain/` cannot hold it.** `domain-purity` forbids `openalpha_cn.backtest`, and this module's
  entire input is a `CandidateRanking`, which is `backtest/`'s. It could not carry its argument.
- **`product/` would hold it and enforce nothing.** Nothing forbids `product` to import
  `openalpha_cn.storage`, `openalpha_cn.runtime` or `domain.portfolio`, and `product/research.py`
  already imports `runtime.contracts`. A gate that could reach the composition root is a gate
  whose own refusal it could route around, and there the prohibition would be a sentence.
- **A top-level module beside `panel_gate.py` would be in no contract's source set at all.**
  That is precisely the P3 acceptance finding this repository installed
  `tests/unit/test_import_layering.py` to stop: eight new `backtest/` modules were in no
  contract, and a probe importing `duckdb`, `panel.store` and `runtime.composition` passed
  `lint-imports` at "4 kept, 0 broken". `panel_gate.py` is top-level because it *consumes* the
  panel plane; this gate consumes neither a store nor a panel and would only inherit the freedom
  to.
- **`backtest/` forbids all of it already, once this module is on the lists.**
  `backtest-no-numeric-stack-or-panel-plane`, `backtest-studies-touch-no-store`,
  `backtest-studies-reach-no-composition-root` and `ranking-creates-no-portfolio-order` -- this
  module joins the source list of the last three on arrival, which is what
  `test_the_two_backtest_study_contracts_cover_every_module_in_the_package` exists to force.
  That clause is load-bearing and `V2-P4-093` measured why: three of the four enumerate their
  sources, so a file that has not joined them is forbidden nothing by them, and a probe under
  `backtest/` importing `numpy` and a store reads `8 kept, 0 broken`. **`lint-imports` stays at
  8 kept, 0 broken; three contracts widen and none is relaxed.**

`ranking-creates-no-portfolio-order` is widened rather than left alone, and that is the one
judgement here that is not forced. D16's 绝不直接创建组合订单 is a property of the ranking, and a
gate that could construct a `PortfolioOrder` would be a module in which "this list was refused"
and "an order was made from it" could both be true in one place. The contract's `id` is kept --
`tests/unit/backtest/test_candidate_ranking.py` reads it by name -- and its `name` now says
"contracts" rather than "contract".

Standard library plus `backtest/candidate_ranking.py`, `backtest/cross_section.py` and
`domain/`. One new edge inside `backtest/` and no new external dependency. It stores nothing,
reads no partition, computes no return, fits no model and creates no order. **Runtime
dependencies remain nine.**
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field

from openalpha_cn.backtest.candidate_ranking import (
    CandidateRanking,
    RankedCandidate,
)
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "GATE_MANIFEST_UNADDRESSED_FIELDS",
    "KNOWN_SHORTLIST_GATE_LIMITATIONS",
    "RANKING_MANIFEST_ID_PATTERN",
    "SHORTLIST_BLOCK_CODES",
    "SHORTLIST_BLOCK_ORDER",
    "SHORTLIST_GATE_LIMITATION_CODES",
    "ShortlistBlockCode",
    "ShortlistClearance",
    "ShortlistGateBlock",
    "ShortlistGateError",
    "ShortlistGateLimitation",
    "ShortlistGateManifest",
    "ShortlistGateSpec",
    "ShortlistMeasurement",
    "gate_shortlist",
]


class ShortlistGateError(ValueError):
    """Raised for a malformed gate call, and for reading a blocked clearance as an admitted one.

    **Never for a fact about the market.** A list that fell below a bar is not this: it is a
    `ShortlistGateBlock` on a returned `ShortlistClearance`, because a caller walking a year of
    `as_of`s has to be able to keep going past a refusal, which is `FunnelCoverage`'s and
    `CandidateRankingError`'s stated rule one and two planes down.

    `ValueError` rather than `RuntimeError` -- `TwoStageFunnelError`'s and
    `CandidateRankingError`'s base, so every call site already writing `except ValueError` around
    this plane keeps catching it. `PanelGateError` is a `RuntimeError` because the panel gate's
    refusals are about a store; nothing here touches one.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistGateLimitation:
    """One named boundary on what clearing this gate can be trusted to mean."""

    code: str
    detail: str


KNOWN_SHORTLIST_GATE_LIMITATIONS: Final[tuple[ShortlistGateLimitation, ...]] = (
    ShortlistGateLimitation(
        code="the_tradable_ratio_divides_by_the_universe_because_the_funnels_own_denominator_shrinks",
        detail=(
            "CrossSectionFunnel.tradeability.tradeable_rate is tradeable_count / scored_count and "
            "this gate does NOT use it, because that denominator is shrunk by the very securities "
            "a coverage bar exists to notice. Measured on the acceptance panel: 601318.SH is "
            "halted on the screened session, so it has no bar, so a price factor has no value for "
            "it, so stage one files it under not_valued and it never reaches the market at all. "
            "tradeable / scored is then 3/7 = 0.4286 while tradeable / universe is 3/8 = 0.375 -- "
            "the funnel's own rate is HIGHER, and a bar read against it would be relieved by its "
            "own bad news. So tradable_ratio is tradeable_count / universe_count, and "
            "scored_count is carried beside it so a reader can see which stage the loss happened "
            "at. What is NOT claimed is that the funnel's rate is wrong: it answers 'of the names "
            "this screen could score, how many could it buy', which is a different question and "
            "the right one for reading a census."
        ),
    ),
    ShortlistGateLimitation(
        code="the_freshness_clock_is_a_field_the_rankings_own_identity_excludes",
        detail=(
            "ranking_age_days is built_at - as_of, and built_at is the ONE "
            "CandidateRankingManifest field RANKING_MANIFEST_UNADDRESSED_FIELDS keeps out of "
            "ranking_manifest_id -- because a manifest that addressed its own assembly clock "
            "could not be used to recognise the same screen re-asked. So two rankings sharing a "
            "ranking_manifest_id can get different verdicts from this gate, and that is the "
            "V2-P3-014 split working rather than a hole in it: the manifest addresses what was "
            "asked for and this clearance answers what was measured. A caller storing a verdict "
            "beside a ranking_manifest_id and expecting the pair to be reproducible is reading "
            "the address as something it has never been."
        ),
    ),
    ShortlistGateLimitation(
        code="freshness_is_counted_in_calendar_days_because_this_leaf_reaches_no_calendar",
        detail=(
            "maximum_ranking_age_days is calendar days, floored to whole days, and NOT trading "
            "sessions. backtest-no-numeric-stack-or-panel-plane forbids openalpha_cn.panel, so "
            "TradingCalendar is not reachable from here and a session count could not be derived "
            "without either importing the panel plane or taking a calendar as an argument this "
            "contract has no other use for. Two consequences, both real: a bar of 3 days spans a "
            "weekend on a Friday as_of and does not on a Tuesday one, so the same bar admits a "
            "different number of sessions depending on the weekday; and a ranking assembled 47 "
            "hours after its as_of measures 1 day rather than 2, because the floor is taken "
            "rather than the ceiling. V2-P4-001 narrowed SignalFrame.horizon to a COUNT OF "
            "SESSIONS for exactly the reason this unit is the weaker one, and the asymmetry is "
            "recorded rather than hidden: the horizon is a declared span the panel plane resolves "
            "and this is a wall-clock age nothing resolves."
        ),
    ),
    ShortlistGateLimitation(
        code="this_gate_refuses_a_list_and_can_never_repair_one",
        detail=(
            "A refusal is the whole list withheld and every failed bar named. This module does "
            "not re-cut to a smaller shortlist, does not re-rank, does not substitute a tradeable "
            "name for an untradeable one and does not truncate -- ShortlistClearance carries the "
            "ranking's own content digest so a caller can say WHICH list was refused, and "
            "admitted is the ranking's candidates verbatim or nothing. A gate that returned a "
            "shorter list would be the failure this issue exists to remove wearing the fix's "
            "name, because a caller cannot tell a repaired list from a list that was always that "
            "length. D17 keeps re-ordering a separate step on purpose and V2-P4-006 is where a "
            "governed screen does it."
        ),
    ),
    ShortlistGateLimitation(
        code="an_admitted_clearance_is_a_coverage_and_age_verdict_and_not_a_quality_one",
        detail=(
            "Clearing these three bars says the list covers enough of its universe, enough of its "
            "own shortlist came back researched, and it was assembled soon enough after the "
            "session it is about. It says nothing about whether the ordering means anything, and "
            "every caveat one plane down survives intact: "
            "KNOWN_CROSS_SECTION_LIMITATIONS.the_shortlist_is_not_a_ranking_of_expected_return "
            "(the weights are fitted to nothing), .the_cut_is_broken_by_subject_code_when_two "
            "_scores_tie, .a_neutralised_tier_orders_the_clip_block_by_industry_and_size, and "
            "KNOWN_RANKING_LIMITATIONS.the_ranking_does_not_re_rank_and_inherits_every_caveat_on "
            "_the_funnels_order. The acceptance fixture is the demonstration: its two admitted "
            "candidates are the two names its own factor liked LEAST of the three it could buy, "
            "and a tradable floor of 0.25 admits that list without a word about it. The "
            "RankedCandidate.risk_flags each candidate already carries are where a per-name "
            "warning lives; this gate adds no flag and removes none."
        ),
    ),
    ShortlistGateLimitation(
        code="dataset_level_staleness_is_v2_p1_013s_and_is_not_restated_or_re_measured_here",
        detail=(
            "panel_gate.require_datasets decides whether a dataset is fresh enough to read, from "
            "DatasetReadiness.freshness and a cadence-derived threshold, and it is unreachable "
            "from backtest/ by contract. This gate measures ONE age -- the ranking's own -- and a "
            "ranking can be assembled minutes after its as_of out of a panel whose adj_factor "
            "partition is a month stale. Those are two different questions and clearing this one "
            "is not evidence about the other. The composition is the caller's: the acceptance "
            "test runs require_datasets first and this gate second, in that order, and neither "
            "consults the other's verdict. Nothing here can even observe whether the first was "
            "asked, which is the honest reading of 'no gate at the list layer' -- it is a third "
            "gate, not a replacement for either."
        ),
    ),
    ShortlistGateLimitation(
        code="a_bar_of_zero_is_legal_and_switches_its_own_check_off",
        detail=(
            "minimum_tradable_ratio and minimum_researched_ratio are ge=0.0, so 0.0 is a legal "
            "declaration and admits any measured ratio -- there is no floor under the floor. That "
            "is deliberate and it is not a hole, because the value is a DECLARED input that "
            "enters gate_manifest_id: a run that switched a bar off is distinguishable from one "
            "that met it, by address, forever. The alternative -- a minimum minimum -- would be "
            "the constant this whole contract exists to avoid, and MINIMUM_SHORTLIST already "
            "admits a vacuous floor one plane down in the same words and for the same reason. "
            "What a zero does NOT do is make the check disappear: the measurement is still taken "
            "and still reported on ShortlistMeasurement, and researched_ratio_not_measurable is "
            "still raised under a zero floor, because a ratio that could not be computed has not "
            "met a bar of zero -- it has not met anything."
        ),
    ),
)
"""What clearing a shortlist gate does not answer, as a closed registry rather than as prose.

The twenty-third `KNOWN_*` registry. Every entry is bound to the suite by
`tests/unit/test_known_limitation_registries.py`, which requires each `code` to appear as a
string literal in *executable* test code -- the P2 review measured that a code named only in
docstrings can be renamed with the whole suite staying green.
"""

SHORTLIST_GATE_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_SHORTLIST_GATE_LIMITATIONS
)


RANKING_MANIFEST_ID_PATTERN: Final[str] = r"^rnk_[0-9a-f]{24}$"
"""Exactly what `stable_model_id(prefix='rnk', ...)` produces, and nothing else.

`RUN_MANIFEST_ID_PATTERN`'s rule applied to this contract's one pointer: an address that is only
conventionally an address stops being one the first time it is convenient. Attached to
`ShortlistGateManifest.ranking_manifest_id` so a hand-built manifest cannot point at a string
somebody typed.

**Declared here rather than imported, and bound by measurement rather than by convention.**
`candidate_ranking.py` does not export a pattern for its own computed id -- it exports
`UNIVERSE_DIGEST_PATTERN` for an input it validates and nothing for an output it produces -- so
there is no constant to import. Restating it is `MAXIMUM_SHORTLIST`'s arrangement and it carries
`MAXIMUM_SHORTLIST`'s obligation: `tests/unit/backtest/test_shortlist_gate.py::
test_the_ranking_id_pattern_is_the_one_a_real_ranking_manifest_actually_produces` builds a real
`CandidateRankingManifest` and requires this pattern to match its real `ranking_manifest_id` and
to reject the other three prefixes this repository mints, so a change to `stable_model_id`'s
output shape fails here rather than leaving a decorative pattern standing.
"""

_RANKING_MANIFEST_ID: Final[re.Pattern[str]] = re.compile(RANKING_MANIFEST_ID_PATTERN)


ShortlistBlockCode = Literal[
    "tradable_ratio_below_floor",
    "researched_ratio_not_measurable",
    "researched_ratio_below_floor",
    "ranking_is_stale",
]
"""Every reason this gate can refuse a list, as a closed set, in the order it decides them.

Closed for `FunnelCoverage`'s reason: a caller reading `blocking_codes()` against a fixed set
can act on each member, and a fifth spelling of "the coverage was bad" would silently become a
code nobody handles.

- **`tradable_ratio_below_floor`** -- `tradeable_count / universe_count` is under
  `minimum_tradable_ratio`. Decided first because it is the only one of the four that is about
  the *market* rather than about the run or the clock.
- **`researched_ratio_not_measurable`** -- the funnel shortlisted nobody, so there is no ratio.
  Decided before the floor below it, because the two are one `if`/`elif` over the same quantity.
  Its own code rather than a zero, `TradeabilityCensus.tradeable_rate`'s and
  `CandidateRanking.researched_rate`'s stated reason one plane down: "every shortlisted name
  failed to research" and "there was nothing to research" are different findings and a rate of
  zero collapses them. It **blocks**, including under a floor of zero, because a ratio that could
  not be computed has not met a bar of zero -- it has not met anything -- and the funnel's own
  coverage code travels in the detail so the caller is told which of the five it was.
- **`researched_ratio_below_floor`** -- `candidate_count / shortlist_count` is under
  `minimum_researched_ratio`: the funnel asked the evidence plane for a shortlist and enough of
  it did not come back.
- **`ranking_is_stale`** -- `built_at - as_of` in whole calendar days exceeds
  `maximum_ranking_age_days`. Last because it is the only one that can be true of a list that is
  otherwise perfect.

**There is no `tradable_ratio_not_measurable`, and the absence is a finding rather than an
omission.** `universe_count` is `ge=1` on `CandidateRankingManifest`, `build_ranking_manifest`
refuses an empty universe outright and `CrossSectionScreen.select` refuses one too, so
`tradeable / universe` is always computable and the branch could not be reached by any input.
That is exactly the measurement that removed `TradeabilityVerdict`'s `not_in_registry`, and it is
not written here for the same reason: a branch no input can take is not evidence of anything.
"""

SHORTLIST_BLOCK_CODES: Final[frozenset[str]] = frozenset(get_args(ShortlistBlockCode))

SHORTLIST_BLOCK_ORDER: Final[tuple[ShortlistBlockCode, ...]] = get_args(ShortlistBlockCode)
"""The order every `ShortlistClearance.blocks` is reported in.

Declared rather than alphabetical and asserted rather than produced by a `sorted()` call, so two
clearances that failed the same two bars report them in the same order and a caller diffing two
runs is not reading an iteration order as a change. `RANKING_RISK_FLAG_ORDER`'s arrangement.
"""


class ShortlistGateSpec(BaseModel):
    """The declared bars a candidate list has to clear. Nothing here has a default.

    `ShortlistSpec`'s rule and this repository's since `V2-P3-005`: each of these moves whether a
    list ships, and a decision that moves the answer is one the caller records making. They are
    fields on a hashed model rather than module constants for the stronger reason -- see this
    module's docstring -- so that two runs under two different bars cannot share an address.

    Three numbers and two concepts, which is the roadmap row's own shape
    (整榜覆盖率**或**新鲜度): two coverage floors over two different denominators, and one age.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["shortlist-gate-spec/v1"] = "shortlist-gate-spec/v1"
    minimum_tradable_ratio: float = Field(ge=0.0, le=1.0)
    """The floor under `tradeable_count / universe_count`.

    Divided by the universe rather than by the scored count; see this module's docstring for the
    3/7-against-3/8 measurement that decided it.
    """
    minimum_researched_ratio: float = Field(ge=0.0, le=1.0)
    """The floor under `candidate_count / shortlist_count`."""
    maximum_ranking_age_days: int = Field(ge=0)
    """The ceiling over `built_at - as_of`, in whole calendar days.

    `0` is legal and means "assembled the same day it is about". Calendar days rather than
    trading sessions; see `freshness_is_counted_in_calendar_days_because_this_leaf_reaches_no
    _calendar` for the two consequences that has.
    """


GATE_MANIFEST_UNADDRESSED_FIELDS: Final[Mapping[str, str]] = MappingProxyType({})
"""Every `ShortlistGateManifest` field that is **recorded but not addressed**, with why.

Empty, and the emptiness is the finding. `RANKING_MANIFEST_UNADDRESSED_FIELDS` has one entry
because a ranking records its own assembly clock and addressing it would stop the address
recognising the same screen re-asked. This manifest records no clock and observes no host: it
holds the address of the declaration it gates and the bars it gates it with, and both of those
are things a caller *asked for*. Two gate runs of one declaration under one set of bars are the
same declaration and share an address; nothing about them can differ.

A mapping rather than a set because the reason is the load-bearing half, and it is declared while
empty rather than omitted so that field *n+1* has somewhere to be argued for.
`tests/unit/backtest/test_shortlist_gate.py::
test_every_gate_manifest_field_is_addressed_or_excluded_by_name` partitions
`ShortlistGateManifest.model_fields` against this mapping -- the audit shape `V2-P3-002`,
`V2-P3-014`, `V2-P3-015`, `V2-P4-025` and `V2-P4-005` each reused.
"""


class ShortlistGateManifest(BaseModel):
    """Which list was gated, and with which bars, as a content address.

    `spec` is embedded rather than digested, `CandidateRankingManifest.scoring_policy`'s own
    choice and for its reason: a digest of a model this manifest can simply hold would be a
    second canonicalisation of one object and the two can disagree. Embedding also makes the
    identity automatically sensitive to **every** declared threshold, including one added in a
    later schema version, without this class needing a field per bar.

    `ranking_manifest_id` rather than the whole `CandidateRankingManifest`, and rather than
    copies of its universe, as-of, horizon and policy: `V2-P4-005` made that string the content
    address of the ranking's entire declaration, so carrying it inherits every declared input at
    once. `RankedCandidate.run_manifest_id` carries `RunManifest`'s for the same reason and states
    it in the same words.

    `schema_version` without a `ContractVersions` registry, `CandidateRankingManifest`'s
    precedent: nothing under `backtest/` can reach a store, so there is no stored payload for
    `read_versioned` to upgrade and a registry here would be a table with no reader. It is still a
    field and it still enters the identity, because a v2 declaration shape is a different
    declaration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["shortlist-gate-manifest/v1"] = "shortlist-gate-manifest/v1"
    ranking_manifest_id: str = Field(pattern=RANKING_MANIFEST_ID_PATTERN)
    spec: ShortlistGateSpec

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def gate_manifest_id(self) -> str:
        """The content address of this gate's **declaration**: the list asked for and the bars.

        `stable_model_id` over every field except `GATE_MANIFEST_UNADDRESSED_FIELDS`, which is
        empty -- so two gate runs that name one ranking declaration and one set of thresholds
        share an address, and two that differ in *any* threshold do not. That second half is the
        property the roadmap row asks for and the reason the bars are a model rather than
        constants.
        """
        return stable_model_id(
            prefix="sgt", model=self, exclude=frozenset(GATE_MANIFEST_UNADDRESSED_FIELDS)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistMeasurement:
    """What this gate measured, carried whether or not anything failed.

    Present on an **admitted** clearance too, `DependencyClearance.notices`' reason: "cleared" is
    a verdict rather than silence, and a caller that can read the numbers a verdict rests on can
    see a list that scraped over a bar as well as one that sailed over it.

    The four counts are carried beside the two ratios rather than left to be recomputed, because
    the interesting reading of a low `tradable_ratio` is *where* the loss happened -- a universe
    of 8 that scored 7 and could buy 3 is a different market from one that scored 3 and could buy
    all 3, and the ratio alone is 0.375 in both.
    """

    universe_count: int
    scored_count: int
    tradeable_count: int
    shortlist_count: int
    candidate_count: int
    tradable_ratio: float
    researched_ratio: float | None
    ranking_age_days: int

    @property
    def scored_ratio(self) -> float:
        """`scored / universe`: the first of the two stages `tradable_ratio` composes over.

        Not a gated quantity and deliberately so -- a bar on it would be a second, weaker
        statement of the one bar this contract has, since `tradable_ratio` is already this times
        the funnel's own `tradeable_rate`. It is offered so a reader of a refusal can see which
        stage lost the names.
        """
        return self.scored_count / self.universe_count


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistGateBlock:
    """One bar this list did not clear.

    `measured` is `None` for exactly `researched_ratio_not_measurable`, which is the block raised
    *because* there was no number -- and it is `None` rather than `0.0` for
    `researched_rate`'s own stated reason: a rate of zero and an absent rate are different
    findings and this record must not be the place they are collapsed.

    `required` is the declared bar the measurement was read against, carried on the block rather
    than left on the spec, so a log line quoting one block quotes both halves of the comparison.
    """

    code: ShortlistBlockCode
    detail: str
    measured: float | None
    required: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortlistClearance:
    """Whether this candidate list may be published, and everything the verdict rests on.

    ## This is a verdict, not a collection, and it refuses to be used as one

    `bool()`, `len()` and iteration all raise -- **including on an admitted clearance**, which is
    the deliberate part. See this module's docstring for `PanelReadOutcome`'s measurement, which
    `DependencyClearance` already acted on one plane over and which this class copies rather than
    re-derives.

    So the states are reached by name: `is_blocked`, `admitted` (which raises when blocked) and
    `admitted_or_none` (the merged shape, under a name that says what it is).

    `ranking_content_digest` is `CandidateRanking.content_digest` carried onto the verdict, so a
    refusal names *which* list was refused rather than only that one was -- and so two refusals
    of the same list are recognisably that. `measurement` is present on both verdicts.
    """

    manifest: ShortlistGateManifest
    ranking_content_digest: str
    measurement: ShortlistMeasurement
    blocks: tuple[ShortlistGateBlock, ...]
    admitted_or_none: tuple[RankedCandidate, ...] | None

    def __post_init__(self) -> None:
        codes = tuple(block.code for block in self.blocks)
        if len(set(codes)) != len(codes):
            raise ShortlistGateError(f"this clearance carries {list(codes)}, which repeats a bar")
        expected = tuple(code for code in SHORTLIST_BLOCK_ORDER if code in set(codes))
        if codes != expected:
            raise ShortlistGateError(
                f"this clearance reports {list(codes)}; blocks are reported in "
                f"{list(SHORTLIST_BLOCK_ORDER)}, so two clearances failing one pair of bars "
                "report them in one order and a caller diffing two runs is not reading an "
                "iteration order as a change"
            )
        if bool(self.blocks) != (self.admitted_or_none is None):
            raise ShortlistGateError(
                f"this clearance carries {len(self.blocks)} blocks and "
                f"{'no' if self.admitted_or_none is None else 'an'} admitted list; a list is "
                "admitted exactly when nothing blocked it, and any other pairing is a refusal "
                "that shipped or a clearance nobody granted"
            )

    @property
    def is_blocked(self) -> bool:
        return self.admitted_or_none is None

    @property
    def admitted(self) -> tuple[RankedCandidate, ...]:
        """The candidates this list may be published with, or `ShortlistGateError` if refused.

        `()` is a real answer and is **not** the refusal: it is a shortlist every name of which
        came back unresearched, under a `minimum_researched_ratio` this caller declared it could
        live with. The refusal raises, so the two cannot be reached by one code path.
        """
        if self.admitted_or_none is None:
            raise ShortlistGateError(
                f"this candidate list is blocked by {[block.code for block in self.blocks]}: "
                + "; ".join(block.detail for block in self.blocks)
                + " -- an empty admitted list is a different answer and is returned rather than "
                "raised, so use `admitted_or_none` to handle blocked and admitted together on "
                "purpose"
            )
        return self.admitted_or_none

    def blocking_codes(self) -> frozenset[str]:
        return frozenset(block.code for block in self.blocks)

    def block_with_code(self, code: str) -> ShortlistGateBlock | None:
        """This gate's block under `code`, or `None`; raises for a code it cannot issue.

        `DependencyClearance.blocks_with_code`'s rule: a caller that asks about
        `tradeable_ratio_below_floor` -- a plausible misspelling of a real code -- and receives
        `None` reads the list as having cleared that bar.

        Singular rather than a tuple, because each member of `ShortlistBlockCode` is decided once
        per clearance by construction, and `__post_init__` refuses a repeat.
        """
        if code not in SHORTLIST_BLOCK_CODES:
            raise ShortlistGateError(
                f"{code!r} is not a bar this gate can refuse on; it declares "
                f"{list(SHORTLIST_BLOCK_ORDER)}"
            )
        return next((block for block in self.blocks if block.code == code), None)

    def __bool__(self) -> bool:
        raise ShortlistGateError(_NOT_A_COLLECTION)

    def __len__(self) -> int:
        raise ShortlistGateError(_NOT_A_COLLECTION)

    def __iter__(self) -> Iterator[RankedCandidate]:
        raise ShortlistGateError(_NOT_A_COLLECTION)


_NOT_A_COLLECTION: Final[str] = (
    "a shortlist clearance is a verdict, not a collection: `if not clearance:`, `clearance or []` "
    "and `len(clearance)` are the three lines that merged blocked with empty in every prior "
    "instance of this defect, so they raise here whether or not this list was admitted -- an "
    "accessor that answered on an admitted clearance would pass every test written against a "
    "healthy market and fail only in production. Ask `is_blocked`, read `admitted` (which raises "
    "when blocked), or name the merged shape as `admitted_or_none`"
)


def gate_shortlist(*, ranking: CandidateRanking, spec: ShortlistGateSpec) -> ShortlistClearance:
    """Measure one candidate list against one declared set of bars, and admit it or refuse it.

    The only builder in `src/`, `build_ranking_manifest`'s arrangement for its reason: the
    manifest's `ranking_manifest_id` is computed from the ranking rather than accepted, so a
    caller cannot gate one list and record the address of another.

    Refuses a ranking assembled **before** the session it is about, and that is a
    `ShortlistGateError` rather than a block code: a negative age is not a fact about the market
    or a bar this list failed, it is a look-ahead, and this repository's whole point-in-time
    discipline says a list that could not have been built when it claims to have been is
    malformed rather than merely stale.
    """
    manifest = ShortlistGateManifest(
        ranking_manifest_id=_ranking_manifest_id_of(ranking), spec=spec
    )
    measurement = _measure(ranking)
    blocks = _blocks_for(measurement, spec=spec, coverage=ranking.coverage)
    return ShortlistClearance(
        manifest=manifest,
        ranking_content_digest=ranking.content_digest,
        measurement=measurement,
        blocks=blocks,
        admitted_or_none=None if blocks else ranking.candidates,
    )


def _ranking_manifest_id_of(ranking: CandidateRanking) -> str:
    """The gated ranking's declaration address, checked against the shape it must have.

    A computed field cannot be anything else today, so this guard is about the *next* change
    rather than about a caller: it is what fails if `stable_model_id`'s output shape moves, in
    the module that would otherwise carry a decorative pattern.
    """
    identity = ranking.manifest.ranking_manifest_id
    if _RANKING_MANIFEST_ID.fullmatch(identity) is None:
        raise ShortlistGateError(
            f"this ranking's manifest address is {identity!r}, which is not "
            "stable_model_id(prefix='rnk', ...)'s own output; a pointer that is only "
            "conventionally a content address stops being one the first time it is convenient"
        )
    return identity


def _measure(ranking: CandidateRanking) -> ShortlistMeasurement:
    """The four counts, the two ratios and the age, read off the ranking and re-derived nowhere.

    Every count here is somebody else's already: `universe_count` and `scored_count` are
    `ScoreCensus`', `tradeable_count` is `TradeabilityCensus`', `shortlist_count` is the funnel's
    and `candidate_count` is the ranking's. A second count taken here would be a second answer to
    a question those records exist to answer once.

    **The `universe_count < 1` guard cannot fire through a `CandidateRanking`, and it is written
    anyway** -- which is the opposite call from the one that removed `TradeabilityVerdict`'s
    `not_in_registry`, so it needs its reason. That one was a *reported verdict* on a branch no
    input could take, and a verdict nothing can produce is evidence of nothing. This is the
    divisor's precondition, stated at the division rather than inferred from a `Field(ge=1)` two
    classes away: `CandidateRankingManifest.universe_count` carries that constraint and
    `CandidateRanking.__post_init__` requires the funnel's census to equal it, so the pairing that
    would reach this line does not construct. It raises rather than returning a code precisely
    because it is unreachable -- a `ZeroDivisionError` here would be a defect and not a market
    fact. `tests/unit/backtest/test_shortlist_gate.py::
    test_there_is_no_tradable_ratio_not_measurable_code_because_no_input_reaches_it` drives both
    refusals that make it unreachable, so a later change that loosens either one fails there.
    """
    funnel = ranking.funnel
    universe_count = funnel.scores.universe_count
    if universe_count < 1:
        raise ShortlistGateError(
            f"this ranking's funnel scored over {universe_count} securities; a list drawn from "
            "an empty market has no coverage to measure, and neither build_ranking_manifest nor "
            "CrossSectionScreen.select can produce one"
        )
    age = _age_in_days(ranking)
    return ShortlistMeasurement(
        universe_count=universe_count,
        scored_count=funnel.scores.scored_count,
        tradeable_count=funnel.tradeability.tradeable_count,
        shortlist_count=funnel.shortlist_count,
        candidate_count=ranking.candidate_count,
        tradable_ratio=funnel.tradeability.tradeable_count / universe_count,
        researched_ratio=ranking.researched_rate,
        ranking_age_days=age,
    )


def _age_in_days(ranking: CandidateRanking) -> int:
    """`built_at - as_of`, floored to whole calendar days, refusing a negative.

    Floor rather than ceiling, so the reported age is "how many whole days have passed" and a
    ranking assembled 47 hours after its `as_of` measures 1. That rounding is disclosed in
    `freshness_is_counted_in_calendar_days_because_this_leaf_reaches_no_calendar` rather than
    argued away, because either direction is defensible and only a stated one is checkable.
    """
    manifest = ranking.manifest
    elapsed = ensure_aware(manifest.built_at) - ensure_aware(manifest.as_of)
    if elapsed < timedelta(0):
        raise ShortlistGateError(
            f"this ranking is as of {manifest.as_of.isoformat()} and was assembled at "
            f"{manifest.built_at.isoformat()}, which is before it; a list that could not have "
            "been built when it says it was is a look-ahead rather than a stale list, so it is "
            "refused here instead of being measured"
        )
    return elapsed // timedelta(days=1)


def _blocks_for(
    measurement: ShortlistMeasurement,
    *,
    spec: ShortlistGateSpec,
    coverage: str,
) -> tuple[ShortlistGateBlock, ...]:
    """The bars this measurement failed, decided in `SHORTLIST_BLOCK_ORDER` and returned in it.

    Appended in `SHORTLIST_BLOCK_ORDER` rather than sorted into it afterwards, so the declaration
    is the specification and this function is the whole of the implementation --
    `_risk_flags_of`'s arrangement one plane down, which is what keeps a reordering from being
    invisible. `ShortlistClearance.__post_init__` then holds the result to that order, so the two
    cannot drift apart silently. **Every bar is evaluated**; there is no early return, because a
    caller told only about the coverage would fix it and meet the staleness on the next run.
    """
    blocks: list[ShortlistGateBlock] = []

    if measurement.tradable_ratio < spec.minimum_tradable_ratio:
        blocks.append(
            ShortlistGateBlock(
                code="tradable_ratio_below_floor",
                measured=measurement.tradable_ratio,
                required=spec.minimum_tradable_ratio,
                detail=(
                    f"{measurement.tradeable_count} of the {measurement.universe_count} "
                    "securities this list's universe holds could be bought at its as_of "
                    f"({measurement.tradable_ratio:.4f}), and this gate was declared with a floor "
                    f"of {spec.minimum_tradable_ratio:.4f}. "
                    f"{measurement.scored_count} of them were scored at all, so the loss is "
                    f"{measurement.universe_count - measurement.scored_count} at stage one and "
                    f"{measurement.scored_count - measurement.tradeable_count} at stage two. "
                    "Every shortlisted name still filled its own buy; this is the list's coverage "
                    "and not any one candidate's verdict"
                ),
            )
        )

    if measurement.researched_ratio is None:
        blocks.append(
            ShortlistGateBlock(
                code="researched_ratio_not_measurable",
                measured=None,
                required=spec.minimum_researched_ratio,
                detail=(
                    f"this list's funnel shortlisted nobody and reports {coverage!r}, so "
                    "candidate_count / shortlist_count is not a number and no floor -- including "
                    "a floor of zero -- has been met by it. 'Every shortlisted name failed to "
                    "research' and 'there was nothing to research' are different findings, and a "
                    "rate of zero would be this gate collapsing them"
                ),
            )
        )
    elif measurement.researched_ratio < spec.minimum_researched_ratio:
        blocks.append(
            ShortlistGateBlock(
                code="researched_ratio_below_floor",
                measured=measurement.researched_ratio,
                required=spec.minimum_researched_ratio,
                detail=(
                    f"{measurement.candidate_count} of the {measurement.shortlist_count} "
                    "shortlisted names came back from the evidence plane with a signal "
                    f"({measurement.researched_ratio:.4f}), and this gate was declared with a "
                    f"floor of {spec.minimum_researched_ratio:.4f}. The rest are named on "
                    "CandidateRanking.unresearched, which is a fact about which runs finished "
                    "rather than about the market"
                ),
            )
        )

    if measurement.ranking_age_days > spec.maximum_ranking_age_days:
        blocks.append(
            ShortlistGateBlock(
                code="ranking_is_stale",
                measured=float(measurement.ranking_age_days),
                required=float(spec.maximum_ranking_age_days),
                detail=(
                    f"this list was assembled {measurement.ranking_age_days} calendar days after "
                    "the session it is about, and this gate was declared with a ceiling of "
                    f"{spec.maximum_ranking_age_days}. Every constituent signal, the funnel and "
                    "any exposure cross section share that one as_of by construction, so this is "
                    "the age of the freshest input and not of the oldest"
                ),
            )
        )

    return tuple(blocks)
