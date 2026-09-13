"""`domain/industry_classification.py` (`V2-P1-010`): the tree, the assignment, the backfill.

Every fixture in this file is a verbatim row from a live probe on 2026-08-09; the suite never
touches the network.

The four things this file is really about:

- **A taxonomy has a birthday, and an answer older than it says so.** `IndustryAnswer` is never
  a bare industry code -- it carries the taxonomy and whether the day asked about predates the
  taxonomy's own effective date, because `index_member_all` expresses 1984 memberships in a
  classification published in 2021.
- **An interval is closed at both ends.** `industry_through` is the last day the assignment
  held, not the first day it did not.
- **A gap is refused, not filled.** 49 of the corpus's 2,004 transitions leave the security
  unclassified for a while, the longest being 000639.SZ's 4,103 sessions and the shortest
  002674.SZ's 45. Which 49 they are needs a calendar: 442 transitions are not calendar-adjacent
  and 393 of those merely cross a weekend or a holiday.
- **The tree and the memberships are joined by a report, never by a precondition.** 25
  membership rows name an L3 node the SW2021 tree does not carry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_PANEL_COLUMNS,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_TREE_PANEL_COLUMNS,
    KNOWN_INDUSTRY_LIMITATIONS,
    SW2014_TAXONOMY,
    SW2021_L1_COUNT,
    SW2021_TAXONOMY,
    IndustryAssignment,
    IndustryClassificationError,
    IndustryHorizonError,
    IndustryNode,
    IndustryReclassification,
    IndustryTree,
    SecurityIndustryHistory,
    build_industry_tree,
    build_security_industry_history,
    industry_coverage_report,
    industry_histories_from_panel_rows,
    industry_trees_from_panel_rows,
)
from openalpha_cn.domain.stock_universe import (
    SecurityLifecycle,
    UniverseHorizonError,
    build_stock_universe,
)

# --------------------------------------------------------------------------------------
# Real rows, captured 2026-08-09
# --------------------------------------------------------------------------------------

# index_classify(src=SW2021): the 农林牧渔 spine, verbatim.
FARMING_L1 = IndustryNode(
    index_code="801010.SI",
    industry_code="110000",
    industry_name="农林牧渔",
    level="L1",
    parent_code="0",
    taxonomy=SW2021_TAXONOMY,
    is_published=True,
)
FARMING_L2 = IndustryNode(
    index_code="801016.SI",
    industry_code="110100",
    industry_name="种植业",
    level="L2",
    parent_code="110000",
    taxonomy=SW2021_TAXONOMY,
    is_published=True,
)
FARMING_L3 = IndustryNode(
    index_code="850111.SI",
    industry_code="110101",
    industry_name="种子",
    level="L3",
    parent_code="110100",
    taxonomy=SW2021_TAXONOMY,
    is_published=True,
)
SPINE = (FARMING_L1, FARMING_L2, FARMING_L3)

# index_member_all(l1_code=801780.SI): 000001.SZ's single, still-open assignment.
PING_AN = IndustryAssignment(
    ts_code="000001.SZ",
    l1_code="801780.SI",
    l2_code="801783.SI",
    l3_code="857831.SI",
    effective_from=date(1991, 4, 3),
    effective_through=None,
)

# index_member_all: 000639.SZ ST西王's four assignments, the corpus's longest coverage hole.
XIWANG = (
    IndustryAssignment(
        ts_code="000639.SZ",
        l1_code="801210.SI",
        l2_code="801212.SI",
        l3_code="857121.SI",
        effective_from=date(1984, 5, 9),
        effective_through=date(2002, 8, 29),
    ),
    IndustryAssignment(
        ts_code="000639.SZ",
        l1_code="801120.SI",
        l2_code="801123.SI",
        l3_code="851231.SI",
        effective_from=date(2019, 7, 24),
        effective_through=date(2021, 7, 29),
    ),
    IndustryAssignment(
        ts_code="000639.SZ",
        l1_code="801010.SI",
        l2_code="801016.SI",
        l3_code="850111.SI",
        effective_from=date(2021, 7, 30),
        effective_through=date(2024, 7, 29),
    ),
    IndustryAssignment(
        ts_code="000639.SZ",
        l1_code="801120.SI",
        l2_code="801123.SI",
        l3_code="851231.SI",
        effective_from=date(2024, 7, 30),
        effective_through=None,
    ),
)

# index_member_all: 600354.SH 敦煌种业 moves 其他种植业 -> 种子 across a **weekend**. 2007-06-29
# is a Friday and 2007-07-02 the next Monday, so the calendar delta is 3 and the session delta
# is 1. 237 of the corpus's 2,004 transitions have exactly this shape and none of them leaves
# the security unclassified for a single session.
DUNHUANG = (
    IndustryAssignment(
        ts_code="600354.SH",
        l1_code="801010.SI",
        l2_code="801016.SI",
        l3_code="850113.SI",
        effective_from=date(2004, 1, 14),
        effective_through=date(2007, 6, 29),
    ),
    IndustryAssignment(
        ts_code="600354.SH",
        l1_code="801010.SI",
        l2_code="801016.SI",
        l3_code="850111.SI",
        effective_from=date(2007, 7, 2),
        effective_through=None,
    ),
)

# index_member_all: 300268.SZ 佳沃食品 moves across the 2011 National Day week -- calendar delta
# 10, session delta 1. 48 transitions have a ten-day calendar delta and none is a gap.
JIAWO = (
    IndustryAssignment(
        ts_code="300268.SZ",
        l1_code="801010.SI",
        l2_code="801016.SI",
        l3_code="850112.SI",
        effective_from=date(2011, 9, 15),
        effective_through=date(2011, 9, 30),
    ),
    IndustryAssignment(
        ts_code="300268.SZ",
        l1_code="801010.SI",
        l2_code="801012.SI",
        l3_code="850154.SI",
        effective_from=date(2011, 10, 10),
        effective_through=None,
    ),
)

# index_member_all: 002674.SZ 兴业科技 is the *shortest* of the 49 real coverage holes -- 45
# sessions with no assignment at all, returning afterwards to the same three codes.
XINGYE = (
    IndustryAssignment(
        ts_code="002674.SZ",
        l1_code="801130.SI",
        l2_code="801131.SI",
        l3_code="851315.SI",
        effective_from=date(2012, 1, 4),
        effective_through=date(2012, 4, 24),
    ),
    IndustryAssignment(
        ts_code="002674.SZ",
        l1_code="801130.SI",
        l2_code="801131.SI",
        l3_code="851315.SI",
        effective_from=date(2012, 7, 2),
        effective_through=None,
    ),
)

# trade_cal(SSE): every session in three real windows, complete over each range, so a count of
# the sessions between any two dates inside one window is exact.
# One session per line is 72 lines of noise for three contiguous windows, so the formatter
# is held off across them.
# fmt: off
SSE_SESSIONS = tuple(
    date.fromisoformat(day)
    for day in (
        # 2007-06-25 .. 2007-07-06, around 敦煌种业's Friday-to-Monday hand-over.
        "2007-06-25", "2007-06-26", "2007-06-27", "2007-06-28", "2007-06-29", "2007-07-02",
        "2007-07-03", "2007-07-04", "2007-07-05", "2007-07-06",
        # 2011-09-26 .. 2011-10-14, around 佳沃食品's National Day hand-over.
        "2011-09-26", "2011-09-27", "2011-09-28", "2011-09-29", "2011-09-30", "2011-10-10",
        "2011-10-11", "2011-10-12", "2011-10-13", "2011-10-14",
        # 2012-04-20 .. 2012-07-05, spanning 兴业科技's whole 45-session hole.
        "2012-04-20", "2012-04-23", "2012-04-24", "2012-04-25", "2012-04-26", "2012-04-27",
        "2012-05-02", "2012-05-03", "2012-05-04", "2012-05-07", "2012-05-08", "2012-05-09",
        "2012-05-10", "2012-05-11", "2012-05-14", "2012-05-15", "2012-05-16", "2012-05-17",
        "2012-05-18", "2012-05-21", "2012-05-22", "2012-05-23", "2012-05-24", "2012-05-25",
        "2012-05-28", "2012-05-29", "2012-05-30", "2012-05-31", "2012-06-01", "2012-06-04",
        "2012-06-05", "2012-06-06", "2012-06-07", "2012-06-08", "2012-06-11", "2012-06-12",
        "2012-06-13", "2012-06-14", "2012-06-15", "2012-06-18", "2012-06-19", "2012-06-20",
        "2012-06-21", "2012-06-25", "2012-06-26", "2012-06-27", "2012-06-28", "2012-06-29",
        "2012-07-02", "2012-07-03", "2012-07-04", "2012-07-05",
    )
)
# fmt: on


# --------------------------------------------------------------------------------------
# The taxonomy has a birthday
# --------------------------------------------------------------------------------------


def test_both_measured_taxonomy_vintages_carry_their_own_effective_date() -> None:
    """SW2014 and SW2021 are separate classifications with separate birthdays.

    Derived from the endpoint rather than looked up: `index_member` reports 912 constituent
    rows entering on 2014-02-21 and 535 leaving on 2021-12-10 against 393 entering
    2021-12-13, and the SW2014-only L1 801020.SI 采掘 loses 66 of its 97 members on exactly
    2021-12-13.
    """
    assert INDUSTRY_TAXONOMY_EFFECTIVE_FROM[SW2014_TAXONOMY] == date(2014, 2, 21)
    assert INDUSTRY_TAXONOMY_EFFECTIVE_FROM[SW2021_TAXONOMY] == date(2021, 12, 13)


def test_an_answer_about_a_day_before_the_taxonomy_existed_reports_the_backfill() -> None:
    """The whole point of `V2-P1-010`: a 2015 answer in a 2021 classification says so.

    `index_member_all` labels 000001.SZ's 1991 assignment with SW2021 nodes, and there is no
    column in the response that says the label did not exist then.
    """
    history = build_security_industry_history("000001.SZ", (PING_AN,), taxonomy=SW2021_TAXONOMY)

    answer = history.industry_on(date(2015, 6, 30))

    assert answer.l1_code == "801780.SI"
    assert answer.taxonomy == SW2021_TAXONOMY
    assert answer.taxonomy_effective_from == date(2021, 12, 13)
    assert answer.is_backfilled is True
    assert answer.backfilled_by_days == (date(2021, 12, 13) - date(2015, 6, 30)).days == 2358


def test_an_answer_from_the_taxonomys_own_era_is_not_backfilled() -> None:
    """The flag is a property of the day asked about, not of the dataset as a whole."""
    history = build_security_industry_history("000001.SZ", (PING_AN,), taxonomy=SW2021_TAXONOMY)

    answer = history.industry_on(date(2024, 6, 28))

    assert answer.is_backfilled is False
    assert answer.backfilled_by_days == 0


def test_the_taxonomy_effective_date_itself_is_not_backfilled() -> None:
    """The boundary is inclusive: 2021-12-13 is the first day SW2021 existed."""
    history = build_security_industry_history("000001.SZ", (PING_AN,), taxonomy=SW2021_TAXONOMY)

    assert history.industry_on(date(2021, 12, 13)).is_backfilled is False
    assert history.industry_on(date(2021, 12, 12)).is_backfilled is True


def test_a_history_cannot_be_built_under_a_taxonomy_nobody_has_dated() -> None:
    """An undated vintage would make `is_backfilled` unanswerable, so it is refused here."""
    with pytest.raises(IndustryClassificationError, match="taxonomy 'SW2099' has no effective"):
        build_security_industry_history("000001.SZ", (PING_AN,), taxonomy="SW2099")


# --------------------------------------------------------------------------------------
# The interval is closed at both ends
# --------------------------------------------------------------------------------------


def test_industry_through_is_the_last_day_the_assignment_held() -> None:
    """Measured: 1,955 of the corpus's 2,004 transitions have the successor's
    `industry_from` on the very next SSE session after `industry_through`, so the two
    endpoints cannot both be exclusive without leaving a one-session hole everywhere."""
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    assert history.industry_on(date(2021, 7, 29)).l1_code == "801120.SI"
    assert history.industry_on(date(2021, 7, 30)).l1_code == "801010.SI"


def test_a_still_open_assignment_answers_every_later_day() -> None:
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    assert history.covered_through is None
    assert history.industry_on(date(2026, 8, 7)).l1_code == "801120.SI"


def test_a_day_inside_a_coverage_hole_is_refused_rather_than_filled_forward() -> None:
    """000639.SZ is unclassified for 4,103 sessions between 2002-08-30 and 2019-07-23.

    Carrying 社会服务 forward across seventeen years would be an answer no row supports, and
    it would be indistinguishable from a security that never left the classification.
    """
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    with pytest.raises(IndustryHorizonError, match="falls in a gap between 2002-08-29 and"):
        history.industry_on(date(2010, 6, 30))


def test_a_day_before_the_first_assignment_is_refused() -> None:
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    with pytest.raises(IndustryHorizonError, match=r"before 000639\.SZ's first assignment"):
        history.industry_on(date(1984, 5, 8))


def test_overlapping_assignments_are_refused() -> None:
    """Zero of the corpus's 5,889 securities carry two assignments covering one day, so an
    overlap is two sources rather than a security in two industries at once."""
    overlapping = (
        IndustryAssignment(
            ts_code="000001.SZ",
            l1_code="801780.SI",
            l2_code="801783.SI",
            l3_code="857831.SI",
            effective_from=date(1991, 4, 3),
            effective_through=date(2020, 1, 1),
        ),
        IndustryAssignment(
            ts_code="000001.SZ",
            l1_code="801790.SI",
            l2_code="801791.SI",
            l3_code="857911.SI",
            effective_from=date(2019, 1, 1),
            effective_through=None,
        ),
    )

    with pytest.raises(IndustryClassificationError, match="overlaps the one starting"):
        build_security_industry_history("000001.SZ", overlapping, taxonomy=SW2021_TAXONOMY)


def test_a_second_open_assignment_is_refused() -> None:
    """Measured: every one of the 5,889 securities has exactly one open assignment."""
    two_open = (
        PING_AN,
        IndustryAssignment(
            ts_code="000001.SZ",
            l1_code="801790.SI",
            l2_code="801791.SI",
            l3_code="857911.SI",
            effective_from=date(2020, 1, 1),
            effective_through=None,
        ),
    )

    with pytest.raises(IndustryClassificationError, match="two assignments with no end date"):
        build_security_industry_history("000001.SZ", two_open, taxonomy=SW2021_TAXONOMY)


def test_an_assignment_that_ends_before_it_starts_is_refused() -> None:
    backwards = IndustryAssignment(
        ts_code="000001.SZ",
        l1_code="801780.SI",
        l2_code="801783.SI",
        l3_code="857831.SI",
        effective_from=date(2020, 1, 2),
        effective_through=date(2020, 1, 1),
    )

    with pytest.raises(IndustryClassificationError, match="ends 2020-01-01, before it starts"):
        build_security_industry_history("000001.SZ", (backwards,), taxonomy=SW2021_TAXONOMY)


def test_a_history_mixing_two_securities_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match=r"carries 000639\.SZ"):
        build_security_industry_history("000001.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)


def test_an_empty_history_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="needs at least one assignment"):
        build_security_industry_history("000001.SZ", (), taxonomy=SW2021_TAXONOMY)


# --------------------------------------------------------------------------------------
# Reclassifications
# --------------------------------------------------------------------------------------


def test_reclassifications_report_every_transition_including_the_ones_across_a_gap() -> None:
    """1,645 of 5,889 securities carry at least one; 000639.SZ carries three."""
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    changes = history.reclassifications()

    assert [change.effective_from for change in changes] == [
        date(2019, 7, 24),
        date(2021, 7, 30),
        date(2024, 7, 30),
    ]
    assert changes[0].is_across_a_gap is True
    assert changes[1].is_across_a_gap is False
    assert changes[1].changed_levels == ("L1", "L2", "L3")


def test_a_reclassification_that_moves_only_the_leaf_names_only_the_leaf() -> None:
    """A demotion inside one L1 is a different fact from a move between two, and a caller
    neutralising on L1 must be able to tell them apart."""
    same_l1 = (
        IndustryAssignment(
            ts_code="600519.SH",
            l1_code="801120.SI",
            l2_code="801124.SI",
            l3_code="851241.SI",
            effective_from=date(2001, 8, 27),
            effective_through=date(2021, 7, 29),
        ),
        IndustryAssignment(
            ts_code="600519.SH",
            l1_code="801120.SI",
            l2_code="801124.SI",
            l3_code="851242.SI",
            effective_from=date(2021, 7, 30),
            effective_through=None,
        ),
    )
    history = build_security_industry_history("600519.SH", same_l1, taxonomy=SW2021_TAXONOMY)

    (change,) = history.reclassifications()

    assert change.changed_levels == ("L3",)


def test_a_weekend_hand_over_is_not_calendar_adjacent_and_is_not_a_gap() -> None:
    """The distinction the calendar-free rule cannot make, on the shape that dominates it.

    600354.SH's assignment ends on Friday 2007-06-29 and its successor starts on Monday
    2007-07-02: three calendar days apart, one session apart, and not a single day on which the
    security carried no industry. On the real corpus 442 of the 2,004 transitions are not
    calendar-adjacent and only 49 are gaps -- the other 393 are this, with calendar deltas of
    exactly the market's shape (237 of three days, 48 of ten, 47 of four, 32 of five, 14 of two).
    """
    history = build_security_industry_history("600354.SH", DUNHUANG, taxonomy=SW2021_TAXONOMY)

    (blind,) = history.reclassifications()
    (exact,) = history.reclassifications(sessions=SSE_SESSIONS)

    assert (exact.current.effective_from - exact.previous.effective_through).days == 3
    assert blind.is_not_calendar_adjacent is True
    assert exact.unclassified_sessions == 0
    assert exact.is_across_a_gap is False
    # And without the calendar the same transition is a false positive, which is the whole
    # reason `is_not_calendar_adjacent` is a separate, differently-named property.
    assert blind.unclassified_sessions is None
    assert blind.is_across_a_gap is True


def test_a_holiday_week_hand_over_is_not_a_gap_either() -> None:
    """Ten calendar days, one session: the 2011 National Day week. 48 transitions look like this
    and a rule tuned to skip weekends alone would still call every one of them a gap."""
    history = build_security_industry_history("300268.SZ", JIAWO, taxonomy=SW2021_TAXONOMY)

    (change,) = history.reclassifications(sessions=SSE_SESSIONS)

    assert (change.current.effective_from - change.previous.effective_through).days == 10
    assert change.is_not_calendar_adjacent is True
    assert change.unclassified_sessions == 0
    assert change.is_across_a_gap is False


def test_a_real_hole_is_counted_in_sessions_rather_than_in_days() -> None:
    """002674.SZ is the shortest of the 49 real holes: 2012-04-24 to 2012-07-02 is 69 calendar
    days and **45 sessions** on which no assignment covered it. The count is sessions strictly
    between the two dates, because the security is still classified on its `out_date` -- which
    is also why 000639.SZ's hole is 4,103 sessions and not 4,104."""
    history = build_security_industry_history("002674.SZ", XINGYE, taxonomy=SW2021_TAXONOMY)

    (change,) = history.reclassifications(sessions=SSE_SESSIONS)

    assert (change.current.effective_from - change.previous.effective_through).days == 69
    assert change.unclassified_sessions == 45
    assert change.is_across_a_gap is True
    assert change.changed_levels == ()


