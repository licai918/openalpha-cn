"""`domain/financial_statements.py` (`V2-P1-011`): one key, more than one answer.

Every fixture in this file is a verbatim row from a live probe on 2026-08-09, fetched with
exactly the projection the panel stores; the suite never touches the network. The fixtures were
chosen to span the shapes the probe actually found, not two extremes:

- a duplicate key whose rows are identical (collapses),
- a duplicate key that differs in **one** field, in the `None` / number direction,
- a duplicate key that differs in one field by a rounding step (`5.86` vs `5.8602`),
- a duplicate key that differs in one field by **4.2 parts per million** (`total_share`
  19,406,000,000 against 19,405,918,198) -- the tightest gap the probe found in any projection,
- a duplicate key that differs in one field by a **sign flip**,
- a duplicate key that differs in **ten** fields at once, one of them by a factor of ten,
- a key with **three** rows and three surviving versions, where the first two agree on a field
  the third contradicts,
- a key with **three** rows where the pair that collapses is not the leading pair,
- a duplicate key whose two rows carry the **same** `update_flag`,
- a duplicate key whose two rows disagree about `f_ann_date` and agree about everything else,
- a filing whose `f_ann_date` precedes its `ann_date`, and one where it follows it by a year,
- a history where an **earlier period was announced later** than a later one,
- a period announced three years late,
- a security asked about before it had announced anything.

The five things this file is really about:

- **Collapse what is one fact; refuse what is two.** Identical rows fold; different rows do not,
  and `value_of` raises on the fields they differ on and answers on the fields they do not.
  Neither side of that may depend on how many rows there are or which one arrived first.
- **`update_flag` is not a rank.** Nothing here reads it as one, and one fixture makes that
  impossible anyway by carrying `'1'` on both rows.
- **A restatement with its own date is answerable.** Two announcements of one period order
  themselves; two rows under one announcement do not.
- **The horizon is refused, never filled.** A day before the first announcement raises, and so
  does a day past the last announcement year the read covered.
- **A period is not an announcement.** `latest_filing_on` answers with the latest *period*, and
  `920403.BJ` is the fixture where the two orderings disagree.
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

# balancesheet(002538.SZ, period=20221231, ann=20230421): THREE rows, three distinct versions,
# each with its own `f_ann_date`. `total_cur_assets` is the field that matters here: the first
# two versions agree on it and the third does not.
BAL_002538_2022_V1 = _balance(
    total_assets=6943496181.91,
    total_liab=1597173490.89,
    total_hldr_eqy_exc_min_int=5346322691.02,
    total_cur_assets=3013409350.92,
    total_cur_liab=1250661700.8,
    money_cap=1022156257.3,
    total_share=853555763.0,
)
BAL_002538_2022_V2 = {
    **BAL_002538_2022_V1,
    "total_assets": 6915040693.62,
    "total_liab": 1617660884.88,
    "total_hldr_eqy_exc_min_int": 5297379808.74,
    "total_cur_liab": 1271149094.79,
}
BAL_002538_2022_V3 = {
    **BAL_002538_2022_V1,
    "total_assets": 6829366152.22,
    "total_liab": 1679268919.75,
    "total_hldr_eqy_exc_min_int": 5150097232.47,
    "total_cur_assets": 3006409350.92,
    "total_cur_liab": 1318187129.66,
}

# balancesheet(600965.SH, period=20221231, ann=20230325): THREE rows whose seven stored numbers
# are all equal; two of them also share `f_ann_date=20230325` and the third says 20260725. So
# one pair collapses and the odd row does not -- and which of the three arrives first is not
# stable: an offset-paged request served (20260725, 20230325, 20230325) and a year-windowed one
# served (20230325, 20230325, 20260725), same rows, same day.
BAL_600965_2022 = _balance(
    total_assets=2487402852.19,
    total_liab=331279557.86,
    total_hldr_eqy_exc_min_int=2118961703.92,
    total_cur_assets=1435468959.12,
    total_cur_liab=245047318.42,
    money_cap=640016852.29,
    total_share=818700955.0,
)

# balancesheet(000001.SZ, period=20231231, ann=20240315): the two rows differ in exactly one
# field and by 81,802 parts in 19,406,000,000 -- 4.2 parts per million, the tightest
# disagreement the probe found anywhere inside a projection.
BAL_000001_2023_FLAG0 = _balance(
    total_assets=5587116000000.0,
    total_liab=5114788000000.0,
    total_hldr_eqy_exc_min_int=472328000000.0,
    total_share=19406000000.0,
)
BAL_000001_2023_FLAG1 = {**BAL_000001_2023_FLAG0, "total_share": 19405918198.0}

# income(920403.BJ), the whole 2023-2024 stretch of its history. On 2024-01-05 it re-announced
# BOTH its 2022 annual and its 2023 interim, months after its 2023 Q3 report -- so the newest
# announcement that day carries the OLDEST period of the three.
INC_920403_2022A_ORIGINAL = _income(
    total_revenue=197624210.5,
    revenue=197624210.5,
    oper_cost=135710952.58,
    operate_profit=40463770.97,
    total_profit=41275179.33,
    income_tax=34893.27,
    n_income=41240286.06,
    n_income_attr_p=41338995.01,
    basic_eps=1.05,
)
INC_920403_2022A_RESTATED = {**INC_920403_2022A_ORIGINAL, "ebit": 41082309.3}
INC_920403_2023H1_ORIGINAL = _income(
    total_revenue=47232130.46,
    revenue=47232130.46,
    oper_cost=31366061.57,
    operate_profit=10663684.75,
    total_profit=10035731.2,
    income_tax=-83129.74,
    n_income=10118860.94,
    n_income_attr_p=10639586.33,
    basic_eps=0.27,
)
INC_920403_2023H1_RESTATED = {**INC_920403_2023H1_ORIGINAL, "ebit": 10571562.9}
INC_920403_2023Q3 = _income(
    total_revenue=54528997.88,
    revenue=54528997.88,
    oper_cost=36085604.13,
    operate_profit=6327212.69,
    total_profit=7260859.14,
    income_tax=-95541.23,
    n_income=7356400.37,
    n_income_attr_p=7947696.9,
    basic_eps=0.2,
)


def _income_920403_rows() -> list[ReportRow]:
    """A contiguous slice of `920403.BJ`'s income history, in the descending-announcement order
    the endpoint serves. Nothing downstream may depend on that order, which is why the fixture
    keeps it rather than tidying it into the order the assertions read in."""
    return [
        ReportRow(
            period=date(2022, 12, 31),
            announced_on=date(2024, 1, 5),
            first_announced_on=date(2024, 1, 5),
            revision_label="1",
            values=INC_920403_2022A_RESTATED,
        ),
        ReportRow(
            period=date(2023, 6, 30),
            announced_on=date(2024, 1, 5),
            first_announced_on=date(2024, 1, 5),
            revision_label="1",
            values=INC_920403_2023H1_RESTATED,
        ),
        ReportRow(
            period=date(2023, 9, 30),
            announced_on=date(2023, 11, 14),
            first_announced_on=date(2023, 11, 14),
            revision_label="0",
            values=INC_920403_2023Q3,
        ),
        ReportRow(
            period=date(2023, 6, 30),
            announced_on=date(2023, 8, 1),
            first_announced_on=date(2023, 11, 24),
            revision_label="1",
            values=INC_920403_2023H1_ORIGINAL,
        ),
        ReportRow(
            period=date(2023, 6, 30),
            announced_on=date(2023, 8, 1),
            first_announced_on=date(2023, 8, 1),
            revision_label="0",
            values=INC_920403_2023H1_ORIGINAL,
        ),
        ReportRow(
            period=date(2022, 12, 31),
            announced_on=date(2023, 3, 14),
            first_announced_on=date(2023, 4, 29),
            revision_label="0",
            values=INC_920403_2022A_ORIGINAL,
        ),
    ]


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


def test_a_duplicate_that_is_not_the_first_version_still_collapses() -> None:
    """`600965.SH`'s 2022 annual `balancesheet` arrives as three rows with the same seven
    numbers, two of them also sharing `f_ann_date=20230325` and the third saying 20260725. The
    pair that folds is therefore not always the leading pair -- an offset-paged request put the
    odd row first and a year-windowed one put it last -- so the collapse has to compare each row
    against every version it has already kept, not just the first."""
    rows_by_announcement = [
        (date(2026, 7, 25), "1"),
        (date(2023, 3, 25), "0"),
        (date(2023, 3, 25), "1"),
    ]
    for order in (rows_by_announcement, list(reversed(rows_by_announcement))):
        history = build_statement_history(
            security="600965.SH",
            dataset=BALANCE_SHEET_DATASET,
            rows=[
                ReportRow(
                    period=date(2022, 12, 31),
                    announced_on=date(2023, 3, 25),
                    first_announced_on=first_announced_on,
                    revision_label=label,
                    values=BAL_600965_2022,
                )
                for first_announced_on, label in order
            ],
        )

        (filing,) = history.filings
        assert filing.row_count == 3
        assert len(filing.versions) == 2, order
        assert filing.collapsed_versions == 1, order
        assert filing.disagreeing_fields == ()
        assert filing.announcement_is_ambiguous
        assert sorted(version.labels for version in filing.versions) == [("0", "1"), ("1",)]


def test_a_four_parts_per_million_difference_is_two_versions_not_one() -> None:
    """`000001.SZ`'s 2023 annual `balancesheet` gives `total_share` as 19,406,000,000 on one row
    and 19,405,918,198 on the other, and agrees on everything else. That is 4.2 parts per
    million -- the tightest disagreement the probe found inside any projection -- so a relative
    tolerance in the collapse would have to be under that to be harmless, and one loose enough
    to be worth writing folds a rounded share count onto an exact one. The share count is the
    denominator of every per-share number a factor computes."""
    history = build_statement_history(
        security="000001.SZ",
        dataset=BALANCE_SHEET_DATASET,
        rows=[
            ReportRow(
                period=date(2023, 12, 31),
                announced_on=date(2024, 3, 15),
                first_announced_on=date(2024, 3, 15),
                revision_label=label,
                values=values,
            )
            for label, values in (
                ("0", BAL_000001_2023_FLAG0),
                ("1", BAL_000001_2023_FLAG1),
            )
        ],
    )

    (filing,) = history.filings
    assert filing.row_count == 2
    assert filing.collapsed_versions == 0
    assert filing.is_ambiguous
    assert filing.disagreeing_fields == ("total_share",)
    assert filing.values_of("total_share") == (19406000000.0, 19405918198.0)
    assert filing.value_of("total_assets") == 5587116000000.0
    with pytest.raises(AmbiguousReportError, match=r"'total_share' \(19406000000.0, 194059"):
        filing.value_of("total_share")


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


def _three_version_history():
    """`002538.SZ`'s 2022 annual `balancesheet`: three rows, three surviving versions."""
    return build_statement_history(
        security="002538.SZ",
        dataset=BALANCE_SHEET_DATASET,
        rows=[
            ReportRow(
                period=date(2022, 12, 31),
                announced_on=date(2023, 4, 21),
                first_announced_on=first_announced_on,
                revision_label=label,
                values=values,
            )
            for first_announced_on, label, values in (
                (date(2023, 4, 21), "0", BAL_002538_2022_V1),
                (date(2025, 5, 21), "1", BAL_002538_2022_V2),
                (date(2026, 4, 29), "1", BAL_002538_2022_V3),
            )
        ],
    )


