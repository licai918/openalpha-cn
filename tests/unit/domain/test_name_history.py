"""The name-history and ST contract (`V2-P1-005`), against real `namechange` rows.

Every fixture below is verbatim Tushare `namechange` output captured while this task was
implemented, in the descending `start_date` order the endpoint returns. No test here touches
the network: the rows are the data, inlined.

Two clocks live in this dataset and the whole point of this file is that they are never
merged. `ann_date` says when a rename was **announced**; `start_date` says when it took
**effect**; the two can be seven months apart (`000001.SZ` was announced 平安银行 on
2012-01-20 and became it on 2012-08-02) and can also be the same day (its 1991 record). A
contract with one clock has to pick, and either pick is wrong somewhere: keying on `ann_date`
renames the security seven months early, keying on `start_date` alone makes the announcement
invisible to a point-in-time reader who did in fact have it.

The third thing pinned here is the **ST prefix grammar**, and it is wrong on real history in
two independent ways. The 2005-2007 share-reform era prefixed a `G` (reform done) or `S`
(reform pending) *in front of* the ST marker: across the full 14,166-row corpus the naive
`name.startswith(("ST", "*ST"))` marks 2,592 rows and the reform-aware rule 2,827 -- **235
rows** where a genuinely special-treated name reads as ordinary. And pre-2020 ChiNext wore
退市风险警示 as a **bare `*`** with no `ST` behind it, which is two more rows (`*欣泰`,
`*金亚`; strict rule 2,829) and is what `CHINEXT_RISK_WARNING` and `GOLDEN_ARMOUR` exist for.
The naive rule has zero false positives in either direction, which is exactly why nobody
notices.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pytest

from openalpha_cn.domain.name_history import (
    NameHistoryError,
    NameHistoryHorizonError,
    NameRecord,
    RiskWarning,
    build_name_history,
    name_histories_from_panel_rows,
    risk_warning_of,
)

# --- real Tushare `namechange` rows ---------------------------------------------------
# (ts_code, name, start_date, end_date, ann_date, change_reason)

PING_AN_BANK = (
    ("000001.SZ", "平安银行", "20120802", None, "20120120", "其他"),
    ("000001.SZ", "深发展A", "20070620", "20120801", "20070614", "其他"),
    ("000001.SZ", "S深发展A", "20061009", "20070619", "20060928", "其他"),
    ("000001.SZ", "深发展A", "19910403", "20061008", "19910403", "其他"),
)

CENTURY_STAR = (
    ("000005.SZ", "ST星源", "20210506", None, "20210430", "ST"),
    ("000005.SZ", "世纪星源", "20080625", "20210505", "20080624", "撤销ST"),
    ("000005.SZ", "ST星源", "20061009", "20080624", "20060928", "其他"),
    ("000005.SZ", "GST星源", "20060731", "20061008", "20060727", "其他"),
    ("000005.SZ", "ST星源", "20040518", "20060730", "20040517", "撤消*ST并实行ST"),
    ("000005.SZ", "*ST星源", "20030512", "20040517", "20030430", "从ST变为*ST"),
    ("000005.SZ", "ST星源", "20030416", "20030511", "20030415", "ST"),
    ("000005.SZ", "世纪星源", "19960615", "20030415", "19960430", "其他"),
    ("000005.SZ", "深星源A", "19940103", "19960614", "19940103", "其他"),
    ("000005.SZ", "深原野A", "19901210", "19940102", "19901210", "其他"),
)

GOLDEN_FIELD = (
    ("000003.SZ", "PT金田A", "20010511", None, "20010509", "PT"),
    ("000003.SZ", "ST金田A", "20000509", "20010510", "20000429", "ST"),
    ("000003.SZ", "深金田A", "19910703", "20000508", "19910703", "其他"),
)

# Real rows carrying every marker form the grammar has to survive.
MARKER_ROWS = (
    ("000004.SZ", "G*ST国农", "20060818", "20061008", "20060816", "其他"),
    ("000004.SZ", "ST国农", "20070525", "20090420", "20070524", "撤消*ST并实行ST"),
    ("000010.SZ", "*ST华新", "20060509", "20061008", "20060508", "*ST"),
    ("000010.SZ", "SST华新", "20070321", "20130718", "20070320", "撤消*ST并实行ST"),
    ("600087.SH", "退市长油", "20140421", None, "20140412", "退市整理期"),
    ("688555.SH", "退市泽达", "20230608", None, "20230601", "退市整理期"),
    ("990018.SH", "G上港", "20050822", "20061008", "20050817", "其他"),
)

# The two securities whose 退市风险警示 was worn as a bare `*`, complete histories. Before
# 2020 创业板 ran no ST regime, so the warning had no `ST` behind it -- and these four names
# are the corpus's only ones that begin with `*` and are not `*ST`. A grammar that tested the
# literal `*ST` answered `RiskWarning.none` for both windows.
CHINEXT_RISK_WARNING = (
    ("300372.SZ", "欣泰退", "20170717", None, "20170623", "退市整理期"),
    ("300372.SZ", "欣泰电气", "20160823", "20170716", "20160819", "撤销高风险警示"),
    ("300372.SZ", "*欣泰", "20160712", "20160822", "20160708", "高风险警示"),
    ("300372.SZ", "欣泰电气", "20140127", "20160711", "20140127", "其他"),
)

GOLDEN_ARMOUR = (
    ("300028.SZ", "金亚退", "20200618", None, "20200617", "退市整理期"),
    ("300028.SZ", "金亚科技", "20180808", "20200617", "20180808", "撤销高风险警示"),
    ("300028.SZ", "*金亚", "20180627", "20180807", "20180626", "高风险警示"),
    ("300028.SZ", "金亚科技", "20091030", "20180626", "20091030", "其他"),
)

IMPOSING_REASONS = frozenset(
    {
        "ST",
        "*ST",
        "从ST变为*ST",
        "撤消*ST并实行ST",
        "叠加ST",
        "叠加*ST",
        "PT",
        "高风险警示",
    }
)
"""The `change_reason` values that *impose* special treatment, out of the corpus's fifteen.

