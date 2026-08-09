"""Daily bars and daily valuations (`V2-P1-007`) -- the panel every return is computed on.

## `pre_close` is already restated, and that is the whole point of this module

Tushare's `daily` publishes `pre_close`, and it is **not** the previous session's `close`: it
is the previous close restated for whatever corporate action took effect that morning. Measured
on `000001.SZ` across its 2026-06-12 ex-dividend date:

    20260611  close=11.30  pre_close=11.32  pct_chg=-0.1767   adj_factor=134.5794
    20260612  close=11.24  pre_close=10.94  pct_chg= 2.7422   adj_factor=139.008

`pre_close` on the 12th is 10.94 while the 11th closed at 11.30. So there are exactly **two**
correct ways to compute that session's return and one wrong one that looks correct 99% of the
time:

| path | 2026-06-12 |
|---|---|
| `close / pre_close - 1`, which is Tushare's own `pct_chg` | **+2.742230%** |
| `(close*f) / (prev_close*f_prev) - 1`, `V2-P1-006`'s factor | **+2.742251%** |
| `close / prev_close - 1` | **-0.530973%** -- wrong, and the *sign* is wrong |

The two correct paths agree to 2.1e-7 here, and the residue is arithmetic rather than
disagreement: the exact restated previous close is 11.30 * 134.5794 / 139.008 = 10.940003, and
`pre_close` is published to two decimals.

`session_returns` computes all three and **refuses** when the two correct ones disagree by more
than the published precision of the rows they were computed from (`pre_close_tolerance`, below).
That is the cross-validation `V2-P1-007` owes
`V2-P1-006`: it exercises `daily` and `adj_factor` against each other on live data, so a
mis-wired factor history, an off-by-one previous session, or a naive close-to-close is caught
where it happens instead of showing up as a plausible number in a backtest.

### Why the tolerance is stated in `pre_close` space, and why it is not a constant

An absolute tolerance on the *return* is wrong by construction: the same half-fen of rounding
on `pre_close` is 4.6e-4 of relative error on an 11-yuan stock and 3.3e-3 on a 1.50-yuan one, so
one number cannot serve both ends of the market. Comparing the two *implied previous closes*
instead lets the tolerance be built out of the publication precision of the inputs rather than
chosen. There are **two** such inputs, and the first version of this module carried only one of
them:

- `pre_close` is published to two decimals, so its own half-tick is 0.005 and a full tick 0.01.
  That term does not scale with anything.
- `adj_factor` is published to **four** decimals, and it enters through
  `implied = prev_close * f_prev / f`. A tick there is worth `implied * 1e-4 / f` of price, so
  **it scales with the price**. On a 450-yuan stock with `f ~ 1` a single tick in the last
  published digit of either factor moves the implied previous close by about 0.045 -- four and a
  half times a flat 0.01 tolerance, with nothing wrong anywhere.

So the tolerance is `MAX_PRE_CLOSE_DISAGREEMENT + implied * ADJ_FACTOR_PUBLICATION_TICK *
(1/f_prev + 1/f)`; see `pre_close_tolerance`.

The flat 0.01 was calibrated on three session pairs (2024-06-27/28, 2025-06-30/07-01,
2026-06-11/12) whose worst residue was 0.0081, and the adjacent pairs it did not sample break
it outright. Measured over the seven pairs 2024-06-21..28, 2025-06-30/07-01 and 2026-06-11/12
(37,602 rows with a bar on both days and a factor on both days):

    20240621->24   5342 pairs    0 over 0.01   worst 0.0039
    20240624->25   5338          201           worst 0.1770    <- 3.8% of the market
    20240625->26   5338          214           worst 0.1759
    20240626->27   5338            0           worst 0.0012
    20240627->28   5336            0           worst 0.0059    <- one of the three sampled
    20250630->0701 5402            0           worst 0.0081    <- one of the three sampled
    20260611->12   5508            0           worst 0.0030    <- one of the three sampled

103 of the 105 worst rows had **no corporate action at all** (`pre_close` equals the previous
close to the fen); what moved was the fourth published digit of the factor, by 3 to 8 units, on
a stock priced between 90 and 1,500 yuan. `600519.SH` on 2024-06-26 is the plain case: `f` goes
8.0205 -> 8.02 on an ordinary session and a 1,486.65-yuan close turns that into 0.0927.

### What the wider tolerance costs, measured

The point of the guard is to catch a factor series that is missing a step. Restricting to the
475 rows of those seven pairs that carry a real corporate action (`|prev_close - pre_close| >
0.005`) and deleting the step from each -- carrying `f_prev` across the ex-rights day, which is
exactly what a factor partition with a session hole answers:

    tolerance                 refused on honest rows      missing step detected
    flat 0.01                 415 / 37,602 = 1.10%        468 / 475 = 98.53%
    0.01 + implied*1e-4*(...) 105 / 37,602 = 0.28%        462 / 475 = 97.26%

The false-refusal rate falls by a factor of four for 1.3 points of detection. The 105 that
still refuse are the factor-restatement rows above: they are a real disagreement between the
two datasets about a session, so refusing them is the fail-closed answer even though the
resulting *return* difference is ~4e-4. On the same 37,602 rows the naive close-to-close path
is wrong by up to **118.30** (`300394.SZ`), three orders of magnitude away from either
population, which is what makes the separation robust to the exact constant.

The `1/f` terms are bounded in practice because Tushare's `adj_factor` is a cumulative series
normalised so its earliest session is 1.0: across the 75,204 factor cells of those seven pairs
the minimum is exactly 1.0 and the maximum 10,055.6401, so the tolerance never exceeds
`0.01 + 2e-4 * implied`. It is not bounded by contract, and a factor below 1.0 would widen it.

## The join with `adj_factor`, and the 49 names that separate them

On 2024-06-28 `adj_factor` served 5,387 rows and `daily` 5,338, with `daily` a strict subset.
The 49-name difference is **not** one population, and calling it "the suspended names" (as this
task's brief did) merges two of `KNOWN_ADJUSTMENT_LIMITATIONS`' entries into one:

- **26** were listed on that session and halted. All 26 appear in `suspend_d` for 2024-06-28,
  which served 28 rows in total. This is `suspension_is_invisible`.
- **23** had already been delisted (`delist_date <= 2024-06-28`) and still emit factor rows.
  `600069.SH` and `600247.SH`, named in `delisted_securities_carry_unstable_factors`, are both
  in this half.

`priced_cross_section` keeps them apart without needing `suspend_d` (`V2-P1-008`), because the
registry already does: it iterates `universe.listed_on(day)`, so the 23 delisted names are never
candidates, and the 26 halted ones land in `PricedCrossSection.unpriced` **by name**. Neither
is dropped silently, and neither is invented.

## Layering

Pure `datetime`/`dataclasses` plus four sibling `domain` modules -- `panel_batch` for the
reserved subject-column name, `adjustment` for the factor contract this cross-checks against,
and `stock_universe`/`trading_calendar` for the cross-section join. No provider, no store, no
clock; the same placement, and the same reason, as `domain/adjustment.py`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from math import isfinite
from typing import Final

from openalpha_cn.domain.adjustment import AdjustmentHistory, adjustment_factors_on
from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.stock_universe import StockUniverse
from openalpha_cn.domain.trading_calendar import TradingCalendar

DAILY_DATASET: Final[str] = "daily"
DAILY_BASIC_DATASET: Final[str] = "daily_basic"
"""The panel datasets (and partition directories) the two halves are stored under.

