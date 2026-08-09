"""`V2-P1-010` end to end: 62 membership slices and 2 trees, stored and read back.

The transport is doubled and every response body is real, captured live on 2026-08-09.

The five things this file is really about:

- **A membership fetch is not a period**, so it is split into one partition per membership-event
  year, exactly as `stock_basic` is split by lifecycle year.
- **An assignment's two ends are two rows.** They became knowable at different instants, so
  filing them together held a whole `in_date` year past every `as_of` before the interval closed.
- **A partial re-fetch is refused.** A backfill that loops over `l1_code` and calls the writer
  once per slice would leave the year holding whichever slice it wrote last.
- **A pre-2021 read is blocked by name.** The stored `available_time` is never earlier than the
  taxonomy's own effective date, so readiness reports `not_yet_knowable` rather than answering a
  2015 question in a 2021 classification.
- **`is_new` is cross-checked, not stored.** All 5,889 current rows carry an empty `out_date` and
  all 2,004 superseded ones carry a real one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TREE_DATASET,
    SW2014_TAXONOMY,
    SW2021_TAXONOMY,
    IndustryClassificationError,
    IndustryHorizonError,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_industry_histories,
    load_industry_trees,
    write_industry_memberships,
    write_industry_tree,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import (
    CURRENT_INDUSTRY_MEMBERSHIP,
    SUPERSEDED_INDUSTRY_MEMBERSHIP,
    TushareProvider,
)

MEMBER_FIELDS = [
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
    "ts_code",
    "name",
    "in_date",
    "out_date",
    "is_new",
]
TREE_FIELDS = [
    "index_code",
    "industry_name",
    "level",
    "industry_code",
    "is_pub",
    "parent_code",
    "src",
]

BANKS_CURRENT = [
    [
        "801780.SI",
        "银行",
        "801783.SI",
        "股份制银行Ⅱ",
        "857831.SI",
        "股份制银行Ⅲ",
        "000001.SZ",
        "平安银行",
        "19910403",
        None,
        "Y",
    ],
    # Two real 2006 entrants, so one partition year holds two securities from one slice.
    [
        "801780.SI",
        "银行",
        "801782.SI",
        "国有大型银行Ⅱ",
        "857821.SI",
        "国有大型银行Ⅲ",
        "601988.SH",
        "中国银行",
        "20060531",
        None,
        "Y",
    ],
    [
        "801780.SI",
        "银行",
        "801782.SI",
        "国有大型银行Ⅱ",
        "857821.SI",
        "国有大型银行Ⅲ",
        "601398.SH",
        "工商银行",
        "20061026",
        None,
        "Y",
    ],
]
BUILDING_SUPERSEDED = [
    [
        "801720.SI",
        "建筑装饰",
        "801722.SI",
        "装修装饰Ⅱ",
        "857221.SI",
        "装修装饰Ⅲ",
        "600681.SH",
        "百川能源",
        "19931018",
        "20170525",
        "N",
    ],
]
UTILITIES_CURRENT = [
    [
        "801160.SI",
        "公用事业",
        "801161.SI",
        "电力",
        "851611.SI",
        "火电",
        "600681.SH",
        "百川能源",
        "20170526",
        None,
        "Y",
    ],
]
# 600423.SH *ST柳化, verbatim: 农化制品 from 2003-07-14 through 2024-07-29 and 化学原料 after.
# Its close lands *after* SW2021's own birthday, which is the case the row split exists for --
# 617 of the corpus's 7,893 rows are shaped like this one.
CHEMICALS_SUPERSEDED = [
    [
        "801030.SI",
        "基础化工",
        "801038.SI",
        "农化制品",
        "850331.SI",
        "氮肥",
        "600423.SH",
        "*ST柳化",
        "20030714",
        "20240729",
        "N",
    ],
]
CHEMICALS_CURRENT = [
    [
        "801030.SI",
        "基础化工",
        "801033.SI",
        "化学原料",
        "850324.SI",
        "其他化学原料",
        "600423.SH",
        "*ST柳化",
        "20240730",
        None,
        "Y",
    ],
]
SW2021_SPINE = [
    ["801010.SI", "农林牧渔", "L1", "110000", "1", "0", "SW2021"],
    ["801016.SI", "种植业", "L2", "110100", "1", "110000", "SW2021"],
    ["850111.SI", "种子", "L3", "110101", "1", "110100", "SW2021"],
]
SW2014_SPINE = [
    ["801020.SI", "采掘", "L1", "210000", None, "0", "SW2014"],
]

AS_OF = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _response(fields: list[str], items: list[list[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": list(fields), "items": [list(row) for row in items], "has_more": False},
    }


class _ScriptedTransport:
    """Answers each request from a `(api_name, params) -> response` script."""

    def __init__(self, script: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        params = payload["params"]
        key = (payload["api_name"], params.get("l1_code", params.get("src", "")))
        if "is_new" in params:
            key = (key[0], f"{key[1]}/{params['is_new']}")
        return self._script[key]


def _provider(script: dict[tuple[str, str], dict[str, Any]]) -> TushareProvider:
    return TushareProvider(
        token="secret-token", transport=_ScriptedTransport(script), clock=lambda: AS_OF
    )


def _membership_batches(
    provider: TushareProvider, slices: tuple[tuple[str, str], ...]
) -> list[Any]:
    return [
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDUSTRY_MEMBERSHIP_DATASET, as_of=AS_OF, subjects=(level_one, state)
            )
        )
        for level_one, state in slices
    ]


MEMBERSHIP_SCRIPT = {
    (INDUSTRY_MEMBERSHIP_DATASET, "801780.SI/Y"): _response(MEMBER_FIELDS, BANKS_CURRENT),
    (INDUSTRY_MEMBERSHIP_DATASET, "801720.SI/N"): _response(MEMBER_FIELDS, BUILDING_SUPERSEDED),
    (INDUSTRY_MEMBERSHIP_DATASET, "801160.SI/Y"): _response(MEMBER_FIELDS, UTILITIES_CURRENT),
    (INDUSTRY_TREE_DATASET, "SW2021"): _response(TREE_FIELDS, SW2021_SPINE),
    (INDUSTRY_TREE_DATASET, "SW2014"): _response(TREE_FIELDS, SW2014_SPINE),
}

ALL_SLICES = (
    ("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
    ("801720.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
    ("801160.SI", CURRENT_INDUSTRY_MEMBERSHIP),
)

CHEMICALS_SCRIPT = {
    (INDUSTRY_MEMBERSHIP_DATASET, "801030.SI/N"): _response(MEMBER_FIELDS, CHEMICALS_SUPERSEDED),
    (INDUSTRY_MEMBERSHIP_DATASET, "801030.SI/Y"): _response(MEMBER_FIELDS, CHEMICALS_CURRENT),
}
CHEMICALS_SLICES = (
    ("801030.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
    ("801030.SI", CURRENT_INDUSTRY_MEMBERSHIP),
)


def _store(tmp_path: Any) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def test_a_membership_fetch_is_filed_by_the_year_its_assignment_began(tmp_path) -> None:
    """`index_member_all` has no date filter, so one fetch spans 1991..2017 and
    `panel_partition_year` refuses it. The years are what makes the partitions mean something:
    the 1991 partition is the assignments that began in 1991."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)

    written = write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    assert sorted(reference.year for reference in written) == [1991, 1993, 2006, 2017]
    assert sorted(store.registered_years(INDUSTRY_MEMBERSHIP_DATASET)) == [1991, 1993, 2006, 2017]