def test_a_field_the_first_two_of_three_versions_agree_on_is_still_refused() -> None:
    """A key is not limited to two rows. `002538.SZ`'s 2022 annual `balancesheet` arrives as
    three, and on `total_cur_assets` the first two say 3,013,409,350.92 while the third says
    3,006,409,350.92 -- so a comparison that stopped at the second version would answer with a
    number one of the published rows contradicts."""
    (filing,) = _three_version_history().filings

    assert filing.row_count == 3
    assert len(filing.versions) == 3
    assert filing.collapsed_versions == 0
    assert filing.values_of("total_cur_assets") == (
        3013409350.92,
        3013409350.92,
        3006409350.92,
    )
    with pytest.raises(AmbiguousReportError, match=r"3 versions disagree about 'total_cur_assets'"):
        filing.value_of("total_cur_assets")
    assert filing.value_of("money_cap") == 1022156257.3
    assert filing.value_of("total_share") == 853555763.0


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


def test_the_latest_filing_is_the_latest_period_not_the_latest_announcement() -> None:
    """On 2024-01-05 `920403.BJ` re-announced both its 2022 annual and its 2023 interim, six
    weeks after its 2023 Q3 report. So the newest announcement that day carries the OLDEST of
    the three periods, and ordering the filings by announcement would hand a reader standing on
    2024-01-05 a half-year report as its most recent period.

    Rare and not hypothetical: over a 76-security probe on 2026-08-09, argmax by period and
    argmax by announcement differ on 12 of `income`'s 3,796 answerable days, 11 of
    `balancesheet`'s 3,890, 11 of `cashflow`'s 3,392 and 16 of `fina_indicator`'s 3,901 -- 11,
    10, 10 and 13 of the 76 securities have at least one such day."""
    history = build_statement_history(
        security="920403.BJ", dataset=INCOME_DATASET, rows=_income_920403_rows()
    )

    latest = history.latest_filing_on(date(2024, 1, 5))

    assert latest.period == date(2023, 9, 30)
    assert latest.announced_on == date(2023, 11, 14)
    assert history.latest_filing_on(date(2024, 1, 4)).period == date(2023, 9, 30)
    assert history.periods_on(date(2024, 1, 5)) == (
        date(2022, 12, 31),
        date(2023, 6, 30),
        date(2023, 9, 30),
    )


