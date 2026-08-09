"""`V2-P1-011` end to end: statement windows stored by announcement year and read back.

The transport is doubled and every response body is real, captured live on 2026-08-09 with
exactly the projection the descriptors request; the suite never touches the network.

The five things this file is really about:

- **A filing is filed under its announcement year, never its period.** `001278.SZ`'s 2018
  annual indicators were announced 2022-01-06, and a 2018 partition would put them in front of
  every reader of 2019, 2020 and 2021.
- **`fina_indicator`'s window straddles two partitions**, because it filters the report period
  rather than the announcement, so the year split is load-bearing rather than defensive.
- **The catalog gets the `update_flag` census and a clock-derived revision count of 0**, which
  is exactly the pair of facts `panel/catalog.py` built two facets for.
- **A per-security backfill loop is refused** rather than leaving the year holding whichever
  security ran last.
- **The ambiguity survives the round trip.** What the endpoint served as two irreconcilable rows
  comes back as two versions, and reading the field they disagree on raises while every other
  field still answers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.financial_statements import (
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    AmbiguousReportError,
    FinancialStatementError,
    FinancialStatementHorizonError,
    financial_ambiguity_report,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    financial_statement_requirement,
    load_statement_histories,
    write_financial_statements,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider

AS_OF = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

INCOME_FIELDS = [
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "update_flag",
    "total_revenue",
    "revenue",
    "oper_cost",
    "operate_profit",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "basic_eps",
    "ebit",
]
INDICATOR_FIELDS = [
    "ts_code",
    "ann_date",
    "end_date",
    "eps",
    "bps",
    "roe",
    "roa",
    "netprofit_margin",
    "grossprofit_margin",
    "debt_to_assets",
    "or_yoy",
    "netprofit_yoy",
    "ocfps",
    "fcff",
]
DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]

# income(000001.SZ, 20180101..20181231): four periods over seven rows. The 2017 annual pair is
# byte-identical; the two 2018 pairs differ on `ebit` alone; 2018Q3 arrives once.
PINGAN_INCOME_2018 = [
    [
        "000001.SZ",
        "20181024",
        "20181024",
        "20180930",
        "0",
        86664000000.0,
        86664000000.0,
        None,
        26614000000.0,
        26566000000.0,
        6110000000.0,
        20456000000.0,
        20456000000.0,
        1.14,
        None,
    ],
    [
        "000001.SZ",
        "20180816",
        "20180816",
        "20180630",
        "0",
        57241000000.0,
        57241000000.0,
        None,
        17402000000.0,
        17367000000.0,
        3995000000.0,
        13372000000.0,
        13372000000.0,
        0.73,
        39700000000.0,
    ],
    [
        "000001.SZ",
        "20180816",
        "20180816",
        "20180630",
        "1",
        57241000000.0,
        57241000000.0,
        None,
        17402000000.0,
        17367000000.0,
        3995000000.0,
        13372000000.0,
        13372000000.0,
        0.73,
        None,
    ],
    [
        "000001.SZ",
        "20180420",
        "20180420",
        "20180331",
        "0",
        28026000000.0,
        28026000000.0,
        None,
        8584000000.0,
        8567000000.0,
        1972000000.0,
        6595000000.0,
        6595000000.0,
        0.33,
        19255000000.0,
    ],
    [
        "000001.SZ",
        "20180420",
        "20180420",
        "20180331",
        "1",
        28026000000.0,
        28026000000.0,
        None,
        8584000000.0,
        8567000000.0,
        1972000000.0,
        6595000000.0,
        6595000000.0,
        0.33,
        None,
    ],
    [
        "000001.SZ",
        "20180315",
        "20180315",
        "20171231",
        "0",
        105786000000.0,
        105786000000.0,
        None,
        30223000000.0,
        30157000000.0,
        6968000000.0,
        23189000000.0,
        23189000000.0,
        1.3,
        73148000000.0,
    ],
    [
        "000001.SZ",
        "20180315",
        "20180315",
        "20171231",
        "1",
        105786000000.0,
        105786000000.0,
        None,
        30223000000.0,
        30157000000.0,
        6968000000.0,
        23189000000.0,
        23189000000.0,
        1.3,
        73148000000.0,
    ],
]

# income(600739.SH, 20180101..20181231): a second security announcing into the same year.
LIAONING_INCOME_2018 = [
    [
        "600739.SH",
        "20181031",
        "20181031",
        "20180930",
        "0",
        14170201635.68,
        14170201635.68,
        12409494178.03,
        939350676.07,
        912251816.33,
        98462618.1,
        813789198.23,
        698601790.81,
        0.4567,
        114898256.24,
    ],
    [
        "600739.SH",
        "20181031",
        "20181031",
        "20180930",
        "1",
        14170201635.68,
        14170201635.68,
        12409494178.03,
        939350676.07,
        912251816.33,
        98462618.1,
        813789198.23,
        698601790.81,
        0.4567,
        114898256.24,
    ],
    [
        "600739.SH",
        "20180831",
        "20180831",
        "20180630",
        "0",
        9121971324.92,
        9121971324.92,
        8037387375.8,
        551863722.92,
        542062893.42,
        59306936.19,
        482755957.23,
        454084335.06,
        0.2968,
        344179176.97,
    ],
    [
        "600739.SH",
        "20180428",
        "20180428",
        "20180331",
        "0",
        4232843582.36,
        4232843582.36,
        3780564177.25,
        165570457.27,
        161030920.41,
        20069349.77,
        140961570.64,
        146383868.42,
        0.0957,
        61415851.26,
    ],
    [
        "600739.SH",
        "20180414",
        "20180414",
        "20171231",
        "0",
        13998827377.12,
        13998827377.12,
        11895848420.36,
        1882623855.96,
        1621498870.5,
        132470901.26,
        1489027969.24,
        1446167952.97,
        0.9454,
        365745260.22,
    ],
]

# fina_indicator(000001.SZ, 20180101..20181231): a *period* window. Its newest row is the 2018
# annual, announced 2019-03-07, so one request spans two announcement years.
PINGAN_INDICATORS_2018_PERIODS = [
    [
        "000001.SZ",
        "20190307",
        "20181231",
        1.39,
        12.8182,
        10.7415,
        None,
        21.2636,
        None,
        92.9783,
        10.3322,
        7.0249,
        -3.34,
        None,
    ],
    [
        "000001.SZ",
        "20190307",
        "20181231",
        1.39,
        12.8182,
        10.7415,
        None,
        21.2636,
        None,
        92.9783,
        10.3322,
        7.0249,
        -3.34,
        132987870528.3733,
    ],
    [
        "000001.SZ",
        "20181024",
        "20180930",
        1.14,
        12.538,
        8.9467,
        None,
        23.6038,
        None,
        92.9825,
        8.558,
        6.8031,
        -0.66,
        None,
    ],
    [
        "000001.SZ",
        "20181024",
        "20180930",
        1.14,
        12.538,
        8.9467,
        None,
        23.6038,
        None,
        92.9825,
        8.558,
        6.8031,
        -0.66,
        None,
    ],
    [
        "000001.SZ",
        "20180816",
        "20180630",
        0.73,
        12.1251,
        5.9405,
        None,
        23.3609,
        None,
        93.225,
        5.8666,
        6.5159,
        0.43,
        None,
    ],
    [
        "000001.SZ",
        "20180816",
        "20180630",
        0.73,
        12.1251,
        5.9405,
        None,
        23.3609,
        None,
        93.225,
        5.8666,
        6.5159,
        0.43,
        31659686704.6698,
    ],
    [
        "000001.SZ",
        "20180420",
        "20180331",
        0.33,
        11.8485,
        2.9611,
        None,
        23.5317,
        None,
        93.3088,
        1.082,
        6.1313,
        2.41,
        None,
    ],
    [
        "000001.SZ",
        "20180420",
        "20180331",
        0.33,
        11.8485,
        2.9611,
        None,
        23.5317,
        None,
        93.3088,
        1.082,
        6.1313,
        2.41,
        None,
    ],
]

# fina_indicator(001278.SZ, 20180101..20181231): the 2018 annual, announced 2022-01-06.
YIYUAN_INDICATORS_2018_PERIODS = [
    [
        "001278.SZ",
        "20220106",
        "20181231",
        0.42,
        4.4014,
        11.9565,
        3.4467,
        1.695,
        22.0479,
        82.8617,
        53.512,
        16.397,
        1.79,
        -68487984.7221,
    ],
    [
        "001278.SZ",
        "20220106",
        "20181231",
        0.42,
        4.4014,
        11.9565,
        3.4467,
        1.695,
        22.0479,
        82.8617,
        53.512,
        16.397,
        1.79,
        26218332.3989,
    ],
]

# daily(000001.SZ, 20180420): a non-financial batch, so the writer's dataset guard has something
# real to refuse.
PINGAN_BAR = [
    [
        "000001.SZ",
        "20180420",
        11.51,
        11.58,
        11.2,
        11.35,
        11.47,
        -0.12,
        -1.05,
        958690.66,
        1090801.311,
    ],
]


def _response(fields: list[str], items: list[list[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(row) for row in items], "has_more": False},
    }


class _ScriptedTransport:
    """Answers each request from an `(api_name, ts_code, window start) -> response` script."""

    def __init__(self, script: dict[tuple[str, str, str], dict[str, Any]]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        params = payload["params"]
        key = (
            payload["api_name"],
            params.get("ts_code", ""),
            params.get("start_date", params.get("trade_date", "")),
        )
        return self._script[key]


SCRIPT: dict[tuple[str, str, str], dict[str, Any]] = {
    (INCOME_DATASET, "000001.SZ", "20180101"): _response(INCOME_FIELDS, PINGAN_INCOME_2018),
    (INCOME_DATASET, "600739.SH", "20180101"): _response(INCOME_FIELDS, LIAONING_INCOME_2018),
    (FINANCIAL_INDICATOR_DATASET, "000001.SZ", "20180101"): _response(
        INDICATOR_FIELDS, PINGAN_INDICATORS_2018_PERIODS
    ),
    (FINANCIAL_INDICATOR_DATASET, "001278.SZ", "20180101"): _response(
        INDICATOR_FIELDS, YIYUAN_INDICATORS_2018_PERIODS
    ),
    (DAILY_DATASET, "000001.SZ", "20180420"): _response(DAILY_FIELDS, PINGAN_BAR),
}


def _provider() -> TushareProvider:
    return TushareProvider(
        token="secret-token", transport=_ScriptedTransport(SCRIPT), clock=lambda: AS_OF
    )


def _store(tmp_path: Any) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _fetch(provider: TushareProvider, security: str, year: int = 2018) -> Any:
    """One statement window, asked for at the end of its own announcement year.

    `as_of` is 31 December rather than mid-year because for these three endpoints it is both
    the window selector and the point-in-time bound: a mid-year `as_of` would fetch the whole
    year's window and then drop every filing announced after June, which is most of them.
    """
    return provider.fetch_panel(
        ProviderRequest(
            dataset=INCOME_DATASET,
            as_of=datetime(year, 12, 31, 12, 0, tzinfo=UTC),
            subjects=(security,),
        )
    )


def _fetch_indicators(provider: TushareProvider, security: str, period_year: str) -> Any:
    """One `fina_indicator` period-year window, read at a present-day `as_of`.

    The period year is a subject rather than a function of `as_of` -- see
    `_financial_indicator_params` -- so the two clocks stay separate and a report announced
    years after its period is still reachable.
    """
    return provider.fetch_panel(
        ProviderRequest(
            dataset=FINANCIAL_INDICATOR_DATASET,
            as_of=AS_OF,
            subjects=(security, period_year),
        )
    )


# --------------------------------------------------------------------------------------
# The partition year is the announcement's
# --------------------------------------------------------------------------------------


def test_an_announcement_window_lands_in_exactly_one_partition(tmp_path) -> None:
    """The three statement endpoints filter `ann_date`, so a year window is one availability
    year is one partition -- `namechange`'s property."""
    store = _store(tmp_path)

    written = write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    assert [reference.year for reference in written] == [2018]
    assert store.registered_years(INCOME_DATASET) == (2018,)


