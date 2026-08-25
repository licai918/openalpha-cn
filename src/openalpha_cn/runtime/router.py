"""Deterministic agent routing over the two halves of an agent's declaration."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from openalpha_cn.agents.base import FeaturePlane, ResearchAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot


@dataclass(frozen=True, slots=True)
class RoutingLimitation:
    """One named boundary on what feature-aware routing can be trusted to mean."""

    code: str
    detail: str


KNOWN_ROUTING_LIMITATIONS: Final[tuple[RoutingLimitation, ...]] = (
    RoutingLimitation(
        code="nothing_on_a_command_line_or_a_rest_body_composes_a_feature_plane",
        detail=(
            "KNOWN_ROUTING_COMPOSITION: the panel-plane handle reaches a cycle through "
            "`OpenAlphaSDK(features=...)` and `ResearchEngine(features=...)`, and through "
            "nothing else. `openalpha research run` builds an SDK with no plane and "
            "`POST /api/v1/research/run` goes through `create_app`'s composition root, which "
            "does the same -- so on those two faces a feature-dependent agent is declared, "
            "never routed, and the cycle abstains with "
            "`No supported point-in-time evidence was available.` That is not an oversight "
            "left unstated: `ResearchRunRequest` is `extra='forbid'` and is digested whole "
            "into `RunRecoveryState.request_digest`, so a field naming a column cannot be "
            "added to it inside this row -- AGENTS.md rule 3 confines breaking contract "
            "changes to the closed `V2-P4-001` window. The reachable face today is the SDK, "
            "and `tests/integration/test_feature_dependent_routing.py` is where it is driven."
        ),
    ),
    RoutingLimitation(
        code="a_face_that_named_a_column_would_need_three_declarations_a_request_does_not_carry",
        detail=(
            "KNOWN_ROUTING_REQUEST: this is the same gap "
            "`shortlist_view.KNOWN_SHORTLIST_VIEW_LIMITATIONS"
            ".a_neutralized_tier_screen_needs_exposures_this_face_does_not_load` measured one "
            "plane over, and it is recorded here **because this row deliberately did not "
            "widen anything towards it**. A `FeaturePlane` is composed by the caller, already "
            "resolved; nothing added here loads a cross section, so the membership years to "
            "read, the trading calendar for the pricing session, and the neutralisation whose "
            "`industry_level` and `market_cap_measure` decide what the numbers ARE are all "
            "settled before this seam. A later face that let a caller *name* a column -- an "
            "`openalpha research run --feature <feature_id>` -- would have to carry all three, "
            "and a `feature_id` on the neutralized tier names the third one in its own text "
            "while saying nothing about the first two. Composing the plane outside the request "
            "is what keeps that gap out of this contract."
        ),
    ),
    RoutingLimitation(
        code="a_feature_only_agent_cites_a_feature_id_that_no_evidence_store_resolves",
        detail=(
            "KNOWN_ROUTING_CITATION: `SignalFrame.validate_conclusion` refuses every direction "
            "except `abstain` when `evidence_ids` is empty, so an agent that reads only the "
            "panel plane has to cite something to conclude anything, and what it has is the "
            "`feature_id` it read. `agents/feature.py::FeatureScoreAgent` cites exactly that. "
            "The two namespaces cannot collide -- `EvidenceSnapshot.evidence_id` is `ev_` plus "
            "24 hex characters and a `feature_id` carries `/` and `@` -- and no product face "
            "resolves `evidence_ids` against the evidence store, so nothing is misled today. "
            "What is true and unwelcome is that `DecisionLedger.evidence_ids` now holds two "
            "kinds of token, and a reader who takes every member to be a row in "
            "`ParquetEvidenceStore` is wrong about one of them."
        ),
    ),
    RoutingLimitation(
        code="the_declared_research_tool_seam_cannot_carry_a_feature_id_or_a_number",
        detail=(
            "KNOWN_ROUTING_TOOL: `V2-P4-009`'s row proposes reusing `tools/base.py:54-62 "
            "ResearchTool` -- declared, satisfied only by `EvidenceLookupTool`, imported by no "
            "module under `src/` -- as the feature handle, and it was measured rather than "
            "adopted. `ToolRequest.kind` is `max_length=64`; the neutralized spelling of this "
            "build's longest registered factor key is "
            "`deducted_earnings_yield_ttm/v1@neutralized:cross_section_standard/v1:"
            "industry_and_size/v1`, **89 characters**, and `ToolRequest` refuses it with a "
            "`ValidationError`. `ToolResult` has exactly three fields -- `status`, "
            "`evidence_ids`, `no_data_reason` -- under `extra='forbid'`, so no field can carry "
            "a number, and `status='success'` requires a non-empty `evidence_ids`, so a read "
            "that produced a value and no evidence id could only be reported as `no_data`. "
            "`FeaturePlane` is therefore a second Protocol beside `ResearchTool` and not a "
            "widening of it. `ResearchTool` still has no importer in `src/`."
        ),
    ),
    RoutingLimitation(
        code="routing_reads_the_column_list_and_never_a_cell",
        detail=(
            "KNOWN_ROUTING_CELL: an agent is routed when every `feature_id` it declared is in "
            "`FeaturePlane.feature_ids`, and the cell for this run's subject is not consulted. "
            "So a routed agent can meet a `None`, or a plane built for a universe this subject "
            "is not in, and both end in an abstention rather than a score. That is the "
            "deliberate half. The undeliberate half is the cost: a run against a subject the "
            "factor build never valued still pays for the agent to be constructed, routed, "
            "written into `RunRecoveryState.agent_ids` and recorded in the manifest, and its "
            "abstention is then averaged in by `_aggregate`. The alternative -- routing on the "
            "cell -- was rejected because it reintroduces `V2-P4-008`'s own defect one level "
            "down: an agent dropped for an empty cell leaves no `routing_path` entry and no "
            'abstention, so "the column had no number here" and "nobody had anything to '
            'say" become one observation again.'
        ),
    ),
    RoutingLimitation(
        code="an_abstention_is_averaged_into_the_run_strength_as_a_zero",
        detail=(
            "KNOWN_ROUTING_DILUTION: `ResearchEngine._aggregate` divides the sum of strengths "
            "by `len(results)`, and an abstaining agent contributes `0.0` to both the "
            "numerator's strength and the confidence. So one abstention beside one bullish "
            "signal halves the run's strength, which reads 'the committee was split' when what "
            "happened is 'one member had nothing to read'. This predates `V2-P4-008` -- it is "
            "reachable today for any mixed roster -- and it was **not** repaired here, because "
            "the repair changes the number every existing mixed run produced and therefore "
            "moves `signal_id` and `decision_id`. What `V2-P4-008` did change is the case "
            "where *every* result abstains: that used to raise `ValidationError` out of "
            "`run_cycle` and now returns an abstention naming which of the two reasons applied."
        ),
    ),
    RoutingLimitation(
        code="a_clamped_score_cannot_separate_a_strong_reading_from_an_extreme_one",
        detail=(
            "KNOWN_ROUTING_CLAMP: `SignalFrame.strength` is `ge=-1, le=1` and a feature value "
            "is not bounded at all -- a raw column carries whatever the factor measures, in "
            "whatever units. `FeatureScoreAgent` clamps, so a standardised reading of `+1.2` "
            "and one of `+14.0` both reach the ledger as `1.0` and the outlier is invisible "
            "downstream. A squashing function would have kept the order at the cost of "
            "inventing a scale nothing in this repository has measured, which is the trade "
            "`backtest/alpha_baseline.py` exists to take properly. Read `rationale`, which "
            "records the value that was read before it was clamped."
        ),
    ),
)
"""What feature-aware routing does not promise (`V2-P4-008`, `V2-P4-009`).

