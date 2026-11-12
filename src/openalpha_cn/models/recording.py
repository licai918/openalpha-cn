"""Content-addressed model exchanges: every request recorded once, replayed without a call.

The contract of this module is one sentence: **a model request is identified by its bytes and
answered by what was recorded under them.** The request digest is sha256 over the canonical
JSON of `{"url", "payload"}` -- headers are deliberately *excluded*, because the header
carrying the credential is not part of the question being asked and must never reach a stored
document; the whole payload *is* included (model, messages, `response_format`, `temperature`,
`max_tokens`), so pinning a different temperature addresses a different exchange rather than
silently replaying an answer that was generated under other settings.

`response_digest` is the sha256 of the canonical JSON of the **decoded** response, not of the
wire bytes: the transport returns a decoded object, two byte encodings of one JSON object are
one answer, and the digest has to be recomputable by a reader that only holds the parsed
document. Neither digest is a security boundary -- the store's job is identity and
write-once, not signatures; the credential boundary is that headers are never stored.

`RecordingJsonTransport` is the decorator the composition root wraps around a real
`JsonTransport` (`V2-P7-012`): record mode looks the request up first, calls the inner
transport only on a miss, stores what came back, and returns it; replay mode (`inner=None`)
looks up only and raises `ModelExchangeNotStoredError` on a miss. A replay therefore makes
zero inner calls and needs no key.
"""

import copy
import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from openalpha_cn.domain.json_value import canonical_json_bytes
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions
from openalpha_cn.models.openai_compatible import JsonTransport, ModelResponseError

MODEL_EXCHANGE_SCHEMA_VERSION: Final[Literal["model-exchange/v1"]] = "model-exchange/v1"
"""The one version of the stored document this build writes and reads."""


class ModelExchange(BaseModel):
    """One recorded model request/response pair, addressed by the request's content address."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["model-exchange/v1"] = MODEL_EXCHANGE_SCHEMA_VERSION
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    """sha256 of canonical JSON `{"url", "payload"}`; headers are excluded by design."""
    url: str = Field(min_length=1)
    request: JsonValue
    response: JsonValue
    response_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    """sha256 of canonical JSON of the decoded response; not the wire bytes."""
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


MODEL_EXCHANGE_VERSIONS: ContractVersions[ModelExchange] = ContractVersions(
    name="model-exchange",
    current_version=MODEL_EXCHANGE_SCHEMA_VERSION,
    versions={MODEL_EXCHANGE_SCHEMA_VERSION: ModelExchange},
)
"""The stored contract's version registry.

Registered as a real `ContractVersions`, not `single_version(...)`: the latter is documented
for models with *no* `schema_version` field (its registry is keyed on `None`), and a stored
row here carries `"model-exchange/v1"`, so `read_versioned` through `single_version` would
refuse the store's own documents as an unknown version. The shape matches
`RUN_MANIFEST_VERSIONS` (`domain/run.py`) for the same reason. A future
`model-exchange/v2` is an entry here plus an upgrade, not a new store.
"""


class ModelExchangeNotStoredError(LookupError):
    """Raised by replay when no exchange is stored under the request's content address."""


class ModelExchangeStore(Protocol):
    """The narrow store `RecordingJsonTransport` needs: read by address, write once."""

    def get(self, request_digest: str) -> ModelExchange | None:
        """The exchange held under `request_digest`, or `None` when nothing is held."""

    def put(self, exchange: ModelExchange) -> Literal["created", "unchanged"]:
        """Hold `exchange`; `unchanged` when an equal-response exchange is already held."""


def exchange_request_digest(*, url: str, payload: Mapping[str, Any]) -> str:
    """The content address of one request: url plus the whole payload, headers excluded."""
    canonical = canonical_json_bytes({"url": url, "payload": dict(payload)})
    return hashlib.sha256(canonical).hexdigest()


def exchange_response_digest(*, response: JsonValue) -> str:
    """The content address of one decoded response (not its wire bytes)."""
    return hashlib.sha256(canonical_json_bytes(response)).hexdigest()


class RecordingJsonTransport:
    """Record each request once and replay it; never touch a network in replay mode."""

    def __init__(
        self,
        *,
        store: ModelExchangeStore,
        inner: JsonTransport | None,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.inner = inner
        self.clock = clock

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Answer from the store when the request is held; otherwise record with `inner`.

        The lookup precedes every other step, so recording the same request twice calls the
        inner transport exactly once -- a run that repeats a prompt is answered by the first
        answer. `headers` are forwarded to `inner` and never stored: they are the credential
        channel, not part of the request's identity.
        """
        request_digest = exchange_request_digest(url=url, payload=payload)
        held = self.store.get(request_digest)
        if held is not None:
            return self._replayed(held)
        if self.inner is None:
            raise ModelExchangeNotStoredError(
                f"no model exchange is stored under request digest {request_digest} for {url}. "
                "Replay mode has no transport by design: record the exchange first, or run "
                "with a live transport; answering an unrecorded request would require the "
                "network call this mode exists to remove"
            )
        response = self.inner.post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        exchange = ModelExchange(
            request_digest=request_digest,
            url=url,
            request=payload,
            response=response,
            response_digest=exchange_response_digest(response=response),
            recorded_at=self.clock(),
        )
        outcome = self.store.put(exchange)
        if outcome == "unchanged":
            held = self.store.get(request_digest)
            if held is not None:
                return self._replayed(held)
        return copy.deepcopy(response)

    @staticmethod
    def _replayed(exchange: ModelExchange) -> dict[str, Any]:
        """Return the stored response as a fresh object, so a caller cannot edit what is held."""
        response = exchange.response
        if not isinstance(response, dict):
            raise ModelResponseError(
                "stored model exchange response is not a JSON object; a replayed answer must "
                "satisfy the provider contract"
            )
        return copy.deepcopy(cast("dict[str, Any]", response))
