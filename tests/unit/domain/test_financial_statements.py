"""`domain/financial_statements.py` (`V2-P1-011`): one key, more than one answer.

Every fixture in this file is a verbatim row from a live probe on 2026-08-09, fetched with
exactly the projection the panel stores; the suite never touches the network. The fixtures were
chosen to span the shapes the probe actually found, not two extremes:

- a duplicate key whose rows are identical (collapses),
- a duplicate key that differs in **one** field, in the `None` / number direction,
- a duplicate key that differs in one field by a rounding step (`5.86` vs `5.8602`),
- a duplicate key that differs in one field by a **sign flip**,
- a duplicate key that differs in **ten** fields at once, one of them by a factor of ten,
- a duplicate key whose two rows carry the **same** `update_flag`,
- a duplicate key whose two rows disagree about `f_ann_date` and agree about everything else,
- a filing whose `f_ann_date` precedes its `ann_date`, and one where it follows it by a year,
- a period announced three years late,
- a security asked about before it had announced anything.

The four things this file is really about:

- **Collapse what is one fact; refuse what is two.** Identical rows fold; different rows do not,
  and `value_of` raises on the fields they differ on and answers on the fields they do not.
- **`update_flag` is not a rank.** Nothing here reads it as one, and one fixture makes that
  impossible anyway by carrying `'1'` on both rows.
- **A restatement with its own date is answerable.** Two announcements of one period order
  themselves; two rows under one announcement do not.
- **The horizon is refused, never filled.** A day before the first announcement raises.
"""

from __future__ import annotations

from datetime import date, datetime
from types import MappingProxyType

import pytest

from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
    STATEMENT_DATA_COLUMNS,
    AmbiguousReportError,
    FinancialStatementError,
    FinancialStatementHorizonError,
    ReportRow,
    build_statement_history,
    financial_ambiguity_report,
    statement_histories_from_panel_rows,
    statement_panel_columns,
)
from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME

# --------------------------------------------------------------------------------------
# Real rows, captured 2026-08-09 with the stored projection
# --------------------------------------------------------------------------------------


def _income(**values: float | None) -> dict[str, float | None]:
    """One `income` value mapping, defaulting every unnamed column to `None`."""
    row = dict.fromkeys(STATEMENT_DATA_COLUMNS[INCOME_DATASET], None)
    row.update(values)
    return row


def _indicator(**values: float | None) -> dict[str, float | None]:
    row = dict.fromkeys(STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET], None)
    row.update(values)
    return row


def _balance(**values: float | None) -> dict[str, float | None]:
    row = dict.fromkeys(STATEMENT_DATA_COLUMNS[BALANCE_SHEET_DATASET], None)
    row.update(values)
    return row


def _cashflow(**values: float | None) -> dict[str, float | None]:
    row = dict.fromkeys(STATEMENT_DATA_COLUMNS[CASH_FLOW_DATASET], None)
    row.update(values)
    return row


# income(000001.SZ, period=20170630): two rows, byte-identical apart from update_flag.
PINGAN_2017H1 = _income(
    total_revenue=54073000000.0,
    revenue=54073000000.0,
    operate_profit=16468000000.0,
    total_profit=16432000000.0,
    income_tax=3878000000.0,
    n_income=12554000000.0,
    n_income_attr_p=12554000000.0,
    basic_eps=0.68,
    ebit=40184000000.0,
)

# income(000001.SZ, period=20180630): the same shape except `ebit` is absent from the `1` row.
PINGAN_2018H1_FLAG0 = _income(
    total_revenue=57241000000.0,
    revenue=57241000000.0,
    operate_profit=17402000000.0,
    total_profit=17367000000.0,
    income_tax=3995000000.0,
    n_income=13372000000.0,
    n_income_attr_p=13372000000.0,
    basic_eps=0.73,
    ebit=39700000000.0,
)
PINGAN_2018H1_FLAG1 = {**PINGAN_2018H1_FLAG0, "ebit": None}

