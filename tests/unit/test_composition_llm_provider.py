"""`V2-P7-012` Task 7: DeepSeek is one named endpoint wired through the composition root.

Three properties are asserted here and each has a failure it exists to stop:

- an **unverified** endpoint (a copy of the shipped one with `verified_on=None`, as any future
  endpoint starts) refuses a live provider *by name* -- an unconfirmed model id and base URL must
  not quietly spend a real request before a human has filled them from the vendor's
  documentation. The shipped `DEEPSEEK_V4_1_FLASH` itself was verified at gated step L2
  (2026-11-12 reading), and `test_the_endpoint_carries_the_verified_documented_values` pins it;
- **replay** needs no key and makes no transport call -- the whole point of the exchange store
  is that a replayed run can be re-executed offline;
- a fake-transport run **records usage once** and a replay adds none -- the usage row is billed
  once, when the request was actually made, and the recording is idempotent by request id.
"""

import ast
import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.agents.base import AgentContext
from openalpha_cn.agents.model import ModelProviderFailure, StructuredSignalAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH, VendorEndpoint
from openalpha_cn.models.openai_compatible import (
    ModelConfigurationError,
    OpenAICompatibleProvider,
    UrllibJsonTransport,
)
from openalpha_cn.models.recording import RecordingJsonTransport
from openalpha_cn.runtime.composition import (
    MODEL_EXCHANGE_DIRECTORY,
    build_storage,
    llm_provider,
    model_exchange_store_at,
    model_usage_store_at,
)
from openalpha_cn.storage.model_exchanges import FileModelExchangeStore
from openalpha_cn.storage.models import SQLiteModelUsageStore

COMPOSITION: Path = (
    Path(__file__).resolve().parents[2] / "src" / "openalpha_cn" / "runtime" / "composition.py"
)
CLOCK: datetime = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
LATER: datetime = datetime(2026, 11, 13, 15, 0, tzinfo=UTC)
RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-deepseek-1",
    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    "choices": [{"message": {"content": '{"direction":"up"}'}}],
}
GENERATE: dict[str, Any] = {
    "system": "Return only the requested structured research payload.",
    "user": "Analyze.",
    "schema": {"type": "object", "properties": {"direction": {"type": "string"}}},
}


class FakeJsonTransport:
    """`JsonTransport` double: counts calls, records headers, opens no socket."""

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


def test_an_unverified_endpoint_refuses_a_live_provider_by_name(tmp_path: Path) -> None:
    """An unverified copy -- as any new endpoint starts -- must not spend a real request."""
    assert DEEPSEEK_V4_1_FLASH.verified_on == date(2026, 11, 12)
    unverified = replace(DEEPSEEK_V4_1_FLASH, verified_on=None)

    with pytest.raises(ModelConfigurationError) as raised:
        llm_provider(
            unverified,
            runtime_dir=tmp_path,
            clock=lambda: CLOCK,
            replay=False,
        )

    message = str(raised.value)
    assert "DEEPSEEK_V4_1_FLASH" in message
    assert "verified" in message.lower()
    assert DEEPSEEK_V4_1_FLASH.documentation_url in message

    # The verified shipped endpoint is allowed to record, and a fake transport is always
    # allowed (the next test records through one).
    provider = llm_provider(
        DEEPSEEK_V4_1_FLASH, runtime_dir=tmp_path, clock=lambda: CLOCK, replay=False
    )
    assert provider.metadata.credential_env_vars == ("DEEPSEEK_API_KEY",)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert isinstance(provider.transport, RecordingJsonTransport)


def test_replay_needs_no_key_and_makes_zero_transport_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    fake = FakeJsonTransport(RESPONSE)
    recorded = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )
    assert recorded.generate_json(**GENERATE) == {"direction": "up"}
    assert len(fake.calls) == 1

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    replay = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: LATER,
        replay=True,
    )

    assert replay.metadata.credential_env_vars == ()
    assert replay.generate_json(**GENERATE) == {"direction": "up"}
    assert len(fake.calls) == 1, "a replay must not reach the transport"


def test_a_fake_transport_run_records_usage_once_and_a_replay_adds_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single recorded request costs one row and its replay adds none.

    It does not measure a *repeated* record-mode request (it never repeats one); that gap --
    review 1's finding 1 -- is pinned by
    `test_two_identical_record_mode_calls_write_one_usage_row_and_call_once` below.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    fake = FakeJsonTransport(RESPONSE)
    record = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )
    record.generate_json(**GENERATE)

    usage = model_usage_store_at(tmp_path).list()
    assert len(usage) == 1
    assert usage[0].request_id == "chatcmpl-deepseek-1"
    assert usage[0].provider_id == DEEPSEEK_V4_1_FLASH.provider_id
    assert usage[0].model == DEEPSEEK_V4_1_FLASH.model

    replay = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: LATER,
        replay=True,
    )
    assert replay.generate_json(**GENERATE) == {"direction": "up"}

    assert model_usage_store_at(tmp_path).list() == usage, (
        "a replay was already billed when it was recorded; appending a second row with a later "
        "occurred_at for the same request id is how a replay would re-charge the same call"
    )
    assert len(fake.calls) == 1


