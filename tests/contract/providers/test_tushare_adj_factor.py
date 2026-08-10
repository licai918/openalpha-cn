"""`adj_factor` on the descriptor table, and the truncation guard (`V2-P1-006`).

No network. Every shape below is one a live Tushare endpoint actually produced on
2026-08-08, and the row counts are the measured ones.

## The measurement this file is built on

    q(adj_factor, ts_code=000001.SZ)                       -> 6000 rows, has_more=True
    q(adj_factor, ts_code=000001.SZ, 19910101..20260808)   -> 6000 rows, has_more=True
    q(adj_factor, ts_code=000001.SZ, 19910101..20011113)   -> 2627 rows, has_more=False
    q(adj_factor, ts_code=000001.SZ, 20011114..20260808)   -> 6000 rows, has_more=True
    q(adj_factor, ts_code=000001.SZ, 20011115..20260808)   -> 5999 rows, has_more=False
    q(adj_factor, trade_date=20240628)                     -> 5387 rows, has_more=False

The true history is 8,627 rows starting 1991-04-03; the capped response starts 2001-11-14, so
**the rows the cap drops are the oldest ones**, and a factor series missing its first decade
produces silently wrong returns rather than an error.

The fourth and fifth lines are the decisive pair. The `20011114..` window holds exactly 6,000
rows and nothing older, yet `has_more` is still `True`; drop one row and it is `False`. So
`has_more` is the server's own `len(rows) == limit` heuristic rather than a genuine "more
exists" flag. It **over**-reports, never under-reports, which is the fail-closed direction --
and it is exactly why the row-count witness is kept beside it rather than replaced by it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.financial_statements import FINANCIAL_STATEMENT_DATASETS
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
)
from openalpha_cn.domain.panel_batch import MAX_SOURCE_URI_LENGTH, PanelBatchError
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    MAX_PANEL_SOURCE_URI_LENGTH,
    TUSHARE_DATASETS,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

FIELDS = ["ts_code", "trade_date", "adj_factor"]

PING_AN = "000001.SZ"

# The oldest rows the capped response drops, and the newest ones it keeps. Real values.
EARLY_ITEMS: list[list[Any]] = [
    [PING_AN, "19910403", 1.0],
    [PING_AN, "19910404", 1.0],
    [PING_AN, "19910502", 1.409],
    [PING_AN, "20011113", 24.359],
]
CAPPED_BOUNDARY: list[list[Any]] = [
    [PING_AN, "20260807", 139.008],
    [PING_AN, "20011114", 24.359],
]

RESPONSE_ROW_CAP = 6000

FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _response(
    items: list[list[Any]], *, has_more: Any = False, omit_flag: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {"fields": list(FIELDS), "items": items, "count": 0}
    if not omit_flag:
        data[TUSHARE_RESPONSE_TRUNCATION_FLAG] = has_more
    return {"code": 0, "msg": "", "data": data}


def _filler(count: int) -> list[list[Any]]:
    """`count` well-formed rows; only their number matters to the row-count witness."""
    return [
        [PING_AN, f"2001{(index % 12) + 1:02d}{(index % 28) + 1:02d}", 24.359]
        for index in range(count)
    ]


def _provider(fake_tushare_transport, response: dict[str, Any], *, clock: datetime = FETCHED_AT):
    transport = fake_tushare_transport(response)
    return TushareProvider(token="secret-token", transport=transport, clock=lambda: clock), (
        transport
    )


def _request(as_of: datetime, subjects: tuple[str, ...] = ()) -> ProviderRequest:
    return ProviderRequest(dataset=ADJ_FACTOR_DATASET, as_of=as_of, subjects=subjects)


def _descriptor(name: str):
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
    return descriptor


# --- the truncation guard -------------------------------------------------------------


def test_a_response_that_says_it_has_more_is_refused_rather_than_stored(
    fake_tushare_transport,
) -> None:
    """The measured failure: 6,000 rows back, 2,627 older ones silently dropped."""
    provider, _ = _provider(fake_tushare_transport, _response(CAPPED_BOUNDARY, has_more=True))
    with pytest.raises(ProviderFailure, match="reports that it has more rows to give"):
        provider.fetch_panel(_request(FETCHED_AT))


def test_a_response_at_the_measured_row_cap_is_refused_even_when_the_flag_says_otherwise(
    fake_tushare_transport,
) -> None:
    """The two witnesses are independent, and this is why keeping both is not redundancy.

    `has_more` is server-computed; the row count is computed here. A response carrying
    exactly the cap while claiming completeness is either a server bug or a schema change,
    and in both cases the honest reading is that the oldest rows may be missing.
    """
    provider, _ = _provider(
        fake_tushare_transport, _response(_filler(RESPONSE_ROW_CAP), has_more=False)
    )
    with pytest.raises(ProviderFailure, match=r"served 6000 adj_factor row\(s\), which is its"):
        provider.fetch_panel(_request(FETCHED_AT))


def test_one_row_below_the_cap_with_a_false_flag_is_accepted(fake_tushare_transport) -> None:
    """The complement of the test above: the guard must not refuse the complete case.

    Measured: the `20011115..` window returns 5,999 rows with `has_more=False`, and that is a
    complete answer.
    """
    provider, _ = _provider(
        fake_tushare_transport, _response(_filler(RESPONSE_ROW_CAP - 1), has_more=False)
    )
    batch = provider.fetch_panel(_request(datetime(2001, 12, 31, 12, 0, tzinfo=UTC)))
    assert batch.status == "success"
    assert batch.row_count == RESPONSE_ROW_CAP - 1


def test_a_response_with_no_truncation_flag_at_all_is_refused(fake_tushare_transport) -> None:
    """A missing flag is not a passed check.

    Every live `adj_factor` response carries `has_more`. One that does not came from a schema
    change, and continuing on the row count alone would leave the guard's stronger half
    silently switched off.
    """
    provider, _ = _provider(fake_tushare_transport, _response(CAPPED_BOUNDARY, omit_flag=True))
    with pytest.raises(ProviderFailure, match="carries no has_more flag"):
        provider.fetch_panel(_request(FETCHED_AT))


@pytest.mark.parametrize("flag", [0, "false", "False", "0", None, "", []])
def test_only_the_boolean_false_counts_as_complete(fake_tushare_transport, flag: Any) -> None:
    """`if not has_more:` is the mistake this rules out.

    `"False"` and `"0"` are both truthy and would have been read as "there is more"; `0` and
    `""` are both falsy and would have been read as "complete". Neither reading is a fact
    about the data, so only the literal `False` is accepted.
    """
    provider, _ = _provider(fake_tushare_transport, _response(CAPPED_BOUNDARY, has_more=flag))
    with pytest.raises(ProviderFailure, match="has_more must be exactly the boolean False"):
        provider.fetch_panel(_request(FETCHED_AT))


def test_the_guard_also_covers_the_evidence_plane(fake_tushare_transport) -> None:
    """`fetch()` and `fetch_panel()` decode the same envelope, so one guard serves both."""
    provider, _ = _provider(fake_tushare_transport, _response(CAPPED_BOUNDARY, has_more=True))
    with pytest.raises(ProviderFailure, match="reports that it has more rows to give"):
        provider.fetch(_request(FETCHED_AT))


def test_the_truncation_failure_is_not_retryable_and_is_categorised_upstream(
    fake_tushare_transport,
) -> None:
    """Retrying returns the same 6,000 rows; only a narrower window fixes it, so a retry
    loop would spin forever on a `retryable=True`."""
    provider, _ = _provider(fake_tushare_transport, _response(CAPPED_BOUNDARY, has_more=True))
    with pytest.raises(ProviderFailure, match="reports that it has more rows to give") as failure:
        provider.fetch_panel(_request(FETCHED_AT))
    assert failure.value.retryable is False
    assert failure.value.category == "upstream"


def test_every_descriptor_states_whether_its_response_cap_was_measured() -> None:
    """The cap is per endpoint, not global, and guessing one is worse than declaring none.

    Measured on 2026-08-08: `adj_factor`, `daily` and `daily_basic` cap at 6,000; `namechange`
    at 10,000; `trade_cal` returned all 13,162 published SSE rows in one response with
    `has_more=False`, so applying 6,000 to it would refuse a complete answer. `stock_basic`
    returned its whole 5,878-row registry with `has_more=False`, which places its cap somewhere
    above that without establishing where -- so it declares none and is guarded by the flag
    alone.

    `V2-P1-008` added two rows and both are measured on 2026-08-09, with a multi-session window
    because one session of either fits: `stk_limit` returns exactly 7,800 rows with
    `has_more=True` for `20260701..20260807` and `suspend_d` exactly 5,000 for
    `20150601..20150831`, and `limit=8000` / `10000` / `12000` raise neither. They are the two
    `V2-P1-009` added `index_weight` at **7,000**, measured on 2026-08-09 with a whole-year
    window because one publication (300, 500 or 1,000 rows) fits many times over:
    `index_weight(000852.SH, 20230101..20231231)` should hold 12,000 rows and returns exactly
    7,000 with `has_more=True`, and no `limit` raises that. `limit` does *narrow* it --
    `limit=5000` returns 5,000 -- which the first pass at this task recorded the other way
    round and the task-32 review re-measured. For `INDEX_WEIGHT_INDEX_CODES` its headroom is
    also the only one here not on the market's clock: what would have to grow is an index's
    constituent count, which its own definition fixes. That does not generalise to every
    `index_code` the descriptor accepts -- a single month of 000985.CSI 中证全指 is 5,126 rows
    and grows with every listing.

    largest caps here and the two furthest apart in headroom: `stk_limit`'s whole-market cross
    section was 7,733 rows on 2026-08-07 (67 spare, growing ~+500/year), `suspend_d`'s worst
    measured session 1,466.

    `V2-P1-010` added the two extremes of this column at once, both measured 2026-08-09.
    `index_member_all` caps at **3,000**, the lowest here: a bare request returns exactly
    3,000 with `has_more=True` and `limit=3001` / `5000` / `10000` return the same 3,000,
    while `limit=100` returns 100 -- so `limit` narrows only, as `index_weight`'s does.
    `index_classify` declares **none**, for `stock_basic`'s reason and with the same probe
    behind it: the 511-row SW2021 tree returns `has_more=False`, `limit=100000` also returns
    511 with `has_more=False`, and `limit=511` returns 511 with `has_more=True`, so the flag
    turns `True` at whatever effective limit is in force and no probe can find the ceiling.

    `V2-P1-011` added four rows and split them two and two, measured 2026-08-09.
    `balancesheet` and `fina_indicator` cap at **100** -- thirty times lower than anything
    already here -- and the probe pins it three ways: `limit=101`, `limit=500` and `limit=5000`
    all return the same 100 rows with `has_more=True`, and asking for six columns instead of
    152 still returns 100, so it is a row cap rather than a payload budget. `income` and
    `cashflow` declare **none**, and not for want of trying: `income(ts_code=000001.SZ,
    limit=5000)` returns **127** rows with `has_more=False` and `cashflow` returns 90, on three
    different long-history securities. Their cap is therefore above 127 and out of reach, since
    a security cannot file more reports than it has quarters -- `index_classify`'s situation,
    reached from the opposite direction.
    """
    caps = {entry.dataset: entry.max_rows_per_response for entry in TUSHARE_DATASETS}
    assert caps == {
        "daily": 6000,
        "trade_cal": None,
        "stock_basic": None,
        "namechange": 10000,
        "adj_factor": 6000,
        "daily_basic": 6000,
        "suspend_d": 5000,
        "stk_limit": 7800,
        "index_weight": 7000,
        "index_classify": None,
        "index_member_all": 3000,
        "income": None,
        "balancesheet": 100,
        "cashflow": None,
        "fina_indicator": 100,
    }


def test_the_flag_is_demanded_exactly_where_it_is_the_only_witness_or_the_stakes_are_highest(
    fake_tushare_transport,
) -> None:
    """Stated as data so that wiring `daily` (`V2-P1-007`) has to make this choice on
    purpose rather than inherit it -- and set on `stock_basic` because that descriptor was the
    one place with **neither** witness: no measured cap and no required flag, so a response
    that simply omitted `has_more` was accepted at any row count with nothing checking it.

    `V2-P1-008` is the "later task" `providers/tushare.py`'s docstring anticipated, and it
    splits its two datasets rather than inheriting either precedent. `stk_limit` demands the
    flag because `daily`'s argument does not transfer: a dropped bar is an absence nothing
    interpolates, but a dropped **band** is silently replaced by `AShareExecutionPolicy`'s
    derived one, which disagrees with the exchange on 159 of 5,338 names on 2024-06-28 -- so
    the stakes are `adj_factor`'s, not `daily`'s. `suspend_d` does not, because every consumer
    uses a halt to *excuse* a missing bar, so a truncated response raises more alarms rather
    than fewer.

    `V2-P1-009`'s `index_weight` is the fourth, and it is the first to demand the flag while
    *also* having an independent second witness one layer up. The cap can split a publication
    rather than only dropping whole ones -- the oldest date of a capped response was measured
    carrying 100 of its 300 constituents -- and a publication missing two thirds of its members
    is a different index rather than a short one. `domain/index_membership.py` would catch that
    particular shape through the weight-sum tolerance, and the flag is demanded anyway because
    the checksum cannot see a truncation that lands on a publication boundary: that one drops
    whole months and leaves every surviving publication summing perfectly.

    `V2-P1-010`'s two are the fifth and sixth. `index_classify` is `stock_basic`'s case
    exactly -- its cap is unmeasurable from outside, so the flag is its only witness.
    `index_member_all` has a measured cap and demands the flag as well, because the cap
    drops the *oldest* rows and the oldest rows of an `l1_code` slice are whole early
    assignments spread across many securities: what survives is a well-formed,
    non-overlapping history that is simply missing its beginning, which no downstream check
    can see. That is `adj_factor`'s situation, on the lowest cap in this table.

    `V2-P1-011`'s four are the seventh through tenth, and they are the first block to demand it
    unanimously. `income` and `cashflow` demand it because it is their **only** witness: their
    cap is above the 127 rows a security can produce and so is unmeasurable, `index_classify`'s
    case. `balancesheet` and `fina_indicator` have a measured cap of 100 and demand the flag as
    well, for `adj_factor`'s reason rather than `daily`'s -- the cap drops the *oldest* rows, so
    a truncated history is not short but silently wrong: every year-on-year growth rate the
    projection carries (`or_yoy`, `netprofit_yoy`) is computed against a period that is no
    longer there, and `build_statement_history` is happy with any set of filings.

    `trade_cal` is the eleventh, added by the P1 stage review, and it is `stock_basic`'s and
    `index_classify`'s case: its cap is unmeasurable from outside (the exchange cannot publish
    more sessions than it has days), so the flag is the only witness available. It was the
    last `False` in this table, and the compensating argument that justified it -- carried in
    this test's own failure message until now -- was disproved on both of its clauses:

    - it said `trade_cal`'s request is "whole-period". `_trade_cal_params` builds a
      **single-year** window (`start_date=Y0101 / end_date=Y1231`); the 13,162-row observation
      came from a differently shaped request.
    - it said a truncated calendar would be caught by `build_trading_calendar`'s gap rule.
      Tushare's cap drops the **oldest** rows, so a truncated response is a contiguous
      *suffix* and there is no gap to see: dropping 2024-01..03 leaves `build_trading_calendar`
      accepting the response and returning 197 sessions where the year has 262.

    Neither correction makes this reachable today -- one year is at most 366 rows, far under
    any cap in this table -- but it made `trade_cal` the only dataset here with neither witness
    *and* no compensating control, guarding the single source of truth that every
    date-completeness check on the panel plane (`required_dates`, `_session_census`,
    `date_gap`) is measured against. Two assertions below changed to add it, deliberately, for
    the reason `providers/tushare.py`'s own docstring gives about the `daily` decision: a later
    task with a real reason to demand the flag "should change the test rather than read its
    redness as a verdict".
    """
    demanded = {entry.dataset for entry in TUSHARE_DATASETS if entry.requires_truncation_flag}
    assert demanded == {
        ADJ_FACTOR_DATASET,
        STOCK_BASIC_DATASET,
        PRICE_LIMIT_DATASET,
        INDEX_WEIGHT_DATASET,
        INDUSTRY_TREE_DATASET,
        INDUSTRY_MEMBERSHIP_DATASET,
        TRADING_CALENDAR_DATASET,
        *FINANCIAL_STATEMENT_DATASETS,
    }

    capless_and_unflagged = {
        entry.dataset
        for entry in TUSHARE_DATASETS
        if entry.max_rows_per_response is None and not entry.requires_truncation_flag
    }
    assert capless_and_unflagged == set(), (
        "every dataset must carry at least one truncation witness -- a measured cap, the "
        f"has_more flag, or both. Unwitnessed: {sorted(capless_and_unflagged)}"
    )


def test_a_registry_response_with_no_truncation_flag_is_now_refused(
    fake_tushare_transport,
) -> None:
    """The hole the flag closes, in the shape it was measured in: `stock_basic` + a missing
    `has_more` used to be `ACCEPTED rows=300` with no guard consulted at all."""
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    items = [[f"{600000 + index}.SH", "SSE", "19910403", None] for index in range(300)]
    response = {
        "code": 0,
        "msg": "",
        "data": {"fields": ["ts_code", "exchange", "list_date", "delist_date"], "items": items},
    }
    provider, _ = _provider(fake_tushare_transport, response, clock=as_of)
    with pytest.raises(ProviderFailure, match="carries no has_more flag"):
        provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))


def test_the_registry_cap_is_declared_unmeasured_rather_than_guessed_at_122_rows() -> None:
    """The margin is *not* 6,000 - 5,878. Measured on 2026-08-08:

        stock_basic(L,D)                 -> n=5878  has_more=false
        stock_basic(L,D, limit=5878)     -> n=5878  has_more=TRUE
        stock_basic(L,D, limit=100000)   -> n=5878  has_more=false

    The flag turns `True` at whatever effective limit is in force, so no probe can push past
    the real ceiling to find it. All that is established is "above 5,878"; naming 122 rows of
    headroom would be exactly the guess `max_rows_per_response=None` declines to make, and if
    this endpoint shares `namechange`'s 10,000 the true figure is 4,122.
    """
    assert _descriptor(STOCK_BASIC_DATASET).max_rows_per_response is None


# --- the request ----------------------------------------------------------------------


def test_the_request_is_one_trading_day_of_the_whole_market(fake_tushare_transport) -> None:
    """A cross section, not a per-security range: 5,387 rows on 2024-06-28, against 8,627 for
    one security's whole history. The whole market for one day fits under the cap; one
    security's history does not."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)  # 17:00 Asia/Shanghai
    provider, transport = _provider(
        fake_tushare_transport, _response([[PING_AN, "20240628", 125.049]]), clock=as_of
    )
    provider.fetch_panel(_request(as_of))
    assert transport.payload is not None
    assert transport.payload["params"] == {"trade_date": "20240628"}
    assert transport.payload["api_name"] == ADJ_FACTOR_DATASET


