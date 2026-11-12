"""Content-addressed model exchanges: every request recorded once, replayed without a call.

The contract of this module is one sentence: **a model request is identified by its bytes and
answered by what was recorded under them.** The request digest is sha256 over the canonical
JSON of `{"url", "payload", "attempt"}` -- headers are deliberately *excluded*, because the
header
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

## The attempt number is a storage key, not part of the question (fix round 1)

A structured agent retries a model answer it could not use. The same prompt sent again must get
a *fresh* answer -- otherwise `max_attempts` is inert, and a stored answer that failed
validation is replayed once per attempt. So the 1-based attempt number joins the request digest
as its own field: attempt 1 and attempt 2 are two exchanges, each recorded once, and a replay
walks the same sequence. It is **not** added to the payload sent to the vendor: the vendor is
asked the same bytes every time, and a recorded exchange whose text began to carry a local
retry counter would no longer be a record of the question.

**A store hit is not a bill.** `RecordingJsonTransport.last_answer_was_replayed` is true when
the answer came from the store rather than from the inner transport, and the provider accounts
usage only when it is false. A repeated request in record mode -- a rerun, a crash-and-retry, a
second agent sending identical bytes -- therefore costs no second usage row, and an agent retry
at a new attempt number is a real second request that is billed once.
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
    attempt: int = Field(ge=1)
    """Which attempt of this request this exchange is, 1-based; part of the address only.

    A retry is a different stored exchange with the same vendor payload (see the module
    docstring). The field is stored so a read-back can re-derive `request_digest` from the
    document itself; the contract has never been written outside this batch's tests, so adding
    it before any stored row exists needs no migration and stays `model-exchange/v1`.
    """
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    """sha256 of canonical JSON `{"url", "payload", "attempt"}`; headers are excluded."""
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


def exchange_request_digest(*, url: str, payload: Mapping[str, Any], attempt: int = 1) -> str:
    """The content address of one request: url, the whole payload and the attempt number.

    Headers are excluded by design (the credential is not part of the question). `attempt` is
    the agent's 1-based retry number; it is a storage key only and never reaches the vendor
    payload. Attempt 1 is the default so a caller that does not retry says nothing new.
    """
    if attempt < 1:
        raise ValueError(f"attempt is 1-based; got {attempt}")
    canonical = canonical_json_bytes({"attempt": attempt, "payload": dict(payload), "url": url})
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
        self.last_answer_was_replayed = False
        """True when the last answer came from the store, so the provider must not bill it.

        Set on every call: a store hit (or an unchanged re-put of a hit) leaves it true and a
        request that actually reached the inner transport leaves it false. The provider reads
        it right after a successful call; see this module's docstring.
        """

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Answer from the store when this attempt is held; otherwise record with `inner`.

        The optional `attempt` keeps this object usable as a plain `JsonTransport`: the provider
        passes the agent's attempt through `post_json_for_attempt` when it has one, and a direct
        caller gets attempt 1.
        """
        return self._answer(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
        )

    def post_json_for_attempt(
        self,
        *,
        attempt: int,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """The provider's entry point: the attempt number is part of the lookup address.

        A retry is a different exchange with the same vendor payload, so a retry after an
        invalid answer gets a fresh answer instead of replaying the one that was rejected.
        """
        return self._answer(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
        )

    def _answer(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
        attempt: int,
    ) -> dict[str, Any]:
        request_digest = exchange_request_digest(url=url, payload=payload, attempt=attempt)
        held = self.store.get(request_digest)
        if held is not None:
            self.last_answer_was_replayed = True
            return self._replayed(held)
        if self.inner is None:
            raise ModelExchangeNotStoredError(
                f"no model exchange is stored under request digest {request_digest} for {url} "
                f"(attempt {attempt}). Replay mode has no transport by design: record the "
                "exchange first, or run with a live transport; answering an unrecorded request "
                "would require the network call this mode exists to remove"
            )
        response = self.inner.post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        exchange = ModelExchange(
            attempt=attempt,
            request_digest=request_digest,
            url=url,
            request=payload,
            response=response,
            response_digest=exchange_response_digest(response=response),
            recorded_at=self.clock(),
        )
        self.store.put(exchange)
        self.last_answer_was_replayed = False
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