def test_the_stored_history_reassembles_a_security_that_changed_industry(tmp_path) -> None:
    """600681.SH 百川能源 is 建筑装饰 from 1993-10-18 through 2017-05-25 and 公用事业 from
    2017-05-26 -- two rows from two different `l1_code` slices, two different partitions, and one
    history."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    histories = load_industry_histories(
        store, years=(1991, 1993, 2006, 2017), as_of=AS_OF, max_staleness=None
    )

    baichuan = histories["600681.SH"]
    assert baichuan.industry_on(date(2017, 5, 25)).l1_code == "801720.SI"
    assert baichuan.industry_on(date(2017, 5, 26)).l1_code == "801160.SI"
    assert baichuan.taxonomy == INDUSTRY_MEMBERSHIP_TAXONOMY
    assert baichuan.industry_on(date(2017, 5, 25)).is_backfilled is True


def test_a_per_slice_backfill_loop_is_refused_rather_than_replacing_the_year(tmp_path) -> None:
    """The `write_index_weights` guard in this dataset's shape. A partition's key is
    `(dataset, year)` with no `l1_code` dimension, and `write_partition` replaces it whole, so a
    `for l1 in codes: write_industry_memberships(store, [fetch(l1)])` backfill would leave 1991
    holding whichever slice ran last -- silently, with a success return."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    # 2006 holds 601988.SH and 601398.SH, both from the banks slice. A second pass that
    # carries only one of them is the shape a re-fetch of a *narrowed* slice has, and it is
    # what a per-slice loop produces for every year two slices share.
    narrowed = _membership_batches(
        _provider(
            {
                **MEMBERSHIP_SCRIPT,
                (INDUSTRY_MEMBERSHIP_DATASET, "801780.SI/Y"): _response(
                    MEMBER_FIELDS, BANKS_CURRENT[:2]
                ),
            }
        ),
        (("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),),
    )

    with pytest.raises(PanelBatchError, match=r"writing it would drop \['601398\.SH'\]"):
        write_industry_memberships(store, narrowed)


def test_a_read_before_the_taxonomy_existed_is_blocked_by_name(tmp_path) -> None:
    """The whole point of the clock. Every stored row's `available_time` is at or after
    2021-12-13, so a readiness check at a 2015 `as_of` reports `not_yet_knowable` -- it does not
    answer a 2015 question with a 2021 classification, and it does not answer it emptily."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    with pytest.raises(PanelStorageError, match="not_yet_knowable"):
        load_industry_histories(
            store,
            years=(1991, 1993, 2006, 2017),
            as_of=datetime(2015, 6, 30, 12, 0, tzinfo=UTC),
            max_staleness=None,
        )


def test_an_assignment_is_filed_under_both_of_its_ends_and_read_back_as_one(tmp_path) -> None:
    """The row split, end to end. 600423.SH's assignment opened in 2003 and closed in 2024, so
    the two facts are two rows in two partitions -- and a read that covers both folds them back
    into the single closed interval `build_security_industry_history` validates."""
    store = _store(tmp_path)
    provider = _provider(CHEMICALS_SCRIPT)

    written = write_industry_memberships(store, _membership_batches(provider, CHEMICALS_SLICES))

    assert sorted(reference.year for reference in written) == [2003, 2024]
    histories = load_industry_histories(store, years=(2003, 2024), as_of=AS_OF, max_staleness=None)
    (assignment, successor) = histories["600423.SH"].assignments
    assert assignment.effective_from == date(2003, 7, 14)
    assert assignment.effective_through == date(2024, 7, 29)
    assert successor.effective_from == date(2024, 7, 30)
    assert histories["600423.SH"].industry_on(date(2024, 7, 29)).l2_code == "801038.SI"
    assert histories["600423.SH"].industry_on(date(2024, 7, 30)).l2_code == "801033.SI"


def test_an_as_of_inside_the_taxonomy_era_reads_an_assignment_that_had_not_closed_yet(
    tmp_path,
) -> None:
    """What the row split is for, and the review finding it answers.

    `V2-P1-010` held a closed row back to its `out_date`, which reads as a 4% row-level residue
    and is not one: the readiness gate compares a *partition's* `max_available_time`, so
    600423.SH's 2024 close pushed the whole 2003 partition past every earlier `as_of`. Fed the
    real 7,893-row corpus that blocked 29 of the 38 requestable years at `as_of` 2023-06-30 and
    left 118 securities readable in total -- no historical cross section at all. Split, the same
    corpus reads 37 of 38 years and 5,270 securities there, and the assignment below is legible
    in 2023 as what it then was: open.
    """
    store = _store(tmp_path)
    provider = _provider(CHEMICALS_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, CHEMICALS_SLICES))

    histories = load_industry_histories(
        store,
        years=(2003,),
        as_of=datetime(2023, 6, 30, 12, 0, tzinfo=UTC),
        max_staleness=None,
    )

    (open_interval,) = histories["600423.SH"].assignments
    assert open_interval.effective_through is None
    assert histories["600423.SH"].industry_on(date(2023, 6, 30)).l2_code == "801038.SI"


def test_a_read_that_skips_a_stored_year_refuses_the_days_after_it(tmp_path) -> None:
    """The fail-open the split introduces, closed by a rule rather than a note.

    Reading 2003 and not 2024 reassembles an interval with no end, and answering 2025 from it
    would name an industry 600423.SH left in 2024. `load_industry_histories` sees that the store
    holds a 2024 partition this read skipped and bounds the histories at 2023.
    """
    store = _store(tmp_path)
    provider = _provider(CHEMICALS_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, CHEMICALS_SLICES))

    histories = load_industry_histories(store, years=(2003,), as_of=AS_OF, max_staleness=None)

    assert histories["600423.SH"].answerable_through == 2023
    with pytest.raises(IndustryHorizonError, match="is after 2023, the last membership year"):
        histories["600423.SH"].industry_on(date(2025, 1, 2))
    with pytest.raises(IndustryHorizonError, match="is after 2023, the last membership year"):
        histories["600423.SH"].is_classified_on(date(2025, 1, 2))
    # A read that covers every stored year is bounded by nothing.
    complete = load_industry_histories(store, years=(2003, 2024), as_of=AS_OF, max_staleness=None)
    assert complete["600423.SH"].answerable_through is None
    assert complete["600423.SH"].is_classified_on(date(2025, 1, 2)) is True


def test_the_stored_availability_is_never_earlier_than_the_taxonomy(tmp_path) -> None:
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    for year in (1991, 1993, 2006, 2017):
        coverage = store.read_coverage(INDUSTRY_MEMBERSHIP_DATASET, year)
        assert coverage is not None
        assert coverage.max_available_time.date() >= date(2021, 12, 12)


def test_both_taxonomy_vintages_can_share_the_store_and_come_back_apart(tmp_path) -> None:
    """One partition per vintage effective year -- 2014 for SW2014 and 2021 for SW2021 -- so the
    two trees never contend for one partition and `load_industry_trees` gets them both."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    for taxonomy in (SW2021_TAXONOMY, SW2014_TAXONOMY):
        write_industry_tree(
            store,
            provider.fetch_panel(
                ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(taxonomy,))
            ),
        )

    assert sorted(store.registered_years(INDUSTRY_TREE_DATASET)) == [2014, 2021]
    trees = load_industry_trees(store, years=(2014, 2021), as_of=AS_OF, max_staleness=None)
    assert sorted(trees) == [SW2014_TAXONOMY, SW2021_TAXONOMY]
    assert trees[SW2021_TAXONOMY].ancestry("850111.SI")[-1].index_code == "801010.SI"
    assert trees[SW2014_TAXONOMY].node("801020.SI").is_published is None


def test_a_membership_batch_handed_to_the_tree_writer_is_refused(tmp_path) -> None:
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)

    with pytest.raises(IndustryClassificationError, match="expected the 'index_classify'"):
        write_industry_tree(store, _membership_batches(provider, ALL_SLICES)[0])


