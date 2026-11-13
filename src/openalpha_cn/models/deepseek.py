"""DeepSeek endpoint configuration (`V2-P7-012`).

The batch's requirement is one **named** vendor endpoint whose model identity, base URL and
prices are verified from the vendor's own documentation by a human -- not inferred by a coding
agent, and not committed as if it already had been. So this module holds two things: the
`VendorEndpoint` value type the composition root wires, and `DEEPSEEK_V4_1_FLASH`, the one named
DeepSeek endpoint, verified by a human's reading on 2026-11-12 (gated step L2).

## Where the values come from

A human read these official pages on 2026-11-12 (newest changelog entry 2026-10-17):

- <https://api-docs.deepseek.com/>
- <https://api-docs.deepseek.com/zh-cn/updates>
- <https://api-docs.deepseek.com/quick_start/pricing>
- <https://api-docs.deepseek.com/guides/json_mode>
- <https://api-docs.deepseek.com/guides/thinking_mode>
- <https://api-docs.deepseek.com/api/create-chat-completion>
- <https://api-docs.deepseek.com/quick_start/token_usage>

What the reading settled: the OpenAI-compatible base URL has no `/v1`; the API model id is
`deepseek-flash` (a moving alias -- `DeepSeek-V4.1-Flash-1017` at reading time); JSON output is
`response_format={"type": "json_object"}` only, with no `json_schema`; thinking mode is on by
default and makes `temperature` inert, so the payload disables it explicitly; and the prices are
off-peak USD per 1M tokens (the evening run, Beijing >= 18:30, is off-peak; peak is 2x).

`verified_on` is the load-bearing field: `runtime.composition.llm_provider` refuses a *live*
provider while it is `None`, and names the endpoint in the refusal. A verification date does not
by itself make a model call; it records that a human read the values. Each stored exchange's
`response.model` / `system_fingerprint` is the per-exchange record of which model behind the
moving alias actually answered.

`api_key_env` is an environment-variable *name*, never a value: `OpenAICompatibleProvider` reads
it from the process environment, and the recording transport excludes headers from the stored
exchange, so the key never reaches disk.
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
    # Values read from the official DeepSeek documentation pages listed in this module's
    # docstring on 2026-11-12 (gated step L2). Prices are off-peak USD per 1M tokens: the
    # evening run (Beijing >= 18:30) is off-peak, and peak is 2x these. `deepseek-flash` is a
    # moving alias (V4.1-Flash-1017 at reading time), so each stored exchange's `response.model`
    # / `system_fingerprint` -- not this constant -- is the record of which model answered.
    model="deepseek-flash",
    base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    structured_output_mode="json_object",
    temperature=0.0,
    max_output_tokens=4096,
    input_price_per_million=Decimal("0.15"),
    output_price_per_million=Decimal("0.60"),
    documentation_url="https://api-docs.deepseek.com/",
    verified_on=date(2026, 11, 12),
    extra_payload={"thinking": {"type": "disabled"}},
)
"""DeepSeek V4.1 Flash, verified from the vendor's documentation on 2026-11-12 (gated step L2).

`verified_on` is what separates configuration from a claim: `llm_provider` refuses a live
provider while it is `None`, and setting it is a human's act, not a coding agent's. The model id
is a moving alias, so `response.model` / `system_fingerprint` stay the per-exchange record of
what answered.
"""
