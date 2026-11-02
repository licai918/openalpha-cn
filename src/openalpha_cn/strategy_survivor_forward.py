"""The survivor forward report's prediction layer (`V2-P7-018`, Part E).

The survivor configuration's only evidence is its forward record: one `survivor_book_composite/v1`
batch a session, filed before the next session's call auction, scoring every eligible name with
its composite (`strategy_survivor_registration`). This module turns those records into the
prediction layer of the weekly report -- the one layer of it that can ever be evidence -- and
nothing else: the paper ledger is a record layer (`strategy_survivor_paper_report`), and the
document and its printed lines are the report script's (`survivor_forward_document`,
`survivor_forward_lines`, Task E4). It reads nothing itself; every read is the caller's, handed
in as a label reader, a prediction store and the index levels.

Every number here is a **candidate forecast of a registered research configuration; not a
performance claim** (`PREDICTION_LAYER_WORDING`). Before the pre-registered window has matured
the layer gives counts and descriptive means only, each labelled `DESCRIPTIVE_WORDING`, no
p-value and no verdict word -- "not tested: k of K pre-registered periods matured".

## The protocol (`read_forward_protocol`)

Read from the registration's `settings["survivor_forward_protocol"]` -- the configuration's own
top-level keys are closed -- as a `survivor-forward-protocol/v1` block, `extra="forbid"`, with
**no default for any field**: the values are frozen at the `V2-P7-006` checkpoint, and a default
here would be a decision taken by code. A missing or invalid block refuses the report by name
(`SurvivorForwardError`). Integers are strict (a JSON `true` is not a count). How the fields are
read:

- `anchor_ordinal` is the first sampled session's ordinal in the paper book's count
  (`SessionOrdinals`; ordinal 0 is the registered epoch, which carries no record, so it is at
  least 1);
- `primary_horizon` and `secondary_horizons` are session counts. The record is the same record at
  every horizon: a secondary horizon reads it with a shorter (or longer) label window. The primary
  horizon must be the one every survivor record declares (`SURVIVOR_HORIZON`, `20d`), which is
  also what the daily command sizes its next-year calendar fetch for: a protocol that names
  another is refused by name (`read_forward_protocol`'s `record_horizon`);
- `primary_metric` is tested at the primary horizon; `secondary_metrics` are the other metrics
  tested at the primary horizon, and at each secondary horizon only `primary_metric` is tested.
  Those two, together, are the secondary family -- `q` is its false-discovery rate, which the
  report's document applies across the horizons (this module tests one horizon at a time);
- `window_periods` (K) is the pre-registered number of non-overlapping periods of the primary
  horizon: the window is K positions of the calendar, `K * primary_horizon` sessions from the
  anchor, and the test is made once, when its last position has matured, never before (see
  "The sample and the test");
- `bootstrap_samples` and `random_seed` are `outcome_statistics.sign_flip_test`'s.

## The witnessed records (`witnessed_survivor_records`)

The prediction store is the witness. A record counts when it is **bound** to the registered
configuration (`survivor_record_is_bound`: its declaration is the one `survivor_declaration`
gives), was **registered before its session's cutoff** (`registered_at` before
`registration_cutoff`, 09:15 of the next session, P6's rule), and its session has ordinal 1 or
more (after the epoch) and is not after `through`. A late record is not a registration and is
skipped, as P6's `witnessed_days` skips one; two on-time bound records for one session refuse the
report -- a session has one record.

## One record's outcome (`record_scope`, `record_outcome`)

**The scope and its deciles come before any outcome.** The scope is the record's predictions --
every eligible name, scored by its composite. Ordered worst first by `(score, ts_code)`, the
kernel's own order (`domain/survivor_screen.screen`), the name at position `p` of `n` is in decile
`floor(10 p / n)`; the survivors are the names left after the worst `n * loser_decile_count // 10`
are dropped, which is exactly how `screen` cuts them, so a record rebuilds the day's
`screen().survivors` from its own scores.

**The label is the shared reader's** (`model_view.OutcomeLabels`, through the structural
`LabelSource`): the window opens at the close of the session after the record's (D+1) and exits
at the close `h` sessions later (D+1+h) -- `build_label_window`'s rule, so at h=20 its last close
is the record's own `outcome_known_at`, and at h=5 the same record is read over five sessions.
**A record is mature only when the report's `as_of` is at or after the exit day's 16:30
publication** (`session_publication_instant`). An immature record is counted and never labelled:
no label of it is asked for, so no price after `as_of` can enter the report. A window that runs
past the stored calendar is immature when the calendar reaches the report's own date (the exit is
then after it), and refuses the report otherwise -- the calendar has to be built forward first.

What one mature record gives, every number gross and over the **labelled** scope only (an
unlabelled name is left out of every number, and counted per decile and per refusal code --
`domain/labels`' codes, a name with two counted under both, and `no_adjustment_history` for a name
the reader has no factor series for). Leaving names out is not neutral, and every layer says so
(`UNLABELLED_WORDING`): a delisting in the window is typically a large loss that is then missing,
so the worst decile's mean is likely biased upward -- understating `ew_minus_worst_decile` and
`top_minus_bottom` rather than inflating them:

- `rank_ic`: the tie-aware Spearman correlation of score and label -- `factor_ic.average_ranks`
  on both, then `statistics.correlation` -- absent with a code when a side is degenerate
  (`fewer_than_two_labelled`, `scores_tied`, `labels_tied`);
- `investable_ew`: the mean label, an equal-weight buy-and-hold of the investable scope;
- `survivors_minus_ew` (the survivors less the equal weight), `ew_minus_worst_decile` (the
  equal weight less decile 0) and `top_minus_bottom` (decile 9 less decile 0);
- `csi500`: `close(exit) / close(entry) - 1` of the protocol's benchmark (`000905.SH`), over the
  same two closes; context only, and absent with a code when either close is missing.

## The sample and the test (`sampled_sessions`, `prediction_layer`)

Overlapping h-session labels of consecutive days are not independent, so the test is made on a
**non-overlapping** sample (the repository's `grid.non_overlapping` rule, not Newey-West): the
sessions `anchor + h k`, counted on the **calendar's** sessions. A sampled session with no
witnessed record, or with one the late-record check refused or never checked, is a **gap**: it
is named, and it does not shift the later picks -- the sample is a property of the calendar, not
of which days produced a record. A sampled record not yet mature is pending, neither a period
nor a gap. Every included mature daily record also enters a descriptive mean, printed as a point
estimate with no standard error.

**The analysis set is fixed by the calendar, before any record exists.** The window is the
first `K_h` positions of the sample, `anchor + h k` for `k = 0 .. K_h - 1`, and every horizon's
window spans the **primary** window's sessions: `K * primary_horizon` from the anchor, so
`K_h = floor(K * primary_horizon / h)` -- K at the primary horizon, 4K at h=5 against h=20 -- and
each horizon's last position exits no later than the primary's. A position is a gap when its
session has no record, a refused or an unchecked one, or (the sensitivity only) an UNVERIFIABLE
one: a gap reduces the number of periods tested, `n`, and never moves the window -- a verdict
that later turns refused takes its period out of the test and brings no later period in. The
window is **satisfied** when every one of its positions has matured: a position with a record
when its record has (`RecordOutcome.mature`), one without when the 16:30 of the exit its label
window would have (the close `h` sessions after the session after it, on the sample's calendar,
`SampledSessions.exits`) is at or before the report's `as_of` -- the outcomes' `label_as_of`;
with no outcome at all, when that exit is on or before `through`, whose cutoff has passed. Only
then is each metric tested, once, over the window's `n` periods, and the status says what the
window lost: "tested on n of K_h positions (g no record, r refused, u unverifiable excluded in
the sensitivity)". A sampled period after the window is described, never tested. A metric with
fewer than two values in the window is not tested and says why (`MINIMUM_INTERVAL_SAMPLE_SIZE`).

The headline includes the records the check verified and the ones it found UNVERIFIABLE (P6's
convention); `excluding_unverifiable` is the same layer, over the same window, with every
UNVERIFIABLE record's session turned into a gap, so the two answers are about the same calendar
periods.

**Left open for the `V2-P7-006` checkpoint** (engineering defaults here, not decisions): whether
a test needs a minimum `n` beyond the sign-flip's two values (none is required: the test runs on
the periods there are, and its status names every gap); how K applies at a secondary horizon
(here the span rule above); and what a gap means for the pre-registered claim -- whether a
window that lost positions to missing, refused or UNVERIFIABLE records still carries the claim,
or how many it may lose.

## Power (`survivor_forward_power`)

The protocol says when the test is made; whether the design can see an effect at all is a second,
pre-registered question, and the report states it beside the sample counts so that a one-sided
p-value above the test level is never read as "no effect". It is read from the registration's
`settings["survivor_forward_power"]` -- a block **beside** the protocol, not inside it, so the
registrations already written under `survivor-forward-protocol/v1` stay readable -- as a
`survivor-forward-power/v1` block (`read_forward_power`), `extra="forbid"` and with no default for
any field: the test level, the target power, the critical value (`normal`, or `t` on n - 1
degrees of freedom), the assumed lag-1 autocorrelation, the minimum number of periods, and for
each planned `(horizon, metric)` a **planning** sd, the true means to ask about and where the sd
comes from. Each planned metric must be one the protocol tests at that horizon, the primary test
must be planned, and the minimum cannot exceed the primary window's positions. A registration
without the block still gets its report: its `power` section says that no power is stated
(`POWER_NOT_PRE_REGISTERED`), carries the `basis` and the `reading_rule` and no row, and prints
that one statement. What the section holds, every sum made by `backtest.power_analysis` (standard
library only):

- The power is always the **plan's**: the planning sd, the planned periods `K_h` and the test
  level. A window's own sd and lag-1 autocorrelation are described after the window's test
  (`OBSERVED_WORDING`) and are never an input: observed power says nothing the p-value did not.
  The assumed autocorrelation sets the independent-period equivalent `K (1 - r) / (1 + r)`; a
  negative one would credit more independent periods than there are, so it is held at 0 and the
  row says so.
- A primary row is powered at the plan's test level; a secondary row at the first step of the
  Benjamini-Yekutieli family, `q / (m * H_m)` with `m` the declared family size -- a lower bound of
  the level it will face, said so in the row.
- A window is stated on two bases, in both readings (the headline and the sensitivity):
  `usable_at_most`, the window's positions less its gaps (`no_record`, `refused`, `unchecked`,
  and `unverifiable` in the sensitivity only), and, once the window is tested, `tested`, the
  periods that carry the metric. `n` is the second when there is one and the first otherwise,
  `n_basis` says which, `at_n` is the power at `n` (stated from 2 periods), and `below_minimum`
  holds `n` to the plan's minimum periods. The minimum labels the result; it does not stop the
  test, which still runs on the periods there are.
- A question the design has no answer to (fewer than two periods, a mean needing more than
  100,000, fewer than two independent periods) is an absence the row names (`absences`), never a
  traceback; the other figures of the row stand.
- The document's section always carries the reading rule (`NOT_DETECTABLE_WORDING`): a p-value
  above the test level reads "not detectable at this design", a category of the design. The
  printed block ends with it once the primary window is satisfied. Before the window the report
  states no one-sided p-value and no reading of one, so the block ends instead with one neutral
  line saying the rule waits for the window (the `basis` line defines power as a property of the
  design and names no result).

## The report's document (`survivor_forward_document`, `survivor_forward_lines`; task E4)

The weekly report script (`scripts/forward_report.py`) reads everything and hands it in; this
module puts it into one `survivor_forward_report/v1` document and prints it. What the document
does on its own:

- **The journals may not contradict the store** (`check_journals_against_the_store`, design
  section 2(a)): a daily journal whose `prediction` stage names a record must name the one the
  store witnesses for its session, and one that filed none must have none; a disagreement
  refuses the report, naming the session.
- **Where the report runs through** (`report_through`): the newest session whose record can no
  longer be filed -- its registration cutoff, 09:15 of the next session, is at or before the
  report's `as_of` -- or a newer session that already carries a witnessed record. A session after
  it may still get its record, so it is neither missing nor a gap yet; every session from ordinal
  1 through it with no witnessed record is listed as missing.
- **One analysis, at the primary window.** The window is the primary horizon's headline sample:
  until its K positions have matured, no layer's test is printed -- a secondary horizon whose own
  window matures a little sooner (its last position exits up to `h - 1` sessions before the
  primary's when `h` does not divide the span) is told "a secondary is tested in the one analysis
  at the primary window" -- and the secondary family is not corrected. Before the window: counts
  and descriptive means only, each labelled `DESCRIPTIVE_WORDING`, and no p-value anywhere.
- **Compromised** (design decision 9): a record the late-record check refused whose session is
  one of the window's positions -- the sample a test is made over -- prints that test
  "compromised (n refused)". The test is still shown; nothing about it is hidden.
- **The secondary family** -- the protocol's secondary metrics at the primary horizon and its
  primary metric at each secondary horizon -- is corrected once the window is satisfied, over the
  one-sided p-values that exist, by Benjamini-Yekutieli under arbitrary dependence at the
  protocol's `q` (`backtest.multiple_testing.control_false_discovery_rate`, the repository's own;
  a secondary without a p-value still counts in the declared family size). A secondary result
  never changes the candidate wording.
- **The paper ledger and the relative return** are `strategy_survivor_paper_report.paper_record`'s
  two blocks as the script hands them in -- or, when that record was refused, the refusal in both
  (`paper_refusal`): a fault of the paper book never costs the prediction layer. The relative
  return's windows are the headline's sampled periods at the primary horizon
  (`sampled_benchmarks`); a period whose equal weight or CSI 500 is missing is named as left out.
  The lines print the ledger's money in yuan (a holding's count and mark after an adjustment are
  the ledger's, said beside it), the one-off initial build beside the turnover and the costs and
  only the steady state annualised, from 60 settled sessions on (`paper_record`'s rule), the
  write-offs' cost basis beside their count, and each chain's returns as percentages with the
  points of its book return the write-offs inside it took.
- **What cannot be evaluated yet** (design section 6): each item is listed while it holds -- no
  survivor record filed under the registration yet, no outcome matured, the window not satisfied
  -- and the standing ones on every report, among them the bias of the names left unlabelled; a
  section like every other, `{"wording", "items"}`.
- **The late-record check's results**: counts by verdict and by scope, every UNVERIFIABLE record
  with its changes and every refused one with its reason, and the UNVERIFIABLE and refused shares
  over every checked record and over the primary window's positions. What UNVERIFIABLE means is
  said in the document (`UNVERIFIABLE_MEANING`, I8) and printed under the records that need it:
  a normal late-published name change makes one, so it is no word for "contaminated". A tested
  mean below zero says that its sign is against the registered direction (`OPPOSITE_SIGN_CLAUSE`,
  M10). How many verdicts a run
  served from the verdict cache is the run's, not the report's, and is not in the document: a
  rerun at the same `as_of` serves them all and writes the same bytes.

Every section carries `SURVIVOR_WORDING`; the prediction layer adds `PREDICTION_LAYER_WORDING`;
the document ends with them and `NO_VERDICT_WORDING`, then the caller's integrity statement
(P6's `daily_selection.INTEGRITY`). No verdict word is printed, before the window or after it:
the secondary family's step-up decision at `q` is printed as `below_q` -- "q-value <= q" -- the
procedure's arithmetic, not a finding.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Annotated, Any, Final, Literal, Protocol, Self, TypeVar, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from openalpha_cn.backtest.factor_ic import average_ranks
from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import (
    MINIMUM_INTERVAL_SAMPLE_SIZE,
    RandomizationTest,
    sign_flip_test,
)
from openalpha_cn.backtest.power_analysis import (
    NOT_DETECTABLE_WORDING,
    POWER_BASIS,
    CriticalValue,
    PowerError,
    autocorrelation_equivalent_periods,
    by_first_step_level,
    periods_for_power,
    power_of_mean_test,
    smallest_mean_at_power,
)
from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.horizon import (
    MAX_HORIZON_COUNT,
    HorizonUnit,
    ResearchHorizon,
    parse_horizon,
)
from openalpha_cn.domain.index_prices import IndexBar
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import (
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_registration import (
    SHANGHAI,
    HeldRecordLookup,
    RegisteredConfiguration,
    StrategyRegistrationError,
    registered_at,
    registration_cutoff,
)
from openalpha_cn.strategy_survivor_market import (
    PROTOCOL_BENCHMARK,
    SessionOrdinalError,
    SessionOrdinals,
)
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    SURVIVOR_HORIZON,
    survivor_record_is_bound,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

__all__ = [
    "DESCRIBED_QUANTITIES",
    "DESCRIPTIVE_WORDING",
    "FORWARD_METRICS",
    "NO_VERDICT_WORDING",
    "OBSERVED_WORDING",
    "OPPOSITE_SIGN_CLAUSE",
    "POWER_NOT_PRE_REGISTERED",
    "PREDICTION_LAYER_WORDING",
    "SURVIVOR_FORWARD_POWER_KEY",
    "SURVIVOR_FORWARD_POWER_SCHEMA",
    "SURVIVOR_FORWARD_PROTOCOL_KEY",
    "SURVIVOR_FORWARD_PROTOCOL_SCHEMA",
    "SURVIVOR_FORWARD_REPORT_KIND",
    "UNLABELLED_WORDING",
    "UNVERIFIABLE_MEANING",
    "CheckedVerdict",
    "DescribedQuantity",
    "DescriptiveMean",
    "ForwardMetric",
    "LabelSource",
    "LayerReading",
    "MetricTest",
    "NonOverlappingSample",
    "PlannedMetric",
    "PredictionLayer",
    "RecordOutcome",
    "RecordScope",
    "SampledBenchmark",
    "SampledPeriod",
    "SampledSessions",
    "ScopedName",
    "SurvivorForwardError",
    "SurvivorForwardPowerPlan",
    "SurvivorForwardProtocol",
    "VerdictLike",
    "WitnessedRecord",
    "check_journals_against_the_store",
    "paper_refusal",
    "prediction_layer",
    "read_forward_power",
    "read_forward_protocol",
    "record_outcome",
    "record_scope",
    "report_through",
    "sampled_benchmarks",
    "sampled_sessions",
    "survivor_forward_document",
    "survivor_forward_lines",
    "witnessed_survivor_records",
]

SURVIVOR_FORWARD_PROTOCOL_KEY: Final = "survivor_forward_protocol"
"""The key of the registration's `settings` that holds the forward protocol."""
SURVIVOR_FORWARD_PROTOCOL_SCHEMA: Final = "survivor-forward-protocol/v1"
SURVIVOR_FORWARD_POWER_KEY: Final = "survivor_forward_power"
"""The key of the registration's `settings` that holds the power plan, beside the protocol's."""
SURVIVOR_FORWARD_POWER_SCHEMA: Final = "survivor-forward-power/v1"

