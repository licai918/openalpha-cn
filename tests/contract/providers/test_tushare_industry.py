"""`index_classify` and `index_member_all` on the descriptor table (`V2-P1-010`).

Every response body here is real, captured live on 2026-08-09; the transport is doubled so the
suite never touches the network.

The four things this file is really about:

- **The vintage has to be named.** `index_classify`'s own default is SW2014 while every
  `index_member_all` row is SW2021, so a request that does not say which tree it wants gets the
  wrong one silently.
- **`is_new` has to be named too.** The endpoint's default is a filter: a bare request returns
  only the 5,889 currently-effective assignments and hides the 2,004 superseded ones, with no
  truncation flag and nothing else to notice it by.
- **The cap is 3,000, the lowest in the table**, and `limit` only narrows it.
- **The clock refuses to claim pre-2021 knowability.** A row's `available_time` is the latest of
  its start, its end, and the day its taxonomy came into force.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_TREE_DATASET,
    SW2014_TAXONOMY,
    SW2021_TAXONOMY,
)
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    CURRENT_INDUSTRY_MEMBERSHIP,
    SUPERSEDED_INDUSTRY_MEMBERSHIP,
    TUSHARE_DATASETS,
    TUSHARE_INDUSTRY_MEMBER_ROW_CAP,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

TREE_FIELDS = [
    "index_code",
    "industry_name",
    "level",
    "industry_code",
    "is_pub",
    "parent_code",
    "src",
]
# index_classify(src=SW2021): the 农林牧渔 spine, verbatim.
SW2021_SPINE = (
    ["801010.SI", "农林牧渔", "L1", "110000", "1", "0", "SW2021"],
    ["801016.SI", "种植业", "L2", "110100", "1", "110000", "SW2021"],
    ["850111.SI", "种子", "L3", "110101", "1", "110100", "SW2021"],
)
# index_classify(index_code=850412.SI): the SW2014 leaf 25 membership rows name, with its null
# `is_pub` -- all 227 SW2014 L3 rows carry one.
SW2014_ORPHAN_LEAF = ["850412.SI", "特钢", "L3", "230102", None, "230100", "SW2014"]

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
# index_member_all(l1_code=801780.SI, is_new=Y): the first row of the 42-row banks slice.
PING_AN_CURRENT = [
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
]
# index_member_all(is_new=N): the first row of the 2,004-row superseded set.
BAICHUAN_SUPERSEDED = [
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
]


def _response(
    fields: list[str], items: tuple[list[Any], ...], *, has_more: bool | None = False
) -> dict[str, Any]:
    data: dict[str, Any] = {"fields": list(fields), "items": [list(row) for row in items]}
    if has_more is not None:
        data[TUSHARE_RESPONSE_TRUNCATION_FLAG] = has_more
    return {"code": 0, "msg": "", "data": data}


def _provider(transport_factory: Any, response: dict[str, Any], *, clock: datetime) -> Any:
    transport = transport_factory(response)
    return (
        TushareProvider(token="secret-token", transport=transport, clock=lambda: clock),
        transport,
    )


def _descriptor(name: str) -> Any:
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
    return descriptor


AS_OF = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")


# --------------------------------------------------------------------------------------
# The tree request names its vintage
# --------------------------------------------------------------------------------------


def test_the_tree_request_asks_for_one_named_vintage_and_all_three_levels(
    fake_tushare_transport,
) -> None:
    """`src` alone returns the whole tree: 511 rows for SW2021 (31/134/346) with
    `has_more=False`, so no per-level request is needed."""
    provider, transport = _provider(
        fake_tushare_transport, _response(TREE_FIELDS, SW2021_SPINE), clock=AS_OF
    )

    provider.fetch_panel(
        ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
    )

    assert transport.payload is not None
    assert transport.payload["params"] == {"src": SW2021_TAXONOMY}
    assert transport.payload["api_name"] == INDUSTRY_TREE_DATASET


def test_a_tree_request_without_a_vintage_is_refused_rather_than_defaulted(
    fake_tushare_transport,
) -> None:
    """The endpoint's own default is **SW2014**: a bare request returns 359 rows and so does
    `src=''`, while every `index_member_all` row is SW2021. Defaulting here would build the
    wrong tree and nothing downstream could tell."""
    provider, _ = _provider(
        fake_tushare_transport, _response(TREE_FIELDS, SW2021_SPINE), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match="names its taxonomy vintage"):
        provider.fetch_panel(ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF))


def test_a_tree_request_for_an_undated_vintage_is_refused(fake_tushare_transport) -> None:
    """`src='SW'`, `'CI'` and `'SW2014L1'` all return zero rows, so an unrecognised vintage is
    not a silent empty partition -- and one whose effective date is unmeasured could not be
    given an honest `available_time` anyway."""
    provider, _ = _provider(
        fake_tushare_transport, _response(TREE_FIELDS, SW2021_SPINE), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match="has no measured effective date"):
        provider.fetch_panel(
            ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=("CI",))
        )


def test_two_vintages_in_one_tree_request_are_refused(fake_tushare_transport) -> None:
    provider, _ = _provider(
        fake_tushare_transport, _response(TREE_FIELDS, SW2021_SPINE), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match="serves one taxonomy vintage per request"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDUSTRY_TREE_DATASET,
                as_of=AS_OF,
                subjects=(SW2021_TAXONOMY, SW2014_TAXONOMY),
            )
        )


def test_the_tree_is_dated_at_its_own_vintages_effective_day(fake_tushare_transport) -> None:
    """`index_classify` has no date column of any kind, so the provider synthesises one: the
    day the vintage came into force, which is the one instant every node in it shares."""
    provider, _ = _provider(
        fake_tushare_transport, _response(TREE_FIELDS, SW2021_SPINE), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
    )

    (dated,) = (column for column in batch.columns if column.name == "taxonomy_date")
    assert set(dated.values) == {"2021-12-13"}
    vintage_midnight = datetime(2021, 12, 13, tzinfo=SHANGHAI)
    assert set(batch.timeline.event_time) == {vintage_midnight}
    assert set(batch.timeline.available_time) == {vintage_midnight}


def test_the_tree_projection_keeps_both_identifiers_and_a_nullable_is_pub(
    fake_tushare_transport,
) -> None:
    """`index_code` joins a membership row, `industry_code` is what `parent_code` points at, and
    `is_pub` is genuinely null for all 227 SW2014 L3 rows."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(TREE_FIELDS, (SW2014_ORPHAN_LEAF,)),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2014_TAXONOMY,))
    )

    values = {column.name: column.values for column in batch.columns}
    assert batch.subjects == ("850412.SI",)
    assert values["industry_code"] == ("230102",)
    assert values["parent_code"] == ("230100",)
    assert values["is_published"] == (None,)
    assert values["taxonomy"] == (SW2014_TAXONOMY,)
    assert values["taxonomy_date"] == ("2014-02-21",)


