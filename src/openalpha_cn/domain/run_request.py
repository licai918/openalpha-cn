"""All deterministic inputs to one research cycle.

Split out of `runtime/contracts.py` as a follow-up to a Critical-review finding on
V2-P0B-012. That task closed the last `storage-no-upward-deps` baseline entry by adding
`allow_indirect_imports = true` to the contract instead of removing the two-hop chain it
was covering for (`storage.batch -> batch_contracts -> runtime.contracts`, needed for this
class). That relaxation weakened the contract from import-linter's default full
transitive-reachability check to a direct-edges-only check, and a probe proved the gap was
real: a neutral top-level module importing a behavioural `product` class, imported in turn
from a module under `storage/`, passed the relaxed contract while `grimp`'s full
reachability check saw the chain plainly (see
`tests/unit/test_import_layering.py::test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module`,
which reproduces and removes that exact probe).

The reasoning that produced the relaxation conflated two different moves: "move the whole
`runtime/contracts.py` module into `domain`" (which *would* drag `agents.base` in, via
`ResearchRunResult.agent_results: tuple[AgentResult, ...]`, directly violating
`domain-purity`) with "move just `ResearchRunRequest`" (which does not: this class's own
field types are `EvidenceSnapshot` and `ensure_aware`, both already `domain.*`, and it has
never depended on `AgentResult` -- only `ResearchRunResult`, a separate class that stays
behind in `runtime/contracts.py`, does). Moving `ResearchRunRequest` alone removes the
two-hop chain `openalpha_cn.batch_contracts` needed `runtime.contracts` for, entirely:
`batch_contracts.py` now imports it from here directly, and `runtime/contracts.py`
re-exports it unchanged so every existing `from openalpha_cn.runtime.contracts import
ResearchRunRequest` keeps working (same class object -- see the identity assertion in
`tests/unit/test_storage_contract_relocation.py`).
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.evidence import EvidenceSnapshot, LookAheadViolationError
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.time import ensure_aware


class ResearchRunRequest(BaseModel):
    """All deterministic inputs to one research cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    mode: RunMode
    """Which cycle this request asks for; declared once in `domain/run_mode.py`.

    This is the second of the three places `V2-P4-003` found the mode set written out --
    it moved here from `runtime/engine.py` with the class -- and it names the shared enum
    now rather than repeating it. `RunManifest.mode` is the first and `cli.py`'s `--mode`
    option the third; all three read the same declaration, so `paper` and `daily` arrived
    at all three at once.
    """
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if any(item.subject != self.subject for item in self.evidence):
            raise ValueError("all evidence must match the requested subject")
        if any(not item.visible_at(self.as_of) for item in self.evidence):
            # V2-P0B-014: typed, not a bare ValueError -- see LookAheadViolationError's
            # docstring (domain/evidence.py) for why the replay runner needs this to be a
            # type it can catch instead of a message it has to parse.
            raise LookAheadViolationError("evidence is not visible at request as_of")
        return self
