"""A partition built from a paged fetch is the partition a one-shot fetch would have built.

`V2-P1-018`'s R5. `stk_limit`'s cross section is 66 rows under its measured 7,800-row cap and
growing +2.231 rows per session, so within about thirty sessions every one-shot fetch of it is
refused -- correctly, because a response at the cap cannot be told from one the cap truncated.
The escape hatch is `limit`/`offset` paging inside `TushareProvider`, and the question it has
to answer before it can be trusted is not "does it return the rows" but **"does the partition
change"**.

Two hashes answer that, they cover different things, and this repository keeps both
(`panel_ingest.py`'s module docstring says why):

- `ColumnarPanelBatch.content_digest`, the provider-side one, over the batch header, every
  column in declared order and the `source_uri`.
- `panel/store.py::_content_hash`, surfaced as `PartitionRef.content_hash`, over the dataset,
  the year, the column names and SQL types, and the rows. It is what makes re-writing identical
  content a no-op, so a fallback that changed it would make every paged re-fetch look like new
  data to the catalog.

Neither can see how many HTTP requests produced the rows. Both can see the row *order*, and
that is the property the fallback rests on: measured on the live endpoint on 2026-08-10,
`limit=4000 offset=0` followed by `limit=4000 offset=4000` returns a 7,733-element sequence
element-for-element identical to the un-paged 7,733-row response for 2026-08-07. This module
asserts the consequence through a real DuckDB catalog and real Parquet files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_panel_batch
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider

FETCHED_AT = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-08-07: that session's 16:30 availability has passed."""

LIMIT_FIELDS = ["trade_date", "ts_code", "up_limit", "down_limit"]

NEAR_CAP = 7700
"""A realistic cross section: `stk_limit` served 7,733 rows on 2026-08-07 and 7,734 on 08-10.

The row count is held **fixed** across both fetches in this module and only the number of
requests is varied, because that is the whole claim -- same rows, different number of round
trips, same partition. What varies instead is the *endpoint's* cap: at 10,000 this cross
section arrives in one response, and at 7,600 the same rows arrive as a truncated response the
guard refuses plus two pages. Holding the rows and moving the cap is the only arrangement that
can produce both batches at all; in the world this fallback is actually for -- a cross section
past the descriptor's own 7,800 -- the one-shot batch does not exist to compare against.
"""

ENDPOINT_CAP_THAT_TRUNCATES = 7600


class PagingTransport:
    """Serves one cross section, honouring `limit`/`offset` and truncating at `cap`.

    The measured endpoint behaviour: an un-`limit`ed request gets the cap-truncated prefix with
    `has_more=True`, and a `limit`ed one gets exactly that slice with `has_more` saying whether
    anything follows.
    """

    def __init__(self, rows: list[list[Any]], *, cap: int) -> None:
        self.rows = rows
        self.cap = cap
        self.posts = 0

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.posts += 1
        params = payload["params"]
        offset = int(params.get("offset", 0))
        limit = int(params["limit"]) if "limit" in params else self.cap
        window = self.rows[offset : offset + limit]
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": LIMIT_FIELDS,
                "items": window,
                "has_more": offset + len(window) < len(self.rows),
            },
        }


def _bands(count: int) -> list[list[Any]]:
    return [
        ["20260807", f"{600000 + index:06d}.SH", 12.03 + index / 100.0, 9.85]
        for index in range(count)
    ]


def _fetch(rows: list[list[Any]], *, cap: int) -> tuple[Any, PagingTransport]:
    transport = PagingTransport(rows, cap=cap)
    provider = TushareProvider(token="secret-token", transport=transport, clock=lambda: FETCHED_AT)
    batch = provider.fetch_panel(ProviderRequest(dataset=PRICE_LIMIT_DATASET, as_of=FETCHED_AT))
    return batch, transport


def test_the_paged_partition_and_the_one_shot_partition_hash_identically(
    tmp_path: Path,
) -> None:
    """Both hashes, through real storage, over one fixed 7,700-row cross section.

    The comparison is against a fetch of the *same rows* rather than against a recorded
    constant, because the claim is "paging changes nothing" -- and only a same-rows comparison
    can say that. `fetched_at` is pinned by the injected clock so the one header field that
    legitimately differs between any two fetches cannot be what makes them agree or disagree
    here.
    """
    rows = _bands(NEAR_CAP)
    unpaged, one_shot_transport = _fetch(rows, cap=10_000)
    paged, paging_transport = _fetch(rows, cap=ENDPOINT_CAP_THAT_TRUNCATES)

    # The premise, asserted rather than assumed: without it this compares two paged fetches
    # with each other and proves nothing. (It did, in the first version of this test.)
    assert one_shot_transport.posts == 1
    assert paging_transport.posts == 3  # the refused one-shot request, then two pages
    assert len(paged.subjects) == len(unpaged.subjects) == NEAR_CAP

    assert paged.content_digest == unpaged.content_digest

    paged_ref = write_panel_batch(PanelStore(tmp_path / "paged"), paged, year=2026)
    unpaged_ref = write_panel_batch(PanelStore(tmp_path / "unpaged"), unpaged, year=2026)

    assert paged_ref.content_hash == unpaged_ref.content_hash
    assert paged_ref.row_count == unpaged_ref.row_count == NEAR_CAP


def test_a_paged_partition_is_still_idempotent_against_a_stored_one_shot_partition(
    tmp_path: Path,
) -> None:
    """The consequence that matters operationally rather than cryptographically.

    `PanelStore.write_partition` is content-hash idempotent: re-writing identical content is a
    no-op. So if the fallback changed the hash, then the first re-fetch after the market crossed
    the cap would rewrite every partition it touched and stamp a new `recorded_at` on coverage
    that had not changed -- a whole year of the panel looking like new data because the market
    grew by one listing.
    """
    rows = _bands(NEAR_CAP)
    store = PanelStore(tmp_path / "panel")
    unpaged, _ = _fetch(rows, cap=10_000)
    paged, _ = _fetch(rows, cap=ENDPOINT_CAP_THAT_TRUNCATES)

    first = write_panel_batch(store, unpaged, year=2026)
    second = write_panel_batch(store, paged, year=2026)

    assert first.content_hash == second.content_hash