# --------------------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------------------


def test_the_sw2021_tree_has_thirty_one_level_one_industries() -> None:
    """Measured against the live endpoint: 31 L1, 134 L2, 346 L3 for src=SW2021, against
    28/104/227 for SW2014 -- which is also what the bare request answers."""
    assert SW2021_L1_COUNT == 31


def test_the_parent_chain_walks_a_leaf_up_to_its_level_one_industry() -> None:
    tree = build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=SPINE)

    assert [node.level for node in tree.ancestry("850111.SI")] == ["L3", "L2", "L1"]
    assert tree.ancestry("850111.SI")[-1].industry_name == "农林牧渔"


def test_a_node_whose_parent_is_absent_is_refused_at_build_time() -> None:
    """A broken chain is what a partial read of the tree partition looks like."""
    with pytest.raises(IndustryClassificationError, match=r"850111\.SI names parent 110100"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=(FARMING_L1, FARMING_L3))


def test_a_level_one_node_with_a_real_parent_is_refused() -> None:
    rooted_wrong = IndustryNode(
        index_code="801010.SI",
        industry_code="110000",
        industry_name="农林牧渔",
        level="L1",
        parent_code="110100",
        taxonomy=SW2021_TAXONOMY,
        is_published=True,
    )

    with pytest.raises(IndustryClassificationError, match=r"L1 node 801010\.SI names parent"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=(rooted_wrong,))