# income(603333.SH, period=20200331): `ebit` flips sign between the two versions.
JIALIN_2020Q1_FLAG0 = _income(
    total_revenue=205877275.37,
    revenue=205877275.37,
    oper_cost=164073680.51,
    operate_profit=-12199136.75,
    total_profit=-16040464.8,
    income_tax=-801193.81,
    n_income=-15239270.99,
    n_income_attr_p=-15047436.31,
    basic_eps=-0.03,
    ebit=3427524.01,
)
JIALIN_2020Q1_FLAG1 = {**JIALIN_2020Q1_FLAG0, "ebit": -7579086.33}

# income(600739.SH, period=20241231): BOTH rows carry update_flag='1', and they disagree about
# revenue, cost and f_ann_date.
LIAONING_2024_A = _income(
    total_revenue=11289276631.83,
    revenue=11289276631.83,
    oper_cost=9751514563.05,
    operate_profit=752021921.72,
    total_profit=739566307.28,
    income_tax=75370915.62,
    n_income=664195391.66,
    n_income_attr_p=209556865.25,
    basic_eps=0.14,
    ebit=39670495.5,
)
LIAONING_2024_B = {
    **LIAONING_2024_A,
    "total_revenue": 10769999495.94,
    "revenue": 10769999495.94,
    "oper_cost": 9232237427.16,
}

# fina_indicator(603049.SH, period=20241231): ten of the eleven stored columns disagree, `bps`
# by a factor of ten and `grossprofit_margin` by a change of sign.
DIMEI_2024_A = _indicator(
    bps=2.2206,
    roe=176.0751,
    roa=0.7395,
    netprofit_margin=83.6344,
    grossprofit_margin=-706.4726,
    or_yoy=-87.0377,
    netprofit_yoy=956.6008,
    ocfps=0.3864,
)
DIMEI_2024_B = _indicator(
    eps=4.81,
    bps=22.2055,
    roe=23.9249,
    roa=9.8934,
    netprofit_margin=9.6476,
    grossprofit_margin=19.4836,
    debt_to_assets=61.0094,
    or_yoy=11.354,
    netprofit_yoy=43.5698,
    ocfps=3.86,
    fcff=2432515794.8604,
)

# fina_indicator(600519.SH, period=20180331): one field, sign flipped.
MOUTAI_2018Q1_A = _indicator(
    eps=6.77,
    bps=79.5718,
    roe=8.8887,
    roa=9.0471,
    netprofit_margin=52.2756,
    grossprofit_margin=91.3055,
    debt_to_assets=21.7665,
    or_yoy=31.2393,
    netprofit_yoy=38.9309,
    ocfps=3.9289,
    fcff=-966053502.6718,
)
MOUTAI_2018Q1_B = {**MOUTAI_2018Q1_A, "fcff": 843920834.0382}

# fina_indicator(000001.SZ, period=20240630): one column absent on one row, one rounded.
PINGAN_IND_2024H1_A = _indicator(
    bps=21.2268,
    roe=5.4242,
    netprofit_margin=33.5516,
    debt_to_assets=91.6255,
    or_yoy=-12.9534,
    netprofit_yoy=1.938,
    ocfps=5.8602,
)
PINGAN_IND_2024H1_B = {**PINGAN_IND_2024H1_A, "eps": 1.23, "ocfps": 5.86}

# balancesheet(000002.SZ, period=20230630): only the share count differs, by 2.5%.
VANKE_2023H1_FLAG0 = _balance(
    total_assets=1684196409372.7,
    total_liab=1281551927215.46,
    total_hldr_eqy_exc_min_int=249326669106.12,
    total_cur_assets=1325043809366.61,
    total_cur_liab=981909082942.54,
    money_cap=122180878822.26,
    total_share=11630709471.0,
)
VANKE_2023H1_FLAG1 = {**VANKE_2023H1_FLAG0, "total_share": 11930709471.0}

# cashflow(300002.SZ, period=20230630): free cash flow flips sign.
SHENZHOU_2023H1_FLAG0 = _cashflow(
    n_cashflow_act=395838995.7,
    n_cashflow_inv_act=-271171253.84,
    n_cash_flows_fnc_act=-68063597.74,
    c_fr_sale_sg=2230094255.46,
    free_cashflow=-294173456.01,
)
SHENZHOU_2023H1_FLAG1 = {**SHENZHOU_2023H1_FLAG0, "free_cashflow": 316026933.8871}