def test_the_key_is_named_by_environment_variable_and_never_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The constant carries the variable's *name*; the value must never reach a document."""
    assert DEEPSEEK_V4_1_FLASH.api_key_env == "DEEPSEEK_API_KEY"
    declared = str(asdict(DEEPSEEK_V4_1_FLASH))
    assert "DEEPSEEK_API_KEY" in declared
    assert "sk-" not in declared

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-the-real-key")
    fake = FakeJsonTransport(RESPONSE)
    provider = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )
    provider.generate_json(**GENERATE)

    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-the-real-key"
    stored = "".join(
        document.read_text(encoding="utf-8")
        for document in (tmp_path / MODEL_EXCHANGE_DIRECTORY).glob("*.json")
    )
    assert "sk-the-real-key" not in stored
    assert "Authorization" not in stored
    assert all(
        "sk-the-real-key" not in row.model_dump_json()
        for row in model_usage_store_at(tmp_path).list()
    )


def _top_level_functions_calling(path: Path, name: str) -> tuple[str, ...]:
    """The top-level functions whose bodies call `name` (`f(...)` or `x.f(...)`).

    The syntax tree rather than the source bytes, for `test_composition_openers.py`'s stated
    reason: the names also appear in docstrings, and a text search would count those.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            function = call.func
            callee = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else None
            )
            if callee == name:
                found.append(node.name)
                break
    return tuple(found)


def test_build_storage_opens_the_usage_store_through_the_opener(tmp_path: Path) -> None:
    """Hard rule 5: one source for the path, and `build_storage` uses it.

    Equal paths alone would not prove the opener is used -- `build_storage` writing the same
    literal by hand would pass that. So the syntax tree pins both directions: the class is
    constructed only inside `model_usage_store_at`, and the only two callers of that opener are
    `llm_provider` (record mode) and `build_storage` (the container field).
    """
    storage = build_storage(runtime_dir=tmp_path, clock=lambda: CLOCK)

    assert isinstance(storage.model_usage_store, SQLiteModelUsageStore)
    assert storage.model_usage_store.path == model_usage_store_at(tmp_path).path
    assert storage.model_usage_store.path == tmp_path / "state.sqlite3"

    assert _top_level_functions_calling(COMPOSITION, "SQLiteModelUsageStore") == (
        "model_usage_store_at",
    )
    assert set(_top_level_functions_calling(COMPOSITION, "model_usage_store_at")) == {
        "llm_provider",
        "build_storage",
    }

    exchange_store = model_exchange_store_at(tmp_path)
    assert isinstance(exchange_store, FileModelExchangeStore)
    assert exchange_store.root == tmp_path / MODEL_EXCHANGE_DIRECTORY
    assert _top_level_functions_calling(COMPOSITION, "FileModelExchangeStore") == (
        "model_exchange_store_at",
    )
    assert _top_level_functions_calling(COMPOSITION, "model_exchange_store_at") == ("llm_provider",)


def test_the_endpoint_carries_the_verified_documented_values() -> None:
    """The shipped endpoint was verified at L2 from the 2026-11-12 reading; pin every value."""
    from decimal import Decimal

    endpoint = DEEPSEEK_V4_1_FLASH

    assert isinstance(endpoint, VendorEndpoint)
    assert endpoint.provider_id == "deepseek"
    assert endpoint.model == "deepseek-flash"
    assert endpoint.base_url == "https://api.deepseek.com"
    assert endpoint.structured_output_mode == "json_object"
    assert endpoint.temperature == 0.0
    assert endpoint.max_output_tokens == 4096
    assert endpoint.input_price_per_million == Decimal("0.15")
    assert endpoint.output_price_per_million == Decimal("0.60")
    assert dict(endpoint.extra_payload) == {"thinking": {"type": "disabled"}}
    assert endpoint.documentation_url == "https://api-docs.deepseek.com/"
    assert endpoint.verified_on == date(2026, 11, 12)


# --- fix round 1: usage is billed only for a request that reached the vendor ------------------


class QueuedJsonTransport:
    """`JsonTransport` double that answers a scripted sequence and counts calls."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        return self.responses[len(self.calls) - 1]


