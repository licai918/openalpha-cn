"""`adj_factor` end to end: Tushare -> day batches -> one compressed partition -> contract.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real factor data captured
live on 2026-08-08.

Two acceptances live here.

**The returns one.** `test_a_stored_year_answers_the_ex_dividend_day_correctly` computes the
2026-06-12 return for `000001.SZ` out of the store: unadjusted `-0.53%`, adjusted `+2.7423%`
against Tushare's own `pct_chg` of `2.7422%`. The signs differ, so this is not a rounding
question.

**The storage one.** A trading year is fetched one cross section at a time -- 5,387 rows on a
real 2024 session, against a 6,000-row response cap that one security's whole history (8,627
rows) blows straight through -- and the year's partition is the *merged, compressed* result.
`test_the_partition_stores_steps_and_still_answers_every_day` proves the compression is
lossless over the window it covers, and `test_a_flat_year_stores_exactly_its_two_anchors`
proves the anchors are what make a single year self-sufficient.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    ADJUSTMENT_PANEL_COLUMNS,
    AdjustmentError,
    AdjustmentHorizonError,
    PriceAdjustment,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    adjustment_requirement,
    compress_adjustment_batch,
    load_adjustment_histories,
    merge_panel_batches,
    write_adjustment_factors,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TUSHARE_RESPONSE_TRUNCATION_FLAG, TushareProvider

FIELDS = ["ts_code", "trade_date", "adj_factor"]
PING_AN = "000001.SZ"
MAOTAI = "600519.SH"

FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

# Real `adj_factor` values. `000001.SZ` steps on 2024-06-14/25/26 and 2024-10-10;
# `600519.SH` steps on 2024-06-19/25/26 and 2024-12-20. Both are flat across these five
# sessions except for the 2024-06-25 pair.
SESSIONS_2024: tuple[tuple[str, float, float], ...] = (
    ("20240624", 125.0496, 8.0204),
    ("20240625", 125.0493, 8.0205),
    ("20240626", 125.049, 8.02),
    ("20240627", 125.049, 8.02),
    ("20240628", 125.049, 8.02),
)

# 2023's tail, for the cross-partition check. `000001.SZ` held 116.713 from 2023-06-14.
SESSIONS_2023: tuple[tuple[str, float, float], ...] = (
    ("20231227", 116.713, 7.8576),
    ("20231228", 116.713, 7.8576),
    ("20231229", 116.713, 7.8576),
)

# `000001.SZ` around its 2026 ex-dividend date, from the live `adj_factor` and `daily`
# endpoints.
SESSIONS_2026: tuple[tuple[str, float, float], ...] = (
    ("20260610", 134.5794, 8.5),
    ("20260611", 134.5794, 8.5),
    ("20260612", 139.008, 8.5),
    ("20260615", 139.008, 8.5),
)
CLOSE_2026_06_11 = 11.3
CLOSE_2026_06_12 = 11.24
TUSHARE_PCT_CHG_2026_06_12 = 2.7422


class _Transport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.response


def _response(items: list[list[Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(FIELDS),
            "items": items,
            "count": 0,
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }


def _session_batch(day: str, ping_an: float, maotai: float):
    """One trading day's cross section, exactly as `fetch_panel` produces it."""
    as_of = datetime(
        int(day[:4]), int(day[4:6]), int(day[6:]), 12, 0, tzinfo=UTC
    )  # 20:00 Asia/Shanghai, after the 16:30 availability
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(_response([[PING_AN, day, ping_an], [MAOTAI, day, maotai]])),
        clock=lambda: FETCHED_AT,
    )
    return provider.fetch_panel(ProviderRequest(dataset=ADJ_FACTOR_DATASET, as_of=as_of))


def _year_batches(sessions: tuple[tuple[str, float, float], ...]):
    return [_session_batch(day, ping_an, maotai) for day, ping_an, maotai in sessions]


def _store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _seeded(tmp_path: Path, *years) -> PanelStore:
    store = _store(tmp_path)
    for sessions in years:
        write_adjustment_factors(store, _year_batches(sessions))
    return store


def _as_date(day: str) -> date:
    return date(int(day[:4]), int(day[4:6]), int(day[6:]))


# --- the returns acceptance -----------------------------------------------------------