# --------------------------------------------------------------------------------------
# Collapsing: what is one fact
# --------------------------------------------------------------------------------------


def test_two_rows_that_say_the_same_thing_become_one_version_carrying_both_labels() -> None:
    history = build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2017, 6, 30),
                announced_on=date(2017, 8, 11),
                first_announced_on=date(2017, 8, 11),
                revision_label=label,
                values=PINGAN_2017H1,
            )
            for label in ("0", "1")
        ],
    )

    (filing,) = history.filings
    assert filing.row_count == 2
    assert filing.collapsed_versions == 1
    assert not filing.is_ambiguous
    assert filing.disagreeing_fields == ()
    assert not filing.announcement_is_ambiguous
    assert filing.versions[0].labels == ("0", "1")
    assert filing.value_of("revenue") == 54073000000.0


def test_a_collapse_keeps_the_label_census_rather_than_discarding_it() -> None:
    """The labels are what `PartitionCoverage.revisions` counts; losing them would make the
    catalog and this contract disagree about what was stored."""
    history = build_statement_history(
        security="000002.SZ",
        dataset=BALANCE_SHEET_DATASET,
        rows=[
            ReportRow(
                period=date(2023, 6, 30),
                announced_on=date(2023, 8, 31),
                first_announced_on=date(2023, 8, 31),
                revision_label=label,
                values=values,
            )
            for label, values in (("0", VANKE_2023H1_FLAG0), ("1", VANKE_2023H1_FLAG1))
        ],
    )

    (filing,) = history.filings
    assert filing.is_ambiguous
    assert [version.labels for version in filing.versions] == [("0",), ("1",)]
    assert filing.collapsed_versions == 0


def test_fina_indicator_collapses_without_any_label_to_collapse_on() -> None:
    """82% of `fina_indicator`'s duplicate keys are byte-identical and it has no `update_flag`
    at all, so the collapse cannot be keyed on the label."""
    history = build_statement_history(
        security="600519.SH",
        dataset=FINANCIAL_INDICATOR_DATASET,
        rows=[
            ReportRow(
                period=date(2018, 3, 31),
                announced_on=date(2018, 4, 28),
                values=MOUTAI_2018Q1_A,
            ),
            ReportRow(
                period=date(2018, 3, 31),
                announced_on=date(2018, 4, 28),
                values=MOUTAI_2018Q1_A,
            ),
        ],
    )

    (filing,) = history.filings
    assert filing.row_count == 2
    assert filing.collapsed_versions == 1
    assert filing.versions[0].labels == ()
    assert filing.value_of("fcff") == -966053502.6718


# --------------------------------------------------------------------------------------
# Refusing: what is two facts
# --------------------------------------------------------------------------------------


def _moutai_history():
    return build_statement_history(
        security="600519.SH",
        dataset=FINANCIAL_INDICATOR_DATASET,
        rows=[
            ReportRow(period=date(2018, 3, 31), announced_on=date(2018, 4, 28), values=values)
            for values in (MOUTAI_2018Q1_A, MOUTAI_2018Q1_B)
        ],
    )


def test_a_field_the_versions_disagree_on_is_refused_by_name() -> None:
    (filing,) = _moutai_history().filings

    assert filing.is_ambiguous
    assert filing.disagreeing_fields == ("fcff",)
    with pytest.raises(AmbiguousReportError, match=r"2 versions disagree about 'fcff'"):
        filing.value_of("fcff")


def test_a_field_the_versions_agree_on_is_still_answered() -> None:
    """The refusal is per field. A `fcff` disagreement must not cost the caller `eps`."""
    (filing,) = _moutai_history().filings

    assert filing.value_of("eps") == 6.77
    assert filing.value_of("roe") == 8.8887
    assert filing.value_of("debt_to_assets") == 21.7665


