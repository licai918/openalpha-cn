"""The candidate ranking contract (`V2-P4-005`): where the panel plane and the evidence plane
meet for one `as_of`, and the one artifact D16 forbids to create an order.

D16, in full: *"通过专用产品合同排序候选。`CandidateRanking` 由股票池、as-of 时间、周期、评分政策、
构成 `SignalFrame`、预测、因子暴露、可交易性、风险标记与 manifest 定义。它绝不直接创建组合订单。"*
Ten constituents and one prohibition, and this module is all eleven. Each of the ten has exactly
one source, and naming the source is most of the design:
- **股票池** -- `CandidateRankingManifest.universe_digest` and `universe_count`:
  `domain/factor.py`'s `set_digest` over the registry's listed set, cross-checked against
  `CrossSectionFunnel.scores.universe_count`.
- **as-of** -- `CandidateRankingManifest.as_of`, required to equal `CrossSectionFunnel.as_of` and
  every constituent signal's own.
- **周期** -- `CandidateRankingManifest.horizon`, under `COUNTABLE_HORIZON_PATTERN`. **New here**,
  because a cross section has no horizon at all.
- **评分政策** -- `CandidateRankingManifest.scoring_policy`, which is `V2-P4-004`'s own
  `ShortlistSpec`, embedded rather than digested.
- **构成 `SignalFrame`** -- `RankedCandidate.signal`, **one per candidate**, from
  `domain/signal.py`. See below.
- **预测** -- `RankedCandidate.prediction`, `None` on every candidate this build can produce; see
  `no_model_prediction_exists_in_this_build`.
- **因子暴露** -- two different things and both are carried: `RankedCandidate.components` (the
  score's decomposition, `V2-P4-004`'s) and `RankedCandidate.exposure` (the risk characteristic,
  `domain/factor_neutralization.py`'s `SecurityCharacteristic`). See below.
- **可交易性** -- `RankedCandidate.fill`, which is `AShareExecutionPolicy`'s own `ExecutionResult`
  carried by `ShortlistEntry`, plus `CrossSectionFunnel.tradeability`.
- **风险标记** -- `RankedCandidate.risk_flags`, this contract's **closed** set, beside
  `signal.risk_flags`, the evidence plane's **open** one.
- **manifest** -- `CandidateRankingManifest` for the ranking, and `RankedCandidate
  .run_manifest_id` for each candidate's own run: `V2-P4-025`'s content address, inherited rather
  than copied.

## This contract joins two planes and does not rank

`V2-P4-004` cut a market to a shortlist on the **panel** plane: a composite score, a hard
tradeability filter, a top-`N` cut, and no `run_cycle`. Each of those `N` names then goes through
the **evidence** plane one at a time and comes back with a `SignalFrame`, a `DecisionLedger` and a
`RunManifest`. D3 is that these are two paths; this contract is the first place their two answers
about one security at one `as_of` sit in one record.

**So the ranks here are `CrossSectionScreen.select`'s and are never recomputed.** `RankedCandidate
.rank` is the funnel's own, `score` is the funnel's own, and `__post_init__` requires both to
match the `ShortlistEntry` they came from. A ranking that re-sorted by `confidence`, or by
`strength * confidence`, or by anything else the evidence plane returned, would be a *third*
ordering wearing the funnel's name -- and every measured caveat on the funnel's order (the clip
block, the alphabetical tie-break) would silently stop applying to it. D17 draws the same line
from the other side: 排序回答什么值得复核 -- ranking answers what is worth reviewing, and
`V2-P4-006` is where a governed screen re-orders one.

What this contract adds to the funnel's ordering is therefore not an order. It is: which of the
shortlisted names actually got researched, what the evidence plane concluded about each, what each
one *is* as opposed to what it scored, and six named ways a rank can be less than it looks.

## "构成 `SignalFrame`": one per candidate, and not one per list

A `SignalFrame` carries `subject: str = Field(min_length=1, max_length=128)` and `signal_id` hashes
it, so a signal is *about* one security by construction. `ResearchRunRequest` carries one
`subject` too, and `run_cycle` is the per-subject path -- which is the whole reason `V2-P4-004`
exists, since putting a 5,000-name cross section through it is 5,000 runs. A list-level
`SignalFrame` would need a subject that is not a security, and there is none.

So `RankedCandidate.signal` is required and `CandidateRanking` holds no signal of its own. The
list-level statements D16 asks for -- the universe, the as-of, the horizon, the policy -- are the
manifest's fields, which is what a manifest is for.

**`SignalFrame.horizon` is why the 周期 column can be a required field rather than a hope.**
`V2-P4-001` narrowed it from `HORIZON_PATTERN`'s four units to `COUNTABLE_HORIZON_PATTERN`'s one
(`^[1-9][0-9]{0,2}d$`), on the argument that PRD D36's "可比较" needs one measure both horizons are
in and that three of the four units have none. This contract is the first consumer of that
narrowing: it declares one horizon and requires every constituent signal to carry **the same**
one, by name, refusing the mismatch with the subject in the message. Equality rather than
`ResearchHorizon` ordering, because a list holding a 5-session and a 10-session conclusion is a
list ordered on two different questions -- `ScoreCoverage.incomplete_components`' argument, one
plane up and about time rather than about factors.

That rule is not vacuous on this contract and it *is* vacuous on one path, which is worth stating
rather than leaving to be discovered: `ResearchEngine._aggregate` writes `horizon="5d"` as a
literal, whatever its agents declared -- and they do not agree, `MarketAgent` declaring `5d` and
`ThemeAgent` `10d`. So every signal `run_cycle` produces today carries `5d` and a ranking built
from that path can never trip the rule. A caller assembling candidates from stored frames can,
and `tests/unit/backtest/test_candidate_ranking.py::
test_two_horizons_in_one_ranking_are_refused_by_name` drives exactly that.

## "因子暴露": two different things, and this contract carries both because they are two

The roadmap row asks for 因子暴露 and `ShortlistEntry` already carries `ComponentScore` per
declared factor -- `value`, `oriented`, `weight`, `contribution`. It is tempting to call that the
exposure and stop. It is not the exposure, and the distinction is measured rather than argued:

- **`ComponentScore.contribution` decomposes the score.** It answers "how much of this name's
  composite came from this factor", and its scale is the screen's declared weights. Change a
  weight and every contribution moves while the security is unchanged.
- **`CandidateExposure` says what the security *is*.** It is `SecurityCharacteristic` --
  `(industry_code, market_cap, is_backfilled)` at the declared `IndustryLevel` and
  `MarketCapMeasure` -- which is precisely one row of the design matrix `V2-P3-004`'s
  neutralisation regresses against. Change every weight in the screen and it does not move.

**There is no third option, and that is the finding.** "Exposure" in its textbook sense is a
fitted loading, and this repository fits none: `FactorNeutralizationStatistics` stores
`market_cap_slope` -- **one** coefficient for the whole cross section -- plus `residual_dispersion`
and the group sizes, and `NeutralizedFactorObservation` stores the residual and the
`industry_code`. Nothing anywhere stores a per-security beta, and getting one would need a
time-series regression of returns on factor returns over a window this contract reads no panel
for. So the honest exposure available at a live `as_of` is the characteristic, and
`factor_exposure_here_is_a_characteristic_and_not_a_fitted_loading` says so with the numbers.

Carrying both is what makes `a_neutralised_tier_orders_the_clip_block_by_industry_and_size`
readable on a single candidate. That limitation measured 41 names sharing **one** processed value
and carrying **41 distinct** neutralised residuals ordered entirely by industry mean and log size,
seven of them in the neutralised top ten. On this contract such a candidate arrives with
`score_is_a_winsorization_bound` set, one `ComponentScore` whose `value` is the bound, and an
`exposure` naming the industry and the capitalisation that produced its rank -- three fields that
together say "this rank is an industry-and-size ordering", where any one of them alone says
nothing. `tests/unit/backtest/test_candidate_ranking.py::
test_the_score_decomposition_and_the_risk_characteristic_separate_in_both_directions` is the
separating pair.

**A neutralised screen with no exposure cross section is refused.** On that tier the industry mean
and the size slope have already been subtracted out of every score, so a ranking that cannot say
what was removed is a ranking whose numbers are unexplainable by construction. On `raw` and
`processed` the cross section is optional and its absence is a per-candidate flag rather than a
refusal, because nothing was projected out of those values.

## "风险标记": a closed set here, beside the open one the evidence plane already has

`SignalFrame.risk_flags` is `tuple[str, ...]` with no vocabulary. `agents/baseline.py::
_quality_flags` copies whatever strings an evidence payload's `quality_flags` holds, and
`DeliberationCommittee` adds `committee-disagreement` of its own. Two gates then read subsets of
it, and **the two subsets are disjoint**: `decisions/risk.py::RiskGate` blocks on `future_data` and
`look_ahead_violation` and reduces on `redistribution_unknown`, `source_uri_missing` and
`revised_after_initial_availability`; `agents/committee.py` treats `regulatory`, `data-quality` and
`suspension` as severe. Neither reads the other's, and neither reads the committee's own
`committee-disagreement` -- so a signal the committee flagged as disagreed-upon reaches the
runtime risk gate and passes it. `the_signals_own_risk_flags_are_an_open_set_and_two_gates_read
_disjoint_subsets_of_it` carries that measurement, and
`tests/unit/backtest/test_candidate_ranking.py::
test_the_two_shipped_gates_read_disjoint_subsets_of_an_open_flag_set` drives it off the
real classes rather than restating their contents here.

`RankingRiskFlag` is therefore this contract's own closed set, derived from quantities this
contract holds, and it does **not** fold the signal's flags in. The signal travels whole, so a
reader gets the evidence plane's own words unaltered and this plane's own vocabulary beside them,
and neither is a lossy summary of the other.

**There is no `capacity` flag, and its absence is the finding rather than the omission.** S47 asks
for liquidity, tradability *and capacity* warnings. Tradeability is `ExecutionResult`, carried.
Capacity needs a declared `participation_cap` and a session turnover in yuan, which
`factor_tradeability` takes and this contract does not -- `KNOWN_CROSS_SECTION_LIMITATIONS
.no_capacity_constraint_is_applied_to_the_shortlist` already says so one plane down. A flag every
candidate carried would be the mirror of `TradeabilityVerdict`'s removed `not_in_registry`: that
one was a branch no input could take, this would be a branch no input could fail, and neither is
evidence of anything. So capacity is a limitation code here and nothing else.

## Identity: two addresses, split the way `V2-P3-014` split them, and no third

The question this contract had to answer is whether it gets a content-addressed identity at all,
given that `V2-P3-001` through `V2-P3-004` measured, repeatedly, that **any** new field on a
hashed model moves every identity derived from it -- all 21 shipped `factor_id`s, every time.
That lesson applies to this contract's own fields, so the answer is not "yes, obviously".

It is *split*, and the split is the one `RunManifest.run_manifest_id` cites:

- **`CandidateRankingManifest.ranking_manifest_id` addresses the declaration.** Universe, as-of,
  horizon, scoring policy, code commit, config digest -- the things a caller *asked for*. Two runs
  of one declaration share it, which is what makes it usable to recognise "the same screen,
  re-asked". `built_at` is excluded by name, and `RANKING_MANIFEST_UNADDRESSED_FIELDS` is the
  mapping `test_every_ranking_manifest_field_is_addressed_or_excluded_by_name` partitions
  `model_fields` against -- so field *n+1* is red until it is either measured to move the address
  or given a reason here. That audit shape is `V2-P3-002`'s, `V2-P3-014`'s, `V2-P3-015`'s and
  `V2-P4-025`'s, reused unchanged.
- **`ranking_content_digest` addresses the answer.** `(subject, rank, score, signal_id,
  run_manifest_id)` per candidate, which is exactly what S49 ("compare current candidate list with
  prior runs") has to diff. `V2-P4-007` is the issue that does the diffing; this is the address it
  compares.

**And nothing else gets one.** `CandidateRanking` itself is a frozen dataclass holding both, and a
third address over "manifest plus funnel plus candidates" would be a fourth statement of two facts
-- the shape `FactorNeutralizationManifest` refuses `industry_taxonomy` for. `stable_model_id` is
the hasher for the manifest because it is a pydantic model and that function has fourteen call
sites and no competitor; `ranking_content_digest` follows `set_digest`'s and
`characteristic_digest`'s form because it addresses a *sequence of rows* rather than a model, which
is the one case this repository has always spelled as a `*_digest` free function on the same
canonicalisation.

## "绝不直接创建订单": which layer, and what that buys

This is a `backtest/` leaf -- the eleventh -- and the placement is the enforcement.

- **`domain/` cannot hold it.** `domain-purity` forbids `openalpha_cn.backtest`, so a contract
  there could not carry a `ShortlistEntry`, a `ComponentScore`, a `TradeabilityVerdict` or a
  `CrossSectionFunnel`: seven of D16's ten constituents are `V2-P4-004`'s types, and the eighth
  (`ExecutionResult`) is only in `domain/execution.py` because `V2-P0B-012` moved it there for
  `storage`.
- **`product/` would hold it and enforce nothing.** Nothing forbids `product` to import
  `openalpha_cn.storage`, `openalpha_cn.runtime` or `domain.portfolio`; `product/research.py`
  already imports `runtime.contracts`. D16's prohibition there would be a sentence.
- **`backtest/` forbids the store and the composition root already.**
  `backtest-studies-touch-no-store` forbids `openalpha_cn.storage`, `openalpha_cn.agents` and
  `openalpha_cn.decisions`; `backtest-studies-reach-no-composition-root` forbids
  `openalpha_cn.runtime`, where `run_cycle` lives. `tests/unit/test_import_layering.py::
  test_the_two_backtest_study_contracts_cover_every_module_in_the_package` is what made this module
  join both source lists on arrival.

**Those three are what `V2-P4-004` had, and they are not enough for D16, which is why there is a
fourth.** They stop this contract *persisting* an order and *reaching the engine that would place
one*; they do not stop it constructing a `PortfolioOrder`, because `domain/portfolio.py` is a plain
data module every `backtest/` study may import and `backtest/portfolio.py`'s `PortfolioSimulator`
sits in the same package. `V2-P4-004` said "it creates no order and cannot, because `backtest/`
reaches no store", and that sentence is true about *storing* and not about *creating*.

So `ranking-creates-no-portfolio-order` is an eighth `lint-imports` contract, scoped to this module
alone, forbidding `openalpha_cn.domain.portfolio`, `openalpha_cn.backtest.portfolio` and
`openalpha_cn.backtest.multi_day` -- the three modules where a **portfolio** order, a
`PortfolioOrder`, is declared or simulated. It is added rather than folded into the two study
contracts because folding would forbid `domain.portfolio` to `backtest/portfolio.py`, which *is*
the simulator, and a contract that has to be relaxed to be added is not a contract.
`tests/unit/backtest/test_candidate_ranking.py::
test_the_ranking_contract_cannot_reach_the_three_modules_that_make_an_order` drives a probe
through it in both directions.

**Those three are not every order intent in the repository, and `V2-P4-035` corrected this
paragraph for saying so.** `backtest/execution.py` declares `ExecutionRequest` -- "a simplified
cash-equity order intent" -- and simulates a fill, and this module *reaches* it, through
`cross_section` and on purpose: that is `V2-P4-004`'s hard tradeability filter, so the edge is a
feature and the module cannot be forbidden without removing one. What D16's
`绝不直接创建组合订单` buys here is therefore precise rather than total: no `PortfolioOrder` can
be constructed here, no simulator or multi-day runner reached, nothing persisted and no engine
touched. A single-security `ExecutionRequest` is reachable and is not barred by any contract --
only by `tests/unit/backtest/test_candidate_ranking.py::
test_this_ranking_grows_no_import_of_its_own_into_the_order_machinery`, which pins this module's
own import list and so catches a direct import rather than a transitive reach.

The three imports this leaves are the ones D16 permits and D17 requires: `ExecutionResult` is a
verdict about a *hypothetical* buy, which is what `V2-P3-006` has produced since it was written;
`SignalFrame` is a conclusion; and `RunManifest`'s identity arrives here as a 28-character string.

## Layering, restated

Standard library plus `backtest/cross_section.py`, `backtest/factor_ic.py` and `domain/` -- one new
edge inside `backtest/` and no new external dependency. It stores nothing, reads no partition,
computes no return, fits no model and creates no order. **Runtime dependencies remain nine.**
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from openalpha_cn.backtest.cross_section import (
    ComponentScore,
    CrossSectionFunnel,
    ShortlistEntry,
    ShortlistSpec,
)
from openalpha_cn.backtest.factor_ic import FactorTier
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.execution import ExecutionResult
from openalpha_cn.domain.factor import set_digest
from openalpha_cn.domain.factor_neutralization import (
    IndustryLevel,
    IndustryMarketCapCrossSection,
    MarketCapMeasure,
)
from openalpha_cn.domain.horizon import COUNTABLE_HORIZON_PATTERN, is_countable_horizon
from openalpha_cn.domain.run import RUN_MANIFEST_ID_PATTERN
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.time import ensure_aware

__all__ = [
    "KNOWN_RANKING_LIMITATIONS",
    "RANKING_LIMITATION_CODES",
    "RANKING_MANIFEST_UNADDRESSED_FIELDS",
    "RANKING_RISK_FLAG_CODES",
    "RANKING_RISK_FLAG_ORDER",
    "UNIVERSE_DIGEST_PATTERN",
    "CandidateExposure",
    "CandidatePrediction",
    "CandidateRanking",
    "CandidateRankingError",
    "CandidateRankingManifest",
    "RankedCandidate",
    "RankingLimitation",
    "RankingRiskFlag",
    "build_ranking_manifest",
    "rank_candidates",
    "ranking_content_digest",
]


class CandidateRankingError(ValueError):
    """Raised for a malformed ranking -- never for a fact about the market or about a run.

    `TwoStageFunnelError`'s reason and its base class. A funnel that shortlisted nobody, and a
    shortlisted name whose research run never produced a signal, are **not** this: the first
    arrives as `CrossSectionFunnel.coverage` and the second as
    `CandidateRanking.unresearched`, because a caller looping over a year of `as_of`s has to be
    able to keep going past both.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class RankingLimitation:
    """One named boundary on what a candidate ranking can be trusted to mean."""

    code: str
    detail: str