def test_a_stored_year_answers_the_ex_dividend_day_correctly(tmp_path: Path) -> None:
    store = _seeded(tmp_path, SESSIONS_2026)
    histories = load_adjustment_histories(
        store, years=(2026,), as_of=FETCHED_AT, max_staleness=None
    )

    unadjusted = CLOSE_2026_06_12 / CLOSE_2026_06_11 - 1
    adjusted = histories[PING_AN].adjusted_return(
        start=date(2026, 6, 11),
        end=date(2026, 6, 12),
        start_price=CLOSE_2026_06_11,
        end_price=CLOSE_2026_06_12,
    )

    assert unadjusted == pytest.approx(-0.00530973, abs=1e-8)
    assert adjusted == pytest.approx(TUSHARE_PCT_CHG_2026_06_12 / 100, abs=1e-6)
    assert unadjusted < 0 < adjusted


def test_both_conventions_survive_the_round_trip(tmp_path: Path) -> None:
    store = _seeded(tmp_path, SESSIONS_2026)
    history = load_adjustment_histories(store, years=(2026,), as_of=FETCHED_AT, max_staleness=None)[
        PING_AN
    ]

    base = date(2026, 6, 15)
    forward = history.adjust_price(
        CLOSE_2026_06_11, date(2026, 6, 11), convention=PriceAdjustment.forward, base_day=base
    )
    backward = history.adjust_price(
        CLOSE_2026_06_11, date(2026, 6, 11), convention=PriceAdjustment.backward
    )
    # 前复权 discounts the pre-ex-dividend price toward the base day; 后复权 scales it up.
    assert forward < CLOSE_2026_06_11 < backward
    assert forward == pytest.approx(CLOSE_2026_06_11 * 134.5794 / 139.008)
    assert backward == pytest.approx(CLOSE_2026_06_11 * 134.5794)


# --- the storage acceptance -----------------------------------------------------------


def test_the_partition_stores_steps_and_still_answers_every_day(tmp_path: Path) -> None:
    store = _seeded(tmp_path, SESSIONS_2024)
    coverage = store.read_coverage(ADJ_FACTOR_DATASET, 2024)
    assert coverage is not None

    # Ten raw observations (5 sessions x 2 securities) compress to eight stored rows:
    # 000001.SZ keeps 06-24, 06-25, 06-26 and the 06-28 closing anchor; 600519.SH the same.
    assert coverage.row_count == 8

    histories = load_adjustment_histories(
        store, years=(2024,), as_of=FETCHED_AT, max_staleness=None
    )
    for day, ping_an, maotai in SESSIONS_2024:
        when = _as_date(day)
        assert histories[PING_AN].factor_on(when) == ping_an
        assert histories[MAOTAI].factor_on(when) == maotai


def test_a_flat_year_stores_exactly_its_two_anchors(tmp_path: Path) -> None:
    """The 2023 tail has no step at all, so the only rows worth keeping are the window's two
    ends -- and both are needed, because they are what `covered_from`/`covered_through`
    report."""
    store = _seeded(tmp_path, SESSIONS_2023)
    coverage = store.read_coverage(ADJ_FACTOR_DATASET, 2023)
    assert coverage is not None
    assert coverage.row_count == 4  # two securities x (first, last)

    history = load_adjustment_histories(store, years=(2023,), as_of=FETCHED_AT, max_staleness=None)[
        PING_AN
    ]
    assert history.covered_from == date(2023, 12, 27)
    assert history.covered_through == date(2023, 12, 29)
    assert history.factor_on(date(2023, 12, 28)) == 116.713
    assert history.change_points() == ()


def test_a_single_year_is_self_sufficient_and_refuses_the_years_it_did_not_read(
    tmp_path: Path,
) -> None:
    """The partition-boundary property. Reading 2024 alone answers every 2024 session from
    that year's own anchor, and refuses 2023 rather than extrapolating the anchor backwards.
    """
    store = _seeded(tmp_path, SESSIONS_2023, SESSIONS_2024)
    history = load_adjustment_histories(store, years=(2024,), as_of=FETCHED_AT, max_staleness=None)[
        PING_AN
    ]

    assert history.factor_on(date(2024, 6, 24)) == 125.0496
    with pytest.raises(AdjustmentHorizonError, match=r"before 000001\.SZ's first adjustment"):
        history.factor_on(date(2023, 12, 29))