POWER_NOT_PRE_REGISTERED: Final = (
    "the registration carries no survivor_forward_power block: no power is stated, and a result "
    "above the test level cannot be read against a pre-registered design"
)
OBSERVED_WORDING: Final = "descriptive, over the tested periods; never used for power"

PREDICTION_LAYER_WORDING: Final = (
    "candidate forecast of a registered research configuration; not a performance claim"
)
DESCRIPTIVE_WORDING: Final = "descriptive, not a test"
UNLABELLED_WORDING: Final = (
    "names without a label (delisted_in_window, halted_session, locked_at_limit, "
    "no_adjustment_history, ...) are left out of the rank IC and of every mean, and counted by "
    "code and by decile; a delisting is typically a large loss that is then missing, so the worst "
    "decile's mean is likely biased upward, which would understate ew_minus_worst_decile and "
    "top_minus_bottom"
)
"""Which way leaving the unlabelled names out (design decision 7) likely biases the layer."""

ForwardMetric = Literal[
    "rank_ic", "survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"
]
"""A quantity the protocol may test: each is centred on zero under the null of no forecast, and
each is oriented the same way -- **greater than zero supports the screen** (the scores order the
labels, the survivors beat the equal weight, the worst decile falls behind it, the top decile
beats the worst). That is why one one-sided p, for "mean > 0", serves every one of them: the
equal weight's lead over the worst decile is tested, never the worst decile's excess over the
equal weight, whose support for the screen would be a mean below zero."""
FORWARD_METRICS: Final[tuple[ForwardMetric, ...]] = get_args(ForwardMetric)
DescribedQuantity = Literal[
    "rank_ic",
    "investable_ew",
    "survivors_minus_ew",
    "ew_minus_worst_decile",
    "top_minus_bottom",
    "csi500",
]
"""Every quantity one record reports, in the order a layer describes them. The equal weight and
the benchmark are levels, not excesses, so they are described and never tested."""
DESCRIBED_QUANTITIES: Final[tuple[DescribedQuantity, ...]] = get_args(DescribedQuantity)
Role = Literal["primary", "secondary"]
Standing = Literal["verified", "unverifiable", "refused", "unchecked"]
GapReason = Literal["no_record", "refused", "unchecked", "unverifiable"]

NO_ADJUSTMENT_HISTORY: Final = "no_adjustment_history"
"""The unlabelled code of a name the reader has no factor series for (it answers `None`)."""
_VERDICTS: Final[Mapping[str, Standing]] = {
    "verified": "verified",
    "unverifiable": "unverifiable",
    "refused": "refused",
}
"""A check's verdict as the layer's standing of its record (a record with none is unchecked)."""
_GAP_REASONS: Final[Mapping[str, GapReason]] = {
    "refused": "refused",
    "unchecked": "unchecked",
    "unverifiable": "unverifiable",
}
_DECILES: Final[int] = 10


class SurvivorForwardError(StrategyRegistrationError):
    """The survivor forward report's prediction layer cannot be produced as asked: no protocol
    or an invalid one, two records for one session, a sample or a window that cannot be drawn.

    A `StrategyRegistrationError`, so the report script's existing refusal of a registration
    error holds this one too."""


# --- the protocol --------------------------------------------------------------------------------

_Count = Annotated[int, Field(strict=True, ge=1)]
_Horizon = Annotated[int, Field(strict=True, ge=1, le=MAX_HORIZON_COUNT)]


class SurvivorForwardProtocol(BaseModel):
    """The pre-registered forward protocol; see the module docstring. No field has a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-forward-protocol/v1"]
    anchor_ordinal: _Count
    primary_horizon: _Horizon
    secondary_horizons: tuple[_Horizon, ...]
    primary_metric: ForwardMetric
    secondary_metrics: tuple[ForwardMetric, ...]
    window_periods: _Count
    bootstrap_samples: Annotated[int, Field(strict=True, ge=100, le=100_000)]
    """`outcome_statistics.OutcomeStatisticsRequest`'s bounds: the same test by the same rule."""
    random_seed: Annotated[int, Field(strict=True, ge=0)]
    q: Annotated[float, Field(strict=True, gt=0, lt=1)]

    @model_validator(mode="after")
    def _the_secondaries_are_distinct_from_each_other_and_the_primary(self) -> Self:
        horizons = self.secondary_horizons
        if len(set(horizons)) != len(horizons) or self.primary_horizon in horizons:
            raise ValueError("secondary_horizons must not repeat a horizon or the primary_horizon")
        metrics = self.secondary_metrics
        if len(set(metrics)) != len(metrics) or self.primary_metric in metrics:
            raise ValueError("secondary_metrics must not repeat a metric or the primary_metric")
        span = self.window_periods * self.primary_horizon
        longer = sorted(horizon for horizon in horizons if horizon > span)
        if longer:
            raise ValueError(
                f"secondary_horizons {longer} are longer than the primary window's span "
                f"({self.window_periods} periods of {self.primary_horizon} sessions, {span} "
                "sessions), so they have no position in it"
            )
        return self

    @property
    def window_span_sessions(self) -> int:
        """The primary window's span: K periods of the primary horizon, in sessions."""
        return self.window_periods * self.primary_horizon

    def window_positions(self, horizon: int) -> int:
        """`K_h`: the whole `horizon`-session positions that fit the primary window's span,
        `floor(K * primary_horizon / horizon)` (module docstring); a horizon not in the protocol
        refuses."""
        self.tested_metrics(horizon)
        return self.window_span_sessions // horizon

    def tested_metrics(self, horizon: int) -> tuple[tuple[ForwardMetric, Role], ...]:
        """What is tested at `horizon`, and in which role; a horizon not in the protocol refuses."""
        if horizon == self.primary_horizon:
            return (
                (self.primary_metric, "primary"),
                *((metric, "secondary") for metric in self.secondary_metrics),
            )
        if horizon in self.secondary_horizons:
            return ((self.primary_metric, "secondary"),)
        raise SurvivorForwardError(
            f"the protocol reads the horizons {self.primary_horizon} (primary) and "
            f"{list(self.secondary_horizons)} (secondary), not horizon {horizon}; a readout at a "
            "horizon nobody pre-registered is not part of the report"
        )


def read_forward_protocol(
    settings: Mapping[str, object], *, record_horizon: str = SURVIVOR_HORIZON
) -> SurvivorForwardProtocol:
    """The protocol in a registration's `settings`, or `SurvivorForwardError` naming why not.

    `record_horizon` is the horizon the records it is read for declare -- every survivor record's
    `SURVIVOR_HORIZON` by default -- and the protocol's `primary_horizon` must be it, in sessions
    (seams review 5): the primary test is made at the horizon the records declare, which is also
    the one the daily command sizes its next-year calendar fetch for. A secondary horizon reads
    the same record over another window and is not pinned."""
    protocol = _read_protocol_block(settings)
    try:
        declared = parse_horizon(record_horizon).sessions
    except ValueError as error:
        raise SurvivorForwardError(
            f"record_horizon {record_horizon!r} is not a horizon in sessions: {error}"
        ) from error
    if protocol.primary_horizon != declared:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_PROTOCOL_KEY} names the primary_horizon "
            f"{protocol.primary_horizon}, and every survivor record declares the horizon "
            f"{record_horizon} ({declared} sessions): the primary test is made at the horizon the "
            "records declare -- the one the daily command sizes its calendar fetch for -- and a "
            "protocol that tests another is not this record's protocol"
        )
    return protocol


def _read_protocol_block(settings: Mapping[str, object]) -> SurvivorForwardProtocol:
    if not isinstance(settings, Mapping):
        raise SurvivorForwardError(
            f"the registration's settings are {type(settings).__name__}, not an object, so they "
            f"carry no {SURVIVOR_FORWARD_PROTOCOL_KEY} block"
        )
    if SURVIVOR_FORWARD_PROTOCOL_KEY not in settings:
        raise SurvivorForwardError(
            f"the registration's settings carry no {SURVIVOR_FORWARD_PROTOCOL_KEY} block; the "
            "survivor forward report reads its window, its horizons, its metrics and its test "
            "from the protocol frozen at V2-P7-006, and is not produced without one"
        )
    block = settings[SURVIVOR_FORWARD_PROTOCOL_KEY]
    if not isinstance(block, Mapping):
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_PROTOCOL_KEY} is {type(block).__name__}, not "
            f"a {SURVIVOR_FORWARD_PROTOCOL_SCHEMA} object"
        )
    try:
        return SurvivorForwardProtocol.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_PROTOCOL_KEY} is not a valid "
            f"{SURVIVOR_FORWARD_PROTOCOL_SCHEMA} block: {problems}"
        ) from error


