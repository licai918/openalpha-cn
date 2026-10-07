"""Index level series: the market return this panel had no regressor for (`V2-P3-016`).

`V2-P3-013` shipped a volatility family and could not ship the two factors its roadmap line
named. The reason was measured rather than argued, and it was two independent facts: **no
declared dataset carried an index's level** (`index_weight` is a constituent *weight*), and
`FactorWindow` carries one security's rows so an evaluator could not have reached a market
series even if one had been stored. This module answers the first of them. `panel_factors.py`'s
`SHARED_SUBJECT_DATASETS` answers the second.

## What `index_daily` serves, measured on 2026-08-17

One request is one `(index, calendar year)` window -- `{ts_code, start_date, end_date}` -- and
that is a fact about the endpoint rather than a preference. `ts_code="000300.SH,000905.SH"`
returns **zero** rows, not the union and not an error, which is `_index_weight_params`' finding
on a second endpoint. The other axis the endpoint offers is `trade_date`, and it is the wrong
one: a bare `index_daily(trade_date=20260630)` returns **8,000 rows with `has_more=True`**
across 8,000 distinct `ts_code`s, because Tushare serves thousands of indices and this panel
wants three.

A year of one index is **243 rows** (2025) against a measured cap of **8,000**, so one request
is one index-year with two orders of magnitude of headroom -- and one index-year is one
partition's worth of one subject, which is the granularity `PanelStore` stores at.

## The three indices, and why the base-date row is not a defect

`INDEX_PRICE_INDEX_CODES` is `INDEX_WEIGHT_INDEX_CODES`: the same 沪深300 / 中证500 / 中证1000
`V2-P1-009` measured, so a level and a composition are answerable for the same index or for
neither. Every one of them was probed over its whole published history on 2026-08-17:

| index | bars | first | base date / point | list date |
|---|---|---|---|---|
| `000300.SH` | 5,972 | 2002-01-04 | 2004-12-31 / 1000 | 2005-04-08 |
| `000905.SH` | 5,252 | 2004-12-31 | 2004-12-31 / 1000 | 2007-01-15 |
| `000852.SH` | 5,252 | 2004-12-31 | 2004-12-31 / 1000 | 2014-10-17 |

Every one of them is published **years before it listed**, back-computed from the base date, and
the back-computed rows are **not the same shape as the live ones**. That is what
`INDEX_DAILY_NULLABLE_COLUMNS` exists to say, and the measurement is per column rather than per
row:

- `000300.SH` serves 721 bars (2002-01-04..2004-12-31) with `open`, `high` and `low` **null** --
  a close-only reconstruction -- and non-null on all 5,251 bars from 2005-01-04.
- The base-date row 2004-12-31 is a synthetic 1,000.00 point: `pre_close`, `pct_chg`, `vol` and
  `amount` are null on it for all three, and `open`/`high`/`low` too for `000905.SH`.
- `000300.SH`'s own first bar (2002-01-04) has a null `pre_close` and `pct_chg` for the ordinary
  reason: there is no session before it.
- **`close` is the one column that is never null and never non-positive**, on all 16,476 bars of
  the three histories. So it is the only required one, and a level series is exactly what this
  module exists to serve.

`pct_chg` is **nullable and signed**, which is worth stating because it is the one column a
`_positive_price` parse would silently be wrong about: 2,872 of `000300.SH`'s 5,972 bars are
negative and every one of them is an ordinary down day.

## The return path, and the one place it differs from `domain/daily_prices.py`

`daily.pre_close` is the previous close *restated for that morning's corporate action*, which is
why `close / pre_close - 1` is the only correct session return for a security and why
`close[t] / close[t-1] - 1` reverses the sign across an ex-rights morning (+2.7422% against
-0.5310% on `000001.SZ`'s 2026-06-12). **An index has no such morning**, and that is measured
rather than assumed: across the whole published history of all three indices,
`pre_close[t] == close[t-1]` on **every one of 15,753 adjacent pairs** (5,251 + 5,251 + 5,251),
so the two paths agree to 0.0 -- exactly, not approximately.

The same path is used anyway, and the reason is not caution. A residual volatility regresses a
security's returns on the market's, and a factor whose two sides were built from two different
definitions of "a session return" would be measuring part of that difference. So both sides read
`close / pre_close - 1`, and both reconcile against the row's own `pct_chg` --
`MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT` is the bound here and it is **tighter** than
`daily_prices.MAX_PUBLISHED_RETURN_DISAGREEMENT` because it is derived rather than sampled:
`pct_chg` is published to four decimals of a percent, so half a unit in the last place is
5e-7, and the largest disagreement measured over 16,476 bars is 4.99995e-7.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Final

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    PRE_CLOSE_COLUMN,
    PRICE_DATE_COLUMN,
)
from openalpha_cn.domain.index_membership import (
    CSI300_INDEX_CODE,
    INDEX_WEIGHT_INDEX_CODES,
)
from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME

INDEX_DAILY_DATASET: Final[str] = "index_daily"
"""The panel dataset (and partition directory) index levels are stored under.