def test_two_partitions_concatenate_into_one_history_across_the_year_boundary(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path, SESSIONS_2023, SESSIONS_2024)
    history = load_adjustment_histories(
        store, years=(2023, 2024), as_of=FETCHED_AT, max_staleness=None
    )[PING_AN]

    assert history.covered_from == date(2023, 12, 27)
    assert history.covered_through == date(2024, 6, 28)
    assert history.factor_on(date(2023, 12, 28)) == 116.713
    assert history.factor_on(date(2024, 6, 24)) == 125.0496
    # The 2023 partition's closing anchor and the step into 2024 are both real observations,
    # so the boundary carries no invented row.
    assert [entry.observed_on for entry in history.change_points()] == [
        date(2024, 6, 24),
        date(2024, 6, 25),
        date(2024, 6, 26),
    ]


def test_a_gap_in_the_requested_years_is_refused_rather_than_bridged(tmp_path: Path) -> None:
    """Reading 2023 and 2026 without 2024 would answer every 2024 and 2025 day with the 2023
    factor, which is a fabricated constancy across two years of corporate actions."""
    store = _seeded(tmp_path, SESSIONS_2023, SESSIONS_2024, SESSIONS_2026)
    with pytest.raises(PanelBatchError, match=r"years 2023\.\.2026 skip \[2024, 2025\]"):
        load_adjustment_histories(store, years=(2023, 2026), as_of=FETCHED_AT, max_staleness=None)


def test_a_year_that_was_never_ingested_blocks_rather_than_being_skipped(
    tmp_path: Path,
) -> None:
    store = _seeded(tmp_path, SESSIONS_2024)
    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_adjustment_histories(store, years=(2023, 2024), as_of=FETCHED_AT, max_staleness=None)


def test_an_empty_year_list_is_refused(tmp_path: Path) -> None:
    store = _seeded(tmp_path, SESSIONS_2024)
    with pytest.raises(PanelBatchError, match="needs at least one factor year"):
        load_adjustment_histories(store, years=(), as_of=FETCHED_AT, max_staleness=None)


# --- the requirement ------------------------------------------------------------------


def test_the_requirement_waives_date_coverage_on_the_record_and_says_so(
    tmp_path: Path,
) -> None:
    """A compressed step function has rows only on load-bearing days, so requiring every
    session would report a permanent `date_gap` for a partition that is complete by
    construction. The waiver is visible in the verdict rather than invisible."""
    store = _seeded(tmp_path, SESSIONS_2024)
    requirement = adjustment_requirement(years=(2024,), as_of=FETCHED_AT, max_staleness=None)
    assert requirement.required_dates is None
    assert requirement.required_subjects is None
    assert requirement.required_fields == ADJUSTMENT_PANEL_COLUMNS

    verdict = store.assess_readiness(requirement)
    assert verdict.is_ready
    assert set(verdict.checks_waived) == {"required_dates", "required_subjects", "max_staleness"}


def test_a_stale_partition_blocks_when_the_caller_states_a_bound(tmp_path: Path) -> None:
    from datetime import timedelta

    store = _seeded(tmp_path, SESSIONS_2024)
    with pytest.raises(PanelStorageError, match="'stale'"):
        load_adjustment_histories(
            store, years=(2024,), as_of=FETCHED_AT, max_staleness=timedelta(days=30)
        )


# --- merge and compress ---------------------------------------------------------------


def test_merging_keeps_every_row_and_takes_the_latest_clocks() -> None:
    batches = _year_batches(SESSIONS_2024)
    merged = merge_panel_batches(batches)

    assert merged.row_count == sum(batch.row_count for batch in batches)
    assert merged.as_of == max(batch.as_of for batch in batches)
    assert merged.fetched_at == max(batch.fetched_at for batch in batches)
    assert [column.name for column in merged.columns] == ["factor_date", "adj_factor"]


def test_merging_one_batch_is_that_batch_and_keeps_its_source_uri() -> None:
    (single,) = _year_batches((SESSIONS_2024[0],))
    merged = merge_panel_batches([single])
    assert merged.source_uri == single.source_uri
    assert merged.to_rows() == single.to_rows()