def test_the_trading_day_is_resolved_in_shanghai_not_utc(fake_tushare_transport) -> None:
    """2024-06-27 17:00Z is already 2024-06-28 in Shanghai; asking UTC would fetch the
    previous session's factors for every late-evening request."""
    as_of = datetime(2024, 6, 27, 17, 0, tzinfo=UTC)
    provider, transport = _provider(
        fake_tushare_transport, _response([[PING_AN, "20240628", 125.049]])
    )
    provider.fetch_panel(_request(as_of))
    assert transport.payload is not None
    assert transport.payload["params"]["trade_date"] == "20240628"


def test_subjects_narrow_the_cross_section(fake_tushare_transport) -> None:
    """The escape route when the market outgrows the cap: 5,553 listed names on 2026-08-07
    against a 6,000-row ceiling is 8% of headroom, so this filter is how a future caller
    splits the day rather than losing the oldest codes."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    provider, transport = _provider(
        fake_tushare_transport, _response([[PING_AN, "20240628", 125.049]]), clock=as_of
    )
    provider.fetch_panel(_request(as_of, subjects=(PING_AN, "600519.SH")))
    assert transport.payload is not None
    assert transport.payload["params"] == {
        "trade_date": "20240628",
        "ts_code": f"{PING_AN},600519.SH",
    }


# --- the projection -------------------------------------------------------------------


def test_the_1991_segment_survives_a_windowed_fetch_with_its_factor_of_one(
    fake_tushare_transport,
) -> None:
    """The completeness acceptance: the segment the capped response drops is real data, and
    the narrower window returns it with the listing-day factor of exactly 1.0."""
    as_of = datetime(2001, 11, 13, 12, 0, tzinfo=UTC)
    provider, _ = _provider(fake_tushare_transport, _response(EARLY_ITEMS), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))

    assert batch.status == "success"
    factor_date, adj_factor = batch.columns
    assert factor_date.name == "factor_date"
    assert adj_factor.name == "adj_factor"
    assert factor_date.values[0] == "1991-04-03"
    assert adj_factor.values[0] == 1.0
    assert adj_factor.kind == "float"
    # ascending, whatever order Tushare served
    assert list(factor_date.values) == ["1991-04-03", "1991-04-04", "1991-05-02", "2001-11-13"]


@pytest.mark.parametrize("value", ["1.0", None, True, 0.0, -1.0, float("inf")])
def test_a_factor_that_cannot_scale_a_price_is_refused_at_the_boundary(
    fake_tushare_transport, value: Any
) -> None:
    """`True` is the one that matters: it is an `int`, so a numeric-tower check would let it
    through as 1.0 and make every adjusted price equal to its raw one."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport, _response([[PING_AN, "20240628", value]]), clock=as_of
    )
    with pytest.raises(ProviderFailure, match="adj_factor must be a finite positive number"):
        provider.fetch_panel(_request(as_of))