def test_a_fina_indicator_period_window_is_split_across_two_announcement_years(
    tmp_path,
) -> None:
    """The asymmetry that makes the split load-bearing: this endpoint's `start_date`/`end_date`
    filter `end_date`, so the 2018 *period* window carries the 2018 annual report, announced
    2019-03-07. Filing the lot under 2018 would hand a 2019 disclosure to a 2018 reader."""
    store = _store(tmp_path)

    written = write_financial_statements(
        store, [_fetch_indicators(_provider(), "000001.SZ", "2018")]
    )

    assert sorted(reference.year for reference in written) == [2018, 2019]
    histories = load_statement_histories(
        store,
        dataset=FINANCIAL_INDICATOR_DATASET,
        years=(2018,),
        as_of=AS_OF,
        max_staleness=None,
    )
    assert histories["000001.SZ"].periods_on(date(2018, 12, 31)) == (
        date(2018, 3, 31),
        date(2018, 6, 30),
        date(2018, 9, 30),
    )


def test_a_filing_announced_three_years_late_is_filed_under_the_announcement(
    tmp_path,
) -> None:
    """`001278.SZ`'s 2018 annual indicators were announced 2022-01-06."""
    store = _store(tmp_path)

    written = write_financial_statements(
        store, [_fetch_indicators(_provider(), "001278.SZ", "2018")]
    )

    assert [reference.year for reference in written] == [2022]
    histories = load_statement_histories(
        store,
        dataset=FINANCIAL_INDICATOR_DATASET,
        years=(2022,),
        as_of=AS_OF,
        max_staleness=None,
    )
    (filing,) = histories["001278.SZ"].filings
    assert filing.period == date(2018, 12, 31)
    assert filing.announced_on == date(2022, 1, 6)
    assert histories["001278.SZ"].periods_on(date(2021, 12, 31)) == ()


