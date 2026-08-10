"""Command-line entry point for OpenAlpha CN."""

import json
import logging
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final
from zoneinfo import ZoneInfo

import typer
import uvicorn

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus
from openalpha_cn.config import ConfigError, load_config, load_dotenv, load_log_level
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PriceDataError,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelBatchError
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.evidence.service import build_provider_evidence, parse_serialized_evidence
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore, PartitionRef
from openalpha_cn.panel_doctor import (
    HEALTH_SEVERITIES,
    HealthFinding,
    PanelDoctorError,
    PanelHealthReport,
    panel_health_report,
)
from openalpha_cn.panel_gate import (
    DependencyClearance,
    DependencyRequest,
    PanelGateError,
    require_datasets,
)
from openalpha_cn.panel_ingest import (
    load_suspensions,
    load_trading_calendar,
    write_adjustment_factors,
    write_daily_panel,
    write_price_limits,
    write_stock_universe,
    write_suspensions,
    write_trading_calendar,
)
from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)
from openalpha_cn.providers.chainlin import ChainLinDataProvider
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.providers.tushare import (
    TRADING_CALENDAR_DEFAULT_EXCHANGE,
    TushareProvider,
    TushareTransport,
    UrllibTushareTransport,
)
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.migrations import MigrationFailedError, read_status
from openalpha_cn.storage.parquet import read_parquet_records

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="openalpha",
    help="Evidence-traceable A-share research.",
    no_args_is_help=True,
)
evidence_app = typer.Typer(help="Build and inspect point-in-time evidence.")
app.add_typer(evidence_app, name="evidence")
research_app = typer.Typer(help="Run evidence-linked multi-agent research.")
app.add_typer(research_app, name="research")
replay_app = typer.Typer(help="Validate frozen point-in-time replay corpora.")
app.add_typer(replay_app, name="replay")
migrate_app = typer.Typer(help="Inspect and apply state.sqlite3 schema migrations.")
app.add_typer(migrate_app, name="migrate")
panel_app = typer.Typer(help="Build and examine the point-in-time panel plane.")
app.add_typer(panel_app, name="panel")
"""`V2-P1-015`'s two panel commands hang under a sub-app rather than at the top level, and the
reason is that `doctor` is already taken. The existing top-level `doctor` probes *provider*
credentials and declared capabilities; this issue's is a *panel* health report, and they answer
different questions about different things. Shadowing the first with the second would silently
change what `openalpha doctor` means for every existing caller, and renaming either is a
breaking change to a published command. Namespacing removes the collision instead of resolving
it: `openalpha doctor` and `openalpha panel doctor` coexist, and each name says which one it is.

`data-check` stays at the top level, where the roadmap names it, because it is not scoped to the
panel plane the way the other two are -- it is the question a CI job or a scheduled research run
asks before it reads anything at all.
"""


class Redistribution(StrEnum):
    """Allowed source-data redistribution states."""

    allowed = "allowed"
    restricted = "restricted"
    unknown = "unknown"


class RunMode(StrEnum):
    """Supported shared research-cycle modes."""

    live = "live"
    replay = "replay"
    backtest = "backtest"


@app.command()
def version() -> None:
    """Print the installed OpenAlpha CN version."""
    typer.echo(f"OpenAlpha CN {__version__}")


def _default_providers() -> list[DataProvider]:
    """Return the built-in providers `doctor` reports on.

    Construction never requires a credential or touches the network: each
    provider only reads its token lazily, inside `fetch()`. ChainLin's base
    URL is sourced from `CHAINLIN_API_BASE_URL`; when it is absent or empty
    the provider is still returned, with `is_configured=False`, instead of
    ever being pointed at an invented placeholder domain.
    """
    chainlin_base_url = os.environ.get("CHAINLIN_API_BASE_URL", "").strip() or None
    return [
        TushareProvider(),
        AKShareProvider(),
        ChainLinDataProvider(
            base_url=chainlin_base_url,
            api_key_env="CHAINLIN_API_KEY",
            source_license="user-held ChainLin subscription",
        ),
    ]


def _credential_report(provider: DataProvider) -> list[dict[str, str]]:
    """Report presence, never the value, of each declared credential env var."""
    return [
        {
            "env_var": env_var,
            "status": "present" if os.environ.get(env_var, "").strip() else "missing",
        }
        for env_var in provider.metadata.credential_env_vars
    ]


def _capability_report(provider: DataProvider) -> dict[str, object]:
    """Report the provider's declared licensing, rate-limit, and dataset coverage."""
    metadata = provider.metadata
    return {
        "provider_id": metadata.provider_id,
        "redistribution": metadata.redistribution,
        "rate_limit": metadata.rate_limit,
        "supported_datasets": list(metadata.supported_datasets),
    }


def _is_configured(provider: DataProvider) -> bool:
    """Return whether `provider` declares itself ready to be probed.

    Providers that need no external configuration (the common case) are
    always configured; a provider may opt out by exposing `is_configured`.
    """
    return bool(getattr(provider, "is_configured", True))


def _probe_subjects(provider: DataProvider, dataset: str) -> tuple[str, ...]:
    """Return the minimal subjects `dataset`'s `fetch()` contract requires for a probe.

    Most datasets accept an empty subject tuple, so this defaults to `()`. A provider
    whose contract requires at least one subject for a given dataset (AKShare's
    `stock_zh_a_hist`, for example) opts in by implementing an optional
    ``probe_subjects(dataset: str) -> tuple[str, ...]`` method -- the same
    `getattr`-based extension pattern `_is_configured` already uses. Because the hook
    lives on the provider, not here, adding datasets in P1 never requires touching
    this function: each provider stays responsible for the minimal input its own
    `fetch()` needs.
    """
    hook = getattr(provider, "probe_subjects", None)
    if hook is None:
        return ()
    subjects = hook(dataset)
    return tuple(str(subject) for subject in subjects)