def test_merging_batches_from_different_days_drops_the_per_fetch_uri() -> None:
    """Stated rather than hidden: a merged batch has no single upstream URI, so the field
    becomes `None` instead of naming one of the fetches. What identifies every row's origin
    is the partition's own `subject` and `factor_date` columns, and what re-proves the
    partition is `PartitionCoverage.batch_digest`."""
    merged = merge_panel_batches(_year_batches(SESSIONS_2024))
    assert merged.source_uri is None


def test_merging_nothing_is_refused() -> None:
    with pytest.raises(PanelBatchError, match="needs at least one batch"):
        merge_panel_batches([])


def test_merging_two_datasets_is_refused() -> None:
    (one,) = _year_batches((SESSIONS_2024[0],))
    other = _session_batch(*SESSIONS_2024[1])
    disguised = type(other)(
        provider_id=other.provider_id,
        dataset="daily",
        kind=other.kind,
        as_of=other.as_of,
        fetched_at=other.fetched_at,
        status="success",
        subjects=other.subjects,
        timeline=other.timeline,
        columns=other.columns,
    )
    with pytest.raises(PanelBatchError, match="every batch must carry the same dataset"):
        merge_panel_batches([one, disguised])


def test_merging_a_no_data_batch_is_refused() -> None:
    (one,) = _year_batches((SESSIONS_2024[0],))
    empty = type(one)(
        provider_id=one.provider_id,
        dataset=one.dataset,
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="no_data",
        no_data_reason="the exchange was shut",
    )
    with pytest.raises(PanelBatchError, match="cannot merge a 'no_data' batch"):
        merge_panel_batches([one, empty])


def test_merging_two_providers_or_two_kinds_is_refused() -> None:
    """A partition records one provider and one kind, so a merged batch that mixed them
    would file rows under provenance that never produced them."""
    (one,) = _year_batches((SESSIONS_2024[0],))
    other = _session_batch(*SESSIONS_2024[1])
    for field_name, value in (("provider_id", "akshare"), ("kind", "prices")):
        disguised = type(other)(
            **{
                **{
                    "provider_id": other.provider_id,
                    "dataset": other.dataset,
                    "kind": other.kind,
                    "as_of": other.as_of,
                    "fetched_at": other.fetched_at,
                    "status": "success",
                    "subjects": other.subjects,
                    "timeline": other.timeline,
                    "columns": other.columns,
                },
                field_name: value,
            }
        )
        with pytest.raises(PanelBatchError, match=f"every batch must carry the same {field_name}"):
            merge_panel_batches([one, disguised])


def test_merging_batches_with_different_columns_is_refused() -> None:
    """Concatenating misaligned columns would put one dataset's values under another's
    name, which every later read would then answer from."""
    (one,) = _year_batches((SESSIONS_2024[0],))
    other = _session_batch(*SESSIONS_2024[1])
    disguised = type(other)(
        provider_id=other.provider_id,
        dataset=other.dataset,
        kind=other.kind,
        as_of=other.as_of,
        fetched_at=other.fetched_at,
        status="success",
        subjects=other.subjects,
        timeline=other.timeline,
        columns=(other.columns[0],),
    )
    with pytest.raises(PanelBatchError, match="the same columns in the same order"):
        merge_panel_batches([one, disguised])


def test_compressing_a_no_data_batch_is_refused() -> None:
    (one,) = _year_batches((SESSIONS_2024[0],))
    empty = type(one)(
        provider_id=one.provider_id,
        dataset=one.dataset,
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="no_data",
        no_data_reason="the exchange was shut",
    )
    with pytest.raises(PanelBatchError, match="cannot compress a 'no_data' batch"):
        compress_adjustment_batch(empty)


def test_compressing_a_batch_without_the_factor_column_is_refused() -> None:
    """`columns` is an ordinary tuple field, so a batch carrying this dataset's name and
    another dataset's columns is constructible; naming what is missing beats a KeyError."""
    (one,) = _year_batches((SESSIONS_2024[0],))
    stripped = type(one)(
        provider_id=one.provider_id,
        dataset=one.dataset,
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="success",
        subjects=one.subjects,
        timeline=one.timeline,
        columns=(one.columns[0],),
    )
    with pytest.raises(PanelBatchError, match="has no 'adj_factor' column"):
        compress_adjustment_batch(stripped)


