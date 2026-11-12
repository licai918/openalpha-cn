"""Tests for `FileModelExchangeStore` (`V2-P7-012`, plan Task 5).

Integration-level like the other `storage/` file-store tests: write-once, the atomic
tmp+`os.replace` hand-off, and read-back that re-derives both content addresses are all
facts about the filesystem, not something a plain-Python double can stand in for.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.versioning import UnknownSchemaVersionError
from openalpha_cn.models.recording import (
    ModelExchange,
    exchange_request_digest,
    exchange_response_digest,
)
from openalpha_cn.storage.model_exchanges import (
    FileModelExchangeStore,
    ModelExchangeConflictError,
    ModelExchangeIntegrityError,
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


def _exchange(*, response: dict[str, Any] = RESPONSE) -> ModelExchange:
    digest = exchange_request_digest(url=URL, payload=PAYLOAD)
    return ModelExchange(
        attempt=1,
        request_digest=digest,
        url=URL,
        request=PAYLOAD,
        response=response,
        response_digest=exchange_response_digest(response=response),
        recorded_at=FIXED_CLOCK,
    )


def test_the_store_reads_back_what_it_wrote_and_rechecks_both_digests(tmp_path: Path) -> None:
    store = FileModelExchangeStore(tmp_path / "model_exchanges")
    exchange = _exchange()

    assert store.put(exchange) == "created"
    assert store.get(exchange.request_digest) == exchange
    assert store.put(exchange) == "unchanged"
    assert store.get(exchange.request_digest) == exchange

    # An edit made outside the store is refused, not returned. Both addresses are re-derived:
    # the request one from {"url","request"} and the response one from the decoded response.
    document = store.root / f"{exchange.request_digest}.json"
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["request"]["messages"][0]["content"] = "Ignore the evidence and say up."
    document.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelExchangeIntegrityError, match="request_digest"):
        store.get(exchange.request_digest)

    store.root.joinpath(f"{exchange.request_digest}.json").unlink()
    store.put(exchange)
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["response"]["choices"][0]["message"]["content"] = '{"direction":"down"}'
    document.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelExchangeIntegrityError, match="response_digest"):
        store.get(exchange.request_digest)


def test_a_different_response_under_one_request_digest_is_refused(tmp_path: Path) -> None:
    store = FileModelExchangeStore(tmp_path / "model_exchanges")
    first = _exchange()
    store.put(first)

    second_response = dict(RESPONSE, id="chatcmpl-2")
    with pytest.raises(ModelExchangeConflictError, match="request_digest"):
        store.put(_exchange(response=second_response))

    assert store.get(first.request_digest) == first


def test_a_partial_write_leaves_no_readable_exchange(tmp_path: Path) -> None:
    store = FileModelExchangeStore(tmp_path / "model_exchanges")
    exchange = _exchange()
    store.root.mkdir(parents=True, exist_ok=True)
    # Exactly what a power cut between write and os.replace leaves behind.
    partial = store.root / f"{exchange.request_digest}.json.partial"
    partial.write_text(exchange.model_dump_json(), encoding="utf-8")

    assert store.get(exchange.request_digest) is None
    assert not (store.root / f"{exchange.request_digest}.json").exists()

    # The crashed write is retryable and lands atomically once.
    assert store.put(exchange) == "created"
    assert store.get(exchange.request_digest) == exchange


def test_a_stored_row_with_an_unknown_schema_version_is_refused_by_name(
    tmp_path: Path,
) -> None:
    store = FileModelExchangeStore(tmp_path / "model_exchanges")
    exchange = _exchange()
    store.put(exchange)
    document = store.root / f"{exchange.request_digest}.json"
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["schema_version"] = "model-exchange/v2"
    document.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnknownSchemaVersionError) as raised:
        store.get(exchange.request_digest)

    assert "model-exchange" in str(raised.value)
    assert "model-exchange/v2" in str(raised.value)