# --------------------------------------------------------------------------------------
# The catalog's two revision facets
# --------------------------------------------------------------------------------------


def test_the_update_flag_census_is_recorded_and_the_clock_derived_count_stays_zero(
    tmp_path,
) -> None:
    """Both halves of `panel/catalog.py`'s revision contract on the data it was built for:
    `revisions` sees the two labels, and `revised_row_count` reads 0 because both rows of every
    corrected pair carry the same `ann_date` and the same `f_ann_date`."""
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    coverage = store.read_coverage(INCOME_DATASET, 2018)

    assert coverage is not None
    assert {entry.label: entry.row_count for entry in coverage.revisions} == {"0": 4, "1": 3}
    assert coverage.revised_row_count == 0


def test_fina_indicator_records_no_revision_census_because_it_has_no_label(tmp_path) -> None:
    """Passing `revision_field='update_flag'` here would raise inside `_revision_census`; the
    absence is a declared property of the dataset rather than a branch to discover."""
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch_indicators(_provider(), "000001.SZ", "2018")])

    coverage = store.read_coverage(FINANCIAL_INDICATOR_DATASET, 2018)

    assert coverage is not None
    assert coverage.revisions == ()
    assert coverage.row_count == 6


# --------------------------------------------------------------------------------------
# The backfill guard
# --------------------------------------------------------------------------------------