KNOWN_RANKING_LIMITATIONS: Final[tuple[RankingLimitation, ...]] = (
    RankingLimitation(
        code="the_ranking_does_not_re_rank_and_inherits_every_caveat_on_the_funnels_order",
        detail=(
            "RankedCandidate.rank and .score are CrossSectionScreen.select's own, and "
            "__post_init__ requires both to equal the ShortlistEntry they came from. So every "
            "measured caveat on that order applies here unchanged and none of them is repaired: "
            "KNOWN_CROSS_SECTION_LIMITATIONS.the_shortlist_is_not_a_ranking_of_expected_return "
            "(the weights are fitted to nothing), .the_cut_is_broken_by_subject_code_when_two "
            "_scores_tie (a boundary decided alphabetically) and .a_neutralised_tier_orders_the "
            "_clip_block_by_industry_and_size (a top rank that is an industry-and-size ordering "
            "wearing a factor's name). A confidence, a strength or a prediction arriving from the "
            "evidence plane moves none of them, because this contract does not re-sort. V2-P4-006 "
            "is where a governed screen re-orders one, and it will own its own ordering's caveats."
        ),
    ),
    RankingLimitation(
        code="no_model_prediction_exists_in_this_build",
        detail=(
            "D16 names 预测 as a constituent and V2-P4-011 through V2-P4-017 are where one comes "
            "from: an AlphaModel contract, a versioned feature matrix, a walk-forward split and a "
            "prediction batch, none of which has landed. models/base.py is an LLM-JSON shape that "
            "the roadmap records as unable to express a panel fit/predict at all. So every "
            "RankedCandidate this build can produce carries prediction=None, and "
            "rank_candidates takes the mapping with no default so that the absence is stated by a "
            "caller rather than defaulted into. The field is here rather than added later because "
            "the all-or-nothing rule below has to exist before there is anything to apply it to: "
            "a ranking in which some candidates carry a model prediction and others do not is a "
            "list ordered on two different statistics, which is ScoreCoverage.incomplete "
            "_components' argument one plane down, and a contract that grew the field afterwards "
            "would have had to grow the rule afterwards too."
        ),
    ),
    RankingLimitation(
        code="factor_exposure_here_is_a_characteristic_and_not_a_fitted_loading",
        detail=(
            "CandidateExposure is SecurityCharacteristic -- an industry code at a declared level "
            "and a market capitalisation under a declared measure -- which is one row of the "
            "design matrix V2-P3-004's neutralisation regresses against, and not a fitted beta. "
            "This repository fits no per-security loading anywhere: "
            "FactorNeutralizationStatistics stores market_cap_slope, ONE coefficient for a whole "
            "cross section, and NeutralizedFactorObservation stores a residual and an "
            "industry_code. A real loading would need a time-series regression of returns on "
            "factor returns, over a window this contract reads no panel for and at a live as_of "
            "where the forward half does not exist. ComponentScore.contribution is the other "
            "thing entirely and is carried beside it: it decomposes the SCORE, its scale is the "
            "screen's declared weights, and changing a weight moves every contribution while the "
            "security is unchanged -- while changing every weight moves no exposure at all."
        ),
    ),
    RankingLimitation(
        code="the_signals_own_risk_flags_are_an_open_set_and_two_gates_read_disjoint_subsets",
        detail=(
            "SignalFrame.risk_flags is tuple[str, ...] with no vocabulary: agents/baseline.py's "
            "_quality_flags copies whatever strings an evidence payload's quality_flags holds. "
            "Two gates in this build read closed subsets of it and the two subsets are disjoint. "
            "decisions/risk.py's RiskGate blocks on {future_data, look_ahead_violation} and "
            "reduces on {redistribution_unknown, source_uri_missing, "
            "revised_after_initial_availability}; agents/committee.py treats {regulatory, "
            "data-quality, suspension} as severe. Their intersection is empty, and "
            "committee-disagreement -- a flag the committee raises itself -- is in neither, so a "
            "signal the committee marked as disagreed-upon reaches RiskGate.evaluate and returns "
            "pass. RankingRiskFlag is therefore this contract's own closed set over quantities "
            "this contract holds, and it does not fold the signal's flags in: the SignalFrame "
            "travels whole so the evidence plane's own words are unaltered, and neither "
            "vocabulary is a lossy summary of the other. What is NOT claimed is that either gate "
            "is wrong -- only that a reader who took risk_flags for a checked vocabulary would be "
            "reading an open set."
        ),
    ),
    RankingLimitation(
        code="no_capacity_warning_is_derivable_here_so_none_is_flagged",
        detail=(
            "S47 asks for liquidity, tradability AND capacity warnings. Tradeability is carried "
            "as AShareExecutionPolicy's own ExecutionResult per candidate plus the funnel's "
            "TradeabilityCensus. Capacity is not, and cannot be: it needs a declared "
            "participation_cap and a session turnover in yuan, which factor_tradeability takes "
            "and this contract does not, and KNOWN_CROSS_SECTION_LIMITATIONS.no_capacity "
            "_constraint_is_applied_to_the_shortlist already records the gap one plane down. So "
            "there is no capacity member of RankingRiskFlag rather than one every candidate "
            "carries. A flag on every candidate is the mirror of the not_in_registry verdict "
            "TradeabilityVerdict removed after measuring that no input could reach it: that was a "
            "branch no input could take and this would be a branch no input could fail, and "
            "neither is evidence of anything."
        ),
    ),
    RankingLimitation(
        code="the_universe_is_addressed_by_digest_and_the_funnel_can_only_check_its_size",
        detail=(
            "CrossSectionFunnel carries scores.universe_count and not the universe, because a "
            "funnel over 5,000 names that also carried the 5,000 names would double every one of "
            "them. So build_ranking_manifest computes set_digest over the registry's listed set "
            "and rank_candidates cross-checks universe_count against the funnel's -- which binds "
            "the digest to the right POPULATION SIZE and not to the right names. Two universes of "
            "equal size and different membership produce two different digests and one identical "
            "cross-check, so a caller that digested one universe and screened another gets a "
            "ranking whose address is honest about the mismatch and whose arithmetic cannot see "
            "it. Closing that would mean carrying the universe onto the funnel, which is "
            "V2-P4-004's contract to change and not this one's."
        ),
    ),
    RankingLimitation(
        code="this_contract_creates_no_order_because_of_where_it_lives_and_not_because_it_says_so",
        detail=(
            "D16's 绝不直接创建组合订单 is enforced by four lint-imports contracts rather than by "
            "this sentence. backtest-studies-touch-no-store forbids openalpha_cn.storage, "
            "openalpha_cn.agents and openalpha_cn.decisions; "
            "backtest-studies-reach-no-composition-root forbids openalpha_cn.runtime, where "
            "run_cycle lives; and ranking-creates-no-portfolio-order, added for this module and "
            "scoped to it, forbids openalpha_cn.domain.portfolio, openalpha_cn.backtest.portfolio "
            "and openalpha_cn.backtest.multi_day -- the three modules where a PortfolioOrder is "
            "declared or simulated. The first two alone were what V2-P4-004 had, and they stop "
            "this contract PERSISTING an order and reaching the engine that would place one; "
            "they do not stop it constructing one, because domain/portfolio.py is a plain data "
            "module every backtest study may import. What is NOT claimed is that no caller can "
            "build an order FROM a ranking: a caller holding this record and PortfolioSimulator "
            "can do exactly that, which is V2-P4-006 and the construction issues, and D17 says "
            "that is a separate step on purpose. Nor -- V2-P4-035 -- is it claimed that no order "
            "intent of ANY kind is reachable: openalpha_cn.backtest.execution declares "
            "ExecutionRequest, 'a simplified cash-equity order intent', and simulates a fill in "
            "AShareExecutionPolicy.execute, and this module reaches it through "
            "backtest/cross_section.py, which imports that policy for V2-P4-004's tradeability "
            "filter. That module is deliberately NOT forbidden, because forbidding it would "
            "delete the filter. So the ban is the portfolio-order ban exactly, and the "
            "single-security order intent one step below it is guarded only by a file-scoped pin "
            "on this module's own import list, which catches a direct import and not a "
            "transitive reach."
        ),
    ),
)
"""What a candidate ranking does not answer, as a closed registry rather than as prose.

The twenty-second `KNOWN_*` registry. Every entry is bound to the suite by
`tests/unit/test_known_limitation_registries.py`, which requires each `code` to appear as a
string literal in *executable* test code -- the P2 review measured that a code named only in
docstrings can be renamed with the whole suite staying green.
"""