def test_a_tree_mixing_two_vintages_is_refused() -> None:
    """The endpoint's own default is SW2014 while every `index_member_all` row is SW2021, so
    a mixed tree is exactly the mistake a bare request makes."""
    stray = IndustryNode(
        index_code="850412.SI",
        industry_code="230102",
        industry_name="特钢",
        level="L3",
        parent_code="230100",
        taxonomy=SW2014_TAXONOMY,
        is_published=None,
    )

    with pytest.raises(IndustryClassificationError, match="carries a SW2014 node"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=(*SPINE, stray))


def test_an_unknown_level_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="level must be one of"):
        build_industry_tree(
            taxonomy=SW2021_TAXONOMY,
            nodes=(
                IndustryNode(
                    index_code="801010.SI",
                    industry_code="110000",
                    industry_name="农林牧渔",
                    level="L4",
                    parent_code="0",
                    taxonomy=SW2021_TAXONOMY,
                    is_published=True,
                ),
            ),
        )


def test_asking_the_tree_about_a_node_it_does_not_carry_is_refused() -> None:
    """25 membership rows name 850412.SI 特钢, which is an SW2014 L3 node and is absent from
    the SW2021 tree's 346. `ancestry` says so rather than answering about a neighbour."""
    tree = build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=SPINE)

    with pytest.raises(IndustryClassificationError, match=r"850412\.SI is not in the SW2021 tree"):
        tree.ancestry("850412.SI")


