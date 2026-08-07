"""Recovery-store contract: durable node-level resumption state for one research run.

Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
consumer side (`runtime/`), not in `storage/`. `ResearchEngine` (`runtime/engine.py`) is
this Protocol's primary consumer, using both `get` and `save`; `OpenAlphaSDK.get_recovery`
and the `/api/v1/research/recovery/{run_id}` route also only ever call `get`. Neither
consumer calls `SQLiteRecoveryStore.clear`, so it is deliberately not part of this
Protocol.

`RunRecoveryState` itself stays defined in `storage/recovery.py` (out of this task's
scope to relocate) -- importing it here is a legal downward `runtime -> storage`
dependency for a plain, infra-free pydantic data model, not a re-introduction of the
concrete `SQLiteRecoveryStore` coupling this Protocol replaces.
"""

from typing import Protocol

from openalpha_cn.storage.recovery import RunRecoveryState


class RecoveryStore(Protocol):
    """Extension contract for durable per-run recovery state consumed by ResearchEngine."""

    def get(self, run_id: str) -> RunRecoveryState | None:
        """Load the latest recovery state for a run."""

    def save(self, state: RunRecoveryState) -> None:
        """Atomically insert or advance a compatible recovery state."""