# --- the power plan ------------------------------------------------------------------------------

_Positive = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


class PlannedMetric(BaseModel):
    """One `(horizon, metric)` the plan states a power for; see the module docstring. A
    planning value, not an observation: no field is read off a record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon: _Horizon
    metric: ForwardMetric
    planning_sd: _Positive
    planning_means: Annotated[tuple[_Positive, ...], Field(min_length=1)]
    """The true means the design is asked about: each is greater than zero, the direction that
    supports the screen."""
    sd_source: Annotated[str, Field(strict=True)]
    """Where the planning sd comes from: a statement, not a default."""

    @field_validator("sd_source")
    @classmethod
    def _the_sd_has_a_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sd_source must say where the planning sd comes from")
        return value


class SurvivorForwardPowerPlan(BaseModel):
    """The pre-registered power plan; see the module docstring. No field has a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-forward-power/v1"]
    test_level: Annotated[float, Field(strict=True, gt=0, lt=0.5, allow_inf_nan=False)]
    target_power: Annotated[float, Field(strict=True, gt=0, lt=1, allow_inf_nan=False)]
    critical_value: CriticalValue
    assumed_lag1_autocorrelation: Annotated[
        float, Field(strict=True, gt=-1, lt=1, allow_inf_nan=False)
    ]
    minimum_periods: Annotated[int, Field(strict=True, ge=2)]
    planned: Annotated[tuple[PlannedMetric, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _a_metric_is_planned_once(self) -> Self:
        keys = [(item.horizon, item.metric) for item in self.planned]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueError(f"planned must not repeat a (horizon, metric); repeated: {repeated}")
        return self


def read_forward_power(
    settings: Mapping[str, object], *, protocol: SurvivorForwardProtocol
) -> SurvivorForwardPowerPlan | None:
    """The power plan in a registration's `settings`, `None` when it carries none, or
    `SurvivorForwardError` naming why the block does not read.

    The block is held to `protocol`: every planned `(horizon, metric)` is one the protocol tests,
    the primary test is planned, and the minimum number of periods is no more than the primary
    window's positions."""
    if not isinstance(settings, Mapping):
        raise SurvivorForwardError(
            f"the registration's settings are {type(settings).__name__}, not an object, so they "
            f"carry no {SURVIVOR_FORWARD_POWER_KEY} block"
        )
    if SURVIVOR_FORWARD_POWER_KEY not in settings:
        return None
    block = settings[SURVIVOR_FORWARD_POWER_KEY]
    if not isinstance(block, Mapping):
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_POWER_KEY} is {type(block).__name__}, not a "
            f"{SURVIVOR_FORWARD_POWER_SCHEMA} object"
        )
    try:
        plan = SurvivorForwardPowerPlan.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_POWER_KEY} is not a valid "
            f"{SURVIVOR_FORWARD_POWER_SCHEMA} block: {problems}"
        ) from error
    _hold_the_plan_to_the_protocol(plan, protocol)
    return plan


def _hold_the_plan_to_the_protocol(
    plan: SurvivorForwardPowerPlan, protocol: SurvivorForwardProtocol
) -> None:
    for item in plan.planned:
        try:
            tested = dict(protocol.tested_metrics(item.horizon))
        except SurvivorForwardError as error:
            raise SurvivorForwardError(
                f"the registration's {SURVIVOR_FORWARD_POWER_KEY} plans horizon {item.horizon}: "
                f"{error}"
            ) from error
        if item.metric not in tested:
            raise SurvivorForwardError(
                f"the registration's {SURVIVOR_FORWARD_POWER_KEY} plans {item.metric} at horizon "
                f"{item.horizon}, which is not tested there by the protocol (it tests "
                f"{sorted(tested)})"
            )
    primary = (protocol.primary_horizon, protocol.primary_metric)
    if primary not in {(item.horizon, item.metric) for item in plan.planned}:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_POWER_KEY} does not plan the protocol's "
            f"primary test (horizon {primary[0]}, {primary[1]}): the design its result is read "
            "against has to be stated"
        )
    positions = protocol.window_positions(protocol.primary_horizon)
    if plan.minimum_periods > positions:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_POWER_KEY} has minimum_periods "
            f"{plan.minimum_periods}, more than the primary window's {positions} positions: a "
            "window that cannot reach its own minimum is not a design"
        )


# --- the witnessed records -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class WitnessedRecord:
    """One session's record, as the prediction store witnesses it."""

    session: date
    ordinal: int
    """The session's ordinal in the paper book's count (`SessionOrdinals`): 1 or more."""
    record_id: str
    record: PredictionRecord


def witnessed_survivor_records(
    records: HeldRecordLookup,
    *,
    book: SurvivorBookConfig,
    registered: RegisteredConfiguration,
    calendar: TradingCalendar,
    ordinals: SessionOrdinals,
    through: date,
) -> tuple[WitnessedRecord, ...]:
    """Every session's one bound, on-time record from ordinal 1 through `through`, by session.

    See the module docstring for the rules. Refuses two on-time bound records of one session.
    """
    on_time: dict[date, list[tuple[str, PredictionRecord]]] = {}
    for record_id in records.list_ids():
        record = records.get(record_id)
        if record is None or not survivor_record_is_bound(record, book, registered):
            continue
        session = record.batch.as_of.astimezone(SHANGHAI).date()
        if session <= ordinals.epoch or session > through:
            continue
        try:
            cutoff = registration_cutoff(calendar, session)
        except TradingCalendarError as error:
            raise SurvivorForwardError(
                f"the registration cutoff of {session.isoformat()} (09:15 of the next session) "
                f"cannot be read off the {calendar.exchange} calendar: {error}"
            ) from error
        if registered_at(record) >= cutoff:
            continue
        on_time.setdefault(session, []).append((record_id, record))
    witnessed: list[WitnessedRecord] = []
    for session in sorted(on_time):
        held = on_time[session]
        if len(held) > 1:
            raise SurvivorForwardError(
                f"{session.isoformat()} carries {len(held)} records bound to the registration, "
                f"each registered before its cutoff ({sorted(record_id for record_id, _ in held)})"
                "; a session has one record, and the report does not choose between them"
            )
        record_id, record = held[0]
        try:
            ordinal = ordinals.index_of(session)
        except SessionOrdinalError as error:
            raise SurvivorForwardError(
                f"{record_id} is bound to the registration and dated {session.isoformat()}, "
                f"which has no session ordinal: {error}"
            ) from error
        witnessed.append(
            WitnessedRecord(session=session, ordinal=ordinal, record_id=record_id, record=record)
        )
    return tuple(witnessed)


def check_journals_against_the_store(
    witnessed: Sequence[WitnessedRecord],
    journals: Mapping[date, Mapping[str, Any]],
    *,
    through: date,
) -> None:
    """The store is the witness, and the journals may not contradict it (task E4).

    Every journal of a session on or before `through` whose `prediction` stage says its evening
    filed a record must name the record the store witnesses for that session -- bound, on time --
    and one whose stage says it filed none must have none. A journal without a `prediction`
    stage claims nothing, and a witnessed session without a journal is the store's word alone.
    `journals` are the daily command's survivor journals by session, read by the caller. Refuses
    the first session that disagrees, as P6's schedule does: neither side is preferred."""
    held = {item.session: item.record_id for item in witnessed}
    for session in sorted(journals):
        if session > through:
            continue
        stage = journals[session].get("prediction")
        if not isinstance(stage, Mapping):
            continue
        named = stage.get("record_id") if stage.get("registered") is True else None
        if named != held.get(session):
            raise SurvivorForwardError(
                f"the journal of {session.isoformat()} says its evening filed "
                f"{named or 'no record'} and the store witnesses {held.get(session) or 'none'} "
                "for that session (bound and on time): the store is the witness, and a journal "
                "that disagrees with it is not a record to report on"
            )


# --- the sampled sessions ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledSessions:
    """The non-overlapping sample at one horizon: `anchor + horizon * k` through `through`."""

    anchor_ordinal: int
    horizon: int
    through: date
    sessions: tuple[date, ...]
    exits: tuple[date | None, ...]
    """Each session's label exit at `horizon`: the close `horizon` sessions after the session
    after it (`build_label_window`'s rule), on the calendar the sample is drawn on; `None` past
    that calendar's end. A sampled session with no record matures when this exit does."""


def sampled_sessions(
    ordinals: SessionOrdinals, *, anchor_ordinal: int, through: date, horizon: int
) -> SampledSessions:
    """The sessions with ordinal `anchor_ordinal + horizon * k` on or before `through`.

    Counted on the calendar's sessions (`ordinals`), never on the sessions that carry a record:
    a session with no record is a gap in the sample, not a reason to move the next pick. Each
    carries its label exit (`SampledSessions.exits`).
    """
    if type(anchor_ordinal) is not int or anchor_ordinal < 1:
        raise SurvivorForwardError(
            f"the sample's anchor ordinal is {anchor_ordinal!r}; ordinal 0 is the registered "
            "epoch, which carries no record, so the anchor is a whole number of at least 1"
        )
    _require_horizon(horizon)
    try:
        last = ordinals.index_of(through)
    except SessionOrdinalError as error:
        raise SurvivorForwardError(
            f"the sample cannot be drawn through {through.isoformat()}: {error}"
        ) from error
    drawn = range(anchor_ordinal, last + 1, horizon)
    return SampledSessions(
        anchor_ordinal=anchor_ordinal,
        horizon=horizon,
        through=through,
        sessions=tuple(ordinals.day_of(ordinal) for ordinal in drawn),
        exits=tuple(_exit_of(ordinals, ordinal, horizon=horizon) for ordinal in drawn),
    )


def _exit_of(ordinals: SessionOrdinals, ordinal: int, *, horizon: int) -> date | None:
    try:
        return ordinals.day_of(ordinal + 1 + horizon)
    except SessionOrdinalError:
        return None


def _require_horizon(horizon: int) -> None:
    if type(horizon) is not int or not 1 <= horizon <= MAX_HORIZON_COUNT:
        raise SurvivorForwardError(
            f"the horizon is {horizon!r}; a label window spans a whole number of sessions from "
            f"1 to {MAX_HORIZON_COUNT}"
        )


# --- one record's scope --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedName:
    """One name of a record's scope: its score, its worst-first position and its decile."""

    ts_code: str
    score: float
    position: int
    decile: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordScope:
    """A record's scope cut before any outcome: worst first, and the survivors best first."""

    names: tuple[ScopedName, ...]
    """Worst first by `(score, ts_code)`; `decile = floor(10 * position / n)`."""
    survivors: tuple[str, ...]
    """The scope less its worst `n * loser_decile_count // 10`, best first by `(-score,
    ts_code)` -- `screen().survivors`' codes, in their order."""


def record_scope(batch: PredictionBatch, *, book: SurvivorBookConfig) -> RecordScope:
    """The deciles and the survivors of a survivor record's scope; see the module docstring."""
    scored: list[tuple[float, str]] = []
    for prediction in batch.predictions:
        if prediction.score is None:
            raise SurvivorForwardError(
                f"{prediction.ts_code} abstains in the batch of {batch.as_of.isoformat()}; a "
                "survivor record scores every eligible name and abstains for none, so this is "
                "not one"
            )
        scored.append((prediction.score, prediction.ts_code))
    worst_first = sorted(scored)
    count = len(worst_first)
    dropped = count * book.loser_decile_count // _DECILES
    return RecordScope(
        names=tuple(
            ScopedName(
                ts_code=code, score=score, position=position, decile=_DECILES * position // count
            )
            for position, (score, code) in enumerate(worst_first)
        ),
        survivors=tuple(
            code
            for score, code in sorted(worst_first[dropped:], key=lambda pair: (-pair[0], pair[1]))
        ),
    )


# --- one record's outcome ------------------------------------------------------------------------


class LabelSource(Protocol):
    """What the outcome of one record needs of a label reader: `model_view.OutcomeLabels`."""

    @property
    def calendar(self) -> TradingCalendar: ...

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow: ...

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordOutcome:
    """One witnessed record read at one horizon; see the module docstring for every number.

    An immature record carries its scope and nothing measured: every number is `None`, and no
    name of it was labelled."""

    record_id: str
    session: date
    ordinal: int
    horizon: int
    label_as_of: datetime
    """The report's `as_of`, which every label of the report is read at."""
    entry_day: date | None
    exit_day: date | None
    """`None` only when the window runs past the stored calendar's end (an immature record)."""
    mature: bool
    scope: int
    survivors: int
    labelled: int
    unlabelled: int
    unlabelled_by_code: tuple[tuple[str, int], ...]
    """Names per refusal code, ascending by code; a name with two codes counts under both."""
    scope_by_decile: tuple[int, ...]
    unlabelled_by_decile: tuple[int, ...]
    rank_ic: float | None
    investable_ew: float | None
    survivors_minus_ew: float | None
    ew_minus_worst_decile: float | None
    top_minus_bottom: float | None
    csi500: float | None
    absent: tuple[tuple[str, str], ...]
    """`(quantity, code)` for every number a mature record could not put a value on."""

    def value(self, quantity: DescribedQuantity) -> float | None:
        """The record's number for `quantity` (one of `DESCRIBED_QUANTITIES`)."""
        values: dict[str, float | None] = {
            "rank_ic": self.rank_ic,
            "investable_ew": self.investable_ew,
            "survivors_minus_ew": self.survivors_minus_ew,
            "ew_minus_worst_decile": self.ew_minus_worst_decile,
            "top_minus_bottom": self.top_minus_bottom,
            "csi500": self.csi500,
        }
        return values[quantity]