def test_compression_refuses_a_dataset_that_is_not_the_factor_series() -> None:
    (one,) = _year_batches((SESSIONS_2024[0],))
    disguised = type(one)(
        provider_id=one.provider_id,
        dataset="daily",
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="success",
        subjects=one.subjects,
        timeline=one.timeline,
        columns=one.columns,
    )
    with pytest.raises(PanelBatchError, match="expected the 'adj_factor' dataset"):
        compress_adjustment_batch(disguised)


def test_compressing_a_null_factor_is_refused(tmp_path: Path) -> None:
    """A `None` is legal in a `float` panel column and meaningless in a step function: it
    compares unequal to nothing, rides through as an unchanged row, and only fails on the way
    back out -- leaving a partition that passes readiness and explodes at parse."""
    (one,) = _year_batches((SESSIONS_2024[0],))
    factor_date, adj_factor = one.columns
    holed = type(one)(
        provider_id=one.provider_id,
        dataset=one.dataset,
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="success",
        subjects=one.subjects,
        timeline=one.timeline,
        columns=(factor_date, type(adj_factor)(adj_factor.name, adj_factor.kind, (None, 8.0204))),
    )
    with pytest.raises(PanelBatchError, match="adj_factor row 0 is null"):
        compress_adjustment_batch(holed)


def test_the_same_session_merged_twice_is_refused_rather_than_double_counted() -> None:
    """The ordinary operator mistake: a retry that appends the day's batch again. A step
    function with two entries for one session has no value on it."""
    (one,) = _year_batches((SESSIONS_2024[0],))
    with pytest.raises(AdjustmentError, match="must be ascending by observed_on"):
        compress_adjustment_batch(merge_panel_batches([one, one]))


def test_compression_is_idempotent() -> None:
    once = compress_adjustment_batch(merge_panel_batches(_year_batches(SESSIONS_2024)))
    twice = compress_adjustment_batch(once)
    assert once.to_rows() == twice.to_rows()


# --- the write guards -----------------------------------------------------------------


def test_writing_a_different_dataset_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (one,) = _year_batches((SESSIONS_2024[0],))
    disguised = type(one)(
        provider_id=one.provider_id,
        dataset="daily",
        kind=one.kind,
        as_of=one.as_of,
        fetched_at=one.fetched_at,
        status="success",
        subjects=one.subjects,
        timeline=one.timeline,
        columns=one.columns,
    )
    with pytest.raises(PanelBatchError, match="expected the 'adj_factor' dataset"):
        write_adjustment_factors(store, [disguised])


def test_a_write_that_straddles_two_years_is_refused(tmp_path: Path) -> None:
    """A year partition is a year. `panel_partition_year` refuses to pick one, which is what
    stops December's sessions from being filed under the following January's partition."""
    store = _store(tmp_path)
    with pytest.raises(PanelBatchError, match=r"spans \[2023, 2024\]"):
        write_adjustment_factors(store, _year_batches(SESSIONS_2023 + SESSIONS_2024))


def test_a_rewrite_that_would_drop_a_security_is_refused(tmp_path: Path) -> None:
    """A partition is replaced whole, so a year rewritten from a narrower cross section
    destroys the securities it omits and reports success."""
    store = _seeded(tmp_path, SESSIONS_2024)
    narrower = [
        TushareProvider(
            token="secret-token",
            transport=_Transport(_response([[PING_AN, day, ping_an]])),
            clock=lambda: FETCHED_AT,
        ).fetch_panel(
            ProviderRequest(
                dataset=ADJ_FACTOR_DATASET,
                as_of=datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 12, 0, tzinfo=UTC),
            )
        )
        for day, ping_an, _ in SESSIONS_2024
    ]
    with pytest.raises(PanelBatchError, match="writing it would drop"):
        write_adjustment_factors(store, narrower)


def test_rewriting_the_same_year_with_the_same_rows_is_accepted(tmp_path: Path) -> None:
    store = _seeded(tmp_path, SESSIONS_2024)
    reference = write_adjustment_factors(store, _year_batches(SESSIONS_2024))
    assert reference.row_count == 8