def test_the_restated_period_is_still_reachable_by_name_on_its_own_announcement_day() -> None:
    """The same history from the other side: `filing_for` names the period, so 2024-01-05's
    re-announcement of the 2022 annual is exactly what it returns -- and the day before, the
    2023-03-14 version, which had no `ebit` at all."""
    history = build_statement_history(
        security="920403.BJ", dataset=INCOME_DATASET, rows=_income_920403_rows()
    )

    before = history.filing_for(date(2022, 12, 31), date(2024, 1, 4))
    after = history.filing_for(date(2022, 12, 31), date(2024, 1, 5))

    assert before.announced_on == date(2023, 3, 14)
    assert before.value_of("ebit") is None
    assert after.announced_on == date(2024, 1, 5)
    assert after.value_of("ebit") == 41082309.3


def test_filings_are_ordered_however_the_endpoint_served_the_rows() -> None:
    """`_income_920403_rows` is in response order, newest announcement first, and the ordering
    is not cosmetic: `covered_from` reads `filings[0]`, so a history that kept the response
    order would report a security's coverage as beginning at its most recent filing."""
    history = build_statement_history(
        security="920403.BJ", dataset=INCOME_DATASET, rows=_income_920403_rows()
    )

    assert history.covered_from == date(2023, 3, 14)
    assert [filing.announced_on for filing in history.filings] == [
        date(2023, 3, 14),
        date(2023, 8, 1),
        date(2023, 11, 14),
        date(2024, 1, 5),
        date(2024, 1, 5),
    ]
    assert [filing.period for filing in history.filings] == [
        date(2022, 12, 31),
        date(2023, 6, 30),
        date(2023, 9, 30),
        date(2022, 12, 31),
        date(2023, 6, 30),
    ]


