"""DeepSeek endpoint configuration (`V2-P7-012`).

The batch's requirement is one **named** vendor endpoint whose model identity, base URL and
prices are verified from the vendor's own documentation by a human -- not inferred by a coding
agent, and not committed as if it already had been. So this module holds two things: the
`VendorEndpoint` value type the composition root wires, and `DEEPSEEK_V4_1_FLASH`, a
**placeholder** whose `verified_on` is `None`.

While `verified_on` is `None`, `runtime.composition.llm_provider` refuses a *live* provider --
the one that would spend a real request -- and names this constant in the refusal. Replay and an
injected (fake) transport are still allowed, which is what lets the batch's tests run with zero
network calls and without a credential.

## What a human fills in, and where from

Read <https://api-docs.deepseek.com/> and fill:

- `model` -- the exact model id the API accepts;
- `base_url` -- the OpenAI-compatible base URL (the provider appends `/chat/completions`);
- `structured_output_mode` -- whether this endpoint supports `json_schema` or only
  `json_object`;
- `max_output_tokens` -- the vendor's cap this deployment wants to use;
- `input_price_per_million` / `output_price_per_million` -- the vendor's prices, as `Decimal`.

Then set `verified_on` to the date of that reading. `api_key_env` is an environment-variable
*name*, never a value: `OpenAICompatibleProvider` reads it from the process environment, and the
recording transport excludes headers from the stored exchange, so the key never reaches disk.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from pydantic import JsonValue


@dataclass(frozen=True, slots=True, kw_only=True)
class VendorEndpoint:
    """One vendor endpoint as configuration: identity, limits, prices and verification date.

    `name` is the human-facing constant name (`"DEEPSEEK_V4_1_FLASH"`). It exists so the
    composition root's refusal can name the endpoint it refused: `llm_provider` is generic over
    endpoints, and a Python variable name is not a value it could otherwise recover.
    """

    name: str
    provider_id: str
    model: str
    base_url: str
    api_key_env: str
    """The environment variable's NAME; the key is never stored or accepted as a value here."""
    structured_output_mode: Literal["json_schema", "json_object"]
    temperature: float
    max_output_tokens: int
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    documentation_url: str
    verified_on: date | None
    extra_payload: Mapping[str, JsonValue] = field(default_factory=dict)
    """Endpoint-specific JSON merged last into every request payload (`V2-P7-012` follow-up).

    This is where a vendor dial that the provider has no first-class keyword for lives -- for
    DeepSeek that is the thinking switch, `{"thinking": {"type": "disabled"}}`, which must be
    sent explicitly because thinking mode is on by default and makes `temperature` inert.

    Empty by default **on purpose**: `OpenAICompatibleProvider` merges this mapping after its own
    required keys and adds nothing when it is empty, so an endpoint that sets no extra payload
    sends exactly the bytes `V2-P7-012` pinned. The provider refuses a mapping that would
    override a key it owns (`RESERVED_PAYLOAD_KEYS`).
    """

    def __post_init__(self) -> None:
        """Freeze the endpoint-specific JSON: no caller may mutate what an endpoint sends.

        Fix round 1, F9: the field's mutable `dict` default and the provider's shallow copy left
        the nested vendor dial shared with whoever built the endpoint. A deep copy severs that, so
        mutating the caller's mapping after construction cannot change what this endpoint sends.
        The provider additionally freezes its own copy (`MappingProxyType`), so no downstream
        caller can mutate the request either; a plain dict here keeps `dataclasses.asdict` (used
        by the endpoint tests) working.
        """
        object.__setattr__(self, "extra_payload", copy.deepcopy(dict(self.extra_payload)))


DEEPSEEK_V4_1_FLASH: Final[VendorEndpoint] = VendorEndpoint(
    name="DEEPSEEK_V4_1_FLASH",
    provider_id="deepseek",
    # PLACEHOLDERS. A human fills `model`, `base_url`, `structured_output_mode`,
    # `max_output_tokens`, both prices and -- once the reading confirms the thinking switch --
    # `extra_payload` (for this vendor, `{"thinking": {"type": "disabled"}}`) from
    # https://api-docs.deepseek.com/, then sets `verified_on` to the date of that reading.
    # Deliberately not guessed here: a plausible real-looking model id or URL is how an
    # unverified endpoint comes to look verified.
    model="PLACEHOLDER-set-from-api-docs.deepseek.com",
    base_url="https://placeholder.invalid/deepseek/v1",
    api_key_env="DEEPSEEK_API_KEY",
    structured_output_mode="json_schema",
    temperature=0.0,
    max_output_tokens=4096,
    input_price_per_million=Decimal("0"),
    output_price_per_million=Decimal("0"),
    documentation_url="https://api-docs.deepseek.com/",
    verified_on=None,
)
"""DeepSeek V4.1 Flash, as a placeholder until a human verifies the vendor's documented values.

`verified_on=None` is the load-bearing field: it is what makes a live call refuse before a
request is spent, and it is the only assertion that separates configuration from a claim.
"""
