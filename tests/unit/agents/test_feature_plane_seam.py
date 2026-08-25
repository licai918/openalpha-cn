"""`V2-P4-009`: the handle `AgentContext` gained, and the measurement that chose its shape.

The row proposes reusing `tools/base.py:54-62 ResearchTool` -- the extension point the seam
audit lists as declared, satisfied by exactly one class and imported by no module under `src/`.
This file is where that proposal was tested rather than taken, and the two measurements below
are the whole of the answer: the request type cannot express a real `feature_id` and the result
type cannot carry a number. What shipped instead is `FeaturePlane`, a second Protocol declared
beside its consumer in `agents/base.py`, which `domain/alpha_model.py::FeatureCrossSection`
already satisfies with no adapter and no edit.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openalpha_cn.agents.base import AgentContext, FeaturePlane
from openalpha_cn.agents.feature import CLAMP, DIRECTION_THRESHOLD, FeatureScoreAgent
from openalpha_cn.domain.alpha_model import FeatureCrossSection, FeatureRow
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.runtime.router import KNOWN_ROUTING_LIMITATIONS, ROUTING_LIMITATION_CODES
from openalpha_cn.tools.base import ResearchTool, ToolMetadata, ToolRequest, ToolResult

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
COLUMN = "reversal_1d/v1@raw"


def _plane(
    *, value: float | None = 0.4, ts_code: str = "000001.SZ", column: str = COLUMN
) -> FeatureCrossSection:
    return FeatureCrossSection(
        as_of=NOW, feature_ids=(column,), rows=(FeatureRow(ts_code=ts_code, values=(value,)),)
    )


def _context(plane: FeaturePlane | None) -> AgentContext:
    return AgentContext(run_id="run_1", subject="000001.SZ", as_of=NOW, evidence=(), features=plane)


def _declared_members(protocol: type) -> set[str]:
    """The names a Protocol's own class body declares."""
    return {name for name in vars(protocol) if not name.startswith("_")}


def test_the_registry_names_every_boundary_this_arm_declares() -> None:
    """The set-literal binding every registry in this repository carries."""
    declared = {
        "nothing_on_a_command_line_or_a_rest_body_composes_a_feature_plane",
        "a_face_that_named_a_column_would_need_three_declarations_a_request_does_not_carry",
        "a_feature_only_agent_cites_a_feature_id_that_no_evidence_store_resolves",
        "the_declared_research_tool_seam_cannot_carry_a_feature_id_or_a_number",
        "routing_reads_the_column_list_and_never_a_cell",
        "an_abstention_is_averaged_into_the_run_strength_as_a_zero",
        "a_clamped_score_cannot_separate_a_strong_reading_from_an_extreme_one",
    }

    assert declared == ROUTING_LIMITATION_CODES
    assert len(KNOWN_ROUTING_LIMITATIONS) == len(ROUTING_LIMITATION_CODES)


def test_a_real_neutralized_feature_id_is_longer_than_a_tool_request_can_carry() -> None:
    """The first half of `the_declared_research_tool_seam_cannot_carry_a_feature_id_or_a_number`.

    The factor key is read out of `FACTOR_DEFINITIONS` rather than typed in, so this measures
    the build rather than a sentence about it: a shorter registry would make the number smaller
    and this test says how much smaller before it stops mattering.
    """
    longest = max(
        (definition.qualified_key for definition in FACTOR_DEFINITIONS.definitions), key=len
    )
    feature_id = f"{longest}@neutralized:cross_section_standard/v1:industry_and_size/v1"

    assert len(feature_id) == 89
    assert ToolRequest.model_fields["kind"].metadata[1].max_length == 64
    with pytest.raises(ValidationError):
        ToolRequest(subject="000001.SZ", as_of=NOW, kind=feature_id)


