import json
from datetime import UTC, datetime
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import app
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)

runner = CliRunner()

NOW = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

SECRET_TOKEN = "sk-doctor-test-token-must-not-leak-77219"


class _StubProvider:
    """A minimal `DataProvider` test double with a scripted `fetch` outcome."""

    def __init__(
        self,
        *,
        provider_id: str,
        supported_datasets: tuple[str, ...],
        credential_env_vars: tuple[str, ...] = (),
        on_fetch: Any = None,
    ) -> None:
        self._metadata = ProviderMetadata(
            provider_id=provider_id,
            display_name=provider_id,
            source_license="test-only",
            redistribution="unknown",
            credential_env_vars=credential_env_vars,
            supported_datasets=supported_datasets,
            caching_policy="prohibited",
            rate_limit="n/a",
            freshness="n/a",
            failure_semantics="n/a",
        )
        self._on_fetch = on_fetch

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        if self._on_fetch is None:
            raise AssertionError("transport must not be called when --probe is off")
        return self._on_fetch(request)  # type: ignore[no-any-return]


def test_version_reports_project_name_and_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OpenAlpha CN 1.0.0"


def test_doctor_json_reports_required_runtime_checks() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["checks"]["python"]["ok"] is True
    assert payload["checks"]["timezone"]["ok"] is True


def test_doctor_human_output_names_each_runtime_check() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "PASS python" in result.stdout
    assert "PASS timezone" in result.stdout


def test_doctor_json_reports_provider_capability_declarations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _harmless_fetch(request: ProviderRequest) -> ProviderBatch:
        # Never called with the default `--probe`-off doctor invocation below; only
        # present so this test stays valid even if a future change enables probing
        # unconditionally, instead of colliding with the probe-guard test.
        return ProviderBatch(
            provider_id="fake.capability",
            request=request,
            fetched_at=NOW,
            status="no_data",
            no_data_reason="unused fixture fetch",
        )

    stub_provider = _StubProvider(
        provider_id="fake.capability",
        supported_datasets=("alpha", "beta"),
        on_fetch=_harmless_fetch,
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [stub_provider])

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["providers"]["fake.capability"]["capabilities"] == {
        "provider_id": "fake.capability",
        "redistribution": "unknown",
        "rate_limit": "n/a",
        "supported_datasets": ["alpha", "beta"],
    }


def test_doctor_json_reports_present_credential_env_vars_as_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    credentials = payload["providers"]["tushare.pro"]["credentials"]
    assert {"env_var": "TUSHARE_TOKEN", "status": "present"} in credentials


def test_doctor_missing_credential_is_reported_as_warning_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    credentials = payload["providers"]["tushare.pro"]["credentials"]
    assert {"env_var": "TUSHARE_TOKEN", "status": "missing"} in credentials
    assert any("TUSHARE_TOKEN" in warning for warning in payload["warnings"])

    human = runner.invoke(app, ["doctor"])
    assert human.exit_code == 0
    assert "WARN credential tushare.pro TUSHARE_TOKEN missing" in human.stdout
    assert "FAIL" not in human.stdout
    assert "ERROR" not in human.stdout


def test_doctor_never_leaks_credential_value_in_human_or_json_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("CHAINLIN_API_KEY", SECRET_TOKEN)

    human = runner.invoke(app, ["doctor"])
    machine = runner.invoke(app, ["doctor", "--json"])

    assert human.exit_code == 0
    assert machine.exit_code == 0
    assert SECRET_TOKEN not in human.stdout
    assert SECRET_TOKEN not in machine.stdout


def test_doctor_probe_defaults_off_and_never_invokes_provider_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raising_provider = _StubProvider(
        provider_id="fake.raising",
        supported_datasets=("widgets",),
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [raising_provider])

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "probe" not in payload["providers"]["fake.raising"]


def test_doctor_probe_reports_ok_when_fetch_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    def _succeed(request: ProviderRequest) -> ProviderBatch:
        return ProviderBatch(
            provider_id="fake.succeeding",
            request=request,
            fetched_at=NOW,
            status="no_data",
            no_data_reason="fixture has no rows",
        )

    succeeding_provider = _StubProvider(
        provider_id="fake.succeeding",
        supported_datasets=("widgets",),
        on_fetch=_succeed,
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [succeeding_provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["providers"]["fake.succeeding"]["probe"] == {"widgets": "ok"}


def test_doctor_probe_reports_provider_failure_category_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _rate_limited(request: ProviderRequest) -> ProviderBatch:
        raise ProviderFailure(
            provider_id="fake.limited",
            category="rate_limit",
            message="too many calls",
            retryable=True,
        )

    limited_provider = _StubProvider(
        provider_id="fake.limited",
        supported_datasets=("widgets",),
        on_fetch=_rate_limited,
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [limited_provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["providers"]["fake.limited"]["probe"] == {"widgets": "rate_limit"}