def _probe_report(provider: DataProvider) -> dict[str, str]:
    """Make one minimal request per declared dataset and classify the outcome.

    `ProviderFailure` reports its declared, closed-`Literal` category
    verbatim. Anything else -- a bug, or a future or third-party provider
    that has not adopted the `ProviderFailure` contract -- is recorded as
    the doctor-level state `probe_error` without ever echoing the
    exception's message or repr, so this boundary can never leak a
    credential embedded in an unexpected error.
    """
    if not _is_configured(provider):
        return dict.fromkeys(provider.metadata.supported_datasets, "not_configured")
    as_of = datetime.now(UTC)
    results: dict[str, str] = {}
    for dataset in provider.metadata.supported_datasets:
        try:
            request = ProviderRequest(
                dataset=dataset,
                as_of=as_of,
                subjects=_probe_subjects(provider, dataset),
            )
            provider.fetch(request)
        except ProviderFailure as failure:
            results[dataset] = failure.category
            # Never `failure.args`/`str(failure)`: `ProviderFailure.message` (see
            # `providers/base.py`) can carry a credential or URL query string --
            # only the closed-`Literal` category, provider_id, and dataset name are
            # safe to log. This is the "Provider request failure path" call site
            # V2-P0B-007's brief names explicitly.
            logger.warning(
                "provider_probe_failed",
                extra={
                    "provider_id": failure.provider_id,
                    "category": failure.category,
                    "dataset": dataset,
                },
            )
        except Exception:
            results[dataset] = "probe_error"
        else:
            results[dataset] = "ok"
    return results


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable health report."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help=(
                "Make one live request per provider dataset. Off by default; "
                "never required in CI and never needed for the credential or "
                "capability checks."
            ),
        ),
    ] = False,
) -> None:
    """Check runtime requirements, provider credentials, and declared capabilities.

    Also validates `OPENALPHA_*` config (Finding 2 fix): `main()` only ever resolves
    `OPENALPHA_LOG_LEVEL` before dispatch (see `load_log_level()`), specifically so an
    unrelated invalid `OPENALPHA_*` field never aborts dispatch to `doctor` -- the one
    command whose entire job is diagnosing exactly that kind of broken environment.
    So `doctor` calls `load_config()` itself, here, and reports a `ConfigError` as an
    ordinary `"config"` finding (both in `--json` and human output) instead of letting
    it propagate and kill the process before anything can be reported.
    """
    timezone_ok = datetime.now().astimezone().utcoffset() is not None
    python_ok = sys.version_info >= (3, 11)
    try:
        load_config()
        config_error: str | None = None
    except ConfigError as error:
        config_error = str(error)
    config_ok = config_error is None
    checks: dict[str, dict[str, object]] = {
        "python": {
            "ok": python_ok,
            "version": platform.python_version(),
            "minimum": "3.11",
        },
        "timezone": {
            "ok": timezone_ok,
            "name": str(datetime.now().astimezone().tzinfo),
        },
        "config": {
            "ok": config_ok,
            **({"error": config_error} if config_error is not None else {}),
        },
    }

    providers: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    for provider in _default_providers():
        provider_id = provider.metadata.provider_id
        credentials = _credential_report(provider)
        warnings.extend(
            f"{provider_id}: credential {credential['env_var']} is missing"
            for credential in credentials
            if credential["status"] == "missing"
        )
        report: dict[str, object] = {
            "capabilities": _capability_report(provider),
            "credentials": credentials,
        }
        if probe:
            report["probe"] = _probe_report(provider)
        providers[provider_id] = report

    payload = {
        "status": "ok" if python_ok and timezone_ok and config_ok else "error",
        "checks": checks,
        "providers": providers,
        "warnings": warnings,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return

    for name, check in checks.items():
        line = f"{'PASS' if check['ok'] else 'FAIL'} {name}"
        if not check["ok"] and "error" in check:
            line += f": {check['error']}"
        typer.echo(line)
    for provider_id, report in providers.items():
        capabilities = report["capabilities"]
        assert isinstance(capabilities, dict)
        datasets = ",".join(capabilities["supported_datasets"])
        typer.echo(f"INFO capability {provider_id} datasets={datasets}")
        credential_reports = report["credentials"]
        assert isinstance(credential_reports, list)
        for credential in credential_reports:
            if credential["status"] == "present":
                typer.echo(f"PASS credential {provider_id} {credential['env_var']}")
            else:
                typer.echo(f"WARN credential {provider_id} {credential['env_var']} missing")
        if probe:
            probe_results = report["probe"]
            assert isinstance(probe_results, dict)
            for dataset, result in probe_results.items():
                typer.echo(f"PROBE {provider_id}/{dataset} {result}")
    if payload["status"] != "ok":
        raise typer.Exit(code=1)


@evidence_app.command("build")
def evidence_build(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    as_of: Annotated[str, typer.Option("--as-of", help="ISO-8601 point-in-time clock.")],
    source_id: Annotated[str, typer.Option("--source-id")],
    source_license: Annotated[str, typer.Option("--source-license")],
    redistribution: Annotated[
        Redistribution,
        typer.Option("--redistribution"),
    ] = Redistribution.restricted,
) -> None:
    """Build evidence from a user-owned CSV, JSON, JSONL, or Parquet file."""
    point_in_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    metadata = ProviderMetadata(
        provider_id=source_id,
        display_name=source_id,
        source_license=source_license,
        redistribution=redistribution.value,
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input-file",
        failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    )
    provider = FileProvider(path=path, metadata=metadata, parquet_reader=read_parquet_records)
    response = build_provider_evidence(provider=provider, dataset="events", as_of=point_in_time)
    typer.echo(response.model_dump_json())


_CODE_COMMIT_HELP = (
    "Defaults to the real git commit this process is running from (a literal "
    "'-dirty' suffix when the workspace has uncommitted changes), or an explicit "
    "unknown marker outside a git workspace -- never a placeholder that merely "
    "looks like a real commit. See runtime/provenance.py#resolve_code_commit."
)
_CONFIG_DIGEST_HELP = (
    "Defaults to a SHA-256 of the resolved, non-secret OpenAlphaConfig -- never all "
    "zeros. See runtime/provenance.py#compute_config_digest."
)


def _resolved_code_commit(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise resolve a real commit.

    Never touches git when the caller already supplied a value -- resolution only
    runs when `--code-commit` was genuinely omitted.
    """
    return explicit if explicit is not None else resolve_code_commit()


def _resolved_config_digest(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise digest the effective config.

    Mirrors `serve`'s `ConfigError` handling (see its docstring): `load_config()` is
    only called here, lazily, when `--config-digest` was omitted and this command
    genuinely needs the resolved config -- an unrelated invalid `OPENALPHA_*` field
    never blocks a caller that passed `--config-digest` explicitly.
    """
    if explicit is not None:
        return explicit
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    return compute_config_digest(config)


@research_app.command("run")
def research_run(
    evidence_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    run_id: Annotated[str, typer.Option("--run-id")] = "local-run",
    mode: Annotated[RunMode, typer.Option("--mode")] = RunMode.live,
    subject: Annotated[str, typer.Option("--subject")] = "",
    as_of: Annotated[str, typer.Option("--as-of")] = "",
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_CONFIG_DIGEST_HELP)
    ] = None,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 7,
) -> None:
    """Run multi-agent research from serialized EvidenceSnapshot items."""
    raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    raw_items = raw.get("items") if isinstance(raw, dict) else raw
    evidence = parse_serialized_evidence(raw_items)
    point_in_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)
    result = sdk.run_research(
        ResearchRunRequest(
            run_id=run_id,
            mode=mode.value,
            subject=subject,
            as_of=point_in_time,
            evidence=evidence,
            code_commit=_resolved_code_commit(code_commit),
            config_digest=_resolved_config_digest(config_digest),
            random_seed=random_seed,
        )
    )
    typer.echo(result.model_dump_json())


@replay_app.command("run")
def replay_run(
    corpus_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    code_commit: Annotated[
        str | None, typer.Option("--code-commit", help=_CODE_COMMIT_HELP)
    ] = None,
    config_digest: Annotated[
        str | None, typer.Option("--config-digest", help=_CONFIG_DIGEST_HELP)
    ] = None,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 7,
) -> None:
    """Run and validate a frozen replay corpus."""
    report = OpenAlphaSDK(runtime_dir=runtime_dir).replay(
        corpus=ReplayCorpus.load(corpus_path),
        code_commit=_resolved_code_commit(code_commit),
        config_digest=_resolved_config_digest(config_digest),
        random_seed=random_seed,
    )
    typer.echo(report.model_dump_json())