def _response(*, answer: dict[str, Any], request_id: str, prompt_tokens: int = 3) -> dict[str, Any]:
    return {
        "id": request_id,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 2,
            "total_tokens": prompt_tokens + 2,
        },
        "choices": [{"message": {"content": json.dumps(answer)}}],
    }


def _evidence_item() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="theme",
        timeline=Timeline(
            event_time=CLOCK,
            available_time=CLOCK,
            ingested_time=CLOCK,
            revision_time=CLOCK,
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


def _valid_answer(item: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "signal": {
            "subject": "000001.SZ",
            "as_of": CLOCK.isoformat(),
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


def _invalid_answer(item: EvidenceSnapshot) -> dict[str, Any]:
    answer = _valid_answer(item)
    answer["signal"]["subject"] = "999999.SH"
    return answer


def _agent(provider: OpenAICompatibleProvider, *, max_attempts: int = 2) -> StructuredSignalAgent:
    return StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=max_attempts,
    )


def _context(item: EvidenceSnapshot) -> AgentContext:
    return AgentContext(
        run_id="run_model",
        subject="000001.SZ",
        as_of=CLOCK,
        evidence=(item,),
    )


def test_two_identical_record_mode_calls_write_one_usage_row_and_call_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1, F1(a): a store hit is not a bill.

    The advancing clock is what makes this a regression test: the second use of the same
    request is answered from the store, so no second usage row may be written (a second row
    with the same provider request id and a later `occurred_at` used to raise).
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    times = iter((CLOCK, LATER))
    fake = FakeJsonTransport(RESPONSE)
    record = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: next(times),
        replay=False,
        transport=fake,
    )

    assert record.generate_json(**GENERATE) == {"direction": "up"}
    assert record.generate_json(**GENERATE) == {"direction": "up"}

    assert len(fake.calls) == 1, "the second call is a store hit"
    usage = model_usage_store_at(tmp_path).list()
    assert len(usage) == 1
    assert usage[0].request_id == "chatcmpl-deepseek-1"
    # The recording transport takes the first clock value for `recorded_at` and the provider
    # the next for the usage row; what matters is one row and no conflict.
    assert usage[0].occurred_at in {CLOCK, LATER}


def test_an_unverified_endpoint_refuses_an_explicit_urllib_transport(tmp_path: Path) -> None:
    """Fix round 1, F5: an injected real transport is still a real request."""
    unverified = replace(DEEPSEEK_V4_1_FLASH, verified_on=None)
    with pytest.raises(ModelConfigurationError) as raised:
        llm_provider(
            unverified,
            runtime_dir=tmp_path,
            clock=lambda: CLOCK,
            replay=False,
            transport=UrllibJsonTransport(),
        )

    message = str(raised.value)
    assert "DEEPSEEK_V4_1_FLASH" in message
    assert "verified" in message.lower()


def test_a_retry_after_an_invalid_answer_gets_a_fresh_answer_and_the_real_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1, F1(b): each attempt is its own exchange; a retry is not inert.

    Both recorded answers are invalid for the agent's context, so the run still fails -- but
    it fails with the agent's own validation reason after two vendor calls, not with a usage
    conflict whose text hides what the model actually answered.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    item = _evidence_item()
    fake = QueuedJsonTransport(
        [
            _response(answer=_invalid_answer(item), request_id="chatcmpl-1"),
            _response(answer=_invalid_answer(item), request_id="chatcmpl-2"),
        ]
    )
    record = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )

    with pytest.raises(ModelProviderFailure) as raised:
        _agent(record).analyze(_context(item))

    message = str(raised.value)
    assert "2 attempts" in message
    assert "does not match context" in message
    assert "request_id conflicts" not in message
    assert len(fake.calls) == 2
    assert len(model_usage_store_at(tmp_path).list()) == 2
    exchanges = sorted(model_exchange_store_at(tmp_path).root.glob("*.json"))
    assert len(exchanges) == 2, "each attempt is its own stored exchange"
    attempts = {json.loads(path.read_text(encoding="utf-8"))["attempt"] for path in exchanges}
    assert attempts == {1, 2}


def test_the_recorded_retry_sequence_replays_both_attempts_with_zero_inner_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1, F1: replay reproduces the attempt sequence, not just the first answer."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    item = _evidence_item()
    fake = QueuedJsonTransport(
        [
            _response(answer=_invalid_answer(item), request_id="chatcmpl-1"),
            _response(answer=_invalid_answer(item), request_id="chatcmpl-2"),
        ]
    )
    record = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )
    with pytest.raises(ModelProviderFailure) as recorded:
        _agent(record).analyze(_context(item))

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    replay = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: LATER,
        replay=True,
    )

    with pytest.raises(ModelProviderFailure) as replayed:
        _agent(replay).analyze(_context(item))

    assert str(replayed.value) == str(recorded.value)
    assert len(fake.calls) == 2, "replay must reach neither the fake nor a real transport"


