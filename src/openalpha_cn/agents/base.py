"""Stable contracts for research agents."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.run import AgentProvenance

__all__ = [
    "AgentContext",
    "AgentProvenance",
    "AgentResult",
    "FeaturePlane",
    "ResearchAgent",
]


@runtime_checkable
class FeaturePlane(Protocol):
    """The panel-plane handle an agent reads its declared feature columns through (`V2-P4-009`).

    ## A Protocol beside the consumer, which is this repository's shape for exactly this

    `shortlist_view.ShortlistDocumentStore` and `factor_view.ExperimentDocumentStore` are the
    two precedents and they are precedents for the same reason: the consumer names the methods
    it calls, the producer satisfies them structurally, and neither package imports the other.
    Here the producer is `V2-P4-012`'s feature matrix, whose
    `domain/alpha_model.py::FeatureCrossSection` already declares `feature_ids` and
    `value(ts_code=..., feature_id=...)` and therefore satisfies this with no adapter and no
    edit. What that buys is the whole point of declaring it here rather than importing that
    class: `agents/` gains no edge into `openalpha_cn.feature_matrix`, which reaches
    `panel_factors`, `panel_neutralization` and `panel_ingest` and through them DuckDB. The
    agent plane stays off the panel plane; the composition root hands it a finished cross
    section.

    ## Why this is not `tools/base.py::ResearchTool`, measured rather than asserted

    `V2-P4-009`'s row proposes reusing the declared-but-unused `ResearchTool` as this handle,
    and it does not fit. Two measurements, both in
    `tests/unit/agents/test_feature_plane_seam.py`:

    1. **`ToolRequest.kind` is `max_length=64` and a real `feature_id` is longer.** The
       grammar `feature_matrix.py` ships is `<factor>@<tier>[:<transform>][:<neutralization>]`;
       this build's longest registered factor key is `deducted_earnings_yield_ttm/v1`, and the
       neutralized spelling of that column is **89 characters**. `ToolRequest` refuses it with
       a `ValidationError`. Not one column of the neutralized tier can be *asked for* through
       that request type.
    2. **`ToolResult` has exactly three fields -- `status`, `evidence_ids`, `no_data_reason` --
       and `extra="forbid"`.** None of them can carry a number, so a feature *value* cannot
       cross that seam at all; and `status="success"` requires a non-empty `evidence_ids`, so
       a read that produced a number and no evidence id could only be reported as `no_data`.

    `ResearchTool` stays exactly what it is -- an evidence lookup contract, satisfied by
    `tools/evidence.py::EvidenceLookupTool` -- and this is a second, narrower seam beside it
    rather than a widening of it. Widening `kind` and adding a numeric field would have made
    one Protocol answer two unrelated questions, and the `status`/`evidence_ids` rule would
    have had to be relaxed to let a valued read through.

    ## Why `AgentContext` carries the handle and not the rows

    Implementation Decision 31 forbids per-row pydantic rebuilds on a panel query path, and a
    whole-market cross section is ~5,500 rows. `FeatureCrossSection` is a frozen dataclass for
    that reason, and pydantic revalidates a stdlib dataclass handed to a model field -- so
    annotating the field with the concrete class would rebuild the market on every
    `AgentContext` construction. A `runtime_checkable` Protocol under
    `arbitrary_types_allowed` is an `isinstance` check on method presence instead, and
    `test_the_context_holds_the_same_cross_section_object_it_was_given` asserts the identity
    that proves nothing was rebuilt.
    """

    @property
    def feature_ids(self) -> tuple[str, ...]:
        """Every column this plane carries, which is what routing is decided against."""

    def value(self, *, ts_code: str, feature_id: str) -> float | None:
        """One cell, or `None` when the column carries no number for that security."""


class AgentContext(BaseModel):
    """Point-in-time evidence and run context supplied to an agent."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]
    features: FeaturePlane | None = None
    """The panel-plane columns this cycle was composed with, or `None` (`V2-P4-009`).

    `None` and not an empty plane, because the two are different facts and a caller can tell
    them apart: `None` is "this deployment composed no feature plane", and a plane whose
    `feature_ids` do not include what an agent declared is "this plane was built and does not
    carry that column". `FeatureCrossSection` cannot even express the empty case -- it refuses
    a `rows` of length zero by name -- so collapsing them would have meant inventing a null
    object that lies about being a cross section.

    Optional with a default so that every agent written before this row keeps working: the
    three deterministic baselines and `StructuredSignalAgent` read `context.evidence` and never
    look here, and `AgentRouter` guarantees a feature-dependent agent is only ever handed a
    context whose plane carries every column it declared.
    """


class ResearchAgent(Protocol):
    """Extension contract for deterministic or model-backed agents."""

    agent_id: str
    evidence_families: frozenset[str]
    feature_dependencies: frozenset[str]
    """Which panel-plane columns this agent reads, by `feature_id` (`V2-P4-008`, S38).

    Required rather than optional, for `provenance`'s stated reason one field down: a
    declaration the contract lets an agent omit is a declaration the router has to guess at,
    and the guess it would have to make -- "no features" -- is indistinguishable from the
    misdeclaration `UndeclaredAgentDependencyError` exists to name. An agent that reads no
    panel column declares `frozenset()` and says so; the three baselines do exactly that.

    The empty case is not the same as the empty `evidence_families` case, and the router is
    where the asymmetry lives: an agent declaring **neither** is refused, because nothing can
    satisfy it. See `runtime/router.py` for the two quantifiers and why they differ.
    """
    provenance: AgentProvenance
    """What this agent is, for the run manifest (`V2-P4-010`, S40).

    Required rather than optional, and declared by the agent rather than inferred from its
    type. `ResearchEngine` could distinguish this repository's own `StructuredSignalAgent`
    from its own `MarketAgent` with an `isinstance` check, and would then record every *other*
    `ModelProvider`-backed agent as deterministic -- a silent wrong answer about the one fact
    S40 asks a manifest to carry, arrived at by a mechanism that looks like it works. An agent
    that omits this fails structurally at the point it is handed to the engine instead.
    """

    def analyze(self, context: AgentContext) -> AgentResult:
        """Return one schema-validated research result."""
