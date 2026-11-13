"""Prompt templates as content-addressed artifacts (`V2-P7-012`).

`RunManifest.prompt_versions` was declared by `V2-P4-010` and left empty on every path, with a
reason that was true then: the only prompt in this repository was a string literal in
`agents/model.py`, and `RunManifest.code_commit` already pins the source tree, so a prompt
version would have been a second copy of one fact. This module changes the fact rather than the
slot -- a prompt becomes a value a caller can choose, retain and compare, so the manifest has
to record *which* one ran.

`PromptTemplate.version_ref` hashes `{name, system, user_format, response_schema}` through the
same canonical-JSON helper every other content address in this repository uses
(`domain/json_value.canonical_json_bytes`). The response schema is deliberately part of the
address: the schema is what the model was asked to produce, so a schema edit is a prompt edit
even when the prose is untouched -- `StructuredAgentPayload.model_json_schema(mode="validation")`
is the exact value the agent sends.

The default below is the literal the agent sent before this issue, verbatim. That matters for
the same reason the model-exchange digest exists: a recorded exchange is addressed by the
request bytes, so "the prompt became versioned" may not quietly mean "the prompt was rewritten".

`STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT` is the second artifact, not a rewrite of the first: a
vendor whose structured-output mode is only `json_object` (no schema in the request) documents
that the prompt itself must contain the word "json" and a worked example. V1 stays
byte-identical because the exchanges already recorded under it are addressed by those bytes.
"""

import hashlib
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from openalpha_cn.domain.json_value import canonical_json_bytes
from openalpha_cn.domain.run import VersionRef

PROMPT_COMPONENT_PREFIX: Final[str] = "prompt:"
"""The prefix every prompt artifact's `VersionRef.component` carries."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptTemplate:
    """One versioned prompt artifact: the system text and how the user message is rendered."""

    name: str
    """The artifact family, e.g. `structured-signal/v1`; part of the address and the component."""
    system: str
    user_format: str
    """The user message template; `{payload_json}` is the rendered user payload."""

    def __post_init__(self) -> None:
        """Refuse a template that cannot be rendered, at construction, by name.

        Fix round 1, F7: `StructuredSignalAgent` renders this with
        `.format(payload_json=...)`, so a literal-brace JSON example or another placeholder name
        raised `KeyError`/`IndexError` *inside the cycle* -- not caught by the agent's
        `except (ValidationError, ValueError)` -- after `version_ref` had already accepted the
        template as an artifact. Validation belongs where the artifact is created.
        """
        try:
            parsed = [
                (field_name, format_spec, conversion)
                for _, field_name, format_spec, conversion in string.Formatter().parse(
                    self.user_format
                )
                if field_name is not None
            ]
        except ValueError as error:
            raise ValueError(
                f"prompt {self.name!r}: user_format must contain exactly one "
                f"{{payload_json}} field; its braces are not a legal template ({error})"
            ) from error
        # A nested format spec (`{payload_json:{width}}`) or an attribute/conversion
        # (`{payload_json.upper}`, `{payload_json!r}`) parses as one field but renders to a
        # `KeyError` or to a value the agent never validated: the field must be exactly
        # `payload_json`, with nothing after it. Review 1's F7 residue.
        strict = [
            (field_name, format_spec, conversion)
            for field_name, format_spec, conversion in parsed
            if field_name != "payload_json" or format_spec or conversion is not None
        ]
        if len(parsed) != 1 or strict:
            raise ValueError(
                f"prompt {self.name!r}: user_format must contain exactly one bare "
                f"{{payload_json}} field (no attribute, conversion or format spec); it "
                f"declares {parsed!r}"
            )

    def version_ref(self, *, response_schema: Mapping[str, Any]) -> VersionRef:
        """The content address of this template as it will be sent with `response_schema`."""
        canonical = canonical_json_bytes(
            {
                "name": self.name,
                "system": self.system,
                "user_format": self.user_format,
                "schema": dict(response_schema),
            }
        )
        return VersionRef(
            component=f"{PROMPT_COMPONENT_PREFIX}{self.name}",
            version=hashlib.sha256(canonical).hexdigest(),
        )


STRUCTURED_SIGNAL_PROMPT_V1: Final[PromptTemplate] = PromptTemplate(
    name="structured-signal/v1",
    system=(
        "Return only the requested structured research payload. "
        "Cite only supplied evidence IDs and abstain when insufficient."
    ),
    user_format="{payload_json}",
)
"""The default prompt, byte-for-byte the system text `StructuredSignalAgent` sent before
`V2-P7-012`, plus the rendering that produced the same user message."""

STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT: Final[PromptTemplate] = PromptTemplate(
    name="structured-signal/v2-json-object-examples",
    system=(
        "Return exactly one json object and nothing else -- no prose and no code fences. "
        'The object has two keys: "signal" (one structured research signal) and "rationale" '
        "(one short sentence explaining it). The signal has subject, as_of, direction (one of "
        '"bullish", "bearish", "neutral", "abstain"), strength, confidence, horizon, '
        "evidence_ids, confirmation_conditions, invalidation_conditions and risk_flags. Cite "
        "only supplied evidence IDs; the evidence facts in the user payload are the only facts "
        "to reason about, and a directional signal must cite at least one of them. If the "
        "evidence cannot support a direction, abstain with strength 0 and an empty "
        "evidence_ids. Two examples follow, one scored and one abstaining, with placeholder "
        "values that are not facts: "
        '{"signal": {"subject": "000001.SZ", "as_of": "2026-01-05T07:00:00+00:00", '
        '"direction": "bullish", "strength": 0.2, "confidence": 0.6, "horizon": "5d", '
        '"evidence_ids": ["ev-placeholder-1"], "confirmation_conditions": [], '
        '"invalidation_conditions": [], "risk_flags": []}, '
        '"rationale": "One short sentence citing the placeholder evidence."} '
        "and "
        '{"signal": {"subject": "000001.SZ", "as_of": "2026-01-05T07:00:00+00:00", '
        '"direction": "abstain", "strength": 0.0, "confidence": 0.0, "horizon": "5d", '
        '"evidence_ids": [], "confirmation_conditions": [], '
        '"invalidation_conditions": [], "risk_flags": []}, '
        '"rationale": "No usable evidence was supplied."}'
    ),
    user_format="{payload_json}",
)
"""The `json_object` prompt (`V2-P7-012` Task 2.1), for a vendor that accepts no schema.

Distinct from V1 in exactly the ways that mode needs: the system text contains the word "json"
and worked JSON objects showing the whole answer shape. Fix round 1 (F13) changed the second
worked object into two -- one scored, one abstaining -- with neutral placeholder values, because
a lone abstention example at temperature 0 could anchor a model toward abstaining and bias the
abstention rate C reports; the artifact name was bumped with it since no prompt version was bound
yet. V1 remains the default; a caller opts in by passing this template, and
`StructuredSignalAgent(include_facts=True)` is what fills the user payload with the evidence facts
the examples refer to.
"""
