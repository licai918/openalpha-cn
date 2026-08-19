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

`V2-P4-027` adds a sixth, in the block at the foot of the file: **a mid-year `as_of` can assemble
a cross section against a partition that has not finished happening, and the days it cannot speak
for are refused by name rather than answered from an interval with no end in it.** The two
securities that block uses were chosen so that at 2024-06-30 one of them is open because its
closing row is *withheld* and the other because no closing row *exists* -- the two shapes this
dataset cannot tell apart from its rows alone.
"""

from __future__ import annotations

import dataclasses
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
from openalpha_cn.domain.panel_batch import PanelBatchError, TimelineColumns
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_industry_cross_section,
    load_industry_histories,
    load_industry_trees,
    merge_panel_batches,
    split_panel_batch_by_year,
    write_industry_memberships,
    write_industry_tree,
    write_panel_batch,
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


# --- `V2-P4-027`: the as-of-sensitive membership read --------------------------------------

MID_2024 = datetime(2024, 6, 30, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2024-06-30 -- inside the 2024 membership partition by seven months.

That partition's newest `available_time` is 2024-07-29T16:00Z (600423.SH's reclassification takes
effect on the 30th, Asia/Shanghai), so `read_if_ready` refuses the whole of it here. This is the
annual-review shape `V2-P4-027` names on the real corpus -- 613 rows effective 2021-07-30, 255
effective 2022-07-29 -- which a walk-forward that fetches today and replays history hits once a
year.
"""