RANKING_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_RANKING_LIMITATIONS
)


UNIVERSE_DIGEST_PATTERN: Final[str] = r"^set_[0-9a-f]{24}$"
"""Exactly what `domain/factor.py::set_digest` produces, and nothing else.

`RUN_MANIFEST_ID_PATTERN`'s rule applied to the other addressed input: a content address that is
only conventionally a content address stops being one the first time it is convenient. Attached to
`CandidateRankingManifest.universe_digest` so a caller cannot hand the manifest a placeholder and
get a ranking whose stock pool is a string somebody typed. `build_ranking_manifest` is the only
builder in `src/` and it computes the digest rather than accepting one.
"""


_RUN_MANIFEST_ID: Final[re.Pattern[str]] = re.compile(RUN_MANIFEST_ID_PATTERN)
"""`RUN_MANIFEST_ID_PATTERN` compiled once, read by `RankedCandidate.__post_init__`.

The pattern is imported from `domain/run.py` rather than restated, `DecisionLedger
.run_manifest_id`'s arrangement: two spellings of "what `stable_model_id(prefix='run', ...)`
produces" are two things that can disagree about it.
"""


RankingRiskFlag = Literal[
    "score_is_a_winsorization_bound",
    "rank_shares_its_score",
    "exposure_is_not_measured",
    "industry_exposure_is_backfilled",
    "evidence_plane_abstained",
    "evidence_plane_is_bearish",
]
"""Six ways one candidate's rank is less than it looks, as a closed set this contract derives.

Closed and this contract's own, for the reason `the_signals_own_risk_flags_are_an_open_set_and_two
_gates_read_disjoint_subsets` measures: the evidence plane's own `risk_flags` is an open string set
that two shipped gates read two disjoint closed subsets of. Folding the two vocabularies together
would produce a third open set nobody reads all of. The `SignalFrame` travels whole instead.

Every member is derived from a quantity this record holds, and every member separates -- there is
deliberately no flag that every candidate would carry (see
`no_capacity_warning_is_derivable_here_so_none_is_flagged`).

- **`score_is_a_winsorization_bound`** -- at least one of this candidate's `ComponentScore`s is
  `clipped`, so that term is the transform's upper bound rather than the security's own number.
  `ShortlistEntry.clipped_component_count`'s fact, promoted to a flag because a count of zero and
  a count of two are the same reading at a glance.
- **`rank_shares_its_score`** -- another *tradeable* name carries this candidate's exact composite,
  so the position it holds was decided by ascending `ts_code` and carries nothing. Two disjuncts,
  and the second is load-bearing: within the shortlist the tie is visible from the entries
  themselves, but the **last** candidate can be tied with names the cut left outside, and only
  `CrossSectionFunnel.tied_at_the_cut` knows about those. A rule written from the shortlist alone
  would report a clean cut for exactly the boundary the funnel exists to warn about.
- **`exposure_is_not_measured`** -- no `SecurityCharacteristic` for this security in the offered
  cross section, so what it *is* is unknown here. On the neutralised tier this cannot occur,
  because a missing exposure cross section is refused outright.
- **`industry_exposure_is_backfilled`** -- `SecurityCharacteristic.is_backfilled`, carried rather
  than recomputed. `KNOWN_INDUSTRY_LIMITATIONS` records that `index_member_all` expresses its
  entire history in a taxonomy that came into force 2021-12-13, so an answer for an earlier day is
  a label the classification did not have then.
- **`evidence_plane_abstained`** -- the run came back `direction="abstain"`. The panel plane
  ranked this name into the top `N` and the evidence plane declined to conclude about it, which is
  the single most important thing a reader of a ranking can be told and is invisible in the score.
- **`evidence_plane_is_bearish`** -- the run came back `direction="bearish"`. Its own flag rather
  than folded into the one above, because "no conclusion" and "the opposite conclusion" have
  different remedies -- and deliberately **not** named "disagrees with the screen": the composite
  claims nothing about return (`the_shortlist_is_not_a_ranking_of_expected_return`), so there is no
  agreement for it to be in.
"""