def record_outcome(
    labels: LabelSource,
    witnessed: WitnessedRecord,
    *,
    horizon: int,
    book: SurvivorBookConfig,
    csi500: Sequence[IndexBar],
    as_of: datetime,
) -> RecordOutcome:
    """`witnessed`'s record labelled over `horizon` sessions by `labels`, as of `as_of`.

    `csi500` is the protocol benchmark's level series (`panel_ingest.load_index_prices(...)
    [PROTOCOL_BENCHMARK]`). No label is read for a record that is not mature at `as_of`; the
    reader's own refusals propagate unchanged. See the module docstring.
    """
    _require_horizon(horizon)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SurvivorForwardError(f"the report's as_of {as_of.isoformat()} carries no timezone")
    foreign = sorted({bar.ts_code for bar in csi500 if bar.ts_code != PROTOCOL_BENCHMARK})
    if foreign:
        raise SurvivorForwardError(
            f"the benchmark levels handed in are {foreign}'s; the report's benchmark is "
            f"{PROTOCOL_BENCHMARK}, and another index's closes would be printed under its name"
        )
    record = witnessed.record
    scope = record_scope(record.batch, book=book)
    count = len(scope.names)
    scope_by_decile = _per_decile(scope.names)
    entry, window = _label_window(labels, record.batch.as_of, horizon=horizon, as_of=as_of)
    immature = RecordOutcome(
        record_id=witnessed.record_id,
        session=witnessed.session,
        ordinal=witnessed.ordinal,
        horizon=horizon,
        label_as_of=as_of,
        entry_day=entry,
        exit_day=None if window is None else window.exit_day,
        mature=False,
        scope=count,
        survivors=len(scope.survivors),
        labelled=0,
        unlabelled=0,
        unlabelled_by_code=(),
        scope_by_decile=scope_by_decile,
        unlabelled_by_decile=(0,) * _DECILES,
        rank_ic=None,
        investable_ew=None,
        survivors_minus_ew=None,
        ew_minus_worst_decile=None,
        top_minus_bottom=None,
        csi500=None,
        absent=(),
    )
    if window is None or as_of < session_publication_instant(window.exit_day):
        return immature
    values: dict[str, float] = {}
    codes: Counter[str] = Counter()
    unlabelled = [
        name for name in scope.names if not _labelled(labels, name, window, values, codes)
    ]
    labelled = [name for name in scope.names if name.ts_code in values]
    absent: list[tuple[str, str]] = []

    def measured(quantity: str, value: float | None, code: str) -> float | None:
        if value is None:
            absent.append((quantity, code))
        return value

    rank_ic, rank_ic_code = _rank_ic(labelled, values)
    equal_weight = _mean(values[name.ts_code] for name in labelled)
    survivors = set(scope.survivors)
    survivor_mean = _mean(values[name.ts_code] for name in labelled if name.ts_code in survivors)
    worst = _mean(values[name.ts_code] for name in labelled if name.decile == 0)
    top = _mean(values[name.ts_code] for name in labelled if name.decile == _DECILES - 1)
    return dataclasses.replace(
        immature,
        mature=True,
        labelled=len(labelled),
        unlabelled=len(unlabelled),
        unlabelled_by_code=tuple(sorted(codes.items())),
        unlabelled_by_decile=_per_decile(unlabelled),
        rank_ic=measured("rank_ic", rank_ic, rank_ic_code),
        investable_ew=measured("investable_ew", equal_weight, "no_labelled_name"),
        survivors_minus_ew=measured(
            "survivors_minus_ew",
            _less(survivor_mean, equal_weight),
            "no_labelled_survivor",
        ),
        ew_minus_worst_decile=measured(
            "ew_minus_worst_decile",
            _less(equal_weight, worst),
            "no_labelled_name_in_the_worst_decile",
        ),
        top_minus_bottom=measured(
            "top_minus_bottom",
            _less(top, worst),
            "no_labelled_name_in_the_top_decile"
            if top is None
            else "no_labelled_name_in_the_worst_decile",
        ),
        csi500=measured(
            "csi500", _close_ratio(csi500, entry, window.exit_day), "benchmark_close_missing"
        ),
        absent=tuple(absent),
    )


def _label_window(
    labels: LabelSource, instant: datetime, *, horizon: int, as_of: datetime
) -> tuple[date | None, LabelWindow | None]:
    """The record's entry session and label window, or `None` for a window past the calendar.

    The reach is checked on the reader's own calendar before the reader is asked, so a window
    that runs past the calendar's end is told apart from every other refusal: immature when the
    calendar reaches the report's date (its exit is then after it), refused otherwise. A record
    whose session the calendar does not hold at all is refused: it is unknown, not immature.
    """
    calendar = labels.calendar
    day = instant.astimezone(SHANGHAI).date()
    reach = calendar.horizon
    if not reach.covers(day):
        raise SurvivorForwardError(
            f"the record of {day.isoformat()} is outside the {calendar.exchange} calendar the "
            f"labels are read on ({reach.first_date.isoformat()}..{reach.last_date.isoformat()}); "
            "read the labels over the years the records span"
        )
    entry: date | None = None
    try:
        entry = calendar.next_trading_day(day)
        calendar.shift(entry, horizon)
    except CalendarHorizonError as error:
        reported = as_of.astimezone(SHANGHAI).date()
        if reported <= reach.last_date:
            return entry, None
        raise SurvivorForwardError(
            f"the {horizon}-session label window of {day.isoformat()} leaves the "
            f"{calendar.exchange} calendar, which stops at "
            f"{calendar.horizon.last_date.isoformat()}, before the report's as_of "
            f"{reported.isoformat()}: whether the record has matured is unknown until the "
            f"calendar is built forward ({error})"
        ) from error
    window = labels.window(
        instant, horizon=ResearchHorizon(count=horizon, unit=HorizonUnit.trading_days)
    )
    return window.entry_day, window


def _labelled(
    labels: LabelSource,
    name: ScopedName,
    window: LabelWindow,
    values: dict[str, float],
    codes: Counter[str],
) -> bool:
    """Label one name into `values`, or count its refusal codes into `codes`; whether it was."""
    label = labels.label(name.ts_code, window)
    if label is None:
        codes[NO_ADJUSTMENT_HISTORY] += 1
        return False
    if not label.is_labelled:
        codes.update(sorted({refusal.code for refusal in label.refusals}))
        return False
    values[name.ts_code] = label.realized_return
    return True


def _rank_ic(
    labelled: Sequence[ScopedName], values: Mapping[str, float]
) -> tuple[float | None, str]:
    """The tie-aware Spearman correlation of score and label over the labelled names."""
    if len(labelled) < 2:
        return None, "fewer_than_two_labelled"
    scores = [name.score for name in labelled]
    returns = [values[name.ts_code] for name in labelled]
    if len(set(scores)) == 1:
        return None, "scores_tied"
    if len(set(returns)) == 1:
        return None, "labels_tied"
    return statistics.correlation(average_ranks(scores), average_ranks(returns)), ""


def _close_ratio(levels: Sequence[IndexBar], entry: date | None, exit_day: date) -> float | None:
    closes = {bar.trade_date: bar.close for bar in levels}
    opened = None if entry is None else closes.get(entry)
    closed = closes.get(exit_day)
    if opened is None or closed is None or opened <= 0:
        return None
    return closed / opened - 1.0


def _per_decile(names: Sequence[ScopedName]) -> tuple[int, ...]:
    counts = Counter(name.decile for name in names)
    return tuple(counts.get(decile, 0) for decile in range(_DECILES))


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return statistics.fmean(present) if present else None


def _less(value: float | None, base: float | None) -> float | None:
    return None if value is None or base is None else value - base


# --- the prediction layer ------------------------------------------------------------------------