def test_an_is_pub_that_is_neither_a_flag_nor_null_is_refused(fake_tushare_transport) -> None:
    """`_open_flag`'s rule, for `_open_flag`'s reason: `bool('0')` is `True`, so a coercion
    would turn a schema change into a wrong answer about which industries carry an index."""
    drifted = ["801010.SI", "农林牧渔", "L1", "110000", "yes", "0", "SW2021"]
    provider, _ = _provider(fake_tushare_transport, _response(TREE_FIELDS, (drifted,)), clock=AS_OF)

    with pytest.raises(ProviderFailure, match="is_published must be 0, 1 or absent"):
        provider.fetch_panel(
            ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
        )


# --------------------------------------------------------------------------------------
# The membership request names its half
# --------------------------------------------------------------------------------------


def test_the_membership_request_names_one_l1_slice_and_one_membership_state(
    fake_tushare_transport,
) -> None:
    """31 L1 codes x 2 states = 62 requests for the whole 7,893-row corpus, every one of them
    far under the 3,000-row cap (the largest is 801890.SI at 625 current and 229 superseded)."""
    provider, transport = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
        )
    )

    assert transport.payload is not None
    assert transport.payload["params"] == {"l1_code": "801780.SI", "is_new": "Y"}


def test_the_membership_request_will_not_let_the_state_default(fake_tushare_transport) -> None:
    """The one refusal this dataset exists for. A bare request returns 3,000 then 2,889 rows,
    all `is_new='Y'` with an empty `out_date` -- one row per security, no truncation flag, and
    the 2,004 superseded assignments simply absent. `is_new=''` and `is_new='Y,N'` behave the
    same way, so there is no request shape that returns both halves."""
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match="hides the 2,004 superseded"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDUSTRY_MEMBERSHIP_DATASET, as_of=AS_OF, subjects=("801780.SI",)
            )
        )


def test_an_unknown_membership_state_is_refused(fake_tushare_transport) -> None:
    """`is_new='A'` and `is_new='0'` both return zero rows, so a typo would be an empty
    partition rather than an error."""
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match="membership state must be 'Y' or 'N'"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDUSTRY_MEMBERSHIP_DATASET, as_of=AS_OF, subjects=("801780.SI", "0")
            )
        )


