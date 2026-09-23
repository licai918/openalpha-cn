"""`V2-P4-008` turned a public extension point breaking, and the failure was a bare traceback.

`ResearchAgent`'s docstring calls it an "Extension contract", and `V2-P4-008` added
`feature_dependencies` to it as a **required** attribute. `runtime/router.py` then read
`agent.feature_dependencies` unguarded -- one line above the named refusal built for exactly this
class of failure -- so a third-party agent written before that wave met::

    AttributeError: 'LegacyAgent' object has no attribute 'feature_dependencies'

which names no contract, no remedy and no version, and reaches the caller through whatever
`ResearchEngine` does with an exception it did not anticipate.

## The measured shape of the claim that was wrong

`provenance`'s own docstring says an agent that omits it "fails structurally at the point it is
handed to the engine instead". Measured on `daaabf5`, it does not: `ResearchEngine._pair` reads
`agent.provenance` inside a dict comprehension and raises the same bare `AttributeError`, after
every agent has already run. So the structural check the contract advertised did not exist for
either attribute. `MissingAgentDeclarationError` is that check, and it is at the router because
that is the one seam every cycle crosses before any agent runs -- so nothing is written and no
recovery row is left behind claiming a run started, which is the property
`test_an_agent_declaring_nothing_refuses_the_cycle_by_name_on_the_product_path` already holds for
the sibling refusal.

## Why refuse rather than default to `frozenset()`

Defaulting is the reading `feature_dependencies`' own docstring argues against: "a declaration the
contract lets an agent omit is a declaration the router has to guess at, and the guess it would
have to make -- 'no features' -- is indistinguishable from the misdeclaration
`UndeclaredAgentDependencyError` exists to name". A legacy agent that in fact reads no panel
column and one that was written against a newer contract and lost its declaration are the same
object to a default, and only one of them is safe to route.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.agents.base import AgentProvenance
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.router import (
    MissingAgentDeclarationError,
    UndeclaredAgentDependencyError,
)
from openalpha_cn.sdk import OpenAlphaSDK

DIGEST: Final[str] = "c" * 64

REQUIRED_DECLARATIONS: Final[tuple[str, ...]] = (
    "agent_id",
    "evidence_families",
    "feature_dependencies",
    "provenance",
)
"""Every attribute `ResearchAgent` declares as required, named here as executable literals.

Kept as a literal rather than read off the Protocol, because `typing.get_type_hints` on a
Protocol answers about the annotations rather than about what the runtime reads, and the point of
this file is the second. `test_the_router_checks_every_attribute_the_contract_requires` holds
this tuple against the refusal, so an attribute added to the contract with no check arrives as a
red test rather than as an `AttributeError` at somebody's deployment.
"""


class _LegacyAgent:
    """An agent written against the contract as it stood before `V2-P4-008`.

    Deliberately not a subclass of anything in this package: the defect is about a *third-party*
    implementation of a Protocol, and inheriting a base class that supplies the attribute would
    be a fixture that cannot reproduce the failure it is about.
    """

    agent_id = "legacy-agent"
    evidence_families: frozenset[str] = frozenset({"market"})

    def __init__(self) -> None:
        self.provenance = AgentProvenance(kind="deterministic")

    def analyze(self, context: object) -> object:  # pragma: no cover - never reached
        raise AssertionError("an agent with an incomplete declaration must never be routed")


def _agent_missing(attribute: str) -> Any:
    """A `_LegacyAgent`-shaped object short exactly one required declaration."""

    namespace: dict[str, Any] = {
        "agent_id": "legacy-agent",
        "evidence_families": frozenset({"market"}),
        "feature_dependencies": frozenset(),
        "provenance": AgentProvenance(kind="deterministic"),
        "analyze": lambda self, context: None,
    }
    namespace.pop(attribute)
    return type("Shaped", (), namespace)()


@pytest.fixture
def request_for(frozen_now: datetime) -> Callable[..., ResearchRunRequest]:
    def _make(*, run_id: str, evidence: tuple[EvidenceSnapshot, ...] = ()) -> ResearchRunRequest:
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


def test_an_agent_written_before_this_wave_is_refused_by_name_and_not_by_traceback(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """`V2-P4-008`'s breaking change, met by the refusal it was owed.

    Driven through `OpenAlphaSDK` because that is the only face a composed agent reaches, and
    because the claim is that the *cycle* refuses rather than that a router function raises.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(_LegacyAgent(),))

    with pytest.raises(MissingAgentDeclarationError) as raised:
        sdk.run_research(request_for(run_id="run_legacy"))

    message = str(raised.value)
    assert "legacy-agent" in message
    assert "feature_dependencies" in message
    assert "frozenset()" in message


