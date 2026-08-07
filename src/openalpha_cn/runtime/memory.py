"""Research memory contracts and deterministic in-memory implementation."""

from datetime import datetime
from typing import Protocol

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


class ResearchMemory(Protocol):
    """Extension contract for research memory stores."""

    def append(self, entry: MemoryEntry) -> None:
        """Append an entry idempotently by decision ID."""

    def list(self, *, subject: str) -> tuple[MemoryEntry, ...]:
        """Return subject memory in append order."""


class InMemoryResearchMemory:
    """Process-local memory used by tests and local baseline runs."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def append(self, entry: MemoryEntry) -> None:
        """Append once for each immutable decision."""
        if any(item.decision_id == entry.decision_id for item in self._entries):
            return
        self._entries.append(entry)

    def list(self, *, subject: str) -> tuple[MemoryEntry, ...]:
        """Return entries for one subject."""
        return tuple(item for item in self._entries if item.subject == subject)