def test_each_level_reports_its_own_nodes_and_the_counts_differ() -> None:
    """`nodes_at` and `level_one_count` name a level and must count that level.

    A tree with one node per level cannot tell "count the L1s" from "count the L2s" or from
    "count everything and divide by three", so this one is a real, lopsided slice of SW2021: two
    L1 industries, three L2s under one of them and one L2 under the other, two L3s. The vintage's
    own shape is 31 / 134 / 346, so no two of its levels have the same size either.
    """
    lopsided = (
        FARMING_L1,
        FARMING_L2,
        FARMING_L3,
        IndustryNode(
            index_code="801012.SI",
            industry_code="110500",
            industry_name="农产品加工",
            level="L2",
            parent_code="110000",
            taxonomy=SW2021_TAXONOMY,
            is_published=True,
        ),
        IndustryNode(
            index_code="801017.SI",
            industry_code="110700",
            industry_name="养殖业",
            level="L2",
            parent_code="110000",
            taxonomy=SW2021_TAXONOMY,
            is_published=True,
        ),
        IndustryNode(
            index_code="801780.SI",
            industry_code="480000",
            industry_name="银行",
            level="L1",
            parent_code="0",
            taxonomy=SW2021_TAXONOMY,
            is_published=True,
        ),
        IndustryNode(
            index_code="801783.SI",
            industry_code="480300",
            industry_name="股份制银行Ⅱ",
            level="L2",
            parent_code="480000",
            taxonomy=SW2021_TAXONOMY,
            is_published=True,
        ),
        IndustryNode(
            index_code="857831.SI",
            industry_code="480301",
            industry_name="股份制银行Ⅲ",
            level="L3",
            parent_code="480300",
            taxonomy=SW2021_TAXONOMY,
            is_published=True,
        ),
    )
    tree = build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=lopsided)

    assert [node.index_code for node in tree.nodes_at("L1")] == ["801010.SI", "801780.SI"]
    assert [node.index_code for node in tree.nodes_at("L2")] == [
        "801012.SI",
        "801016.SI",
        "801017.SI",
        "801783.SI",
    ]
    assert [node.index_code for node in tree.nodes_at("L3")] == ["850111.SI", "857831.SI"]
    assert tree.nodes_at("L4") == ()
    assert tree.level_one_count == 2
    assert len(tree.nodes) == 8