Declared here rather than in `providers/tushare.py` for the reason `INDEX_WEIGHT_DATASET` is:
`panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

MARKET_INDEX_CODE: Final[str] = CSI300_INDEX_CODE
"""The one index a factor evaluator can reach, and the scope choice that name records.

`panel_factors.SHARED_SUBJECT_DATASETS` maps `index_daily` to exactly this code, so "the market"
is 沪深300 for every factor in this build and a factor cannot ask for a different one. Widening
that would mean putting the index on `FactorDefinition`, which is the model `factor_id` is the
content address of -- every one of the twenty shipped ids would move for a field nineteen of
them would leave at its default. The bound is therefore deliberate and is stated here rather
than discovered: this build can regress on 沪深300 and on nothing else.

沪深300 rather than 中证500 or 中证1000 because it is the broadest of the three by
capitalisation and the only one whose published history reaches back to 2002 (the other two
begin at their common 2004-12-31 base point), and because it is the A-share market proxy the
literature this family's factors come from uses.
"""

INDEX_PRICE_INDEX_CODES: Final[tuple[str, ...]] = INDEX_WEIGHT_INDEX_CODES
"""Which indices `panel build --dataset index_daily` fetches: the same three `index_weight` does.

Not a limit on what the descriptor accepts -- `_index_daily_params` takes whatever `ts_code` it
is handed. What is bounded by this tuple is the *evidence* and the *pairing*: the cap, the
nullability and the return-path measurements in this module's docstring were all taken on these
three, and a stored level for an index whose composition this panel cannot answer (or the other
way round) would be a half-built pair nothing names.
"""

INDEX_PRICE_COLUMNS: Final[tuple[str, ...]] = (
    "open",
    "high",
    "low",
    CLOSE_COLUMN,
    PRE_CLOSE_COLUMN,
)
"""The five level columns, in the order the response serves them."""

INDEX_DAILY_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    *INDEX_PRICE_COLUMNS,
    "pct_chg",
    "vol",
    "amount",
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

The same nine names `DAILY_DATA_COLUMNS` carries and for the same reasons: `change` is
`close - pre_close` and carries nothing the two of them do not, and `pct_chg` is kept even
though it is derivable because it is the upstream's own statement of the session return and
therefore the independent witness `index_session_returns` reconciles against.
"""

INDEX_DAILY_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *INDEX_DAILY_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back.

The subject is the **index**, `INDEX_WEIGHT_PANEL_COLUMNS`' choice and for its reason:
`PanelStore`'s key is `(dataset, year)` with no index dimension, so three indices sharing a year
are told apart by this column alone and `panel_ingest._refuse_to_drop_stored_subjects` compares
exactly it.
"""

INDEX_DAILY_NULLABLE_COLUMNS: Final[frozenset[str]] = frozenset(
    {"open", "high", "low", PRE_CLOSE_COLUMN, "pct_chg", "vol", "amount"}
)
"""Seven of the eight data columns may be null, and the eighth may not. Measured, not assumed.

`close` is the exception and it is the whole point of the dataset: **16,476 bars across the
three indices' entire published histories carry a finite positive `close`, and not one carries a
null or a non-positive one**. Everything else has a measured null, and every one of them comes
from the same fact -- these series are published *before* the index existed, back-computed from
a base point:

- `open` / `high` / `low`: null on `000300.SH`'s 721 pre-2005 bars (a close-only
  reconstruction) and on `000905.SH`'s base-date row. Never null after 2005-01-04 for any of
  the three.
- `pre_close` / `pct_chg`: null on each index's own first bar, which has no session before it.
- `vol` / `amount`: null on the synthetic 2004-12-31 base row of all three.

A stricter parse would have refused whole partitions of real published history, and the version
of this claim that mattered is the *negative* one: a `_positive_price` parse over `pct_chg`
would have refused 2,872 of `000300.SH`'s 5,972 bars, every one of them an ordinary down day.
"""

MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT: Final[float] = 1e-6
"""How far `close / pre_close - 1` and `pct_chg / 100` may be apart on one index bar.

Derived rather than sampled, and then checked against the sample. `pct_chg` is published to four
decimals of a percent, so the most a correctly rounded value can differ from the exact quotient
is half a unit in that last place: `0.5e-4 %` = **5e-7**. The largest disagreement measured over
the 16,476 bars of the three indices' whole histories is **4.99995e-7**, which is that bound and
not a coincidence.

Two orders of magnitude tighter than `daily_prices.MAX_PUBLISHED_RETURN_DISAGREEMENT` (1e-4),
and that difference is real rather than an accident of sampling: a security's `pre_close` is
restated for corporate actions and the two statements of its return are reconciled across that
restatement, while an index's is not restated at all.
"""


class IndexPriceError(ValueError):
    """Raised for any malformed index level row, or any malformed question about one.

    A `ValueError` subclass to match `domain/daily_prices.py`'s `PriceDataError` and
    `domain/index_membership.py`'s `IndexMembershipError`.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexPriceLimitation:
    """One named boundary on what a stored index level panel can be trusted to answer."""

    code: str
    detail: str


KNOWN_INDEX_PRICE_LIMITATIONS: Final[tuple[IndexPriceLimitation, ...]] = (
    IndexPriceLimitation(
        code="the_series_is_back_computed_before_the_index_listed",
        detail=(
            "index_daily serves every one of the three indices years before it listed, "
            "back-computed from a 2004-12-31 base point of 1000. 000300.SH lists 2005-04-08 "
            "and is served from 2002-01-04 (5,972 bars); 000905.SH lists 2007-01-15 and "
            "000852.SH 2014-10-17, and both are served from 2004-12-31 (5,252 bars each). "
            "Those rows are a reconstruction rather than a quotation: nobody could have traded "
            "against them at the time, and a factor whose window reaches into them is reading a "
            "series computed with hindsight about which securities the index would later hold. "
            "Nothing here refuses them -- the panel stores what the publisher publishes -- and "
            "the shape is visible in the data itself, which is what "
            "INDEX_DAILY_NULLABLE_COLUMNS records: 000300.SH's 721 pre-2005 bars carry a close "
            "and no open, high or low at all."
        ),
    ),
    IndexPriceLimitation(
        code="an_index_pre_close_is_the_previous_close_and_a_securitys_is_not",
        detail=(
            "domain/daily_prices.py's central measurement is that daily.pre_close is the "
            "previous close RESTATED for that morning's corporate action, so "
            "close[t]/close[t-1] - 1 reverses the sign across an ex-rights day (000001.SZ, "
            "2026-06-12: +2.7422% against -0.5310%). None of that is true here, and it is "
            "measured rather than assumed: across the whole published history of all three "
            "indices, pre_close[t] equals close[t-1] on every one of 15,753 adjacent pairs and "
            "the two return paths agree to exactly 0.0. So a reader who computed an index "
            "return the naive way would get the right answer today, and would be relying on a "
            "property of index construction that no assertion in this repository holds. "
            "index_session_returns uses close/pre_close - 1 regardless, because the security "
            "side of a residual regression uses it and a regression whose two sides define a "
            "return differently is measuring part of that difference."
        ),
    ),
    IndexPriceLimitation(
        code="the_market_is_one_index_because_a_factor_cannot_name_another",
        detail=(
            "MARKET_INDEX_CODE is 000300.SH and panel_factors.SHARED_SUBJECT_DATASETS maps the "
            "dataset to that one code, so every factor in this build regresses on 沪深300 and "
            "no factor can ask for 中证500 or 中证1000 even though both are fetched and stored "
            "beside it. That is a scope choice with a stated price rather than a limitation of "
            "the endpoint: letting a factor name its index would mean a new field on "
            "FactorDefinition, which is the model factor_id is the content address of, so every "
            "one of the twenty already-shipped factor ids would move for a field nineteen of "
            "them would leave at its default."
        ),
    ),
    IndexPriceLimitation(
        code="the_whole_market_axis_of_this_endpoint_exceeds_its_own_cap",
        detail=(
            "index_daily caps at 8,000 rows, measured 2026-08-17: a bare "
            "index_daily(trade_date=20260630) returns exactly 8,000 with has_more=True, and "
            "limit=8001 / 10000 / 12000 all return the same 8,000 while limit=100 returns 100 "
            "-- so limit narrows only. A single index-year is 243 rows and never approaches it, "
            "which is why this panel fetches by (index, year). A caller who reached for the "
            "cross-section axis instead would be truncated on the first request, and 000001.SH "
            "上证综指 is already past the cap on its own whole-history window (8,000 rows, "
            "has_more=True) because it is served from 1990."
        ),
    ),
)
"""Named boundaries on what a stored index level panel answers, each measured on real data.

**Not an enumeration of every way this dataset could be wrong.** These are the ones a live probe
of the endpoint could demonstrate on 2026-08-17.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexBar:
    """One index's level and turnover on one session.

    A plain carrier with no validation of its own, following `DailyBar`'s precedent: a nominal
    type is not a boundary, so the rules live once, in `index_bars_from_panel_rows`.
    """

    ts_code: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    pre_close: float | None
    pct_chg: float | None
    vol: float | None
    amount: float | None

    @property
    def published_return(self) -> float | None:
        """`close / pre_close - 1`, or `None` on a bar with no previous session.

        `None` rather than a raise, because the bar it is `None` on is a real published row --
        each index's own first, and each index's synthetic base-date row. A series that refused
        to exist because its oldest member has no predecessor would refuse the whole history.
        """
        if self.pre_close is None or self.pre_close == 0.0:
            return None
        return self.close / self.pre_close - 1.0

    @property
    def upstream_return(self) -> float | None:
        """`pct_chg / 100`: the upstream's own statement of the same session return."""
        if self.pct_chg is None:
            return None
        return self.pct_chg / 100.0


def index_bars_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[str, tuple[IndexBar, ...]]:
    """Rebuild stored `index_daily` rows into one ascending bar series per index.

    Positionally bound to `INDEX_DAILY_PANEL_COLUMNS`, which is the projection every reader asks
    for, so "the writer accepted it" and "the reader can return it" are one question --
    `daily_bars_from_panel_rows`' contract, with two differences that are both properties of this
    dataset rather than relaxations:

    - **Many sessions rather than one.** A price partition is read one cross section at a time
      because a caller wants the market on a day; a level partition is read as a *series*,
      because that is the only shape a regressor has. So the return is per index and ascending
      rather than per security and single-session.
    - **Seven columns may be null.** See `INDEX_DAILY_NULLABLE_COLUMNS`, which is measured. Only
      `close` is required, and it is required to be finite and strictly positive.

    Refuses, by raising `IndexPriceError`: a row of the wrong width, a blank index code, a
    `trade_date` that is not an ISO date, a null or non-positive `close`, a non-finite number in
    any column, and two rows for one `(index, session)`. That last one is the guard the whole
    series rests on -- a duplicated session would put two returns where the market had one and
    silently change every regression's sample size.
    """
    collected: dict[str, dict[date, IndexBar]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(INDEX_DAILY_PANEL_COLUMNS):
            raise IndexPriceError(
                f"index level row {index} has {len(row)} values and "
                f"{len(INDEX_DAILY_PANEL_COLUMNS)} are required "
                f"({', '.join(INDEX_DAILY_PANEL_COLUMNS)})"
            )
        ts_code = _required_code(row[0], index)
        trade_date = _parse_iso_date(row[1], index)
        bar = IndexBar(
            ts_code=ts_code,
            trade_date=trade_date,
            open=_optional_level(row[2], index, "open"),
            high=_optional_level(row[3], index, "high"),
            low=_optional_level(row[4], index, "low"),
            close=_required_level(row[5], index, CLOSE_COLUMN),
            pre_close=_optional_level(row[6], index, PRE_CLOSE_COLUMN),
            pct_chg=_optional_number(row[7], index, "pct_chg"),
            vol=_optional_number(row[8], index, "vol"),
            amount=_optional_number(row[9], index, "amount"),
        )
        series = collected.setdefault(ts_code, {})
        if trade_date in series:
            raise IndexPriceError(
                f"{ts_code} has more than one stored level for {trade_date.isoformat()}; a "
                "duplicated session would put two returns where the market had one and change "
                "the sample size of every regression that reads the series"
            )
        series[trade_date] = bar
    return {
        code: tuple(bars[day] for day in sorted(bars)) for code, bars in sorted(collected.items())
    }


def index_session_returns(bars: Sequence[IndexBar]) -> tuple[float, ...]:
    """One market return per bar, `close / pre_close - 1`, reconciled against `pct_chg`.

    The same construction `panel_factors._session_returns` runs on a security, so the two sides
    of a residual regression define a session return identically -- which is the whole reason
    this function exists here rather than being open-coded wherever a market series is wanted.

    Refuses a bar whose two statements of its own return disagree by more than
    `MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT`, and refuses a bar that cannot state one at all: a
    `None` `pre_close` has no return, and silently dropping it would shorten the series without
    telling the caller the window it asked for is not the window it got.
    """
    returns: list[float] = []
    for bar in bars:
        published = bar.published_return
        if published is None:
            raise IndexPriceError(
                f"{bar.ts_code} has no computable return on {bar.trade_date.isoformat()}: its "
                "pre_close is absent or zero, which is the shape of an index's own first bar "
                "and of its synthetic base-date row"
            )
        upstream = bar.upstream_return
        if upstream is not None and abs(published - upstream) > (
            MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT
        ):
            raise IndexPriceError(
                f"{bar.ts_code} on {bar.trade_date.isoformat()} states its own return twice and "
                f"the two disagree by {abs(published - upstream)!r}, over the "
                f"{MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT!r} half-a-unit-in-the-last-published-"
                "place bound; close/pre_close says "
                f"{published!r} and pct_chg says {upstream!r}"
            )
        returns.append(published)
    return tuple(returns)


def _required_code(value: object, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndexPriceError(f"index level row {index} carries no index code")
    return value


def _parse_iso_date(value: object, index: int) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise IndexPriceError(
            f"index level row {index} has a {PRICE_DATE_COLUMN} of type "
            f"{type(value).__name__}; an ISO date string is required"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise IndexPriceError(
            f"index level row {index} has a {PRICE_DATE_COLUMN} of {value!r}, which is not an "
            "ISO date"
        ) from error


def _optional_number(value: object, index: int, column: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise IndexPriceError(
            f"index level row {index} has a {column} of type {type(value).__name__}; a number "
            "or nothing is required"
        )
    number = float(value)
    if not isfinite(number):
        raise IndexPriceError(f"index level row {index} has a non-finite {column} of {number!r}")
    return number


def _optional_level(value: object, index: int, column: str) -> float | None:
    """A level column that may be absent, and must be strictly positive when it is not.

    Absent is ordinary here and non-positive is not: `INDEX_DAILY_NULLABLE_COLUMNS` records
    which of the pre-listing rows carry nulls, and none of the 16,476 bars measured carries a
    level at or below zero.
    """
    number = _optional_number(value, index, column)
    if number is not None and number <= 0.0:
        raise IndexPriceError(
            f"index level row {index} has a {column} of {number!r}; an index level is strictly "
            "positive when it is published at all"
        )
    return number


def _required_level(value: object, index: int, column: str) -> float:
    number = _optional_level(value, index, column)
    if number is None:
        raise IndexPriceError(
            f"index level row {index} has no {column}; it is the one column of the eight that "
            "is never null in any of the 16,476 bars measured, and a series with no level is "
            "not a series"
        )
    return number
