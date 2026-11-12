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
            fields = [
                field_name
                for _, field_name, _, _ in string.Formatter().parse(self.user_format)
                if field_name is not None
            ]
        except ValueError as error:
            raise ValueError(
                f"prompt {self.name!r}: user_format must contain exactly one "
                f"{{payload_json}} field; its braces are not a legal template ({error})"
            ) from error
        if fields != ["payload_json"]:
            raise ValueError(
                f"prompt {self.name!r}: user_format must contain exactly one "
                f"{{payload_json}} field; it declares {fields!r}"
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
