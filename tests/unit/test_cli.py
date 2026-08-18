import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import app
from openalpha_cn.providers.akshare import AKShareProvider
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


def test_doctor_json_reports_config_ok_when_the_environment_is_valid() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["checks"]["config"]["ok"] is True


def test_doctor_json_reports_invalid_openalpha_env_as_a_config_finding_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 2: `doctor` exists to diagnose a broken environment, so a malformed
    `OPENALPHA_*` value must surface as one of its own findings -- in valid JSON --
    rather than crash before it can report anything. Reproduces the reviewer's exact
    repro (`OPENALPHA_MAX_REQUEST_BYTES=not-a-number`) directly against `doctor()`'s
    own body via `CliRunner`, independent of whether `main()`'s eager load is fixed.
    """
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    result = runner.invoke(app, ["doctor", "--json"])

    payload = json.loads(result.stdout)  # must not raise json.JSONDecodeError
    assert payload["checks"]["config"]["ok"] is False
    assert "OPENALPHA_MAX_REQUEST_BYTES" in payload["checks"]["config"]["error"]
    assert payload["status"] == "error"


def test_doctor_human_output_reports_invalid_openalpha_env_as_a_config_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    result = runner.invoke(app, ["doctor"])

    assert "FAIL config" in result.stdout
    assert "OPENALPHA_MAX_REQUEST_BYTES" in result.stdout


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


def test_probe_report_logs_provider_failure_category_and_provider_id_not_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`_probe_report`'s `ProviderFailure` branch (V2-P0B-007) is one of the four
    call sites this task instruments. The log record must carry the closed-`Literal`
    `category`, the `provider_id`, and the `dataset` -- never `str(failure)` (the
    failure's own `message`), which could carry a credential or URL query string
    (see `ProviderFailure.__init__`, which stores `message` as the exception's own
    `args[0]`)."""
    secret = "sk-probe-report-log-must-not-leak-11223"

    def _fails_with_secret_message(request: ProviderRequest) -> ProviderBatch:
        raise ProviderFailure(
            provider_id="fake.limited",
            category="rate_limit",
            message=f"too many calls, token={secret}",
            retryable=True,
        )

    limited_provider = _StubProvider(
        provider_id="fake.limited",
        supported_datasets=("widgets",),
        on_fetch=_fails_with_secret_message,
    )
    caplog.set_level(logging.INFO, logger="openalpha_cn.cli")

    result = cli._probe_report(limited_provider)

    assert result == {"widgets": "rate_limit"}
    assert secret not in caplog.text
    matching = [record for record in caplog.records if record.message == "provider_probe_failed"]
    assert len(matching) == 1
    assert matching[0].category == "rate_limit"  # type: ignore[attr-defined]
    assert matching[0].provider_id == "fake.limited"  # type: ignore[attr-defined]
    assert matching[0].dataset == "widgets"  # type: ignore[attr-defined]


_PROBE_LEAK_SCRIPT = """
from openalpha_cn import cli
from openalpha_cn.providers.base import ProviderMetadata


class _LeakingProvider:
    @property
    def metadata(self):
        return ProviderMetadata(
            provider_id="fake.leaking",
            display_name="fake.leaking",
            source_license="test-only",
            redistribution="unknown",
            credential_env_vars=(),
            supported_datasets=("widgets",),
            caching_policy="prohibited",
            rate_limit="n/a",
            freshness="n/a",
            failure_semantics="n/a",
        )

    def fetch(self, request):
        raise ValueError("unexpected failure while calling with token=__SENTINEL__")


cli._default_providers = lambda: [_LeakingProvider()]
cli.app(["doctor", "--probe", "--json"], standalone_mode=True)
"""