The thirty-third registry. `tests/unit/runtime/test_agent_routing.py` and
`tests/unit/agents/test_feature_plane_seam.py` name every code below in executable test code,
which is the binding `tests/unit/test_known_limitation_registries.py` installs on all of them.
"""

ROUTING_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_ROUTING_LIMITATIONS
)


class UndeclaredAgentDependencyError(ValueError):
    """An agent was handed to the router declaring neither an evidence family nor a column.

    `V2-P4-008`. Before this row the router was `agent.evidence_families & families`, so such
    an agent produced an empty intersection, was dropped from the returned tuple, and left
    nothing behind: no entry in `DecisionLedger.routing_path`, no `AgentVersion` in the
    manifest, no abstention in the ledger. "This agent had nothing to say about this run" and
    "this agent can never say anything about any run" were one observation, and the second is
    a misdeclaration a deployment wants to hear about at the point it is wired up.

    Refusing rather than routing-always, because the fail-open answer is worse than it looks:
    `SignalFrame.validate_conclusion` refuses every direction except `abstain` when
    `evidence_ids` is empty, so an agent with no declared inputs has no reachable output other
    than an abstention -- and `ResearchEngine._aggregate` averages abstentions into the run's
    strength and confidence alongside real signals. So routing one would move the aggregate
    towards zero on the strength of an agent that was never given anything to read.
    """


class AgentRouter:
    """Select the agents whose whole declaration this run can satisfy.

    ## The two quantifiers, which are deliberately not the same

    An **evidence family** is satisfied when *any* declared family is present. That is what the
    shipped agents are built for: `ThemeAgent` declares `{"theme", "catalyst", "disclosure"}`
    and `analyze` filters `_family(item) in self.evidence_families`, so a run carrying only
    catalysts gives it a smaller sample rather than a hole, and the `& families` rule this
    router has always used is the rule that matches.

    A **feature dependency** is satisfied only when *every* declared column is on the plane. An
    agent's arithmetic names a column by its `feature_id`; a column that is not there is not a
    smaller sample, it is a missing term, and an agent routed without it would either raise
    inside `analyze` (`FeatureCrossSection.value` refuses an unknown `feature_id` by name) or
    silently compute an answer without a factor it declared it needed.

    An agent declaring both halves needs both, which is the fail-closed reading and the one
    worth stating: routing a hybrid agent because its evidence arrived would hand it a plane
    with no column it named.

    ## What routing reads off the plane, and what it deliberately does not

    Column presence, never a cell. `FeatureRow.values` carries `None` for a security a column
    has no number for, and the tempting rule -- route only when *this subject's* cell is
    populated -- reintroduces this row's own defect one level down. An agent dropped for an
    empty cell leaves no abstention, no `routing_path` entry and no manifest row, so "the
    column had no number for this security" and "no agent had anything to say" collapse into
    one observation again. Routed, the agent meets a `None` and abstains, and the abstention is
    recorded in the ledger where somebody can read it.

    ## Order

    The configured order, unchanged. `baseline_agents` calls its order stable and
    `ResearchEngine._load_or_start_recovery` hashes `agent_ids` positionally into
    `graph_signature`, so a router that sorted its output would invalidate every resumable run
    whose roster it reordered.
    """

    def route(
        self,
        *,
        agents: Sequence[ResearchAgent],
        evidence: tuple[EvidenceSnapshot, ...],
        features: FeaturePlane | None = None,
    ) -> tuple[ResearchAgent, ...]:
        """Return selected agents in the configured stable order.

        `features=None` means this deployment reads no panel plane on this cycle, and it is
        read as "no column is available" rather than "every column is": the engine composes the
        plane, so `None` is the composition saying it has none, and a feature-dependent agent
        routed against nothing is an agent asked to read a column that does not exist.
        """
        families = _present_families(evidence)
        available = frozenset() if features is None else frozenset(features.feature_ids)
        selected: list[ResearchAgent] = []
        for agent in agents:
            declared_families = agent.evidence_families
            declared_features = agent.feature_dependencies
            if not declared_families and not declared_features:
                raise UndeclaredAgentDependencyError(
                    f"{agent.agent_id} declares neither an evidence family nor a feature "
                    "dependency, so no run can satisfy it and no run could ever have routed "
                    "it; declare `evidence_families`, `feature_dependencies`, or both"
                )
            if declared_families and not declared_families & families:
                continue
            if not declared_features <= available:
                continue
            selected.append(agent)
        return tuple(selected)


def _present_families(evidence: tuple[EvidenceSnapshot, ...]) -> frozenset[str]:
    """Every evidence family this run carries, read off the payloads exactly as before.

    `EvidenceSnapshot.payload` is a `JsonValue` with no schema, so a payload that is not an
    object contributes no family rather than raising -- unchanged from the rule this router
    shipped with, and `agents/baseline.py::_family` is the same read one plane over.
    """
    return frozenset(
        str(item.payload.get("family", ""))
        for item in evidence
        if isinstance(item.payload, Mapping)
    )
