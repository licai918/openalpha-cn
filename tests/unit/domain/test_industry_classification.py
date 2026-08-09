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
  unclassified for a while, the longest being 000639.SZ's 4,104 sessions.
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
    SW2014_TAXONOMY,
    SW2021_L1_COUNT,
    SW2021_TAXONOMY,
    IndustryAssignment,
    IndustryClassificationError,
    IndustryHorizonError,
    IndustryNode,
    IndustryReclassification,
    IndustryTree,
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
    """000639.SZ is unclassified for 4,104 sessions between 2002-08-30 and 2019-07-23.

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
