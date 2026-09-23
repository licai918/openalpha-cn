"""`V2-P4-010` end-to-end: what a real `run_cycle` writes into the manifest's model slots.

The measurement this module exists to invert, taken on `d234e4b` against this same wiring:

    A model_versions : [{'component': 'llm-agent', 'version': 'baseline/v1'}]
    B model_versions : [{'component': 'llm-agent', 'version': 'baseline/v1'}]
    A prompt_versions: ()
    addresses equal? True
    decision ids equal? True

Two runs whose only difference was which vendor model answered produced the same
`run_manifest_id` and the same `decision_id`. That is roadmap section 9's finding -- "different
configurations produce the same decision ID" -- reproduced in the model plane after
`V2-P4-025` closed it in the configuration plane, and it was reachable with the agents this
repository already ships: `StructuredSignalAgent` takes any `ModelProvider`, and the manifest
recorded the same six words for all of them.

The contract-level half is `tests/unit/domain/test_manifest_component_provenance.py`. This
module is the one that can fail on account of the *engine*, which is where the row's evidence
points (`runtime/engine.py`, lines 92-96 and 128-129 as of `d234e4b`; the row's `131-135,
160-161` had drifted).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.agents.baseline import MarketAgent
from openalpha_cn.agents.model import StructuredSignalAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.models.base import ModelCapabilities, ModelMetadata
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine

DIGEST: Final[str] = "a" * 64
RUN_ID: Final[str] = "run_model_provenance"


class _FixedProvider:
    """A `ModelProvider` that answers identically whatever model it claims to be.

    Identical answers are the whole point: the two runs below differ in the *vendor model
    string and nothing else*, so every downstream artefact -- the signal, the rationale, the
    agent output, the risk decision -- is byte-identical between them. If the addresses still
    separate, the only thing that can have separated them is the model identity reaching the
    manifest.
    """

    def __init__(self, *, model: str, as_of: datetime, evidence_id: str) -> None:
        self._model = model
        self._as_of = as_of
        self._evidence_id = evidence_id

    @property
    def metadata(self) -> ModelMetadata:
        return ModelMetadata(
            provider_id="openai-compatible",
            model=self._model,
            credential_env_vars=(),
            structured_output=True,
            capabilities=ModelCapabilities(),
        )

    def generate_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "signal": {
                "subject": "000001.SZ",
                "as_of": self._as_of.isoformat(),
                "direction": "bullish",
                "strength": 0.4,
                "confidence": 0.6,
                "horizon": "5d",
                "evidence_ids": [self._evidence_id],
            },
            "rationale": "Price and volume confirm the event.",
        }


@pytest.fixture
def run_once(
    tmp_path: Path,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
    frozen_clock: Callable[[], datetime],
) -> Callable[..., ResearchRunResult]:
    """Drive one real `run_cycle` in a fresh runtime directory, varying only the agent panel."""
    counter = {"n": 0}

    def _run(*, model: str | None) -> ResearchRunResult:
        counter["n"] += 1
        item = evidence(
            kind="limit_up",
            facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
        )
        if model is None:
            agents: tuple[Any, ...] = (MarketAgent(),)
        else:
            provider = _FixedProvider(model=model, as_of=frozen_now, evidence_id=item.evidence_id)
            agents = (
                StructuredSignalAgent(
                    agent_id="llm-agent",
                    evidence_families=frozenset({"market_event"}),
                    provider=provider,
                ),
            )
        storage = build_storage(runtime_dir=tmp_path / f"runtime{counter['n']}", clock=frozen_clock)
        engine = ResearchEngine(
            repository=storage.repository,
            memory=storage.memory,
            clock=frozen_clock,
            recovery_store=storage.recovery_store,
            agents=agents,
        )
        return engine.run_cycle(
            ResearchRunRequest(
                run_id=RUN_ID,
                mode="live",
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(item,),
                code_commit="0123456789abcdef",
                config_digest=DIGEST,
                random_seed=7,
            )
        )

    return _run


def test_two_runs_answered_by_different_vendor_models_do_not_share_an_identity(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """The measured collision, inverted, and asserted at both levels it was measured at.

    `decision_id` as well as `run_manifest_id`, because the first alone would leave PRD section
    1.3 B6 standing for the model plane -- and the ledger reaches it through
    `run_manifest_id` rather than through a copy, which is `V2-P4-025`'s arrangement working
    for an input it never knew about.
    """
    qwen = run_once(model="qwen-max-2025-01-25")
    deepseek = run_once(model="deepseek-chat-v3")

    assert qwen.manifest.run_manifest_id != deepseek.manifest.run_manifest_id
    assert qwen.decision.decision_id != deepseek.decision.decision_id


def test_the_same_vendor_model_twice_reproduces_every_identity(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """The control the assertion above is worthless without.

    A `run_manifest_id` that moved between two identical runs would satisfy the separation test
    for free. Both runs here build a fresh runtime directory and a fresh provider object, so
    this also rules out the address being stabilised by anything the first run left behind.
    """
    first = run_once(model="qwen-max-2025-01-25")
    second = run_once(model="qwen-max-2025-01-25")

    assert first.manifest.run_manifest_id == second.manifest.run_manifest_id
    assert first.decision.decision_id == second.decision.decision_id


def test_an_llm_backed_run_records_the_model_it_called_and_says_the_agent_is_llm_backed(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """S40, stated over the two slots that now carry the answer between them.

    The agent plane says *what kind of thing* ran; the LLM plane says *which vendor model* it
    ran on. Both are asserted by value rather than by non-emptiness, because "the slot is
    populated" was true before this issue too -- with the wrong contents.
    """
    result = run_once(model="qwen-max-2025-01-25")

    assert [item.model_dump() for item in result.manifest.agent_versions] == [
        {"agent_id": "llm-agent", "kind": "llm_backed"}
    ]
    assert [item.model_dump() for item in result.manifest.model_versions] == [
        {"component": "openai-compatible", "version": "qwen-max-2025-01-25"}
    ]


def test_a_deterministic_run_names_no_model_and_no_longer_claims_a_baseline_version(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """The other half of the row: the constant is gone, and nothing replaced it with a fiction.

    `"baseline/v1"` is asserted absent from the whole serialised manifest rather than from
    `model_versions` alone -- the failure this guards against is the string being moved to the
    new slot rather than being retired, and a field-scoped assertion would not see that.
    `model_versions` is asserted **empty** for a run with no model in it, which is the state
    the pre-issue engine could never produce: it wrote one entry per agent unconditionally.
    """
    result = run_once(model=None)

    assert [item.model_dump() for item in result.manifest.agent_versions] == [
        {"agent_id": "market-agent", "kind": "deterministic"}
    ]
    assert result.manifest.model_versions == ()
    assert result.manifest.alpha_model_versions == ()
    assert "baseline/v1" not in result.manifest.model_dump_json()


def test_the_ledger_inherits_the_new_planes_without_gaining_a_field_per_plane(
    run_once: Callable[..., ResearchRunResult],
) -> None:
    """Why `DecisionLedger` gains nothing here, asserted rather than left to the commit message.

    `domain/run.py` states the arrangement `V2-P4-025` installed: the ledger names the
    manifest's *address*, so it inherits every declared run input at once instead of gaining a
    field per input -- "including inputs `RunManifest` gains later", which these two are. Adding
    `agent_versions`/`alpha_model_versions` mirrors to the ledger would put the same fact in two
    places and re-open exactly the drift that field was introduced to close.
    """
    from openalpha_cn.domain.decision import DecisionLedger

    result = run_once(model="qwen-max-2025-01-25")

    assert "agent_versions" not in DecisionLedger.model_fields
    assert "alpha_model_versions" not in DecisionLedger.model_fields
    assert result.decision.run_manifest_id == result.manifest.run_manifest_id