def test_doctor_probe_real_cli_path_never_leaks_a_non_provider_failure_exception() -> None:
    """A provider whose `fetch()` raises a plain exception must never leak it.

    This reproduces the reviewer's exact repro: the real CLI entry point
    (`cli.app([...], standalone_mode=True)`), not `CliRunner`, is what let an
    uncaught exception's message escape via Python's default traceback
    printer. Run in a subprocess so the process's real stdout/stderr -- the
    channel that actually leaked -- can be inspected directly.
    """
    sentinel = "sk-scratch-leak-check-REALCLI-999888"
    script = _PROBE_LEAK_SCRIPT.replace("__SENTINEL__", sentinel)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["providers"]["fake.leaking"]["probe"] == {"widgets": "probe_error"}


def test_doctor_probe_reports_chainlin_not_configured_without_base_url_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAINLIN_API_BASE_URL", raising=False)

    def _forbidden_get_json(self: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("ChainLin transport must not be invoked when base URL is unset")

    monkeypatch.setattr(
        "openalpha_cn.providers.chainlin.UrllibChainLinTransport.get_json",
        _forbidden_get_json,
    )

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["providers"]["chainlin.api"]["probe"] == {
        "broken_board": "not_configured",
        "capital": "not_configured",
        "consecutive_board": "not_configured",
        "daily": "not_configured",
        "disclosure": "not_configured",
        "limit_up": "not_configured",
        "quote": "not_configured",
        "theme": "not_configured",
    }


def test_doctor_probe_runs_chainlin_normally_when_base_url_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAINLIN_API_BASE_URL", "https://custom-chainlin-base.example/v1")
    monkeypatch.setenv("CHAINLIN_API_KEY", "probe-only-token")
    captured_urls: list[str] = []

    def _fake_get_json(self: Any, *, url: str, **kwargs: Any) -> dict[str, Any]:
        captured_urls.append(url)
        return {"schema_version": "chainlin-data/v1", "records": []}

    monkeypatch.setattr(
        "openalpha_cn.providers.chainlin.UrllibChainLinTransport.get_json",
        _fake_get_json,
    )

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    probe = payload["providers"]["chainlin.api"]["probe"]
    assert probe == dict.fromkeys(probe, "ok")
    assert len(captured_urls) == 8
    assert all(url.startswith("https://custom-chainlin-base.example/v1") for url in captured_urls)


class _FakeAKShareFrame:
    """Minimal `FrameLike` fixture: an empty result, just enough to reach `fetch()`."""

    empty = True

    def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return []


class _FakeAKShareClient:
    """A fake wired through `AKShareProvider`'s documented `client=` seam.

    Deliberately not the permissive `_StubProvider` used elsewhere in this file:
    `_StubProvider.fetch` accepts whatever `ProviderRequest` it is given, so it could
    never have caught `_probe_report` calling `AKShareProvider.fetch()` with an empty
    `subjects` tuple. This fake sits behind the *real* `AKShareProvider.fetch()`, which
    enforces "exactly one subject" itself, so the request that reaches
    `stock_zh_a_hist` is only ever the one `_probe_report` actually built.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def stock_zh_a_hist(self, **kwargs: str) -> _FakeAKShareFrame:
        self.calls.append(kwargs)
        return _FakeAKShareFrame()


def test_doctor_probe_supplies_the_subject_akshare_fetch_requires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `_probe_report` used to call every provider's `fetch()` with
    `subjects=()`. `AKShareProvider.fetch()` requires exactly one subject and raises
    `ProviderFailure(category="configuration", ...)` before it ever imports `akshare`,
    so `--probe` reported `configuration` for `stock_zh_a_hist` in every environment,
    regardless of whether AKShare actually works -- confirmed against a real
    `akshare==1.18.77` install that the probe still reported `configuration` for.

    This uses the real `AKShareProvider` (via its `client=` seam), not a stub that
    accepts any request shape, so the assertion only passes if `_probe_report` builds
    a request the real contract accepts.
    """
    client = _FakeAKShareClient()
    provider = AKShareProvider(client=client)
    monkeypatch.setattr(cli, "_default_providers", lambda: [provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["providers"]["akshare.research"]["probe"] == {"stock_zh_a_hist": "ok"}
    assert len(client.calls) == 1
    assert client.calls[0]["symbol"], "the probe must supply a real, non-empty subject"


# --- V2-P0B-006: config object + in-process `.env` loading -------------------------------


def test_serve_host_port_precedence_cli_arg_beats_env_beats_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`openalpha serve`'s bind address/port used to hardcode `127.0.0.1:8000` with no
    env fallback at all, making `OPENALPHA_HOST`/`OPENALPHA_PORT` dead variables for
    this command. Precedence must now be: `--host`/`--port` > `OPENALPHA_HOST`/
    `OPENALPHA_PORT` > the same `127.0.0.1:8000` default as before.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda target, **kwargs: calls.append({"target": target, **kwargs})
    )
    monkeypatch.delenv("OPENALPHA_HOST", raising=False)
    monkeypatch.delenv("OPENALPHA_PORT", raising=False)

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert calls[-1]["host"] == "127.0.0.1"
    assert calls[-1]["port"] == 8000

    monkeypatch.setenv("OPENALPHA_HOST", "0.0.0.0")
    monkeypatch.setenv("OPENALPHA_PORT", "9100")
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert calls[-1]["host"] == "0.0.0.0"
    assert calls[-1]["port"] == 9100

    result = runner.invoke(app, ["serve", "--host", "192.168.1.5", "--port", "7000"])
    assert result.exit_code == 0, result.stdout
    assert calls[-1]["host"] == "192.168.1.5"
    assert calls[-1]["port"] == 7000
    assert calls[-1]["target"] == "openalpha_cn.api.app:app"


def test_serve_rejects_a_non_numeric_openalpha_port_with_a_named_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda target, **kwargs: None)
    monkeypatch.setenv("OPENALPHA_PORT", "not-a-port")

    result = runner.invoke(app, ["serve"])

    assert result.exit_code != 0
    assert "OPENALPHA_PORT" in result.output


def test_cli_runner_invocation_never_auto_loads_dotenv_unlike_the_real_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The isolation boundary this task's hard constraint relies on: `cli.main()` (the
    real console-script entry point registered as `openalpha` in `pyproject.toml`)
    loads `.env`; the Typer `app` object `CliRunner` drives directly in every test in
    this file does not. If it did, every `CliRunner`-based test here -- run from
    whatever directory `pytest` happens to be invoked from -- could silently pick up
    this repository's real, gitignored root `.env` and its real credentials.

    Proven directly: chdir into a `tmp_path` carrying a `.env` with a recognizable
    sentinel variable, invoke a subcommand through `app`/`CliRunner` exactly like
    every other test in this file does, and confirm the sentinel never reaches
    `os.environ` or the command's own output.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    sentinel = "cli-runner-must-not-auto-load-this-89213"
    (tmp_path / ".env").write_text(f"TUSHARE_TOKEN={sentinel}\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    assert "TUSHARE_TOKEN" not in os.environ
    payload = json.loads(result.stdout)
    tushare_credentials = payload["providers"]["tushare.pro"]["credentials"]
    assert tushare_credentials == [{"env_var": "TUSHARE_TOKEN", "status": "missing"}]


def test_main_entrypoint_loads_dotenv_before_dispatching_to_doctor(tmp_path: Path) -> None:
    """The positive half of the isolation proof above: the real console-script entry
    point (`cli.main()`, invoked the way the installed `openalpha` command invokes
    it) *does* load a `.env` sitting in its current working directory, and a
    credential declared only there reaches `doctor`'s report. Run in a fresh
    subprocess (own `cwd`, own `os.environ`) so this can never contaminate -- or be
    contaminated by -- the pytest process's own environment.
    """
    sentinel = "subprocess-real-entrypoint-token-55014"
    (tmp_path / ".env").write_text(f"TUSHARE_TOKEN={sentinel}\n", encoding="utf-8")
    script = (
        "import sys\n"
        'sys.argv = ["openalpha", "doctor", "--json"]\n'
        "from openalpha_cn import cli\n"
        "cli.main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    payload = json.loads(result.stdout)
    tushare_credentials = payload["providers"]["tushare.pro"]["credentials"]
    assert tushare_credentials == [{"env_var": "TUSHARE_TOKEN", "status": "present"}]


def test_main_entrypoint_rejects_an_invalid_log_level_with_a_named_error(
    tmp_path: Path,
) -> None:
    """`main()` -- the real console-script entry point -- configures structured
    logging once before dispatching to any subcommand (V2-P0B-007), the same way it
    already loads `.env`. An invalid `OPENALPHA_LOG_LEVEL` must fail loudly, naming
    the variable, exactly like `serve`'s existing `OPENALPHA_PORT` handling
    (`test_serve_rejects_a_non_numeric_openalpha_port_with_a_named_config_error`
    above) -- never a bare pydantic traceback, and never a silent fallback that
    would leave a scheduled job's logs permanently misconfigured.
    """
    (tmp_path / ".env").write_text("OPENALPHA_LOG_LEVEL=VERBOSE\n", encoding="utf-8")
    script = (
        'import sys\nsys.argv = ["openalpha", "version"]\n'
        "from openalpha_cn import cli\ncli.main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "OPENALPHA_LOG_LEVEL" in result.stderr


def test_main_entrypoint_version_succeeds_despite_an_unrelated_invalid_openalpha_env(
    tmp_path: Path,
) -> None:
    """Finding 2's exact regression repro: `main()` used to call `load_config()` --
    validating *every* `OPENALPHA_*` field atomically -- before dispatch, so an
    invalid `OPENALPHA_MAX_REQUEST_BYTES` aborted `version`, a command with no
    relationship to config at all. `main()` now only needs a validated log level
    before dispatch (see `load_log_level()`), so an unrelated field's validity must
    never affect it -- unlike the test directly above, which pins that an invalid
    `OPENALPHA_LOG_LEVEL` *itself* must still abort loudly.
    """
    script = (
        'import sys\nsys.argv = ["openalpha", "version"]\n'
        "from openalpha_cn import cli\ncli.main()\n"
    )
    env = {**os.environ, "OPENALPHA_MAX_REQUEST_BYTES": "not-a-number"}

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OpenAlpha CN 1.0.0"


def test_main_entrypoint_doctor_json_stays_valid_json_despite_invalid_openalpha_env(
    tmp_path: Path,
) -> None:
    """Finding 2's second repro: `doctor --json` must always emit valid JSON on
    stdout, even when an `OPENALPHA_*` value is malformed -- a monitoring script
    parsing this output must get a structured error, never a decode error from an
    empty stdout.
    """
    script = (
        'import sys\nsys.argv = ["openalpha", "doctor", "--json"]\n'
        "from openalpha_cn import cli\ncli.main()\n"
    )
    env = {**os.environ, "OPENALPHA_MAX_REQUEST_BYTES": "not-a-number"}

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    payload = json.loads(result.stdout)  # must not raise -- this is the core regression
    assert payload["checks"]["config"]["ok"] is False
    assert "OPENALPHA_MAX_REQUEST_BYTES" in payload["checks"]["config"]["error"]


def test_main_entrypoint_doctor_human_output_is_not_empty_despite_invalid_openalpha_env(
    tmp_path: Path,
) -> None:
    """Finding 2's first repro: plain `doctor` must still print its report, not exit
    silently, when an `OPENALPHA_*` value is malformed."""
    script = (
        'import sys\nsys.argv = ["openalpha", "doctor"]\nfrom openalpha_cn import cli\ncli.main()\n'
    )
    env = {**os.environ, "OPENALPHA_MAX_REQUEST_BYTES": "not-a-number"}

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.stdout.strip() != ""
    assert "FAIL config" in result.stdout
    assert "OPENALPHA_MAX_REQUEST_BYTES" in result.stdout


_PROVIDER_FAILURE_LOG_SCRIPT = """
import sys
sys.argv = ["openalpha", "doctor", "--probe", "--json"]

from openalpha_cn import cli
from openalpha_cn.providers.base import ProviderFailure, ProviderMetadata


class _FailingProvider:
    @property
    def metadata(self):
        return ProviderMetadata(
            provider_id="fake.failing",
            display_name="fake.failing",
            source_license="test-only",
            redistribution="unknown",
            credential_env_vars=(),
            supported_datasets=("widgets",),
            caching_policy="prohibited",
            rate_limit="n/a",
            freshness="n/a",
            failure_semantics="n/a",
        )

    def fetch(self, request):
        raise ProviderFailure(
            provider_id="fake.failing",
            category="authentication",
            message="token=__SENTINEL__ rejected by upstream",
            retryable=False,
        )


cli._default_providers = lambda: [_FailingProvider()]
cli.main()
"""


def test_main_entrypoint_logs_provider_probe_failure_without_leaking_the_failure_message(
    tmp_path: Path,
) -> None:
    """The sentinel-driven leak proof through the real entry point (V2-P0B-007):
    `cli.main()` -- not `CliRunner`, not the bare `app` object -- is the only code
    path that actually calls `configure_logging()`, attaching a real `StreamHandler`
    to this process's real stderr. This proves the *logged* `ProviderFailure` branch
    (`cli.py::_probe_report`) is safe even under real, end-to-end logging
    configuration: the resulting structured log line on stderr carries `category`/
    `provider_id`/`dataset`, never the failure's own `message` -- even though that
    message is exactly where the sentinel lives. Companion to
    `test_doctor_probe_real_cli_path_never_leaks_a_non_provider_failure_exception`
    above, which covers the *unlogged* generic-exception branch through the same
    real entry point.

    **The expected return code changed from 0 to 1 in `V2-P1-018`, and that is the
    fix rather than a concession to it.** This provider's probe raises
    `authentication` -- a credential the endpoint rejected, the one outcome in
    `cli.PROBE_FAILURE_STATES` -- and `doctor --json` used to `return` before its own
    exit check, so the rendering a scheduled job parses reported that failure in the
    payload and exited 0 anyway. Everything this test was written to prove is
    unchanged and still asserted below: the sentinel reaches neither stream, and the
    structured log line carries the category rather than the message.
    """
    sentinel = "sk-scratch-leak-check-MAINLOG-224466"
    script = _PROVIDER_FAILURE_LOG_SCRIPT.replace("__SENTINEL__", sentinel)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1, result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["providers"]["fake.failing"]["probe"] == {"widgets": "authentication"}

    log_lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert log_lines, "expected the ProviderFailure branch to emit a structured log line"
    records = [json.loads(line) for line in log_lines]
    matching = [record for record in records if record.get("event") == "provider_probe_failed"]
    assert len(matching) == 1
    record = matching[0]
    assert record["category"] == "authentication"
    assert record["provider_id"] == "fake.failing"
    assert record["dataset"] == "widgets"
    assert sentinel not in json.dumps(record)


# --- V2-P1-018 R12: the probe reaches every declared dataset ----------------------------------


class _RecordingTushareTransport:
    """Answers any Tushare request with a well-formed, empty response, and records it.

    `fields` is taken from the descriptor's own `checked_response_fields` so the schema check
    passes for every dataset without this double having to know fifteen response shapes, and
    `has_more=False` satisfies the descriptors that demand the flag. Empty `items` is enough:
    `fetch()` answers `no_data` and `fetch_panel()` answers `no_data`, and the probe records
    both as `ok` -- an accepted request is the whole question it is asking.
    """

    def __init__(self) -> None:
        self.datasets: list[str] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        from openalpha_cn.providers.tushare import TUSHARE_DATASETS

        name = str(payload["api_name"])
        self.datasets.append(name)
        (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": list(descriptor.checked_response_fields),
                "items": [],
                "has_more": False,
            },
        }


def test_doctor_probe_sends_one_request_for_every_declared_tushare_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R12's measurement, inverted into an assertion.

    The product review timed the probe's own log and found `index_weight`..`fina_indicator`
    -- seven datasets -- landing within **one microsecond** of each other
    (14:59:46.509232 -> .509394): no network round trip happened for any of them. Nine of the
    fifteen sent nothing at all, for two different reasons that the report rendered as the same
    word, `configuration`:

    - four (`stock_basic`, `namechange`, `index_classify`, `index_member_all`) declare
      `serves_evidence_plane=False`, so `fetch()` refuses them by design -- and the probe only
      ever called `fetch()`. `stock_basic` returns 6,217 rows on the plane it is actually
      served on;
    - five (`index_weight` and the four financial-statement endpoints) were handed the empty
      subject tuple their own `params_builder` refuses.

    So this asserts the count and the *set*, by equality against the declared datasets rather
    than by a threshold: a probe that reached fourteen of fifteen is the same defect one dataset
    smaller, and it is the one nobody would notice.
    """
    from openalpha_cn.providers.tushare import TUSHARE_DATASETS, TushareProvider

    transport = _RecordingTushareTransport()
    provider = TushareProvider(token=SECRET_TOKEN, transport=transport)
    monkeypatch.setattr(cli, "_default_providers", lambda: [provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    declared = [entry.dataset for entry in TUSHARE_DATASETS]
    assert transport.datasets == declared
    payload = json.loads(result.stdout)
    assert payload["providers"]["tushare.pro"]["probe"] == dict.fromkeys(declared, "ok")
    assert payload["probe_failures"] == []


def test_doctor_probe_exits_non_zero_when_the_credential_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit code is the deliverable, and it could not previously say no.

    Two independent reasons it could not, both closed here. `doctor --json` returned before its
    own exit check, so the machine-readable rendering always exited 0; and a rejected Tushare
    token answers `code=40101`, which the provider classified `upstream` because the only code
    mapped to `authentication` was one nothing has ever observed.
    """
    from openalpha_cn.providers.tushare import TushareProvider

    class _RejectingTransport:
        def post(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"code": 40101, "msg": "the token is not right", "data": None}

    provider = TushareProvider(
        token=SECRET_TOKEN, transport=_RejectingTransport(), sleep=lambda _: None
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert set(payload["providers"]["tushare.pro"]["probe"].values()) == {"authentication"}
    assert len(payload["probe_failures"]) == 16
    assert payload["status"] == "error"


def test_doctor_probe_does_not_fail_the_command_for_an_endpoints_own_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the rule, and the half that keeps the exit code usable.

    "This account cannot reach this interface" is the *content* of the report Implementation
    Decision 33 asks for, not a reason to refuse to publish it -- so an `upstream` refusal is
    reported per dataset and the command still exits 0. Without this half, one withdrawn
    endpoint would fail every scheduled `doctor --probe` on every machine.
    """
    from openalpha_cn.providers.tushare import TushareProvider

    class _RefusingTransport:
        def post(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"code": 40001, "msg": "no permission for this interface", "data": None}

    provider = TushareProvider(
        token=SECRET_TOKEN, transport=_RefusingTransport(), attempts=1, sleep=lambda _: None
    )
    monkeypatch.setattr(cli, "_default_providers", lambda: [provider])

    result = runner.invoke(app, ["doctor", "--json", "--probe"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert set(payload["providers"]["tushare.pro"]["probe"].values()) == {"upstream"}
    assert payload["probe_failures"] == []


def test_doctor_json_exits_non_zero_when_the_report_it_printed_is_not_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early `return` this issue removed, pinned so it cannot come back.

    `doctor --json` printed a payload with `"status": "error"` and exited 0, which is the exact
    shape of the empty success this repository keeps booking: a check whose verdict is in the
    body and whose exit code always says yes. The config path is used rather than the probe
    path so that the two causes are pinned separately.
    """
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["checks"]["config"]["ok"] is False