def test_the_caller_can_see_both_answers_but_has_to_hold_both() -> None:
    (filing,) = _moutai_history().filings

    assert filing.values_of("fcff") == (-966053502.6718, 843920834.0382)


def test_a_sign_flip_is_refused_rather_than_averaged_or_ordered() -> None:
    history = build_statement_history(
        security="300002.SZ",
        dataset=CASH_FLOW_DATASET,
        rows=[
            ReportRow(
                period=date(2023, 6, 30),
                announced_on=date(2023, 8, 29),
                first_announced_on=date(2023, 8, 29),
                revision_label=label,
                values=values,
            )
            for label, values in (
                ("0", SHENZHOU_2023H1_FLAG0),
                ("1", SHENZHOU_2023H1_FLAG1),
            )
        ],
    )

    (filing,) = history.filings
    assert filing.disagreeing_fields == ("free_cashflow",)
    assert filing.value_of("n_cashflow_act") == 395838995.7
    with pytest.raises(AmbiguousReportError, match=r"'free_cashflow'"):
        filing.value_of("free_cashflow")


def test_a_missing_number_on_one_version_is_a_disagreement_not_a_fallback() -> None:
    """`income`'s commonest shape: 256 of its 259 differing pairs move `ebit`, usually between
    a number and nothing. Falling back to the populated one would be picking a version."""
    history = build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2018, 6, 30),
                announced_on=date(2018, 8, 16),
                first_announced_on=date(2018, 8, 16),
                revision_label=label,
                values=values,
            )
            for label, values in (("0", PINGAN_2018H1_FLAG0), ("1", PINGAN_2018H1_FLAG1))
        ],
    )

    (filing,) = history.filings
    assert filing.disagreeing_fields == ("ebit",)
    assert filing.values_of("ebit") == (39700000000.0, None)
    with pytest.raises(AmbiguousReportError, match=r"'ebit'"):
        filing.value_of("ebit")


def test_a_column_absent_from_every_version_answers_none_rather_than_refusing() -> None:
    """`None` is an answer -- the upstream cell was empty -- and is not an ambiguity."""
    history = build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2018, 6, 30),
                announced_on=date(2018, 8, 16),
                revision_label="0",
                values=PINGAN_2018H1_FLAG0,
            )
        ],
    )

    (filing,) = history.filings
    assert filing.value_of("oper_cost") is None


def test_ten_disagreeing_columns_are_all_named_and_the_eleventh_still_answers() -> None:
    history = build_statement_history(
        security="603049.SH",
        dataset=FINANCIAL_INDICATOR_DATASET,
        rows=[
            ReportRow(period=date(2024, 12, 31), announced_on=date(2025, 5, 15), values=values)
            for values in (DIMEI_2024_A, DIMEI_2024_B)
        ],
    )

    (filing,) = history.filings
    assert filing.disagreeing_fields == (
        "bps",
        "debt_to_assets",
        "eps",
        "fcff",
        "grossprofit_margin",
        "netprofit_margin",
        "netprofit_yoy",
        "ocfps",
        "or_yoy",
        "roa",
        "roe",
    )
    with pytest.raises(AmbiguousReportError, match=r"'bps' \(2.2206, 22.2055\)"):
        filing.value_of("bps")


def test_a_rounding_step_is_a_disagreement_and_is_not_tolerated_away() -> None:
    """`5.86` against `5.8602` is 0.003% and still two different published numbers. A tolerance
    here would be a threshold nobody could justify, and it would have to be wide enough to also
    swallow `bps` 22.2055 against 2.2206."""
    history = build_statement_history(
        security="000001.SZ",
        dataset=FINANCIAL_INDICATOR_DATASET,
        rows=[
            ReportRow(period=date(2024, 6, 30), announced_on=date(2024, 8, 16), values=values)
            for values in (PINGAN_IND_2024H1_A, PINGAN_IND_2024H1_B)
        ],
    )

    (filing,) = history.filings
    assert filing.disagreeing_fields == ("eps", "ocfps")
    assert filing.value_of("bps") == 21.2268
    # `value_of` has to refuse it too, not merely report it: a tolerance wide enough to pass
    # 5.86 against 5.8602 is a threshold nobody could defend, and it would sit on the same
    # code path that has to refuse `bps` 22.2055 against 2.2206.
    with pytest.raises(AmbiguousReportError, match=r"'ocfps' \(5.8602, 5.86\)"):
        filing.value_of("ocfps")