AFTER_THE_REVISION = datetime(2025, 1, 6, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2025-01-06, after every membership event this fixture stores."""

ON_THE_REVISION_DAY = datetime(2024, 7, 30, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2024-07-30 -- the day 600423.SH's new assignment takes effect.

The one `as_of` on this corpus where the date census's `<=` and `<` are different numbers, which
is why it exists: the 2024 partition carries an event on this very day, so the read must count it
as having happened. Without an `as_of` landing exactly on a stored event date, the census
comparison could be written either way and no test could tell.
"""

ON_THE_REVISION_DAY_UTC_YESTERDAY = datetime(2024, 7, 29, 20, 0, tzinfo=UTC)
"""04:00 Asia/Shanghai on 2024-07-30, whose **UTC** date is the 29th.

The same instant question asked across the date line the panel actually uses. Every day this read
compares -- `day` against what `as_of` could see, and `as_of` against the partition's date census
-- is a day in the panel's own zone, and an `as_of` whose UTC date differs from its Asia/Shanghai
date is the only shape that can tell a zone-aware comparison from a naive one.
"""

BEFORE_THE_TAXONOMY = datetime(2015, 6, 30, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2015-06-30 -- six years before SW2021 came into force."""

LATE_AVAILABILITY = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
"""An availability instant `_taxonomy_backfill_timeline` cannot produce for a 2003 event."""

CROSS_SECTION_SCRIPT = {
    (INDUSTRY_MEMBERSHIP_DATASET, "801030.SI/N"): _response(MEMBER_FIELDS, CHEMICALS_SUPERSEDED),
    (INDUSTRY_MEMBERSHIP_DATASET, "801030.SI/Y"): _response(MEMBER_FIELDS, CHEMICALS_CURRENT),
    (INDUSTRY_MEMBERSHIP_DATASET, "801720.SI/N"): _response(MEMBER_FIELDS, BUILDING_SUPERSEDED),
    (INDUSTRY_MEMBERSHIP_DATASET, "801160.SI/Y"): _response(MEMBER_FIELDS, UTILITIES_CURRENT),
}
CROSS_SECTION_SLICES = (
    ("801030.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
    ("801030.SI", CURRENT_INDUSTRY_MEMBERSHIP),
    ("801720.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
    ("801160.SI", CURRENT_INDUSTRY_MEMBERSHIP),
)
CROSS_SECTION_YEARS = (1993, 2003, 2017, 2024)
"""Every year the two securities' six membership events fall in.

`600423.SH` opens in 2003 and is reclassified inside **2024**; `600681.SH` opens in 1993 and is
reclassified inside 2017. So at `MID_2024` one security's final assignment is open because its
closing row is **withheld** and the other's is open because no closing row **exists** -- the two
shapes this dataset cannot tell apart from the returned rows alone, on one store.
"""

AGROCHEMICALS = "801038.SI"
BASIC_CHEMICAL_MATERIALS = "801033.SI"
POWER = "801161.SI"
DECORATION = "801722.SI"


def _cross_section_store(root: Any) -> PanelStore:
    store = PanelStore(root / "panel")
    provider = _provider(CROSS_SECTION_SCRIPT)
    write_industry_memberships(store, _membership_batches(provider, CROSS_SECTION_SLICES))
    return store


def _retimed(batch: Any, moved: datetime) -> Any:
    """`batch` with every `available_time` moved to `moved` and nothing else touched.

    `ingested_time` and `revision_time` follow because `TimelineColumns` forbids either
    preceding `available_time`, and the batch's own `as_of` is raised because the write boundary
    refuses a batch carrying a row that post-dates its request -- which is the fetch-side half of
    the same point-in-time rule and is not the one under test. `event_time` is left exactly where
    the provider put it, so the only thing this fixture differs from a real partition in is the
    clock the read filters on.
    """
    rows = len(batch.timeline.event_time)
    return dataclasses.replace(
        batch,
        as_of=max(batch.as_of, moved),
        timeline=TimelineColumns(
            event_time=batch.timeline.event_time,
            available_time=(moved,) * rows,
            ingested_time=tuple(max(moved, original) for original in batch.timeline.ingested_time),
            revision_time=(moved,) * rows,
        ),
    )


def _write_retimed_memberships(store: PanelStore, *, retimed_year: int, moved: datetime) -> None:
    """Store the whole membership corpus with one year's `available_time` column moved.

    Not `write_industry_memberships`, because the split has to happen before the retiming: the
    fixture moves one *partition's* clock and leaves the other three carrying the provider's own.
    """
    provider = _provider(CROSS_SECTION_SCRIPT)
    merged = merge_panel_batches(_membership_batches(provider, CROSS_SECTION_SLICES))
    for year, yearly in split_panel_batch_by_year(merged):
        write_panel_batch(
            store, _retimed(yearly, moved) if year == retimed_year else yearly, year=year
        )


def test_a_mid_year_as_of_assembles_a_cross_section_on_a_partition_holding_a_later_revision(
    tmp_path,
) -> None:
    """`V2-P4-027`'s acceptance, with the door it extends refusing the identical read beside it.

    At `MID_2024` the 2024 partition's newest `available_time` post-dates the read, so
    `read_if_ready` refuses it whole and `load_industry_histories` -- unchanged -- reports
    `not_yet_knowable`. The day-scoped door answers, and it answers with 600423.SH in the
    industry it was actually in on 2024-06-30: the reclassification effective 2024-07-30 is
    withheld, which is what "not knowable then" means.
    """
    store = _cross_section_store(tmp_path)

    with pytest.raises(PanelStorageError, match="not_yet_knowable"):
        load_industry_histories(
            store, years=CROSS_SECTION_YEARS, as_of=MID_2024, max_staleness=None
        )

    cross_section = load_industry_cross_section(
        store,
        day=date(2024, 6, 30),
        years=CROSS_SECTION_YEARS,
        as_of=MID_2024,
        max_staleness=None,
    )

    assert sorted(cross_section) == ["600423.SH", "600681.SH"]
    assert cross_section["600423.SH"].l2_code == AGROCHEMICALS
    assert cross_section["600681.SH"].l2_code == POWER
    assert cross_section["600423.SH"].asked_for == date(2024, 6, 30)
    assert cross_section["600423.SH"].is_backfilled is False


def test_a_day_the_as_of_cannot_see_is_refused_rather_than_answered_from_an_open_interval(
    tmp_path,
) -> None:
    """The half that must not be traded away, on the fixture that makes it concrete.

    On 2024-06-30 both securities' final assignments read as open. One is open because
    600423.SH's closing row exists in the 2024 partition and was withheld; the other is open
    because 600681.SH has no row after 2017 at all. From the visible rows the two are the same
    shape, so a 2024-12-31 question asked at `MID_2024` is exactly the one this read cannot
    answer -- and it is refused by name rather than answered from the interval with no end in it.
    """
    store = _cross_section_store(tmp_path)

    with pytest.raises(PanelStorageError, match="had not happened yet"):
        load_industry_cross_section(
            store,
            day=date(2024, 12, 31),
            years=CROSS_SECTION_YEARS,
            as_of=MID_2024,
            max_staleness=None,
        )


def test_the_refused_day_is_a_real_difference_and_answers_once_the_revision_has_taken_effect(
    tmp_path,
) -> None:
    """The sentinel under the refusal above: without it, refusing 2024-12-31 would be paranoia.

    Asked at an `as_of` after the annual review, the same day answers -- and the two securities
    **diverge**. 600423.SH moved to 化学原料 on 2024-07-30 and 600681.SH did not move at all,
    which is precisely the difference `MID_2024` could not see and therefore refused to guess.
    """
    store = _cross_section_store(tmp_path)

    cross_section = load_industry_cross_section(
        store,
        day=date(2024, 12, 31),
        years=CROSS_SECTION_YEARS,
        as_of=AFTER_THE_REVISION,
        max_staleness=None,
    )

    assert cross_section["600423.SH"].l2_code == BASIC_CHEMICAL_MATERIALS
    assert cross_section["600681.SH"].l2_code == POWER


def test_the_day_a_reclassification_takes_effect_is_the_first_day_it_answers(tmp_path) -> None:
    """The census boundary, driven at the only `as_of` on this corpus where it is visible.

    A membership row becomes knowable at midnight on the day it takes effect, so an `as_of` inside
    that day must count it as having happened -- the census comparison is `<=` and not `<`. Every
    other `as_of` in this file lands on a day the census has no event for, where the two spellings
    give the same number and no assertion could separate them.

    The pair of days is what makes it an assertion about the boundary rather than about one side
    of it: read at the same instant, 2024-07-30 answers with the new industry and 2024-07-29 with
    the old one, which is `IndustryAssignment.covers`' inclusive `effective_through` seen from the
    outside.
    """
    store = _cross_section_store(tmp_path)

    on_the_day = load_industry_cross_section(
        store,
        day=date(2024, 7, 30),
        years=CROSS_SECTION_YEARS,
        as_of=ON_THE_REVISION_DAY,
        max_staleness=None,
    )
    day_before = load_industry_cross_section(
        store,
        day=date(2024, 7, 29),
        years=CROSS_SECTION_YEARS,
        as_of=ON_THE_REVISION_DAY,
        max_staleness=None,
    )

    assert on_the_day["600423.SH"].l2_code == BASIC_CHEMICAL_MATERIALS
    assert day_before["600423.SH"].l2_code == AGROCHEMICALS

    # The same instant, eight hours earlier, whose *UTC* date is the 29th. Both of this read's
    # day comparisons are in the panel's own zone, so the answer is the 30th's.
    across_the_date_line = load_industry_cross_section(
        store,
        day=date(2024, 7, 30),
        years=CROSS_SECTION_YEARS,
        as_of=ON_THE_REVISION_DAY_UTC_YESTERDAY,
        max_staleness=None,
    )
    assert across_the_date_line["600423.SH"].l2_code == BASIC_CHEMICAL_MATERIALS


def test_a_security_with_no_assignment_covering_the_day_is_left_out_rather_than_raised(
    tmp_path,
) -> None:
    """ "No industry that day" is data, and "this read cannot speak for that day" is not.

    Both used to arrive at `panel_neutralization._industry_answer` as one `IndustryHorizonError`
    and be folded into one `None`. Here they are separated: 600423.SH has no assignment covering
    1995 -- its first begins in 2003 -- so it is simply absent from the mapping, while the days
    this read may not speak for were refused before any of this (the two tests above). A security
    the corpus never carried is absent the same way, which is the same answer to the same
    question.

    The 1995 answer for 600681.SH is also a **backfill**: SW2021 did not exist then, and
    `IndustryAnswer.is_backfilled` says so on the row rather than leaving it to be inferred.
    """
    store = _cross_section_store(tmp_path)

    cross_section = load_industry_cross_section(
        store,
        day=date(1995, 6, 30),
        years=(1993, 2003),
        as_of=MID_2024,
        max_staleness=None,
    )

    assert sorted(cross_section) == ["600681.SH"]
    assert cross_section["600681.SH"].l2_code == DECORATION
    assert cross_section["600681.SH"].is_backfilled is True


def test_a_withheld_row_and_an_absent_one_are_two_different_row_counts(tmp_path) -> None:
    """The question `tests/unit/panel/test_visible_read_callers.py` makes every caller answer.

    The partition's own date census records how many rows carry each event date, so this read
    knows how many rows it *must* see at an `as_of` instead of inferring it from what came back.
    A **withheld** row is one the census counts at or before `as_of` and the predicate removed;
    an **absent** row is one the census never counted. The first is a refusal, the second is the
    ordinary shape of a corpus in which most securities are never reclassified.

    Driven on two stores holding the same six rows and differing in one clock. On the first the
    2003 partition's `available_time` is moved to 2026 -- an instant `_taxonomy_backfill_timeline`
    cannot produce for a 2003 event -- so at `MID_2024` the census says one 2003 row had happened
    and the read sees none of it. On the second the identical read answers, because there the
    only withheld rows are 2024's, whose events genuinely had not happened.
    """
    doctored = PanelStore(tmp_path / "doctored" / "panel")
    _write_retimed_memberships(doctored, retimed_year=2003, moved=LATE_AVAILABILITY)

    with pytest.raises(PanelStorageError, match="whose event had already happened"):
        load_industry_cross_section(
            doctored,
            day=date(2024, 6, 30),
            years=CROSS_SECTION_YEARS,
            as_of=MID_2024,
            max_staleness=None,
        )

    honest = _cross_section_store(tmp_path / "honest")
    answered = load_industry_cross_section(
        honest,
        day=date(2024, 6, 30),
        years=CROSS_SECTION_YEARS,
        as_of=MID_2024,
        max_staleness=None,
    )
    assert answered["600423.SH"].l2_code == AGROCHEMICALS


def test_a_stored_membership_year_at_or_before_the_day_that_the_read_skipped_is_refused(
    tmp_path,
) -> None:
    """`answerable_through`'s rule, restated as a refusal about `day` rather than about a year.

    Naming 2003 and 2024 and not 2017 leaves 600681.SH's 2017 close unread, so its 1993
    assignment reassembles as an interval that never ends and a 2024 question would answer with
    an industry it left in 2017. The unfiltered door bounds the histories at 2016 and lets the
    caller trip over that later; this door is asked about one day and refuses it here.

    The same rule the other way round is what keeps the door usable: a stored year **after**
    `day` bounds nothing, because an event later than `day` cannot change who covered it.
    """
    store = _cross_section_store(tmp_path)

    with pytest.raises(PanelStorageError, match="did not name"):
        load_industry_cross_section(
            store,
            day=date(2024, 6, 30),
            years=(2003, 2024),
            as_of=MID_2024,
            max_staleness=None,
        )

    backfilled = load_industry_cross_section(
        store,
        day=date(2016, 6, 30),
        years=(1993, 2003),
        as_of=MID_2024,
        max_staleness=None,
    )
    assert backfilled["600681.SH"].l2_code == DECORATION
    assert backfilled["600423.SH"].l2_code == AGROCHEMICALS


def test_an_as_of_before_the_taxonomy_is_refused_before_any_partition_is_read(tmp_path) -> None:
    """The outer floor `V2-P4-027` does not move, stated as a gate rather than left to be
    inferred.

    Every membership row's `available_time` is floored at SW2021's own effective date, so at an
    `as_of` in 2015 the filtered read would find every row withheld and hand back an empty
    mapping -- a market with no industries in it, which is a different fact from "not knowable
    yet". Refused before a partition is touched, as the unfiltered door refuses it with
    `not_yet_knowable`.
    """
    store = _cross_section_store(tmp_path)

    with pytest.raises(PanelStorageError, match="SW2021 came into force"):
        load_industry_cross_section(
            store,
            day=date(2015, 6, 30),
            years=(1993, 2003),
            as_of=BEFORE_THE_TAXONOMY,
            max_staleness=None,
        )


def test_the_two_doors_answer_the_same_day_the_same_way_where_the_unfiltered_one_answers(
    tmp_path,
) -> None:
    """The change is an extension and this is the half that says so.

    At `AS_OF` every stored row's `available_time` precedes the read, so the predicate removes
    nothing and the two doors look at the identical rows. They are held to the identical answer
    on the identical day, security by security.
    """
    store = _cross_section_store(tmp_path)
    day = date(2024, 12, 31)

    histories = load_industry_histories(
        store, years=CROSS_SECTION_YEARS, as_of=AS_OF, max_staleness=None
    )
    cross_section = load_industry_cross_section(
        store, day=day, years=CROSS_SECTION_YEARS, as_of=AS_OF, max_staleness=None
    )

    assert sorted(cross_section) == sorted(histories)
    for ts_code, answer in cross_section.items():
        assert answer.assignment == histories[ts_code].assignment_on(day)


def test_the_declared_freshness_bound_is_decided_where_the_unfiltered_door_decides_it(
    tmp_path,
) -> None:
    """`V2-P4-026`'s correction, inherited rather than re-derived.

    `read_visible_at` re-decides `stale` over the rows it is about to return, and the rows here
    are one dataset's whole history -- reclassifications happen once a year, so a slice-scope
    bound would refuse nearly every honest read. So the bound is decided once, at partition
    scope, through the verdict `read_if_ready` itself would have returned, and the requirement
    handed to the filtered read waives it.
    """
    store = _cross_section_store(tmp_path)

    with pytest.raises(PanelStorageError, match="stale"):
        load_industry_cross_section(
            store,
            day=date(2024, 12, 31),
            years=CROSS_SECTION_YEARS,
            as_of=AS_OF,
            max_staleness=timedelta(days=1),
        )
