"""Research memory contracts and deterministic in-memory implementation."""

from typing import Protocol

from openalpha_cn.domain.memory import MEMORY_ENTRY_VERSIONS, MemoryEntry

__all__ = [
    "MEMORY_ENTRY_VERSIONS",
    "InMemoryResearchMemory",
    "MemoryEntry",
    "ResearchMemory",
]


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
