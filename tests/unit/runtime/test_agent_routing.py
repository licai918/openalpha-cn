"""`V2-P4-008`: what `AgentRouter` selects, now that a declaration has two halves.

The defect this file is written against, measured on `be262ea` before anything moved:
`AgentRouter.route` was `agent.evidence_families & families`, so an agent whose whole
dependency is a *feature* column -- and which therefore declares no evidence family --
intersected the empty set with the run's families and was dropped. Not refused, not recorded
in the manifest, not visible in `routing_path`: dropped. `tests/unit/runtime/
test_agent_routing.py::test_the_defect_this_row_closes_is_that_an_empty_declaration_was_silent`
below drives the old rule by hand so the shape of what was fixed stays executable.

The two quantifiers are not the same, and that is the one design decision here worth reading.
An evidence family is satisfied by **any** declared family being present, because that is what
the shipped agents are built for: `ThemeAgent.analyze` filters `_family(item) in
self.evidence_families` over `{"theme", "catalyst", "disclosure"}` and scores whichever of the
three arrived, so a partial arrival is a smaller sample rather than a hole. A feature dependency
is satisfied only when **every** declared column is on the plane, because an agent's arithmetic
names a column by its `feature_id` and a column that is not there is not a smaller sample -- it
is a missing term.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.agents.baseline import ThemeAgent
from openalpha_cn.domain.alpha_model import FeatureCrossSection, FeatureRow
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.router import AgentRouter, UndeclaredAgentDependencyError

DETERMINISTIC = AgentProvenance(kind="deterministic")


class DeclaringAgent:
    """A minimal `ResearchAgent` that declares whatever the test needs it to declare."""

    def __init__(
        self,
        agent_id: str,
        *,
        evidence_families: frozenset[str] = frozenset(),
        feature_dependencies: frozenset[str] = frozenset(),
    ) -> None:
        self.agent_id = agent_id
        self.evidence_families = evidence_families
        self.feature_dependencies = feature_dependencies
        self.provenance = DETERMINISTIC

    def analyze(self, context: AgentContext) -> AgentResult:  # pragma: no cover - never called
        raise AssertionError("routing tests never run an agent")


@pytest.fixture
def plane() -> Callable[..., FeatureCrossSection]:
    def _make(
        *feature_ids: str, values: tuple[float | None, ...] | None = None
    ) -> FeatureCrossSection:
        return FeatureCrossSection(
            as_of=datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
            feature_ids=tuple(sorted(feature_ids)),
            rows=(
                FeatureRow(
                    ts_code="000001.SZ",
                    values=values if values is not None else tuple(0.5 for _ in feature_ids),
                ),
            ),
        )

    return _make


def _families(evidence: tuple[EvidenceSnapshot, ...]) -> set[str]:
    """The rule `route` used before `V2-P4-008`, kept executable rather than quoted."""
    families: set[str] = set()
    for item in evidence:
        if isinstance(item.payload, Mapping):
            families.add(str(item.payload.get("family", "")))
    return families


def test_the_defect_this_row_closes_is_that_an_empty_declaration_was_silent(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """The pre-`V2-P4-008` rule, applied by hand to a feature-only agent.

    Not a test of today's router -- it is the arithmetic the old one did, written out so the
    claim in this module's docstring is measured rather than remembered. An empty
    `frozenset` intersected with anything is empty, and an empty intersection was falsy, so
    `route` returned a tuple this agent was never in and said nothing about why.
    """
    agent = DeclaringAgent("feature-agent", feature_dependencies=frozenset({"m20/v1@raw"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    assert agent.evidence_families & _families((item,)) == set()


def test_an_agent_declaring_only_feature_dependencies_is_routed_when_the_plane_carries_them(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    agent = DeclaringAgent("feature-agent", feature_dependencies=frozenset({"m20/v1@raw"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    selected = AgentRouter().route(agents=(agent,), evidence=(item,), features=plane("m20/v1@raw"))

    assert [chosen.agent_id for chosen in selected] == ["feature-agent"]


def test_a_feature_only_agent_is_not_routed_when_no_plane_was_supplied(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """`features=None` is "this deployment reads no panel plane", not "everything is there"."""
    agent = DeclaringAgent("feature-agent", feature_dependencies=frozenset({"m20/v1@raw"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    assert AgentRouter().route(agents=(agent,), evidence=(item,)) == ()


def test_every_declared_column_must_be_on_the_plane_and_not_merely_one_of_them(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """The all-of quantifier, driven against the any-of one on the same fixture.

    Both columns are declared and exactly one is present, so an `&` rule -- the one the
    evidence half uses -- would select this agent and the subset rule does not. That is the
    separation this test exists to make: on a plane carrying neither column both rules agree.
    """
    agent = DeclaringAgent(
        "feature-agent", feature_dependencies=frozenset({"m20/v1@raw", "turnover/v1@raw"})
    )
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})
    one_of_two = plane("m20/v1@raw")

    assert agent.feature_dependencies & set(one_of_two.feature_ids) != set()
    assert AgentRouter().route(agents=(agent,), evidence=(item,), features=one_of_two) == ()


def test_any_declared_family_suffices_and_not_every_one_of_them(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """The any-of quantifier, driven against the all-of one on the same fixture (`V2-P4-112`).

    The counterpart of
    `test_every_declared_column_must_be_on_the_plane_and_not_merely_one_of_them` above, and
    it did not exist until `V2-P4-112`. Three families are declared and exactly one arrives,
    so `& families` selects this agent and a `<= families` rule would drop it -- which is the
    separation, and the only shape that makes it: every other `evidence_families=` in this
    file declares exactly one family, and on a single-family declaration the two rules agree
    for every run. Mutating `route`'s `declared_families & families` to
    `declared_families <= families` left this whole file green before this test was added.
    """
    agent = DeclaringAgent(
        "theme-agent", evidence_families=frozenset({"theme", "catalyst", "disclosure"})
    )
    one_of_three = evidence(kind="theme", facts={"theme": "机器人", "score": 0.82})

    assert agent.evidence_families & _families((one_of_three,)) != set()
    assert not agent.evidence_families <= _families((one_of_three,))
    selected = AgentRouter().route(agents=(agent,), evidence=(one_of_three,))

    assert [chosen.agent_id for chosen in selected] == ["theme-agent"]


def test_the_shipped_three_family_agent_is_the_reason_the_family_quantifier_is_any(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """This module's docstring cites `ThemeAgent` by name; here it is, actually routed.

    `AgentRouter`'s own docstring justifies `&` over `<=` with a shipped agent -- `ThemeAgent`
    declares `{"theme", "catalyst", "disclosure"}` and `analyze` filters `_family(item) in
    self.evidence_families`, so a run carrying only themes is a smaller sample and not a hole.
    Nothing in this file measured that: the claim was prose about a class the file never
    imported. Routing the real agent rather than a `DeclaringAgent` mirror is the point --
    a mirror can drift from the class it mirrors, and this assertion goes red if somebody
    narrows `ThemeAgent.evidence_families` to one family too.

    The `analyze` half is what makes the any-of rule safe rather than merely permissive:
    `strength = sum(scores) / len(scores)` divides by `len(items)`, so routing on a family
    that did arrive is exactly what guarantees the sample is non-empty.
    """
    agent = ThemeAgent()
    only_theme = evidence(kind="theme", facts={"theme": "机器人", "score": 0.82})

    assert len(agent.evidence_families) == 3
    selected = AgentRouter().route(agents=(agent,), evidence=(only_theme,))

    assert [chosen.agent_id for chosen in selected] == ["theme-agent"]
    assert not agent.evidence_families <= _families((only_theme,))


def test_an_evidence_family_agent_routes_exactly_as_it_did_before_the_plane_existed(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    present = DeclaringAgent("market-agent", evidence_families=frozenset({"market_event"}))
    absent = DeclaringAgent("capital-agent", evidence_families=frozenset({"capital"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    selected = AgentRouter().route(agents=(present, absent), evidence=(item,))

    assert [chosen.agent_id for chosen in selected] == ["market-agent"]


def test_a_hybrid_agent_whose_family_arrived_but_whose_column_did_not_is_not_routed(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """Fail-closed on the half that is missing, rather than routing on the half that is not.

    An agent declaring both halves is declaring that it reads both. Routing it because its
    evidence arrived would hand it a context whose plane has no column it named, and the
    signal it produced would be an answer computed without a term it said it needed.
    """
    agent = DeclaringAgent(
        "hybrid-agent",
        evidence_families=frozenset({"market_event"}),
        feature_dependencies=frozenset({"m20/v1@raw"}),
    )
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    assert AgentRouter().route(agents=(agent,), evidence=(item,)) == ()
    assert (
        AgentRouter().route(agents=(agent,), evidence=(item,), features=plane("turnover/v1@raw"))
        == ()
    )
    selected = AgentRouter().route(agents=(agent,), evidence=(item,), features=plane("m20/v1@raw"))
    assert [chosen.agent_id for chosen in selected] == ["hybrid-agent"]


def test_a_hybrid_agent_whose_column_is_there_but_whose_family_did_not_arrive_is_not_routed(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """The mirror of the test above, so neither half can be dropped without something going red."""
    agent = DeclaringAgent(
        "hybrid-agent",
        evidence_families=frozenset({"capital"}),
        feature_dependencies=frozenset({"m20/v1@raw"}),
    )
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    assert (
        AgentRouter().route(agents=(agent,), evidence=(item,), features=plane("m20/v1@raw")) == ()
    )


def test_an_agent_declaring_neither_a_family_nor_a_column_is_refused_by_name(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """The silence this row is about, converted into a refusal that names the agent.

    Such an agent could never have been routed under the old rule and cannot be routed under
    this one either, because there is nothing to satisfy. Dropping it is what made a
    misdeclared agent look like an agent that had nothing to say; a `SignalFrame` refuses any
    non-abstaining direction with no `evidence_ids`, so the most such an agent could ever have
    contributed is an abstention nobody asked for.
    """
    agent = DeclaringAgent("undeclared-agent")
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    with pytest.raises(UndeclaredAgentDependencyError) as raised:
        AgentRouter().route(agents=(agent,), evidence=(item,), features=plane("m20/v1@raw"))

    assert "undeclared-agent" in str(raised.value)


def test_the_refusal_fires_even_when_a_well_declared_agent_would_have_been_selected(
    evidence: Callable[..., EvidenceSnapshot],
) -> None:
    """A misdeclared agent beside a good one still refuses, rather than being quietly skipped.

    Without this, the refusal would be reachable only in the degenerate roster of one, which
    is not the roster a deployment has.
    """
    good = DeclaringAgent("market-agent", evidence_families=frozenset({"market_event"}))
    bad = DeclaringAgent("undeclared-agent")
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    with pytest.raises(UndeclaredAgentDependencyError):
        AgentRouter().route(agents=(good, bad), evidence=(item,))


def test_routing_returns_the_configured_order_and_not_the_order_a_plane_lists_columns_in(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """`baseline_agents` calls its order stable, and the engine's recovery graph signature
    hashes `agent_ids` positionally, so an order that moved would invalidate a resumable run."""
    first = DeclaringAgent("z-feature-agent", feature_dependencies=frozenset({"a/v1@raw"}))
    second = DeclaringAgent("a-market-agent", evidence_families=frozenset({"market_event"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})

    selected = AgentRouter().route(
        agents=(first, second), evidence=(item,), features=plane("a/v1@raw", "b/v1@raw")
    )

    assert [chosen.agent_id for chosen in selected] == ["z-feature-agent", "a-market-agent"]


def test_a_column_present_on_the_plane_but_null_for_this_security_still_routes(
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """Routing reads the plane's declaration and never one of its cells, on purpose.

    `FeatureRow.values` carries `None` for a security the column has no number for, and the
    tempting rule -- route only when this subject's cell is populated -- reintroduces exactly
    the defect this row closes one level down: an agent dropped for a missing cell leaves no
    abstention, no `routing_path` entry and no manifest row, so "the column was empty here" and
    "no agent had anything to say" become one observation again. Routed, the agent abstains and
    the abstention is recorded.
    """
    agent = DeclaringAgent("feature-agent", feature_dependencies=frozenset({"m20/v1@raw"}))
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})
    hollow = plane("m20/v1@raw", values=(None,))

    assert hollow.value(ts_code="000001.SZ", feature_id="m20/v1@raw") is None
    selected = AgentRouter().route(agents=(agent,), evidence=(item,), features=hollow)
    assert [chosen.agent_id for chosen in selected] == ["feature-agent"]


def test_an_abstaining_signal_is_the_most_a_feature_only_agent_can_emit_without_citing_evidence(
    plane: Callable[..., FeatureCrossSection],
) -> None:
    """`KNOWN_ROUTING_LIMITATIONS.a_feature_only_agent_cites_a_feature_id_that_no_evidence_store_resolves`.

    Measured rather than argued: `SignalFrame.validate_conclusion` refuses any direction other
    than `abstain` when `evidence_ids` is empty, and `neutral` is one of the refused ones. So
    making a feature-dependent agent reachable does not by itself make a feature-dependent
    *conclusion* reachable -- what the agent cites has to come from somewhere, and this build's
    answer is that it cites the `feature_id`s it read.
    """
    columns = plane("m20/v1@raw")
    with pytest.raises(ValueError, match="directional signal requires evidence"):
        SignalFrame(
            subject="000001.SZ",
            as_of=columns.as_of,
            direction="neutral",
            strength=0.0,
            confidence=0.4,
            horizon="5d",
        )

    cited = SignalFrame(
        subject="000001.SZ",
        as_of=columns.as_of,
        direction="neutral",
        strength=0.0,
        confidence=0.4,
        horizon="5d",
        evidence_ids=columns.feature_ids,
    )
    assert cited.evidence_ids == ("m20/v1@raw",)