RANKING_RISK_FLAG_CODES: Final[frozenset[str]] = frozenset(get_args(RankingRiskFlag))

RANKING_RISK_FLAG_ORDER: Final[tuple[RankingRiskFlag, ...]] = get_args(RankingRiskFlag)
"""The order every `RankedCandidate.risk_flags` is reported in.

Declared rather than alphabetical, and asserted rather than produced by a `sorted()` call, so that
two candidates carrying the same two flags carry them in the same order and a caller comparing two
rankings is not reading a set iteration order as a change.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateExposure:
    """What one candidate *is*: its industry group and its size, at the declared readings.

    `SecurityCharacteristic` with the two declarations that give its fields meaning attached --
    an `industry_code` is meaningless without the `IndustryLevel` it was read at (31 groups, 134 or
    346), and a `market_cap` is meaningless without the `MarketCapMeasure` it came from
    (`total_mv` or `circ_mv`). Those two live on `IndustryMarketCapCrossSection` and would be lost
    the moment one characteristic is lifted off it, which is exactly what this record does.

    A plain carrier, `SecurityCharacteristic`'s own precedent: a nominal type is not a boundary,
    and the rules live once, in `build_industry_market_cap_cross_section` one plane down.
    `_exposure_of` is the only construction site in `src/`.
    """

    industry_code: str
    industry_level: IndustryLevel
    market_cap: float
    market_cap_measure: MarketCapMeasure
    is_backfilled: bool


class CandidatePrediction(BaseModel):
    """One model's forward number for one candidate, when there is a model.

    **Nothing in this build produces one**; see `no_model_prediction_exists_in_this_build`. The
    type exists so that `V2-P4-014`'s prediction batch has a declared place to arrive at and so
    that the all-or-nothing rule in `rank_candidates` exists before there is anything to apply it
    to -- a contract that grew the field later would have had to grow the rule later too.

    Three fields and no more, because every one of them is named by D16 or already exists: the
    artifact this number came from (by whatever identity `V2-P4-011` gives it), the number, and
    the horizon it is over. `horizon` carries `COUNTABLE_HORIZON_PATTERN` and `rank_candidates`
    requires it to equal the ranking's, for the reason a signal's must: a prediction over ten
    sessions sitting in a five-session ranking is a number about a different question.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    model_artifact_id: str = Field(min_length=1, max_length=128)
    predicted_value: float
    horizon: str = Field(pattern=COUNTABLE_HORIZON_PATTERN)