# --------------------------------------------------------------------------------------
# Stored rows
# --------------------------------------------------------------------------------------


def test_membership_panel_rows_rebuild_one_history_per_security() -> None:
    rows = [
        ("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI"),
        ("000639.SZ", "1984-05-09", "2002-08-29", "801210.SI", "801212.SI", "857121.SI"),
        ("000639.SZ", "2019-07-24", None, "801120.SI", "801123.SI", "851231.SI"),
    ]

    histories = industry_histories_from_panel_rows(rows, taxonomy=SW2021_TAXONOMY)

    assert sorted(histories) == ["000001.SZ", "000639.SZ"]
    assert histories["000639.SZ"].industry_on(date(2020, 1, 2)).l3_code == "851231.SI"


def test_the_opening_and_closing_rows_of_one_assignment_fold_back_together() -> None:
    """`providers/tushare.py` stores a closed assignment as two rows -- one dated at its start
    with no end, one dated at its end with both -- so that a 2024 termination is not readable in
    2003. A read covering both years gets both, and they are one assignment: the closed half
    wins, because a close is later knowledge than the open it supersedes."""
    rows = [
        ("600423.SH", "2003-07-14", None, "801030.SI", "801038.SI", "850331.SI"),
        ("600423.SH", "2003-07-14", "2024-07-29", "801030.SI", "801038.SI", "850331.SI"),
        ("600423.SH", "2024-07-30", None, "801030.SI", "801033.SI", "850324.SI"),
    ]

    histories = industry_histories_from_panel_rows(rows, taxonomy=SW2021_TAXONOMY)

    assignments = histories["600423.SH"].assignments
    assert len(assignments) == 2
    assert assignments[0].effective_through == date(2024, 7, 29)
    assert histories["600423.SH"].industry_on(date(2024, 7, 29)).l2_code == "801038.SI"
    assert histories["600423.SH"].industry_on(date(2024, 7, 30)).l2_code == "801033.SI"


def test_the_closing_row_wins_whichever_order_the_years_are_read_in() -> None:
    """`load_industry_histories` reads years ascending, so the opening row normally arrives
    first. Nothing downstream may depend on that: a caller naming its years out of order, or a
    store that reordered a partition, must reassemble the same interval."""
    reversed_order = [
        ("600423.SH", "2003-07-14", "2024-07-29", "801030.SI", "801038.SI", "850331.SI"),
        ("600423.SH", "2003-07-14", None, "801030.SI", "801038.SI", "850331.SI"),
    ]

    histories = industry_histories_from_panel_rows(reversed_order, taxonomy=SW2021_TAXONOMY)

    (assignment,) = histories["600423.SH"].assignments
    assert assignment.effective_through == date(2024, 7, 29)


def test_two_rows_on_one_start_day_that_disagree_about_the_industry_are_refused() -> None:
    """The split's halves agree on everything but the end date. Two rows that do not are two
    sources that were never reconciled, which is `build_security_industry_history`'s rule kept
    intact one layer up rather than dissolved by the fold."""
    conflicting = [
        ("600423.SH", "2003-07-14", None, "801030.SI", "801038.SI", "850331.SI"),
        ("600423.SH", "2003-07-14", "2024-07-29", "801030.SI", "801033.SI", "850324.SI"),
    ]

    with pytest.raises(IndustryClassificationError, match="with different industries"):
        industry_histories_from_panel_rows(conflicting, taxonomy=SW2021_TAXONOMY)


def test_one_assignment_closed_on_two_different_days_is_refused() -> None:
    """One assignment ends once. Two closing rows disagreeing about when would answer two
    different sets of days, and picking either is picking silently."""
    twice_closed = [
        ("600423.SH", "2003-07-14", "2024-07-29", "801030.SI", "801038.SI", "850331.SI"),
        ("600423.SH", "2003-07-14", "2022-07-29", "801030.SI", "801038.SI", "850331.SI"),
    ]

    with pytest.raises(IndustryClassificationError, match="is closed twice"):
        industry_histories_from_panel_rows(twice_closed, taxonomy=SW2021_TAXONOMY)


def test_a_history_bounded_at_a_year_refuses_the_days_after_it() -> None:
    """The one fail-open the row split introduces, made a refusal.

    A read that stopped before the year an assignment closed in reassembles an interval with no
    end. `answerable_through` records where the read stopped being able to see a close, and both
    query methods refuse past it -- `is_classified_on` included, because there the `False` and
    the `True` are both guesses rather than the ordinary "no industry that day".
    """
    rows = [("600423.SH", "2003-07-14", None, "801030.SI", "801038.SI", "850331.SI")]

    histories = industry_histories_from_panel_rows(
        rows, taxonomy=SW2021_TAXONOMY, answerable_through=2023
    )

    history = histories["600423.SH"]
    assert history.answerable_through == 2023
    assert history.industry_on(date(2023, 12, 29)).l2_code == "801038.SI"
    assert history.is_classified_on(date(2023, 12, 29)) is True
    with pytest.raises(IndustryHorizonError, match="is after 2023, the last membership year"):
        history.industry_on(date(2024, 1, 2))
    with pytest.raises(IndustryHorizonError, match="is after 2023, the last membership year"):
        history.is_classified_on(date(2024, 1, 2))


def test_a_membership_row_of_the_wrong_width_names_the_columns_it_wanted() -> None:
    with pytest.raises(IndustryClassificationError, match="row 0 has 3 values, expected 6"):
        industry_histories_from_panel_rows(
            [("000001.SZ", "1991-04-03", None)], taxonomy=SW2021_TAXONOMY
        )