@migrate_app.command("status")
def migrate_status(
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable status report."),
    ] = False,
) -> None:
    """Show the current schema version and applied/pending migrations."""
    status = read_status(runtime_dir / "state.sqlite3")
    if json_output:
        payload = {
            "path": str(status.path),
            "current_version": status.current_version,
            "applied": [
                {"version": item.version, "name": item.name, "applied_at": item.applied_at}
                for item in status.applied
            ],
            "pending": [{"version": item.version, "name": item.name} for item in status.pending],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo(f"schema version: {status.current_version}")
    for applied_item in status.applied:
        typer.echo(
            f"applied  {applied_item.version} {applied_item.name} at {applied_item.applied_at}"
        )
    for pending_item in status.pending:
        typer.echo(f"pending  {pending_item.version} {pending_item.name}")


@migrate_app.command("run")
def migrate_run(
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be applied without applying it."),
    ] = False,
) -> None:
    """Apply pending schema migrations, then construct every store once.

    Goes through `build_storage()` -- the same composition root `sdk.py`/`api/app.py`
    use -- rather than calling `run_migrations()` directly. A raw `run_migrations()` call
    never constructs a store, so on a fresh `runtime_dir` a migration deferred only
    because its table doesn't exist yet (e.g. the demo migration) would stay pending
    forever, no matter how many times this command runs: nothing would ever create that
    table. Routing through `build_storage()` constructs the stores as a side effect,
    which creates the table, so the *next* invocation of this command (or the next real
    SDK/API startup against the same directory) can actually apply it.
    """
    path = runtime_dir / "state.sqlite3"
    if dry_run:
        status = read_status(path)
        if not status.pending:
            typer.echo(f"schema version {status.current_version} is up to date; nothing to do")
            return
        typer.echo(
            f"would apply {len(status.pending)} migration(s) from version {status.current_version}:"
        )
        for pending_item in status.pending:
            typer.echo(f"  {pending_item.version} {pending_item.name}")
        return
    try:
        storage = build_storage(runtime_dir=runtime_dir, clock=lambda: datetime.now(UTC))
    except MigrationFailedError as error:
        typer.echo(
            f"migration {error.version} ({error.name}) failed and was rolled back; "
            f"schema version unchanged; backup at {error.backup_path}",
            err=True,
        )
        raise typer.Exit(code=1) from error
    result = storage.migration_result
    status = read_status(path)
    if not result.applied and not status.pending:
        typer.echo(f"schema version {result.to_version} is up to date; nothing to do")
        return
    if result.applied:
        typer.echo(f"migrated {result.from_version} -> {result.to_version}")
        for applied_item in result.applied:
            typer.echo(f"  applied {applied_item.version} {applied_item.name}")
        if result.backup_path is not None:
            typer.echo(f"backup: {result.backup_path}")
    if status.pending:
        # Genuinely stuck, not "up to date": at least one migration's precondition
        # (typically a table owned by a store not yet constructed against this
        # `runtime_dir`) still isn't met. Say so instead of claiming completion.
        typer.echo(
            f"{len(status.pending)} migration(s) still pending at schema version "
            f"{status.current_version} (deferred until their preconditions are met, "
            "e.g. by normal application startup constructing the owning store):"
        )
        for pending_item in status.pending:
            typer.echo(f"  {pending_item.version} {pending_item.name}")


@app.command()
def serve(
    host: Annotated[
        str | None,
        typer.Option(help="Bind address. Defaults to OPENALPHA_HOST, then 127.0.0.1."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(help="Bind port. Defaults to OPENALPHA_PORT, then 8000.", min=1, max=65535),
    ] = None,
) -> None:
    """Serve the versioned REST API.

    Precedence for both `host` and `port`: this command's own `--host`/`--port`
    flag, when given, always wins; otherwise `OPENALPHA_HOST`/`OPENALPHA_PORT`
    (including a value merged in from `.env` by `main()`); otherwise the
    `127.0.0.1:8000` default `OpenAlphaConfig` declares.
    """
    try:
        config = load_config()
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    uvicorn.run(
        "openalpha_cn.api.app:app",
        host=host if host is not None else config.host,
        port=port if port is not None else config.port,
    )


# --- the panel plane: build, examine, gate (V2-P1-015) ----------------------------------------


class PanelExit(IntEnum):
    """Every exit code `panel build`, `panel doctor` and `data-check` issue, as one table.

    ## Why the codes are distinct rather than "zero and non-zero"

    A CI job has three different remedies available and only the exit code to pick between
    them: re-fetch the data, edit the command line, or fix the credential. Collapsing them
    into `1` makes a scheduled build indistinguishable from a typo in it.

    - `ok` -- the command answered and the answer is "nothing is wrong".
    - `unhealthy` -- the **panel** is at fault. `data-check` was refused by the gate; `panel
      doctor` found at least one `blocking` or `warning` finding; `panel build` had a batch
      refused by a write-time guard, or could not read back something it needs. Matches the
      existing `doctor`/`migrate run` convention, where 1 already means "the thing you asked
      about is not in order".
    - `bad_request` -- the **request** could not be put at all: a dataset with no declared
      cadence (`PanelDoctorError`), an override naming a dataset the request never asked about
      (`PanelGateError`), an unparseable `--as-of`, a build target outside the closed table.
      Distinct from `unhealthy` because no amount of re-fetching fixes it.
    - `provider_failure` -- the fetch never happened: authentication, quota, transport or a
      response that would not decode. Distinct again, because the panel may be perfect.

    **2 is deliberately absent.** Click raises its own `UsageError` with exit code 2 for a
    misspelled flag or a missing required option, and that is not a code this module can take
    back. Reusing it would make "you typed the command wrong" and "the gate refused you" the
    same observation. See `CLICK_USAGE_EXIT_CODE`.

    ## `panel doctor`'s own semantics, and why a notice must not reach here

    `panel doctor` exits `unhealthy` exactly when `PanelHealthReport.is_clean` is false -- that
    is, when a finding's severity is in `panel_doctor.BLOCKS_A_READ` (`blocking` or `warning`),
    and never for a `notice`.

    The argument for the notices is measurement, not taste. `V2-P1-011` drove a real
    53-security corpus end to end and `ambiguous_filing` fired on 8.15% of `income`'s filings,
    1.29% of `balancesheet`'s, 15.80% of `cashflow`'s and 13.70% of `fina_indicator`'s, with
    81.7% of `fina_indicator`'s keys carrying more than one row. A command that returned
    non-zero on those would fail on every honest financial panel, be `|| true`-d in the first
    pipeline that used it, and then protect nothing at all -- which is strictly worse than
    exiting 0, because the exit code would still *look* like a check.

    The argument for the warnings is `V2-P1-006`'s Critical. `return_path_disagreement` is a
    `warning` and it is the only code in the whole set that can see a missing factor step: it
    is what stands between a caller and the `-0.530973%` a panel with that hole answers
    against a true `+2.742251%`. A doctor that only counted `blocking` would call that panel
    healthy. Reading `is_clean` rather than re-deciding here is deliberate: the same frozenset
    drives `panel_gate.GATE_BLOCKING_SEVERITIES`, so this command and `data-check` cannot come
    to disagree about which severities matter.

    They can still disagree about a *panel*, and that is not a defect -- see
    `tests/integration/test_cli_panel.py::
    test_the_doctor_and_the_gate_disagree_on_the_same_panel_and_both_are_right`. The gate adds
    one refusal of its own, `unverified_daily_coverage`, which is not a health code because it
    is not a fault of the panel; a request that named no session is refused by the gate while
    the doctor, correctly, reports nothing wrong.
    """

    ok = 0
    unhealthy = 1
    bad_request = 3
    provider_failure = 4


CLICK_USAGE_EXIT_CODE: Final[int] = 2
"""Click's own `UsageError` exit code, recorded here so it stays reserved.

Not raised anywhere in this module. It is written down because the only way to keep `PanelExit`
unambiguous is to know which code is already spoken for by the layer underneath, and a future
addition to `PanelExit` that reached for "the next free number" would otherwise take it.
"""

PANEL_DATE_ZONE: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
"""The zone every panel date is derived in, taken from `panel/catalog.py` rather than restated.

`DependencyRequest` deliberately does not expose `date_timezone`, because a request that judged
a partition against a session boundary the partition was not written to reports a `date_gap`
that is an artefact of the question; the same reasoning applies to the loop bound this module
derives for `panel build`.
"""

PANEL_BUILD_TARGETS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        TRADING_CALENDAR_DATASET: (TRADING_CALENDAR_DATASET,),
        STOCK_BASIC_DATASET: (STOCK_BASIC_DATASET,),
        ADJ_FACTOR_DATASET: (ADJ_FACTOR_DATASET,),
        "price": (SUSPENSION_DATASET, DAILY_DATASET, DAILY_BASIC_DATASET),
        PRICE_LIMIT_DATASET: (PRICE_LIMIT_DATASET,),
    }
)
"""What `panel build --dataset X` fetches and writes, as a closed table in **build order**.

A target is a unit of work a `panel_ingest` writer accepts, which is not always one dataset.
`price` is the case that forces the distinction: `write_daily_panel` takes `daily` and
`daily_basic` together -- there is no supported way to write one partition without the other
having agreed with it -- and its `halts` argument has no default, so the halt corpus for the
same sessions has to be fetched and stored before the pair that consumes it. That is why the
smallest honest unit here is three datasets and why `--dataset daily` cannot mean anything (see
`PANEL_BUILD_COUPLED_DATASETS`), which `panel_ingest.write_daily_panel`'s own docstring names as
a constraint on this command's surface rather than an implementation detail of that one.

The order this table declares is the order `_build_panel` runs its targets in, and it is a
dependency order rather than an alphabetical one: `adj_factor`, `price` and `stk_limit` all read
the stored calendar, so `trade_cal` has to have been written first on a fresh store. The command
therefore ignores the order the `--dataset` flags arrived in, which
`tests/integration/test_cli_panel.py::
test_panel_build_runs_the_targets_in_dependency_order_and_not_in_flag_order` pins by driving them
backwards.

**Five targets, not twelve.** `namechange`, `index_weight`, the two industry datasets and the
four financial-statement endpoints have writers and are not offered here. Each is a different
fetch plan -- one announcement year per request, one index-month, one `l1_code` slice, one
`(security, period-year)` window (~5,500 requests for one market year) -- and wiring them from a
transport this issue cannot exercise would be surface with no test behind it. They are refused
by name, so a caller asking for one is told this command does not build it rather than being
given an empty success.
"""