def test_nothing_is_written_for_a_cycle_that_never_started(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The refusal happens before any agent runs, so no recovery row claims a run began.

    The sibling `UndeclaredAgentDependencyError` already holds this property and it is the
    reason both checks live at the router rather than at the first read of each attribute:
    `ResearchEngine._pair` reads `provenance` *after* every agent has run, so a check there
    would leave a half-finished cycle on disk.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(_LegacyAgent(),))

    with pytest.raises(MissingAgentDeclarationError):
        sdk.run_research(request_for(run_id="run_legacy_nothing"))

    assert sdk.get_recovery("run_legacy_nothing") is None


@pytest.mark.parametrize("attribute", REQUIRED_DECLARATIONS)
def test_the_router_checks_every_attribute_the_contract_requires(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
    attribute: str,
) -> None:
    """One row per required declaration, so the check cannot cover three of four quietly.

    `provenance` is the row that matters most here: its own docstring claimed an agent omitting
    it "fails structurally at the point it is handed to the engine", and on `daaabf5` that was an
    `AttributeError` out of a dict comprehension in `_pair`, raised after the whole roster had
    already run. `agent_id` is the row that would otherwise be assumed -- the refusal has to
    name the agent, and an agent with no `agent_id` is the one case where it cannot.
    """
    sdk = OpenAlphaSDK(
        runtime_dir=tmp_path, clock=frozen_clock, agents=(_agent_missing(attribute),)
    )

    with pytest.raises(MissingAgentDeclarationError) as raised:
        sdk.run_research(request_for(run_id=f"run_missing_{attribute}"))

    assert attribute in str(raised.value)


def test_a_complete_declaration_still_reaches_the_sibling_refusal(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The separator: a check that refused every agent would satisfy every row above.

    `SilentAgent` declares all four attributes and declares *nothing* in two of them, which is
    `UndeclaredAgentDependencyError`'s case and not this one. The two refusals are different
    facts with different remedies -- "your agent predates a contract change" against "your agent
    can never be satisfied by any run" -- so a caller must be able to tell them apart, and
    `MissingAgentDeclarationError` deliberately does not subclass it.
    """

    class SilentAgent:
        agent_id = "silent-agent"
        evidence_families: frozenset[str] = frozenset()
        feature_dependencies: frozenset[str] = frozenset()

        def __init__(self) -> None:
            self.provenance = AgentProvenance(kind="deterministic")

        def analyze(self, context: object) -> object:  # pragma: no cover - never reached
            raise AssertionError("an undeclared agent must never be routed")

    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(SilentAgent(),))

    with pytest.raises(UndeclaredAgentDependencyError, match="silent-agent"):
        sdk.run_research(request_for(run_id="run_silent_still"))

    assert not issubclass(MissingAgentDeclarationError, UndeclaredAgentDependencyError)
    assert not issubclass(UndeclaredAgentDependencyError, MissingAgentDeclarationError)


def test_the_shipped_agents_declare_everything_the_check_requires(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    request_for: Callable[..., ResearchRunRequest],
) -> None:
    """The other direction, which is what keeps the check from being a wall.

    A structural check that refused a shipped roster would be caught by nothing above -- every
    row there asserts a refusal. This one asserts a cycle that completes, on the default agents
    the SDK composes when none is supplied.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock)

    result = sdk.run_research(request_for(run_id="run_shipped"))

    assert result.decision.routing_path