def test_a_membership_row_whose_start_is_not_an_iso_date_is_refused() -> None:
    """`1991-04-33` is not a date. Tushare's own `19910403` compact form *is* accepted, by
    `date.fromisoformat` since 3.11 and identically by every other `_parse_iso_date` in this
    package -- it resolves to the same day, so it is a widened input rather than a wrong one."""
    with pytest.raises(IndustryClassificationError, match="industry_from is not an ISO date"):
        industry_histories_from_panel_rows(
            [("000001.SZ", "1991-04-33", None, "801780.SI", "801783.SI", "857831.SI")],
            taxonomy=SW2021_TAXONOMY,
        )
    compact = industry_histories_from_panel_rows(
        [("000001.SZ", "19910403", None, "801780.SI", "801783.SI", "857831.SI")],
        taxonomy=SW2021_TAXONOMY,
    )
    assert compact["000001.SZ"].covered_from == date(1991, 4, 3)


def test_tree_panel_rows_rebuild_one_tree_per_vintage() -> None:
    rows = [
        ("801010.SI", "110000", "农林牧渔", "L1", "0", True, "SW2021", "2021-12-13"),
        ("801016.SI", "110100", "种植业", "L2", "110000", True, "SW2021", "2021-12-13"),
        ("850111.SI", "110101", "种子", "L3", "110100", True, "SW2021", "2021-12-13"),
        ("801020.SI", "210000", "采掘", "L1", "0", None, "SW2014", "2014-02-21"),
    ]

    trees = industry_trees_from_panel_rows(rows)

    assert sorted(trees) == [SW2014_TAXONOMY, SW2021_TAXONOMY]
    assert trees[SW2021_TAXONOMY].level_one_count == 1
    assert trees[SW2021_TAXONOMY].effective_from == date(2021, 12, 13)
    assert trees[SW2014_TAXONOMY].node("801020.SI").is_published is None


def test_a_tree_row_whose_stored_vintage_date_disagrees_with_the_contract_is_refused() -> None:
    """The stored column is a witness, not a second source of truth: if the two disagree the
    partition was written by a different rule than the one reading it."""
    with pytest.raises(IndustryClassificationError, match="stored taxonomy_date 2020-01-01"):
        industry_trees_from_panel_rows(
            [("801010.SI", "110000", "农林牧渔", "L1", "0", True, "SW2021", "2020-01-01")]
        )


def test_the_two_panel_column_tuples_are_what_the_readers_parse() -> None:
    """One tuple for the projection and the parse, so they cannot drift; see
    `INDEX_WEIGHT_PANEL_COLUMNS` for the precedent."""
    assert INDUSTRY_MEMBERSHIP_PANEL_COLUMNS == (
        "subject",
        "industry_from",
        "industry_through",
        "l1_code",
        "l2_code",
        "l3_code",
    )
    assert INDUSTRY_TREE_PANEL_COLUMNS == (
        "subject",
        "industry_code",
        "industry_name",
        "level",
        "parent_code",
        "is_published",
        "taxonomy",
        "taxonomy_date",
    )


# --------------------------------------------------------------------------------------
# The join with the registry
# --------------------------------------------------------------------------------------


def test_the_coverage_report_names_the_listed_securities_with_no_industry() -> None:
    """Measured on the real corpus: 1 of 5,539 names listed on 2026-08-07 (920038.BJ 森合高科,
    listed 2026-08-05), 82 of 2,776 on 2015-06-30."""
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
            SecurityLifecycle(ts_code="920038.BJ", exchange="BSE", listed_on=date(2026, 8, 5)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI")],
        taxonomy=SW2021_TAXONOMY,
    )

    report = industry_coverage_report(universe, histories, day=date(2026, 8, 7))

    assert report.classified == ("000001.SZ",)
    assert report.unclassified == ("920038.BJ",)
    assert report.classified_ratio == 0.5
    assert report.is_complete is False


def test_the_coverage_report_names_classified_codes_the_registry_does_not_carry() -> None:
    """14 of the corpus's 5,889 codes are absent from stock_basic's 5,878-row registry."""
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [
            ("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI"),
            ("000991.SZ", "2000-01-01", None, "801080.SI", "801081.SI", "850811.SI"),
        ],
        taxonomy=SW2021_TAXONOMY,
    )

    report = industry_coverage_report(universe, histories, day=date(2026, 8, 7))

    assert report.unknown_to_registry == ("000991.SZ",)
    assert report.unclassified == ()


def test_the_coverage_report_counts_a_security_inside_a_hole_as_unclassified() -> None:
    """000639.SZ is listed throughout 2010 and has no assignment covering it, so a
    neutralisation on that day has no industry for it -- the report has to say so rather than
    reporting it as covered because the security appears in the corpus somewhere."""
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000639.SZ", exchange="SZSE", listed_on=date(1993, 11, 29)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [
            ("000639.SZ", "1984-05-09", "2002-08-29", "801210.SI", "801212.SI", "857121.SI"),
            ("000639.SZ", "2019-07-24", None, "801120.SI", "801123.SI", "851231.SI"),
        ],
        taxonomy=SW2021_TAXONOMY,
    )

    assert industry_coverage_report(universe, histories, day=date(2010, 6, 30)).unclassified == (
        "000639.SZ",
    )
    assert industry_coverage_report(universe, histories, day=date(2020, 6, 30)).unclassified == ()


# --------------------------------------------------------------------------------------
# The refusals a partial or drifted partition produces
# --------------------------------------------------------------------------------------


def test_a_duplicated_index_code_in_one_tree_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="appears more than once"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=(*SPINE, FARMING_L2))


def test_two_nodes_claiming_one_industry_code_are_refused() -> None:
    """`parent_code` points at `industry_code`, so a collision makes the chain ambiguous."""
    twin = IndustryNode(
        index_code="801017.SI",
        industry_code="110100",
        industry_name="渔业",
        level="L2",
        parent_code="110000",
        taxonomy=SW2021_TAXONOMY,
        is_published=True,
    )

    with pytest.raises(IndustryClassificationError, match="is claimed by both"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=(*SPINE, twin))


def test_an_empty_tree_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="needs at least one node"):
        build_industry_tree(taxonomy=SW2021_TAXONOMY, nodes=())


def test_a_malformed_node_identifier_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="index_code must be a non-empty"):
        build_industry_tree(
            taxonomy=SW2021_TAXONOMY,
            nodes=(
                IndustryNode(
                    index_code="",
                    industry_code="110000",
                    industry_name="农林牧渔",
                    level="L1",
                    parent_code="0",
                    taxonomy=SW2021_TAXONOMY,
                    is_published=True,
                ),
            ),
        )