PANEL_BUILD_COUPLED_DATASETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        DAILY_DATASET: "price",
        DAILY_BASIC_DATASET: "price",
        SUSPENSION_DATASET: "price",
    }
)
"""The datasets that exist but cannot be built alone, each pointing at the target that owns it.

Stated separately from `PANEL_BUILD_TARGETS` so the refusal can carry the *reason*. Click would
otherwise reject `--dataset daily` as an unknown choice, which reads as "this repository does
not have a daily panel" -- the opposite of the truth. `V2-P1-007`'s coupling is the reason and
the message says so.
"""

_EMPTY_SESSION_IS_ORDINARY: Final[frozenset[str]] = frozenset({SUSPENSION_DATASET})
"""Datasets for which a session serving no rows is the normal case rather than a short fetch.

Exactly one, and `panel_ingest.write_suspensions` gives the measurement: a session on which
nothing was halted and nothing resumed serves **zero** `suspend_d` rows, so an absent session is
indistinguishable from an empty one by construction. Every other dataset here publishes on every
open session, so a `no_data` batch is handed to the writer unchanged and refused there -- the
guard that knows what a missing session costs is the one that should say so.
"""

_PANEL_WRITE_REFUSALS: Final[tuple[type[Exception], ...]] = (
    PanelBatchError,
    PanelStorageError,
    TradingCalendarError,
    PriceDataError,
)
"""The write-time and read-back refusals `panel build` reports rather than crashes on.

Every one of them is a statement about the *data*, so they map to `PanelExit.unhealthy`. None of
them can carry a credential: the writers never see the token, and the batch's own `source_uri`
is `tushare://{dataset}/{subject}/{date}`.
"""

_NEEDS_STORED_CALENDAR: Final[frozenset[str]] = frozenset(
    {ADJ_FACTOR_DATASET, "price", PRICE_LIMIT_DATASET}
)
"""Targets whose writer takes a `TradingCalendar` and therefore needs `trade_cal` in the store
already. `write_adjustment_factors` and `write_daily_panel` both refuse a year missing a session
the calendar reports open, and that census is the whole reason those writers require it."""


def _panel_store(runtime_dir: Path) -> PanelStore:
    """The panel plane inside a runtime directory.

    `runtime_dir/panel`, beside `runtime_dir/state.sqlite3`, so one `--runtime-dir` names one
    installation's whole state exactly as it already does for `migrate` and `research run`.
    """
    return PanelStore(runtime_dir / "panel")


def _panel_transport() -> TushareTransport:
    """The HTTP boundary `panel build` fetches through.

    A named seam, and the only one: tests replace this and everything above it -- the provider's
    credential resolution, its request envelope, its point-in-time filter, its projection and
    every `panel_ingest` guard -- runs for real. `TushareTransport` has been an injectable
    `Protocol` since `V2-P0B-013`, so this adds no indirection that was not already there.
    """
    return UrllibTushareTransport()


def _panel_clock() -> datetime:
    """The wall clock `panel build` stamps its fetches and bounds its session loop with."""
    return datetime.now(UTC)


def _panel_fail(code: PanelExit, message: str) -> typer.Exit:
    """Print `message` on stderr and return the `typer.Exit` the caller must raise.

    Returned rather than raised so every exit in this section is a visible `raise` at its own
    call site, and so `mypy` sees the control flow without a `NoReturn` that a `try` block could
    swallow. Always stderr: `--json` output has to stay parseable on stdout even when the
    command is on its way to a non-zero exit, which is precisely when a caller most needs the
    structured reasons.
    """
    typer.echo(message, err=True)
    return typer.Exit(code=int(code))


