"""Halts and published price limits (`V2-P1-008`) -- what did not trade, and how far it could.

Two datasets, one module, because they answer the two halves of the same question about a
session: `suspend_d` says which securities were not trading, and `stk_limit` says how far the
ones that were could move. Both are joined onto a `daily` cross section on `(ts_code,
trade_date)`, and both exist to replace something the price panel was previously *inferring*.

## `suspend_d` is three states, not a suspension list

`suspend_type` is `S` (停牌) or `R` (复牌), and `suspend_timing` is an intraday window such as
`'13:00-15:00'` when the halt covered only part of a session. Taking the table as "the day's
suspended set" is wrong twice over, and both errors were measured against the live endpoint:

- **An `R` row is a resumption.** On 2024-06-28 the table served 28 rows: 26 `S` and 2 `R`
  (`000615.SZ`, `603050.SH`). Both `R` names have a `daily` bar that session. Across every
  session probed -- 2015-07-08/09, 2016-01-04, 2018-02-08, 2022-04-25, 2024-06-28, 2025-08-01,
  2026-08-07 -- **every** `R` row had a bar (27/27, 5/5, 17/17, 6/6, 5/5, 2/2, 1/1, 1/1).
- **An `S` row with a `suspend_timing` still traded.** 2015-07-08 served 1,343 `S` rows, 31 of
  which carry a timing window, and **all 31** have a bar while **none** of the other 1,312 do.
  The same split holds on 2015-07-09 (1/1 timed with a bar, 0/1,438 untimed), 2016-01-04,
  2018-02-08, 2020-03-02 and 2022-04-25.

So the three states are `TradingState.halted` (an untimed `S`: expect **no** bar),
`TradingState.interrupted` (a timed `S`: expect a bar) and `TradingState.resumed` (`R`: expect
a bar), and only the first is what a cross-section reader means by "suspended".

### The scope of that split, stated rather than implied

"An untimed `S` has no bar" is measured on the eight sessions from **2015-07-08 onward** listed
above and it does **not** hold before then. On 2005-01-04, 12 of 18 untimed `S` names had a
bar; on 2008-01-02, 15 of 143; on 2010-01-04, 1 of 41. `suspend_timing` is null on every row of
all three sessions, so the earlier history appears simply not to carry the intraday marker and
an untimed `S` there means "halted at some point", not "halted all day". That is why
`explain_unpriced` *reports* the residue instead of asserting there is none, and why
`KNOWN_SUSPENSION_LIMITATIONS` carries `intraday_halts_are_unmarked_before_2015`.

## `stk_limit` is the exchange's published band, and the rule that reconstructs it is wrong
## for 159 of 5,338 names on one ordinary session

`backtest/execution.py`'s `AShareExecutionPolicy` derives a band from the board and an `is_st`
flag. Measured against the published `up_limit` on 2024-06-28, joining `stk_limit` onto
`daily.pre_close` for all 5,338 priced names, that rule disagrees on **159** of them, in four
independent ways:

| cause | names | what the rule does |
|---|---:|---|
| Beijing board rounds the band **inward** | 131 | one fen too wide, both sides |
| ST on ChiNext/STAR keeps the board's 20% | 25 | uses 5%, a 4x-too-narrow band |
| new listings have **no** limit for 5 sessions | 2 | invents a 10%/20% band |
| a share-reform `S` name has a 5% band and is not ST | 1 | uses 10% |

**The Beijing board is the systematic one.** All 249 `.BJ` names on 2024-06-28 match
`floor(pre_close * 1.30)` for `up_limit` and `ceil(pre_close * 0.70)` for `down_limit`;
`ROUND_HALF_UP` -- what the main board uses and what the policy applies everywhere -- matches
only 118 and 143 of them. The same 100% / ~50% split holds on 2022-04-25 (87 names),
2025-08-01 (269) and 2026-08-07 (333). The main board is the opposite: 3,068 of 3,174 names
match `ROUND_HALF_UP` at 10% and 104 at 5%, with the remaining 2 limit-free. So the rounding
rule is per exchange, and a policy with one rounding mode is wrong on one exchange by
construction.

**ST is not a ratio, it is a main-board ratio.** 128 securities were ST by name on 2024-06-28
(`domain/name_history.py`'s grammar over the 13,342-row rename corpus). 104 -- all main board --
have a published 5% band. The other 25 (24 ChiNext, 1 STAR: `300013.SZ`, `300029.SZ`, ...,
`688282.SH`) have a published **20%** band. `_rejection_reason`'s
`Decimal("0.05") if market.is_st else _board_limit(...)` makes the ST test win over the board,
so on those 25 it computes a band four times too narrow and rejects orders on bars that were
nowhere near a limit.

**A limit-free session is published as a sentinel, not as a null.** A newly listed security has
no price limit for its first five sessions, and Tushare encodes that as an out-of-range
`up_limit` with `down_limit=0.01`. Four sentinel values have been observed and the encoding has
changed twice: `100000.0` (SSE, 2020 and 2022), `1000000.0` (SZSE, 2022), `99999.999` (SSE,
2024 and 2026) and `999999.999` (SZSE, 2024 and 2026). Every occurrence probed was a security
in session 1..5 of its listing (`688086.SH` 4th, `301120.SZ` 5th, `301707.SZ` 1st, ...) and no
security outside that window carried one. Before the registration system the same situation was
published as a **real** band: `300740.SZ` and `603709.SH` listed 2018-02-08 with `up_limit`
exactly 44% and `down_limit` exactly -36% of `pre_close`.

`is_bounded` therefore separates the two populations by ratio rather than by a value whitelist,
and the separation is not close: the widest **real** published band ever measured here is 1.44x
`pre_close` and the narrowest **sentinel** is 384x (`688052.SH`, 2022-04-25). `LIMIT_FREE_RATIO`
sits at 2.0 -- 39% above the first and a factor of 192 below the second; see that constant for
why the two margins are deliberately not the same size. A whitelist would read a fifth sentinel
value as a real price; the ratio test reads it as limit-free, and even a reader that ignores
the classification entirely gets the right answer, because `high >= 99999.999` and
`low <= 0.01` are both false on any real bar.

## Which securities `stk_limit` covers, which is not "the ones `daily` covers"

On 2024-06-28 `stk_limit` served 6,867 rows against `daily`'s 5,338. The 1,529 extra are **not**
one population and are mostly not stocks at all:

- **1,418 funds** -- ETFs and LOFs (`159*.SZ`, `51*.SH`, `501*.SH`, `16*.SZ`, `56*.SH`), all
  present in `fund_basic`.
- **85 B shares** -- 44 `900*.SH` and 41 `200*/201*.SZ`, in neither `stock_basic` nor
  `fund_basic`.
- **26 halted A shares** -- exactly the untimed `S` set of `suspend_d` for that session. The
  exchange publishes a band for a security that did not trade.

`adj_factor`'s 49-name excess over `daily` on the same session (`domain/daily_prices.py`) is a
*different* set: it shares those 26 and its other 23 are delisted names, which `stk_limit` does
**not** carry. Calling the two excesses one population was measured to be wrong in both
directions.

`daily ⊆ stk_limit` is likewise a **recent** property, not a general one. It holds on
2024-06-28, 2025-08-01 and 2026-08-07 and fails on every earlier session probed: 60 bars had no
published limit on 2020-03-02, 56 on 2022-04-25, 44 on 2016-01-04, 23 on 2015-07-08/09. All but
one of those are `.BJ` codes -- `stk_limit` reached the Beijing board later than `daily` did --
and the exception is `001914.SZ`, which is missing on five separate sessions. `stk_limit` also
has a **horizon**: 0 rows on 2005-01-04 and 2006-01-04, 1,408 on 2007-01-04, and
`stk_limit(ts_code=000001.SZ)` returns 4,762 rows beginning 2007-01-04 against a bar history
that starts in 1991.

## Layering

Pure `datetime`/`dataclasses`/`enum` plus one sibling `domain` module -- `daily_prices` for the
shared `trade_date` column name and for `DailyBar`/`PricedCrossSection`, which are what a limit
and a halt are joined onto. No provider, no store, no clock; the same placement, and the same
reason, as `domain/daily_prices.py` itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.daily_prices import (
    PRICE_DATE_COLUMN,
    DailyBar,
    PricedCrossSection,
)
from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME

SUSPENSION_DATASET: Final[str] = "suspend_d"
PRICE_LIMIT_DATASET: Final[str] = "stk_limit"
"""The panel datasets (and partition directories) these two are stored under.