RANKING_MANIFEST_UNADDRESSED_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "built_at": (
            "the wall clock this ranking was assembled on. Re-running one declaration must "
            "reproduce its ranking_manifest_id or the address cannot be used to recognise the "
            "same screen re-asked, and the clock is the one field guaranteed to differ between "
            "two such runs. This repository has paid for the other arrangement three times: "
            "FactorBuildManifest keeps built_at out of its payload for exactly this reason, "
            "V2-P3-002's FactorInputRef had to have fetched_at moved out of batch_digest after a "
            "byte-identical re-fetch moved every manifest_id derived from it, and "
            "RUN_MANIFEST_UNADDRESSED_FIELDS names started_at first for the same sentence"
        )
    }
)
"""Every `CandidateRankingManifest` field that is **recorded but not addressed**, with why.

One entry, and the list is short because the manifest holds only declared inputs -- there is no
`status`, no `checkpoints` and no observed `environment` here, since a ranking has no lifecycle
and this leaf observes no host. A mapping rather than a set because the reason is the load-bearing
half: an exclusion with no stated reason is indistinguishable from an oversight.

`ranking_manifest_id` excludes exactly these keys, and
`tests/unit/backtest/test_candidate_ranking.py::
test_every_ranking_manifest_field_is_addressed_or_excluded_by_name` partitions
`CandidateRankingManifest.model_fields` against this mapping, so field *n+1* fails until it is
either measured to move the address or named here -- the audit shape `V2-P3-002`, `V2-P3-014`,
`V2-P3-015` and `V2-P4-025` each reused.
"""


