"""`V2-P7-012` Task 7: DeepSeek is one named endpoint wired through the composition root.

Three properties are asserted here and each has a failure it exists to stop:

- an **unverified** endpoint refuses a live provider *by name* -- the placeholder model id and
  base URL in `models/deepseek.py` must not quietly spend a real request before a human has
  filled them from the vendor's documentation;
- **replay** needs no key and makes no transport call -- the whole point of the exchange store
  is that a replayed run can be re-executed offline;
- a fake-transport run **records usage once** and a replay adds none -- the usage row is billed
  once, when the request was actually made, and the recording is idempotent by request id.
"""

import ast
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH, VendorEndpoint
from openalpha_cn.models.openai_compatible import (
    ModelConfigurationError,
    OpenAICompatibleProvider,
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
    """The placeholder must not spend a real request before a human fills it in."""
    assert DEEPSEEK_V4_1_FLASH.verified_on is None

    with pytest.raises(ModelConfigurationError) as raised:
        llm_provider(
            DEEPSEEK_V4_1_FLASH,
            runtime_dir=tmp_path,
            clock=lambda: CLOCK,
            replay=False,
        )

    message = str(raised.value)
    assert "DEEPSEEK_V4_1_FLASH" in message
    assert "verified" in message.lower()
    assert DEEPSEEK_V4_1_FLASH.documentation_url in message

    # A verified endpoint is allowed to record, and a fake transport is always allowed (the
    # next test records through one while the shipped constant is still unverified).
    verified = replace(DEEPSEEK_V4_1_FLASH, verified_on=date(2026, 1, 1))
    provider = llm_provider(verified, runtime_dir=tmp_path, clock=lambda: CLOCK, replay=False)
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


def test_the_endpoint_carries_the_limits_the_batch_was_asked_for() -> None:
    """Placeholders are visible as placeholders: prices zero, verification unset, mode declared."""
    endpoint = DEEPSEEK_V4_1_FLASH

    assert isinstance(endpoint, VendorEndpoint)
    assert endpoint.provider_id == "deepseek"
    assert endpoint.structured_output_mode == "json_schema"
    assert endpoint.temperature == 0.0
    assert endpoint.max_output_tokens > 0
    assert endpoint.input_price_per_million == 0
    assert endpoint.output_price_per_million == 0
    assert endpoint.verified_on is None
