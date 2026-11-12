"""`V2-P7-012`: prompts are versioned artifacts, not free text inside a call.

`RunManifest.prompt_versions` was declared by `V2-P4-010` and stayed empty on every path, with
a stated reason: the only prompt was a string literal in `agents/model.py`, and `code_commit`
already pins the source tree. That reason stops holding when the *prompt* is what a caller can
choose independently of the commit -- and it is now a real choice: `StructuredSignalAgent`
takes a `PromptTemplate`. The template's addressed content is
`{name, system, user_format, response_schema}`; the agent declares the digest as
`AgentProvenance.prompt`, and the engine lifts it into the manifest.
"""

import json
import string
from typing import Any, Final

import pytest

from openalpha_cn.agents.model import StructuredAgentPayload, StructuredSignalAgent
from openalpha_cn.agents.prompts import (
    STRUCTURED_SIGNAL_PROMPT_V1,
    STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT,
    PromptTemplate,
)
from openalpha_cn.domain.run import AgentProvenance, VersionRef
from openalpha_cn.models.base import ModelMetadata

PREVIOUS_SYSTEM: Final[str] = (
    "Return only the requested structured research payload. "
    "Cite only supplied evidence IDs and abstain when insufficient."
)

RESPONSE_SCHEMA: Final[dict[str, Any]] = StructuredAgentPayload.model_json_schema(mode="validation")

V1_PROMPT_VERSION: Final[str] = "9b70f97590a18bbd91416010985b3c8bbed70b853d24091237b05345e7bbcf2b"
"""The content address `STRUCTURED_SIGNAL_PROMPT_V1` had before V2 existed."""


class _NeverCalledProvider:
    """The provenance half needs a provider's metadata, not a response."""

    metadata = ModelMetadata(
        provider_id="fake.model",
        model="fake-v1",
        credential_env_vars=(),
        structured_output=True,
    )

    def generate_json(
        self, *, system: str, user: str, schema: dict[str, Any], attempt: int = 1
    ) -> dict[str, Any]:
        raise AssertionError("the provenance tests never run the agent")


def _agent(*, prompt: PromptTemplate = STRUCTURED_SIGNAL_PROMPT_V1) -> StructuredSignalAgent:
    return StructuredSignalAgent(
        agent_id="llm-agent",
        evidence_families=frozenset({"market_event"}),
        provider=_NeverCalledProvider(),
        prompt=prompt,
    )


def test_the_default_system_text_is_the_literal_the_agent_sent_before() -> None:
    """The artifact starts as exactly the text it replaces -- not a rewrite dressed as a move."""
    assert STRUCTURED_SIGNAL_PROMPT_V1.system == PREVIOUS_SYSTEM


def test_the_prompt_version_is_stable_for_the_same_template_and_schema() -> None:
    first = STRUCTURED_SIGNAL_PROMPT_V1.version_ref(response_schema=RESPONSE_SCHEMA)
    second = _agent().provenance.prompt

    assert second is not None
    assert first == second
    assert first.component == f"prompt:{STRUCTURED_SIGNAL_PROMPT_V1.name}"
    # A content address, not a hand-written tag: 64 hex characters.
    assert len(first.version) == 64 and all(c in "0123456789abcdef" for c in first.version)


def test_a_one_character_prompt_edit_moves_the_prompt_version() -> None:
    edited = PromptTemplate(
        name=STRUCTURED_SIGNAL_PROMPT_V1.name,
        system=PREVIOUS_SYSTEM.replace("Return", "return"),
        user_format=STRUCTURED_SIGNAL_PROMPT_V1.user_format,
    )
    assert edited.system != STRUCTURED_SIGNAL_PROMPT_V1.system
    assert len(edited.system) == len(STRUCTURED_SIGNAL_PROMPT_V1.system)

    original_ref = STRUCTURED_SIGNAL_PROMPT_V1.version_ref(response_schema=RESPONSE_SCHEMA)
    edited_ref = edited.version_ref(response_schema=RESPONSE_SCHEMA)

    assert edited_ref.component == original_ref.component
    assert edited_ref.version != original_ref.version


def test_the_prompt_version_covers_the_response_schema() -> None:
    """The output contract is part of the prompt: a schema change changes what was asked."""
    wider = dict(RESPONSE_SCHEMA, description="One extra sentence.")
    assert wider != RESPONSE_SCHEMA

    original_ref = STRUCTURED_SIGNAL_PROMPT_V1.version_ref(response_schema=RESPONSE_SCHEMA)
    wider_ref = STRUCTURED_SIGNAL_PROMPT_V1.version_ref(response_schema=wider)

    assert wider_ref.version != original_ref.version