class CandidateRankingManifest(BaseModel):
    """Everything a candidate ranking was declared from, as a content address.

    D16's tenth constituent, and the four of the other nine that are properties of the *list*
    rather than of a candidate: the stock pool, the as-of, the horizon and the scoring policy.

    `schema_version` without a `ContractVersions` registry, `backtest/factor_experiment.py`'s three
    models' precedent: nothing under `backtest/` can reach a store, so there is no stored payload
    for `read_versioned` to upgrade and a registry here would be a table with no reader. It is
    still a field, and it still enters the identity, because a v2 declaration shape is a different
    declaration.

    `scoring_policy` is `V2-P4-004`'s `ShortlistSpec` **embedded rather than digested**, and that is
    the same choice `FactorNeutralizationManifest` makes for its parameters and refuses for its
    taxonomy: a digest of a model this manifest can simply hold would be a second canonicalisation
    of one object, and the two can disagree. Embedding it also makes the identity automatically
    sensitive to every declared weight, every declared factor and the tier -- so
    `test_the_ranking_manifest_address_moves_for_every_declared_input` varies them without this
    class needing a field per input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["candidate-ranking-manifest/v1"] = "candidate-ranking-manifest/v1"
    as_of: datetime
    horizon: str = Field(pattern=COUNTABLE_HORIZON_PATTERN)
    """The one span every constituent conclusion in this ranking is over.

    `COUNTABLE_HORIZON_PATTERN` rather than `HORIZON_PATTERN`, which is `V2-P4-001`'s narrowing
    consumed rather than restated: `SignalFrame.horizon` accepts exactly this grammar, so a
    ranking whose declared horizon could not be a signal's would be unsatisfiable by construction.
    """
    universe_digest: str = Field(pattern=UNIVERSE_DIGEST_PATTERN)
    universe_count: int = Field(ge=1)
    scoring_policy: ShortlistSpec
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    built_at: datetime
    """Recorded and **not** addressed; see `RANKING_MANIFEST_UNADDRESSED_FIELDS`."""

    @field_validator("as_of", "built_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @property
    def tier(self) -> FactorTier:
        """The declared tier, read off the policy rather than restated as a field of its own."""
        return self.scoring_policy.tier

    @property
    def shortlist_size(self) -> int:
        return self.scoring_policy.shortlist_size

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def ranking_manifest_id(self) -> str:
        """The content address of this ranking's **declaration**.

        `stable_model_id` over every field except `RANKING_MANIFEST_UNADDRESSED_FIELDS`, so two
        rankings that declare the same universe, as-of, horizon and policy share an address
        however long apart they were assembled, and two that declare anything different do not.
        `ranking_content_digest` is the other half and addresses the answer; see this module's
        docstring for why the split is `V2-P3-014`'s rather than a new one.
        """
        return stable_model_id(
            prefix="rnk", model=self, exclude=frozenset(RANKING_MANIFEST_UNADDRESSED_FIELDS)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RankedCandidate:
    """One shortlisted security, with both planes' answers about it at one `as_of`.

    The panel plane's half is carried from `ShortlistEntry` unchanged -- `rank`, `score`,
    `components`, `fill` -- and `CandidateRanking.__post_init__` requires it to still equal the
    entry it came from. The evidence plane's half is the `SignalFrame` its run produced and that
    run's `run_manifest_id`.

    `run_manifest_id` rather than a whole `RunManifest`, and rather than copies of `config_digest`
    and `random_seed`: `V2-P4-025` made that string the content address of the run's entire
    declaration, so carrying it inherits *every* declared input at once, including ones added
    later. `DecisionLedger.run_manifest_id` carries it for the same reason and states it in the
    same words, and `RUN_MANIFEST_ID_PATTERN` is what stops a placeholder taking its place.
    """

    subject: str
    rank: int
    score: float
    components: tuple[ComponentScore, ...]
    fill: ExecutionResult
    signal: SignalFrame
    run_manifest_id: str
    exposure: CandidateExposure | None
    prediction: CandidatePrediction | None
    risk_flags: tuple[RankingRiskFlag, ...]

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise CandidateRankingError("a ranked candidate must name a subject")
        if self.rank < 1:
            raise CandidateRankingError(
                f"{self.subject} carries rank {self.rank}; ranks are 1-based and are the funnel's"
            )
        if not math.isfinite(self.score):
            raise CandidateRankingError(
                f"{self.subject}'s score is {self.score!r}, which is not a number"
            )
        if self.signal.subject != self.subject:
            raise CandidateRankingError(
                f"{self.subject} carries a signal about {self.signal.subject!r}; a candidate's "
                "conclusion is a conclusion about that candidate"
            )
        if self.fill.status != "filled":
            raise CandidateRankingError(
                f"{self.subject} is ranked carrying a {self.fill.status} execution; only a name "
                "the market would have sold reaches a shortlist, so only one reaches a ranking"
            )
        if _RUN_MANIFEST_ID.fullmatch(self.run_manifest_id) is None:
            raise CandidateRankingError(
                f"{self.subject} carries run_manifest_id {self.run_manifest_id!r}, which is not "
                "stable_model_id(prefix='run', ...)'s own output; a provenance pointer that is "
                "only conventionally a content address stops being one the first time it is "
                "convenient"
            )
        flags = tuple(self.risk_flags)
        if len(set(flags)) != len(flags):
            raise CandidateRankingError(f"{self.subject} carries {list(flags)}, which repeats")
        expected = tuple(code for code in RANKING_RISK_FLAG_ORDER if code in set(flags))
        if flags != expected:
            raise CandidateRankingError(
                f"{self.subject} carries risk flags {list(flags)}; they are reported in "
                f"{list(RANKING_RISK_FLAG_ORDER)}, so two candidates carrying one set carry it "
                "in one order and a caller diffing two rankings is not reading an iteration order "
                "as a change"
            )

    @property
    def direction(self) -> str:
        """The evidence plane's own verdict. S44's `direction`, not re-derived from the score."""
        return self.signal.direction

    @property
    def confidence(self) -> float:
        """S44's `confidence`. It orders nothing here; see this module's docstring."""
        return self.signal.confidence

    @property
    def horizon(self) -> str:
        """The constituent signal's own horizon, which the ranking requires to be its own."""
        return self.signal.horizon

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """S45's supporting references, from the `SignalFrame` rather than copied beside it."""
        return self.signal.evidence_ids

    @property
    def signal_risk_flags(self) -> tuple[str, ...]:
        """The evidence plane's **open** flag set, verbatim. `risk_flags` is this plane's closed
        one, and neither is a summary of the other."""
        return self.signal.risk_flags

    @property
    def clipped_component_count(self) -> int:
        """`ShortlistEntry.clipped_component_count`, over this candidate's carried terms."""
        return sum(1 for component in self.components if component.clipped)


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateRanking:
    """One `as_of`'s candidate list: the declaration, the funnel it cut, and both planes' answers.

    Built by `rank_candidates`; this constructor re-derives nothing and checks everything,
    `CrossSectionFunnel`'s precedent and `validate_neutralized_factor_observation`'s two-call-site
    reason -- a frozen dataclass with `slots=True` is still constructible directly, and the
    invariants that make a rank mean anything are exactly the ones a hand-built record would
    skip.

    `unresearched` is the shortlisted names that came back with no signal, ascending. Carried
    rather than dropped, `IndustryMarketCapCrossSection`'s three-collection argument: a ranking
    that returned only the researched names would leave a reader unable to tell a 100-name
    shortlist that produced 100 candidates from one that produced 60, and the second is a fact
    about which runs finished rather than about the market. `candidates` and `unresearched`
    partition `funnel.shortlist` exactly, so a lost name fails this constructor's own arithmetic.
    """

    manifest: CandidateRankingManifest
    funnel: CrossSectionFunnel
    candidates: tuple[RankedCandidate, ...]
    unresearched: tuple[str, ...]

    def __post_init__(self) -> None:
        _refuse_a_manifest_that_does_not_describe_this_funnel(self.manifest, self.funnel)

        shortlisted = tuple(entry.subject for entry in self.funnel.shortlist)
        entries = {entry.subject: entry for entry in self.funnel.shortlist}
        ranked = tuple(candidate.subject for candidate in self.candidates)
        if len(set(ranked)) != len(ranked):
            raise CandidateRankingError("one security is ranked twice in one ranking")
        if tuple(sorted(self.unresearched)) != self.unresearched:
            raise CandidateRankingError(
                f"unresearched is {list(self.unresearched)}; it is ascending by subject, because "
                "it is a set of names and an iteration order that leaked into it would make two "
                "identical rankings compare unequal"
            )
        accounted = sorted([*ranked, *self.unresearched])
        if accounted != sorted(shortlisted):
            raise CandidateRankingError(
                f"the ranking accounts for {len(accounted)} securities and the funnel shortlisted "
                f"{len(shortlisted)}; every shortlisted name is ranked or is named unresearched, "
                "and a ranking that does not add up has lost one of them"
            )
        if ranked != tuple(subject for subject in shortlisted if subject in set(ranked)):
            raise CandidateRankingError(
                "the candidates are not in the funnel's own order; this contract does not re-rank"
            )
        for candidate in self.candidates:
            entry = entries[candidate.subject]
            if (candidate.rank, candidate.score) != (entry.rank, entry.score):
                raise CandidateRankingError(
                    f"{candidate.subject} is ranked {candidate.rank} at {candidate.score!r} and "
                    f"the funnel shortlisted it {entry.rank} at {entry.score!r}; a ranking that "
                    "moved a rank or a score is a ranking of something else"
                )
            if candidate.components != entry.components:
                raise CandidateRankingError(
                    f"{candidate.subject} carries score terms the funnel did not produce"
                )
            if candidate.signal.horizon != self.manifest.horizon:
                raise CandidateRankingError(
                    f"{candidate.subject}'s signal is over {candidate.signal.horizon!r} and this "
                    f"ranking declares {self.manifest.horizon!r}; a list holding two horizons is "
                    "ordered on two different questions"
                )
            if ensure_aware(candidate.signal.as_of) != self.manifest.as_of:
                raise CandidateRankingError(
                    f"{candidate.subject}'s signal is as of {candidate.signal.as_of.isoformat()} "
                    f"and this ranking is as of {self.manifest.as_of.isoformat()}"
                )
        predicted = [candidate.prediction is not None for candidate in self.candidates]
        if any(predicted) and not all(predicted):
            raise CandidateRankingError(
                f"{sum(predicted)} of {len(predicted)} candidates carry a model prediction; a "
                "ranking in which some names have one and others do not is a list ordered on two "
                "different statistics, so predictions are all or nothing"
            )

    @property
    def coverage(self) -> str:
        """`CrossSectionFunnel.coverage`, read off the funnel rather than restated.

        A ranking over a funnel that shortlisted nobody is a ranking with no candidates and the
        funnel's own code saying why -- `no_scored_candidate`, `degenerate_scores`,
        `cut_inside_the_clip_block`, `no_tradeable_candidate` or `cut_exceeds_the_cross_section`.
        A code rather than a refusal, `FunnelCoverage`'s stated rule: a caller looping over a year
        of `as_of`s has to be able to keep going past all five.
        """
        return self.funnel.coverage

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def researched_rate(self) -> float | None:
        """`ranked / shortlisted`, or `None` when the funnel shortlisted nobody.

        `None` rather than `0.0` on a funnel with no shortlist, `TradeabilityCensus
        .tradeable_rate`'s reason: "every shortlisted name failed to research" and "there was
        nothing to research" are different findings and a rate of zero would collapse them.
        """
        shortlisted = self.funnel.shortlist_count
        return None if shortlisted == 0 else self.candidate_count / shortlisted

    @property
    def content_digest(self) -> str:
        """`ranking_content_digest` over this ranking's candidates: the address of the answer."""
        return ranking_content_digest(self.candidates)

    def candidate(self, subject: str) -> RankedCandidate | None:
        """This security's candidate, or `None` when it is not one.

        `None` rather than a refusal, `IndustryMarketCapCrossSection.get`'s reason: asking about a
        name that did not make the cut is the ordinary question this record exists to answer.
        """
        return next((item for item in self.candidates if item.subject == subject), None)

    def flagged(self, flag: RankingRiskFlag) -> tuple[RankedCandidate, ...]:
        """Every candidate carrying `flag`, in rank order. Refuses a flag this contract has no
        member for, rather than returning an empty tuple that reads like a clean ranking."""
        if flag not in RANKING_RISK_FLAG_CODES:
            raise CandidateRankingError(
                f"{flag!r} is not a declared ranking risk flag; this contract declares "
                f"{list(RANKING_RISK_FLAG_ORDER)}"
            )
        return tuple(item for item in self.candidates if flag in item.risk_flags)