def _panel_as_of(value: str) -> datetime:
    """Resolve `--as-of`, defaulting to the wall clock.

    A naive instant is refused rather than localised: every clock in this repository is
    timezone-aware, and guessing a zone for a point-in-time query is the one error that produces
    a plausible wrong answer instead of a failure.
    """
    if not value:
        return _panel_clock()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--as-of expects an ISO-8601 instant with an offset, e.g. "
            f"2026-01-17T04:00:00+00:00; got {value!r}",
        ) from error
    if parsed.tzinfo is None:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--as-of {value!r} carries no UTC offset; a point-in-time question answered in a "
            "guessed timezone is wrong by up to a session",
        )
    return parsed


def _panel_sessions(values: Sequence[str]) -> tuple[date, ...]:
    days: list[date] = []
    for value in values:
        try:
            days.append(date.fromisoformat(value))
        except ValueError as error:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--session expects an ISO-8601 date (YYYY-MM-DD); got {value!r}",
            ) from error
    return tuple(dict.fromkeys(days))


_CALENDAR_BUILD_REMEDY: Final[str] = (
    "Build it first: `openalpha panel build --dataset trade_cal --year <year>`"
)
_CALENDAR_READ_REMEDY: Final[str] = (
    "Build it first (`openalpha panel build --dataset trade_cal --year <year>`), or state on "
    "the record that this run has no calendar with --no-calendar"
)


def _stored_calendar(
    store: PanelStore, *, exchange: str, years: Sequence[int], as_of: datetime, remedy: str
) -> TradingCalendar:
    """The stored exchange calendar, or an exit that says which of the two commands to run.

    `remedy` differs by caller and is not decoration: `panel build` has no `--no-calendar` --
    the writers it drives take a `TradingCalendar` and refuse without one -- so offering that
    flag there would name an option the command does not have.
    """
    try:
        return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)
    except (TradingCalendarError, PanelStorageError) as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {exchange} calendar could not be read out of {store.root}: {error}. {remedy}",
        ) from error


def _panel_request(
    *,
    runtime_dir: Path,
    dataset: Sequence[str],
    year: Sequence[int],
    session: Sequence[str] | None,
    index_code: Sequence[str] | None,
    exchange: str,
    as_of: str,
    with_calendar: bool,
) -> tuple[PanelStore, DependencyRequest]:
    """Resolve the options `panel doctor` and `data-check` share into one stated request.

    Both commands build a `DependencyRequest` even though only one of them hands it to the gate,
    so the two cannot drift about what a request *is*. Its five mandatory fields are the point:
    `sessions=()` and `calendar=None` are legitimate answers and are recorded here as answers
    rather than arriving as defaults nobody chose -- which is why `--no-calendar` exists at all
    instead of the calendar being loaded opportunistically and silently skipped when absent.
    """
    instant = _panel_as_of(as_of)
    years = tuple(dict.fromkeys(year))
    store = _panel_store(runtime_dir)
    calendar = (
        _stored_calendar(
            store,
            exchange=exchange,
            years=years,
            as_of=instant,
            remedy=_CALENDAR_READ_REMEDY,
        )
        if with_calendar
        else None
    )
    return store, DependencyRequest(
        datasets=tuple(dict.fromkeys(dataset)),
        as_of=instant,
        years=years,
        sessions=_panel_sessions(session or ()),
        calendar=calendar,
        index_codes=tuple(dict.fromkeys(index_code or ())),
    )


# --- serialisation ----------------------------------------------------------------------------


def _seconds(span: timedelta | None) -> float | None:
    return None if span is None else span.total_seconds()


def _finding_payload(finding: HealthFinding) -> dict[str, object]:
    return {
        "code": finding.code,
        "category": finding.category,
        "severity": finding.severity,
        "dataset": finding.dataset,
        "datasets": list(finding.datasets),
        "detail": finding.detail,
        "year": finding.year,
        "count": finding.count,
        "dates": [day.isoformat() for day in finding.dates],
        "items": list(finding.items),
        "related_limitations": list(finding.related_limitations),
    }


def health_report_payload(report: PanelHealthReport) -> dict[str, object]:
    """A `PanelHealthReport` as JSON-ready data, losing nothing the report carries.

    Written as one function rather than inline in the command because `V2-P1-016`'s REST and SDK
    faces serialise the same object, and two renderings of one report that disagree about which
    fields exist is how a caller comes to believe a severity is absent when it was merely
    dropped.

    `counts_by_severity` is total over `panel_doctor.HEALTH_SEVERITIES` rather than built from
    the findings that happen to be present: a severity with no findings must read `0`, not be
    missing, or "no blocking findings" and "the blocking key was never emitted" become the same
    observation for a consumer.
    """
    counts = dict.fromkeys(sorted(HEALTH_SEVERITIES), 0)
    for finding in report.findings:
        counts[finding.severity] += 1
    return {
        "as_of": report.as_of.isoformat(),
        "is_clean": report.is_clean,
        "counts_by_severity": counts,
        "blocked_datasets": list(report.blocked_datasets),
        "datasets": [
            {
                "dataset": health.dataset,
                "is_ready": health.is_ready,
                "state": health.readiness.state,
                "years_requested": list(health.years_requested),
                "years_present": list(health.readiness.years_present),
                "row_count": health.readiness.row_count,
                "subject_count": health.readiness.subject_count,
                "checks_waived": list(health.readiness.checks_waived),
                "cadence": health.freshness.cadence,
                "max_staleness_seconds": _seconds(health.freshness.max_staleness),
                "freshness_basis": health.freshness.basis,
                "event_age_seconds": _seconds(health.event_age),
                "fetch_age_seconds": _seconds(health.fetch_age),
                "revised_row_count": health.revised_row_count,
                "revision_labels": [[label, count] for label, count in health.revision_labels],
                "codes": [finding.code for finding in health.findings],
            }
            for health in report.datasets
        ],
        "findings": [_finding_payload(finding) for finding in report.findings],
        "cross_checks": [
            {
                "name": check.name,
                "datasets": list(check.datasets),
                "ran": check.ran,
                "skipped_reason": check.skipped_reason,
                "finding_count": check.finding_count,
            }
            for check in report.cross_checks
        ],
        "limitations": [
            {
                "code": limitation.code,
                "datasets": list(limitation.datasets),
                "detail": limitation.detail,
                "dates": [day.isoformat() for day in limitation.dates],
            }
            for limitation in report.limitations
        ],
    }


