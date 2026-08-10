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

### A session can carry both an `R` and an `S` for one security, and the `S` decides

The two are not alternatives. An `R` says trading resumed; an `S` on that same session says how
much of it the security traded, and the second is the finer statement -- so `_reconcile_states`
prefers it and lets `suspend_timing` choose between `halted` and `interrupted` exactly as it
does when no `R` stands beside it. A census of every session of 2015..2026 (334,362 rows) finds
73 `(security, session)` pairs with two rows, **all 73 of them one `R` and one `S`**, and on the
47 whose security `daily` carries that year the `S` row's timing predicts the bar 45 times. See
`KNOWN_SUSPENSION_LIMITATIONS`' `a_resumption_and_a_halt_can_share_one_session` for the two
misses and for why both fall the safe way.

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
independent ways.

A count like that only means something with its two definitions attached, because both admit a
defensible alternative that moves it. **`is_st`** is `RiskWarning.st` / `star_st` / `pt` from
`domain/name_history.py`'s grammar over the 14,167-row rename corpus, and *not*
`delisting_process`: a delisting-arrangement name such as `002433.SZ` 太安退 carries the
ordinary main-board 10% (0.30 -> 0.33), so folding it in adds two spurious disagreements and
gives 161. **The board** comes from the code prefix with `300`/`301`/**`302`** all on ChiNext;
reading `302132.SZ` 中航电测 as main board adds another and gives 162. Comparing `down_limit`
as well as `up_limit` adds none -- the two sides disagree on exactly the same names. Under the
definitions this repository actually uses, the figure is 159, and it is 131 + 25 + 2 + 1:

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

**A limit-free session is published as a sentinel, not as a null -- and the floor is not always
0.01.** A security with no price limit that day gets an out-of-range `up_limit`. **Six**
encodings have been observed, from a scan of 459 whole-market sessions -- the first trading day
of every month from 2007-01 to 2026-08 and every `.BJ` listing day since 2022-02-28 --
`stk_limit` joined onto `daily.pre_close`, 1,918,266 rows in all, of which 1,387 are sentinels:

| `up_limit` | `down_limit` | exchange | sessions seen on | span |
|---|---|---|---|---|
| `10000.0`    | `0.01`    | SSE  | 1   | 2007-01-04 only (`600145.SH`) |
| `100000.0`   | `0.01`    | SSE  | 120 | 2019-10-08 .. 2023-06-08 |
| `99999.999`  | `0.01`    | SSE  | 111 | 2023-06-21 .. 2026-07-29 |
| `1000000.0`  | `0.01`    | SZSE | 111 | 2020-09-01 .. 2023-06-08 |
| `999999.999` | `0.01`    | SZSE | 121 | 2023-06-21 .. 2026-08-05 |
| `99999.99`   | **`0.0`** | BSE  | 235 | 2022-02-28 .. 2026-08-05, unchanged |

SSE and SZSE each changed encoding once, both between 2023-06-08 and 2023-06-21, and SSE has an
older form still that a post-2019 sample does not reach.

The Beijing row is the one an SSE/SZSE sample does not show at all, and it is not rare:
**every** `.BJ` security's first trading session carries `(99999.99, 0.0)`, which is 235
distinct trading days at or after 2022-02-28 (64 in 2022, 75 in 2023, 23 in 2024, 26 in 2025,
47 in 2026, matching `stock_basic`'s `list_date` census exactly), and so does the first session
of a delisting arrangement (`920680.BJ`, 2025-12-11, between a 12.37/6.67 band on the 10th and
a 3.57/1.93 one on the 12th). Across the whole scan, **every** row with `down_limit <= 0` is a
`.BJ` code with a sentinel `up_limit` -- 254 of them, on those 235 sessions. A `down_limit > 0`
rule therefore refuses whole sessions -- 2024-02-02's entire 6,741-row cross section fails on
`920656.BJ` alone -- and, since `write_price_limits` needs every open session of a year at
once, whole years with them.

On SSE and SZSE the sentinel marks sessions 1..5 of a listing (`688086.SH` 4th, `301120.SZ`
5th, `301707.SZ` 1st, ...) and no security outside that window carried one. Before the
registration system the same situation was published as a **real** band: `300740.SZ` and
`603709.SH` listed 2018-02-08 with `up_limit` exactly 44% and `down_limit` exactly -36% of
`pre_close`.

`is_bounded` therefore separates the two populations by ratio rather than by a value whitelist,
and the two populations do not merely separate, they leave a **void**. Over all 1,918,266 rows
the widest real band is 1.4409x `pre_close` (`300830.SZ`, 2020-05-06) and the narrowest
sentinel is 115.61x (`688808.SH`, 2026-04-29, 99999.999 against an 864.99 close); **nothing at
all lands between them**. `LIMIT_FREE_RATIO` sits at 2.0, 39% above the first and 58x below the
second; see that constant for why the two margins are deliberately not the same size. Two of
those six encodings were unknown when the whitelist alternative was rejected, which is the
argument for the ratio test made concrete -- and even a reader that ignores the classification
entirely is correct, because `high >= 10000.0` and `low <= 0.01` are both false on any real
bar of a security that had no limit.

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

`daily ⊆ stk_limit` is likewise a **recent** property, not a general one, and "recent" is dated
rather than gestured at. A quarterly sweep of 2013-01..2026-07 puts the changeover between
**2022-12-26** (4 bars with no band, all `.BJ`) and **2023-01-03** (0), and every session
sampled from 2023-01-03 onward holds while every session before 2022-12-26 fails: 8 bars had no
published limit on 2022-11-01, 67 on 2022-01-04, 133 on 2021-10-08, 56 on 2020-01-02, 44 on
2016-01-04, 1 on 2013-01-04. All but one of those are `.BJ` codes -- `stk_limit` reached the
Beijing board later than `daily` did -- and the exception is `001914.SZ`, which is missing on
every session sampled from 2013-01-04 to 2022-04-01. So the property is not a 2024 discovery:
it has held for the whole of the panel's recent history and fails across a decade before that.
`stk_limit` also has a **horizon**: 0 rows on 2005-01-04 and 2006-01-04, 1,408 on 2007-01-04,
and `stk_limit(ts_code=000001.SZ)` returns 4,762 rows beginning 2007-01-04 against a bar
history that starts in 1991.

## Layering

Pure `datetime`/`dataclasses`/`enum` plus one sibling `domain` module -- `daily_prices` for the
shared `trade_date` column name and for `DailyBar`/`PricedCrossSection`, which are what a limit
and a halt are joined onto. No provider, no store, no clock; the same placement, and the same
reason, as `domain/daily_prices.py` itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
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

CLOSING_CALL_AUCTION_START: Final[time] = time(14, 57)
"""When the closing call auction opens, written the way `suspend_timing` writes a clock time.

`daily.close` is that auction's price on both exchanges, so a halt still running at this
instant means the published close is an earlier print that no order could have been filled at.
`halt_spans_the_close` is the question; `domain/labels.py` is the consumer that acts on it.
"""

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

SUSPENSION_CORPUS_FIRST_SESSION: Final[date] = date(1999, 5, 4)
"""The first session `suspend_d` carries a row for. Before it, the corpus is **empty**.

Measured, not inferred, and by a whole-history census rather than a probe: every session of
1991..1998 -- 2,006 of them, pulled in windows narrow enough that no response was truncated --
returns **zero** `suspend_d` rows, and the first row in the corpus is dated 1999-05-04 (72
sessions into that year). 1999 carries 1,263 whole-day halts over 157 of its 239 sessions, 2000
carries 3,008 over 234 of 239, and every later year is denser still.

This is what bounds `MIN_EXPLAINED_SESSION_SHARE`'s reach. On a session with no halt rows the
"explained cross section" is just the bar count, so the explained floor degenerates into a
second row-count floor at a much higher threshold -- a strictly stronger claim resting on
strictly no extra information. `panel_ingest._refuse_unexplained_thin_sessions` therefore skips
sessions before this date rather than judging them, and `MIN_SESSION_ROW_SHARE` keeps running
underneath on every session of every year.
"""

EXPLAINED_SESSION_HALF_WINDOW: Final[int] = 20
"""How many sessions on each side of a session its comparison median is drawn from.

A **rolling** median, not the partition's. The whole-year median is what made a 0.85 floor
unusable on early history, and the cause is not halts at all -- it is within-year listing
growth. 1996 opened with 313 bars and closed with 514 against a year median of 377, so its
January is 0.809 of a figure it only reaches in June, and 1997 (0.766) is the same shape. A
+/-20-session window compares a session against the market as it was that month: 1996's minimum
rises to 0.900 and 1997's to 0.935, with no loss anywhere later.

20 is a trading month either side, i.e. 41 sessions. Two properties of that width are worth
stating. It is **wide enough** that one thin session cannot drag its own comparison figure
down: a median over 41 values is unmoved by 20 of them, and a fetch would have to lose most of
a month before the window followed it. And it is **narrower than any partition this guard runs
on**, because a partition is a calendar year (~243 sessions). On a partition of 41 sessions or
fewer the window covers everything and the rolling median *is* the whole-partition median, so
this is exactly `V2-P1-008`'s original arithmetic on any small batch.
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
that session becomes 2,801 against a comparable median of 2,796 and stops being thin at all.

## The calibration is the whole history, because a three-year sample got this wrong

The first cut of this constant was set from full-year censuses of 2008, 2015 and 2018 -- and
0.85 clears all three comfortably. It also **refuses seven real years**. Every session of
1991-01-02..2026-08-07 has since been censused: one `daily` row count and one `suspend_d`
whole-day-halt count per session, **8,690 sessions across 36 years**, pulled in windows narrow
enough that no response was truncated (a window that came back at the cap was split and
re-pulled until none was).

    year   bars med  bars min/med  explained min/med   rolling min   n < 0.85
                                   (whole-year median)  (+/-20 sess)  (year / rolling)
    1991         10       0.300         0.300              0.429        85 / 26
    1992         30       0.200         0.200              0.462        86 /  6
    1993         98       0.347         0.347              0.596        89 /  7
    1994        270       0.607         0.607              0.605        47 /  3
    1995        296       0.578         0.578              0.586         7 /  7
    1996        377       0.809         0.809              0.900        39 /  0
    1997        670       0.766         0.766              0.935        73 /  0
    1998        781       0.919         0.919              0.959         0 /  0
    1999..2026  872..5493 0.578..0.993  0.906..0.997       0.963..1.000  0 /  0
      worst of those        2015          2010               2001

Two separate facts come out of that table and both are load-bearing.

**The whole-year median was the wrong comparison figure.** 1994..1997 fail a 0.85 whole-year
floor -- 47, 7, 39 and 73 sessions respectively -- and not one of those sessions is short. 1996
served 313 bars on 01-02, 305 on 05-28 and 514 on 12-31 against a year median of 377, with
`suspend_d` carrying **zero** rows for the whole year; the shortfall is the market growing
inside the year. `EXPLAINED_SESSION_HALF_WINDOW` is the fix, and it is enough from 1996 on.

**Before 1999 the halt corpus is empty**, so the explained share is the bar share and this
floor would be a bare row-count floor at 0.85 where `MIN_SESSION_ROW_SHARE` deliberately sits
at 0.5. That is what still binds 1991..1995 (0.429..0.605 on the rolling median), and it is why
the guard does not run before `SUSPENSION_CORPUS_FIRST_SESSION` rather than why the constant is
lower. 1991..1993 are in any case already refused by the 0.5 bar floor (0.300 / 0.200 / 0.347),
which is a `V2-P1-007` boundary this issue does not move; 1994 and 1995 pass that floor and now
pass this one too.

## Where 0.85 comes from, and what it leaves

Above it, the binding real session in the guard's whole range (1999-05-04 onward) is
**2001-05-14 at 0.963**, and no session of the 6,612 in that range falls under 0.85. So the
headroom is 11 points over the worst true partition of 27 years rather than a margin fitted to
three. Below it, the floor catches what 0.5 cannot: a session that came back with 85% of its
neighbours' cross section and no halts to show for it -- on a 2015-sized market a session
missing ~420 names rather than ~1,180. The 3-of-40 fetch this guard was written for is 0.075
and is nowhere near either.

2015 is worth looking at twice in that table, because it is the year `MIN_SESSION_ROW_SHARE`'s
0.5 exists for. Its bars alone reach 0.578; add the whole-day halts and its worst *whole-year*
share is 0.927; compare each session against its own month and its worst is 0.994. The week a
large part of the market halted at once stops being an outlier under all three readings once
the halts are counted, and what is left binding the year is January.

This is still a floor and not a census. A fetch that lost 5% of the market passes, and the
fully general answer is a per-name expectation, which needs the universe rather than a row
count -- `explain_unpriced` is where that question is asked, on the read side, where the
registry is in hand.

One hazard survives and points the safe way. Before 2015 `suspend_timing` is null throughout,
so `halted` *over*-counts (see `KNOWN_SUSPENSION_LIMITATIONS`), which inflates the explained
share and makes this floor **weaker** on 1999..2014 rather than stricter. The 0.5 row-count
floor still runs underneath, unconditionally, on every session of every year.
"""

LIMIT_FREE_RATIO: Final[float] = 2.0
"""At or above this multiple of the previous close, an `up_limit` is a "no limit" sentinel.

A ratio test rather than a whitelist of the six observed sentinel values, and the gap it sits
in is measured from both sides on the same scan: 459 whole-market sessions -- the first trading
day of every month from 2007-01 to 2026-08 plus every `.BJ` listing day since 2022-02-28 --
`stk_limit` joined onto `daily.pre_close`, **1,918,266 rows**.

**Below it, the widest real published band in that scan is 1.4409x** -- `300830.SZ` on
2020-05-06, its listing day under the 44% first-day rule, published as 6.34 against a 4.40
previous close. The next four are 1.4406, 1.4405, 1.4404, 1.4404, spread over 2015..2023, so
the ceiling is the *rule* rather than one outlier. The threshold leaves 39% of headroom over
it: enough for a first-day rule wider than 44%, not enough for an unbounded one.

**Above it, the narrowest sentinel in that scan is 115.61x** -- `688808.SH` on 2026-04-29, an
`up_limit` of 99999.999 against an 864.99 previous close, the highest-priced security found
carrying one. That is 58x of margin, not the 192x an earlier reading of this constant claimed;
the figure moves with the most expensive security that ever goes limit-free, so it is a
measurement rather than a bound.

**Between 1.4409x and 115.61x the scan contains nothing at all.** That void, not either
margin, is what makes the classification safe: any threshold in it gives the same answer on
every one of those 1.9 million rows, and 2.0 is chosen inside it rather than fitted to an edge.

The two margins are deliberately **not** symmetric, and the asymmetry is the right shape
because the two errors are not equivalent. Reading a real band as limit-free would widen a
bar's band to a number no price reaches and let an order fill on a locked bar. Reading a
sentinel as a real price is harmless in both consumers, because no bar satisfies
`high >= 10000.0` on a security that had no limit. See this module's docstring for the six
sentinel values and the two encoding changes among them.

The comparison is `<`, so a band of exactly twice the previous close is classified as
limit-free. Nothing published sits anywhere near that, and pinning the closed side keeps the
choice from drifting silently.
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

    **A bar is not a fill at the close.** 39 of the 59 timed rows served across 68 whole-market
    sessions were still halted when the closing call auction opened -- 30 of that 2015-07-08
    set among them -- so their `daily.close` is the last print before the halt began.
    `halt_spans_the_close` is that question, and it is the window rather than the state that
    answers it.
    """

    resumed = "resumed"
    """`R`: trading resumed that session. Expect a bar -- **usually**, and the exceptions are
    named rather than assumed away.

    An earlier reading of this said "every `R` row probed had a bar", which was true of the
    eight sessions probed for it and false of the market. A quarterly sweep of 2013-01..2026-07
    (55 whole-market sessions, `suspend_d` joined to `daily`) finds 7 sessions carrying an `R`
    row with no bar: 2015-01-05 (11 `R`, 10 with a bar; the odd one is `830879.BJ`), 2018-07-02
    (`835174.BJ`), 2019-04-01 (`430564.BJ`), 2020-01-02 (`830879.BJ`), 2022-10-10
    (`430685.BJ`), 2023-04-03 (`834261.BJ`) and 2023-07-03 (`836717.BJ`). Every one is a
    NEEQ-era `.BJ` code, which is to say a security `daily` did not carry that session at all
    (see `KNOWN_SUSPENSION_LIMITATIONS`' coverage entry), rather than a resumption that failed
    to trade.

    The consequence is benign in both consumers and that is *why* they are shaped this way:
    `explain_unpriced` and `_refuse_unexplained_thin_sessions` count only `halted`, so an `R`
    with no bar lands in `unexplained` -- an unexplained absence, which is the loud direction --
    instead of quietly excusing one.

    **A security is only `resumed` when the session's rows say nothing more specific.** An `S`
    row on the same session is the finer statement and wins; see `_reconcile_states`. That is
    not a loss of information, because `resumed` and `interrupted` agree on the one thing this
    enum is asked -- the security traded -- and the `S` row additionally carries the window.
    """

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
        code="a_resumption_and_a_halt_can_share_one_session",
        detail=(
            "One security can carry both an R row and an S row on one session, and the shape is "
            "systematic rather than a fetch accident: every session of 2015..2026 was censused "
            "in windows narrow enough that no response truncated -- 334,362 rows, 334,289 "
            "distinct (security, session) pairs -- and 73 of those pairs carry two rows. All 73 "
            "are exactly one R and one S; none carries three, and no pair anywhere in the census "
            "carries two S rows. They cluster on resumption days and on the recent years: 1 in "
            "2015, 1 in 2018, none in 2016/2017/2019/2020, then 11, 13, 13, 8, 9 and 17 in "
            "2021..2026. build_suspension_day used to refuse this as 'two sources that were "
            "never reconciled', which the census disproves -- it is one suspend_d request for "
            "one session. _reconcile_states now prefers the S row and lets its suspend_timing "
            "decide, which is the ordinary rule rather than a new one, and the bars agree: 26 of "
            "the 73 are .BJ codes daily carries nowhere in that year (the coverage gap this "
            "table's stk_limit entry already names, so they say nothing about the state), and on "
            "the 47 daily does carry, the S row's own timing predicts the bar 45 times. The two "
            "misses are both in the safe direction and are named: 603003.SH on 2025-06-10 has an "
            "untimed S and traded, so it is called halted when it was not -- which inflates "
            "_refuse_unexplained_thin_sessions' explained count by one name and makes labels.py "
            "refuse a session it could have labelled; and 688766.SH on 2025-11-26 carries the "
            "zero-width window '09:30-09:30' and did not trade, so it is called interrupted and "
            "lands in explain_unpriced's unexplained residue, which is the loud direction. That "
            "second shape is not about R at all -- 688005.SH carries a lone '09:30-09:30' on "
            "2026-01-16 with no R beside it and no bar -- so a zero-width window is a spelling "
            "of a whole-day halt that the timed/untimed split reads as intraday, independently "
            "of this entry."
        ),
    ),
    SuspensionLimitation(
        code="stk_limit_starts_in_2007_and_reached_the_beijing_board_late",
        detail=(
            "stk_limit has a horizon and a coverage gap, both measured. It serves 0 rows for "
            "2005-01-04 and 2006-01-04 and 1,408 for 2007-01-04, and "
            "stk_limit(ts_code=000001.SZ) returns 4,762 rows beginning 2007-01-04 against a bar "
            "history that starts in 1991 -- so no published band exists for the first sixteen "
            "years of the market. Separately, daily is a subset of stk_limit only from "
            "2023-01-03: a quarterly sweep of 2013-01..2026-07 brackets the changeover between "
            "2022-12-26 (4 bars with no band) and 2023-01-03 (0), with 133 unbanded bars on "
            "2021-10-08, 56 on 2020-01-02, 44 on 2016-01-04 and 1 on 2013-01-04. Every one of "
            "those but 001914.SZ is a .BJ code. So a historical partition legitimately carries "
            "bars with no band, and limit_touch is asked per security rather than being "
            "derived for a whole cross section. The same coverage gap is why a suspend_d R row "
            "can have no bar on 7 of those 55 sessions -- all NEEQ-era .BJ codes."
        ),
    ),
    SuspensionLimitation(
        code="the_limit_free_sentinel_encoding_has_changed_twice",
        detail=(
            "A security with no price limit is published as an out-of-range up_limit, and the "
            "encoding is neither stable nor single. A 459-session scan finds SIX values on "
            "three exchanges: SSE served 10000.0 on 2007-01-04, 100000.0 from 2019-10-08 and "
            "99999.999 from 2023-06-21; SZSE served 1000000.0 from 2020-09-01 and 999999.999 "
            "from 2023-06-21 (both exchanges changed between 2023-06-08 and 2023-06-21); and "
            "the Beijing board has published 99999.99 unchanged since 2022-02-28. The floor "
            "moves too -- SSE and SZSE pair the sentinel with down_limit=0.01 and BSE with "
            "down_limit=0.0, which is why the stored domain of that column includes zero (see "
            "providers/tushare.py::_lower_limit_price). is_bounded tests the ratio to the "
            "previous close rather than the value: a whitelist written from the four values "
            "this repository knew first would misread two of the six, and a seventh is a "
            "schema change away. A reader that ignores the classification is still correct -- "
            "no real bar satisfies high >= 10000.0 or low <= 0.01 -- so this shape is fail-safe "
            "in both consumers rather than merely disclosed."
        ),
    ),
    SuspensionLimitation(
        code="the_halt_corpus_is_empty_before_1999_so_the_explained_floor_cannot_run_there",
        detail=(
            "suspend_d returns zero rows for every one of the 2,002 sessions of 1991..1998 and "
            "its first row is dated 1999-05-04; 1999 then carries 1,263 whole-day halts over "
            "157 of its 239 sessions and every later year is denser. So on a pre-1999 session "
            "the explained cross section is exactly the bar count, and "
            "panel_ingest._refuse_unexplained_thin_sessions would be a bare row-count floor at "
            "MIN_EXPLAINED_SESSION_SHARE sitting on top of one deliberately set to 0.5. It "
            "does not run before SUSPENSION_CORPUS_FIRST_SESSION for that reason: applied to "
            "1994 and 1995 it refuses true partitions (a rolling-median minimum of 0.605 and "
            "0.586, where the shortfall is the market growing inside the year rather than a "
            "short fetch). MIN_SESSION_ROW_SHARE still runs on every session of every year, "
            "and it independently refuses 1991..1993 (0.300 / 0.200 / 0.347), which is a "
            "V2-P1-007 boundary this dataset does not move."
        ),
    ),
    SuspensionLimitation(
        code="the_published_band_is_not_reproducible_from_board_and_st_alone",
        detail=(
            "Measured on 2024-06-28 against all 5,338 priced names, a board-plus-is_st rule "
            "disagrees with the published up_limit on 159 of them -- with is_st taken as "
            "RiskWarning.st/star_st/pt (a delisting-arrangement name keeps the ordinary board "
            "band, and counting it as ST gives 161) and 302* read as ChiNext (reading it as "
            "main board gives 162) -- for four independent "
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


def halt_spans_the_close(timing: str) -> bool:
    """Whether a timed `S` row's halt was still running at the closing call auction.

    `suspend_timing` is one or more comma-separated `HH:MM-HH:MM` halt windows -- the spans the
    security did **not** trade in -- and the answer is `True` when any of them runs to
    `CLOSING_CALL_AUCTION_START` or beyond.

    **The shape this exists for is the common one.** A sweep of 68 whole-market sessions (the
    first open session of each quarter from 2013-01-04 to 2026-10-08, plus the sessions this
    module probed for the three-state split) served 59 timed `S` rows, and **39 of them run
    through the close**. The single most common spelling is `13:00-15:00`: halted from the
    lunch break to the bell, so `daily.close` is the last print before 13:00. On 2015-07-08
    alone, **30 of the 31** timed rows are that shape, and all 31 have a bar -- which is why
    `TradingState.interrupted` is right that they traded and why "traded" is not the same
    question as "could have been bought or sold at the close".

    A window ending exactly at 14:57 counts as spanning it. Whether such a security actually
    took part in that session's auction is not something `suspend_timing` says, and reading the
    boundary as "resumed in time" is the fail-open direction for anything that prices at the
    close. The two measured rows at that boundary are 2020-02-03's `13:36-13:46,14:53-14:57`
    and 2022-04-25's `14:52-14:57`.

    An unparseable window is also `True`, for the same reason: a right endpoint this cannot
    read is a right endpoint that is unknown.
    """
    for window in timing.split(","):
        ends = window.split("-")
        if len(ends) != 2:
            return True
        try:
            finish = time.fromisoformat(ends[1].strip())
        except ValueError:
            return True
        if finish >= CLOSING_CALL_AUCTION_START:
            return True
    return False


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
    timings: tuple[tuple[str, str], ...]
    """`(ts_code, suspend_timing)` for every `interrupted` name, sorted by code.

    The column itself rather than the verdict derived from it, because "the security traded"
    and "the security could have been traded at the close" are different questions and only the
    first is answerable from `TradingState`. Pairs rather than a mapping so the value stays
    hashable and deterministic like the three tuples beside it; `timing_of` is the lookup.
    """

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

    def timing_of(self, ts_code: str) -> str | None:
        """This security's `suspend_timing` window, or `None` when it has no timed halt.

        `None` for all three of "not mentioned", "resumed" and "halted for the whole session",
        because none of those carries a window: a caller asking this question has already
        established that the row is `TradingState.interrupted`.
        """
        for code, timing in self.timings:
            if code == ts_code:
                return timing
        return None

    def is_halted(self, ts_code: str) -> bool:
        """Whether this security was halted for the **whole** session.

        The one boolean this contract does offer, because it is the question a cross section
        asks and because its two false cases ("traded" and "not mentioned") mean the same thing
        to that caller.

        **A warning for the consumer that does not exist yet.** Both of today's consumers use a
        halt to *excuse* a missing bar, which is why `suspend_d`'s descriptor can decline
        `requires_truncation_flag`: a dropped row excuses fewer absences and raises more alarms.
        A consumer that fed this into `MarketBar.suspended` would invert that. A dropped row
        would then make a halted security look tradeable and let an order fill on a session
        with no market -- fail-open, in a dataset whose truncation witness is deliberately the
        weaker one. Wiring these two together therefore needs `requires_truncation_flag` turned
        on for `suspend_d` first, not merely a call to this method.
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


def _reconcile_states(
    ts_code: str, day: date, first: TradingState, second: TradingState
) -> TradingState:
    """The one state a security held on `day`, given two rows that derive different ones.

    **`resumed` yields to whichever `S` state stands beside it, and that is not a tie-break --
    it is the ordinary rule applied to the row that answers the question.** A session's `R` row
    says trading resumed; an `S` row on that same session says how much of it the security
    traded, which is strictly the finer statement, and `suspend_timing` already decides between
    `halted` and `interrupted` when no `R` is present. Nothing is discarded that the three
    states could have carried: `resumed` and `interrupted` both mean "the security traded", so
    the pair `(R, timed S)` loses no fact by resolving to `interrupted` and gains the window.

    `halted` against `interrupted` is a different shape and stays a refusal. Two `S` rows for
    one security, one untimed and one timed, disagree about the same question with nothing
    finer to appeal to -- and the corpus has never served it (see
    `KNOWN_SUSPENSION_LIMITATIONS`' `a_resumption_and_a_halt_can_share_one_session`, which
    censuses every multi-row pair of 2015..2026 and finds all 73 of them `R`-plus-`S`).

    Commutative by construction, because `build_suspension_day` folds a session's rows in
    whatever order they arrive and a state that depended on that order would make a partition's
    meaning depend on how it was fetched.
    """
    if first is second:
        return first
    if first is TradingState.resumed:
        return second
    if second is TradingState.resumed:
        return first
    raise SuspensionError(
        f"{ts_code} is both {first.name} and {second.name} on {day.isoformat()}; one untimed "
        "S row says the whole session and a timed one says part of it, and unlike an R beside "
        "an S there is no finer row to prefer -- the same response carries both and nothing in "
        "the corpus decides between them"
    )


def build_suspension_day(day: date, records: Iterable[SuspensionRecord]) -> SuspensionDay:
    """Assemble one session's `SuspensionDay` from its rows, in any order.

    Refuses, rather than repairs, four things: a record for another session, an unknown
    `suspend_type`, a blank `suspend_timing` (which would read as an untimed halt while
    carrying a column the upstream populated), and one security appearing twice with two
    different halt windows. Byte-identical duplicates collapse, following `build_name_history`:
    a duplicate carries no fact the original does not, and one live `namechange` pull returned
    380 of them.

    A fifth shape used to be refused and is now **reconciled**: a security carrying both an `R`
    row and an `S` row for one session. That refusal named "two sources that were never
    reconciled", and a census of the live endpoint disproved the attribution -- one
    `suspend_d` request for one session serves both rows, 73 times over 2015..2026.
    `_reconcile_states` states the rule and `KNOWN_SUSPENSION_LIMITATIONS`'
    `a_resumption_and_a_halt_can_share_one_session` carries the measurement.
    """
    _require_plain_date(day, "day")
    states: dict[str, TradingState] = {}
    timings: dict[str, str] = {}
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
        states[record.ts_code] = (
            state if existing is None else _reconcile_states(record.ts_code, day, existing, state)
        )
        if state is TradingState.interrupted and timing is not None:
            recorded = timings.get(record.ts_code)
            if recorded is not None and recorded != timing:
                raise SuspensionError(
                    f"{record.ts_code} is halted {recorded!r} and {timing!r} on "
                    f"{day.isoformat()}; one row carries every window a session has (the "
                    "corpus spells a two-window halt '13:36-13:46,14:53-14:57'), so two "
                    "disagreeing windows are two sources that were never reconciled"
                )
            timings[record.ts_code] = timing
    return SuspensionDay(
        day=day,
        halted=tuple(sorted(c for c, s in states.items() if s is TradingState.halted)),
        interrupted=tuple(sorted(c for c, s in states.items() if s is TradingState.interrupted)),
        resumed=tuple(sorted(c for c, s in states.items() if s is TradingState.resumed)),
        timings=tuple(sorted(timings.items())),
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
        down = _stored_lower_limit(down_limit, index, DOWN_LIMIT_COLUMN)
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
    """A stored `up_limit`: a finite **positive** `float`, exactly. `type(...) is float` for
    `_require_price`'s reason.

    An upper limit of zero or below would bound every price out of existence, and a `None`
    here would compare `False` against every bar and silently report a security as never
    touching its band. The **lower** limit is a different domain -- see `_stored_lower_limit`.
    """
    if type(value) is not float or not isfinite(value) or value <= 0.0:
        raise SuspensionError(
            f"row {index}: {column} must be a finite positive float, got "
            f"{type(value).__name__} {value!r}"
        )
    return value


def _stored_lower_limit(value: object, index: int, column: str) -> float:
    """A stored `down_limit`: a finite **non-negative** `float`, exactly.

    `_stored_price`'s rule with one value added to the domain, and that value is published
    rather than hypothetical: the Beijing board writes "no lower bound" as `down_limit` of
    exactly `0.0` beside an `up_limit` of `99999.99`, on every `.BJ` security's first trading
    session since 2022-02-28 and on the first session of a delisting arrangement. Rejecting it
    costs 235 whole sessions of `stk_limit` and, because a partition is written a year at a
    time, every year from 2022 on. See `providers/tushare.py::_lower_limit_price`.

    Zero needs no branch anywhere downstream: `limit_touch` asks `bar.low <= down_limit`,
    which is false for every real bar, so an unbounded security correctly never touches its
    floor, and `PriceLimit.is_bounded` classifies on the *upper* ratio, which is 5,025x on
    that same row. A negative is still refused -- no price floor is below zero, and a small
    negative beside a real `up_limit` would pass the `down > up` check that follows.
    """
    if type(value) is not float or not isfinite(value) or value < 0.0:
        raise SuspensionError(
            f"row {index}: {column} must be a finite non-negative float, got "
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