def test_a_tool_result_has_no_field_a_feature_value_could_travel_in() -> None:
    """The second half of the same code, in both directions.

    The field set is asserted whole rather than by absence, because "there is no numeric field"
    is a claim about the model and a membership check would still pass on a model that gained
    one. And the `success` rule is driven, because it is what would force a valued read to be
    reported as `no_data` even if a number could be attached.
    """
    assert set(ToolResult.model_fields) == {"status", "evidence_ids", "no_data_reason"}
    assert ToolResult.model_config["extra"] == "forbid"

    with pytest.raises(ValidationError, match="success requires evidence_ids"):
        ToolResult(status="success")
    with pytest.raises(ValidationError):
        ToolResult(status="success", evidence_ids=(COLUMN,), value=0.4)


def test_the_two_seams_have_disjoint_members_so_neither_can_stand_in_for_the_other() -> None:
    """`FeaturePlane` is a second seam beside `ResearchTool`, not a replacement for it.

    Member sets rather than `isinstance` in both directions, because `ResearchTool` is **not**
    `runtime_checkable` -- `isinstance` against it raises `TypeError` -- and making it so to
    satisfy a test would be editing a shipped contract for the convenience of its own
    assertion. `FeaturePlane` is `runtime_checkable` because `AgentContext` needs pydantic to
    check the field; `ResearchTool` has no such consumer and gains nothing from it.

    The names are read off each Protocol rather than typed in, so a member added to either one
    fails here instead of being discovered when a class quietly satisfies both. `vars()` and
    not `typing.get_protocol_members`, which arrived in 3.12 and this build targets 3.11; it
    reads the class body, so it would miss a bare annotation -- neither Protocol here has one,
    and both would fail the equality below if one were added.
    """
    plane_members = _declared_members(FeaturePlane)
    tool_members = _declared_members(ResearchTool)

    assert plane_members == {"feature_ids", "value"}
    assert tool_members == {"metadata", "execute"}
    assert plane_members & tool_members == set()

    class Lookup:
        _metadata = ToolMetadata(tool_id="t", description="d", read_only=True)

        @property
        def metadata(self) -> ToolMetadata:
            return self._metadata

        def execute(self, request: ToolRequest) -> ToolResult:
            return ToolResult(status="no_data", no_data_reason="nothing")

    assert isinstance(_plane(), FeaturePlane)
    assert not isinstance(Lookup(), FeaturePlane)
    assert not any(hasattr(_plane(), member) for member in tool_members)


def test_the_context_holds_the_same_cross_section_object_it_was_given() -> None:
    """Implementation Decision 31: no per-row pydantic rebuild on a panel query path.

    Identity and not equality, because a rebuilt copy compares equal to its original -- a
    frozen dataclass has a generated `__eq__` -- so an equality assertion cannot tell the
    forbidden thing from the permitted one.
    """
    plane = _plane()

    assert _context(plane).features is plane


def test_a_handle_that_answers_only_one_of_the_two_questions_is_refused() -> None:
    """The Protocol is `runtime_checkable`, so the field is an `isinstance` check rather than a
    rebuild -- and that check is what refuses a half-built handle at the boundary."""

    class ColumnsOnly:
        feature_ids = (COLUMN,)

    with pytest.raises(ValidationError):
        _context(ColumnsOnly())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _context(object())  # type: ignore[arg-type]


def test_a_context_built_without_a_plane_carries_none_rather_than_an_empty_one() -> None:
    """`None` and an empty plane are different facts, and the second is not constructible.

    `FeatureCrossSection` refuses `rows=()` by name, so "a plane carrying nothing" cannot be
    built at all -- collapsing the two would have meant inventing a null object that claims to
    be a cross section.
    """
    assert _context(None).features is None
    with pytest.raises(Exception, match="carries no security"):
        FeatureCrossSection(as_of=NOW, feature_ids=(COLUMN,), rows=())