def test_an_integer_factor_is_accepted_and_stored_as_a_float(fake_tushare_transport) -> None:
    """JSON has one number type, so a factor of exactly 1 can arrive as `1` rather than
    `1.0`; the column's kind is `float` and `PanelColumn` refuses an `int` in it."""
    as_of = datetime(1991, 4, 3, 9, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport, _response([[PING_AN, "19910403", 1]]), clock=as_of
    )
    batch = provider.fetch_panel(_request(as_of))
    (_, adj_factor) = batch.columns
    assert adj_factor.values == (1.0,)
    assert type(adj_factor.values[0]) is float


def test_the_evidence_plane_hands_back_the_response_row_verbatim(fake_tushare_transport) -> None:
    """Unlike the two registry datasets, an `adj_factor` row carries one fact about one
    session, so there is a single honest `available_time` for it and `fetch()` serves it."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport, _response([[PING_AN, "20240628", 125.049]]), clock=as_of
    )
    batch = provider.fetch(_request(as_of))
    (record,) = batch.records
    assert record.subject == PING_AN
    assert record.payload == {"ts_code": PING_AN, "trade_date": "20240628", "adj_factor": 125.049}
    assert record.source_uri == "tushare://adj_factor/000001.SZ/20240628"


def test_availability_is_the_sessions_close_not_its_midnight(fake_tushare_transport) -> None:
    """`daily_close`: 15:00 event, 16:30 availability, both Asia/Shanghai.

    A backfill run today (`clock=FETCHED_AT`) replaying an `as_of` of that morning sees the
    session's factors as not yet knowable, rather than as absent -- the distinction
    `fetch_panel`'s two no-data reasons exist for.
    """
    morning = datetime(2024, 6, 28, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    provider, _ = _provider(fake_tushare_transport, _response([[PING_AN, "20240628", 125.049]]))
    batch = provider.fetch_panel(_request(morning))
    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet knowable is not the same as absent" in batch.no_data_reason


def test_a_fetch_that_runs_before_the_session_closes_drops_the_row_it_cannot_know(
    fake_tushare_transport,
) -> None:
    """A property of `ClockStrategy.daily_close`, pinned here because `adj_factor` is the
    second dataset on it -- **and changed in `V2-P1-018` because live measurement disproved
    the premise it used to rest on.**

    This test previously asserted that such a fetch *raises*, on the argument that
    `_daily_close_timeline` does not raise `ingested_time` the way `_calendar_static_timeline`
    does, that the alternative would be "a stored factor dated as knowable before it was
    published", and that "in practice the endpoint serves no rows for a session that has not
    closed". The last clause is false: `suspend_d(trade_date=20260811)` served two rows at
    05:29 Asia/Shanghai on 2026-08-11 -- a halt announced for the next session -- so
    `openalpha doctor --probe` reported `invalid_response` for a working endpoint every
    morning, with the reason "Tushare response could not be decoded", about a response that
    decoded perfectly.

    The middle clause is false too, and that is what this test now asserts. Raising
    `ingested_time` does **not** store an unknowable row: `_decode_panel_rows` bounds its
    point-in-time filter at `min(as_of, ingested_at)`, so the row is represented only long
    enough to be discarded, exactly as `_calendar_static_timeline` has documented since
    `V2-P1-005`. The safe direction is kept and is asserted directly -- nothing whose
    availability runs past the fetch instant reaches the batch -- rather than being inferred
    from an exception.
    """
    morning = datetime(2024, 6, 28, 1, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response([[PING_AN, "20240627", 124.0], [PING_AN, "20240628", 125.049]]),
        clock=morning,
    )

    batch = provider.fetch_panel(_request(morning))

    assert batch.status == "success"
    assert batch.subjects == (PING_AN,)
    assert max(batch.timeline.available_time) <= morning
    assert 125.049 not in batch.columns[0].values


# --- provenance for a whole-market batch ----------------------------------------------


def test_a_whole_market_batch_summarises_its_subjects_instead_of_overflowing_the_uri(
    fake_tushare_transport,
) -> None:
    """A cross section carries thousands of subjects, and joining them all produced a
    ~60,000-character `source_uri` that `ColumnarPanelBatch` refused outright -- so no
    whole-market panel fetch could complete at all. The exact set stays recoverable from the
    partition's own `subject` column and is covered by `content_digest`.
    """
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    items = [[f"{600000 + index}.SH", "20240628", 1.5] for index in range(400)]
    provider, _ = _provider(fake_tushare_transport, _response(items), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))
    assert batch.source_uri == "tushare://adj_factor/400-subjects/20240628-20240628"


def test_the_producer_side_uri_bound_is_the_contracts_own_constant() -> None:
    """The one thing holding the fix together, asserted instead of commented.

    `_panel_source_uri` summarises a subject list precisely so the URI it hands back fits
    inside what `ColumnarPanelBatch` will accept -- and the contract refuses an over-long
    `source_uri` *outside* `fetch_panel`'s decode `try`, so a mismatch is not a provider
    failure but a contract error that kills the whole fetch. While these were two independent
    `2048` literals, raising the producer's to 3,000 left the entire suite green and restored
    the original bug for every fetch whose joined URI lands between the two numbers.
    """
    assert MAX_PANEL_SOURCE_URI_LENGTH == MAX_SOURCE_URI_LENGTH
    assert MAX_SOURCE_URI_LENGTH == 2048


@pytest.mark.parametrize(
    ("subjects", "expected_length", "summarised"),
    [(200, 2038, False), (201, MAX_SOURCE_URI_LENGTH, False), (202, 51, True)],
)
def test_the_uri_switches_to_a_summary_exactly_at_the_contracts_limit(
    fake_tushare_transport, subjects: int, expected_length: int, summarised: bool
) -> None:
    """Both sides of the switch and the boundary itself, in one parametrisation.

    Nine-character codes over a 39-character frame make the joined URI `10n + 38`, so 201
    subjects land on exactly 2,048 and 202 land one row past it. The window is what makes the
    two constants' agreement load-bearing rather than theoretical: the whole band from 2,048
    up to the 400-subject test's 4,038 is reachable by an ordinary fetch, and every value in
    it is refused by the contract and accepted by a drifted producer.
    """
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    items = [[f"{600000 + index}.SH", "20240628", 1.5] for index in range(subjects)]
    provider, _ = _provider(fake_tushare_transport, _response(items), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))

    assert batch.source_uri is not None
    assert len(batch.source_uri) == expected_length
    assert len(batch.source_uri) <= MAX_SOURCE_URI_LENGTH
    assert (f"/{subjects}-subjects/" in batch.source_uri) is summarised


def test_a_two_subject_batch_still_names_both(fake_tushare_transport) -> None:
    """The summary is a fallback for an over-long URI, not a change of shape: the calendar's
    two-exchange partitions keep naming their exchanges."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    items = [[PING_AN, "20240628", 125.049], ["600519.SH", "20240628", 8.0205]]
    provider, _ = _provider(fake_tushare_transport, _response(items), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))
    assert batch.source_uri == "tushare://adj_factor/000001.SZ,600519.SH/20240628-20240628"


