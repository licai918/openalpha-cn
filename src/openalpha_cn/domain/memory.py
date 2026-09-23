"""A compact durable-memory candidate linked to a completed decision.

Split out of `runtime/memory.py` (V2-P0B-012) so `storage/memory.py` can persist
`MemoryEntry` without importing `openalpha_cn.runtime` at all, forbidden by the
`storage-no-upward-deps` import-linter contract. `MemoryEntry` was already a plain data
value with no dependency beyond `domain.versioning`, so this is a pure relocation.

`runtime/memory.py` re-exports `MemoryEntry`/`MEMORY_ENTRY_VERSIONS` unchanged alongside
the `ResearchMemory` Protocol and `InMemoryResearchMemory` implementation that stay behind
(both are runtime behavior, not needed by storage), so every existing
`from openalpha_cn.runtime.memory import MemoryEntry` keeps working.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.domain.versioning import ContractVersions, single_version


class MemoryEntry(BaseModel):
    """A compact durable-memory candidate linked to a completed decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    created_at: datetime
    decision_id: str = Field(min_length=1, max_length=128)
    signal_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)


MEMORY_ENTRY_VERSIONS: ContractVersions[MemoryEntry] = single_version("memory-entry", MemoryEntry)