def test_two_rows_carrying_the_same_update_flag_are_still_two_versions() -> None:
    """`600739.SH`'s 2024 annual `income` arrives as two rows that BOTH say `update_flag='1'`.
    Any rule that ranked the pair by the flag would have nothing to rank."""
    history = build_statement_history(
        security="600739.SH",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2024, 12, 31),
                announced_on=date(2025, 4, 26),
                first_announced_on=first_announced_on,
                revision_label="1",
                values=values,
            )
            for first_announced_on, values in (
                (date(2025, 4, 26), LIAONING_2024_A),
                (date(2026, 4, 25), LIAONING_2024_B),
            )
        ],
    )

    (filing,) = history.filings
    assert filing.is_ambiguous
    assert [version.labels for version in filing.versions] == [("1",), ("1",)]
    assert filing.disagreeing_fields == ("oper_cost", "revenue", "total_revenue")
    assert filing.value_of("n_income_attr_p") == 209556865.25


def test_versions_that_differ_only_in_the_first_announcement_date_stay_apart() -> None:
    """`f_ann_date` is part of what the row says, so two rows that disagree about it are two
    rows. No number is ambiguous, and `announcement_is_ambiguous` is what reports it."""
    history = build_statement_history(
        security="600739.SH",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2024, 12, 31),
                announced_on=date(2025, 4, 26),
                first_announced_on=first_announced_on,
                revision_label="1",
                values=LIAONING_2024_A,
            )
            for first_announced_on in (date(2025, 4, 26), date(2026, 4, 25))
        ],
    )

    (filing,) = history.filings
    assert filing.is_ambiguous
    assert filing.disagreeing_fields == ()
    assert filing.announcement_is_ambiguous
    assert filing.value_of("revenue") == 11289276631.83


def test_reading_a_column_the_dataset_does_not_store_is_a_bug_not_an_ambiguity() -> None:
    (filing,) = _moutai_history().filings

    with pytest.raises(FinancialStatementError, match=r"'total_assets' is not a stored column"):
        filing.value_of("total_assets")


# --------------------------------------------------------------------------------------
# The point-in-time reads
# --------------------------------------------------------------------------------------


def _pingan_income_history():
    """Three periods of 000001.SZ, one of them announced under two versions."""
    return build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2017, 6, 30),
                announced_on=date(2017, 8, 11),
                first_announced_on=date(2017, 8, 11),
                revision_label="0",
                values=PINGAN_2017H1,
            ),
            ReportRow(
                period=date(2018, 6, 30),
                announced_on=date(2018, 8, 16),
                first_announced_on=date(2018, 8, 16),
                revision_label="0",
                values=PINGAN_2018H1_FLAG0,
            ),
            ReportRow(
                period=date(2018, 6, 30),
                announced_on=date(2018, 8, 16),
                first_announced_on=date(2018, 8, 16),
                revision_label="1",
                values=PINGAN_2018H1_FLAG1,
            ),
        ],
    )


def test_a_filing_is_invisible_the_day_before_it_was_announced() -> None:
    history = _pingan_income_history()

    assert history.periods_on(date(2018, 8, 15)) == (date(2017, 6, 30),)
    assert history.periods_on(date(2018, 8, 16)) == (date(2017, 6, 30), date(2018, 6, 30))


def test_the_latest_filing_on_the_announcement_day_is_the_new_one() -> None:
    history = _pingan_income_history()

    assert history.latest_filing_on(date(2018, 8, 16)).period == date(2018, 6, 30)
    assert history.latest_filing_on(date(2018, 8, 15)).period == date(2017, 6, 30)


def test_a_day_before_the_first_announcement_is_refused_rather_than_answered() -> None:
    history = _pingan_income_history()

    with pytest.raises(
        FinancialStatementHorizonError, match=r"had announced no income filing by 2017-08-10"
    ):
        history.latest_filing_on(date(2017, 8, 10))