def test_the_membership_projection_stores_the_codes_and_drops_the_names(
    fake_tushare_transport,
) -> None:
    """The three `*_name` columns are the *current* tree's labels and `name` is the current
    registry's security name; both would be a 2026 value stamped on a 1991 row, which is why
    `stock_basic` does not project `name` either."""
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
        )
    )

    assert batch.subjects == ("000001.SZ",)
    assert [column.name for column in batch.columns] == [
        "industry_from",
        "industry_through",
        "l1_code",
        "l2_code",
        "l3_code",
    ]
    values = {column.name: column.values for column in batch.columns}
    assert values["industry_from"] == ("1991-04-03",)
    assert values["industry_through"] == (None,)
    assert values["l3_code"] == ("857831.SI",)


# --------------------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------------------


def test_a_backfilled_assignment_is_available_no_earlier_than_its_taxonomy(
    fake_tushare_transport,
) -> None:
    """000001.SZ's assignment starts 1991-04-03 and is labelled with SW2021 nodes. SW2021 came
    into force 2021-12-13, so 1991 is the event and 2021 is the earliest honest availability --
    the alternative dates a 2021 classification at a 1991 instant, which is the 1,461,237
    (name x session) look-ahead this dataset's own rows cannot see."""
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
        )
    )

    assert batch.timeline.event_time[0] == datetime(1991, 4, 3, tzinfo=SHANGHAI)
    sw2021_born = INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY]
    assert batch.timeline.available_time[0] == datetime(
        sw2021_born.year, sw2021_born.month, sw2021_born.day, tzinfo=SHANGHAI
    )


def test_a_closed_assignment_is_not_available_before_it_closed(fake_tushare_transport) -> None:
    """A row carries both ends of its interval, so it cannot be knowable before the later one.

    This is `stock_basic`'s dilemma answered by waiting instead of splitting: dating this row at
    `in_date` would make its 2017 termination readable in 1993. The residue is the other
    direction and is measured -- 249,568 (name x session) of the SW2021 era's 6,203,397 (4.02%)
    are answered as unclassified by a replay whose `as_of` falls inside an interval that had not
    closed yet.
    """
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (BAICHUAN_SUPERSEDED,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801720.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
        )
    )

    assert batch.timeline.event_time[0] == datetime(1993, 10, 18, tzinfo=SHANGHAI)
    # max(1993-10-18, 2017-05-25, 2021-12-13)
    assert batch.timeline.available_time[0] == datetime(2021, 12, 13, tzinfo=SHANGHAI)


def test_an_assignment_that_closed_after_its_taxonomy_is_dated_at_its_close(
    fake_tushare_transport,
) -> None:
    """617 rows have an `out_date` at or after 2021-12-13, so the third bound is not decorative:
    for those the interval's end is the latest of the three and is what dates the row."""
    later = list(BAICHUAN_SUPERSEDED)
    later[MEMBER_FIELDS.index("in_date")] = "20220729"
    later[MEMBER_FIELDS.index("out_date")] = "20240729"
    provider, _ = _provider(fake_tushare_transport, _response(MEMBER_FIELDS, (later,)), clock=AS_OF)

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801720.SI", SUPERSEDED_INDUSTRY_MEMBERSHIP),
        )
    )

    assert batch.timeline.event_time[0] == datetime(2022, 7, 29, tzinfo=SHANGHAI)
    assert batch.timeline.available_time[0] == datetime(2024, 7, 29, tzinfo=SHANGHAI)


def test_a_row_not_yet_knowable_at_the_as_of_is_dropped_rather_than_stored(
    fake_tushare_transport,
) -> None:
    """The point-in-time filter every panel fetch runs, seen through this dataset's own clock:
    replayed at a 2015 `as_of`, an SW2021-labelled assignment is not data that was withheld, it
    is data that did not exist."""
    as_of_2015 = datetime(2015, 6, 30, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=as_of_2015
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=as_of_2015,
            subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
        )
    )

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet knowable" in batch.no_data_reason


# --------------------------------------------------------------------------------------
# The cap
# --------------------------------------------------------------------------------------


def test_the_membership_cap_is_three_thousand_and_is_the_lowest_in_the_table() -> None:
    """Measured: a bare request returns exactly 3,000 rows with `has_more=True`, and
    `limit=3001`, `5000` and `10000` all return the same 3,000. `limit` is not ignored -- it
    only narrows, `limit=100` returning 100 -- so 3,000 is the server's own ceiling."""
    assert TUSHARE_INDUSTRY_MEMBER_ROW_CAP == 3000
    caps = [
        entry.max_rows_per_response
        for entry in TUSHARE_DATASETS
        if entry.max_rows_per_response is not None
    ]
    assert min(caps) == TUSHARE_INDUSTRY_MEMBER_ROW_CAP