Declared here rather than in `providers/tushare.py` for the reason `ADJ_FACTOR_DATASET` is:
`panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

PRICE_DATE_COLUMN: Final[str] = "trade_date"
CLOSE_COLUMN: Final[str] = "close"
PRE_CLOSE_COLUMN: Final[str] = "pre_close"

DAILY_PRICE_COLUMNS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "pre_close")
"""The five columns that must be a finite positive `float` in every stored row.

Not a guess about the feed's shape, and the sample says which history it covers: **nineteen**
sessions spanning 2001-01-02 to 2026-08-07 (58,055 bars, the oldest three from before the
`daily_basic` endpoint has usable share counts at all) carried no null and no non-positive
value in any of the five. A null price is therefore a fault rather than a sparse cell, and
letting one through would surface as a `TypeError` inside a return several layers from the row
that caused it.

The width of that window is deliberate and is the correction this constant's neighbour
`DAILY_BASIC_NULLABLE_COLUMNS` had to make: a nullability claim sampled only on recent sessions
is a claim about recent sessions, and this panel is built to hold a decade.
"""

DAILY_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    *DAILY_PRICE_COLUMNS,
    "pct_chg",
    "vol",
    "amount",
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

`change` is the one response field deliberately not stored: it is `close - pre_close` to two
decimals and carries nothing `close` and `pre_close` do not. `pct_chg` *is* stored even though
it is likewise derivable, because it is the upstream's own statement of the session return and
therefore the third witness `session_returns` is checked against -- see this module's docstring.
"""

DAILY_PANEL_COLUMNS: Final[tuple[str, ...]] = (SUBJECT_COLUMN_NAME, *DAILY_DATA_COLUMNS)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back."""

DAILY_BASIC_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    CLOSE_COLUMN,
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
"""Every `daily_basic` response field except `ts_code`, which becomes the subject.

Nothing is dropped. `total_mv`/`circ_mv`/`turnover_rate` are what P3's neutralisation and P4's
leaderboards read, and the rest cost one column each in a projected store while a re-fetch of a
decade costs hours. `close` is kept even though `daily` already has it, because that duplication
is the free cross-check `close_disagreements` runs.
"""

DAILY_BASIC_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *DAILY_BASIC_DATA_COLUMNS,
)

DAILY_BASIC_NULLABLE_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "turnover_rate_f",
        "free_share",
    }
)
"""Valuation columns whose absence is data rather than a fault, and how that was measured.

## The eight ratios: measured on 2024-06-28 (5,338 rows)

`pe` null on 1,102, `pe_ttm` on 1,214, `dv_ttm` on 1,840, `dv_ratio` on 354, `pb` on 29,
`ps`/`ps_ttm` on 3, `volume_ratio` on 2. A loss-making company has no P/E and a non-payer has
no yield; refusing those would refuse a fifth of the market.

## `free_share` and `turnover_rate_f`: measured across 2001..2026, and this is a correction

The first version of this set was drawn from five sessions between 2023-01-03 and 2026-08-07
and put these two in the *complement* -- refused when null. That sample does not reach the
history the panel is built for, and eighteen sessions spanning 2001-01-02 to 2026-08-07 show
where it breaks. Null counts, `free_share` first:

    20010102  1,022 rows   8 / 8      20161010  2,704   0 / 0
    20030102  1,187        3 / 3      20170103  2,832   1 / 0
    20050104  1,344        4 / 4      20171009  3,180   7 / 0
    20080102  1,371        4 / 2      20171204  3,232   4 / 0
    20100104  1,632        2 / 0      20180102  3,252   1 / 0
    20120104  2,228        0 / 0      20200302..20260807 (7 sessions)  0 / 0

It is not a boundary effect either: `300290.SZ` has a null `free_share` on **74 consecutive
trading days**, 2017-09-20 through 2018-01-16. Refusing a null here does not refuse one row --
`fetch_panel` fails the whole cross section, so the session cannot be built, the year is then
missing a session the calendar reports open, and `write_daily_panel` refuses the year on both
datasets. Two whole years inside the ten-year window this panel exists for (2017 and 2018)
were unstorable.

## The complement, and the scope of that claim

`close`, `turnover_rate`, `total_share`, `float_share`, `total_mv` and `circ_mv` were populated
on **every** row of all eighteen sessions above -- 51,708 rows spanning 2001..2026. That is
what is measured; it is not a proof about every session the endpoint serves. A null in one of
them is refused because the fail-closed direction is the right one for these six specifically:
`total_mv` and `circ_mv` are P3 neutralisation inputs and a null one silently drops a name from
a regression, `close` is the cross-check against `daily`, and `total_share`/`float_share` are
what those caps are computed from. `free_share` and `turnover_rate_f` are neither: nothing in
P3 or P4 reads them today, so the cost of a null is a null cell rather than a dropped name.
"""

SESSION_CLOSE_TIME: Final[time] = time(15, 0)
"""When an A-share session's continuous auction ends, Asia/Shanghai. `event_time`."""

DAILY_AVAILABILITY_TIME: Final[time] = time(16, 30)
"""When a session's daily data is treated as knowable, Asia/Shanghai. `available_time`.

Declared here and imported by `providers/tushare.py::_daily_close_timeline` rather than
restated as a literal in each place. The read side derives which sessions a partition is
*required* to hold from this same instant (`panel_ingest.daily_requirement`), so the two
drifting apart would either manufacture a permanent false `date_gap` on every intraday read or
stop requiring the newest session. A comment saying "these must agree" is not a check;
`tests/contract/providers/test_tushare_daily.py` asserts the provider builds its clocks from
these two constants.
"""

MIN_SESSION_ROW_SHARE: Final[float] = 0.5
"""How thin one session's cross section may be relative to its partition's median, before the
write refuses it. See `panel_ingest._refuse_thin_price_sessions` for what it guards.

## Why a share of the partition's own median, and not a count

The market was 1,022 names on 2001-01-02 and 5,535 on 2026-08-07, so any absolute floor is
wrong at one end of the history. The per-session row counts are already in hand when the date
census runs, so this costs no request.

## Why the share is this low, which is the uncomfortable part

It is set below the thinnest real session found rather than at a level that would be
satisfying. Full-year censuses of the live endpoint, one request per session:

    2015   244 sessions   min 1,363 (07-09)   median 2,359   min/median 0.578
    2018   243 sessions   min 3,177 (02-08)   median 3,378   min/median 0.940
    2024   242 sessions   min 5,301 (04-30)   median 5,345   min/median 0.992

2015 is not an outlier to be argued away: 2015-07-09 sits in the week when a large part of the
A-share market suspended trading at once, and 1,363 rows were served against a year median of
2,359 -- 07-10 served 1,425 and 07-08 1,489. A floor at 0.9 would refuse a true partition of
that year, which is the failure this repository has already made twice by calibrating a bound on
a window that excluded the counterexample.

## What that leaves uncovered, plainly

A fetch that returned *most* of the market is invisible here. What is caught is the shape that
actually occurred in review -- a session that came back with a handful of names while its
siblings carried the market -- and the fully general answer is still `suspend_d` (`V2-P1-008`),
which is what distinguishes a halted security from an absent row. This is a floor, not a census.
"""

MAX_PRE_CLOSE_DISAGREEMENT: Final[float] = 0.01
"""The `pre_close` term of the tolerance: one published tick of a two-decimal price.

The whole tolerance is `pre_close_tolerance`, of which this is the part that does not scale.
"""

ADJ_FACTOR_PUBLICATION_TICK: Final[float] = 1e-4
"""The `adj_factor` term of the tolerance: one published tick of a four-decimal factor.

Carried into price space by `pre_close_tolerance`, because a tick of `f` is worth
`implied * 1e-4 / f` yuan of implied previous close and therefore grows with the price. One
full tick rather than the 5e-5 half-tick that pure round-to-nearest would give: the observed
residue is not only rounding but the upstream restating the fourth digit, and a factor of two
here costs 1.3 points of missing-step detection while removing three quarters of the false
refusals. Both numbers are measured in this module's docstring.
"""


class PriceDataError(ValueError):
    """Raised for any malformed daily row, or any malformed question about one.

    A `ValueError` subclass to match `domain/adjustment.py`'s `AdjustmentError` and
    `domain/trading_calendar.py`'s `TradingCalendarError`.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceLimitation:
    """One named boundary on what a stored price panel can be trusted to answer."""

    code: str
    detail: str


KNOWN_PRICE_LIMITATIONS: Final[tuple[PriceLimitation, ...]] = (
    PriceLimitation(
        code="pre_close_is_already_adjusted",
        detail=(
            "daily.pre_close is the previous session's close restated for that morning's "
            "corporate action, not the previous session's close. 000001.SZ closed at 11.30 on "
            "2026-06-11 and its 2026-06-12 pre_close is 10.94. Any code that reconstructs a "
            "return as close[t]/close[t-1] - 1 gets -0.530973% where the truth is +2.742230%, "
            "with the sign reversed, and is right on every other day of that month -- which is "
            "why the defect survives casual testing. On the market-wide 2026-06-11/12 pair, "
            "100 of 5,508 securities had a pre_close that differs from the previous close by "
            "more than one fen."
        ),
    ),
    PriceLimitation(
        code="daily_basic_omits_the_beijing_board_before_2024",
        detail=(
            "daily_basic is a subset of daily, never a superset, and the gap is one board. "
            "Measured: 2015-07-08 daily 1,489 / daily_basic 1,467 (22 missing), 2018-01-02 "
            "3,282 / 3,252 (30), 2020-03-02 3,843 / 3,783 (60), 2022-04-25 4,836 / 4,780 (56), "
            "and 2024-06-28 / 2026-08-07 with zero difference. Every missing code on every one "
            "of those sessions ends in .BJ. So a historical partition legitimately carries "
            "prices without market caps for Beijing-board names, and close_disagreements "
            "tolerates that direction while refusing the other."
        ),
    ),
    PriceLimitation(
        code="a_priced_security_can_be_absent_from_the_registry",
        detail=(
            "300114.SZ has a real daily bar on 2024-06-28 and is absent from stock_basic under "
            "every list_status probed -- 'L', 'D', 'P', 'L,D' and the bare request all return "
            "zero rows for it -- so StockUniverse.security() refuses it as an unknown code "
            "rather than reporting it delisted. It is the only such name on that session "
            "(5,338 bars) and there are none on 2025-08-01 or 2026-08-07. "
            "PricedCrossSection.unlisted_bars names these rather than keeping them (which "
            "would put a code in the cross section that is_listed cannot answer for) or "
            "dropping them (which would shrink the market with no signal). Related to but not "
            "the same as stock_universe's ts_code_is_not_a_permanent_security_identity: that "
            "entry is about two codes for one company, this one is about a traded code the "
            "registry has no row for at all."
        ),
    ),
    PriceLimitation(
        code="a_partial_cross_section_is_invisible_without_suspend_d",
        detail=(
            "Nothing in daily distinguishes 'this security was halted' from 'this fetch was "
            "short', because both are simply an absent row. A session whose fetch returned "
            "only some of the market otherwise stores a real, well-formed, complete-looking "
            "partition: every row parses, the date census carries that session, the year's "
            "subject set is complete because its other sessions supply every code, and "
            "readiness reports ready. priced_cross_section surfaces the population as "
            "PricedCrossSection.unpriced instead of hiding it, and the count is checkable "
            "against a known figure (26 halted names on 2024-06-28). "
            "panel_ingest._refuse_thin_price_sessions now takes the extreme end of this: a "
            "session holding under MIN_SESSION_ROW_SHARE of its partition's median cross "
            "section is refused at write time, which catches a session that came back with a "
            "handful of names. It is a floor and not a census -- it is set at 0.5 because "
            "2015-07-09 legitimately served 1,363 rows against that year's median of 2,359, "
            "so a fetch that returned most of the market is still invisible. The dataset that "
            "settles the general case is suspend_d (V2-P1-008)."
        ),
    ),
    PriceLimitation(
        code="silent_truncation_at_the_response_cap",
        detail=(
            "Both endpoints serve at most 6,000 rows per response and drop the oldest. "
            "Measured on 2026-08-08: a bare daily(ts_code=000001.SZ) and a bare "
            "daily_basic(ts_code=000001.SZ) each return exactly 6,000 rows with has_more=True. "
            "The whole-market cross section fits -- 5,338 on 2024-06-28, 5,535 on 2026-08-07 "
            "-- which is why the fetch granularity is one session of the market rather than "
            "one security's history, and providers/tushare.py::_check_response_completeness "
            "refuses a response at the cap rather than storing a truncated one."
        ),
    ),
)
"""Named boundaries on what a stored price panel answers, each measured on real data.

**Not an enumeration of every way the panel could be wrong.** These are the ones a live probe
of the endpoints could demonstrate on 2026-08-08/09.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyBar:
    """One security's price and turnover on one session.

    A plain carrier with no validation of its own, following `FactorObservation`'s precedent:
    a nominal type is not a boundary, so the rules live once, in
    `daily_bars_from_panel_rows`.
    """

    ts_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    pct_chg: float
    vol: float
    amount: float

    @property
    def published_return(self) -> float:
        """`close / pre_close - 1`: the session return, correct across ex-rights days.

        Equals Tushare's own `pct_chg / 100` to the four decimals it publishes. It is *not*
        exactly `round(published_return * 100, 4)` for every row -- 12 of the 5,338 bars on
        2024-06-28 differ in the last digit, because the exact quotient lands on a half-way
        point that Python's round-half-even and the upstream's rounding resolve differently
        (`600644.SH`: 6.49/6.40 - 1 is 1.40624999...%, published as 1.4063). So the agreement is
        pinned with a tolerance and not as an equality.
        """
        return self.close / self.pre_close - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyValuation:
    """One security's share counts, market caps, turnover and valuation ratios on one session.

    Ten of the seventeen fields are `None` when the upstream published none -- the eight ratios
    plus `free_share` and `turnover_rate_f`, which are sparse before 2019. See
    `DAILY_BASIC_NULLABLE_COLUMNS` for which, for the sessions that were measured, and for why
    the other six are refused instead.
    """

    ts_code: str
    trade_date: date
    close: float
    turnover_rate: float
    turnover_rate_f: float | None
    volume_ratio: float | None
    pe: float | None
    pe_ttm: float | None
    pb: float | None
    ps: float | None
    ps_ttm: float | None
    dv_ratio: float | None
    dv_ttm: float | None
    total_share: float
    float_share: float
    free_share: float | None
    total_mv: float
    circ_mv: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionReturns:
    """The three ways to compute one session's return, side by side.

    `unadjusted` is carried so the size of the error is visible when a reader compares them,
    never so it is used: on 2026-06-12 it is `-0.530973%` against a true `+2.742230%`. There is
    no method here that returns "the" return, because which of `published` and `adjusted` a
    caller wants depends on whether it is also adjusting prices, and both are correct.
    """

    ts_code: str
    day: date
    previous_day: date
    published: float
    """`close / pre_close - 1`. One row, no factor history needed."""
    adjusted: float
    """`(close*f) / (prev_close*f_prev) - 1`. Two rows and `V2-P1-006`'s step function."""
    unadjusted: float
    """`close / prev_close - 1`. **Wrong** on any ex-rights day. Reported, never used."""
    published_pre_close: float
    implied_pre_close: float
    """`prev_close * f_prev / f`: what `pre_close` must be if the factor series is right."""
    factor: float
    """`adj_factor` on `day`. Carried so `tolerance` can be recomputed from the record."""
    previous_factor: float
    """`adj_factor` on `previous_day`."""

    @property
    def disagreement(self) -> float:
        """How far the two correct paths are apart, in yuan of implied `pre_close`.

        Unsigned, and both signs occur on real mis-wirings rather than only one. A factor
        series whose step is **missing** on an ex-rights day implies a previous close that is
        too *high* (`+0.36` on `000001.SZ`'s 2026-06-12); the same off-by-one series carrying
        that step one session **late** implies one that is too *low* on the following session
        (`-0.358` on 2026-06-15). Dropping the `abs()` would keep the first and wave the second
        through, so both directions are exercised in `tests/unit/domain/test_daily_prices.py`.
        """
        return abs(self.implied_pre_close - self.published_pre_close)

    @property
    def tolerance(self) -> float:
        """The disagreement this pair of rows is allowed to carry; see `pre_close_tolerance`."""
        return pre_close_tolerance(
            self.implied_pre_close, factor=self.factor, previous_factor=self.previous_factor
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PricedSecurity:
    """One listed security's bar on a session, beside the adjustment factor in force."""

    ts_code: str
    bar: DailyBar
    factor: float

    @property
    def adjusted_close(self) -> float:
        """后复权 close: the raw close on the factor's own scale (`PriceAdjustment.backward`)."""
        return self.bar.close * self.factor


@dataclass(frozen=True, slots=True, kw_only=True)
class PricedCrossSection:
    """One open session's listed market: what traded, what did not, and what does not belong.

    The three tuples partition the day's evidence and none of them is allowed to be silent:

    - `priced` is every listed security that has both a bar and a factor.
    - `unpriced` is every listed security with a factor and **no bar**. On a real session these
      are the halted names -- 26 of them on 2024-06-28 -- and this contract cannot tell a halt
      from a short fetch; see `KNOWN_PRICE_LIMITATIONS`.
    - `unlisted_bars` is every security with a bar that the registry does not report listed.
      Measured: exactly one such name (`300114.SZ`) on 2024-06-28, absent from `stock_basic`
      entirely.
    """

    day: date
    exchange: str
    priced: tuple[PricedSecurity, ...]
    unpriced: tuple[str, ...]
    unlisted_bars: tuple[str, ...]

    @property
    def listed_count(self) -> int:
        """How many securities the registry reported listed on this session."""
        return len(self.priced) + len(self.unpriced)


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseDisagreement:
    """One security whose `daily` and `daily_basic` rows do not tell the same story.

    `bar_close` is `None` when `daily_basic` carried a security `daily` did not, which is the
    direction that has never been observed and is therefore a contradiction rather than sparse
    data. The other direction -- a bar with no valuation -- is real (see
    `daily_basic_omits_the_beijing_board_before_2024`) and is not reported here.
    """

    ts_code: str
    trade_date: date
    bar_close: float | None
    valuation_close: float


def daily_bars_from_panel_rows(rows: Iterable[Sequence[object]]) -> dict[str, DailyBar]:
    """Rebuild **one session's** bars from rows shaped like `DAILY_PANEL_COLUMNS`.

    One session, not many: the result is keyed by `ts_code`, which is the shape
    `priced_cross_section` joins against a single day's factors and a single day's registry
    membership. A mapping that quietly held two dates would adjust one session's prices with
    the other's factor, so two dates in one call is refused rather than grouped.

    A mutable `dict` rather than a read-only mapping, unlike
    `adjustment_histories_from_panel_rows`: a cross section is a working set that callers
    legitimately narrow (to an index's constituents, to a screen's survivors), and a factor
    corpus is not.
    """
    bars: dict[str, DailyBar] = {}
    session: date | None = None
    for index, row in enumerate(rows):
        if len(row) != len(DAILY_PANEL_COLUMNS):
            raise PriceDataError(
                f"row {index} has {len(row)} values, expected {len(DAILY_PANEL_COLUMNS)} "
                f"({', '.join(DAILY_PANEL_COLUMNS)})"
            )
        subject, day_text, open_, high, low, close, pre_close, pct_chg, vol, amount = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        trade_date = _parse_iso_date(day_text, index, PRICE_DATE_COLUMN)
        if session is None:
            session = trade_date
        elif session != trade_date:
            raise PriceDataError(
                f"row {index} is dated {trade_date.isoformat()} and this cross section already "
                f"carries more than one trade_date ({session.isoformat()}); one call is one "
                "session, because a factor and a registry membership are both per-day"
            )
        if ts_code in bars:
            raise PriceDataError(
                f"{ts_code} appears twice in one {DAILY_DATASET} cross section "
                f"({trade_date.isoformat()}); a session has one bar per security, so this is "
                "two fetches that were never reconciled"
            )
        bars[ts_code] = DailyBar(
            ts_code=ts_code,
            trade_date=trade_date,
            open=_stored_price(open_, index, "open"),
            high=_stored_price(high, index, "high"),
            low=_stored_price(low, index, "low"),
            close=_stored_price(close, index, CLOSE_COLUMN),
            pre_close=_stored_price(pre_close, index, PRE_CLOSE_COLUMN),
            pct_chg=_stored_number(pct_chg, index, "pct_chg"),
            vol=_stored_number(vol, index, "vol"),
            amount=_stored_number(amount, index, "amount"),
        )
    return bars


def daily_valuations_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> dict[str, DailyValuation]:
    """Rebuild one session's valuations from rows shaped like `DAILY_BASIC_PANEL_COLUMNS`.

    Same one-session rule, and the same reason, as `daily_bars_from_panel_rows`: a market cap
    is a fact about a day.
    """
    valuations: dict[str, DailyValuation] = {}
    session: date | None = None
    for index, row in enumerate(rows):
        if len(row) != len(DAILY_BASIC_PANEL_COLUMNS):
            raise PriceDataError(
                f"row {index} has {len(row)} values, expected "
                f"{len(DAILY_BASIC_PANEL_COLUMNS)} ({', '.join(DAILY_BASIC_PANEL_COLUMNS)})"
            )
        ts_code = _require_stored_text(row[0], index, SUBJECT_COLUMN_NAME)
        trade_date = _parse_iso_date(row[1], index, PRICE_DATE_COLUMN)
        if session is None:
            session = trade_date
        elif session != trade_date:
            raise PriceDataError(
                f"row {index} is dated {trade_date.isoformat()} and this cross section already "
                f"carries more than one trade_date ({session.isoformat()}); one call is one "
                "session"
            )
        if ts_code in valuations:
            raise PriceDataError(
                f"{ts_code} appears twice in one {DAILY_BASIC_DATASET} cross section "
                f"({trade_date.isoformat()}); a session has one valuation per security"
            )
        cells = {
            name: _stored_optional_number(value, index, name)
            if name in DAILY_BASIC_NULLABLE_COLUMNS
            else _stored_number(value, index, name)
            for name, value in zip(DAILY_BASIC_DATA_COLUMNS[1:], row[2:], strict=True)
        }
        valuations[ts_code] = DailyValuation(ts_code=ts_code, trade_date=trade_date, **cells)  # type: ignore[arg-type]
    return valuations


def session_returns(
    bar: DailyBar,
    *,
    previous_close: float,
    previous_day: date,
    factors: AdjustmentHistory,
) -> SessionReturns:
    """The three return paths for one session, with the two correct ones cross-checked.

    Raises `PriceDataError` when the published `pre_close` and the one implied by the factor
    series differ by more than `pre_close_tolerance` allows for the two rows' own publication
    precision -- one tick of `pre_close` plus one tick of each `adj_factor`, the second of which
    scales with the price. That refusal is the point of this
    function: `daily` and `adj_factor` are two independent statements about the same corporate
    action, and a caller who has wired the wrong factor history, the wrong previous session, or
    a naive close-to-close gets an error rather than a number that is wrong by 3.3 percentage
    points with the sign reversed.

    Propagates `AdjustmentHorizonError` from `factor_on` when the factor series does not cover
    both days. That is the honest answer: a return across a window the factor read never saw is
    not computable, and carrying the nearest factor forward is the fail-open this repository
    has rejected before.
    """
    if factors.ts_code != bar.ts_code:
        raise PriceDataError(
            f"this bar is {bar.ts_code}'s and the factor history is {factors.ts_code}'s; "
            "adjusting one security's price by another's corporate actions produces a "
            "plausible number from the wrong scale"
        )
    _require_plain_date(previous_day, "previous_day")
    if previous_day >= bar.trade_date:
        raise PriceDataError(
            f"previous_day {previous_day.isoformat()} is not before the bar's "
            f"{bar.trade_date.isoformat()}; a session return needs two distinct days in order"
        )
    _require_price(previous_close, "previous_close")
    factor = factors.factor_on(bar.trade_date)
    previous_factor = factors.factor_on(previous_day)
    implied_pre_close = previous_close * previous_factor / factor
    returns = SessionReturns(
        ts_code=bar.ts_code,
        day=bar.trade_date,
        previous_day=previous_day,
        published=bar.published_return,
        adjusted=(bar.close * factor) / (previous_close * previous_factor) - 1,
        unadjusted=bar.close / previous_close - 1,
        published_pre_close=bar.pre_close,
        implied_pre_close=implied_pre_close,
        factor=factor,
        previous_factor=previous_factor,
    )
    if returns.disagreement > returns.tolerance:
        raise PriceDataError(
            f"{bar.ts_code} on {bar.trade_date.isoformat()}: the implied pre_close from "
            f"{previous_day.isoformat()}'s close and the adjustment factors is "
            f"{implied_pre_close!r}, and daily published {bar.pre_close!r} -- a gap of "
            f"{returns.disagreement!r}, past the {returns.tolerance!r} that one tick of "
            f"pre_close ({MAX_PRE_CLOSE_DISAGREEMENT}) plus one tick of each adj_factor "
            f"({ADJ_FACTOR_PUBLICATION_TICK}, carried into price space at this price and "
            "these factors) allows. The two datasets disagree about that session's corporate "
            "action, so neither return can be trusted"
        )
    return returns


def pre_close_tolerance(
    implied_pre_close: float, *, factor: float, previous_factor: float
) -> float:
    """How far the two implied previous closes may differ before that is a disagreement.

    Two terms, one per published input, both carried into yuan of `pre_close`:

    - `MAX_PRE_CLOSE_DISAGREEMENT` (0.01), one tick of a two-decimal price. Flat.
    - `implied * ADJ_FACTOR_PUBLICATION_TICK * (1/f_prev + 1/f)`, one tick of each four-decimal
      factor. `implied = prev_close * f_prev / f`, so a perturbation of either factor moves it
      by `implied * delta / f_i` -- **linear in the price**, which is the term the flat 0.01
      was missing and the reason a 450-yuan stock broke it on an ordinary session.

    Exposed as a function rather than folded into `session_returns` so a caller can ask what a
    given pair of rows was allowed, and so the measured rejection and detection rates in this
    module's docstring are checkable against the same arithmetic the guard runs.
    """
    return MAX_PRE_CLOSE_DISAGREEMENT + implied_pre_close * ADJ_FACTOR_PUBLICATION_TICK * (
        1.0 / previous_factor + 1.0 / factor
    )


def priced_cross_section(
    bars: Mapping[str, DailyBar],
    histories: Mapping[str, AdjustmentHistory],
    *,
    universe: StockUniverse,
    calendar: TradingCalendar,
    day: date,
) -> PricedCrossSection:
    """Join one session's bars onto the listed universe and its adjustment factors.

    The join `V2-P1-007` owes `V2-P1-004`, `005` and `006`, and it is fail-closed on the two
    sides where a quiet answer would be wrong and explicit on the two where it would not:

    - The session comes from `calendar.is_trading_day` (through `adjustment_factors_on`), which
      raises rather than answering `False` beyond its horizon.
    - Membership and factors come from `adjustment_factors_on`, so a listed security with no
      factor blocks the whole cross section rather than being dropped from it.
    - A listed security with a factor and **no bar** is reported in `unpriced`, not dropped.
    - A bar for a security the registry does not report listed is reported in `unlisted_bars`,
      not kept and not dropped.

    A bar dated other than `day` is refused outright: the factors and the membership are both
    resolved for `day`, so a mismatched bar would be adjusted by the wrong session's factor.
    """
    _require_plain_date(day, "day")
    for ts_code, bar in bars.items():
        if bar.trade_date != day:
            raise PriceDataError(
                f"{ts_code}'s bar is dated {bar.trade_date.isoformat()} and this cross section "
                f"is for {day.isoformat()}; the factors and the registry membership are both "
                "resolved for the requested day"
            )
    listed = adjustment_factors_on(histories, universe=universe, calendar=calendar, day=day)
    priced: list[PricedSecurity] = []
    unpriced: list[str] = []
    for ts_code, factor in listed:
        listed_bar = bars.get(ts_code)
        if listed_bar is None:
            unpriced.append(ts_code)
            continue
        priced.append(PricedSecurity(ts_code=ts_code, bar=listed_bar, factor=factor))
    listed_codes = {ts_code for ts_code, _ in listed}
    return PricedCrossSection(
        day=day,
        exchange=calendar.exchange,
        priced=tuple(priced),
        unpriced=tuple(unpriced),
        unlisted_bars=tuple(sorted(set(bars) - listed_codes)),
    )


def close_disagreements(
    bar_closes: Mapping[tuple[str, date], float],
    valuation_closes: Mapping[tuple[str, date], float],
) -> tuple[CloseDisagreement, ...]:
    """Every `(security, session)` whose two datasets disagree about the close, ascending.

    `daily_basic` republishes `close`, so the two fetches a session needs already cross-check
    each other and the check costs no request. Measured across five sessions from 2023-01-03 to
    2026-08-07 (24,188 shared rows): zero disagreements.

    Keyed by `(ts_code, trade_date)` rather than by security, so the same rule serves a single
    cross section on the read side and a whole year on the write side. A rule stated once is a
    rule that cannot be stated two ways.

    Direction is asymmetric, and the asymmetry is measured rather than assumed. A **valuation
    with no bar** is reported, because `daily_basic` was a subset of `daily` on every session
    probed and never a superset, so that direction is a contradiction. A **bar with no
    valuation** is not reported, because it is the ordinary shape of a historical partition:
    all 60 of 2020-03-02's missing names are Beijing-board codes. Refusing it would refuse every
    pre-2024 year.
    """
    findings: list[CloseDisagreement] = []
    for key in sorted(valuation_closes):
        ts_code, trade_date = key
        valuation_close = valuation_closes[key]
        bar_close = bar_closes.get(key)
        if bar_close is not None and bar_close == valuation_close:
            continue
        findings.append(
            CloseDisagreement(
                ts_code=ts_code,
                trade_date=trade_date,
                bar_close=bar_close,
                valuation_close=valuation_close,
            )
        )
    return tuple(findings)


def close_index(
    rows: Mapping[str, DailyBar] | Mapping[str, DailyValuation],
) -> dict[tuple[str, date], float]:
    """Index one session's bars or valuations by `(ts_code, trade_date)`.

    The adapter between the object-shaped result a reader holds and the key-shaped input
    `close_disagreements` compares, so the read side does not have to restate the key.
    """
    return {(ts_code, row.trade_date): row.close for ts_code, row in rows.items()}


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PriceDataError(
            f"row {index}: {column} must be a non-empty string without surrounding whitespace, "
            f"got {type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise PriceDataError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PriceDataError(f"row {index}: {column} is not an ISO date: {value!r}") from error


def _stored_number(value: object, index: int, column: str) -> float:
    """A finite `float`, exactly. `type(...) is float` for `_require_price`'s reason."""
    if type(value) is not float or not isfinite(value):
        raise PriceDataError(
            f"row {index}: {column} must be a finite float, got {type(value).__name__} {value!r}"
        )
    return value


def _stored_optional_number(value: object, index: int, column: str) -> float | None:
    if value is None:
        return None
    return _stored_number(value, index, column)


def _stored_price(value: object, index: int, column: str) -> float:
    """A finite **positive** `float`; see `DAILY_PRICE_COLUMNS` for why this is not a guess."""
    number = _stored_number(value, index, column)
    if number <= 0.0:
        raise PriceDataError(
            f"row {index}: {column} must be a positive price, got {number!r}; no session of "
            "the five probed carried a non-positive one, and a zero close divides into a return"
        )
    return number


def _require_price(value: float, role: str) -> None:
    """Reject anything that is not exactly a finite positive `float`.

    `type(...) is float`, not `isinstance`, for the reason `domain/adjustment.py`'s
    `_require_price` gives: what only the exact check refuses is a `float` *subclass*, and this
    value is about to be multiplied by a factor, so the arithmetic that runs must be `float`'s.
    """
    if type(value) is not float or not isfinite(value) or value <= 0.0:
        raise PriceDataError(
            f"{role} must be a finite positive float; got {type(value).__name__} {value!r}"
        )


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `adjustment._require_plain_date`."""
    if type(value) is not date:
        raise PriceDataError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