def clearance_payload(clearance: DependencyClearance) -> dict[str, object]:
    """A `DependencyClearance` as JSON-ready data.

    Reads `cleared_or_none` and never `cleared`, `bool(...)`, `len(...)` or iteration.
    `DependencyClearance` raises for all three of those **even when it cleared** -- Task 36's
    deliberate choice, because an accessor that answered on a healthy panel and raised on a sick
    one would pass every test written against the first and fail only in production. The merged
    shape has a name that says what it is, and this is the one place in the CLI that wants it.

    `cleared` entries carry their own width -- the years the year-scoped checks covered, the
    sessions a cross-check actually opened, and the caveats still open outside them -- because a
    bare dataset name is exactly as wide as its reader assumes, and that assumption is how
    `V2-P1-013`'s review found Task 29's wrong number reachable through a *cleared* gate.
    """
    cleared = clearance.cleared_or_none
    return {
        "as_of": clearance.request.as_of.isoformat(),
        "is_blocked": clearance.is_blocked,
        "blocked_datasets": list(clearance.blocked_datasets),
        "blocks": [
            {
                "code": block.code,
                "category": block.category,
                "severity": block.severity,
                "dataset": block.dataset,
                "datasets": list(block.datasets),
                "detail": block.detail,
                "year": block.year,
            }
            for block in clearance.blocks
        ],
        "cleared": (
            None
            if cleared is None
            else [
                {
                    "dataset": entry.dataset,
                    "years": list(entry.years),
                    "corroborated_sessions": [
                        day.isoformat() for day in entry.corroborated_sessions
                    ],
                    "caveats": list(entry.caveats),
                }
                for entry in cleared
            ]
        ),
        "notices": [_finding_payload(notice) for notice in clearance.notices],
        "unverified_checks": [
            {"dataset": name, "checks": list(checks)}
            for name, checks in clearance.unverified_checks
        ],
        "report": health_report_payload(clearance.report),
    }


def _echo_report(report: PanelHealthReport) -> None:
    for health in report.datasets:
        state = "READY" if health.is_ready else "BLOCKED"
        years = ",".join(str(year) for year in health.years_requested)
        waived = ",".join(health.readiness.checks_waived) or "-"
        typer.echo(
            f"{state} {health.dataset} years={years} cadence={health.freshness.cadence} "
            f"rows={health.readiness.row_count} waived={waived}"
        )
    for finding in report.findings:
        typer.echo(f"{finding.severity.upper()} {finding.dataset} {finding.code}: {finding.detail}")
    for check in report.cross_checks:
        outcome = (
            f"findings={check.finding_count}" if check.ran else f"skipped: {check.skipped_reason}"
        )
        typer.echo(f"CHECK {check.name} [{','.join(check.datasets)}] {outcome}")
    if report.limitations:
        # Deliberately a count and not the list: `known_limitations` returns up to 55 entries,
        # each a paragraph, and a human report that buried its own findings under them would
        # teach its readers to skim both -- the exact failure `PanelHealthReport` keeps
        # `limitations` a sibling of `findings` to avoid.
        typer.echo(
            f"INFO {len(report.limitations)} known limitation(s) of these datasets "
            "(structural, not defects of this fetch); --json carries them in full"
        )


def _echo_clearance(clearance: DependencyClearance) -> None:
    cleared = clearance.cleared_or_none
    # `cleared or ()` deliberately avoided even here, where the value is already the merged
    # shape: it is the third of the three lines `DependencyClearance` names as the ones that
    # merged blocked with ready-and-empty, and writing it once anywhere makes it the house style.
    for entry in () if cleared is None else cleared:
        sessions = ",".join(day.isoformat() for day in entry.corroborated_sessions) or "-"
        caveats = ",".join(entry.caveats) or "-"
        years = ",".join(str(year) for year in entry.years)
        typer.echo(
            f"CLEARED {entry.dataset} years={years} corroborated_sessions={sessions} "
            f"caveats={caveats}"
        )
    for block in clearance.blocks:
        typer.echo(f"BLOCKED {block.dataset} {block.code}: {block.detail}")
    for notice in clearance.notices:
        typer.echo(f"NOTICE {notice.dataset} {notice.code}: {notice.detail}")
    for name, checks in clearance.unverified_checks:
        typer.echo(f"UNVERIFIED {name} {','.join(checks)}")


# --- panel build ------------------------------------------------------------------------------


def _build_targets(requested: Sequence[str]) -> frozenset[str]:
    asked = tuple(dict.fromkeys(requested))
    for name in asked:
        owner = PANEL_BUILD_COUPLED_DATASETS.get(name)
        if owner is not None:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{name!r} cannot be built on its own: write_daily_panel takes {DAILY_DATASET} "
                f"and {DAILY_BASIC_DATASET} together -- there is no supported way to write one "
                "of those partitions without the other having agreed with it -- and its halts "
                f"argument has no default, so {SUSPENSION_DATASET} is fetched in the same loop. "
                f"Ask for --dataset {owner}",
            )
        if name not in PANEL_BUILD_TARGETS:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{name!r} is not one of this command's build targets "
                f"({sorted(PANEL_BUILD_TARGETS)})",
            )
    return frozenset(asked)


def _year_as_of(year: int) -> datetime:
    """Midnight on 1 January of `year`, in the panel's own zone.

    What `trade_cal` is asked at: `_trade_cal_params` derives the fetched year from `as_of`'s
    Asia/Shanghai year, and `_calendar_publication_timeline` dates every row of a year as
    available from the start of that year, so this instant sees the whole year and no more.
    """
    return datetime(year, 1, 1, tzinfo=PANEL_DATE_ZONE)


def _session_as_of(day: date) -> datetime:
    """The instant a session's cross section became knowable: 16:30 Asia/Shanghai.

    `DAILY_AVAILABILITY_TIME`, imported rather than restated -- it is the same constant
    `providers/tushare.py` dates `available_time` at and `panel_ingest` bounds its session
    census with, so a request built here cannot come to disagree with the row it asks for.
    """
    return datetime.combine(day, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_DATE_ZONE)


def _build_sessions(calendar: TradingCalendar, year: int, now: datetime) -> tuple[date, ...]:
    """Every session this build has to fetch, and the bound is not a choice.

    `panel_ingest._session_census` requires a partition to hold every session the calendar
    reports open between 1 January of its year and the fetch's local date minus one day -- the
    lower bound because a partition that begins in March is exactly the hole the census exists
    for, the upper because a session publishes at 16:30 and a fetch earlier that day cannot hold
    it. A loop over anything narrower is refused by its own writer; a loop over anything wider
    asks for sessions that have not published.
    """
    opens_on = date(year, 1, 1)
    closes_on = min(date(year, 12, 31), now.astimezone(PANEL_DATE_ZONE).date() - timedelta(days=1))
    if closes_on < opens_on:
        raise _panel_fail(
            PanelExit.bad_request,
            f"no session of {year} had published at {now.isoformat()}; there is nothing to "
            "build yet",
        )
    return calendar.trading_days_between(opens_on, closes_on)


def _fetch_panel(
    provider: TushareProvider,
    dataset: str,
    *,
    as_of: datetime,
    subjects: tuple[str, ...] = (),
) -> ColumnarPanelBatch:
    """One panel-plane fetch, reporting a refusal without ever echoing its message.

    `_probe_report`'s rule at this module's second credential boundary, and the reason is the
    same: `ProviderFailure.message` can carry the token or the URL query string it was sent in,
    so only the closed-`Literal` category, the provider id and the dataset name are safe to
    print or log.
    """
    try:
        return provider.fetch_panel(
            ProviderRequest(dataset=dataset, as_of=as_of, subjects=subjects)
        )
    except ProviderFailure as failure:
        logger.warning(
            "panel_fetch_failed",
            extra={
                "provider_id": failure.provider_id,
                "category": failure.category,
                "dataset": dataset,
            },
        )
        raise _panel_fail(
            PanelExit.provider_failure,
            f"provider {failure.provider_id} refused dataset {dataset}: {failure.category}. "
            "The failure's own message is withheld because it can carry the credential it was "
            "sent with; `openalpha doctor --probe` checks the credential itself",
        ) from failure


