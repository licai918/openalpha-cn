import json
from datetime import datetime
from typing import Any

import pytest

from openalpha_cn.agents.base import AgentContext
from openalpha_cn.agents.model import ModelProviderFailure, StructuredSignalAgent
from openalpha_cn.agents.prompts import STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.models.base import ModelMetadata
from openalpha_cn.models.openai_compatible import ModelResponseError


@pytest.fixture
def theme_evidence(frozen_now: datetime):
    """A single, fixed "theme" evidence item -- unlike the suite-wide `evidence(kind,
    facts)` fixture in `tests/conftest.py`, this one takes no arguments and has a
    different payload shape, so it stays local to this file rather than sharing that
    name."""

    def _make() -> EvidenceSnapshot:
        return EvidenceSnapshot(
            subject="000001.SZ",
            kind="theme",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="synthetic",
            source_uri="fixture://theme",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Synthetic theme evidence.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "theme",
                "facts": {"theme": "机器人", "score": 0.8},
                "quality_flags": [],
            },
        )

    return _make


class FakeModelProvider:
    metadata = ModelMetadata(
        provider_id="fake.model",
        model="fake-v1",
        credential_env_vars=(),
        structured_output=True,
    )

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.schema: dict[str, Any] | None = None
        self.systems: list[str] = []
        self.users: list[str] = []

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        attempt: int = 1,
    ) -> dict[str, Any]:
        assert system
        assert "ev_" in user
        self.systems.append(system)
        self.users.append(user)
        self.schema = schema
        output = self.outputs[self.calls]
        self.calls += 1
        return output


@pytest.fixture
def valid_output(frozen_now: datetime):
    def _make(item: EvidenceSnapshot) -> dict[str, Any]:
        return {
            "signal": {
                "subject": "000001.SZ",
                "as_of": frozen_now.isoformat(),
                "direction": "bullish",
                "strength": 0.5,
                "confidence": 0.7,
                "horizon": "5d",
                "evidence_ids": [item.evidence_id],
                "confirmation_conditions": ["Theme score remains elevated."],
                "invalidation_conditions": ["Theme score falls below 0.5."],
                "risk_flags": [],
            },
            "rationale": "The visible theme evidence supports a cautious bullish view.",
        }

    return _make