def test_a_per_security_backfill_loop_is_refused_rather_than_replacing_the_year(
    tmp_path,
) -> None:
    """A partition's key is `(dataset, year)` with no `ts_code` dimension and `write_partition`
    replaces it whole, so `for code in universe: write_financial_statements(store,
    [fetch(code)])` would leave 2018 holding the last security -- silently, with a success
    return."""
    store = _store(tmp_path)
    provider = _provider()
    write_financial_statements(store, [_fetch(provider, "000001.SZ")])

    with pytest.raises(PanelBatchError, match=r"writing it would drop \['000001.SZ'\]"):
        write_financial_statements(store, [_fetch(provider, "600739.SH")])


def test_two_securities_written_together_keep_both(tmp_path) -> None:
    """The supported shape: every security whose filings fall in the year arrives in one call."""
    store = _store(tmp_path)
    provider = _provider()

    write_financial_statements(
        store,
        [
            _fetch(provider, "000001.SZ"),
            _fetch(provider, "600739.SH"),
        ],
    )

    histories = load_statement_histories(
        store, dataset=INCOME_DATASET, years=(2018,), as_of=AS_OF, max_staleness=None
    )
    assert sorted(histories) == ["000001.SZ", "600739.SH"]


def test_a_batch_from_another_dataset_is_refused_by_name(tmp_path) -> None:
    store = _store(tmp_path)
    bar = _provider().fetch_panel(
        ProviderRequest(
            dataset=DAILY_DATASET,
            as_of=datetime(2018, 4, 20, 12, 0, tzinfo=UTC),
            subjects=("000001.SZ",),
        )
    )

    with pytest.raises(FinancialStatementError, match=r"got 'daily'"):
        write_financial_statements(store, [bar])


