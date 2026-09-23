"""The durable-watchlist extension contract (`S51`).

Split out of `product/research.py` by `V2-P4-006`, which found three unrelated
responsibilities in one file: the screen (now `product/screening.py`), this, and the report
contract (`product/reporting.py`). `product/research.py` re-exports every name below
unchanged, so `from openalpha_cn.product.research import WatchlistStore` keeps working and no
caller moved.

`WatchlistEntry` and `WATCHLIST_ENTRY_VERSIONS` live in `domain/watchlist.py` (V2-P0B-012, so
`storage/product.py` can persist them without importing upward) and are re-exported here for
the same reason `product/research.py` re-exported them before: the Protocol and the shape it
stores read as one contract at one import.
"""

from __future__ import annotations

from typing import Protocol

from openalpha_cn.domain.watchlist import WATCHLIST_ENTRY_VERSIONS, WatchlistEntry

__all__ = ["WATCHLIST_ENTRY_VERSIONS", "WatchlistEntry", "WatchlistStore"]


class WatchlistStore(Protocol):
    """Extension contract for durable watchlist storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives in the
    product layer (`product/`), not in `storage/`. (`WatchlistEntry` itself moved to
    `domain.watchlist` in V2-P0B-012, re-exported here unchanged -- see that module's
    docstring -- but this Protocol, being behavior rather than a stored data shape, stayed
    put.) `SQLiteWatchlistStore`'s full public surface is exactly `put`/`list`/`remove`, so
    this Protocol declares all three -- unlike the other storage Protocols in this task,
    there was no wider surface to narrow.
    """

    def put(self, entry: WatchlistEntry) -> None:
        """Create or intentionally update one local watchlist entry."""

    def list(self) -> tuple[WatchlistEntry, ...]:
        """List the local observation pool."""

    def remove(self, subject: str) -> bool:
        """Remove one watchlist entry; return whether it existed."""