def ranking_content_digest(candidates: Sequence[RankedCandidate]) -> str:
    """A content address for one ranking's **answers**: `(subject, rank, score, ids)` per candidate.

    S49 asks for "compare current candidate list with prior runs", and `V2-P4-007` is the issue
    that renders the difference. This is the address it compares: two rankings whose candidates
    agree on every subject, rank, composite, conclusion and run declaration share it, and two that
    differ anywhere in those five do not.

    **`signal_id` and `run_manifest_id` rather than the signal and the manifest**, because both are
    already content addresses of exactly those objects -- `signal_id` over the whole `SignalFrame`
    and `run_manifest_id` over the run's whole declaration -- so hashing the objects would be a
    second canonicalisation of two things this repository already addresses once each.

    **`fill`, `exposure`, `prediction` and `risk_flags` are deliberately not in it**, and that is a
    claim rather than an economy: every one of them is a function of the five that are. The fill is
    the funnel's verdict on the name it ranked, the exposure is a characteristic of that same
    security at the same `as_of`, the prediction is `None` in this build, and the flags are derived
    below from the score terms, the funnel and the signal. A digest over derived quantities moves
    when a derivation changes and reports it as a changed candidate list, which is the opposite of
    what a caller diffing two runs is asking.

    Sorted by subject and not by rank, `set_digest`'s reason: the ranks are in the payload, so
    ordering the payload by them too would put one fact in twice, and a stable key that does not
    move when a rank does is what makes the sort itself unable to fail.

    The same canonicalisation `stable_model_id`, `set_digest` and `characteristic_digest` use.
    Returns the digest of the empty list for a ranking with no candidates, which is a real answer:
    two `as_of`s that both produced nothing produced the same nothing.
    """
    subjects = [candidate.subject for candidate in candidates]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise CandidateRankingError(
            f"{duplicates} appears more than once in this ranking; a duplicated security is two "
            "answers to one question, and a digest that hashed both would give two different "
            "rankings one address"
        )
    payload = sorted(
        [
            candidate.subject,
            candidate.rank,
            candidate.score,
            candidate.signal.signal_id,
            candidate.run_manifest_id,
        ]
        for candidate in candidates
    )
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    return f"rkc_{sha256(canonical).hexdigest()[:24]}"


def build_ranking_manifest(
    *,
    as_of: datetime,
    horizon: str,
    universe: Sequence[str],
    scoring_policy: ShortlistSpec,
    code_commit: str,
    config_digest: str,
    built_at: datetime,
) -> CandidateRankingManifest:
    """Assemble the manifest, computing the universe's address rather than accepting one.

    The only builder in `src/`, `panel_neutralization.load_industry_market_cap_cross_section`'s
    arrangement for its reason: `universe_digest` carries `UNIVERSE_DIGEST_PATTERN` so a
    hand-written string cannot pass for a content address, and a second builder that accepted one
    would make that pattern decorative. `universe_count` is `len(set(universe))` and not
    `len(universe)`, matching `set_digest`'s own de-duplication -- a universe naming one security
    twice is one security, and a count that disagreed with the digest's population would be the
    one number `rank_candidates` cross-checks against the funnel.

    `horizon` is refused here rather than only by the field's `pattern`, so that a caller handing
    it `3m` -- a legal `HORIZON_PATTERN` value that `SignalFrame` has not accepted since
    `V2-P4-001` -- is told which of the two grammars it fell out of.
    """
    subjects = sorted(set(universe))
    if not subjects:
        raise CandidateRankingError(
            "a ranking needs a universe; an empty one has no content address and no screen "
            "could have produced the funnel it would describe"
        )
    if any(not subject.strip() for subject in subjects):
        raise CandidateRankingError("a universe cannot contain an unnamed security")
    if not is_countable_horizon(horizon):
        raise CandidateRankingError(
            f"{horizon!r} is not a horizon a SignalFrame can carry; V2-P4-001 narrowed "
            f"SignalFrame.horizon to {COUNTABLE_HORIZON_PATTERN} -- a count of trading sessions "
            "-- because a calendar span holds a variable number of them and a future one's count "
            "is not knowable at all, so a ranking declaring one could never be satisfied"
        )
    return CandidateRankingManifest(
        as_of=as_of,
        horizon=horizon,
        universe_digest=set_digest(subjects),
        universe_count=len(subjects),
        scoring_policy=scoring_policy,
        code_commit=code_commit,
        config_digest=config_digest,
        built_at=built_at,
    )


def rank_candidates(
    *,
    manifest: CandidateRankingManifest,
    funnel: CrossSectionFunnel,
    signals: Mapping[str, SignalFrame],
    run_manifest_ids: Mapping[str, str],
    exposures: IndustryMarketCapCrossSection | None,
    predictions: Mapping[str, CandidatePrediction],
) -> CandidateRanking:
    """Join one funnel's shortlist to the evidence plane's answers about it.

    Nothing here is defaulted, `ShortlistSpec`'s rule: `exposures=None` and `predictions={}` are
    the two declared-absent states and a caller states them, because the first decides two risk
    flags and the second decides whether this list is ordered on one statistic or two.

    - `signals` is one `SignalFrame` per researched candidate, keyed by subject. A shortlisted name
      with no entry is `unresearched` and is counted; a signal for a name the funnel did **not**
      shortlist is a malformed call, because a ranking is the shortlist's own answers and a name
      that reached the evidence plane without reaching the cut came from somewhere this record
      cannot describe.
    - `run_manifest_ids` is that run's `RunManifest.run_manifest_id`, one per researched candidate.
      Required rather than optional: a conclusion with no reproducible declaration behind it is
      exactly what roadmap section 9 measured `RunManifest` to have been missing.
    - `exposures` is the same `IndustryMarketCapCrossSection` a neutralisation consumes, or `None`.
      **`None` is refused on the `neutralized` tier**, where an industry mean and a size slope have
      already been subtracted out of every score: a ranking that cannot say what was removed is one
      whose numbers are unexplainable by construction.
    - `predictions` is empty in this build; see `no_model_prediction_exists_in_this_build`.
    """
    _refuse_a_manifest_that_does_not_describe_this_funnel(manifest, funnel)
    if exposures is not None and ensure_aware(exposures.as_of) != manifest.as_of:
        raise CandidateRankingError(
            f"the exposure cross section is as of {exposures.as_of.isoformat()} and this ranking "
            f"is as of {manifest.as_of.isoformat()}; a characteristic read on another day is a "
            "different security's answer to this one's question"
        )
    if exposures is None and manifest.tier == "neutralized":
        raise CandidateRankingError(
            "a neutralized-tier ranking needs the industry and market-cap cross section its "
            "scores were neutralised against; on that tier an industry mean and a size slope have "
            "already been subtracted out of every value, so a ranking that cannot say what was "
            "removed is one whose ordering has no readable explanation. Offer the same "
            "IndustryMarketCapCrossSection the neutralisation consumed, or screen on the raw or "
            "processed tier, where nothing was projected out"
        )

    entries = {entry.subject: entry for entry in funnel.shortlist}
    _refuse_answers_about_names_that_were_not_shortlisted(
        entries, signals, run_manifest_ids, predictions
    )

    researched = tuple(entry for entry in funnel.shortlist if entry.subject in signals)
    missing_manifests = sorted(
        entry.subject for entry in researched if entry.subject not in run_manifest_ids
    )
    if missing_manifests:
        raise CandidateRankingError(
            f"{missing_manifests} carry a signal and no run_manifest_id; a conclusion with no "
            "reproducible declaration behind it is what roadmap section 9 measured RunManifest to "
            "have been missing, and carrying the address rather than a copy of config_digest and "
            "random_seed is V2-P4-025's own arrangement"
        )
    unpredicted = sorted(entry.subject for entry in researched if entry.subject not in predictions)
    if predictions and unpredicted:
        raise CandidateRankingError(
            f"{unpredicted} carry no model prediction and {sorted(predictions)} do; a ranking in "
            "which some names have one and others do not is a list ordered on two different "
            "statistics, so predictions are all or nothing"
        )
    for subject, prediction in sorted(predictions.items()):
        if prediction.horizon != manifest.horizon:
            raise CandidateRankingError(
                f"{subject}'s prediction is over {prediction.horizon!r} and this ranking declares "
                f"{manifest.horizon!r}; a number about a different window is a number about a "
                "different question"
            )

    boundary = funnel.shortlist[-1].subject if funnel.shortlist else None
    boundary_tie = funnel.tied_at_the_cut > 1
    shared = _scores_shared_inside_the_shortlist(funnel.shortlist)
    candidates: list[RankedCandidate] = []
    for entry in researched:
        exposure = _exposure_of(exposures, entry.subject)
        candidates.append(
            RankedCandidate(
                subject=entry.subject,
                rank=entry.rank,
                score=entry.score,
                components=entry.components,
                fill=entry.fill,
                signal=signals[entry.subject],
                run_manifest_id=run_manifest_ids[entry.subject],
                exposure=exposure,
                prediction=predictions.get(entry.subject),
                risk_flags=_risk_flags_of(
                    entry,
                    signal=signals[entry.subject],
                    exposure=exposure,
                    shares_its_score=entry.score in shared
                    or (entry.subject == boundary and boundary_tie),
                ),
            )
        )
    return CandidateRanking(
        manifest=manifest,
        funnel=funnel,
        candidates=tuple(candidates),
        unresearched=tuple(
            sorted(entry.subject for entry in funnel.shortlist if entry.subject not in signals)
        ),
    )