def test_asking_for_a_period_before_it_was_announced_is_refused_by_name() -> None:
    history = _pingan_income_history()

    with pytest.raises(
        FinancialStatementHorizonError, match=r"had not announced its 2018-06-30 income"
    ):
        history.filing_for(date(2018, 6, 30), date(2018, 8, 15))


def test_a_restatement_with_its_own_date_resolves_to_the_later_announcement() -> None:
    """The shape `update_flag` cannot express and 3 of `income`'s 3,198 periods actually have:
    one period, two announcement days. Before the second day the first answer stands."""
    history = build_statement_history(
        security="600739.SH",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2024, 12, 31),
                announced_on=date(2025, 4, 26),
                revision_label="1",
                values=LIAONING_2024_A,
            ),
            ReportRow(
                period=date(2024, 12, 31),
                announced_on=date(2026, 4, 25),
                revision_label="1",
                values=LIAONING_2024_B,
            ),
        ],
    )

    early = history.filing_for(date(2024, 12, 31), date(2026, 4, 24))
    late = history.filing_for(date(2024, 12, 31), date(2026, 4, 25))

    assert early.announced_on == date(2025, 4, 26)
    assert early.value_of("revenue") == 11289276631.83
    assert late.announced_on == date(2026, 4, 25)
    assert late.value_of("revenue") == 10769999495.94


def test_a_period_announced_three_years_late_is_dated_at_the_announcement() -> None:
    """`001278.SZ` announced its 2018 annual `fina_indicator` on 2022-01-06. Dating it at the
    period end would make it readable through 2019, 2020 and 2021."""
    history = build_statement_history(
        security="001278.SZ",
        dataset=FINANCIAL_INDICATOR_DATASET,
        rows=[
            ReportRow(
                period=date(2018, 12, 31),
                announced_on=date(2022, 1, 6),
                values=_indicator(eps=0.42, bps=4.4014, fcff=-68487984.7221),
            )
        ],
    )

    assert history.periods_on(date(2021, 12, 31)) == ()
    assert history.periods_on(date(2022, 1, 6)) == (date(2018, 12, 31),)


def test_a_datetime_is_refused_where_a_date_is_required() -> None:
    history = _pingan_income_history()

    with pytest.raises(FinancialStatementError, match=r"day must be a datetime.date"):
        history.filings_on(datetime(2018, 8, 16, 12, 0))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Construction refuses the malformed
# --------------------------------------------------------------------------------------


def test_a_history_with_no_rows_is_refused() -> None:
    with pytest.raises(FinancialStatementError, match=r"needs at least one row"):
        build_statement_history(security="000001.SZ", dataset=INCOME_DATASET, rows=[])


def test_a_row_missing_a_projected_column_is_refused_rather_than_defaulted() -> None:
    partial = dict(PINGAN_2017H1)
    partial.pop("ebit")

    with pytest.raises(FinancialStatementError, match=r"must carry exactly the columns"):
        build_statement_history(
            security="000001.SZ",
            dataset=INCOME_DATASET,
            rows=[
                ReportRow(
                    period=date(2017, 6, 30),
                    announced_on=date(2017, 8, 11),
                    values=partial,
                )
            ],
        )


def test_an_unknown_dataset_is_refused_by_name() -> None:
    with pytest.raises(FinancialStatementError, match=r"'dividend' is not one of the financial"):
        statement_panel_columns("dividend")


# --------------------------------------------------------------------------------------
# The stored shape
# --------------------------------------------------------------------------------------