def test_a_day_past_the_last_year_read_is_refused_rather_than_answered_stale() -> None:
    """The `answerable_through` bound, on the history that motivates it. A read that covered
    2023 and stopped holds the 2023-03-14 version of the 2022 annual and nothing from
    2024-01-05, so every day in 2023 is answered exactly right and every later day would be
    answered from a filing that had already been superseded."""
    rows = [row for row in _income_920403_rows() if row.announced_on < date(2024, 1, 1)]
    bounded = build_statement_history(
        security="920403.BJ", dataset=INCOME_DATASET, rows=rows, answerable_through=2023
    )
    unbounded = build_statement_history(security="920403.BJ", dataset=INCOME_DATASET, rows=rows)

    assert bounded.filing_for(date(2022, 12, 31), date(2023, 12, 31)).value_of("ebit") is None
    assert bounded.latest_filing_on(date(2023, 12, 31)).period == date(2023, 9, 30)
    # The boundary is the last day of the bounding year and the first day after it. 2024-01-01
    # is already outside, five days before the restatement this read cannot see.
    with pytest.raises(FinancialStatementHorizonError, match=r"2024-01-01 is after 2023"):
        bounded.latest_filing_on(date(2024, 1, 1))
    with pytest.raises(
        FinancialStatementHorizonError,
        match=r"2026-08-01 is after 2023, the last announcement year",
    ):
        bounded.filing_for(date(2022, 12, 31), date(2026, 8, 1))
    with pytest.raises(FinancialStatementHorizonError, match=r"is after 2023"):
        bounded.latest_filing_on(date(2026, 8, 1))
    assert unbounded.latest_filing_on(date(2026, 8, 1)).period == date(2023, 9, 30)


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