def _session_batches(
    provider: TushareProvider, datasets: Sequence[str], sessions: Sequence[date]
) -> dict[str, list[ColumnarPanelBatch]]:
    """Fetch every named dataset for every session, one pass over the sessions.

    Takes a *set* of datasets rather than one, so the price target's three-in-one-loop shape --
    which `write_daily_panel`'s docstring predicted of this command, and which is what lets a
    session's halts be fetched beside the bars they explain -- is the same code path a
    single-dataset target uses. A second copy of this loop for the price panel would leave the
    `_EMPTY_SESSION_IS_ORDINARY` branch dead in one of the two.
    """
    collected: dict[str, list[ColumnarPanelBatch]] = {name: [] for name in datasets}
    for day in sessions:
        for name in datasets:
            batch = _fetch_panel(provider, name, as_of=_session_as_of(day))
            if batch.status == "no_data" and name in _EMPTY_SESSION_IS_ORDINARY:
                continue
            collected[name].append(batch)
    return collected


def _build_price_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    sessions: Sequence[date],
    calendar: TradingCalendar,
    year: int,
    now: datetime,
    halts: bool,
) -> tuple[list[PartitionRef], str]:
    """Fetch the three price datasets session by session, then write them in dependency order.

    One loop over the sessions rather than three, which is what `write_daily_panel`'s docstring
    predicted of this command: the halt corpus for a session is fetched beside the bars it
    explains, so the strongest guard in that writer -- the one that refuses a session whose
    missing bars nothing accounts for -- is given a real corpus rather than the `None` that
    switches it off.
    """
    collected = _session_batches(provider, PANEL_BUILD_TARGETS["price"], sessions)
    written: list[PartitionRef] = []
    halt_batches = collected[SUSPENSION_DATASET]
    if halt_batches:
        written.append(write_suspensions(store, halt_batches))
    corpus = None
    if halts:
        if not halt_batches:
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{SUSPENSION_DATASET} served no rows for any session of {year}, so the halt "
                "corpus write_daily_panel's explained-share guard needs does not exist. That is "
                "a fetch to investigate, not a check to skip -- pass --no-halts to state on the "
                "record that this build waives that guard",
            )
        corpus = load_suspensions(store, years=(year,), as_of=now, max_staleness=None)
    written.extend(
        write_daily_panel(
            store,
            bars=collected[DAILY_DATASET],
            fundamentals=collected[DAILY_BASIC_DATASET],
            calendar=calendar,
            halts=corpus,
        )
    )
    return written, "corroborated" if corpus is not None else "waived"


def _build_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    targets: frozenset[str],
    year: int,
    exchange: str,
    halts: bool,
    now: datetime,
) -> tuple[tuple[PartitionRef, ...], tuple[date, ...], str]:
    written: list[PartitionRef] = []
    sessions: tuple[date, ...] = ()
    calendar: TradingCalendar | None = None
    halt_state = "not-applicable"

    if TRADING_CALENDAR_DATASET in targets:
        written.append(
            write_trading_calendar(
                store,
                _fetch_panel(
                    provider,
                    TRADING_CALENDAR_DATASET,
                    as_of=_year_as_of(year),
                    subjects=(exchange,),
                ),
            )
        )
    if STOCK_BASIC_DATASET in targets:
        # `--year` does not scope this one and cannot: `stock_basic` has no date filter, so one
        # request is the whole registry and `write_stock_universe` splits it into one partition
        # per lifecycle year. Recorded in the command's help rather than papered over.
        written.extend(
            write_stock_universe(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now))
        )
    if targets & _NEEDS_STORED_CALENDAR:
        calendar = _stored_calendar(
            store,
            exchange=exchange,
            years=(year,),
            as_of=now,
            remedy=_CALENDAR_BUILD_REMEDY,
        )
        sessions = _build_sessions(calendar, year, now)
    if ADJ_FACTOR_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.append(
            write_adjustment_factors(
                store,
                _session_batches(provider, (ADJ_FACTOR_DATASET,), sessions)[ADJ_FACTOR_DATASET],
                calendar=calendar,
            )
        )
    if "price" in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        price_refs, halt_state = _build_price_panel(
            store,
            provider,
            sessions=sessions,
            calendar=calendar,
            year=year,
            now=now,
            halts=halts,
        )
        written.extend(price_refs)
    if PRICE_LIMIT_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.append(
            write_price_limits(
                store,
                _session_batches(provider, (PRICE_LIMIT_DATASET,), sessions)[PRICE_LIMIT_DATASET],
                calendar=calendar,
            )
        )
    return tuple(written), sessions, halt_state


_BUILD_DATASET_HELP = (
    "A build target, repeatable. One target is one unit of work a panel_ingest writer accepts, "
    "which is not always one dataset: 'price' is daily + daily_basic + suspend_d, because "
    "write_daily_panel takes the pair together and its halts argument has no default. "
    "'stock_basic' ignores --year: the registry has no date filter and is split into one "
    "partition per lifecycle year."
)


