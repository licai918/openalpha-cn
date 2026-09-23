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

from datetime import datetime
from typing import Protocol

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.storage.recovery import RunRecoveryState


class RecoveryStore(Protocol):
    """Extension contract for durable per-run recovery state consumed by ResearchEngine."""

    def get(self, run_id: str) -> RunRecoveryState | None:
        """Load the latest recovery state for a run."""

    def save(self, state: RunRecoveryState) -> None:
        """Atomically insert or advance a compatible recovery state."""

    def append_result(
        self,
        run_id: str,
        *,
        position: int,
        result: AgentResult,
        updated_at: datetime,
    ) -> None:
        """Record one completed agent without rewriting the run's earlier results.

        `V2-P4-020` added this, and the reason it is a third method rather than a faster
        `save()` is that `save()`'s argument is the shape of the problem: it takes a whole
        `RunRecoveryState`, so any implementation of it is handed every result the run has
        completed and can only choose how much of that to write. Measured at `be262ea`, the
        engine calling `save()` once per agent cost `N(N+1)/2` result serialisations -- 78 for
        the 12 shipped agents, 80,200 and 46.68 MB for 400.

        This signature carries one result and the position the graph declares it at, so an
        implementation can be O(1) in the run's length, and so that a result written against
        the wrong position is a refusal rather than a stored state that stops validating. It
        is the `SQLiteBatchTaskStore.update_item` shape, arrived at from the same measurement
        one plane over.
        """