def test_a_not_a_number_is_refused_rather_than_becoming_a_version_of_its_own() -> None:
    """Defence, not an observed case: `providers/tushare.py` refuses a non-finite cell at the
    boundary, so nothing serves one. It matters because both rules here are built on `==`, and
    `nan != nan` -- two rows identical apart from a shared `nan` would be reported as two
    versions and `value_of` would raise on a field nobody disagreed about."""
    with pytest.raises(FinancialStatementError, match=r"column 'ebit' must be a finite number"):
        build_statement_history(
            security="000001.SZ",
            dataset=INCOME_DATASET,
            rows=[
                ReportRow(
                    period=date(2017, 6, 30),
                    announced_on=date(2017, 8, 11),
                    values={**PINGAN_2017H1, "ebit": float("nan")},
                )
            ],
        )


def test_the_two_spellings_of_zero_are_one_answer() -> None:
    """`-0.0` and `0.0` compare equal, so they collapse. That is the right answer -- they are
    the same number -- and pinning it keeps a future `nan` guard from being widened into a
    general float-identity rule."""
    history = build_statement_history(
        security="000001.SZ",
        dataset=INCOME_DATASET,
        rows=[
            ReportRow(
                period=date(2017, 6, 30),
                announced_on=date(2017, 8, 11),
                revision_label=label,
                values={**PINGAN_2017H1, "oper_cost": zero},
            )
            for label, zero in (("0", 0.0), ("1", -0.0))
        ],
    )

    (filing,) = history.filings
    assert filing.collapsed_versions == 1
    assert not filing.is_ambiguous


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


def test_the_report_counts_every_field_a_filing_disagrees_on() -> None:
    """One filing is routinely ambiguous on several columns at once -- 20 of `income`'s, 18 of
    `balancesheet`'s and 58 of `fina_indicator`'s ambiguous keys in a 76-security probe -- and
    `ambiguous_field_reads` is what `V2-P1-012` will publish, so counting one field per filing
    would understate every column but the alphabetically first."""
    report = financial_ambiguity_report(
        dataset=BALANCE_SHEET_DATASET, histories={"002538.SZ": _three_version_history()}
    )

    assert report.filings == 1
    assert report.ambiguous_filings == 1
    assert dict(report.ambiguous_field_reads) == {
        "total_assets": 1,
        "total_liab": 1,
        "total_hldr_eqy_exc_min_int": 1,
        "total_cur_assets": 1,
        "total_cur_liab": 1,
        "money_cap": 0,
        "total_share": 0,
    }

    indicators = financial_ambiguity_report(
        dataset=FINANCIAL_INDICATOR_DATASET,
        histories={
            "603049.SH": build_statement_history(
                security="603049.SH",
                dataset=FINANCIAL_INDICATOR_DATASET,
                rows=[
                    ReportRow(
                        period=date(2024, 12, 31), announced_on=date(2025, 5, 15), values=values
                    )
                    for values in (DIMEI_2024_A, DIMEI_2024_B)
                ],
            )
        },
    )
    assert sum(indicators.ambiguous_field_reads.values()) == 11
    assert set(indicators.ambiguous_field_reads.values()) == {1}


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


def test_the_two_gaps_the_review_found_are_named_rather_than_only_fixed() -> None:
    """A limitation is only useful if a caller can find it by name and read a number off it.
    Both of these are disclosure, not defect: the collapse is right about what it compares, and
    a partial read is right about the days it covers -- the hazard is that neither says so."""
    named = {
        limitation.code: limitation.detail for limitation in KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
    }

    projection = named["the_merge_rule_is_agreement_in_the_projection"]
    assert "133" in projection
    assert "fix_assets" in projection
    assert "undistr_porfit" in projection

    window = named["a_partial_year_read_answers_from_inside_its_window"]
    assert "920403.BJ" in window
    assert "answerable_through" in window


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
