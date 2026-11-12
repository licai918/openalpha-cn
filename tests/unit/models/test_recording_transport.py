"""Tests for the content-addressed model-exchange recorder (`V2-P7-012`, plan Task 5).

The store here is an in-memory double that satisfies `ModelExchangeStore`; the real
`FileModelExchangeStore` is exercised in `tests/integration/storage/test_model_exchange_store.py`.
What these tests pin is the *transport* behaviour: one lookup
before any call, replay reads only the store, a stored request can never be reached by a
different credential, and no path in this module opens a socket.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.models.recording import (
    ModelExchange,
    ModelExchangeNotStoredError,
    RecordingJsonTransport,
    exchange_request_digest,
    exchange_response_digest,
)

FIXED_CLOCK = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
URL = "https://example.invalid/v1/chat/completions"
PAYLOAD: dict[str, Any] = {
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "Analyze."}],
}
RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-1",
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    "choices": [{"message": {"content": '{"direction":"up"}'}}],
}


class MemoryModelExchangeStore:
    """In-memory `ModelExchangeStore`: what the transport needs, nothing the store needs."""

    def __init__(self) -> None:
        self.exchanges: dict[str, ModelExchange] = {}
        self.puts: list[ModelExchange] = []

    def get(self, request_digest: str) -> ModelExchange | None:
        return self.exchanges.get(request_digest)

    def put(self, exchange: ModelExchange) -> str:
        self.puts.append(exchange)
        held = self.exchanges.get(exchange.request_digest)
        if held is None:
            self.exchanges[exchange.request_digest] = exchange
            return "created"
        assert held.response_digest == exchange.response_digest
        return "unchanged"


class CountingJsonTransport:
    """`JsonTransport` double that counts calls instead of opening a socket."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def _replay(store: MemoryModelExchangeStore) -> RecordingJsonTransport:
    return RecordingJsonTransport(
        store=store,
        inner=None,
        clock=lambda: FIXED_CLOCK,
    )


def _record(
    store: MemoryModelExchangeStore, inner: CountingJsonTransport
) -> RecordingJsonTransport:
    return RecordingJsonTransport(
        store=store,
        inner=inner,
        clock=lambda: FIXED_CLOCK,
    )


def test_replay_reads_the_stored_response_and_never_calls_the_inner_transport() -> None:
    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)
    _record(store, inner).post_json(
        url=URL,
        headers={"Authorization": "Bearer live"},
        payload=PAYLOAD,
        timeout_seconds=30.0,
    )
    assert len(inner.calls) == 1

    replayed = _replay(store).post_json(
        url=URL,
        headers={"Authorization": "Bearer not-even-a-real-key"},
        payload=PAYLOAD,
        timeout_seconds=30.0,
    )

    assert replayed == RESPONSE
    assert len(inner.calls) == 1, "replay must not call the inner transport"
    assert len(store.puts) == 1, "replay must not write a second exchange"


def test_replay_of_an_unrecorded_request_is_refused_by_name() -> None:
    store = MemoryModelExchangeStore()
    digest = exchange_request_digest(url=URL, payload=PAYLOAD)
    transport = _replay(store)

    with pytest.raises(ModelExchangeNotStoredError) as raised:
        transport.post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)

    assert digest in str(raised.value)
    assert URL in str(raised.value)
    assert store.puts == []


def test_the_request_digest_ignores_headers_so_the_key_never_reaches_the_store() -> None:
    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)
    _record(store, inner).post_json(
        url=URL,
        headers={"Authorization": "Bearer sk-the-real-key"},
        payload=PAYLOAD,
        timeout_seconds=30.0,
    )

    stored = next(iter(store.exchanges.values()))
    serialized = stored.model_dump_json()
    assert "sk-the-real-key" not in serialized
    assert "Authorization" not in serialized
    # The same request with a different credential is the same address and replays.
    replayed = _replay(store).post_json(
        url=URL,
        headers={"Authorization": "Bearer sk-another-key"},
        payload=PAYLOAD,
        timeout_seconds=30.0,
    )
    assert replayed == RESPONSE
    assert exchange_request_digest(url=URL, payload=PAYLOAD) == stored.request_digest


def test_a_second_record_of_the_same_request_returns_the_stored_exchange() -> None:
    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)
    transport = _record(store, inner)

    first = transport.post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)
    second = transport.post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)

    assert first == second == RESPONSE
    assert len(inner.calls) == 1, "the lookup must precede the inner call"
    assert len(store.puts) == 1
    assert len(store.exchanges) == 1


def test_recording_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_a_socket(*args: object, **kwargs: object) -> object:
        raise AssertionError("recording must never open a socket")

    monkeypatch.setattr("openalpha_cn.models.openai_compatible.urlopen", refuse_a_socket)
    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)

    recorded = _record(store, inner).post_json(
        url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0
    )
    replayed = _replay(store).post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)

    assert recorded == replayed == RESPONSE
    assert len(inner.calls) == 1


def test_the_response_digest_covers_the_decoded_response_not_the_wire_bytes() -> None:
    reordered = {
        "choices": RESPONSE["choices"],
        "usage": RESPONSE["usage"],
        "id": RESPONSE["id"],
    }

    assert exchange_response_digest(response=RESPONSE) == exchange_response_digest(
        response=reordered
    )
    changed = dict(RESPONSE, id="chatcmpl-2")
    assert exchange_response_digest(response=RESPONSE) != exchange_response_digest(response=changed)


# --- the attempt number (fix round 1, finding 1) ------------------------------------------------


def test_the_attempt_number_joins_the_request_address_and_never_the_payload() -> None:
    """A retry is a different question; the vendor is asked the same bytes."""
    first = exchange_request_digest(url=URL, payload=PAYLOAD, attempt=1)
    second = exchange_request_digest(url=URL, payload=PAYLOAD, attempt=2)
    assert first != second
    assert exchange_request_digest(url=URL, payload=PAYLOAD) == first, "attempt 1 is the default"

    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)
    _record(store, inner).post_json(
        url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0, attempt=2
    )

    stored = next(iter(store.exchanges.values()))
    assert stored.attempt == 2
    assert stored.request_digest == second
    assert [call["payload"] for call in inner.calls] == [PAYLOAD], (
        "the attempt is a storage key; the vendor payload is unchanged"
    )
    # The same bytes at attempt 2 replay; attempt 1 was never recorded.
    assert (
        _replay(store).post_json(
            url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0, attempt=2
        )
        == RESPONSE
    )
    with pytest.raises(ModelExchangeNotStoredError):
        _replay(store).post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)


def test_a_record_mode_store_hit_reports_that_it_did_not_call_the_inner_transport() -> None:
    """The provider accounts usage only for a request that actually reached the vendor."""
    store = MemoryModelExchangeStore()
    inner = CountingJsonTransport(RESPONSE)
    transport = _record(store, inner)

    transport.post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)
    assert transport.last_answer_was_replayed is False

    transport.post_json(url=URL, headers={}, payload=PAYLOAD, timeout_seconds=30.0)
    assert transport.last_answer_was_replayed is True
    assert len(inner.calls) == 1