Eight of them, and the eighth is the one this set used to be missing. `高风险警示` is
ChiNext's pre-2020 spelling of 退市风险警示, it occurs on exactly two rows, and both of those
rows carry a bare-`*` name -- so a witness scan that omitted the reason could never reach the
two names the grammar got wrong. The five revoking reasons (`撤销ST`, `撤销*ST`, `撤销PT`,
`撤销高风险警示`, `撤销叠加*ST`) are deliberately **not** here: they land on ordinary names,
which is the point of a revocation.
"""


def _iso(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


def _records(rows) -> tuple[NameRecord, ...]:
    return tuple(
        NameRecord(
            ts_code=ts_code,
            name=name,
            effective_from=_iso(start),
            announced_on=_iso(ann),
            change_reason=reason,
        )
        for ts_code, name, start, _end, ann, reason in rows
    )


def _history(rows=PING_AN_BANK):
    return build_name_history(rows[0][0], _records(rows))


# --- the two clocks, never merged -----------------------------------------------------


def test_the_name_on_a_day_is_the_one_in_effect_not_the_one_already_announced() -> None:
    history = _history()
    # Announced 2012-01-20, effective 2012-08-02 -- seven months apart.
    assert history.name_on(date(2012, 3, 1)) == "深发展A"
    assert history.name_on(date(2012, 8, 1)) == "深发展A"
    assert history.name_on(date(2012, 8, 2)) == "平安银行"
    assert history.name_on(date(2012, 9, 1)) == "平安银行"


def test_an_announced_but_not_yet_effective_rename_is_reported_without_being_applied() -> None:
    history = _history()
    pending = history.pending_at(date(2012, 3, 1))
    assert tuple(record.name for record in pending) == ("平安银行",)
    assert pending[0].announced_on == date(2012, 1, 20)
    assert pending[0].effective_from == date(2012, 8, 2)
    assert history.name_on(date(2012, 3, 1)) == "深发展A"
    # One day before the announcement there is nothing to report.
    assert history.pending_at(date(2012, 1, 19)) == ()


def test_the_announcement_clock_bounds_what_an_observer_could_have_known() -> None:
    history = _history()
    observer = history.announced_by(date(2012, 1, 19))
    # The 2012-01-19 observer is wrong about 2012-09-01, and that is the correct behaviour:
    # the rename had not been announced yet.
    assert observer.name_on(date(2012, 9, 1)) == "深发展A"
    assert history.name_on(date(2012, 9, 1)) == "平安银行"


def test_an_announcement_filter_that_removes_everything_is_refused() -> None:
    with pytest.raises(NameHistoryHorizonError, match=r"nothing about 000001\.SZ was announced"):
        _history().announced_by(date(1991, 4, 2))


def test_the_same_day_case_is_real_and_is_not_treated_as_a_missing_announcement() -> None:
    history = _history()
    first = history.records[0]
    assert first.announced_on == first.effective_from == date(1991, 4, 3)
    assert history.name_on(date(1991, 4, 3)) == "深发展A"


# --- `end_date=None` --------------------------------------------------------------------


def test_an_absent_upstream_end_date_marks_the_record_in_effect_not_a_missing_value() -> None:
    history = _history()
    assert PING_AN_BANK[0][3] is None
    for day in (date(2012, 8, 2), date(2019, 6, 28), date(2026, 8, 8)):
        assert history.name_on(day) == "平安银行"


def test_the_upstream_end_date_agrees_with_the_successors_start_date() -> None:
    """`end_date` is kept as a witness and never stored: it is derivable from the successor's
    `start_date`, and storing it would put an unannounced future rename on the record that is
    currently in effect. Every fixture is checked in both directions."""
    for rows in (PING_AN_BANK, CENTURY_STAR, GOLDEN_FIELD):
        ordered = sorted(rows, key=lambda row: row[2])
        for earlier, later in pairwise(ordered):
            assert earlier[3] is not None
            assert (_iso(later[2]) - _iso(earlier[3])).days == 1
        assert ordered[-1][3] is None


def test_a_delisted_securitys_last_name_record_never_closes() -> None:
    """`000003.SZ` was delisted on 2002-06-14, and its final `namechange` record is still
    open-ended -- the registry calls it `PT金田A(退)` while this dataset calls it `PT金田A`
    for ever after. That disagreement is real and is not reconciled here: this contract knows
    nothing about delisting, and `stock_universe.py` is the other half of the answer."""
    history = _history(GOLDEN_FIELD)
    assert GOLDEN_FIELD[0][3] is None
    assert history.name_on(date(2002, 6, 14)) == "PT金田A"
    assert history.name_on(date(2026, 8, 8)) == "PT金田A"


# --- the ST prefix grammar --------------------------------------------------------------


def test_the_share_reform_prefixes_do_not_hide_the_special_treatment_marker() -> None:
    assert risk_warning_of("*ST星源") is RiskWarning.star_st
    assert risk_warning_of("ST星源") is RiskWarning.st
    assert risk_warning_of("GST星源") is RiskWarning.st
    assert risk_warning_of("SST华新") is RiskWarning.st
    assert risk_warning_of("G*ST国农") is RiskWarning.star_st
    assert risk_warning_of("S*ST兰宝") is RiskWarning.star_st
    assert risk_warning_of("PT金田A") is RiskWarning.pt


def test_a_bare_share_reform_prefix_is_not_a_special_treatment_marker() -> None:
    assert risk_warning_of("S深发展A") is RiskWarning.none
    assert risk_warning_of("G上港") is RiskWarning.none
    assert risk_warning_of("平安银行") is RiskWarning.none
    assert risk_warning_of("世纪星源") is RiskWarning.none


def test_the_delisting_period_naming_is_classified_rather_than_read_as_ordinary() -> None:
    assert risk_warning_of("退市长油") is RiskWarning.delisting_process
    assert risk_warning_of("退市泽达") is RiskWarning.delisting_process
    assert risk_warning_of("国华退") is RiskWarning.delisting_process
    assert risk_warning_of("神城A退") is RiskWarning.delisting_process


def test_the_naive_prefix_test_misses_exactly_the_share_reform_forms() -> None:
    names = tuple(row[1] for row in (*CENTURY_STAR, *GOLDEN_FIELD, *MARKER_ROWS, *PING_AN_BANK))
    naive = {name for name in names if name.startswith(("ST", "*ST"))}
    strict = {
        name for name in names if risk_warning_of(name) in (RiskWarning.st, RiskWarning.star_st)
    }
    assert strict - naive == {"GST星源", "SST华新", "G*ST国农"}
    assert naive - strict == set()


def test_the_change_reason_column_is_an_independent_witness_that_never_disagrees() -> None:
    """`change_reason` labels the transition, the name carries the state, and the two are
    derived from different upstream columns. Across the full 14,166-row corpus a live probe
    found zero rows where a reason imposing special treatment lands on a name the grammar
    calls ordinary. The reason cannot replace the name -- a `撤销PT` transition lands on
    `ST吉轻工`, still marked -- which is why the state is read from the name."""
    imposing = IMPOSING_REASONS
    rows = (*CENTURY_STAR, *GOLDEN_FIELD, *MARKER_ROWS)
    checked = 0
    for _code, name, _start, _end, _ann, reason in rows:
        if reason in imposing:
            assert risk_warning_of(name) is not RiskWarning.none, (name, reason)
            checked += 1
    # A structural count, so a fixture edit that stops exercising the witness fails here
    # rather than passing vacuously.
    assert checked == 9


def test_the_pre_2020_chinext_warning_is_a_bare_star_and_reads_as_the_same_state() -> None:
    """创业板 had no ST regime before 2020: 退市风险警示 was a bare `*` prefix with nothing
    behind it. Testing the literal `*ST` answered `RiskWarning.none` for `*欣泰` and `*金亚`,
    the only two names in all 14,166 corpus rows that start with `*` and are not `*ST`. It is
    the same warning the main board spells `*ST`, so it is the same member rather than a sixth
    one -- an ST filter that merged them would have been right, and one keyed on the string
    would not."""
    assert risk_warning_of("*欣泰") is RiskWarning.star_st
    assert risk_warning_of("*金亚") is RiskWarning.star_st
    # The revocation lands on an ordinary name, which is exactly why the state is read from
    # the name and not from the reason.
    assert risk_warning_of("欣泰电气") is RiskWarning.none
    assert risk_warning_of("金亚科技") is RiskWarning.none
    # Widening the test to the marker does not widen it to anything else.
    assert risk_warning_of("*") is RiskWarning.star_st
    assert risk_warning_of("平安银行") is RiskWarning.none
    assert risk_warning_of("S深发展A") is RiskWarning.none


def test_the_witness_scan_reaches_the_chinext_rows_it_used_to_step_over() -> None:
    """The same cross-check `test_the_change_reason_column_is_...` runs, over the two rows
    whose reason the `imposing` set omitted. Both are `高风险警示`, and with that reason absent
    the scan could not reach either of the names the grammar answered `none` for -- an
    assertion that was true because it never met a counterexample."""
    rows = (*CHINEXT_RISK_WARNING, *GOLDEN_ARMOUR)
    checked = 0
    for _code, name, _start, _end, _ann, reason in rows:
        if reason in IMPOSING_REASONS:
            assert risk_warning_of(name) is not RiskWarning.none, (name, reason)
            checked += 1
    assert checked == 2
    assert "高风险警示" in IMPOSING_REASONS
    # And the revoking half is not in the set, because it lands on ordinary names.
    revoked = [row for row in rows if row[5] == "撤销高风险警示"]
    assert len(revoked) == 2
    for row in revoked:
        assert row[5] not in IMPOSING_REASONS
        assert risk_warning_of(row[1]) is RiskWarning.none


@pytest.mark.parametrize(
    ("rows", "warned", "cleared"),
    [
        (CHINEXT_RISK_WARNING, (date(2016, 7, 12), date(2016, 8, 22)), date(2016, 8, 23)),
        (GOLDEN_ARMOUR, (date(2018, 6, 27), date(2018, 8, 7)), date(2018, 8, 8)),
    ],
)
def test_the_bare_star_window_is_answered_by_date_not_only_by_name(
    rows: tuple, warned: tuple[date, date], cleared: date
) -> None:
    """The module's headline contract -- subject plus day -> is it under special treatment --
    over the two real windows it used to get wrong. Both securities were terminated afterwards
    (`欣泰退` 2017-08-28, `金亚退` 2020-08-03), so these are the windows an ST filter most
    needed."""
    history = _history(rows)
    for day in warned:
        assert history.risk_warning_on(day) is RiskWarning.star_st, day
    assert history.risk_warning_on(cleared) is RiskWarning.none
    assert history.risk_warning_on(warned[0] - timedelta(days=1)) is RiskWarning.none
    # ... and the delisting-period name that follows is still classified, not swept to `none`.
    assert history.risk_warning_on(date(2026, 1, 1)) is RiskWarning.delisting_process


def test_a_risk_warning_is_not_a_truth_value() -> None:
    for warning in RiskWarning:
        with pytest.raises(NameHistoryError, match="has no truth value"):
            bool(warning)


def test_the_whole_special_treatment_ladder_of_000005_is_walked_by_date() -> None:
    history = _history(CENTURY_STAR)
    expected = (
        (date(1990, 12, 10), "深原野A", RiskWarning.none),
        (date(1996, 6, 15), "世纪星源", RiskWarning.none),
        (date(2003, 4, 16), "ST星源", RiskWarning.st),
        (date(2003, 5, 12), "*ST星源", RiskWarning.star_st),
        (date(2004, 5, 18), "ST星源", RiskWarning.st),
        (date(2006, 7, 31), "GST星源", RiskWarning.st),
        (date(2006, 10, 9), "ST星源", RiskWarning.st),
        (date(2008, 6, 25), "世纪星源", RiskWarning.none),
        (date(2021, 5, 6), "ST星源", RiskWarning.st),
    )
    for day, name, warning in expected:
        assert history.name_on(day) == name, day
        assert history.risk_warning_on(day) is warning, day


# --- fail-closed construction -----------------------------------------------------------


def test_a_day_before_the_first_record_is_refused_rather_than_guessed() -> None:
    history = _history()
    with pytest.raises(NameHistoryHorizonError, match=r"before 000001\.SZ's first known name"):
        history.name_on(date(1991, 4, 2))


def test_byte_identical_duplicate_records_collapse_instead_of_being_refused() -> None:
    """A live probe found 380 exactly duplicated rows in one `namechange` pull. A duplicate
    carries no new fact, so it collapses; two *different* names on one effective date do not."""
    doubled = (*_records(PING_AN_BANK), *_records(PING_AN_BANK))
    history = build_name_history("000001.SZ", doubled)
    assert len(history.records) == len(PING_AN_BANK)


def test_two_different_names_on_one_effective_date_are_refused() -> None:
    conflicting = (
        *_records(PING_AN_BANK),
        NameRecord(
            ts_code="000001.SZ",
            name="深发展B",
            effective_from=date(2012, 8, 2),
            announced_on=date(2012, 1, 20),
            change_reason="其他",
        ),
    )
    with pytest.raises(NameHistoryError, match="two names effective on 2012-08-02"):
        build_name_history("000001.SZ", conflicting)


def test_a_record_for_another_security_is_refused() -> None:
    mixed = (*_records(PING_AN_BANK), *_records(GOLDEN_FIELD))
    with pytest.raises(NameHistoryError, match=r"carries 000003\.SZ"):
        build_name_history("000001.SZ", mixed)


def test_an_empty_history_cannot_be_built() -> None:
    with pytest.raises(NameHistoryError, match="at least one name record"):
        build_name_history("000001.SZ", ())


def test_a_rename_announced_after_it_took_effect_is_accepted_rather_than_assumed_away() -> None:
    """A live probe found zero such rows in all 14,166, so this ordering is measured and not
    relied upon: nothing here requires `announced_on <= effective_from`, because a late
    announcement is a real possibility whose point-in-time handling already works -- the
    reader simply does not see the rename until it is announced."""
    late = (
        NameRecord(
            ts_code="000001.SZ",
            name="旧名",
            effective_from=date(2019, 1, 2),
            announced_on=date(2019, 1, 2),
            change_reason="其他",
        ),
        NameRecord(
            ts_code="000001.SZ",
            name="新名",
            effective_from=date(2019, 6, 3),
            announced_on=date(2019, 7, 1),
            change_reason="其他",
        ),
    )
    history = build_name_history("000001.SZ", late)
    assert history.name_on(date(2019, 6, 3)) == "新名"
    assert history.announced_by(date(2019, 6, 30)).name_on(date(2019, 6, 3)) == "旧名"


def test_a_datetime_masquerading_as_an_effective_date_is_refused() -> None:
    from datetime import datetime

    broken = (
        NameRecord(
            ts_code="000001.SZ",
            name="平安银行",
            effective_from=datetime(2012, 8, 2),  # type: ignore[arg-type]
            announced_on=date(2012, 1, 20),
            change_reason="其他",
        ),
    )
    with pytest.raises(NameHistoryError, match=r"must be a plain datetime\.date"):
        build_name_history("000001.SZ", broken)


# --- reading the stored panel back --------------------------------------------------------


def _panel_rows(*groups):
    return tuple(
        (ts_code, name, _iso(start).isoformat(), _iso(ann).isoformat(), reason)
        for group in groups
        for ts_code, name, start, _end, ann, reason in group
    )


def test_stored_rows_rebuild_one_history_per_security() -> None:
    histories = name_histories_from_panel_rows(_panel_rows(PING_AN_BANK, GOLDEN_FIELD))
    assert sorted(histories) == ["000001.SZ", "000003.SZ"]
    assert histories["000001.SZ"].name_on(date(2012, 9, 1)) == "平安银行"
    assert histories["000003.SZ"].risk_warning_on(date(2001, 6, 1)) is RiskWarning.pt


def test_a_stored_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(NameHistoryError, match="row 0 has 3 values, expected 5"):
        name_histories_from_panel_rows((("000001.SZ", "平安银行", "2012-08-02"),))


def test_a_stored_announcement_date_that_is_not_an_iso_date_is_refused() -> None:
    with pytest.raises(NameHistoryError, match="announcement_date is not an ISO date"):
        name_histories_from_panel_rows(
            (("000001.SZ", "平安银行", "2012-08-02", "2012-01-32", "其他"),)
        )


def test_a_stored_name_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(NameHistoryError, match="name must be a non-empty string"):
        name_histories_from_panel_rows((("000001.SZ", None, "2012-08-02", "2012-01-20", "其他"),))


# --- guards on the values themselves -----------------------------------------------------


def test_an_empty_security_name_has_no_risk_warning_to_read() -> None:
    with pytest.raises(NameHistoryError, match="must be a non-empty string"):
        risk_warning_of("")
    with pytest.raises(NameHistoryError, match="must be a non-empty string"):
        risk_warning_of(None)  # type: ignore[arg-type]


def test_a_blank_ts_code_is_refused() -> None:
    with pytest.raises(NameHistoryError, match="ts_code must be a non-empty string"):
        build_name_history("  ", _records(PING_AN_BANK))


def test_a_stored_effective_date_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(NameHistoryError, match="effective_date must be an ISO date string"):
        name_histories_from_panel_rows((("000001.SZ", "平安银行", 20120802, "2012-01-20", "其他"),))