def test_only_fina_indicator_stores_no_revision_label_or_first_announcement() -> None:
    for dataset in (INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        assert statement_panel_columns(dataset)[:4] == (
            "report_period",
            "ann_date",
            "f_ann_date",
            "update_flag",
        )
    assert statement_panel_columns(FINANCIAL_INDICATOR_DATASET)[:2] == (
        "report_period",
        "ann_date",
    )
    assert "update_flag" not in statement_panel_columns(FINANCIAL_INDICATOR_DATASET)
    assert "f_ann_date" not in statement_panel_columns(FINANCIAL_INDICATOR_DATASET)


def test_the_projection_keeps_the_columns_the_versions_actually_fight_over() -> None:
    """`ebit`, `total_share`, `free_cashflow` and `fcff` carry most of each endpoint's
    disagreement. Projecting them away would make the panel look clean by not looking."""
    assert "ebit" in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert "total_share" in STATEMENT_DATA_COLUMNS[BALANCE_SHEET_DATASET]
    assert "free_cashflow" in STATEMENT_DATA_COLUMNS[CASH_FLOW_DATASET]
    assert "fcff" in STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET]


def test_histories_rebuild_from_stored_panel_rows() -> None:
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(INCOME_DATASET))
    rows = [
        (
            "000001.SZ",
            "2018-06-30",
            "2018-08-16",
            "2018-08-16",
            label,
            *[values[name] for name in STATEMENT_DATA_COLUMNS[INCOME_DATASET]],
        )
        for label, values in (("0", PINGAN_2018H1_FLAG0), ("1", PINGAN_2018H1_FLAG1))
    ]

    histories = statement_histories_from_panel_rows(
        dataset=INCOME_DATASET, columns=columns, rows=rows
    )

    (filing,) = histories["000001.SZ"].filings
    assert filing.period == date(2018, 6, 30)
    assert filing.announced_on == date(2018, 8, 16)
    assert filing.disagreeing_fields == ("ebit",)


def test_a_stored_row_whose_first_announcement_is_null_reads_back_as_none() -> None:
    """Defence rather than an observed case: `f_ann_date` was populated on all 3,836 `income`
    rows the probe read, and a fabricated date here would be worse than a null."""
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(INCOME_DATASET))
    rows = [
        (
            "000001.SZ",
            "2018-06-30",
            "2018-08-16",
            None,
            "0",
            *[PINGAN_2018H1_FLAG0[name] for name in STATEMENT_DATA_COLUMNS[INCOME_DATASET]],
        )
    ]

    histories = statement_histories_from_panel_rows(
        dataset=INCOME_DATASET, columns=columns, rows=rows
    )

    (filing,) = histories["000001.SZ"].filings
    assert filing.versions[0].first_announced_on is None


def test_panel_rows_missing_a_declared_column_are_refused() -> None:
    columns = [SUBJECT_COLUMN_NAME, *statement_panel_columns(INCOME_DATASET)]
    columns.remove("update_flag")

    with pytest.raises(FinancialStatementError, match=r"missing column\(s\) \['update_flag'\]"):
        statement_histories_from_panel_rows(dataset=INCOME_DATASET, columns=columns, rows=[])


def test_a_stored_value_that_is_not_a_number_is_refused() -> None:
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(FINANCIAL_INDICATOR_DATASET))
    rows = [("600519.SH", "2018-03-31", "2018-04-28", "6.77", *[None] * 10)]

    with pytest.raises(FinancialStatementError, match=r"column 'eps' must be a number or None"):
        statement_histories_from_panel_rows(
            dataset=FINANCIAL_INDICATOR_DATASET, columns=columns, rows=rows
        )


# --------------------------------------------------------------------------------------
# The ambiguity report
# --------------------------------------------------------------------------------------


def test_the_ambiguity_report_separates_what_collapsed_from_what_refuses() -> None:
    histories = {
        "600519.SH": _moutai_history(),
        "000001.SZ": _pingan_income_history(),
    }

    with pytest.raises(FinancialStatementError, match=r"is 'income', not 'fina_indicator'"):
        financial_ambiguity_report(dataset=FINANCIAL_INDICATOR_DATASET, histories=histories)

    report = financial_ambiguity_report(
        dataset=FINANCIAL_INDICATOR_DATASET, histories={"600519.SH": _moutai_history()}
    )
    assert report.filings == 1
    assert report.ambiguous_filings == 1
    assert report.collapsed_versions == 0
    assert report.ambiguous_field_reads["fcff"] == 1
    assert report.ambiguous_field_reads["eps"] == 0
    assert not report.is_clean


