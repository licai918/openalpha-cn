"""Deterministic research-cycle contracts: request/result models and the conflict error.

Split out of `runtime/engine.py` (V2-P0B-001) so that modules which only need
`ResearchRunRequest`/`ResearchRunResult`/`RunConflictError` are not forced to transitively
depend on `ResearchEngine`'s SQLite storage (`storage.recovery`, `storage.sqlite`). This
module must stay free of any import of `runtime.engine` or `openalpha_cn.storage`.

`AgentResult` is imported from `domain.agent_result`, not `agents.base`, since V2-P0B-012:
`agents.base` re-exports the identical object (see that module and
`domain/agent_result.py`'s docstring), but importing it from `agents.base` here would mean
that any module reaching this file transitively pulled in the whole `agents`/`models`
subsystem (`agents/__init__.py` eagerly imports `agents.baseline`/`agents.model`, which
import `models.base`/`models/__init__.py` in turn) just to obtain one plain data type.
Routing through `domain.agent_result` instead removes that edge entirely with no change in
behavior (same class object either way).

`ResearchRunRequest` itself now lives in `openalpha_cn.domain.run_request` (a Critical-review
follow-up on V2-P0B-012 -- see that module's docstring for why moving just this one class,
not the whole module, removes `openalpha_cn.batch_contracts`'s only edge into
`openalpha_cn.runtime`) and is re-exported here unchanged, so every existing
`from openalpha_cn.runtime.contracts import ResearchRunRequest` keeps working (same class
object -- see the identity assertion in `tests/unit/test_storage_contract_relocation.py`).
"""

from pydantic import BaseModel, ConfigDict

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.signal import SignalFrame

__all__ = ["ResearchRunRequest", "ResearchRunResult", "RunConflictError"]


class RunConflictError(RuntimeError):
    """Raised when a run ID is reused with different immutable inputs."""


class ResearchRunResult(BaseModel):
    """Signal, decision, manifest, and agent outputs from one cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalFrame
    decision: DecisionLedger
    manifest: RunManifest
    agent_results: tuple[AgentResult, ...]