def test_a_tree_row_with_a_non_bool_is_published_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="is_published must be a bool or None"):
        industry_trees_from_panel_rows(
            [("801010.SI", "110000", "农林牧渔", "L1", "0", 1, "SW2021", "2021-12-13")]
        )


def test_a_tree_row_of_the_wrong_width_names_the_columns_it_wanted() -> None:
    with pytest.raises(IndustryClassificationError, match="row 0 has 3 values, expected 8"):
        industry_trees_from_panel_rows([("801010.SI", "110000", "农林牧渔")])


def test_a_tree_row_whose_taxonomy_date_is_not_a_date_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="taxonomy_date is not an ISO date"):
        industry_trees_from_panel_rows(
            [("801010.SI", "110000", "农林牧渔", "L1", "0", True, "SW2021", "whenever")]
        )


def test_a_stored_row_whose_subject_is_not_text_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="subject must be a non-empty string"):
        industry_histories_from_panel_rows(
            [(None, "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI")],
            taxonomy=SW2021_TAXONOMY,
        )


def test_a_stored_row_whose_start_is_not_a_string_is_refused() -> None:
    with pytest.raises(IndustryClassificationError, match="industry_from must be an ISO date"):
        industry_histories_from_panel_rows(
            [("000001.SZ", 19910403, None, "801780.SI", "801783.SI", "857831.SI")],
            taxonomy=SW2021_TAXONOMY,
        )


def test_a_datetime_is_refused_wherever_a_plain_date_is_wanted() -> None:
    """`datetime(2020, 1, 7) > date(2020, 1, 7)` is True, so a `datetime` would move an
    interval boundary by a day while comparing as though it had not."""
    history = build_security_industry_history("000001.SZ", (PING_AN,), taxonomy=SW2021_TAXONOMY)

    with pytest.raises(IndustryClassificationError, match=r"must be a plain datetime\.date"):
        history.industry_on(datetime(2020, 1, 7, tzinfo=UTC))  # type: ignore[arg-type]


def test_two_assignments_starting_on_one_day_are_refused() -> None:
    twin = IndustryAssignment(
        ts_code="000001.SZ",
        l1_code="801790.SI",
        l2_code="801791.SI",
        l3_code="857911.SI",
        effective_from=date(1991, 4, 3),
        effective_through=date(2000, 1, 1),
    )

    with pytest.raises(IndustryClassificationError, match="two assignments starting"):
        build_security_industry_history("000001.SZ", (PING_AN, twin), taxonomy=SW2021_TAXONOMY)


def test_a_day_after_a_closed_final_assignment_is_refused() -> None:
    """A delisted security's last assignment closes and nothing follows it. Carrying it forward
    would answer 2026 questions about a security whose classification ended in 2002."""
    closed = IndustryAssignment(
        ts_code="000639.SZ",
        l1_code="801210.SI",
        l2_code="801212.SI",
        l3_code="857121.SI",
        effective_from=date(1984, 5, 9),
        effective_through=date(2002, 8, 29),
    )
    history = build_security_industry_history("000639.SZ", (closed,), taxonomy=SW2021_TAXONOMY)

    assert history.covered_through == date(2002, 8, 29)
    with pytest.raises(IndustryHorizonError, match=r"after 000639\.SZ's last assignment"):
        history.industry_on(date(2010, 6, 30))


def test_is_classified_on_answers_where_assignment_on_refuses() -> None:
    """A cross-section builder needs "no industry that day" as a value, not an exception: the
    residue is 82 of 2,776 names on 2015-06-30 and is entirely ordinary."""
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    assert history.is_classified_on(date(2001, 1, 2)) is True
    assert history.is_classified_on(date(2010, 6, 30)) is False
    assert history.is_classified_on(date(1984, 5, 8)) is False


def test_a_current_assignment_reports_itself_as_current() -> None:
    history = build_security_industry_history("000639.SZ", XIWANG, taxonomy=SW2021_TAXONOMY)

    assert history.assignments[-1].is_current is True
    assert history.assignments[0].is_current is False
    assert history.industry_on(date(2026, 8, 7)).l2_code == "801123.SI"


def test_the_coverage_report_reports_a_day_the_registry_cannot_see() -> None:
    """`StockUniverse` refuses to answer past its own snapshot, and this join must not turn
    that refusal into a quiet 'not listed'."""
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI")],
        taxonomy=SW2021_TAXONOMY,
    )

    with pytest.raises(UniverseHorizonError):
        industry_coverage_report(universe, histories, day=date(2026, 9, 1))


def test_the_coverage_report_of_an_empty_market_has_no_ratio_at_all() -> None:
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI")],
        taxonomy=SW2021_TAXONOMY,
    )

    report = industry_coverage_report(universe, histories, day=date(1990, 1, 2))

    assert report.listed_count == 0
    # Neither 0.0 nor 1.0: a threshold check has to answer the empty case rather than read a
    # plausible number off it.
    assert report.classified_ratio is None
    assert report.is_complete is True


def test_a_complete_day_reports_itself_complete() -> None:
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
        ),
    )
    histories = industry_histories_from_panel_rows(
        [("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI")],
        taxonomy=SW2021_TAXONOMY,
    )

    report = industry_coverage_report(universe, histories, day=date(2026, 8, 7))

    assert report.is_complete is True
    assert report.classified_ratio == 1.0


def test_the_tree_refuses_a_parent_industry_code_it_cannot_resolve() -> None:
    """`build_industry_tree` closes this at build time, so reaching it needs the unvalidated
    constructor -- which is what a future writer bypassing the builder would do."""
    tree = IndustryTree(SW2021_TAXONOMY, date(2021, 12, 13), (FARMING_L3,))

    with pytest.raises(IndustryClassificationError, match="no node with industry_code '110100'"):
        tree.ancestry("850111.SI")