def test_the_context_still_refuses_a_field_nobody_declared() -> None:
    """`arbitrary_types_allowed` was added for the handle and `extra='forbid'` was not relaxed."""
    with pytest.raises(ValidationError):
        AgentContext(
            run_id="run_1",
            subject="000001.SZ",
            as_of=NOW,
            evidence=(),
            panel="whatever",  # type: ignore[call-arg]
        )


def test_the_reference_agent_cites_the_feature_id_it_read_and_not_an_evidence_id() -> None:
    """`a_feature_only_agent_cites_a_feature_id_that_no_evidence_store_resolves`, driven.

    The second assertion is the part worth keeping: the citation is measured *not* to look like
    an `EvidenceSnapshot.evidence_id`, which is what makes the two namespaces provably
    disjoint rather than merely different-looking.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=COLUMN, direction="higher_is_better"
    )

    result = agent.analyze(_context(_plane(value=0.4)))

    assert result.signal.evidence_ids == (COLUMN,)
    assert not COLUMN.startswith("ev_")
    assert result.signal.direction == "bullish"


def test_the_declared_direction_is_what_turns_a_value_into_a_side() -> None:
    """Both members of `FactorDirection` on one value, so neither branch is dead."""
    plane = _plane(value=0.4)
    higher = FeatureScoreAgent(
        agent_id="a", feature_id=COLUMN, direction="higher_is_better"
    ).analyze(_context(plane))
    lower = FeatureScoreAgent(agent_id="b", feature_id=COLUMN, direction="lower_is_better").analyze(
        _context(plane)
    )

    assert higher.signal.strength == pytest.approx(0.4)
    assert lower.signal.strength == pytest.approx(-0.4)
    assert (higher.signal.direction, lower.signal.direction) == ("bullish", "bearish")


def test_a_reading_inside_the_threshold_is_neutral_and_still_cites_its_column() -> None:
    """The third branch of the heading, which the two tests above cannot reach."""
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    result = agent.analyze(_context(_plane(value=0.1)))

    assert result.signal.direction == "neutral"
    assert result.signal.evidence_ids == (COLUMN,)


def test_an_extreme_reading_and_a_strong_one_reach_the_ledger_as_the_same_number() -> None:
    """`a_clamped_score_cannot_separate_a_strong_reading_from_an_extreme_one`, driven.

    Both directions of the clamp, and the `rationale` check is the remedy the entry points at:
    the pre-clamp value survives in prose even though it cannot survive in `strength`.
    """
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    strong = agent.analyze(_context(_plane(value=1.2)))
    extreme = agent.analyze(_context(_plane(value=14.0)))
    negative = agent.analyze(_context(_plane(value=-14.0)))

    assert strong.signal.strength == extreme.signal.strength == CLAMP
    assert negative.signal.strength == -CLAMP
    assert "1.2" in strong.rationale
    assert "14.0" in extreme.rationale


def test_a_null_cell_and_an_absent_security_are_two_abstentions_with_two_reasons() -> None:
    """`routing_reads_the_column_list_and_never_a_cell`: both consequences, told apart.

    `FeatureCrossSection.value` returns `None` for the first and raises `AlphaModelError` for
    the second, so an agent that handled only one of them would either score a hole or fail the
    whole cycle. Both end in an abstention and the two sentences differ.
    """
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    null_cell = agent.analyze(_context(_plane(value=None)))
    absent_security = agent.analyze(_context(_plane(ts_code="600000.SH")))

    assert null_cell.signal.direction == absent_security.signal.direction == "abstain"
    assert null_cell.signal.abstention_reason != absent_security.signal.abstention_reason
    assert COLUMN in (null_cell.signal.abstention_reason or "")
    assert "000001.SZ" in (absent_security.signal.abstention_reason or "")


def test_the_agent_refuses_a_context_the_router_would_never_have_built() -> None:
    """`analyze` is public, and a caller who assembled a context by hand can hand it no plane.

    Refusing rather than abstaining, because an abstention here would record "there was no
    number" when what happened is that the cycle was composed wrong -- and
    `nothing_on_a_command_line_or_a_rest_body_composes_a_feature_plane` is exactly the
    composition mistake a deployment is most likely to make.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=COLUMN, direction="higher_is_better"
    )

    with pytest.raises(ValueError, match="reversal-agent"):
        agent.analyze(_context(None))