def test_a_custom_prompt_reaches_the_agent_provenance() -> None:
    custom = PromptTemplate(
        name="structured-signal/experiment",
        system=PREVIOUS_SYSTEM + " Answer in one sentence.",
        user_format=STRUCTURED_SIGNAL_PROMPT_V1.user_format,
    )

    agent = _agent(prompt=custom)

    assert agent.provenance.prompt == custom.version_ref(response_schema=RESPONSE_SCHEMA)
    assert agent.provenance.prompt != STRUCTURED_SIGNAL_PROMPT_V1.version_ref(
        response_schema=RESPONSE_SCHEMA
    )


def test_an_llm_backed_agent_may_declare_a_prompt() -> None:
    reference = VersionRef(component="prompt:structured-signal/v1", version="a" * 64)

    provenance = AgentProvenance(
        kind="llm_backed",
        model=VersionRef(component="deepseek", version="deepseek-chat"),
        prompt=reference,
    )

    assert provenance.prompt == reference


def test_a_deterministic_agent_may_not_declare_a_prompt() -> None:
    """Constrained in both directions, like the vendor model beside it: a prompt nothing ran
    is a fabricated provenance row, not a stricter declaration."""
    with pytest.raises(ValueError, match="only an llm_backed agent may name a prompt"):
        AgentProvenance(
            kind="deterministic",
            prompt=VersionRef(component="prompt:structured-signal/v1", version="a" * 64),
        )


# --- fix round 1: a template that cannot be rendered is not a prompt artifact -------------------


def test_a_custom_template_that_cannot_be_rendered_is_refused_at_construction() -> None:
    """Fix round 1, F7: a bad template must fail here, named, not as a `KeyError` mid-cycle.

    A literal-brace JSON example or another placeholder name used to be accepted by
    `version_ref` and then raise `KeyError`/`IndexError` inside the agent, where the retry
    `except (ValidationError, ValueError)` does not catch it and the cycle dies unnamed.
    """
    for user_format in (
        '{"example": true}',
        "{payload}",
        "{payload_json} {payload_json}",
        "",
        # A nested format spec and a conversion both render to a `KeyError`/a value the agent
        # never validates (review 1's F7 residue): the template must be refused here, not at
        # call time.
        "{payload_json:{width}}",
        "{payload_json!r}",
        "{payload_json.upper}",
    ):
        with pytest.raises(ValueError, match="payload_json"):
            PromptTemplate(name="custom/v1", system="Return JSON.", user_format=user_format)


def test_the_default_template_is_accepted_and_renderable() -> None:
    rendered = STRUCTURED_SIGNAL_PROMPT_V1.user_format.format(payload_json='{"run_id": "r"}')
    assert rendered == '{"run_id": "r"}'


# --- V2-P7-012 Task 2.1: the json_object prompt, beside an unchanged V1 ----------------------


def test_v2_prompt_mentions_json_and_carries_an_example() -> None:
    """`response_format={"type": "json_object"}` needs the prompt to say "json" and show one.

    The example is the only description of the output contract the vendor gets in `json_object`
    mode -- the response schema is not sent -- so it must be one parseable JSON object, and the
    rendering must still be exactly one bare `{payload_json}`.
    """
    prompt = STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT

    assert "json" in prompt.system.lower()
    assert prompt.name != STRUCTURED_SIGNAL_PROMPT_V1.name

    fields = [
        (field_name, format_spec, conversion)
        for _, field_name, format_spec, conversion in string.Formatter().parse(prompt.user_format)
        if field_name is not None
    ]
    assert fields == [("payload_json", "", None)]

    start = prompt.system.index("{")
    example, _ = json.JSONDecoder().raw_decode(prompt.system[start:])
    assert isinstance(example, dict)
    assert set(example) == {"signal", "rationale"}
    assert isinstance(example["signal"], dict)
    assert prompt.user_format.format(payload_json='{"subject": "000001.SZ"}') == (
        '{"subject": "000001.SZ"}'
    )


def test_v1_prompt_bytes_unchanged() -> None:
    """V2 is a new artifact; it may not rewrite the one recorded exchanges already address."""
    assert STRUCTURED_SIGNAL_PROMPT_V1.name == "structured-signal/v1"
    assert STRUCTURED_SIGNAL_PROMPT_V1.system == PREVIOUS_SYSTEM
    assert STRUCTURED_SIGNAL_PROMPT_V1.user_format == "{payload_json}"
    assert (
        STRUCTURED_SIGNAL_PROMPT_V1.version_ref(response_schema=RESPONSE_SCHEMA).version
        == V1_PROMPT_VERSION
    )
