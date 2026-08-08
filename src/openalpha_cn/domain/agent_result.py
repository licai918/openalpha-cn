"""One agent's validated signal and rationale.

Split out of `agents/base.py` (V2-P0B-012) so `storage/recovery.py` -- which persists
`RunRecoveryState.completed_results: tuple[AgentResult, ...]` -- can depend on it without
importing `openalpha_cn.agents` at all, forbidden by the `storage-no-upward-deps`
import-linter contract. `AgentResult` was already a plain data value (no behavior, no
infrastructure dependency; its only field type, `SignalFrame`, already lived in `domain`),
so this is a pure relocation, not a redesign.

`agents/base.py` re-exports `AgentResult` unchanged alongside the `AgentContext` model and
`ResearchAgent` Protocol that stay behind (both are agent-specific, not needed by storage),
so every existing `from openalpha_cn.agents.base import AgentResult` keeps working.
"""

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.domain.signal import SignalFrame


class AgentResult(BaseModel):
    """One agent's validated signal and rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=128)
    signal: SignalFrame
    rationale: str = Field(min_length=1, max_length=4000)