def test_an_empty_response_is_no_data_and_not_a_truncation(fake_tushare_transport) -> None:
    """A holiday cross section is 0 rows with `has_more=False` -- measured on 2024-01-01 and
    2024-02-11 -- and must stay distinguishable from a capped one."""
    as_of = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
    provider, _ = _provider(fake_tushare_transport, _response([]), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))
    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "which is a horizon and not a closed period" in batch.no_data_reason


def test_the_descriptor_checks_the_three_columns_it_reads() -> None:
    descriptor = _descriptor(ADJ_FACTOR_DATASET)
    assert descriptor.checked_response_fields == ("ts_code", "trade_date", "adj_factor")
    assert descriptor.response_fields == ""
    assert descriptor.serves_evidence_plane is True


def test_a_response_missing_the_factor_column_is_refused(fake_tushare_transport) -> None:
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    response = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["ts_code", "trade_date"],
            "items": [[PING_AN, "20240628"]],
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }
    provider, _ = _provider(fake_tushare_transport, response, clock=as_of)
    with pytest.raises(ProviderFailure, match="has no adj_factor column"):
        provider.fetch_panel(_request(as_of))


def test_a_panel_batch_error_is_not_dressed_up_as_a_tushare_outage(
    fake_tushare_transport,
) -> None:
    """The contract's own failures stay outside the decode `try`; this pins that the
    truncation guard did not move a decode failure into it."""
    as_of = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    items = [[f"{600000 + index}.SH", "20240628", 1.5] for index in range(3)]
    provider, _ = _provider(fake_tushare_transport, _response(items), clock=as_of)
    batch = provider.fetch_panel(_request(as_of))
    with pytest.raises(PanelBatchError, match="success requires at least one row"):
        type(batch)(
            provider_id=batch.provider_id,
            dataset=batch.dataset,
            kind=batch.kind,
            as_of=batch.as_of,
            fetched_at=batch.fetched_at,
            status="success",
        )