Declared here rather than in `providers/tushare.py` for the reason `DAILY_DATASET` is:
`panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

SUSPENSION_TYPE_COLUMN: Final[str] = "suspend_type"
SUSPENSION_TIMING_COLUMN: Final[str] = "suspend_timing"
UP_LIMIT_COLUMN: Final[str] = "up_limit"
DOWN_LIMIT_COLUMN: Final[str] = "down_limit"

SUSPENSION_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    SUSPENSION_TYPE_COLUMN,
    SUSPENSION_TIMING_COLUMN,
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

The date column is `daily`'s own `PRICE_DATE_COLUMN`, imported rather than restated, because it
is the join key: a halt is a fact about the same session a bar is. `adj_factor` renames its date
column for the opposite reason -- a factor date and a trade date are different things.

`suspend_timing` is projected even though it is null on most rows, because it is the column that
separates a whole-day halt from an intraday one; see this module's docstring.
"""

SUSPENSION_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *SUSPENSION_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back."""

PRICE_LIMIT_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    UP_LIMIT_COLUMN,
    DOWN_LIMIT_COLUMN,
)
"""Every `stk_limit` response field except `ts_code`, which becomes the subject."""

PRICE_LIMIT_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *PRICE_LIMIT_DATA_COLUMNS,
)

HALT_TYPE: Final[str] = "S"
RESUMPTION_TYPE: Final[str] = "R"
"""The only two `suspend_type` values, measured across the whole published corpus.

Not a guess about the endpoint's vocabulary, and not a sample either. 240 monthly windows
covering 2007-01..2026-12 were pulled; 32 of them came back at the 5,000-row cap and were
re-pulled as four sub-windows each; the one sub-window that *still* truncated
(2015-07-08..14, the week a large part of the market halted at once) was covered
session-by-session by the 2015 full-year census. **537,671 rows in total, none of them
truncated, and every one carries `S` or `R`** -- 460,643 `S` and 22,828 `R` in the monthly pass
alone.

A third value is refused by `build_suspension_day` rather than bucketed into one of these,
because both of the things this column decides -- whether the security traded, and whether an
absent bar is explained -- invert on it.
"""

MIN_EXPLAINED_SESSION_SHARE: Final[float] = 0.85
"""How thin one session may be **after its whole-day halts are added back**, before the write
refuses it. See `panel_ingest._refuse_unexplained_thin_sessions` for what it guards.

## Why this can sit where `MIN_SESSION_ROW_SHARE` cannot

`domain/daily_prices.py::MIN_SESSION_ROW_SHARE` is 0.5, and its docstring is candid that this
is "set below the thinnest real session found rather than at a level that would be satisfying":
2015-07-09 served 1,363 bars against that year's median of 2,359, a share of **0.578**, because
a large part of the A-share market halted trading at once that week. So a fetch that returned
80% of the market was invisible by construction.

`suspend_d` supplies the missing term. Counting each session's whole-day halts beside its bars,
a **full-year** census of 2015 -- one `daily` request and one `suspend_d` request for each of
its 244 sessions -- gives:

    metric                       min        median   min/median
    bars alone                   1,363      2,359    0.578   (2015-07-09)
    bars + whole-day halts       2,593      2,796    0.927   (2015-01-09)

and the session that binds moves from July to January, where the "shortfall" is not a halt at
all but the ordinary fact that the market had fewer listed names at the start of the year than
at its median. So the constant is bounded below by what a real partition of the worst year
reaches, exactly as `MIN_SESSION_ROW_SHARE` is, and the whole gain is that the bound moved from
0.578 to 0.927.

2015 is the year that binds of the three censused in full:

    year   sessions   bars min/median          bars + halts min/median
    2008   246        0.914  (05-13)           **0.959**  (02-13)
    2015   244        0.578  (07-09)           **0.927**  (01-09)
    2018   243        0.941  (02-08)           **0.984**  (01-03)

2018's bar figure reproduces the one `MIN_SESSION_ROW_SHARE`'s docstring already published
(median 3,378, min 3,177 on 02-08), which is what makes the other two checkable rather than
merely asserted -- the same script produced all three and one of them lands on a number this
repository had before it existed. 2008 is included precisely because it is *before* the
intraday marker exists, so it is the year where `halted` over-counts most; it does not bind
either.

## Where 0.85 comes from, and what it leaves

It is set a clear margin below the 0.927 measured minimum -- the margin is for within-year
listing growth, which is what puts January at the bottom and which was steeper in some years
than in 2015 -- and it catches what the 0.5 floor cannot: a session that came back with 85% of
the market and no halts to show for it. On 2015's median that is a session missing ~420 names
rather than ~1,180.

This is still a floor and not a census. A fetch that lost 5% of the market passes, and the
fully general answer is a per-name expectation, which needs the universe rather than a row
count -- `explain_unpriced` is where that question is asked, on the read side, where the
registry is in hand.

Three years were censused in full and they are not the whole history. Years before 2015 carry a
second hazard on top of that: `suspend_timing` is null throughout, so `halted` *over*-counts
there (see `KNOWN_SUSPENSION_LIMITATIONS`), which inflates the explained share and makes this
floor **weaker** on early history rather than stricter -- 2008 is in the table above for that
reason and does not bind, but "does not bind in 2008" is not "cannot bind in 2003". The 0.5
row-count floor still runs underneath, unconditionally, on every year.
"""

LIMIT_FREE_RATIO: Final[float] = 2.0
"""Above this multiple of the previous close, an `up_limit` is a "no limit" sentinel.

A ratio test rather than a whitelist of the four observed sentinel values, and the two
populations it separates are 267x apart: the widest real published band measured is **1.44x**
(`300740.SZ` and `603709.SH` on their first trading day, 2018-02-08, under the 44% rule the
registration system replaced) and the narrowest sentinel is **384x** (`688052.SH`,
2022-04-25).

The margins are deliberately **not** symmetric, and the asymmetry is stated rather than
averaged over. Below, there is 39% of headroom over the widest real band ever observed here --
enough for a band rule wider than 44% but not for an unbounded one. Above, there is a factor of
192 to the nearest sentinel. That is the right shape, because the two errors are not
equivalent: reading a real band as limit-free would widen a bar's band to a number no price
reaches and let an order fill on a locked bar, while reading a sentinel as a real price is
harmless in both consumers (no bar satisfies `high >= 99999.999`). See this module's docstring
for the four sentinel values.
"""


class SuspensionError(ValueError):
    """Raised for any malformed halt or limit row, or any malformed question about one.

    A `ValueError` subclass to match `domain/daily_prices.py`'s `PriceDataError` and
    `domain/adjustment.py`'s `AdjustmentError`. One error type for both datasets because they
    are stored, read and joined together; splitting it would make a caller catch two.
    """


class TradingState(Enum):
    """What one `suspend_d` row says about whether the security traded. Not a `bool`.

    `__bool__` raises for every member, including `resumed`, for `RiskWarning`'s reason: two of
    the three states mean the security **did** trade, so `if state:` would read correctly for
    one member and silently merge the other two.
    """

    halted = "halted"
    """`S` with no `suspend_timing`: the whole session. Expect **no** bar."""

    interrupted = "interrupted"
    """`S` with a `suspend_timing` window: part of the session. Expect a bar.

    31 of 2015-07-08's 1,343 `S` rows were this shape and all 31 had a bar. Counting them as
    halted is how a cross-section reader over-explains a thin session.
    """

    resumed = "resumed"
    """`R`: trading resumed that session. Expect a bar; every `R` row probed had one."""

    def __bool__(self) -> bool:
        raise SuspensionError(
            f"{type(self).__name__}.{self.name} is a three-valued verdict and has no truth "
            "value; two of its three members mean the security traded, so compare it against "
            "the member you mean (`is TradingState.halted`) rather than collapsing them"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SuspensionLimitation:
    """One named boundary on what stored halts and limits can be trusted to answer."""

    code: str
    detail: str


KNOWN_SUSPENSION_LIMITATIONS: Final[tuple[SuspensionLimitation, ...]] = (
    SuspensionLimitation(
        code="intraday_halts_are_unmarked_before_2015",
        detail=(
            "TradingState.interrupted is derived from suspend_timing, and that column is null "
            "on every row of the three earliest sessions probed. On 2005-01-04, 12 of 18 "
            "untimed S rows had a daily bar; on 2008-01-02, 15 of 143; on 2010-01-04, 1 of 41. "
            "From 2015-07-08 onward the split is exact on every session probed -- every timed S "
            "had a bar and no untimed S did -- so halted() over-counts on early history and "
            "explain_unpriced's residue is reported rather than asserted away. The direction is "
            "the safe one for a cross section (a name is called halted when it traded, so it "
            "shows up as an unexplained *surplus* rather than a hidden absence) and the wrong "
            "one for a halt census."
        ),
    ),
    SuspensionLimitation(
        code="stk_limit_starts_in_2007_and_reached_the_beijing_board_late",
        detail=(
            "stk_limit has a horizon and a coverage gap, both measured. It serves 0 rows for "
            "2005-01-04 and 2006-01-04 and 1,408 for 2007-01-04, and "
            "stk_limit(ts_code=000001.SZ) returns 4,762 rows beginning 2007-01-04 against a bar "
            "history that starts in 1991 -- so no published band exists for the first sixteen "
            "years of the market. Separately, daily is a subset of stk_limit only on recent "
            "sessions: 60 bars had no published limit on 2020-03-02, 56 on 2022-04-25, 44 on "
            "2016-01-04 and 23 on 2015-07-08, and every one of those but 001914.SZ is a .BJ "
            "code. So a historical partition legitimately carries bars with no band, and "
            "limit_touch is asked per security rather than being derived for a whole cross "
            "section."
        ),
    ),
    SuspensionLimitation(
        code="the_limit_free_sentinel_encoding_has_changed_twice",
        detail=(
            "A security with no price limit is published as an out-of-range up_limit with "
            "down_limit=0.01, and the value is neither stable nor single: 100000.0 (SSE 2020, "
            "2022), 1000000.0 (SZSE 2022), 99999.999 (SSE 2024, 2026) and 999999.999 (SZSE "
            "2024, 2026). is_bounded therefore tests the ratio to the previous close rather "
            "than the value, because a whitelist would misread a fifth encoding. A reader that "
            "ignores the classification is still correct -- no real bar satisfies high >= "
            "99999.999 or low <= 0.01 -- so this shape is fail-safe in both consumers rather "
            "than merely disclosed."
        ),
    ),
    SuspensionLimitation(
        code="the_published_band_is_not_reproducible_from_board_and_st_alone",
        detail=(
            "Measured on 2024-06-28 against all 5,338 priced names, a board-plus-is_st rule "
            "disagrees with the published up_limit on 159 of them, for four independent "
            "reasons: the Beijing board rounds the band inward (all 249 .BJ names match "
            "floor/ceil and only 118/143 match ROUND_HALF_UP, so 131 differ by one fen); ST on "
            "ChiNext and STAR keeps the board's 20% rather than dropping to 5% (25 names, where "
            "the rule computes a band four times too narrow); a new listing has no limit for "
            "five sessions (2 names); and 600182.SH 'S佳通', a share-reform name the ST grammar "
            "correctly calls ordinary, carries a 5% band (1 name). None of these is a rounding "
            "artefact of the rule -- they are four different exchange rules -- which is why the "
            "published value is what AShareExecutionPolicy prefers when it is given one."
        ),
    ),
    SuspensionLimitation(
        code="stk_limit_covers_funds_and_b_shares_as_well_as_stocks",
        detail=(
            "stk_limit served 6,867 rows on 2024-06-28 against daily's 5,338, and the 1,529 "
            "extra are three populations: 1,418 funds (ETFs and LOFs, all in fund_basic), 85 B "
            "shares (44 900*.SH and 41 200*/201*.SZ) and the 26 halted A shares of that "
            "session's suspend_d. So the row count of this dataset is not a count of stocks, "
            "a partition of it is not a stock universe, and price_limits_from_panel_rows keys "
            "by ts_code without asserting that every key is an equity."
        ),
    ),
    SuspensionLimitation(
        code="both_datasets_are_dated_one_session_late_rather_than_at_the_open",
        detail=(
            "Both descriptors use ClockStrategy.daily_close, so a session's halts and bands "
            "become knowable at 16:30 Asia/Shanghai on that session -- the same instant a bar "
            "does. In reality both are known before the open: a band is computed from the "
            "previous close and published pre-open, and a halt is announced before trading "
            "starts. So a reader standing at 09:30 on session D sees neither, and an intraday "
            "or open-auction strategy cannot ask this panel what today's band is. The error is "
            "in the conservative direction (late, never early, so no look-ahead reaches a "
            "stored partition) and the alternative is worse: neither response carries a "
            "publication instant, so an earlier available_time would have to be invented, and "
            "the one place this repository invented one -- trade_cal's start-of-year rule -- is "
            "carried as KNOWN_CALENDAR_LOOKAHEAD with two proven leaks. Closing this needs a "
            "pre-open clock strategy whose instant is defensible, which is a change to "
            "ClockStrategy rather than to these two rows."
        ),
    ),
    SuspensionLimitation(
        code="silent_truncation_at_a_cap_this_cross_section_is_close_to",
        detail=(
            "stk_limit serves at most 7,800 rows per response and suspend_d at most 5,000, both "
            "measured on 2026-08-09 with a multi-session window and limit=8000/10000/12000 (the "
            "row count does not rise). providers/tushare.py refuses a response at the cap. The "
            "headroom is the part worth watching: the whole-market stk_limit cross section was "
            "6,867 rows on 2024-06-28, 7,216 on 2025-08-01 and 7,733 on 2026-08-07 -- +349 then "
            "+517 -- so it sits 67 rows under the cap and the last two years' growth would clear "
            "it inside one. daily's equivalent margin is 465 of 6,000. When it goes, the fetch "
            "refuses rather than storing a short session, and ProviderRequest.subjects splits "
            "the session; suspend_d is nowhere near its own cap (1,466 rows on its worst "
            "measured session, 2015-07-09)."
        ),
    ),
)
"""Named boundaries on what stored halts and limits answer, each measured on real data.

**Not an enumeration of every way these datasets could be wrong.** These are the ones a live
probe of the endpoints could demonstrate on 2026-08-09.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class SuspensionRecord:
    """One security's halt or resumption on one session.

    A plain carrier with no validation of its own, following `DailyBar`'s precedent: a nominal
    type is not a boundary, so the rules live once, in `suspensions_from_panel_rows`.
    """

    ts_code: str
    trade_date: date
    suspend_type: str
    timing: str | None

    @property
    def state(self) -> TradingState:
        """The three-valued verdict this row carries. See `TradingState`."""
        if self.suspend_type == RESUMPTION_TYPE:
            return TradingState.resumed
        if self.timing is None:
            return TradingState.halted
        return TradingState.interrupted


@dataclass(frozen=True, slots=True, kw_only=True)
class SuspensionDay:
    """One session's halts, partitioned by the three states a `suspend_d` row can carry.

    The three tuples are disjoint and each is sorted. `halted` is the only one that means "no
    bar"; the other two name securities that traded and are carried so a caller cannot reach
    them by accident and cannot lose them either.
    """

    day: date
    halted: tuple[str, ...]
    interrupted: tuple[str, ...]
    resumed: tuple[str, ...]

    @property
    def traded(self) -> frozenset[str]:
        """Securities this session's rows say **did** trade: the timed halts and the
        resumptions."""
        return frozenset(self.interrupted) | frozenset(self.resumed)

    @property
    def halted_codes(self) -> frozenset[str]:
        """`halted` as a set, for a caller asking about many securities.

        `halted` stays a sorted tuple because that is what makes an error message and a stored
        record deterministic, and because 2015-07-09's 1,438 halts are still only 1,438 strings.
        But `is_halted` is then a linear scan, and `explain_unpriced` on that same session would
        run it 1,438 times -- 2e6 string comparisons for a question that is one set intersection.
        So the loop-shaped callers build this once; `is_halted` is for a single question.
        """
        return frozenset(self.halted)

    def state_of(self, ts_code: str) -> TradingState | None:
        """This security's state, or `None` when the session's rows do not mention it.

        `None` rather than a fourth enum member: a security with no row is the ordinary case
        (5,312 of 5,338 on 2024-06-28) and giving it a name in `TradingState` would invite a
        caller to treat "nothing happened" as a kind of halt.
        """
        if ts_code in self.halted:
            return TradingState.halted
        if ts_code in self.interrupted:
            return TradingState.interrupted
        if ts_code in self.resumed:
            return TradingState.resumed
        return None

    def is_halted(self, ts_code: str) -> bool:
        """Whether this security was halted for the **whole** session.

        The one boolean this contract does offer, because it is the question a cross section
        asks and because its two false cases ("traded" and "not mentioned") mean the same thing
        to that caller.
        """
        return ts_code in self.halted


@dataclass(frozen=True, slots=True, kw_only=True)
class ExplainedCrossSection:
    """A session's unpriced names, split into the ones a halt accounts for and the rest.

    The answer `domain/daily_prices.py::KNOWN_PRICE_LIMITATIONS`'
    `a_partial_cross_section_is_invisible_without_suspend_d` names as missing. A
    `PricedCrossSection` reports that N listed securities had no bar; this says how many of
    those N `suspend_d` says were halted, and -- the part that matters -- **which ones it does
    not**.

    `unexplained` is not asserted to be empty and must not be: `suspend_d` does not carry
    intraday markers before 2015 and a security can be absent from a cross section for reasons
    no halt table records. What the split buys is that a short fetch, which was previously
    indistinguishable from a halt-heavy session, now shows up as a population with no halt
    behind it.
    """

    day: date
    halted: tuple[str, ...]
    unexplained: tuple[str, ...]

    @property
    def unpriced_count(self) -> int:
        """How many listed securities had no bar, explained or not."""
        return len(self.halted) + len(self.unexplained)

    @property
    def is_fully_explained(self) -> bool:
        """Whether every unpriced name has a whole-day halt behind it."""
        return not self.unexplained


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceLimit:
    """One security's published upper and lower limit prices for one session.

    A plain carrier, `DailyBar`'s precedent again. The two values are stored exactly as
    published, including the limit-free sentinels -- see `is_bounded` and this module's
    docstring for why they are neither normalised to `None` here nor whitelisted.
    """

    ts_code: str
    trade_date: date
    up_limit: float
    down_limit: float

    def is_bounded(self, previous_close: float) -> bool:
        """Whether this row is a real band rather than the "no limit" sentinel.

        Needs `previous_close` because the sentinel is recognised by its ratio and not by its
        value; `LIMIT_FREE_RATIO` states the measured separation between the two populations.
        """
        _require_price(previous_close, "previous_close")
        return self.up_limit < previous_close * LIMIT_FREE_RATIO

    def implied_ratio(self, previous_close: float) -> float:
        """`up_limit / previous_close - 1`: the band the exchange actually published.

        Reported rather than rounded to one of 5% / 10% / 20% / 30%, because the rounding is
        part of what differs: on the Beijing board `floor(pre * 1.30)` gives 0.2992 for
        `920924.BJ` on 2024-06-28 where the nominal ratio is 0.30, and a reader comparing this
        against a nominal figure should see the fen rather than have it hidden.
        """
        _require_price(previous_close, "previous_close")
        return self.up_limit / previous_close - 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class LimitTouch:
    """Where one session's bar sat against its published band.

    `at_up`/`at_down` are the *touch*: the bar reached the limit at some point. `one_price_up`/
    `one_price_down` are the stronger shape `AShareExecutionPolicy` rejects on -- the whole
    session traded at the limit, so there was no counterparty on the other side.

    All four are `False` for a limit-free session, and that falls out of the arithmetic rather
    than needing a branch: no real bar satisfies `high >= 99999.999`.
    """

    ts_code: str
    trade_date: date
    at_up: bool
    at_down: bool
    closed_at_up: bool
    closed_at_down: bool
    one_price_up: bool
    one_price_down: bool


def build_suspension_day(day: date, records: Iterable[SuspensionRecord]) -> SuspensionDay:
    """Assemble one session's `SuspensionDay` from its rows, in any order.

    Refuses, rather than repairs, four things: a record for another session, an unknown
    `suspend_type`, a blank `suspend_timing` (which would read as an untimed halt while
    carrying a column the upstream populated), and one security appearing twice in states that
    disagree. Byte-identical duplicates collapse, following `build_name_history`: a duplicate
    carries no fact the original does not.
    """
    _require_plain_date(day, "day")
    states: dict[str, TradingState] = {}
    for record in records:
        _require_text(record.ts_code, "ts_code")
        _require_plain_date(record.trade_date, "trade_date")
        if record.trade_date != day:
            raise SuspensionError(
                f"{record.ts_code} is dated {record.trade_date.isoformat()} and this suspension "
                f"day is {day.isoformat()}; one call is one session, because whether a security "
                "traded is a fact about a day"
            )
        if record.suspend_type not in (HALT_TYPE, RESUMPTION_TYPE):
            raise SuspensionError(
                f"{record.ts_code} on {day.isoformat()} carries suspend_type "
                f"{record.suspend_type!r}; the whole published corpus uses only "
                f"{HALT_TYPE!r} and {RESUMPTION_TYPE!r}, so a third value is a schema change "
                "and bucketing it into one of the two would answer for a state this contract "
                "cannot name"
            )
        timing = record.timing
        if timing is not None and (not timing or timing.strip() != timing):
            raise SuspensionError(
                f"{record.ts_code} on {day.isoformat()} carries suspend_timing "
                f"{timing!r}; an empty or padded window is not a window, and it would "
                "read as a whole-day halt while the upstream in fact populated the column"
            )
        state = record.state
        existing = states.get(record.ts_code)
        if existing is not None and existing is not state:
            raise SuspensionError(
                f"{record.ts_code} is both {existing.name} and {state.name} on "
                f"{day.isoformat()}; a security has one trading state per session, so this is "
                "two sources that were never reconciled"
            )
        states[record.ts_code] = state
    return SuspensionDay(
        day=day,
        halted=tuple(sorted(c for c, s in states.items() if s is TradingState.halted)),
        interrupted=tuple(sorted(c for c, s in states.items() if s is TradingState.interrupted)),
        resumed=tuple(sorted(c for c, s in states.items() if s is TradingState.resumed)),
    )


def suspensions_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[date, SuspensionDay]:
    """Rebuild one `SuspensionDay` per session from rows shaped like
    `SUSPENSION_PANEL_COLUMNS`.

    **Many sessions per call, unlike `daily_bars_from_panel_rows`**, and the asymmetry is not an
    oversight. A cross section is refused more than one date because its factors and its
    registry membership are resolved for one day, so two dates in one mapping would adjust one
    session with the other's factor. A halt row joins to nothing: it is self-dating, and the
    unit a caller wants is a year's worth of them at once -- `_refuse_unexplained_thin_sessions`
    needs every session of a partition in one value.

    A read-only mapping rather than a `dict`, following `name_histories_from_panel_rows`: the
    corpus covers the whole market and a caller mutating one entry would be editing what other
    callers hold.
    """
    grouped: dict[date, list[SuspensionRecord]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(SUSPENSION_PANEL_COLUMNS):
            raise SuspensionError(
                f"row {index} has {len(row)} values, expected {len(SUSPENSION_PANEL_COLUMNS)} "
                f"({', '.join(SUSPENSION_PANEL_COLUMNS)})"
            )
        subject, day_text, suspend_type, timing = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        trade_date = _parse_iso_date(day_text, index, PRICE_DATE_COLUMN)
        grouped.setdefault(trade_date, []).append(
            SuspensionRecord(
                ts_code=ts_code,
                trade_date=trade_date,
                suspend_type=_require_stored_text(suspend_type, index, SUSPENSION_TYPE_COLUMN),
                timing=_stored_optional_text(timing, index, SUSPENSION_TIMING_COLUMN),
            )
        )
    return MappingProxyType(
        {day: build_suspension_day(day, records) for day, records in sorted(grouped.items())}
    )


def price_limits_from_panel_rows(rows: Iterable[Sequence[object]]) -> dict[str, PriceLimit]:
    """Rebuild **one session's** published bands from rows shaped like
    `PRICE_LIMIT_PANEL_COLUMNS`.

    One session, `daily_bars_from_panel_rows`' rule and for its reason: the result is keyed by
    `ts_code` and is joined against one day's bars, so a mapping that quietly held two dates
    would compare a bar to another session's band.

    A mutable `dict` for that function's reason too -- a cross section is a working set a caller
    legitimately narrows.
    """
    limits: dict[str, PriceLimit] = {}
    session: date | None = None
    for index, row in enumerate(rows):
        if len(row) != len(PRICE_LIMIT_PANEL_COLUMNS):
            raise SuspensionError(
                f"row {index} has {len(row)} values, expected "
                f"{len(PRICE_LIMIT_PANEL_COLUMNS)} ({', '.join(PRICE_LIMIT_PANEL_COLUMNS)})"
            )
        subject, day_text, up_limit, down_limit = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        trade_date = _parse_iso_date(day_text, index, PRICE_DATE_COLUMN)
        if session is None:
            session = trade_date
        elif session != trade_date:
            raise SuspensionError(
                f"row {index} is dated {trade_date.isoformat()} and this band set already "
                f"carries more than one {PRICE_DATE_COLUMN} ({session.isoformat()}); one call "
                "is one session, because a band is computed from that session's previous close"
            )
        if ts_code in limits:
            raise SuspensionError(
                f"{ts_code} appears twice in one {PRICE_LIMIT_DATASET} cross section "
                f"({trade_date.isoformat()}); a session has one band per security, so this is "
                "two fetches that were never reconciled"
            )
        up = _stored_price(up_limit, index, UP_LIMIT_COLUMN)
        down = _stored_price(down_limit, index, DOWN_LIMIT_COLUMN)
        if down > up:
            raise SuspensionError(
                f"row {index}: {ts_code} on {trade_date.isoformat()} has a "
                f"{DOWN_LIMIT_COLUMN} of {down!r} above its {UP_LIMIT_COLUMN} of {up!r}; that "
                "is an empty band, and every order against it would be rejected on both sides"
            )
        limits[ts_code] = PriceLimit(
            ts_code=ts_code, trade_date=trade_date, up_limit=up, down_limit=down
        )
    return limits


def limit_touch(bar: DailyBar, limit: PriceLimit) -> LimitTouch:
    """Where one bar sat against its own published band.

    Refuses a bar and a band that are not the same `(security, session)`: comparing a price to
    another security's limit, or to the same security's limit on another day, produces a
    plausible boolean from the wrong numbers -- the failure `session_returns` refuses for the
    same reason.

    The comparisons are `>=` and `<=` rather than `==`. A published limit is a two-decimal price
    and so is a high, so equality is the ordinary case; but a bar that prints *through* its
    published band is a real shape on the sessions where the band is not what the rule says
    (`domain/price_limits`' docstring lists four), and reporting such a bar as "did not touch"
    would be the wrong direction.
    """
    if bar.ts_code != limit.ts_code:
        raise SuspensionError(
            f"this bar is {bar.ts_code}'s and the band is {limit.ts_code}'s; comparing one "
            "security's price to another's limit produces a plausible verdict from the wrong "
            "numbers"
        )
    if bar.trade_date != limit.trade_date:
        raise SuspensionError(
            f"{bar.ts_code}'s bar is dated {bar.trade_date.isoformat()} and its band "
            f"{limit.trade_date.isoformat()}; a band is computed from one session's previous "
            "close, so the two have to be the same session"
        )
    return LimitTouch(
        ts_code=bar.ts_code,
        trade_date=bar.trade_date,
        at_up=bar.high >= limit.up_limit,
        at_down=bar.low <= limit.down_limit,
        closed_at_up=bar.close >= limit.up_limit,
        closed_at_down=bar.close <= limit.down_limit,
        one_price_up=bar.low >= limit.up_limit,
        one_price_down=bar.high <= limit.down_limit,
    )


def explain_unpriced(
    cross_section: PricedCrossSection, suspensions: SuspensionDay
) -> ExplainedCrossSection:
    """Split a cross section's `unpriced` names by whether a whole-day halt accounts for them.

    The join `V2-P1-008` owes `V2-P1-007`. `PricedCrossSection.unpriced` is every listed
    security with a factor and no bar, and until this dataset existed there was nothing to say
    whether that was 26 halted names or a fetch that came back short.

    Only `TradingState.halted` explains an absence. A timed `S` and an `R` both traded, so
    counting them here would let a session with 1,300 intraday halts explain away 1,300 missing
    bars -- which is exactly the over-explanation that makes an alarm useless.

    Refuses a mismatched pair of days outright rather than answering from the wrong session's
    halts.
    """
    if cross_section.day != suspensions.day:
        raise SuspensionError(
            f"the cross section is for {cross_section.day.isoformat()} and the halts are for "
            f"{suspensions.day.isoformat()}; a halt on the wrong day explains nothing and "
            "silently reports the difference as unexplained"
        )
    halted_codes = suspensions.halted_codes
    halted = tuple(code for code in cross_section.unpriced if code in halted_codes)
    unexplained = tuple(code for code in cross_section.unpriced if code not in halted_codes)
    return ExplainedCrossSection(day=cross_section.day, halted=halted, unexplained=unexplained)


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise SuspensionError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise SuspensionError(
            f"row {index}: {column} must be a non-empty string without surrounding whitespace, "
            f"got {type(value).__name__} {value!r}"
        )
    return value


def _stored_optional_text(value: object, index: int, column: str) -> str | None:
    """A stored nullable text cell. `None` stays `None`; anything else must be real text.

    `""` is refused rather than folded into `None`, because the two mean different things here:
    a null `suspend_timing` is `TradingState.halted` and a populated one is
    `TradingState.interrupted`, so a blank string would be an intraday halt with no window.
    """
    if value is None:
        return None
    return _require_stored_text(value, index, column)


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise SuspensionError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SuspensionError(f"row {index}: {column} is not an ISO date: {value!r}") from error


def _stored_price(value: object, index: int, column: str) -> float:
    """A finite **positive** `float`, exactly. `type(...) is float` for `_require_price`'s
    reason.

    A limit price of zero or below cannot bound anything, and a `None` here would compare
    `False` against every bar and silently report a security as never touching its band.
    """
    if type(value) is not float or not isfinite(value) or value <= 0.0:
        raise SuspensionError(
            f"row {index}: {column} must be a finite positive float, got "
            f"{type(value).__name__} {value!r}"
        )
    return value


def _require_price(value: float, role: str) -> None:
    """Reject anything that is not exactly a finite positive `float`; see
    `daily_prices._require_price`."""
    if type(value) is not float or not isfinite(value) or value <= 0.0:
        raise SuspensionError(
            f"{role} must be a finite positive float; got {type(value).__name__} {value!r}"
        )


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `adjustment._require_plain_date`."""
    if type(value) is not date:
        raise SuspensionError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