def _refuse_a_manifest_that_does_not_describe_this_funnel(
    manifest: CandidateRankingManifest, funnel: CrossSectionFunnel
) -> None:
    """The four statements a manifest and a funnel make about the same screen, held equal.

    Declared once and called from both `rank_candidates` and `CandidateRanking.__post_init__`, the
    two-call-site shape `validate_neutralized_factor_observation` argues: the builder is one entry
    and a hand-built `CandidateRanking` is the other, and a rule stated only in the builder is one
    a directly constructed record skips.

    `universe_digest` is **not** among them and cannot be; see
    `the_universe_is_addressed_by_digest_and_the_funnel_can_only_check_its_size`.
    """
    if ensure_aware(manifest.as_of) != ensure_aware(funnel.as_of):
        raise CandidateRankingError(
            f"this ranking is as of {manifest.as_of.isoformat()} and its funnel is as of "
            f"{funnel.as_of.isoformat()}; a candidate list is one moment's answer"
        )
    if manifest.tier != funnel.tier:
        raise CandidateRankingError(
            f"this ranking declares the {manifest.tier!r} tier and its funnel reports "
            f"{funnel.tier!r}"
        )
    if manifest.shortlist_size != funnel.shortlist_size:
        raise CandidateRankingError(
            f"this ranking declares a shortlist of {manifest.shortlist_size} and its funnel cut "
            f"to {funnel.shortlist_size}"
        )
    if manifest.universe_count != funnel.scores.universe_count:
        raise CandidateRankingError(
            f"this ranking's universe holds {manifest.universe_count} securities and its funnel "
            f"scored over {funnel.scores.universe_count}; the digest addresses a population, so a "
            "count that disagrees means the two are not the same market"
        )


def _refuse_answers_about_names_that_were_not_shortlisted(
    entries: Mapping[str, ShortlistEntry],
    signals: Mapping[str, SignalFrame],
    run_manifest_ids: Mapping[str, str],
    predictions: Mapping[str, CandidatePrediction],
) -> None:
    """Every offered answer is about a shortlisted name, or the call is malformed.

    `_refuse_components_that_are_not_the_declared_ones`' rule one plane down: an answer about a
    security the funnel did not select is not a market fact, because a ranking is the shortlist's
    own answers and a name that reached the evidence plane without reaching the cut came from a
    path this record cannot describe.
    """
    for label, offered in (
        ("signals", signals),
        ("run_manifest_ids", run_manifest_ids),
        ("predictions", predictions),
    ):
        extra = sorted(set(offered) - set(entries))
        if extra:
            raise CandidateRankingError(
                f"{label} carries {extra}, which this funnel did not shortlist; a ranking is the "
                "shortlist's own answers and a name that reached the evidence plane without "
                "reaching the cut came from a path this record cannot describe"
            )


def _scores_shared_inside_the_shortlist(shortlist: Sequence[ShortlistEntry]) -> frozenset[float]:
    """Every composite that two or more shortlisted names carry.

    Half of `rank_shares_its_score`. The other half is `CrossSectionFunnel.tied_at_the_cut`, which
    is the only thing that knows about tradeable names the cut left outside -- see
    `RankingRiskFlag` for why a rule written from the shortlist alone reports a clean cut for
    exactly the boundary the funnel exists to warn about.
    """
    counted: dict[float, int] = {}
    for entry in shortlist:
        counted[entry.score] = counted.get(entry.score, 0) + 1
    return frozenset(score for score, count in counted.items() if count > 1)


def _exposure_of(
    exposures: IndustryMarketCapCrossSection | None, subject: str
) -> CandidateExposure | None:
    """This security's characteristic, with the two declarations that give it meaning attached.

    `None` when there is no cross section at all and when the cross section has no complete row
    for this name -- `IndustryMarketCapCrossSection.get` already returns `None` for a security in
    `without_industry` or `without_market_cap`, which are the two ordinary residues that plane
    exists to count, and `exposure_is_not_measured` is one flag rather than two because this
    contract cannot act differently on them.
    """
    if exposures is None:
        return None
    characteristic = exposures.get(subject)
    if characteristic is None:
        return None
    return CandidateExposure(
        industry_code=characteristic.industry_code,
        industry_level=exposures.industry_level,
        market_cap=characteristic.market_cap,
        market_cap_measure=exposures.market_cap_measure,
        is_backfilled=characteristic.is_backfilled,
    )


def _risk_flags_of(
    entry: ShortlistEntry,
    *,
    signal: SignalFrame,
    exposure: CandidateExposure | None,
    shares_its_score: bool,
) -> tuple[RankingRiskFlag, ...]:
    """The six markers, decided in `RANKING_RISK_FLAG_ORDER` and returned in it.

    Built as a filtered walk of the declared order rather than as a `sorted()` of a set, so the
    declaration is the specification and this function is the whole of the implementation --
    `_refusal_before_stage_two`'s arrangement one plane down, which is what keeps a reordering
    from being invisible.
    """
    raised: set[RankingRiskFlag] = set()
    if entry.clipped_component_count > 0:
        raised.add("score_is_a_winsorization_bound")
    if shares_its_score:
        raised.add("rank_shares_its_score")
    if exposure is None:
        raised.add("exposure_is_not_measured")
    elif exposure.is_backfilled:
        raised.add("industry_exposure_is_backfilled")
    if signal.direction == "abstain":
        raised.add("evidence_plane_abstained")
    if signal.direction == "bearish":
        raised.add("evidence_plane_is_bearish")
    return tuple(code for code in RANKING_RISK_FLAG_ORDER if code in raised)