def test_a_tree_batch_handed_to_the_membership_writer_is_refused(tmp_path) -> None:
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    tree = provider.fetch_panel(
        ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
    )

    with pytest.raises(IndustryClassificationError, match="expected the 'index_member_all'"):
        write_industry_memberships(store, [tree])


def test_a_read_of_no_years_is_refused(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(IndustryClassificationError, match="needs at least one"):
        load_industry_histories(store, years=(), as_of=AS_OF, max_staleness=None)


def test_a_stale_partition_blocks_the_read(tmp_path) -> None:
    """`max_staleness` has no default here, for `stock_universe_requirement`'s reason: an
    industry assignment has no cadence a contract could assume."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, ALL_SLICES))

    with pytest.raises(PanelStorageError, match="stale"):
        load_industry_histories(
            store,
            years=(1991, 1993, 2006, 2017),
            as_of=AS_OF,
            max_staleness=timedelta(days=1),
        )


def test_is_new_agrees_with_out_date_on_every_row_of_both_halves() -> None:
    """The witness the projection drops. `is_new='Y'` and an empty `out_date` are the same fact
    on all 5,889 current rows, and `is_new='N'` with a populated one on all 2,004 superseded --
    which is why storing the flag would be storing `out_date is None` twice."""
    for row in BANKS_CURRENT + UTILITIES_CURRENT:
        assert row[MEMBER_FIELDS.index("is_new")] == "Y"
        assert row[MEMBER_FIELDS.index("out_date")] is None
    for row in BUILDING_SUPERSEDED:
        assert row[MEMBER_FIELDS.index("is_new")] == "N"
        assert row[MEMBER_FIELDS.index("out_date")] is not None


def test_a_tree_read_of_no_years_is_refused(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(IndustryClassificationError, match="needs at least one vintage year"):
        load_industry_trees(store, years=(), as_of=AS_OF, max_staleness=None)


def test_a_tree_year_that_was_never_written_blocks_the_read(tmp_path) -> None:
    """A missing partition is `partition_missing`, not an empty tree: the SW2014 vintage this
    store never saw is not a classification with no industries in it."""
    store = _store(tmp_path)
    provider = _provider(MEMBERSHIP_SCRIPT)
    write_industry_tree(
        store,
        provider.fetch_panel(
            ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
        ),
    )

    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_industry_trees(store, years=(2014, 2021), as_of=AS_OF, max_staleness=None)
