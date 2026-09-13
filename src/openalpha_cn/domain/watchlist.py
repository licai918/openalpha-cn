"""A user-observed subject with tags and a note.

Split out of `product/research.py` (V2-P0B-012) so `storage/product.py` can persist
`WatchlistEntry` without importing `openalpha_cn.product` at all, forbidden by the
`storage-no-upward-deps` import-linter contract. `WatchlistEntry` was already a plain data
value (no dependency on `ResearchRunResult` or any other product-layer behavior), so this
is a pure relocation.

`product/research.py` re-exports `WatchlistEntry`/`WATCHLIST_ENTRY_VERSIONS` unchanged
alongside the `WatchlistStore` Protocol that stays behind (product-layer behavior, not
needed by storage), so every existing
`from openalpha_cn.product.research import WatchlistEntry` keeps working.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, single_version


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    tags: tuple[str, ...] = ()
    note: str = Field(default="", max_length=2000)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)


WATCHLIST_ENTRY_VERSIONS: ContractVersions[WatchlistEntry] = single_version(
    "watchlist-entry", WatchlistEntry
)