def test_a_reclassification_that_moves_only_the_middle_level_names_only_it() -> None:
    """L2 and L3 move independently of L1: 申万 renamed and re-parented second-level industries
    in the 2021 revision without moving every constituent between first-level ones."""
    same_l1 = (
        IndustryAssignment(
            ts_code="600519.SH",
            l1_code="801120.SI",
            l2_code="801123.SI",
            l3_code="851241.SI",
            effective_from=date(2001, 8, 27),
            effective_through=date(2021, 7, 29),
        ),
        IndustryAssignment(
            ts_code="600519.SH",
            l1_code="801120.SI",
            l2_code="801124.SI",
            l3_code="851241.SI",
            effective_from=date(2021, 7, 30),
            effective_through=None,
        ),
    )
    history = build_security_industry_history("600519.SH", same_l1, taxonomy=SW2021_TAXONOMY)

    (change,) = history.reclassifications()

    assert change.changed_levels == ("L2",)


def test_a_reclassification_out_of_an_open_assignment_is_not_across_a_gap() -> None:
    """`build_security_industry_history` cannot produce this -- an open assignment is always
    last -- so it is reached through the unvalidated constructor, which is what a future caller
    bypassing the builder would do. It answers `False` rather than raising, because a
    reclassification is a report and an unreachable state is not a question."""
    change = IndustryReclassification(
        ts_code="000001.SZ",
        effective_from=date(2020, 1, 1),
        previous=PING_AN,
        current=PING_AN,
    )

    assert change.is_across_a_gap is False


def test_counting_the_sessions_out_of_an_open_assignment_answers_zero() -> None:
    """The same unreachable state one layer up, with a calendar in hand.

    `reclassifications(sessions=...)` has to give the pair a session count, and there is no
    session strictly after "no end date". `0` is right here and `None` would be wrong: `None`
    means "no calendar was supplied", which is not what happened.
    """
    history = SecurityIndustryHistory(
        ts_code="000001.SZ",
        assignments=(PING_AN, PING_AN),
        taxonomy=SW2021_TAXONOMY,
        taxonomy_effective_from=date(2021, 12, 13),
    )

    (change,) = history.reclassifications(sessions=SSE_SESSIONS)

    assert change.unclassified_sessions == 0
    assert change.is_across_a_gap is False


def test_a_day_past_the_registry_snapshot_refuses_the_whole_report() -> None:
    """There is no per-code `beyond_snapshot` bucket: `listed_on` refuses the day before the
    loop starts, so the horizon is a refusal of the question rather than a verdict about some of
    its codes."""
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 7),
        securities=(
            SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3)),
            SecurityLifecycle(ts_code="920038.BJ", exchange="BSE", listed_on=date(2026, 8, 5)),
        ),
        years_read=(1991, 2026),
    )
    histories = industry_histories_from_panel_rows(
        [
            ("000001.SZ", "1991-04-03", None, "801780.SI", "801783.SI", "857831.SI"),
            ("920038.BJ", "2026-08-06", None, "801890.SI", "801072.SI", "850713.SI"),
        ],
        taxonomy=SW2021_TAXONOMY,
    )

    assert industry_coverage_report(universe, histories, day=date(2026, 8, 7)).is_complete is True

    with pytest.raises(UniverseHorizonError, match="beyond the 2026-08-07 registry snapshot"):
        industry_coverage_report(universe, histories, day=date(2026, 8, 8))


def test_two_assignments_that_share_their_boundary_day_are_refused() -> None:
    """The overlap rule is `<=`, not `<`, and the difference is exactly one day.

    A predecessor ending on the day its successor begins puts the security in two industries for
    that session, and `bisect` would silently answer with the later one. It is not a
    hypothetical shape the corpus rules out by luck either -- `industry_through` is inclusive, so
    an upstream that switched to an exclusive end date would produce precisely this on all 2,004
    transitions rather than on none. Measured today: the smallest gap between any consecutive
    pair is one SSE session, so zero of the 2,004 touch.
    """
    touching = (
        IndustryAssignment(
            ts_code="000001.SZ",
            l1_code="801780.SI",
            l2_code="801783.SI",
            l3_code="857831.SI",
            effective_from=date(1991, 4, 3),
            effective_through=date(2020, 1, 1),
        ),
        IndustryAssignment(
            ts_code="000001.SZ",
            l1_code="801790.SI",
            l2_code="801791.SI",
            l3_code="857911.SI",
            effective_from=date(2020, 1, 1),
            effective_through=None,
        ),
    )

    with pytest.raises(IndustryClassificationError, match="overlaps the one starting"):
        build_security_industry_history("000001.SZ", touching, taxonomy=SW2021_TAXONOMY)


# --- the disclosures ------------------------------------------------------------------


def test_the_known_limitations_are_named_rather_than_argued_away() -> None:
    """`KNOWN_INDUSTRY_LIMITATIONS` had no assertion of any kind until this one.

    Ten entries, two of which are cited by name elsewhere in the repository:
    `no_announcement_and_no_revision_history` is what dates an assignment's availability at
    `in_date`'s midnight and is cited by name in `panel_fixtures.py`'s
    `industry.reclassification_after_the_as_of` measurement, and
    `every_pre_2021_answer_is_a_backfill` is the reason `IndustryAnswer` carries a taxonomy at
    all. Cited, and until now spelled only in prose -- so a rename broke the citations and
    failed nothing.

    The set is an equality rather than a subset for the reason
    `tests/unit/test_known_limitation_registries.py` states once for all ten registries: a
    membership check cannot see a deletion, and a registry nobody can delete from by accident
    is the only kind worth citing.
    """
    assert {entry.code for entry in KNOWN_INDUSTRY_LIMITATIONS} == {
        "every_pre_2021_answer_is_a_backfill",
        "the_taxonomy_revision_itself_is_erased",
        "the_first_in_date_is_not_a_classification_event",
        "a_security_can_be_unclassified_inside_its_listed_life",
        "a_membership_can_name_a_node_the_tree_does_not_carry",
        "the_default_response_hides_the_history",
        "no_announcement_and_no_revision_history",
        "a_partial_year_read_cannot_see_an_interval_close",
        "no_cross_section_before_the_taxonomy_is_readable_at_all",
        "silent_truncation_at_the_response_cap",
    }
    assert len({entry.code for entry in KNOWN_INDUSTRY_LIMITATIONS}) == len(
        KNOWN_INDUSTRY_LIMITATIONS
    ), "a code is declared twice"
    assert all(len(entry.detail) > 120 for entry in KNOWN_INDUSTRY_LIMITATIONS)
