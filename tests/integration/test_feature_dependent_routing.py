"""`V2-P4-008`/`V2-P4-009` driven through `OpenAlphaSDK`, which is the only face they have.

A research cycle is composed, not requested: `ResearchRunRequest` is `extra="forbid"`, is the
body of `POST /api/v1/research/run`, and is digested whole into `RunRecoveryState.request_digest`
-- so the panel-plane handle a feature-dependent agent reads cannot arrive on it without moving
the digest of every stored recovery row. It arrives beside `agents=` instead, on the SDK
constructor, and this file drives that seam end to end rather than calling `AgentRouter.route`
directly: the claim these two rows make is that such an agent *runs and is recorded*, and a
router returning a tuple is not that claim.

The sharpest case is the first one below, and it is the one that was unreachable on `be262ea`: a
run carrying **no evidence at all**. Every shipped agent declares an evidence family, so such a
run routed nobody, `_aggregate` returned the "No supported point-in-time evidence was available"
abstention, and the ledger recorded a `routing_path` of one element -- `("risk-gate",)`.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.agents.feature import FeatureScoreAgent
from openalpha_cn.domain.alpha_model import FeatureCrossSection, FeatureRow
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.router import UndeclaredAgentDependencyError
from openalpha_cn.sdk import OpenAlphaSDK

DIGEST = "c" * 64
MOMENTUM = "reversal_1d/v1@processed:cross_section_standard/v1"


@pytest.fixture
def plane(frozen_now: datetime) -> Callable[..., FeatureCrossSection]:
    def _make(
        *,
        feature_ids: tuple[str, ...] = (MOMENTUM,),
        value: float | None = 0.8,
        ts_code: str = "000001.SZ",
    ) -> FeatureCrossSection:
        return FeatureCrossSection(
            as_of=frozen_now,
            feature_ids=feature_ids,
            rows=(FeatureRow(ts_code=ts_code, values=tuple(value for _ in feature_ids)),),
        )

    return _make


@pytest.fixture
def request_for(frozen_now: datetime) -> Callable[..., ResearchRunRequest]:
    def _make(
        *,
        run_id: str,
        evidence: tuple[EvidenceSnapshot, ...] = (),
    ) -> ResearchRunRequest:
        return ResearchRunRequest(
            run_id=run_id,
            mode="live",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=evidence,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
        )

    return _make


def test_a_feature_dependent_agent_runs_and_is_recorded_on_a_run_carrying_no_evidence(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The row's whole claim, on the run shape that could not previously reach an agent at all.

    Four assertions and each one is a different plane of the record: the ledger's
    `agent_outputs`, its `routing_path`, the manifest's `agent_versions` (which is what S40 asks
    a run declaration to carry) and the aggregated signal. A fix that routed the agent but left
    it out of the manifest would pass one of them.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(agent,), features=plane())

    result = sdk.run_research(request_for(run_id="run_feature_only"))

    assert [output.agent_id for output in result.decision.agent_outputs] == ["reversal-agent"]
    assert result.decision.routing_path == ("reversal-agent", "risk-gate")
    assert [version.agent_id for version in result.manifest.agent_versions] == ["reversal-agent"]
    assert result.signal.direction == "bearish"
    assert result.signal.evidence_ids == (MOMENTUM,)


def test_the_same_agent_and_the_same_request_reach_no_agent_when_no_plane_was_composed(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The control for the test above: everything identical except `features=`.

    This is also exactly what the whole build did on `be262ea` -- so the abstention asserted
    here is the pre-fix behaviour, still reachable and still correct, rather than a regression
    that had to be tolerated. A deployment that has not built a factor panel gets it.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(agent,))

    result = sdk.run_research(request_for(run_id="run_no_plane"))

    assert result.decision.agent_outputs == ()
    assert result.decision.routing_path == ("risk-gate",)
    assert result.signal.direction == "abstain"
    assert result.signal.abstention_reason == ("No supported point-in-time evidence was available.")


def test_a_plane_that_carries_a_different_column_does_not_route_the_agent(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """Composing *a* plane is not composing *the* column, and the two are separated here.

    Without this, `features=<anything>` would be indistinguishable from `features=<the right
    thing>` on the product path, and the subset rule in `AgentRouter` would be pinned only by
    the unit test.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path,
        clock=frozen_clock,
        agents=(agent,),
        features=plane(feature_ids=("turnover_20d/v1@raw",)),
    )

    result = sdk.run_research(request_for(run_id="run_wrong_column"))

    assert result.decision.agent_outputs == ()
    assert result.signal.direction == "abstain"