@panel_app.command("build")
def panel_build(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_BUILD_DATASET_HELP)],
    year: Annotated[int, typer.Option("--year", help="The partition year to build.")],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    exchange: Annotated[
        str, typer.Option("--exchange", help="Which exchange's calendar to fetch and read.")
    ] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    halts: Annotated[
        bool,
        typer.Option(
            "--halts/--no-halts",
            help=(
                "Whether write_daily_panel is given the year's halt corpus. --no-halts passes "
                "halts=None, which switches off the guard that refuses a session whose missing "
                "bars nothing accounts for. A recorded waiver, never a default."
            ),
        ),
    ] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable build report.")
    ] = False,
) -> None:
    """Fetch and store one year of the panel plane through the real `panel_ingest` writers.

    Nothing here bypasses a write-time guard, and that is the whole design: the batches this
    command assembles go into `write_trading_calendar`, `write_stock_universe`,
    `write_adjustment_factors`, `write_suspensions`, `write_daily_panel` and
    `write_price_limits` unchanged, so a year missing a session the calendar reports open, a
    cross section that arrived short, or two datasets contradicting each other about a close is
    refused there and reported here rather than being stored.

    The credential is never read by this command. `TushareProvider` resolves `TUSHARE_TOKEN`
    itself, inside its own constructor, so the token exists in this process only inside the
    provider and the request envelope it posts -- and a `ProviderFailure`'s message, which can
    carry it, is never printed or logged (see `_fetch_panel`).
    """
    targets = _build_targets(dataset)
    store = _panel_store(runtime_dir)
    provider = TushareProvider(transport=_panel_transport(), clock=_panel_clock)
    now = _panel_clock()
    try:
        written, sessions, halt_state = _build_panel(
            store,
            provider,
            targets=targets,
            year=year,
            exchange=exchange,
            halts=halts,
            now=now,
        )
    except _PANEL_WRITE_REFUSALS as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the panel refused this build and nothing partial was stored: {error}",
        ) from error

    payload = {
        "year": year,
        "exchange": exchange,
        "targets": sorted(targets),
        "halts": halt_state,
        "sessions": {
            "first": sessions[0].isoformat() if sessions else None,
            "last": sessions[-1].isoformat() if sessions else None,
            "count": len(sessions),
        },
        "partitions": [
            {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
            for ref in written
        ],
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if sessions:
        typer.echo(
            f"SESSIONS {len(sessions)} from {sessions[0].isoformat()} to {sessions[-1].isoformat()}"
        )
    for ref in written:
        typer.echo(f"WROTE {ref.dataset} year={ref.year} rows={ref.row_count}")
    typer.echo(f"HALTS {halt_state}")


# --- panel doctor and data-check ---------------------------------------------------------------

_DATASET_HELP = "A dataset to assess, repeatable. Nothing is inferred: this is the caller's own "
_DATASET_HELP += "statement of what should be there."
_YEAR_HELP = (
    "A partition year to assess, repeatable. Deliberately the caller's assertion of what should "
    "be present rather than a reading of what is -- passing the stored years would make "
    "partition_missing unreachable by construction."
)
_SESSION_HELP = (
    "An ISO-8601 session the day-level cross-checks run on, repeatable. Not inferred: 'check "
    "every session' is a whole-corpus scan and 'check the last one' is a guess."
)
_CALENDAR_HELP = (
    "Whether to read the exchange calendar out of the panel. --no-calendar states on the record "
    "that this run has none, which switches off every session-scoped cross-check; the gate then "
    "refuses a daily-cadence dataset nothing corroborated rather than clearing it."
)


@panel_app.command("doctor")
def panel_doctor_command(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_DATASET_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_YEAR_HELP)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    session: Annotated[list[str] | None, typer.Option("--session", help=_SESSION_HELP)] = None,
    index_code: Annotated[list[str] | None, typer.Option("--index-code")] = None,
    exchange: Annotated[str, typer.Option("--exchange")] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[
        str, typer.Option("--as-of", help="ISO-8601 point-in-time clock; defaults to now.")
    ] = "",
    calendar: Annotated[bool, typer.Option("--calendar/--no-calendar", help=_CALENDAR_HELP)] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the health report as data.")
    ] = False,
) -> None:
    """Report what is wrong with the stored panel at a stated `as_of`.

    Distinct from the top-level `doctor`, which probes *provider* credentials and declared
    capabilities. This one reads the panel: per-dataset readiness and freshness, the
    cross-dataset checks, and the datasets' own structural limitations kept separate from this
    fetch's defects.

    Exits non-zero exactly when the report is not `is_clean` -- one or more `blocking` or
    `warning` findings. A `notice` never does; see `PanelExit` for the measurement behind that.
    """
    store, request = _panel_request(
        runtime_dir=runtime_dir,
        dataset=dataset,
        year=year,
        session=session,
        index_code=index_code,
        exchange=exchange,
        as_of=as_of,
        with_calendar=calendar,
    )
    try:
        report = panel_health_report(
            store,
            as_of=request.as_of,
            datasets=request.datasets,
            years=request.years,
            calendar=request.calendar,
            index_codes=request.index_codes,
            cross_section_days=request.sessions,
        )
    except PanelDoctorError as error:
        raise _panel_fail(PanelExit.bad_request, str(error)) from error

    if json_output:
        typer.echo(json.dumps(health_report_payload(report), ensure_ascii=False, sort_keys=True))
    else:
        _echo_report(report)
    if not report.is_clean:
        raise typer.Exit(code=int(PanelExit.unhealthy))


@app.command("data-check")
def data_check(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_DATASET_HELP)],
    year: Annotated[list[int], typer.Option("--year", help=_YEAR_HELP)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    session: Annotated[list[str] | None, typer.Option("--session", help=_SESSION_HELP)] = None,
    index_code: Annotated[list[str] | None, typer.Option("--index-code")] = None,
    exchange: Annotated[str, typer.Option("--exchange")] = TRADING_CALENDAR_DEFAULT_EXCHANGE,
    as_of: Annotated[
        str, typer.Option("--as-of", help="ISO-8601 point-in-time clock; defaults to now.")
    ] = "",
    calendar: Annotated[bool, typer.Option("--calendar/--no-calendar", help=_CALENDAR_HELP)] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the clearance as data.")
    ] = False,
) -> None:
    """Run the fail-closed dependency gate and exit non-zero when it refuses.

    The exit code is the deliverable. A command that ran the gate, was refused, and still
    exited 0 would be no gate at all in a CI job or a scheduled run -- which is the "empty
    success" `V2-P1-013` exists to make unavailable, reappearing one layer up.

    A clearance is a verdict rather than a collection, and this command treats it as one: it
    asks `is_blocked` and reads `cleared_or_none`, never `bool()`, `len()` or iteration, all
    three of which raise here **even when the request cleared**.
    """
    store, request = _panel_request(
        runtime_dir=runtime_dir,
        dataset=dataset,
        year=year,
        session=session,
        index_code=index_code,
        exchange=exchange,
        as_of=as_of,
        with_calendar=calendar,
    )
    try:
        clearance = require_datasets(store, request)
    except (PanelGateError, PanelDoctorError) as error:
        raise _panel_fail(PanelExit.bad_request, str(error)) from error

    if json_output:
        typer.echo(json.dumps(clearance_payload(clearance), ensure_ascii=False, sort_keys=True))
    else:
        _echo_clearance(clearance)
    if clearance.is_blocked:
        raise typer.Exit(code=int(PanelExit.unhealthy))


def main() -> None:
    """Run the command-line application.

    Loads `.env` (if present in the process's current working directory) into the
    real process environment before dispatching to any subcommand -- so `doctor`/
    `serve`/... see values that live only in `.env`, with an already-exported real
    environment variable always winning over the same name in `.env`. This is the
    *only* place in this package that does so: the Typer `app` object driven
    directly by `CliRunner` in tests is never routed through here, so exercising
    the CLI in tests never touches a real `.env` as a side effect. See
    `openalpha_cn/config.py` for the full precedence/discovery contract.

    Also configures structured logging (V2-P0B-007), once, before dispatching to any
    subcommand -- the other of this package's two logging entry points, alongside
    `api/app.py::create_app()`. Resolves *only* `OPENALPHA_LOG_LEVEL` for this, via
    `load_log_level()`, never the full `OpenAlphaConfig` -- an invalid `OPENALPHA_LOG_LEVEL`
    itself still fails loudly here with a named `ConfigError`, printed to stderr, since a
    scheduled job's logs being silently misconfigured is exactly the failure mode this
    guards against. Deliberately *not* `load_config()` (Finding 2, a P0.B review fix): an
    earlier version called `load_config()` here, which validates every `OPENALPHA_*` field
    atomically, so an invalid field with nothing to do with logging (e.g. a non-numeric
    `OPENALPHA_MAX_REQUEST_BYTES`) aborted dispatch to *every* command -- including
    `doctor`, whose entire job is diagnosing exactly that kind of broken environment, and
    `version`, which touches no config at all. Any other command that genuinely needs the
    full config still calls `load_config()` itself and fails with the same good named error
    at the point it actually needs it (see `serve`, and `doctor`'s own `"config"` finding).
    """
    load_dotenv()
    try:
        configure_logging(load_log_level())
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise SystemExit(1) from error
    app()


if __name__ == "__main__":
    main()
