"""Run repository contract: durable storage for immutable run manifests and decisions.

Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
consumer side (`runtime/`), not in `storage/`, so a consumer can be typed against it
without importing `openalpha_cn.storage` at all. `ResearchEngine` (`runtime/engine.py`)
is this Protocol's only consumer; its method set is exactly the four methods
`ResearchEngine` calls on `self.repository` -- not `SQLiteRunRepository`'s full public
surface, which also includes `journal_mode`, `get_decision`, `append_checkpoint`, and
`list_checkpoints` for callers `ResearchEngine` does not have.
"""

from typing import Protocol

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest


class RunRepository(Protocol):
    """Extension contract for durable run/decision storage consumed by ResearchEngine."""

    def append_run(self, manifest: RunManifest) -> None:
        """Append a run manifest without replacing an existing run."""

    def get_run(self, run_id: str) -> RunManifest | None:
        """Load a run manifest by ID."""

    def append_decision(self, decision: DecisionLedger) -> None:
        """Append a decision linked to an existing run."""

    def get_decision_for_run(self, run_id: str) -> DecisionLedger | None:
        """Load the single immutable decision associated with a run."""
