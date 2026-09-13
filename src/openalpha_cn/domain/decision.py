"""Append-only decision record contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.run import RUN_MANIFEST_ID_PATTERN, VersionRef
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, IdentityRewriteRequiredError


class AgentDecision(BaseModel):
    """One agent's structured contribution to a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=128)
    signal_id: str = Field(min_length=1, max_length=128)
    recommendation: Literal["support", "oppose", "abstain"]
    rationale: str = Field(min_length=1, max_length=4000)


class DecisionLedger(BaseModel):
    """An immutable record intended to be appended to the decision ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["decision-ledger/v2"] = "decision-ledger/v2"
    run_id: str = Field(min_length=1, max_length=128)
    run_manifest_id: str = Field(pattern=RUN_MANIFEST_ID_PATTERN)
    """The content address of the run declaration this decision was produced under.

    `V2-P4-025`, and the field that makes roadmap section 9's finding false. That section
    measured `config_digest` and `random_seed` failing to reach `decision_id` -- not through
    an oversight in the hashing, but because neither is a field of this model and
    `RunManifest`, which owns both, had no address to borrow. Carrying the manifest's address
    here fixes it at the root: every declared run input reaches `decision_id` through one
    field, and an input added to `RunManifest` later reaches it without this contract changing
    again.

    The reference is the *address*, not the `run_id` this model already carries, and the
    difference is the whole point: `run_id` is caller-supplied and says which run this was,
    while `run_manifest_id` says what that run was made of. Two runs that differ only in
    configuration have always had different `run_id`s in practice -- `ResearchEngine` refuses
    to reuse one with a different request -- so `run_id` could never have distinguished them
    by content, only by name.

    Pattern-constrained to `stable_model_id`'s own output so this cannot be filled with a
    placeholder; see `RUN_MANIFEST_ID_PATTERN`.
    """
    created_at: datetime
    agent_outputs: tuple[AgentDecision, ...] = ()
    routing_path: tuple[str, ...] = ()
    risk_decision: Literal["pass", "reduce", "block"]
    final_action: Literal["watch", "avoid", "abstain"]
    evidence_ids: tuple[str, ...] = ()
    signal_ids: tuple[str, ...] = ()
    code_commit: str = Field(min_length=7, max_length=64)
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.final_action != "abstain" and (not self.evidence_ids or not self.signal_ids):
            raise ValueError("non-abstaining decision requires evidence_ids and signal_ids")
        output_signal_ids = {item.signal_id for item in self.agent_outputs}
        if not output_signal_ids.issubset(set(self.signal_ids)):
            raise ValueError("agent output signal_id must be listed in signal_ids")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def decision_id(self) -> str:
        """Return the stable content-derived decision identifier."""
        return stable_model_id(prefix="dec", model=self)


class DecisionLedgerV1(BaseModel):
    """The frozen `decision-ledger/v1` shape, kept so a stored v1 row can still be read.

    Read by `storage/migrations.py::rewrite_contract_identities`, which is the only thing that
    may advance one of these rows -- see `refuse_decision_ledger_v1_upgrade` below. It differs
    from `DecisionLedger` in exactly two places: the `schema_version` literal and the absence
    of `run_manifest_id`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["decision-ledger/v1"] = "decision-ledger/v1"
    run_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    agent_outputs: tuple[AgentDecision, ...] = ()
    routing_path: tuple[str, ...] = ()
    risk_decision: Literal["pass", "reduce", "block"]
    final_action: Literal["watch", "avoid", "abstain"]
    evidence_ids: tuple[str, ...] = ()
    signal_ids: tuple[str, ...] = ()
    code_commit: str = Field(min_length=7, max_length=64)
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


def refuse_decision_ledger_v1_upgrade(old: BaseModel) -> BaseModel:
    """Refuse to advance a v1 ledger at read time; the storage migration must do it.

    Two independent reasons, either of which is sufficient. The first is roadmap section 8's:
    `decisions.decision_id` is this model's own content address and is the table's primary
    key, referenced by `validation_results.decision_id`, `research_memory.decision_id`,
    `research_reports`' payload and the batch tables' payloads -- an upcast here recomputes it
    and updates none of them. The second is arithmetic: `run_manifest_id` is not derivable
    from this row at all. It lives in the `runs` table, which a row-level reader does not
    have and a migration does.
    """
    raise IdentityRewriteRequiredError(
        contract="decision-ledger", found_version=getattr(old, "schema_version", None)
    )


DECISION_LEDGER_VERSIONS: ContractVersions[DecisionLedger] = ContractVersions(
    name="decision-ledger",
    current_version="decision-ledger/v2",
    versions={"decision-ledger/v1": DecisionLedgerV1, "decision-ledger/v2": DecisionLedger},
    upgrades={"decision-ledger/v1": refuse_decision_ledger_v1_upgrade},
)
