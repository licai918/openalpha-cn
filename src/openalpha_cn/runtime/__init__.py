"""Research routing, memory, recovery, and shared execution."""

from typing import Any

from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult, RunConflictError
from openalpha_cn.runtime.memory import InMemoryResearchMemory, MemoryEntry, ResearchMemory
from openalpha_cn.runtime.router import AgentRouter

__all__ = [
    "AgentRouter",
    "InMemoryResearchMemory",
    "MemoryEntry",
    "ResearchEngine",
    "ResearchMemory",
    "ResearchRunRequest",
    "ResearchRunResult",
    "RunConflictError",
]


def __getattr__(name: str) -> Any:
    """Resolve `ResearchEngine` lazily.

    Python always fully executes a package's `__init__.py` before importing any of its
    submodules. An eager top-level `from openalpha_cn.runtime.engine import ResearchEngine`
    here would force-load `runtime.engine`'s SQLite storage dependency merely by importing
    `openalpha_cn.runtime.contracts` (or any other lightweight submodule under this
    package) -- silently reintroducing, via this file, the exact coupling V2-P0B-001 exists
    to remove. Resolving it on first access keeps `ResearchEngine` importable from
    `openalpha_cn.runtime` (the exposed symbol set is unchanged) without paying that cost
    unless something actually uses it.
    """
    if name == "ResearchEngine":
        from openalpha_cn.runtime.engine import ResearchEngine

        return ResearchEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