class VerdictLike(Protocol):
    """What the layer reads of a late-record check's verdict (`SurvivorRecordVerdict`)."""

    @property
    def verdict(self) -> str:
        """`verified`, `unverifiable` or `refused`."""
        ...

    @property
    def changes(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DescriptiveMean:
    """A point estimate with no standard error: a mean over the records it names, described."""

    quantity: DescribedQuantity
    n: int
    mean: float | None
    wording: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledPeriod:
    """One sampled session whose record is included and mature: its numbers at this horizon."""

    session: date
    record_id: str
    values: tuple[tuple[DescribedQuantity, float | None], ...]

    def value(self, quantity: DescribedQuantity) -> float | None:
        return dict(self.values)[quantity]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricTest:
    """The one test of one metric over the window's periods (module docstring).

    `test` is `outcome_statistics.sign_flip_test`'s two-sided answer for "mean == 0";
    `one_sided_p_value` is the protocol's one-sided p for "mean > 0" from it -- half of it when
    the mean is positive, one less half of it otherwise (`scripts/research/grid.py`'s
    `one_sided_p_value` rule). "Mean > 0" is the direction that supports the screen for every
    `ForwardMetric`, so the rule holds whichever metric is tested. Neither is a verdict: no
    threshold is applied here."""

    metric: ForwardMetric
    role: Role
    values: int
    mean: float | None
    test: RandomizationTest | None
    one_sided_p_value: float | None
    absence: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NonOverlappingSample:
    """The sample at one horizon, its gaps and pending sessions, its window of `K_h` calendar
    positions, and the test once every one of them has matured (module docstring)."""

    anchor_ordinal: int
    horizon: int
    sampled: tuple[date, ...]
    gaps: tuple[tuple[date, GapReason], ...]
    """Every sampled session that is a gap, in the window or after it."""
    pending: tuple[date, ...]
    """Sampled sessions whose included record has not matured: neither a period nor a gap."""
    periods: tuple[SampledPeriod, ...]
    """Every sampled period whose included record matured, in the window or after it."""
    window_positions: int
    """`K_h`, this horizon's positions (`SurvivorForwardProtocol.window_positions`)."""
    window_span_sessions: int
    """The primary window's span, `K * primary_horizon` sessions from the anchor."""
    window_sessions: tuple[date, ...]
    """The window's positions sampled so far: the first `K_h` of `sampled`."""
    in_window: tuple[tuple[str, int], ...]
    """The window's sampled positions by what they are in this reading: `periods`, `pending`,
    and the gaps `no_record`, `refused`, `unchecked`, `unverifiable` (the sensitivity's only)."""
    tested_periods: tuple[SampledPeriod, ...]
    """The periods among the window's positions: the sample a test is made over."""
    matured: int
    """k: the window's positions that have matured, with a record or without one."""
    window_satisfied: bool
    """All `K_h` positions have matured."""
    means: tuple[DescriptiveMean, ...]
    """Every described quantity over all `periods`, descriptive."""
    tests: tuple[MetricTest, ...]
    """Empty until `window_satisfied`; then one per tested metric of the horizon, each over
    `tested_periods`."""
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LayerReading:
    """The layer over one set of included records (the headline, or the sensitivity)."""

    included: int
    """Mature records the reading includes."""
    unlabelled_by_code: tuple[tuple[str, int], ...]
    unlabelled_by_decile: tuple[int, ...]
    scope_by_decile: tuple[int, ...]
    descriptive: tuple[DescriptiveMean, ...]
    """Every described quantity over every included mature daily record (overlapping)."""
    non_overlapping: NonOverlappingSample
    unlabelled_wording: str
    """`UNLABELLED_WORDING`: which way the names left unlabelled likely bias the numbers."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PredictionLayer:
    """The prediction layer at one horizon; see the module docstring."""

    horizon: int
    role: Role
    label_as_of: datetime | None
    witnessed: int
    mature: int
    immature: int
    excluded: tuple[tuple[str, int], ...]
    """Witnessed records left out of every reading, by reason (`refused`, `unchecked`)."""
    unverifiable: int
    """Witnessed records the check found UNVERIFIABLE: in the headline, out of the sensitivity."""
    headline: LayerReading
    """Verified and UNVERIFIABLE records (P6's convention)."""
    excluding_unverifiable: LayerReading
    """The same with every UNVERIFIABLE record's sampled session a gap."""
    wording: str


def prediction_layer(
    outcomes: Sequence[RecordOutcome],
    verdicts: Mapping[str, VerdictLike],
    *,
    protocol: SurvivorForwardProtocol,
    sampled: SampledSessions,
) -> PredictionLayer:
    """The prediction layer of `outcomes` -- one horizon's, `sampled.horizon` -- under `protocol`.

    `verdicts` are the late-record check's, by record id; a record without one is unchecked and
    left out. The sign-flip test is made only once every one of the window's `K_h` calendar
    positions has matured, over the periods among them. See the module docstring.
    """
    horizon = sampled.horizon
    metrics = protocol.tested_metrics(horizon)
    if sampled.anchor_ordinal != protocol.anchor_ordinal:
        raise SurvivorForwardError(
            f"the sample is anchored at ordinal {sampled.anchor_ordinal} and the protocol at "
            f"{protocol.anchor_ordinal}; the sample is the protocol's or it is not the test's"
        )
    by_session: dict[date, RecordOutcome] = {}
    for outcome in outcomes:
        if outcome.horizon != horizon:
            raise SurvivorForwardError(
                f"{outcome.record_id} is read at horizon {outcome.horizon} and the sample is "
                f"drawn at horizon {horizon}; a layer is one horizon's"
            )
        if outcome.session in by_session:
            raise SurvivorForwardError(
                f"{outcome.session.isoformat()} has two outcomes "
                f"({by_session[outcome.session].record_id}, {outcome.record_id}); a session has "
                "one record"
            )
        by_session[outcome.session] = outcome
    instants = {outcome.label_as_of for outcome in outcomes}
    if len(instants) > 1:
        raise SurvivorForwardError(
            f"the outcomes were labelled at {len(instants)} instants; a report reads every label "
            "at its one as_of"
        )
    label_as_of = next(iter(instants), None)
    for session, exit_day in zip(sampled.sessions, sampled.exits, strict=True):
        held = by_session[session].exit_day if session in by_session else None
        if held is not None and exit_day is not None and held != exit_day:
            raise SurvivorForwardError(
                f"{session.isoformat()}'s record exits at {held.isoformat()} and its position's "
                f"label window exits at {exit_day.isoformat()} on the sample's calendar; a "
                "position matures on one calendar"
            )
    standing = {session: _standing(outcome, verdicts) for session, outcome in by_session.items()}
    excluded = Counter(state for state in standing.values() if state in ("refused", "unchecked"))

    def reading(left_out: frozenset[str]) -> LayerReading:
        return _reading(
            by_session,
            standing,
            left_out=left_out,
            sampled=sampled,
            protocol=protocol,
            metrics=metrics,
            label_as_of=label_as_of,
        )

    return PredictionLayer(
        horizon=horizon,
        role=metrics[0][1],
        label_as_of=label_as_of,
        witnessed=len(by_session),
        mature=sum(1 for outcome in by_session.values() if outcome.mature),
        immature=sum(1 for outcome in by_session.values() if not outcome.mature),
        excluded=tuple(sorted(excluded.items())),
        unverifiable=sum(1 for state in standing.values() if state == "unverifiable"),
        headline=reading(frozenset({"refused", "unchecked"})),
        excluding_unverifiable=reading(frozenset({"refused", "unchecked", "unverifiable"})),
        wording=PREDICTION_LAYER_WORDING,
    )


def _standing(outcome: RecordOutcome, verdicts: Mapping[str, VerdictLike]) -> Standing:
    verdict = verdicts.get(outcome.record_id)
    if verdict is None:
        return "unchecked"
    named = _VERDICTS.get(verdict.verdict)
    if named is None:
        raise SurvivorForwardError(
            f"{outcome.record_id}'s late-record verdict is {verdict.verdict!r}, none of "
            f"{sorted(_VERDICTS)}"
        )
    return named


def _reading(
    by_session: Mapping[date, RecordOutcome],
    standing: Mapping[date, Standing],
    *,
    left_out: frozenset[str],
    sampled: SampledSessions,
    protocol: SurvivorForwardProtocol,
    metrics: tuple[tuple[ForwardMetric, Role], ...],
    label_as_of: datetime | None,
) -> LayerReading:
    included = [
        outcome
        for session, outcome in sorted(by_session.items())
        if outcome.mature and standing[session] not in left_out
    ]
    codes: Counter[str] = Counter()
    for outcome in included:
        codes.update(dict(outcome.unlabelled_by_code))
    return LayerReading(
        included=len(included),
        unlabelled_by_code=tuple(sorted(codes.items())),
        unlabelled_by_decile=_summed(outcome.unlabelled_by_decile for outcome in included),
        scope_by_decile=_summed(outcome.scope_by_decile for outcome in included),
        descriptive=_described(lambda quantity: [outcome.value(quantity) for outcome in included]),
        non_overlapping=_sample(
            by_session,
            standing,
            left_out=left_out,
            sampled=sampled,
            protocol=protocol,
            metrics=metrics,
            label_as_of=label_as_of,
        ),
        unlabelled_wording=UNLABELLED_WORDING,
    )


_WINDOW_KINDS: Final[tuple[str, ...]] = (
    "periods",
    "pending",
    "no_record",
    "refused",
    "unchecked",
    "unverifiable",
)
"""What a window position is in one reading (`NonOverlappingSample.in_window`), in order."""


def _sample(
    by_session: Mapping[date, RecordOutcome],
    standing: Mapping[date, Standing],
    *,
    left_out: frozenset[str],
    sampled: SampledSessions,
    protocol: SurvivorForwardProtocol,
    metrics: tuple[tuple[ForwardMetric, Role], ...],
    label_as_of: datetime | None,
) -> NonOverlappingSample:
    size = protocol.window_positions(sampled.horizon)
    gaps: list[tuple[date, GapReason]] = []
    pending: list[date] = []
    periods: list[SampledPeriod] = []
    tested: list[SampledPeriod] = []
    in_window: Counter[str] = Counter()
    matured = 0
    unverifiable = 0
    for position, (session, exit_day) in enumerate(
        zip(sampled.sessions, sampled.exits, strict=True)
    ):
        outcome = by_session.get(session)
        period: SampledPeriod | None = None
        kind: str
        if outcome is None:
            kind = "no_record"
            gaps.append((session, "no_record"))
            ripe = _exit_published(exit_day, label_as_of=label_as_of, through=sampled.through)
        else:
            ripe = outcome.mature
            state = standing[session]
            if state in left_out:
                kind = _GAP_REASONS[state]
                gaps.append((session, _GAP_REASONS[state]))
            elif not outcome.mature:
                kind = "pending"
                pending.append(session)
            else:
                kind = "periods"
                period = SampledPeriod(
                    session=session,
                    record_id=outcome.record_id,
                    values=tuple(
                        (quantity, outcome.value(quantity)) for quantity in DESCRIBED_QUANTITIES
                    ),
                )
                periods.append(period)
        if position >= size:
            continue
        in_window[kind] += 1
        if ripe:
            matured += 1
        if period is not None:
            tested.append(period)
        if outcome is not None and standing[session] == "unverifiable":
            unverifiable += 1
    satisfied = matured == size
    tests: tuple[MetricTest, ...] = ()
    if satisfied:
        tests = tuple(_tested(metric, role, tested, protocol) for metric, role in metrics)
        status = _tested_status(in_window, size=size, unverifiable=unverifiable)
    else:
        status = f"not tested: {matured} of {size} pre-registered periods matured"
    return NonOverlappingSample(
        anchor_ordinal=sampled.anchor_ordinal,
        horizon=sampled.horizon,
        sampled=sampled.sessions,
        gaps=tuple(gaps),
        pending=tuple(pending),
        periods=tuple(periods),
        window_positions=size,
        window_span_sessions=protocol.window_span_sessions,
        window_sessions=sampled.sessions[:size],
        in_window=tuple((kind, in_window[kind]) for kind in _WINDOW_KINDS),
        tested_periods=tuple(tested),
        matured=matured,
        window_satisfied=satisfied,
        means=_described(lambda quantity: [period.value(quantity) for period in periods]),
        tests=tests,
        status=status,
    )


def _exit_published(exit_day: date | None, *, label_as_of: datetime | None, through: date) -> bool:
    """Whether a position with no record has matured (module docstring): its exit's 16:30 at
    or before the outcomes' `label_as_of`; with no outcome, its exit on or before `through`."""
    if exit_day is None:
        return False
    if label_as_of is None:
        return exit_day <= through
    return label_as_of >= session_publication_instant(exit_day)


def _tested_status(counts: Mapping[str, int], *, size: int, unverifiable: int) -> str:
    """The status of a tested window -- what it lost, said alike in both readings: `n` periods of
    `K_h` positions, the gaps by reason, and the UNVERIFIABLE records the sensitivity excludes."""
    unchecked = counts["unchecked"]
    return (
        f"tested on {counts['periods']} of {size} positions ({counts['no_record']} no record, "
        f"{counts['refused']} refused, "
        + (f"{unchecked} unchecked, " if unchecked else "")
        + f"{unverifiable} unverifiable excluded in the sensitivity)"
    )


def _tested(
    metric: ForwardMetric,
    role: Role,
    periods: Sequence[SampledPeriod],
    protocol: SurvivorForwardProtocol,
) -> MetricTest:
    values = tuple(value for period in periods if (value := period.value(metric)) is not None)
    if len(values) < MINIMUM_INTERVAL_SAMPLE_SIZE:
        return MetricTest(
            metric=metric,
            role=role,
            values=len(values),
            mean=statistics.fmean(values) if values else None,
            test=None,
            one_sided_p_value=None,
            absence=(
                f"{len(values)} of the {len(periods)} periods carry a {metric}; a sign-flip test "
                f"needs at least {MINIMUM_INTERVAL_SAMPLE_SIZE}"
            ),
        )
    test = sign_flip_test(
        values, bootstrap_samples=protocol.bootstrap_samples, random_seed=protocol.random_seed
    )
    mean = statistics.fmean(values)
    return MetricTest(
        metric=metric,
        role=role,
        values=len(values),
        mean=mean,
        test=test,
        one_sided_p_value=test.p_value / 2 if mean > 0 else 1 - test.p_value / 2,
        absence=None,
    )


def _described(
    values_of: Callable[[DescribedQuantity], Sequence[float | None]],
) -> tuple[DescriptiveMean, ...]:
    described: list[DescriptiveMean] = []
    for quantity in DESCRIBED_QUANTITIES:
        present = [value for value in values_of(quantity) if value is not None]
        described.append(
            DescriptiveMean(
                quantity=quantity,
                n=len(present),
                mean=statistics.fmean(present) if present else None,
                wording=DESCRIPTIVE_WORDING,
            )
        )
    return tuple(described)


def _summed(rows: Iterable[Sequence[int]]) -> tuple[int, ...]:
    totals = [0] * _DECILES
    for row in rows:
        for decile, count in enumerate(row):
            totals[decile] += count
    return tuple(totals)


# --- the report's document (task E4) -------------------------------------------------------------

SURVIVOR_FORWARD_REPORT_KIND: Final = "survivor_forward_report/v1"
NO_VERDICT_WORDING: Final = "no verdict before the pre-registered window"
_POINT_ESTIMATE: Final = "a point estimate with no standard error"
_SIGN_FLIP: Final = "one-sided sign-flip randomization (outcome_statistics.sign_flip_test)"
_BENJAMINI_YEKUTIELI: Final = (
    "Benjamini-Yekutieli, arbitrary dependence (backtest.multiple_testing)"
)
_DEPENDENCE: Final = "arbitrary"
_HEADLINE_BASIS: Final = (
    "the headline counts verified and UNVERIFIABLE records (P6's convention), the sensitivity "
    "excludes the UNVERIFIABLE ones, and refused records are in neither: they are excluded and "
    "listed"
)
UNVERIFIABLE_MEANING: Final = (
    "the record was filed on time, at its evening's instant, and stands in the append-only "
    "store; an input it read was restated after that instant -- typically a name-change "
    "announcement dated on or before the session but published after the evening's read (T3: "
    "201 of the 371 ST entries of 2017-2021 were announced the day before they took effect) -- "
    "so it cannot be scored again equal and what it read can no longer be re-established; it is "
    "counted in the headline and excluded from the sensitivity reading, not discarded, and it "
    "says neither that the record carried what arrived after its instant nor that it did not"
)
"""What an UNVERIFIABLE record means (I8), in the document and under the records that need it."""
OPPOSITE_SIGN_CLAUSE: Final = (
    "the mean is below zero: its sign is opposite to the registered direction, in which greater "
    "than zero supports the screen"
)
"""Said on a tested metric's line when its mean is below zero (M10): the one-sided p is for the
registered direction, "mean > 0", which the sample's sign is against."""
_FAMILY_BASIS: Final = (
    "the secondary metrics at the primary horizon and the primary metric at each secondary "
    "horizon; corrected once the pre-registered window is satisfied, over the one-sided p-values "
    "that exist, a secondary without one still counted in the family; a secondary result never "
    "changes the candidate wording"
)
_SCOPES: Final[tuple[str, ...]] = (
    "not_bound",
    "no_provenance",
    "recompute_differs",
    "recompute_failed",
)
"""The late-record check's scopes (`strategy_survivor_check.VerdictScope`), counted by name."""
_BENCHMARKS: Final[tuple[DescribedQuantity, ...]] = ("investable_ew", "csi500")


class CheckedVerdict(VerdictLike, Protocol):
    """What the document reads of a late-record check's verdict (`SurvivorRecordVerdict`)."""

    @property
    def record_id(self) -> str: ...

    @property
    def session(self) -> date: ...

    @property
    def scope(self) -> str | None: ...

    @property
    def reason(self) -> str | None: ...

    def document(self) -> dict[str, object]: ...


def report_through(
    calendar: TradingCalendar,
    ordinals: SessionOrdinals,
    *,
    as_of: datetime,
    witnessed: Sequence[date],
) -> date:
    """The session the report's sample and its missing sessions run through (module docstring):
    the newest whose registration cutoff is at or before `as_of`, or the newest `witnessed`
    session when that is later; the epoch when no session after it has closed."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SurvivorForwardError(f"the report's as_of {as_of.isoformat()} carries no timezone")
    reported = as_of.astimezone(SHANGHAI).date()
    closed = ordinals.epoch
    for session in reversed(calendar.trading_days):
        if session > reported:
            continue
        if session <= ordinals.epoch:
            break
        try:
            cutoff = registration_cutoff(calendar, session)
        except TradingCalendarError:
            continue
        if cutoff <= as_of:
            closed = session
            break
    return max(closed, *witnessed) if witnessed else closed


@dataclass(frozen=True, slots=True, kw_only=True)
class SampledBenchmark:
    """One of the headline's sampled periods at the primary horizon, as the paper record's
    relative return reads it (`strategy_survivor_paper_report.BenchmarkWindow`): the record's
    entry and exit closes, its investable equal weight and the CSI 500 over the same closes."""

    record_id: str
    entry: date
    exit: date
    investable_ew: float
    csi500: float


def sampled_benchmarks(
    layer: PredictionLayer, outcomes: Sequence[RecordOutcome]
) -> tuple[SampledBenchmark, ...]:
    """The headline's sampled periods of `layer` with both benchmarks, each from its outcome in
    `outcomes`; a period missing either is left out (the document names it)."""
    by_id = {outcome.record_id: outcome for outcome in outcomes}
    picked: list[SampledBenchmark] = []
    for period in layer.headline.non_overlapping.periods:
        outcome = by_id.get(period.record_id)
        if outcome is None or outcome.horizon != layer.horizon:
            raise SurvivorForwardError(
                f"{period.record_id} is a sampled period of the h={layer.horizon} layer and no "
                "outcome of it at that horizon was handed in"
            )
        entry, exit_day = outcome.entry_day, outcome.exit_day
        ew, csi = outcome.investable_ew, outcome.csi500
        if entry is None or exit_day is None or ew is None or csi is None:
            continue
        picked.append(
            SampledBenchmark(
                record_id=outcome.record_id,
                entry=entry,
                exit=exit_day,
                investable_ew=ew,
                csi500=csi,
            )
        )
    return tuple(picked)


def paper_refusal(reason: str) -> dict[str, Any]:
    """The paper record's two sections when it was refused: the refusal, said in both."""
    return {
        "paper_ledger": {"wording": SURVIVOR_WORDING, "refused": reason},
        "relative": {"wording": SURVIVOR_WORDING, "refused": reason},
    }


def survivor_forward_document(
    *,
    as_of: datetime,
    config_id: str,
    registration_sha256: str,
    protocol: SurvivorForwardProtocol,
    ordinals: SessionOrdinals,
    through: date,
    witnessed: Sequence[date],
    verdicts: Mapping[str, CheckedVerdict],
    layers: Sequence[PredictionLayer],
    paper: Mapping[str, Any],
    power: SurvivorForwardPowerPlan | None,
    integrity: str,
) -> dict[str, Any]:
    """The `survivor_forward_report/v1` document (module docstring).

    `witnessed` are the sessions of the witnessed records, `verdicts` the late-record check's by
    record id, `layers` one `prediction_layer` per horizon of the protocol (the primary and every
    secondary), `paper` `paper_record`'s `{"paper_ledger", "relative"}` or `paper_refusal`'s,
    `power` the registration's `read_forward_power` (`None`: it carries none, and the `power`
    section says so), and `integrity` the threat model every forward report states. JSON-safe,
    with no set iterated unsorted: the same inputs give the same document."""
    horizons = (protocol.primary_horizon, *protocol.secondary_horizons)
    by_horizon = {layer.horizon: layer for layer in layers}
    if len(by_horizon) != len(layers) or sorted(by_horizon) != sorted(horizons):
        raise SurvivorForwardError(
            f"the document reads one layer per horizon of the protocol ({list(horizons)}), and "
            f"was handed the horizons {[layer.horizon for layer in layers]}"
        )
    for section in ("paper_ledger", "relative"):
        if not isinstance(paper.get(section), Mapping):
            raise SurvivorForwardError(
                f"the paper record hands in no {section} section: it is paper_record's two "
                "sections or paper_refusal's"
            )
    primary = by_horizon[protocol.primary_horizon]
    analysis = primary.headline.non_overlapping
    sessions = tuple(sorted(witnessed))
    document: dict[str, Any] = {
        "kind": SURVIVOR_FORWARD_REPORT_KIND,
        "as_of": as_of.isoformat(),
        "config_id": config_id,
        "registration_sha256": registration_sha256,
        "family": SURVIVOR_FAMILY,
        "protocol": {
            "wording": SURVIVOR_WORDING,
            **protocol.model_dump(mode="json"),
            "periods_matured": analysis.matured,
            "window_satisfied": analysis.window_satisfied,
        },
        "records": _records_section(
            sessions, verdicts, ordinals=ordinals, through=through, primary=primary
        ),
        "prediction_layer": {
            "wording": SURVIVOR_WORDING,
            "forecast_wording": PREDICTION_LAYER_WORDING,
            **{
                f"h{horizon}": _layer_section(by_horizon[horizon], analysis=analysis)
                for horizon in horizons
            },
            "secondary_family": _secondary_family(by_horizon, protocol=protocol, analysis=analysis),
        },
        "power": _power_section(power, protocol=protocol, by_horizon=by_horizon, analysis=analysis),
        "paper_ledger": dict(paper["paper_ledger"]),
        "relative": _relative_section(paper["relative"], primary),
        "cannot_be_evaluated_yet": _cannot_be_evaluated_yet(
            sessions, primary=primary, protocol=protocol
        ),
        "wording": [SURVIVOR_WORDING, PREDICTION_LAYER_WORDING, NO_VERDICT_WORDING],
        "integrity": integrity,
    }
    return document


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _records_section(
    sessions: Sequence[date],
    verdicts: Mapping[str, CheckedVerdict],
    *,
    ordinals: SessionOrdinals,
    through: date,
    primary: PredictionLayer,
) -> dict[str, Any]:
    checked = sorted(verdicts.values(), key=lambda item: (item.session, item.record_id))
    counts = Counter(item.verdict for item in checked)
    scopes = Counter(item.scope for item in checked if item.scope is not None)
    sampled = set(primary.headline.non_overlapping.window_sessions)
    held = set(sessions)
    try:
        last = ordinals.index_of(through)
    except SessionOrdinalError as error:
        raise SurvivorForwardError(
            f"the report runs through {through.isoformat()}, which has no session ordinal: {error}"
        ) from error
    missing = [
        session.isoformat()
        for session in (ordinals.day_of(ordinal) for ordinal in range(1, last + 1))
        if session not in held
    ]
    return {
        "wording": SURVIVOR_WORDING,
        "witnessed": len(sessions),
        "first_session": _day(sessions[0]) if sessions else None,
        "newest_session": _day(sessions[-1]) if sessions else None,
        "through": through.isoformat(),
        "missing_sessions": missing,
        "checked": len(checked),
        "by_verdict": {
            "verified": counts["verified"],
            "unverifiable": counts["unverifiable"],
            "refused": counts["refused"],
            "unchecked": dict(primary.excluded).get("unchecked", 0),
        },
        "by_scope": {scope: scopes.get(scope, 0) for scope in sorted({*_SCOPES, *scopes})},
        "unverifiable": [item.document() for item in checked if item.verdict == "unverifiable"],
        "unverifiable_meaning": UNVERIFIABLE_MEANING,
        "refused": [item.document() for item in checked if item.verdict == "refused"],
        "shares": {
            "overall": _shares(checked),
            "in_sample": _shares([item for item in checked if item.session in sampled]),
            "sample": (
                f"the h={primary.horizon} pre-registered window's positions that carry a record"
            ),
        },
        "headline_basis": _HEADLINE_BASIS,
    }


def _shares(items: Sequence[CheckedVerdict]) -> dict[str, Any]:
    checked = len(items)
    unverifiable = sum(1 for item in items if item.verdict == "unverifiable")
    refused = sum(1 for item in items if item.verdict == "refused")
    return {
        "checked": checked,
        "unverifiable": unverifiable,
        "refused": refused,
        "unverifiable_share": unverifiable / checked if checked else None,
        "refused_share": refused / checked if checked else None,
    }


def _layer_section(layer: PredictionLayer, *, analysis: NonOverlappingSample) -> dict[str, Any]:
    excluded = dict(layer.excluded)
    return {
        "wording": SURVIVOR_WORDING,
        "forecast_wording": PREDICTION_LAYER_WORDING,
        "horizon": layer.horizon,
        "role": layer.role,
        "label_as_of": None if layer.label_as_of is None else layer.label_as_of.isoformat(),
        "witnessed": layer.witnessed,
        "mature": layer.mature,
        "immature": layer.immature,
        "excluded": {
            "refused": excluded.get("refused", 0),
            "unchecked": excluded.get("unchecked", 0),
        },
        "unverifiable": layer.unverifiable,
        **_reading_section(layer.headline, analysis=analysis),
        "excluding_unverifiable": _reading_section(layer.excluding_unverifiable, analysis=analysis),
    }


def _means(means: Sequence[DescriptiveMean]) -> dict[str, Any]:
    return {
        "wording": DESCRIPTIVE_WORDING,
        "estimate": _POINT_ESTIMATE,
        "means": {mean.quantity: {"n": mean.n, "mean": mean.mean} for mean in means},
    }


def _reading_section(reading: LayerReading, *, analysis: NonOverlappingSample) -> dict[str, Any]:
    sample = reading.non_overlapping
    in_window = dict(sample.in_window)
    refused = in_window["refused"]
    test: list[dict[str, Any]] | None = None
    if sample.window_satisfied and analysis.window_satisfied:
        test = [_test(item) for item in sample.tests]
        status = (
            sample.status if not refused else f"compromised ({refused} refused): {sample.status}"
        )
    elif sample.window_satisfied:
        status = (
            "not tested: a secondary is tested in the one analysis at the primary window "
            f"({analysis.matured} of {analysis.window_positions} primary periods matured)"
        )
    else:
        status = sample.status
    return {
        "included": reading.included,
        "scope_by_decile": list(reading.scope_by_decile),
        "unlabelled_by_decile": list(reading.unlabelled_by_decile),
        "unlabelled_by_code": dict(reading.unlabelled_by_code),
        "unlabelled_wording": reading.unlabelled_wording,
        "descriptive": _means(reading.descriptive),
        "non_overlapping": {
            "anchor_ordinal": sample.anchor_ordinal,
            "horizon": sample.horizon,
            "sampled": [session.isoformat() for session in sample.sampled],
            "gaps": [
                {"session": session.isoformat(), "reason": reason}
                for session, reason in sample.gaps
            ],
            "pending": [session.isoformat() for session in sample.pending],
            "values": [
                {
                    "session": period.session.isoformat(),
                    "record_id": period.record_id,
                    **dict(period.values),
                }
                for period in sample.periods
            ],
            "window_positions": sample.window_positions,
            "window_span_sessions": sample.window_span_sessions,
            "window_sessions": [session.isoformat() for session in sample.window_sessions],
            "in_window": in_window,
            "matured": sample.matured,
            "window_satisfied": sample.window_satisfied,
            "descriptive": _means(sample.means),
            "refused_in_sample": refused,
            "test": test,
            "status": status,
        },
    }


def _test(test: MetricTest) -> dict[str, Any]:
    drawn = test.test
    return {
        "metric": test.metric,
        "role": test.role,
        "values": test.values,
        "mean": test.mean,
        "procedure": _SIGN_FLIP,
        "null_hypothesis": None if drawn is None else drawn.null_hypothesis,
        "two_sided_p_value": None if drawn is None else drawn.p_value,
        "one_sided_p_value": test.one_sided_p_value,
        "exact": None if drawn is None else drawn.exact,
        "sign_patterns": None if drawn is None else drawn.sign_patterns,
        "random_seed": None if drawn is None else drawn.random_seed,
        "absence": test.absence,
    }


def _secondary_family(
    by_horizon: Mapping[int, PredictionLayer],
    *,
    protocol: SurvivorForwardProtocol,
    analysis: NonOverlappingSample,
) -> dict[str, Any]:
    members: list[tuple[str, int, ForwardMetric]] = [
        (f"h{protocol.primary_horizon}.{metric}", protocol.primary_horizon, metric)
        for metric in protocol.secondary_metrics
    ]
    members += [
        (f"h{horizon}.{protocol.primary_metric}", horizon, protocol.primary_metric)
        for horizon in protocol.secondary_horizons
    ]

    def corrected(pick: Callable[[PredictionLayer], LayerReading]) -> dict[str, Any]:
        if not members:
            return {
                "status": "not applied: the protocol registers no secondary hypothesis",
                "tests": [],
            }
        if not analysis.window_satisfied:
            return {
                "status": "not applied: before the pre-registered window there is no p-value to "
                f"correct ({analysis.matured} of {analysis.window_positions} primary periods "
                "matured)",
                "tests": [],
            }
        p_values: dict[str, float] = {}
        for name, horizon, metric in members:
            for item in pick(by_horizon[horizon]).non_overlapping.tests:
                tested = (item.metric, item.role) == (metric, "secondary")
                if tested and item.one_sided_p_value is not None:
                    p_values[name] = item.one_sided_p_value
        if not p_values:
            return {"status": "not applied: no secondary hypothesis carries a p-value", "tests": []}
        report = control_false_discovery_rate(
            MultipleTestingRequest(
                tests=tuple(
                    HypothesisTest(hypothesis_id=name, p_value=p_value, test=_SIGN_FLIP)
                    for name, p_value in p_values.items()
                ),
                family_size=len(members),
                false_discovery_rate=protocol.q,
                dependence=_DEPENDENCE,
            )
        )
        tests: list[dict[str, Any]] = []
        for name, _horizon, _metric in members:
            verdict = report.verdict_for(name)
            if verdict is not None:
                tests.append(
                    {
                        "hypothesis_id": name,
                        "one_sided_p_value": p_values[name],
                        "q_value": verdict.q_value,
                        "below_q": verdict.rejected,
                    }
                )
        withheld = len(members) - len(p_values)
        return {
            "status": f"applied over {len(p_values)} of {len(members)} secondary hypotheses"
            + (f"; {withheld} without a p-value count in the family" if withheld else ""),
            "tests": tests,
        }

    return {
        "wording": SURVIVOR_WORDING,
        "procedure": _BENJAMINI_YEKUTIELI,
        "dependence": _DEPENDENCE,
        "q": protocol.q,
        "family": [name for name, _horizon, _metric in members],
        "family_size": len(members),
        "basis": _FAMILY_BASIS,
        "headline": corrected(lambda layer: layer.headline),
        "excluding_unverifiable": corrected(lambda layer: layer.excluding_unverifiable),
    }


_GAP_KINDS: Final[tuple[str, ...]] = ("no_record", "refused", "unchecked", "unverifiable")
"""The kinds of `NonOverlappingSample.in_window` that are gaps: neither a period nor pending."""
_LAG1_NOT_CREDITED: Final = (
    "a negative assumed lag-1 autocorrelation would credit more independent periods than there "
    "are: it is not credited, and the independent-period equivalent is computed at 0"
)
_SECONDARY_LEVEL: Final = (
    "a lower bound under Benjamini-Yekutieli: q / (m * H_m) at q {q:g} and a declared family of "
    "{size}, the first step of the step-up"
)
_Answer = TypeVar("_Answer")


def _attempt(absences: list[str], what: str, compute: Callable[[], _Answer]) -> _Answer | None:
    """`compute()`, or `None` with `what` and the refusal named in `absences`: a question the
    design has no answer to (`PowerError`) is an absence the section states, never a traceback."""
    try:
        return compute()
    except PowerError as error:
        absences.append(f"{what}: {error}")
        return None


def _power_section(
    power: SurvivorForwardPowerPlan | None,
    *,
    protocol: SurvivorForwardProtocol,
    by_horizon: Mapping[int, PredictionLayer],
    analysis: NonOverlappingSample,
) -> dict[str, Any]:
    """The `power` section: the plan's power beside the sample counts, or the statement that none
    was registered (module docstring)."""
    section: dict[str, Any] = {
        "wording": SURVIVOR_WORDING,
        "status": POWER_NOT_PRE_REGISTERED,
        "basis": POWER_BASIS,
        "reading_rule": NOT_DETECTABLE_WORDING,
        "plan": None,
        "rows": [],
    }
    if power is None:
        return section
    section["status"] = f"pre-registered ({SURVIVOR_FORWARD_POWER_SCHEMA})"
    section["plan"] = power.model_dump(mode="json")
    section["rows"] = [
        _power_row(
            item, power, protocol=protocol, layer=by_horizon[item.horizon], analysis=analysis
        )
        for item in power.planned
    ]
    return section


def _power_row(
    item: PlannedMetric,
    plan: SurvivorForwardPowerPlan,
    *,
    protocol: SurvivorForwardProtocol,
    layer: PredictionLayer,
    analysis: NonOverlappingSample,
) -> dict[str, Any]:
    role = dict(protocol.tested_metrics(item.horizon)).get(item.metric)
    if role is None:
        raise SurvivorForwardError(
            f"the power plan states {item.metric} at horizon {item.horizon}, which is not tested "
            "there by the protocol"
        )
    absences: list[str] = []
    periods = protocol.window_positions(item.horizon)
    kind, sd, target = plan.critical_value, item.planning_sd, plan.target_power
    level: float | None = plan.test_level
    level_basis = "the plan's test level"
    if role == "secondary":
        size = len(protocol.secondary_metrics) + len(protocol.secondary_horizons)
        level = _attempt(
            absences,
            "the secondary test level",
            lambda: by_first_step_level(q=protocol.q, family_size=size),
        )
        level_basis = _SECONDARY_LEVEL.format(q=protocol.q, size=size)

    def power_at(mean: float, count: float) -> float | None:
        stated = level
        if stated is None:
            return None
        return _attempt(
            absences,
            f"power at a true mean of {mean:g} over {count:g} periods",
            lambda: power_of_mean_test(
                mean=mean, sd=sd, periods=count, test_level=stated, kind=kind
            ),
        )

    def periods_to_reach(mean: float) -> int | None:
        stated = level
        if stated is None:
            return None
        return _attempt(
            absences,
            f"periods for a power of {target:g} at a true mean of {mean:g}",
            lambda: periods_for_power(mean=mean, power=target, sd=sd, test_level=stated, kind=kind),
        )

    smallest: float | None = None
    if level is not None:
        stated = level
        smallest = _attempt(
            absences,
            f"the smallest mean with a power of {target:g}",
            lambda: smallest_mean_at_power(
                power=target, sd=sd, periods=periods, test_level=stated, kind=kind
            ),
        )
    assumed = plan.assumed_lag1_autocorrelation
    used = max(0.0, assumed)
    equivalent = _attempt(
        absences,
        "the independent-period equivalent",
        lambda: autocorrelation_equivalent_periods(periods=periods, lag1=used),
    )
    tested = analysis.window_satisfied
    return {
        "horizon": item.horizon,
        "metric": item.metric,
        "role": role,
        "test_level": level,
        "test_level_basis": level_basis,
        "critical_value": kind,
        "planning_sd": sd,
        "sd_source": item.sd_source,
        "planned_periods": periods,
        "planned": [
            {"mean": mean, "power": power_at(mean, periods)} for mean in item.planning_means
        ],
        "smallest_mean_at_target": smallest,
        "periods_for_target": [
            {"mean": mean, "periods": periods_to_reach(mean)} for mean in item.planning_means
        ],
        "autocorrelation": {
            "assumed_lag1": assumed,
            "lag1_used": used,
            "lag1_note": _LAG1_NOT_CREDITED if assumed < 0 else None,
            "independent_period_equivalent": equivalent,
            "power_at_equivalent": [
                {
                    "mean": mean,
                    "power": None if equivalent is None else power_at(mean, equivalent),
                }
                for mean in item.planning_means
            ],
        },
        "headline": _window_power(
            layer.headline.non_overlapping,
            item=item,
            plan=plan,
            tested=tested,
            power_at=power_at,
        ),
        "excluding_unverifiable": _window_power(
            layer.excluding_unverifiable.non_overlapping,
            item=item,
            plan=plan,
            tested=tested,
            power_at=power_at,
        ),
        "absences": absences,
    }


def _window_power(
    sample: NonOverlappingSample,
    *,
    item: PlannedMetric,
    plan: SurvivorForwardPowerPlan,
    tested: bool,
    power_at: Callable[[float, float], float | None],
) -> dict[str, Any]:
    """One reading's window for a planned metric: the sample counts, the plan's power at the
    periods the window has, and the window's own sd and lag-1 autocorrelation as description.

    `tested` is whether the primary window is satisfied, as for the tests the document prints:
    only then is a metric's period count `MetricTest.values`; before, `n` is the most the window
    can still have."""
    in_window = dict(sample.in_window)
    gaps = sum(in_window[kind] for kind in _GAP_KINDS)
    usable = sample.window_positions - gaps
    result = (
        next((test for test in sample.tests if test.metric == item.metric), None)
        if tested and sample.window_satisfied
        else None
    )
    count = None if result is None else result.values
    n = usable if count is None else count
    values = (
        []
        if count is None
        else [
            value
            for period in sample.tested_periods
            if (value := period.value(item.metric)) is not None
        ]
    )
    return {
        "positions": sample.window_positions,
        "matured": sample.matured,
        "gaps": gaps,
        "usable_at_most": usable,
        "tested": count,
        "n": n,
        "n_basis": "usable_at_most" if count is None else "tested",
        "at_n": (
            [{"mean": mean, "power": power_at(mean, n)} for mean in item.planning_means]
            if n >= 2
            else None
        ),
        "minimum_periods": plan.minimum_periods,
        "below_minimum": n < plan.minimum_periods,
        "observed": {
            "wording": OBSERVED_WORDING,
            "sd": statistics.stdev(values) if len(values) >= 2 else None,
            "lag1_autocorrelation": _lag1_autocorrelation(values),
        },
    }


def _lag1_autocorrelation(values: Sequence[float]) -> float | None:
    """The lag-1 autocorrelation of a series of at least three values: the sum of the products of
    neighbouring deviations over the sum of squared deviations; absent for a constant series."""
    if len(values) < 3:
        return None
    centre = statistics.fmean(values)
    spread = math.fsum((value - centre) ** 2 for value in values)
    if spread == 0:
        return None
    steps = math.fsum((earlier - centre) * (later - centre) for earlier, later in pairwise(values))
    return steps / spread


def _relative_section(relative: Mapping[str, Any], primary: PredictionLayer) -> dict[str, Any]:
    if "refused" in relative:
        return dict(relative)
    left_out = [
        {
            "record_id": period.record_id,
            "session": period.session.isoformat(),
            "missing": [quantity for quantity in _BENCHMARKS if period.value(quantity) is None],
        }
        for period in primary.headline.non_overlapping.periods
        if any(period.value(quantity) is None for quantity in _BENCHMARKS)
    ]
    return {**relative, "windows_left_out": left_out}


def _cannot_be_evaluated_yet(
    sessions: Sequence[date], *, primary: PredictionLayer, protocol: SurvivorForwardProtocol
) -> dict[str, Any]:
    horizon = protocol.primary_horizon
    analysis = primary.headline.non_overlapping
    items: list[tuple[str, str]] = []
    if not sessions:
        items.append(
            (
                "no_survivor_record_yet",
                "no survivor record has been filed under this registration yet",
            )
        )
    if primary.mature == 0:
        items.append(
            (
                "first_outcome_not_matured",
                f"the first h={horizon} outcome matures about {horizon + 1} sessions after the "
                "first evening; no record has matured yet",
            )
        )
    if not analysis.window_satisfied:
        items.append(
            (
                "window_not_satisfied",
                f"the pre-registered window needs {analysis.window_positions} non-overlapping "
                f"{horizon}-session periods: {analysis.matured} of {analysis.window_positions} "
                "have matured",
            )
        )
    items += [
        (
            "g0_is_one_preregistered_read_of_2022_2023",
            "G-0 rests on one pre-registered, read-once reading of 2022-01-04..2023-12-29 "
            "against a stop rule written before it (V2-P7-024): a first out-of-sample reading, "
            "never confirmatory evidence",
        ),
        (
            "spent_window_2024_01_02_to_2026_09_24",
            "2024-01-02..2026-09-24 is spent: nothing from that window is evidence here",
        ),
        ("paper_equity_is_not_evidence", "paper equity is not evidence"),
        (
            "t8_expected_numbers_are_optimistic",
            "T8's expected numbers are about 0.9 pp a year optimistic (T9) and must be "
            "re-measured; no expected number is printed beside a forward result",
        ),
        ("ic_2021", "2021's IC was +0.037 (exploratory, not evidence)"),
    ]
    items += [
        (
            f"h{secondary}_ic_dispersion_unmeasured",
            f"the dispersion of the h={secondary} IC has never been measured",
        )
        for secondary in protocol.secondary_horizons
    ]
    items.append(("unlabelled_names_left_out", UNLABELLED_WORDING))
    items.append(("no_null_signal_control", "there is no null-signal control yet (V2-P7-002)"))
    return {
        "wording": SURVIVOR_WORDING,
        "items": [{"code": code, "statement": statement} for code, statement in items],
    }


# --- the report's lines ---------------------------------------------------------------------------


def survivor_forward_lines(document: Mapping[str, Any]) -> list[str]:
    """`document` as the lines the report prints: the records and their refusals first, then
    the prediction layer, the paper ledger, the relative return, what cannot be evaluated yet,
    the wording and the integrity statement. Read from the document alone, so a document read
    back from its file prints the same lines."""
    protocol = document["protocol"]
    records = document["records"]
    horizons = (protocol["primary_horizon"], *protocol["secondary_horizons"])
    lines = [
        f"as_of              {document['as_of']}",
        f"config_id          {document['config_id']}",
        f"report             {document['kind']}, family {document['family']}, registration "
        f"{document['registration_sha256'][:16]}",
        f"protocol           {protocol['schema_version']}: anchor ordinal "
        f"{protocol['anchor_ordinal']}, primary h={protocol['primary_horizon']} "
        f"{protocol['primary_metric']}, secondary horizons {protocol['secondary_horizons']}, "
        f"window {protocol['window_periods']} periods, {protocol['periods_matured']} matured",
    ]
    lines += _records_lines(records)
    layer = document["prediction_layer"]
    lines.append(f"prediction layer -- {layer['forecast_wording']}")
    for horizon in horizons:
        lines += _layer_lines(layer[f"h{horizon}"])
    lines += _family_lines(layer["secondary_family"])
    lines += _power_lines(document["power"], window_satisfied=protocol["window_satisfied"])
    lines += _ledger_lines(document["paper_ledger"])
    lines += _relative_lines(document["relative"])
    lines.append("cannot be evaluated yet:")
    lines += [f"  - {item['statement']}" for item in document["cannot_be_evaluated_yet"]["items"]]
    lines += [f"wording: {text}" for text in document["wording"]]
    lines.append(document["integrity"])
    return lines


def _number(value: object) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def _records_lines(records: Mapping[str, Any]) -> list[str]:
    counts = records["by_verdict"]
    span = (
        f"{records['first_session']}..{records['newest_session']}"
        if records["witnessed"]
        else "none"
    )
    lines = [
        f"records: {records['witnessed']} witnessed ({span}) through {records['through']}, "
        f"{len(records['missing_sessions'])} missing; checked {records['checked']}: verified "
        f"{counts['verified']}, UNVERIFIABLE {counts['unverifiable']}, refused "
        f"{counts['refused']}, unchecked {counts['unchecked']}"
    ]
    lines += [
        f"  REFUSED {item['record_id']} ({item['session']}, {item['scope']}): {item['reason']}"
        for item in records["refused"]
    ]
    lines += [
        f"  UNVERIFIABLE {item['record_id']} ({item['session']}, {item['scope']}): changes "
        f"{'; '.join(item['changes'])} -- counted in the headline, excluded from the sensitivity "
        "reading, not discarded"
        for item in records["unverifiable"]
    ]
    if records["unverifiable"]:
        lines.append(f"  UNVERIFIABLE means: {records['unverifiable_meaning']}")
    if records["missing_sessions"]:
        lines.append(f"  missing sessions: {', '.join(records['missing_sessions'])}")
    overall, sampled = records["shares"]["overall"], records["shares"]["in_sample"]
    lines.append(
        f"  shares: UNVERIFIABLE {overall['unverifiable']} of {overall['checked']} checked, "
        f"{sampled['unverifiable']} of {sampled['checked']} in the sample; refused "
        f"{overall['refused']} of {overall['checked']}, {sampled['refused']} of "
        f"{sampled['checked']} in the sample; {records['headline_basis']}"
    )
    return lines


def _layer_lines(layer: Mapping[str, Any]) -> list[str]:
    excluded = layer["excluded"]
    lines = [
        f"  h{layer['horizon']} ({layer['role']}): witnessed {layer['witnessed']}, mature "
        f"{layer['mature']}, immature {layer['immature']}; labels as of {layer['label_as_of']}; "
        f"excluded refused {excluded['refused']}, unchecked {excluded['unchecked']}; "
        f"UNVERIFIABLE {layer['unverifiable']}, in the headline and excluded in the sensitivity"
    ]
    lines += _reading_lines(layer, indent="    ")
    lines.append("    sensitivity, UNVERIFIABLE records excluded:")
    lines += _reading_lines(layer["excluding_unverifiable"], indent="      ")
    return lines


def _means_text(described: Mapping[str, Any]) -> str:
    means = described["means"]
    return ", ".join(
        f"{quantity} {_number(means[quantity]['mean'])} (n {means[quantity]['n']})"
        for quantity in DESCRIBED_QUANTITIES
        if quantity in means
    )


def _reading_lines(reading: Mapping[str, Any], *, indent: str) -> list[str]:
    sample = reading["non_overlapping"]
    described = reading["descriptive"]
    codes = reading["unlabelled_by_code"]
    lines = [
        f"{indent}{described['wording']} ({described['estimate']}), over {reading['included']} "
        f"mature daily records: {_means_text(described)}",
        f"{indent}unlabelled by code: "
        + (", ".join(f"{code} {count}" for code, count in sorted(codes.items())) or "none"),
        f"{indent}unlabelled names: {reading['unlabelled_wording']}",
        f"{indent}non-overlapping sample (anchor ordinal {sample['anchor_ordinal']}, every "
        f"{sample['horizon']} sessions): {len(sample['sampled'])} sampled, "
        f"{len(sample['gaps'])} gaps, {len(sample['pending'])} pending; the pre-registered "
        f"window is its first {sample['window_positions']} positions "
        f"({sample['window_span_sessions']} sessions), {sample['matured']} of them matured",
    ]
    if sample["gaps"]:
        lines.append(
            f"{indent}gaps: "
            + ", ".join(f"{gap['session']} ({gap['reason']})" for gap in sample["gaps"])
        )
    if sample["values"]:
        lines.append(
            f"{indent}{sample['descriptive']['wording']}, over the matured periods: "
            f"{_means_text(sample['descriptive'])}"
        )
    lines.append(f"{indent}{sample['status']}")
    for test in sample["test"] or ():
        head = f"{indent}{test['metric']} ({test['role']}): {test['values']} periods"
        if test["absence"] is not None:
            lines.append(f"{head}, not tested -- {test['absence']}")
            continue
        drawn = (
            "exact"
            if test["exact"]
            else f"{test['sign_patterns']} sign patterns, seed {test['random_seed']}"
        )
        opposite = test["mean"] is not None and test["mean"] < 0
        lines.append(
            f"{head}, mean {_number(test['mean'])}, one-sided p "
            f"{_number(test['one_sided_p_value'])} (two-sided {_number(test['two_sided_p_value'])}"
            f", {drawn})" + (f"; {OPPOSITE_SIGN_CLAUSE}" if opposite else "")
        )
    return lines


def _corrected_lines(tests: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"    {item['hypothesis_id']}: one-sided p {_number(item['one_sided_p_value'])}, "
        f"q-value {_number(item['q_value'])}, q-value <= q: {item['below_q']}"
        for item in tests
    ]


def _family_lines(family: Mapping[str, Any]) -> list[str]:
    headline, sensitivity = family["headline"], family["excluding_unverifiable"]
    return [
        f"  secondary family ({family['procedure']}, q {family['q']}; "
        f"{', '.join(family['family']) or 'none'}): {headline['status']}",
        *_corrected_lines(headline["tests"]),
        f"    sensitivity, UNVERIFIABLE records excluded: {sensitivity['status']}",
        *_corrected_lines(sensitivity["tests"]),
    ]


def _stated(value: object) -> str:
    """A figure of the power section as printed: six places, or `not stated` when the design
    has no answer for it (the row's `absences` say why)."""
    return "not stated" if value is None else f"{value:.6f}"


def _periods_stated(value: object) -> str:
    return "not stated" if value is None else str(value)


def _by_mean(
    entries: Sequence[Mapping[str, Any]], key: str, show: Callable[[object], str] = _stated
) -> str:
    return ", ".join(f"{entry['mean']:g} -> {show(entry[key])}" for entry in entries)


_READING_RULE_PENDING: Final = "reading rule: printed once the primary window is satisfied"
"""What the power block ends with before the pre-registered window: the reading rule speaks of a
one-sided p-value, and none is printed before the window."""


def _power_lines(power: Mapping[str, Any], *, window_satisfied: bool) -> list[str]:
    """The `power` section as lines: the plan, then each planned row with its windows, and last
    the reading rule once the primary window is satisfied (before it, the neutral line that says
    the rule waits); or the statement that no power was registered."""
    plan = power["plan"]
    if plan is None:
        return [f"power -- {power['status']}"]
    lines = [
        f"power ({plan['schema_version']}): test level {plan['test_level']:g}, target power "
        f"{plan['target_power']:g}, critical value {plan['critical_value']}"
        + (" (t on n - 1 degrees of freedom)" if plan["critical_value"] == "t" else "")
        + f", assumed lag-1 autocorrelation {plan['assumed_lag1_autocorrelation']:g}, minimum "
        f"periods {plan['minimum_periods']}",
        f"  {power['basis']}",
    ]
    for row in power["rows"]:
        lines += _power_row_lines(row)
    lines.append(f"  {power['reading_rule'] if window_satisfied else _READING_RULE_PENDING}")
    return lines


def _power_row_lines(row: Mapping[str, Any]) -> list[str]:
    serial = row["autocorrelation"]
    level = row["test_level"]
    lines = [
        f"  h{row['horizon']} {row['metric']} ({row['role']}): test level "
        f"{'not stated' if level is None else format(level, 'g')} ({row['test_level_basis']}); "
        f"planning sd {row['planning_sd']:g} ({row['sd_source']}); {row['planned_periods']} "
        "planned periods",
        f"    power at the planned periods, by true mean: {_by_mean(row['planned'], 'power')}; "
        f"smallest mean with the target power: {_stated(row['smallest_mean_at_target'])}",
        "    periods for the target power, by true mean: "
        + _by_mean(row["periods_for_target"], "periods", _periods_stated),
        f"    serial correlation: assumed lag-1 {serial['assumed_lag1']:g}, used "
        f"{serial['lag1_used']:g}; independent-period equivalent "
        f"{_stated(serial['independent_period_equivalent'])}; power there, by true mean: "
        + _by_mean(serial["power_at_equivalent"], "power"),
    ]
    if serial["lag1_note"] is not None:
        lines.append(f"      {serial['lag1_note']}")
    lines += _window_power_lines("headline", row["headline"])
    lines += _window_power_lines(
        "sensitivity, UNVERIFIABLE records excluded", row["excluding_unverifiable"]
    )
    lines += [f"    not stated: {absence}" for absence in row["absences"]]
    return lines


def _window_power_lines(label: str, window: Mapping[str, Any]) -> list[str]:
    at_n = window["at_n"]
    observed = window["observed"]
    below = "below" if window["below_minimum"] else "not below"
    return [
        f"    {label}: {window['positions']} positions, {window['matured']} matured, "
        f"gaps {window['gaps']}, at most {window['usable_at_most']} usable; n {window['n']} "
        f"({window['n_basis']}), {below} the minimum periods {window['minimum_periods']}; power "
        "at n, by true mean: "
        + ("not stated (fewer than 2 periods)" if at_n is None else _by_mean(at_n, "power")),
        f"      observed, {observed['wording']}: sd {_number(observed['sd'])}, lag-1 "
        f"autocorrelation {_number(observed['lag1_autocorrelation'])}",
    ]


_LEDGER_CAVEAT: Final = (
    "a holding's share count and mark are the paper ledger's, which after an adjustment (a bonus "
    "issue, a cash dividend) are not the account's"
)
"""What the equity line says beside the yuan (I7): the money is yuan; a holding's count and mark
after an adjustment are the ledger's units, not the account's."""


def _percent(value: object) -> str:
    """A ratio the document holds as text (`"-0.110165"`) as a percentage to two places, half
    away from zero (`"-11.02%"`); `n/a` when absent (I7)."""
    if value is None:
        return "n/a"
    percent = (Decimal(str(value)) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{abs(percent) if percent == 0 else percent}%"


def _annualised(value: object, *, annualisation: str, sessions: object, percent: bool) -> str:
    """An annualised figure as printed: its value a year and the sessions it is over, or, under
    the minimum, the record's own `too short to annualise` (I6)."""
    if annualisation.startswith("too short"):
        return annualisation
    if value is None:
        return "n/a"
    shown = _percent(value) if percent else str(value)
    return f"{shown} a year over {sessions} settled sessions"


def _ledger_lines(ledger: Mapping[str, Any]) -> list[str]:
    if "refused" in ledger:
        return [f"paper ledger -- refused: {ledger['refused']}; the prediction layer above stands"]
    coverage, equity = ledger["coverage"], ledger["equity"]
    holdings, turnover = ledger["holdings"], ledger["turnover"]
    costs, write_offs = ledger["costs"], ledger["write_offs"]
    built = turnover["initial_build"]
    written = (
        f"  write-offs: {write_offs['count']}, cost basis {write_offs['total_cost_basis']} yuan "
        f"in all (equity removed {write_offs['total_equity_removed']} yuan, at their last marks)"
        if write_offs["count"]
        else "  write-offs: none"
    )
    return [
        "paper ledger -- a record, never tested; ledger equity is not evidence",
        f"  coverage: {coverage['settled_sessions']} settled sessions "
        f"({coverage['first_settled']}..{coverage['last_settled']}): evenings "
        f"{coverage['evenings']}, settle-only {coverage['settle_only_days']}, caught up "
        f"{coverage['caught_up_sessions']}, refused days {len(coverage['refused_days'])}",
        f"  equity (yuan): opening {equity['opening']['equity']}, final "
        f"{equity['final_equity']}, mean {equity['mean_equity']}, minimum "
        f"{equity['minimum_equity']}; {_LEDGER_CAVEAT}",
        f"  holdings: mean held {holdings['mean_held']} of {holdings['holding_count']}, "
        f"sessions below N {holdings['sessions_below_n']}, mean cash share "
        f"{holdings['mean_cash_share']}",
        f"  turnover: one-sided {turnover['one_sided_turnover']} over {turnover['sessions']} "
        f"settled sessions, of which the one-off initial build {built['one_sided_turnover']} "
        f"({built['fills']} fills); annualised, the initial build left out: "
        + _annualised(
            turnover["annualised"],
            annualisation=turnover["annualisation"],
            sessions=turnover["sessions"],
            percent=False,
        ),
        f"  costs: {costs['total']['total']} yuan, of which the one-off initial build "
        f"{costs['total']['initial_build']} yuan; annualised share of mean equity, the initial "
        "build left out: "
        + _annualised(
            costs["total"]["annualised_share_of_mean_equity"],
            annualisation=costs["annualisation"],
            sessions=turnover["sessions"],
            percent=True,
        ),
        written,
        f"  {ledger['status']}",
    ]


def _chain_line(chain: Mapping[str, Any]) -> str:
    """One chain, its returns in percent (I7), and the points of its book return the write-offs
    inside it took, when there were any."""
    line = (
        f"  chain {chain['entry']}..{chain['exit']} ({chain['windows']} windows): book "
        f"{_percent(chain['book'])}, investable EW {_percent(chain['investable_ew'])}, CSI 500 "
        f"{_percent(chain['csi500'])}"
    )
    written = chain.get("write_offs") or {}
    if not written.get("count"):
        return line
    points = _percent(written["share_of_entry_equity"]).removesuffix("%")
    return (
        f"{line}; the write-offs inside it took {points} points of the book's return "
        f"({written['count']} written off, {written['equity_removed']} yuan removed)"
    )


def _relative_lines(relative: Mapping[str, Any]) -> list[str]:
    if "refused" in relative:
        return [f"relative return -- refused: {relative['refused']}"]
    lines = [
        f"relative return -- {relative['label']}",
        f"  windows: {relative['windows_computed']} computed of {relative['windows_given']} "
        f"given, {len(relative['chains'])} chains, {len(relative['gaps'])} gaps; "
        f"{relative['status']}",
    ]
    lines += [_chain_line(chain) for chain in relative["chains"]]
    lines += [f"  gap: {gap['reason']}" for gap in relative["gaps"]]
    lines += [
        f"  left out: {item['record_id']} ({item['session']}), no {', '.join(item['missing'])}"
        for item in relative.get("windows_left_out", ())
    ]
    return lines