def test_a_corpus_whose_duplicates_all_collapse_reports_itself_clean() -> None:
    history = build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2017, 6, 30),
                announced_on=date(2017, 8, 11),
                first_announced_on=date(2017, 8, 11),
                revision_label=label,
                values=PINGAN_2017H1,
            )
            for label in ("0", "1")
        ],
    )

    report = financial_ambiguity_report(dataset=INCOME_DATASET, histories={"000001.SZ": history})

    assert report.is_clean
    assert report.collapsed_versions == 1
    assert report.filings == 1


# --------------------------------------------------------------------------------------
# The named limitations
# --------------------------------------------------------------------------------------


def test_every_dataset_is_named_and_the_limitations_are_unique_and_specific() -> None:
    assert FINANCIAL_STATEMENT_DATASETS == (
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
    )
    codes = [limitation.code for limitation in KNOWN_FINANCIAL_STATEMENT_LIMITATIONS]
    assert len(codes) == len(set(codes))
    assert "fina_indicator_has_no_version_column_at_all" in codes
    assert "a_correction_carries_no_instant_of_its_own" in codes
    assert "update_flag_does_not_say_which_version_is_current" in codes
    for limitation in KNOWN_FINANCIAL_STATEMENT_LIMITATIONS:
        assert len(limitation.detail) > 200, limitation.code


def test_the_stored_values_are_immutable_once_a_history_is_built() -> None:
    (filing,) = _moutai_history().filings

    assert isinstance(filing.versions[0].values, MappingProxyType)
    with pytest.raises(TypeError):
        filing.versions[0].values["eps"] = 0.0  # type: ignore[index]


def test_a_history_for_a_nameless_security_is_refused() -> None:
    with pytest.raises(FinancialStatementError, match=r"security must be a non-empty string"):
        build_statement_history(
            security="   ",
            dataset=INCOME_DATASET,
            rows=[
                ReportRow(
                    period=date(2017, 6, 30),
                    announced_on=date(2017, 8, 11),
                    values=PINGAN_2017H1,
                )
            ],
        )


def test_a_stored_subject_that_is_not_text_is_refused() -> None:
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(FINANCIAL_INDICATOR_DATASET))
    rows = [(None, "2018-03-31", "2018-04-28", *[None] * 11)]

    with pytest.raises(FinancialStatementError, match=r"column 'subject' must be a non-empty"):
        statement_histories_from_panel_rows(
            dataset=FINANCIAL_INDICATOR_DATASET, columns=columns, rows=rows
        )


def test_a_stored_date_that_is_not_iso_is_refused_by_column_name() -> None:
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(FINANCIAL_INDICATOR_DATASET))
    rows = [("600519.SH", "2018-13-45", "2018-04-28", *[None] * 11)]

    with pytest.raises(
        FinancialStatementError, match=r"column 'report_period' is not an ISO date: '2018-13-45'"
    ):
        statement_histories_from_panel_rows(
            dataset=FINANCIAL_INDICATOR_DATASET, columns=columns, rows=rows
        )


def test_the_ambiguity_report_refuses_a_dataset_it_does_not_know() -> None:
    with pytest.raises(FinancialStatementError, match=r"'dividend' is not one of the financial"):
        financial_ambiguity_report(dataset="dividend", histories={})


def test_the_ambiguity_report_counts_a_clock_only_disagreement_separately() -> None:
    """`600739.SH`'s 2024 annual: two rows, same numbers, different `f_ann_date`. That is one
    ambiguous filing with zero ambiguous field reads, which is why the two are separate counts."""
    history = build_statement_history(
        security="600739.SH",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2024, 12, 31),
                announced_on=date(2025, 4, 26),
                first_announced_on=first_announced_on,
                revision_label="1",
                values=LIAONING_2024_A,
            )
            for first_announced_on in (date(2025, 4, 26), date(2026, 4, 25))
        ],
    )

    report = financial_ambiguity_report(dataset=INCOME_DATASET, histories={"600739.SH": history})

    assert report.ambiguous_filings == 1
    assert report.ambiguous_announcements == 1
    assert set(report.ambiguous_field_reads.values()) == {0}