def test_the_reference_agent_declares_exactly_the_column_it_reads() -> None:
    """The declaration and the read are one string, so a routed agent cannot read a column it
    did not declare -- which is the property `AgentRouter`'s subset rule is enforcing."""
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    assert agent.feature_dependencies == frozenset({COLUMN})
    assert agent.evidence_families == frozenset()


def test_a_confidence_outside_the_unit_interval_is_refused_at_construction() -> None:
    """`SignalFrame.confidence` is `ge=0, le=1`, and finding that out at `analyze` time would
    mean a cycle that fails on the run rather than on the wiring."""
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError, match="probability"):
            FeatureScoreAgent(
                agent_id="a", feature_id=COLUMN, direction="higher_is_better", confidence=bad
            )


def test_a_reading_exactly_on_a_threshold_is_neutral_and_one_past_it_is_not() -> None:
    """Both thresholds are exclusive, and both boundaries are driven (mutation sweep).

    A sweep over `agents/feature.py` left `strength > DIRECTION_THRESHOLD` and
    `strength < -DIRECTION_THRESHOLD` alive as `>=` and `<=`: every other test reads 0.4, 0.1 or
    1.2, so no fixture stood on either boundary and the two answers could not be separated. A
    threshold nobody stands on is a threshold nobody has chosen.

    Exact rather than approximate on purpose: `_clamped` does no arithmetic on a value inside the
    bounds, so `0.15` and `-0.15` reach the comparison as the literals they were written as.
    """
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    on_the_line = agent.analyze(_context(_plane(value=DIRECTION_THRESHOLD)))
    just_past = agent.analyze(_context(_plane(value=DIRECTION_THRESHOLD + 0.01)))
    on_the_other_line = agent.analyze(_context(_plane(value=-DIRECTION_THRESHOLD)))
    just_past_it = agent.analyze(_context(_plane(value=-DIRECTION_THRESHOLD - 0.01)))

    assert on_the_line.signal.strength == DIRECTION_THRESHOLD
    assert on_the_other_line.signal.strength == -DIRECTION_THRESHOLD
    assert on_the_line.signal.direction == "neutral"
    assert on_the_other_line.signal.direction == "neutral"
    assert just_past.signal.direction == "bullish"
    assert just_past_it.signal.direction == "bearish"


def test_an_abstention_reports_no_confidence_in_the_direction_it_did_not_take() -> None:
    """`confidence` on an abstaining frame, which `SignalFrame` does not constrain (sweep).

    A sweep left `confidence=0.0` alive at `1.0` in `_abstaining`: `validate_conclusion` checks
    `abstention_reason` and `strength` on an abstention and says nothing about confidence, so a
    frame reading "abstain, confidence 1.0" validates -- and `ResearchEngine._aggregate` averages
    that number into the run's confidence beside real signals. Both reachable abstentions are
    checked, because they are built by one helper and a test on either alone would not say so.
    """
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    for context in (_context(_plane(value=None)), _context(_plane(ts_code="600000.SH"))):
        signal = agent.analyze(context).signal
        assert signal.direction == "abstain"
        assert signal.confidence == 0.0
        assert signal.strength == 0.0


def test_the_reference_agent_declares_itself_deterministic_for_the_manifest() -> None:
    """S40's one fact, on the agent that carries it (sweep).

    `AgentProvenance.kind` is a closed `Literal`, so this cannot be a typo -- what it can be is
    the *wrong member*, and `RunManifest.agent_versions` records whichever it is. This agent
    reads one stored number and applies a declared sign; there is no model and nothing learned.
    """
    agent = FeatureScoreAgent(agent_id="a", feature_id=COLUMN, direction="higher_is_better")

    assert agent.provenance.kind == "deterministic"
    assert agent.provenance.model is None