def test_a_routed_agent_whose_cell_is_null_abstains_and_the_abstention_is_recorded(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """`AgentRouter` routes on column presence, never on a cell, and this is what that buys.

    The alternative rule -- route only when this subject's cell carries a number -- would make
    this run indistinguishable from the one above: no `agent_outputs`, no `routing_path` entry,
    nothing anywhere saying the column was consulted and found empty. Here the agent ran, said
    so, and the reason is in the ledger.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path, clock=frozen_clock, agents=(agent,), features=plane(value=None)
    )

    result = sdk.run_research(request_for(run_id="run_null_cell"))

    assert [output.agent_id for output in result.decision.agent_outputs] == ["reversal-agent"]
    assert result.decision.routing_path == ("reversal-agent", "risk-gate")
    assert result.agent_results[0].signal.direction == "abstain"
    assert result.agent_results[0].signal.abstention_reason is not None
    assert MOMENTUM in result.agent_results[0].signal.abstention_reason


def test_a_routed_agent_whose_security_is_not_on_the_plane_abstains_rather_than_raising(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """Routing decides on columns, so a plane built for a different universe still routes.

    `FeatureCrossSection.value` raises `AlphaModelError` for a security it does not carry, and
    an agent that let that escape would turn "this run's subject was not in the factor build"
    into a failed cycle with a `RunRecoveryState` marked `failed` -- see
    `ResearchEngine._run_agents_with_recovery`, which saves the failure and re-raises. An
    abstention naming the security is the answer that leaves a record instead.
    """
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path,
        clock=frozen_clock,
        agents=(agent,),
        features=plane(ts_code="600000.SH"),
    )

    result = sdk.run_research(request_for(run_id="run_absent_security"))

    signal = result.agent_results[0].signal
    assert signal.direction == "abstain"
    assert "000001.SZ" in (signal.abstention_reason or "")


def test_a_feature_agent_and_an_evidence_agent_both_run_in_one_cycle(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    evidence: Callable[..., EvidenceSnapshot],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The two halves of a declaration select two different agents in the configured order.

    `MarketAgent` is the shipped agent and it is untouched by this row: it declares
    `feature_dependencies = NO_FEATURE_COLUMNS`, so the plane composed here is irrelevant to
    whether it runs. That both appear, in the order they were given, is the regression this
    test holds.
    """
    from openalpha_cn.agents.baseline import MarketAgent

    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})
    agent = FeatureScoreAgent(
        agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
    )
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path,
        clock=frozen_clock,
        agents=(MarketAgent(), agent),
        features=plane(),
    )

    result = sdk.run_research(request_for(run_id="run_both", evidence=(item,)))

    assert [output.agent_id for output in result.decision.agent_outputs] == [
        "market-agent",
        "reversal-agent",
    ]
    assert set(result.signal.evidence_ids) == {item.evidence_id, MOMENTUM}


def test_an_agent_declaring_nothing_refuses_the_cycle_by_name_on_the_product_path(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """`UndeclaredAgentDependencyError` reaches the caller rather than being swallowed.

    The refusal is worth driving here and not only in the unit test, because the engine catches
    a raising *agent* (`_run_agents_with_recovery` records `failed` and re-raises) and this
    raises before any agent runs -- so nothing is written and no recovery row is left behind
    claiming a run started.
    """

    class SilentAgent:
        agent_id = "silent-agent"
        evidence_families: frozenset[str] = frozenset()
        feature_dependencies: frozenset[str] = frozenset()

        def __init__(self) -> None:
            from openalpha_cn.agents.base import AgentProvenance

            self.provenance = AgentProvenance(kind="deterministic")

        def analyze(self, context: object) -> object:  # pragma: no cover - never reached
            raise AssertionError("an undeclared agent must never be routed")

    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(SilentAgent(),))

    with pytest.raises(UndeclaredAgentDependencyError, match="silent-agent"):
        sdk.run_research(request_for(run_id="run_silent"))

    assert sdk.get_recovery("run_silent") is None


def test_the_agent_reads_the_very_cross_section_object_it_was_composed_with(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    plane: Callable[..., FeatureCrossSection],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """Implementation Decision 31, driven on the product path (`V2-P4-009`).

    `AgentContext` is a pydantic model and `FeatureCrossSection` is a frozen dataclass, so
    annotating the field with the concrete class would have made pydantic revalidate every row
    of a ~5,500-row market on every cycle. The field is a `runtime_checkable` Protocol under
    `arbitrary_types_allowed` instead, and object identity is the only assertion that can tell
    the two apart -- an equality check passes on a rebuilt copy.
    """
    seen: list[object] = []

    class RecordingAgent(FeatureScoreAgent):
        def analyze(self, context):  # type: ignore[no-untyped-def]
            seen.append(context.features)
            return super().analyze(context)

    composed = plane()
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path,
        clock=frozen_clock,
        agents=(
            RecordingAgent(
                agent_id="reversal-agent", feature_id=MOMENTUM, direction="lower_is_better"
            ),
        ),
        features=composed,
    )

    sdk.run_research(request_for(run_id="run_identity"))

    assert seen == [composed]
    assert seen[0] is composed