def test_a_membership_response_at_the_cap_is_refused(fake_tushare_transport) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        # `has_more=False`, so it is the row count and nothing else that refuses this: a
        # response *at* the cap cannot be told apart from one the cap truncated.
        _response(MEMBER_FIELDS, tuple([PING_AN_CURRENT] * 3000), has_more=False),
        clock=AS_OF,
    )

    with pytest.raises(ProviderFailure, match="measured per-response cap of 3000"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDUSTRY_MEMBERSHIP_DATASET,
                as_of=AS_OF,
                subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
            )
        )


def test_both_industry_descriptors_demand_the_truncation_flag() -> None:
    """`index_classify`'s cap is unmeasurable from outside, exactly as `stock_basic`'s is: the
    511-row SW2021 tree returns `has_more=False`, `limit=100000` also returns 511 with
    `has_more=False`, and `limit=511` returns 511 with `has_more=True` -- so the flag turns
    `True` at whatever effective limit is in force and no probe can find the real ceiling. The
    flag is that descriptor's only witness. `index_member_all` has a measured cap and demands
    the flag as well, because a truncated slice is silently *wrong* rather than short: the rows
    it drops are the oldest, so a security's earliest assignments disappear and
    `SecurityIndustryHistory` answers its whole early life from a horizon error or, worse, from
    a later assignment that starts too soon."""
    assert _descriptor(INDUSTRY_TREE_DATASET).max_rows_per_response is None
    assert _descriptor(INDUSTRY_TREE_DATASET).requires_truncation_flag is True
    assert _descriptor(INDUSTRY_MEMBERSHIP_DATASET).requires_truncation_flag is True


def test_neither_industry_dataset_serves_the_evidence_plane(fake_tushare_transport) -> None:
    """`index_classify` because its date column is synthesised -- there is no verbatim response
    row to hand back under a single clock. `index_member_all` because its response carries
    `l1_name`/`l2_name`/`l3_name` from the *current* tree (25 rows say 特钢Ⅲ for a node the
    SW2021 tree does not carry at all) and `name` from the current registry, so a verbatim
    payload would be re-proved as though four 2026 labels had been contemporaneous with a 1991
    event."""
    provider, _ = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    for dataset, subjects in (
        (INDUSTRY_TREE_DATASET, (SW2021_TAXONOMY,)),
        (INDUSTRY_MEMBERSHIP_DATASET, ("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP)),
    ):
        with pytest.raises(ProviderFailure, match="served only on the panel plane"):
            provider.fetch(ProviderRequest(dataset=dataset, as_of=AS_OF, subjects=subjects))


def test_the_token_never_reaches_a_stored_column_or_a_uri(fake_tushare_transport) -> None:
    provider, transport = _provider(
        fake_tushare_transport, _response(MEMBER_FIELDS, (PING_AN_CURRENT,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            as_of=AS_OF,
            subjects=("801780.SI", CURRENT_INDUSTRY_MEMBERSHIP),
        )
    )

    assert transport.payload is not None
    assert transport.payload["token"] == "secret-token"
    assert batch.source_uri is not None
    assert "secret-token" not in batch.source_uri
    assert not any(value == "secret-token" for column in batch.columns for value in column.values)
    assert "secret-token" not in batch.subjects


@pytest.mark.parametrize(
    ("published", "expected"),
    [("1", True), ("0", False), (1, True), (0, False), (True, True), (None, None), ("", None)],
)
def test_is_published_accepts_every_shape_the_endpoint_actually_sends(
    fake_tushare_transport, published: object, expected: bool | None
) -> None:
    """The two vintages disagree about this column's type -- SW2021 sends the strings `'1'` and
    `'0'`, SW2014 sends null on all 227 of its L3 rows -- and JSON has one number type, so an
    upstream that stopped quoting the flag would send `1`. All of those are the same fact; the
    string `'yes'` is not, and is refused above."""
    row = ["801010.SI", "农林牧渔", "L1", "110000", published, "0", "SW2021"]
    provider, _ = _provider(fake_tushare_transport, _response(TREE_FIELDS, (row,)), clock=AS_OF)

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
    )

    (flag,) = (column for column in batch.columns if column.name == "is_published")
    assert flag.values == (expected,)


def test_a_tree_node_from_an_undated_vintage_is_refused_at_decode(
    fake_tushare_transport,
) -> None:
    """The params builder refuses an unknown `src` on the way out; this is the same refusal on
    the way back, for a response that carries a vintage nobody asked for."""
    row = ["801010.SI", "农林牧渔", "L1", "110000", "1", "0", "SW2099"]
    provider, _ = _provider(fake_tushare_transport, _response(TREE_FIELDS, (row,)), clock=AS_OF)

    with pytest.raises(ProviderFailure, match="has no measured effective date"):
        provider.fetch_panel(
            ProviderRequest(dataset=INDUSTRY_TREE_DATASET, as_of=AS_OF, subjects=(SW2021_TAXONOMY,))
        )