def test_structured_model_agent_retries_invalid_output_then_validates(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    item = theme_evidence()
    invalid = valid_output(item)
    invalid["signal"]["subject"] = "999999.SH"
    provider = FakeModelProvider([invalid, valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    result = agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
        )
    )

    assert provider.calls == 2
    assert provider.schema is not None
    assert result.signal.evidence_ids == (item.evidence_id,)
    assert result.agent_id == "model-theme-agent"


def test_structured_model_agent_fails_explicitly_after_retry_budget(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    item = theme_evidence()
    invalid = valid_output(item)
    invalid["signal"]["evidence_ids"] = ["ev_not_visible"]
    provider = FakeModelProvider([invalid, invalid])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    with pytest.raises(ModelProviderFailure, match="2 attempts"):
        agent.analyze(
            AgentContext(
                run_id="run_model",
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(item,),
            )
        )


PREVIOUS_SYSTEM = (
    "Return only the requested structured research payload. "
    "Cite only supplied evidence IDs and abstain when insufficient."
)


def test_the_default_prompt_sends_exactly_the_previous_system_text(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    """`V2-P7-012`: versioning the prompt did not rewrite it.

    The system message and the user rendering are pinned byte for byte against what this
    agent sent before the prompt became an artifact. A store whose recorded exchanges are
    content-addressed would treat any drift here as a new question, so "it got versioned"
    may not quietly mean "it got edited".
    """
    item = theme_evidence()
    provider = FakeModelProvider([valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
        )
    )

    assert provider.systems == [PREVIOUS_SYSTEM]
    assert provider.users == [
        json.dumps(
            {
                "run_id": "run_model",
                "subject": "000001.SZ",
                "as_of": frozen_now.isoformat(),
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "kind": "theme",
                        "summary": "Synthetic theme evidence.",
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    ]


# --- fix round 2, N1: a provider written before the attempt argument still works -------------


class OldSignatureModelProvider:
    """A user-written provider whose `generate_json` predates `attempt` (`V2-P7-012`).

    The SDK accepts user agents and providers, so this signature is a real extension point;
    handing it `attempt=` raised `TypeError`, which the agent's `except (ValidationError,
    ValueError)` does not catch and the cycle died unnamed.
    """

    metadata = ModelMetadata(
        provider_id="legacy.model",
        model="legacy-v1",
        credential_env_vars=(),
        structured_output=True,
    )

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.calls = 0

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        output = self.outputs[self.calls]
        self.calls += 1
        return output


class KwargsModelProvider(OldSignatureModelProvider):
    """The same, but with `**kwargs`: the attempt is accepted and observable."""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        super().__init__(outputs)
        self.attempts: list[int] = []

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.attempts.append(int(kwargs.get("attempt", 1)))
        return super().generate_json(system=system, user=user, schema=schema)


def test_an_old_signature_provider_keeps_working(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    """The old three-keyword `generate_json` is called exactly as it was before this batch."""
    item = theme_evidence()
    invalid = valid_output(item)
    invalid["signal"]["subject"] = "999999.SH"
    provider = OldSignatureModelProvider([invalid, valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    result = agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
        )
    )

    assert provider.calls == 2, "the old provider still retries; its retries are just unlabelled"
    assert result.signal.subject == "000001.SZ"


def test_a_kwargs_provider_still_receives_the_attempt_number(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    """`**kwargs` counts as accepting `attempt`: the retry stays distinguishable."""
    item = theme_evidence()
    invalid = valid_output(item)
    invalid["signal"]["subject"] = "999999.SH"
    provider = KwargsModelProvider([invalid, valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
        )
    )

    assert provider.attempts == [1, 2]


# --- V2-P7-012 Task 2.1: the json_object prompt carries the evidence facts -------------------


def test_include_facts_adds_the_evidence_facts_to_the_user_payload(
    theme_evidence, valid_output, frozen_now: datetime
) -> None:
    """A `json_object` deployment may set `include_facts`, and the facts actually arrive.

    The default payload carries only `evidence_id`/`kind`/`summary`, which gives the model no
    number to reason about. `include_facts=True` adds the frozen evidence payload's `facts` to
    each item; the default stays byte-identical (pinned by
    `test_the_default_prompt_sends_exactly_the_previous_system_text`).
    """
    item = theme_evidence()
    provider = FakeModelProvider([valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
        prompt=STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT,
        include_facts=True,
    )

    agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
        )
    )

    user = json.loads(provider.users[0])
    assert user["evidence"] == [
        {
            "evidence_id": item.evidence_id,
            "kind": "theme",
            "summary": "Synthetic theme evidence.",
            "facts": {"theme": "机器人", "score": 0.8},
        }
    ]
    assert agent.include_facts is True


class BlankContentProvider:
    """A provider that refuses every answer as blank, exactly as the real one now does."""

    metadata = ModelMetadata(
        provider_id="fake.model",
        model="fake-v1",
        credential_env_vars=(),
        structured_output=True,
    )

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(
        self, *, system: str, user: str, schema: dict[str, Any], attempt: int = 1
    ) -> dict[str, Any]:
        self.calls += 1
        raise ModelResponseError(
            "model response content is blank: an empty or whitespace-only answer is not a JSON "
            "object"
        )


def test_blank_content_is_retried_up_to_max_attempts_and_never_accepted(
    theme_evidence, frozen_now: datetime
) -> None:
    """`ModelResponseError` is a `ValueError`, so the blank refusal walks the retry path (D6).

    The agent must spend its budget re-asking, then raise `ModelProviderFailure` naming the
    answer's own reason -- an invalid answer becomes a coded abstention upstream, never a score
    of zero and never an accepted empty object.
    """
    item = theme_evidence()
    provider = BlankContentProvider()
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    with pytest.raises(ModelProviderFailure) as raised:
        agent.analyze(
            AgentContext(
                run_id="run_model",
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(item,),
            )
        )

    message = str(raised.value)
    assert provider.calls == 2, "each blank answer is retried, up to max_attempts"
    assert "2 attempts" in message
    assert "blank" in message