def test_a_requirement_for_an_unknown_dataset_is_refused_by_name() -> None:
    with pytest.raises(FinancialStatementError, match=r"got 'dividend'"):
        financial_statement_requirement(
            dataset="dividend", years=(2018,), as_of=AS_OF, max_staleness=None
        )


# --------------------------------------------------------------------------------------
# The ambiguity survives the round trip
# --------------------------------------------------------------------------------------


def test_two_irreconcilable_rows_come_back_as_two_versions_and_refuse_one_field(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    histories = load_statement_histories(
        store, dataset=INCOME_DATASET, years=(2018,), as_of=AS_OF, max_staleness=None
    )

    filing = histories["000001.SZ"].filing_for(date(2018, 6, 30), date(2018, 8, 16))
    assert filing.is_ambiguous
    assert filing.value_of("revenue") == 57241000000.0
    assert filing.value_of("n_income_attr_p") == 13372000000.0
    with pytest.raises(AmbiguousReportError, match=r"'ebit'"):
        filing.value_of("ebit")


def test_an_identical_pair_survives_the_round_trip_as_one_answer(tmp_path) -> None:
    """The 2017 annual arrives twice, labelled `0` and `1`, byte-identical. Both rows are
    stored -- the catalog counts them -- and the read collapses them into one version."""
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    histories = load_statement_histories(
        store, dataset=INCOME_DATASET, years=(2018,), as_of=AS_OF, max_staleness=None
    )

    filing = histories["000001.SZ"].filing_for(date(2017, 12, 31), date(2018, 3, 15))
    assert filing.row_count == 2
    assert not filing.is_ambiguous
    assert filing.versions[0].labels == ("0", "1")
    assert filing.value_of("ebit") == 73148000000.0


def test_the_ambiguity_report_is_computed_from_the_stored_corpus(tmp_path) -> None:
    """The cost is measured on the objects a reader uses rather than modelled: of the four
    stored filings, two refuse exactly one of the ten projected columns and one collapsed."""
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    histories = load_statement_histories(
        store, dataset=INCOME_DATASET, years=(2018,), as_of=AS_OF, max_staleness=None
    )
    report = financial_ambiguity_report(dataset=INCOME_DATASET, histories=histories)

    assert report.filings == 4
    assert report.collapsed_versions == 1
    assert report.ambiguous_filings == 2
    assert report.ambiguous_field_reads["ebit"] == 2
    assert report.ambiguous_field_reads["revenue"] == 0
    assert not report.is_clean


def test_a_read_of_no_years_is_refused_rather_than_answering_nothing(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(FinancialStatementError, match=r"needs at least one announcement year"):
        load_statement_histories(
            store, dataset=INCOME_DATASET, years=(), as_of=AS_OF, max_staleness=None
        )


def test_a_year_that_was_never_written_blocks_rather_than_reading_empty(tmp_path) -> None:
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    with pytest.raises(PanelStorageError, match=r"the income panel cannot be read"):
        load_statement_histories(
            store, dataset=INCOME_DATASET, years=(2018, 2019), as_of=AS_OF, max_staleness=None
        )


def test_a_day_before_the_first_filing_is_refused_after_the_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    write_financial_statements(store, [_fetch(_provider(), "000001.SZ")])

    histories = load_statement_histories(
        store, dataset=INCOME_DATASET, years=(2018,), as_of=AS_OF, max_staleness=None
    )

    with pytest.raises(FinancialStatementHorizonError, match=r"had announced no income filing"):
        histories["000001.SZ"].latest_filing_on(date(2018, 3, 14))