# --- V2-P7-012 Task 2.1: the endpoint's extra payload goes through the composition root -----


def test_llm_provider_passes_the_endpoint_extra_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one assembly passes the endpoint's mapping through, and replay addresses it.

    `llm_provider` is the only `src/` construction site of the provider (hard rule 5), so this is
    where the endpoint's `extra_payload` -- the DeepSeek thinking switch -- must reach the vendor
    request. It is part of the payload, therefore part of the exchange digest: a replay of the
    same endpoint finds the recorded answer with zero inner calls.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    endpoint = replace(
        DEEPSEEK_V4_1_FLASH,
        verified_on=date(2026, 1, 1),
        extra_payload={"thinking": {"type": "disabled"}},
    )
    fake = FakeJsonTransport(RESPONSE)
    record = llm_provider(
        endpoint,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
    )

    assert record.generate_json(**GENERATE) == {"direction": "up"}
    payload = fake.calls[0]["payload"]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["model"] == endpoint.model
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    # The verified endpoint declares DeepSeek's only supported mode: json_object, not json_schema.
    assert endpoint.structured_output_mode == "json_object"
    assert payload["response_format"]["type"] == "json_object"

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    replay = llm_provider(endpoint, runtime_dir=tmp_path, clock=lambda: LATER, replay=True)
    assert replay.generate_json(**GENERATE) == {"direction": "up"}
    assert len(fake.calls) == 1, "the extra payload is part of the request address"


# --- F2/F8 (fix round 1): the budget decorator wraps the INNER transport -----------------------


class CountingWrapper:
    """A `JsonTransport` decorator double: one counter per forwarded request, then the inner."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls += 1
        return self.inner.post_json(
            url=url, headers=headers, payload=payload, timeout_seconds=timeout_seconds
        )


def test_wrap_transport_decorates_the_inner_transport_inside_the_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook is applied to the HTTP transport, *inside* `RecordingJsonTransport`.

    A decorator there counts and refuses only the requests that would reach the vendor; a store
    hit is answered by the recorder before the decorator, which is the ordering a rerun needs.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-record")
    fake = FakeJsonTransport(RESPONSE)
    wrapped: list[Any] = []

    def wrap(inner: Any) -> Any:
        wrapped.append(inner)
        return CountingWrapper(inner)

    record = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
        wrap_transport=wrap,
    )

    assert wrapped == [fake], "the hook receives the caller's transport, not the recorder"
    assert isinstance(record.transport, RecordingJsonTransport)
    assert isinstance(record.transport.inner, CountingWrapper), "the recorder must sit outside"
    assert record.transport.inner.inner is fake

    assert record.generate_json(**GENERATE) == {"direction": "up"}
    assert record.transport.inner.calls == 1
    assert len(fake.calls) == 1

    assert record.generate_json(**GENERATE) == {"direction": "up"}
    assert record.transport.inner.calls == 1, "a store hit is answered before the decorator"
    assert len(fake.calls) == 1


def test_wrap_transport_is_not_applied_in_replay_mode(tmp_path: Path) -> None:
    """Replay has no inner transport, so there is nothing to decorate and the hook is not called."""
    wrapped: list[Any] = []

    def wrap(inner: Any) -> Any:
        wrapped.append(inner)
        return CountingWrapper(inner)

    replay = llm_provider(
        DEEPSEEK_V4_1_FLASH,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=True,
        wrap_transport=wrap,
    )

    assert replay.transport.inner is None
    assert wrapped == []


def test_wrap_transport_cannot_bypass_the_unverified_endpoint_gate(tmp_path: Path) -> None:
    """The gate is decided on the undecorated caller transport, before any wrapping."""
    unverified = replace(DEEPSEEK_V4_1_FLASH, verified_on=None)
    for supplied in (None, UrllibJsonTransport()):
        with pytest.raises(ModelConfigurationError):
            llm_provider(
                unverified,
                runtime_dir=tmp_path,
                clock=lambda: CLOCK,
                replay=False,
                transport=supplied,
                wrap_transport=CountingWrapper,
            )

    # An explicitly injected test transport is allowed while the endpoint is unverified; the
    # hook is still applied to it.
    fake = FakeJsonTransport(RESPONSE)
    provider = llm_provider(
        unverified,
        runtime_dir=tmp_path,
        clock=lambda: CLOCK,
        replay=False,
        transport=fake,
        wrap_transport=CountingWrapper,
    )
    assert isinstance(provider.transport.inner, CountingWrapper)