def test_every_name_the_agent_contract_module_exports_resolves_and_none_is_missing() -> None:
    """`__all__` in both directions (mutation sweep).

    A sweep mutated every member of `agents/base.__all__` and killed none: nothing in this
    repository does `from openalpha_cn.agents.base import *`, so a name in that list that does not
    exist is a lie about the module's public surface that no import ever discovers. The first
    assertion is what refuses that.

    The second is the direction that matters more and is the one `FeaturePlane` arrived through: a
    contract *declared here* and left out of `__all__` is a seam a reader does not find.
    `__module__` is what separates the three declared here from `AgentResult`, `AgentProvenance`
    and `EvidenceSnapshot`, which are re-exports of `domain/` and are a different claim.
    """
    import openalpha_cn.agents.base as contracts

    assert all(hasattr(contracts, name) for name in contracts.__all__)

    declared_here = {
        name
        for name, value in vars(contracts).items()
        if not name.startswith("_") and getattr(value, "__module__", None) == contracts.__name__
    }
    assert declared_here == {"AgentContext", "FeaturePlane", "ResearchAgent"}
    assert declared_here <= set(contracts.__all__)
    assert list(contracts.__all__) == sorted(contracts.__all__)


def test_a_declared_routing_limitation_cannot_be_edited_after_it_is_declared() -> None:
    """The registry entries are frozen, which a sweep found nothing asserting.

    `@dataclass(frozen=True)` on a limitation is not decoration: `KNOWN_ROUTING_LIMITATIONS` is
    module state that every reader of this build shares, and an entry whose `detail` could be
    rebound at run time is a disclosure that can be edited by the code it describes.
    """
    entry = KNOWN_ROUTING_LIMITATIONS[0]

    with pytest.raises(FrozenInstanceError):
        entry.detail = "something else"  # type: ignore[misc]


def test_the_context_is_frozen_and_bounds_the_two_identifiers_it_carries() -> None:
    """`AgentContext`'s own `model_config` and field bounds (mutation sweep).

    Three properties a sweep found nothing asserting, and each is one an agent can rely on.
    **Frozen**: the context is the point-in-time statement of what this cycle may read, and one
    agent rebinding a field on it would change what the next agent in `selected` is handed --
    `_run_agents_with_recovery` passes the same object to every one of them. **Bounded**: an
    empty `run_id` or `subject` is not an identifier, and `RunRecoveryState.agent_ids` and the
    manifest are keyed off them.

    `arbitrary_types_allowed` is deliberately not asserted here and is not a gap: `FeaturePlane`
    is a Protocol, so a build without it cannot produce a schema for `features` at all and every
    test in this file fails at import.
    """
    context = _context(_plane())

    with pytest.raises(ValidationError):
        context.subject = "600000.SH"  # type: ignore[misc]

    # Both ends are asserted **accepted** as well as refused, because a bound tightened by one
    # refuses everything the loose bound refused: a sweep raising `min_length` from 1 to 2
    # survived a test that only checked `""`, since `""` is refused either way.
    inside = AgentContext(run_id="r", subject="s", as_of=NOW, evidence=())
    assert (inside.run_id, inside.subject) == ("r", "s")
    edge = AgentContext(run_id="r" * 128, subject="s" * 128, as_of=NOW, evidence=())
    assert len(edge.run_id) == len(edge.subject) == 128

    for field in ("run_id", "subject"):
        with pytest.raises(ValidationError):
            AgentContext(
                **{**dict(run_id="r", subject="s"), field: ""},
                as_of=NOW,
                evidence=(),
            )
        with pytest.raises(ValidationError):
            AgentContext(
                **{**dict(run_id="r", subject="s"), field: "x" * 129},
                as_of=NOW,
                evidence=(),
            )
