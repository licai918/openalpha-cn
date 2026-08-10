"""Command-line entry point for OpenAlpha CN."""

import json
import logging
import os
import platform
import sys
from collections.abc import Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Annotated, Final, cast
from zoneinfo import ZoneInfo

import typer
import uvicorn

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus
from openalpha_cn.config import ConfigError, load_config, load_dotenv, load_log_level
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PriceDataError,
)
from openalpha_cn.domain.financial_statements import FinancialStatementError
from openalpha_cn.domain.index_membership import IndexMembershipError
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelBatchError
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SuspensionError,
)
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET, StockUniverseError
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
from openalpha_cn.panel_view import (
    PanelRequestError,
    PanelUnreadableError,
    clearance_payload,
    health_report_payload,
    panel_request,
    panel_store,
)
from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    PanelDataProvider,
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


def _probe_plane(provider: DataProvider, dataset: str) -> str:
    """Which of a provider's fetch methods one minimal request for `dataset` should go to.

    `_probe_subjects`' sibling, the same `getattr` seam, and it exists for the defect that
    seam was one half of. `_probe_report` called `fetch()` for every declared dataset, and
    four of Tushare's fifteen declare `serves_evidence_plane=False` -- a verbatim evidence
    record has no single `available_time` for them -- so `fetch()` refused all four with
    `configuration` **before any transport call**, in the same microsecond, in every
    environment. The probe was reporting "this account cannot fetch `stock_basic`" about an
    endpoint that returns 6,217 rows on the plane it is actually served on.

    A provider opts in by exposing `probe_plane(dataset) -> "evidence" | "panel"`; anything
    else, and any unrecognised answer, stays on the evidence plane, which is the only method
    `DataProvider` guarantees.
    """
    hook = getattr(provider, "probe_plane", None)
    if hook is None:
        return "evidence"
    return "panel" if hook(dataset) == "panel" else "evidence"


def _probe_once(provider: DataProvider, request: ProviderRequest) -> None:
    """Send one minimal request on whichever plane `_probe_plane` named."""
    if _probe_plane(provider, request.dataset) == "panel":
        panel_provider = cast(PanelDataProvider, provider)
        panel_provider.fetch_panel(request)
        return
    provider.fetch(request)


PROBE_FAILURE_STATES: Final[frozenset[str]] = frozenset({"authentication"})
"""The probe outcomes that make `doctor --probe` exit non-zero, as a closed set.

**One member**, and the reasoning behind that number is the whole of this decision.

`authentication` is a credential this provider *had* and the endpoint *rejected*. It is the
one probe outcome that is both about this side of the connection and unambiguous, and until
`V2-P1-018` it could not be produced at all: `providers/tushare.py::TUSHARE_CREDENTIAL_CODE`
records the measurement -- a wrong token answers `code=40101`, and the only code mapped to
`authentication` was `-2001`, which nothing here has ever observed. So the single most common
real failure was reported as `upstream`, advertised as retryable, and exited 0.

Four outcomes are deliberately *not* here, and three of them are the ones a naive "anything
that is not ok" rule would have swept in:

- `configuration`. R12's brief draws this line itself: "this dataset needs a parameter" must
  not count as a failure. After `_probe_subjects` and `_probe_plane` that reading is no longer
  reachable through this path, but the other one is -- an **absent** `TUSHARE_TOKEN` is
  `configuration` too, and a default install with no Tushare account is a normal state, already
  reported as `WARN credential ... missing` by the check next to this one. Failing the command
  for it would make `--probe` non-zero on almost every machine, which is how a check becomes a
  `|| true`.
- `rate_limit` is a fact about the next sixty seconds, and the provider already waits and
  retries within one (`providers/tushare.py::TUSHARE_RATE_LIMIT_DELAY`).
- `upstream` is the endpoint's own verdict about one interface. That is the **content** of the
  report Implementation Decision 33 asks for -- "which interfaces can this account actually
  reach" -- not a reason to refuse to publish it.
- `not_configured` is a provider that declined to be probed at all (ChainLin without a base
  URL), which is again the default install.

`probe_error` sits closest to the line and stays out by the same argument as `upstream`: it is
per-dataset, and the *report* is what carries it.
"""


def _probe_report(provider: DataProvider) -> dict[str, str]:
    """Make one minimal request per declared dataset and classify the outcome.

    `ProviderFailure` reports its declared, closed-`Literal` category
    verbatim. Anything else -- a bug, or a future or third-party provider
    that has not adopted the `ProviderFailure` contract -- is recorded as
    the doctor-level state `probe_error` without ever echoing the
    exception's message or repr, so this boundary can never leak a
    credential embedded in an unexpected error.

    "One minimal request" is now a fact rather than a description: the subjects come from
    `_probe_subjects` and the plane from `_probe_plane`, both of which the provider answers, so
    every declared dataset reaches the network. Before those two hooks, nine of Tushare's
    fifteen never did -- see `PROBE_FAILURE_STATES` and `_probe_plane` for the two causes and
    the measurement that separated them.
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
            _probe_once(provider, request)
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
    probe_failures: list[str] = []
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
            outcomes = _probe_report(provider)
            report["probe"] = outcomes
            probe_failures.extend(
                f"{provider_id}/{dataset}: {result}"
                for dataset, result in outcomes.items()
                if result in PROBE_FAILURE_STATES
            )
        providers[provider_id] = report

    environment_ok = python_ok and timezone_ok and config_ok
    payload = {
        "status": "ok" if environment_ok and not probe_failures else "error",
        "checks": checks,
        "providers": providers,
        "warnings": warnings,
        # Always present with `--probe`, and empty is the interesting value: a caller reading
        # this to decide whether a scheduled build can run needs "the probe ran and found
        # nothing" to be distinguishable from "the probe did not run", which an absent key is
        # not. Without `--probe` the key is absent, because no probe happened.
        **({"probe_failures": probe_failures} if probe else {}),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        # Falls through to the exit check rather than returning, and that is a fix rather than
        # a tidy-up. `doctor --json` used to `return` here, so **the one rendering a CI job
        # parses always exited 0** -- a malformed `OPENALPHA_*` value, a rejected credential, a
        # probe that could not reach an endpoint, all reported faithfully in the payload and all
        # indistinguishable from a clean run to `set -e`. `panel doctor` and `data-check` both
        # print their JSON and then raise; this is the same rule, and `PanelExit`'s docstring
        # already says why an exit code that cannot say "no" is not a check.
        raise typer.Exit(code=0 if payload["status"] == "ok" else 1)

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
                state = "FAIL" if result in PROBE_FAILURE_STATES else "PROBE"
                typer.echo(f"{state} {provider_id}/{dataset} {result}")
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
    - `internal_error` -- **the command itself broke.** Nothing was judged: an exception no
      branch here anticipated reached the top of the command, or a build target this module's
      own closed table accepted turned out to have no branch that builds it. Distinct from
      `unhealthy` for the reason that matters most in this table: without it, a CLI that
      crashed exited 1 through Typer's default handler and was indistinguishable from a panel
      that failed its check, so a scheduled job would report "the data is bad" for a defect in
      this file. The remedy is a bug report, not a re-fetch.

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
    internal_error = 5


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
same sessions has to be fetched and stored before the pair that consumes it. That is why
`--dataset daily` cannot mean anything (see `PANEL_BUILD_COUPLED_DATASETS`), which
`panel_ingest.write_daily_panel`'s own docstring names as a constraint on this command's surface
rather than an implementation detail of that one.

**`suspend_d`'s place inside `price` is this issue's scope choice, not a constraint from below,
and the earlier wording here overstated it.** The coupling `write_daily_panel` imposes is on
`daily` and `daily_basic` only; `write_suspensions(store, batches)` takes no calendar, needs no
partner and refuses nothing about being written alone. What is true is narrower: `price` needs a
stored halt corpus *for the same year*, so this target fetches it in the same pass rather than
requiring two invocations. The cost of folding it in is real and is recorded rather than hidden
-- a caller who only wants to backfill one year of halts (~28 rows on a measured session) has to
re-fetch that year's `daily` and `daily_basic` too (~5,338 rows per session across ~244
sessions). A standalone `suspend_d` target is therefore a sound addition, deliberately left to
`V2-P1-016` rather than taken here, because adding it changes this table and the closed-table
test that pins it.

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
not have a daily panel" -- the opposite of the truth. `V2-P1-007`'s coupling is the reason for
`daily` and `daily_basic`; `suspend_d` is here because *this command* fetches it inside `price`,
which is a scope decision -- see `PANEL_BUILD_TARGETS`.
"""

_LIFECYCLE_YEAR_TARGETS: Final[frozenset[str]] = frozenset({STOCK_BASIC_DATASET})
"""Targets whose partitions are not the `--year` that was asked for, and why that is legitimate.

Exactly one. `stock_basic` has no date filter, so one request is the whole registry and
`write_stock_universe` splits it into one partition per *lifecycle* year -- a security listed in
1991 goes to 1991 whatever `--year` said. Every other target writes the year it was asked for,
which is what `_audit_written_partitions` checks; this set is the exemption, stated once so a
future target cannot acquire the exemption by accident.
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
    PanelStorageError,
    PanelBatchError,
    PriceDataError,
    AdjustmentError,
    SuspensionError,
    StockUniverseError,
    IndexMembershipError,
    FinancialStatementError,
    TradingCalendarError,
)
"""The write-time and read-back refusals `panel build` reports rather than crashes on.

Every one of them is a statement about the *data*, so they map to `PanelExit.unhealthy`. None of
them can carry a credential: the writers never see the token, and the batch's own `source_uri`
is `tushare://{dataset}/{subject}/{date}`.

**Equal, as a set, to `panel_doctor._LOAD_FAILURES`, and pinned that way by
`tests/unit/test_cli_panel_rules.py`.** That module already answers the question this one is
asking -- "which exceptions are facts about stored data rather than defects in the code that
read it" -- and the two lists were allowed to drift apart. The doctor named all nine; this named
four, so a `SuspensionError` out of `load_suspensions` in the middle of `panel build --dataset
price` was classified `PanelExit.internal_error`: exit 5, "a defect in the command, not a
verdict about the panel", with the exception's own message withheld on the grounds that an
unanticipated failure might carry a credential. That refusal names one ticker and one session.
Two modules disagreeing about what counts as a data fact is the drift the equality test exists
to stop; if a tenth domain error is added, both lists must learn it together.
"""

_NEEDS_STORED_CALENDAR: Final[frozenset[str]] = frozenset(
    {ADJ_FACTOR_DATASET, "price", PRICE_LIMIT_DATASET}
)
"""Targets whose writer takes a `TradingCalendar` and therefore needs `trade_cal` in the store
already. `write_adjustment_factors` and `write_daily_panel` both refuse a year missing a session
the calendar reports open, and that census is the whole reason those writers require it."""

SESSION_SCOPED_DATASETS: Final[tuple[str, ...]] = (
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The datasets that must reach the **same last session** for a year to be assessable at all.

Four of the seven this command writes, and the other three are excluded for reasons rather than
by omission. `trade_cal` is a whole year including days that have not happened; `stock_basic` is
keyed by lifecycle year; and `suspend_d` legitimately holds nothing for a session on which
nothing was halted, so its last covered date is a fact about the market rather than about this
build's horizon (`_EMPTY_SESSION_IS_ORDINARY` says the same thing one layer down). Each of these
four publishes on every open session, so its last covered date **is** the horizon its build ran
to. See `_refuse_split_horizon`.
"""


def _panel_store(runtime_dir: Path) -> PanelStore:
    """The panel plane inside a runtime directory.

    `runtime_dir/panel`, beside `runtime_dir/state.sqlite3`, so one `--runtime-dir` names one
    installation's whole state exactly as it already does for `migrate` and `research run`.

    Delegates to `panel_view.panel_store` rather than restating the subdirectory: `V2-P1-016`'s
    HTTP app and SDK read the same store from the same `runtime_dir`, and three faces
    disagreeing about where it lives would make every equivalence between them a coincidence.
    """
    return panel_store(runtime_dir)


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


@contextmanager
def _panel_command(name: str) -> Iterator[None]:
    """Wrap one panel command so a defect in it can never be read as a verdict about the panel.

    Without this, anything the command did not anticipate -- a `NotADirectoryError` from a
    `--runtime-dir` that names a file, an `AttributeError` in a rendering -- reaches Typer's own
    handler, which prints a traceback and exits **1**. That is `PanelExit.unhealthy`, so "the CLI
    crashed" and "the panel failed its check" arrive at a CI job as the same number. This turns
    the first into `PanelExit.internal_error`, which no data can produce.

    The exception's own message is deliberately withheld, `_fetch_panel`'s rule at this module's
    third credential boundary and for a stronger reason: an *unanticipated* exception can carry
    anything the frame it escaped was holding, and a traceback with locals carries the frames
    too. Only the type and the command name are printed. `typer.Exit` and `typer.Abort` are
    re-raised untouched -- both subclass `RuntimeError`, so a bare `except Exception` here would
    otherwise swallow every deliberate exit this module raises and turn `--json` on a blocked
    gate into a crash report.
    """
    try:
        yield
    except (typer.Exit, typer.Abort):
        raise
    except Exception as error:
        raise _panel_fail(
            PanelExit.internal_error,
            f"`{name}` did not finish: it raised an unhandled {type(error).__name__}. This is a "
            "defect in the command, not a verdict about the panel -- nothing was checked and "
            "nothing here should be read as a health result. The exception's own message is "
            "withheld because an unanticipated failure can carry whatever the frame it escaped "
            "was holding, including the credential",
        ) from error


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
"""`panel build`'s own way out of a missing calendar, which is only one way.

Deliberately not `panel_view.NO_CALENDAR_REMEDY`: that one offers "state on the record that
this run has no calendar", and `panel build` has no `--no-calendar` -- the writers it drives
take a `TradingCalendar` and refuse without one -- so offering it here would name an option
this command does not have. The read side has both ways out and uses the shared text.
"""


def _stored_calendar(
    store: PanelStore, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar:
    """The stored exchange calendar for a *build*, or an exit naming the command to run first.

    `panel build`'s own, and the read side's is `panel_view.stored_calendar`. The two differ in
    the one thing that is not shareable -- which remedies exist on this channel -- and in
    nothing else, which is why this one is a build-time helper rather than a second general
    loader: `_panel_request` below goes through the shared resolver.
    """
    try:
        return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)
    except (TradingCalendarError, PanelStorageError) as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {exchange} calendar could not be read out of {store.root}: {error}. "
            f"{_CALENDAR_BUILD_REMEDY}",
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

    What is left here is exactly the part that is this channel's: parsing option *strings* into
    the values a request is made of, and turning the shared resolver's two faults into exit
    codes. The resolution itself -- the de-duplication, the calendar load, the refusals for a
    naive `as_of`, an empty dataset list and a malformed exchange -- is
    `panel_view.panel_request`, the same call `GET /api/v1/panel/*` and `OpenAlphaSDK` make.
    A near-copy of it here was the drift `panel_view.py` exists to prevent, on the request side
    instead of the rendering side: three faces that render one report identically but resolve
    the request three ways still answer three different questions.

    The two faults map onto the rows `PanelExit` already has for them, by the names
    `panel_view.py` gives them. `PanelRequestError` is `bad_request` -- no re-fetch fixes a
    naive `as_of`. `PanelUnreadableError` is `unhealthy`, which is the code the hand-written
    loader this replaced already used, and right for `panel doctor`'s reason: the request was
    well formed and the panel could not answer it.
    """
    instant = _panel_as_of(as_of)
    sessions = _panel_sessions(session or ())
    store = _panel_store(runtime_dir)
    try:
        request = panel_request(
            store,
            datasets=dataset,
            years=year,
            sessions=sessions,
            index_codes=index_code or (),
            as_of=instant,
            exchange=exchange,
            with_calendar=with_calendar,
        )
    except PanelRequestError as error:
        raise _panel_fail(PanelExit.bad_request, str(error)) from error
    except PanelUnreadableError as error:
        raise _panel_fail(PanelExit.unhealthy, str(error)) from error
    return store, request


# --- human-readable output --------------------------------------------------------------
#
# The structural renderings these two commands' `--json` emits live in
# `openalpha_cn/panel_view.py`, shared verbatim with `V2-P1-016`'s HTTP app and SDK. They
# were written as standalone functions here for exactly that reason: two renderings of one
# report that disagree about which fields exist is how a caller comes to believe a severity
# is absent when it was merely dropped.


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


def _stored_horizon(store: PanelStore, dataset: str, year: int) -> date | None:
    """The last session `dataset`'s stored partition for `year` covers, or `None`.

    `None` for both "no partition" and "a partition with no coverage census", because neither
    can say what horizon the build that wrote it ran to, and `_refuse_split_horizon` may only
    refuse on evidence.
    """
    if year not in store.registered_years(dataset):
        return None
    coverage = store.read_coverage(dataset, year)
    if coverage is None or not coverage.dates:
        return None
    return max(entry.event_date for entry in coverage.dates)


def _refuse_split_horizon(
    store: PanelStore, *, sessions: Sequence[date], year: int, exchange: str, rewritten: Set[str]
) -> None:
    """Refuse a build whose horizon disagrees with a session-scoped partition already stored.

    ## The panel this stops from existing

    `panel build` reads its clock once per **invocation**, and the five targets are five
    invocations. A build that starts at 23:34 and finishes at 00:39 Asia/Shanghai therefore
    runs some of its targets against `today - 1` and the rest against `tomorrow - 1`, and the
    panel that lands cannot be assessed clean at **any** `as_of`. Measured by
    `tests/e2e/test_panel_chain_online.py` on this suite's own first build:

        daily / daily_basic / adj_factor   144 sessions, last 2026-08-07
        stk_limit                          145 sessions, last 2026-08-10

    Before `stk_limit`'s newest row is knowable, `panel doctor` reports `stk_limit
    not_yet_knowable`; at or after it, the calendar requires 2026-08-10 of the price panel,
    which does not have it, and the report is `daily date_gap`. Both refusals are correct and
    there is no third instant. The only remedy once it has happened is re-fetching the lagging
    targets, which cost 386s and 2,374s on that run.

    ## Why a refusal here rather than a warning, and why it is not a rule about time

    This runs **before the first session is fetched** -- after the calendar load, which is what
    `sessions` needs, and before any of the work. So the cost of being wrong is one command that
    has to be re-run with a flag, against forty-five minutes of fetching that has to be thrown
    away. It also does not reason about clocks at all: it compares the last session *this build
    would reach* against the last session a sibling partition *already covers*, both derived
    from the same stored calendar, so it catches a split horizon whatever produced it -- a clock
    that rolled over, a `--as-of` typo, a partial re-fetch a week later.

    ## Why `rewritten` is subtracted, and why that is the whole difference between a guard and
    ## a wall

    A disagreement is not always an accident: extending a stored year by a day is a legitimate
    thing to want. `PanelStore` replaces a partition **whole** -- there is no append -- so
    "extend the panel" means "rebuild every session-scoped partition of the year", and a rule
    that compared this build's horizon against every stored partition would refuse the very
    command that does it correctly. What matters is not whether the store disagrees *now* but
    whether it will still disagree when this build finishes, so the comparison is against the
    partitions this invocation is **not** going to replace.

    That makes the two remedies real rather than rhetorical, and they are the two the message
    offers: name every session-scoped target in one invocation, which reads one clock and moves
    them together, or pin `--as-of` at the stored horizon. Doing it one target at a time without
    pinning is the case that stays refused, and it is the case that produced the defect.

    The second remedy is only offered when it exists. A store written *before* this guard can
    already hold siblings that disagree with each other -- that is the panel the e2e suite
    measured -- and there is then no instant that agrees with all of them, so naming one would
    be a remedy that fails on the next run. `_pinning_remedy` says which case this is.
    """
    if not sessions:
        return
    reached = sessions[-1]
    stored = {
        dataset: horizon
        for dataset in SESSION_SCOPED_DATASETS
        if dataset not in rewritten
        and (horizon := _stored_horizon(store, dataset, year)) is not None
        and horizon != reached
    }
    if not stored:
        return
    listed = ", ".join(
        f"{dataset} stops at {horizon.isoformat()}" for dataset, horizon in sorted(stored.items())
    )
    raise _panel_fail(
        PanelExit.unhealthy,
        f"this build would reach {reached.isoformat()} and the {exchange} panel for {year} "
        f"already holds partitions that do not: {listed}. A panel whose session-scoped datasets "
        "stop on different days cannot be assessed clean at any as_of -- earlier than the "
        "newest partition's last row and the doctor reports not_yet_knowable, at or after it "
        "and it reports a date_gap in the older ones -- so this is refused before anything is "
        "fetched rather than after. Either build the session-scoped targets together in one "
        "invocation, which moves the horizon atomically because a partition is replaced whole, "
        f"{_pinning_remedy(stored)}",
    )


def _pinning_remedy(stored: Mapping[str, date]) -> str:
    """The `--as-of` that would make this build agree with `stored`, or why there is none.

    A remedy printed in a refusal is a promise, so it is only made when it can be kept. When
    every stored sibling stops on the same session there is an instant that reproduces it --
    any instant on the day after, because `_build_sessions` bounds at the local date minus one
    -- and midday is named to keep it clear of both the 16:30 publication and either midnight.

    When they *disagree with each other*, no instant agrees with all of them, and offering the
    oldest one's would produce a build the next sibling refuses. That state cannot be created
    by a build this guard has seen, but it can be inherited: it is exactly the panel the e2e
    suite measured before `--as-of` existed. So the message says so instead.
    """
    horizons = set(stored.values())
    if len(horizons) > 1:
        return (
            "or -- since those partitions do not agree with each other either, so no single "
            "--as-of reproduces all of them -- rebuild every one of "
            f"{sorted(SESSION_SCOPED_DATASETS)} in one invocation"
        )
    pinned = datetime.combine(
        horizons.pop() + timedelta(days=1), time(12, 0), tzinfo=PANEL_DATE_ZONE
    )
    return f"or pin this build to the stored horizon with --as-of {pinned.isoformat()}"


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


PANEL_PROGRESS_EVERY: Final[int] = 10
"""How many sessions one `panel build` progress line covers.

Ten, against a year-to-date build of ~145 sessions, so a target reports about fifteen times --
often enough that a stalled fetch is visible within a minute or two, rarely enough that the
lines are not themselves the output. The measured builds this was chosen against: `adj_factor`
386--444s, `price` ~1,000--2,374s, `stk_limit` ~330s, whole build ~30--50 minutes, during which
this command printed **nothing at all** and a caller could not tell a live fetch from a wedged
one without `lsof`.
"""


def _echo_progress(datasets: Sequence[str], done: int, total: int, started: float) -> None:
    """One progress line on stderr: what, how far, how long, and how much longer.

    stderr rather than stdout, for `_panel_fail`'s reason at the other end of the same command:
    `--json` has to stay parseable on stdout, and a progress line interleaved into it would make
    every scripted caller's `json.loads` fail. The remaining estimate is linear in sessions
    fetched, which is the right model here -- every session is the same whole-market request --
    and it is labelled `eta` rather than presented as a promise.
    """
    elapsed = monotonic() - started
    rate = elapsed / done if done else 0.0
    typer.echo(
        f"FETCHING {'+'.join(datasets)} {done}/{total} sessions "
        f"elapsed={elapsed:.0f}s eta={rate * (total - done):.0f}s",
        err=True,
    )


def _session_batches(
    provider: TushareProvider, datasets: Sequence[str], sessions: Sequence[date]
) -> dict[str, list[ColumnarPanelBatch]]:
    """Fetch every named dataset for every session, one pass over the sessions.

    Takes a *set* of datasets rather than one, so the price target's three-in-one-loop shape --
    which `write_daily_panel`'s docstring predicted of this command, and which is what lets a
    session's halts be fetched beside the bars they explain -- is the same code path a
    single-dataset target uses. A second copy of this loop for the price panel would leave the
    `_EMPTY_SESSION_IS_ORDINARY` branch dead in one of the two.

    Reports progress every `PANEL_PROGRESS_EVERY` sessions and once at the end. This is the one
    loop in the command that takes minutes rather than seconds, so it is the only one that
    needs to say anything while it runs.
    """
    collected: dict[str, list[ColumnarPanelBatch]] = {name: [] for name in datasets}
    started = monotonic()
    for index, day in enumerate(sessions, start=1):
        for name in datasets:
            batch = _fetch_panel(provider, name, as_of=_session_as_of(day))
            if batch.status == "no_data" and name in _EMPTY_SESSION_IS_ORDINARY:
                continue
            collected[name].append(batch)
        if index % PANEL_PROGRESS_EVERY == 0 or index == len(sessions):
            _echo_progress(datasets, index, len(sessions), started)
    return collected


def _build_price_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: list[PartitionRef],
    sessions: Sequence[date],
    calendar: TradingCalendar,
    year: int,
    now: datetime,
    halts: bool,
) -> str:
    """Fetch the three price datasets session by session, then write them in dependency order.

    One loop over the sessions rather than three, which is what `write_daily_panel`'s docstring
    predicted of this command: the halt corpus for a session is fetched beside the bars it
    explains, so the strongest guard in that writer -- the one that refuses a session whose
    missing bars nothing accounts for -- is given a real corpus rather than the `None` that
    switches it off.

    Appends into the caller's `written` list rather than returning one of its own, because the
    `suspend_d` partition is stored *before* `write_daily_panel` is even called: a refusal from
    that writer leaves a real partition behind, and a list that only exists on the success path
    cannot say so. See `_stored_so_far`.
    """
    collected = _session_batches(provider, PANEL_BUILD_TARGETS["price"], sessions)
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
    return "corroborated" if corpus is not None else "waived"


def _build_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: dict[str, list[PartitionRef]],
    targets: frozenset[str],
    year: int,
    exchange: str,
    halts: bool,
    now: datetime,
) -> tuple[tuple[date, ...], str]:
    """Run every requested target in `PANEL_BUILD_TARGETS`' declared order.

    `written` is the caller's, keyed by target and filled as each partition lands, for two
    reasons that a returned value cannot serve. A build is a sequence of whole-partition writes
    with no transaction around them, so when a later target is refused the earlier ones are
    already on disk and the command has to be able to *name* them (`_stored_so_far`). And a
    target that this function accepted but wrote nothing for is only visible as an absent key
    (`_audit_written_partitions`) -- the failure a sixth entry in `PANEL_BUILD_TARGETS` with no
    branch below produces, which would otherwise be an exit 0 with an empty `partitions` list.
    """
    sessions: tuple[date, ...] = ()
    calendar: TradingCalendar | None = None
    halt_state = "not-applicable"

    if TRADING_CALENDAR_DATASET in targets:
        written.setdefault(TRADING_CALENDAR_DATASET, []).append(
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
        # per lifecycle year. Recorded in the command's help, in `_LIFECYCLE_YEAR_TARGETS` (which
        # exempts it from the partition-year audit) rather than papered over.
        written.setdefault(STOCK_BASIC_DATASET, []).extend(
            write_stock_universe(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now))
        )
    if targets & _NEEDS_STORED_CALENDAR:
        calendar = _stored_calendar(store, exchange=exchange, years=(year,), as_of=now)
        sessions = _build_sessions(calendar, year, now)
        _refuse_split_horizon(
            store,
            sessions=sessions,
            year=year,
            exchange=exchange,
            # What this invocation will replace, resolved through the target table rather than
            # from the flags: `--dataset price` names one target and rewrites three datasets.
            rewritten={name for target in targets for name in PANEL_BUILD_TARGETS[target]},
        )
    if ADJ_FACTOR_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.setdefault(ADJ_FACTOR_DATASET, []).append(
            write_adjustment_factors(
                store,
                _session_batches(provider, (ADJ_FACTOR_DATASET,), sessions)[ADJ_FACTOR_DATASET],
                calendar=calendar,
            )
        )
    if "price" in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        halt_state = _build_price_panel(
            store,
            provider,
            written=written.setdefault("price", []),
            sessions=sessions,
            calendar=calendar,
            year=year,
            now=now,
            halts=halts,
        )
    if PRICE_LIMIT_DATASET in targets:
        assert calendar is not None  # guaranteed by `_NEEDS_STORED_CALENDAR` above
        written.setdefault(PRICE_LIMIT_DATASET, []).append(
            write_price_limits(
                store,
                _session_batches(provider, (PRICE_LIMIT_DATASET,), sessions)[PRICE_LIMIT_DATASET],
                calendar=calendar,
            )
        )
    return sessions, halt_state


def _stored_so_far(written: Mapping[str, Sequence[PartitionRef]]) -> str:
    """What is on disk at the moment a build was refused, as a sentence a reader can act on.

    An earlier version of `panel_build` said "nothing partial was stored" here, and that was
    false whenever more than one partition was in flight. `_build_panel` writes whole partitions
    one after another with no transaction around them, so `panel build --dataset trade_cal
    --dataset price` that is refused by `write_daily_panel` leaves `trade_cal` **and**
    `suspend_d` in the store. Only the writer that raised is all-or-nothing; the build is not,
    and a caller deciding whether to re-run or to clear the runtime directory needs the
    difference.
    """
    refs = [ref for group in written.values() for ref in group]
    if not refs:
        return "No partition had been written when this build stopped."
    listed = ", ".join(f"{ref.dataset}:{ref.year}({ref.row_count} rows)" for ref in refs)
    return (
        f"{len(refs)} partition(s) were written before this build stopped and are still "
        f"stored: {listed}. A build is a sequence of whole-partition writes with no transaction "
        "around them, so an earlier target's partition survives a later target's refusal."
    )


def _audit_written_partitions(
    written: Mapping[str, Sequence[PartitionRef]], *, targets: frozenset[str], year: int
) -> None:
    """Refuse a build that answered `ok` without having built what it was asked for.

    Two failures, both of which reach a caller as an exit 0 with a `partitions` list that does
    not say what it looks like it says, and neither of which any writer below can see -- the
    writers are told what to store, not what was requested.

    1. **A requested target wrote no partition at all.** This is what a sixth entry in
       `PANEL_BUILD_TARGETS` with no matching branch in `_build_panel` produces: `_build_targets`
       accepts the name because the table has it, every `if` misses, and the command reports
       `exit 0` with `"partitions": []` -- the empty success this whole issue exists to make
       unavailable, at the one layer where nothing downstream can catch it. It is
       `internal_error` rather than `unhealthy` because no data produced it: the table and the
       branches are both this module's, and they have come apart.

    2. **A partition landed in a year other than the one asked for.** A partition's year comes
       from `panel_partition_year`, which reads the *rows' own dates*; `--year` only bounds the
       sessions that were fetched and the corpus that is read back. So a `suspend_d` fetch that
       serves rows dated last year is stored as last year's partition while
       `_build_price_panel` reads `load_suspensions(years=(year,))` -- the corpus a *previous*
       run left behind -- and the build reports `halts: corroborated` about a year it did not
       corroborate, plus a partition nobody asked for. `load_suspensions` cannot catch it: this
       command passes `max_staleness=None`, which is necessary and not a shortcut, because
       `suspend_d`'s freshness is measured in *event* time and the most recent halt event can
       legitimately be days before `as_of` -- but waiving it means a five-year-old corpus and
       this year's are the same observation to that call. The year is the check that survives.

       `stock_basic` is exempt and only `stock_basic` (`_LIFECYCLE_YEAR_TARGETS`): its
       partitions are lifecycle years by construction.
    """
    silent = sorted(name for name in targets if not written.get(name))
    if silent:
        raise _panel_fail(
            PanelExit.internal_error,
            f"{silent} is in PANEL_BUILD_TARGETS but produced no partition, so this build "
            "reported success without building it. That is a defect in this command -- a "
            "target the table accepts and no branch builds -- not a fact about the panel. "
            f"{_stored_so_far(written)}",
        )
    misfiled = [
        f"{ref.dataset}:{ref.year}"
        for name, group in written.items()
        if name not in _LIFECYCLE_YEAR_TARGETS
        for ref in group
        if ref.year != year
    ]
    if misfiled:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"--year {year} was asked for and {misfiled} was written: a partition's year comes "
            "from the dates in the rows the provider served, not from this flag, so the fetch "
            "returned another year's data. Anything this build read back for --year "
            f"{year} came from an earlier run rather than from this fetch, and any halt corpus "
            "it reported as corroborated was not corroborated by it. "
            f"{_stored_so_far(written)}",
        )


_BUILD_AS_OF_HELP = (
    "ISO-8601 instant this build's session horizon is derived from; defaults to the wall "
    "clock. The session loop runs to this instant's Asia/Shanghai date minus one day, so "
    "passing the same value to every target of one panel is what makes them stop on the same "
    "session -- see `_refuse_split_horizon`, which refuses the build rather than letting them "
    "diverge. Every run reports the instant it used, as `as_of` in --json and as the AS-OF "
    "line otherwise, so a later re-fetch of one target can be pinned to it."
)

_BUILD_DATASET_HELP = (
    "A build target, repeatable. The five this command builds, in the order it runs them: "
    f"{', '.join(PANEL_BUILD_TARGETS)}. Anything else is refused by name. One target is one "
    "unit of work a panel_ingest writer accepts, which is not always one dataset: 'price' is "
    "daily + daily_basic + suspend_d, because write_daily_panel takes the pair together and its "
    "halts argument has no default. 'stock_basic' ignores --year: the registry has no date "
    "filter and is split into one partition per lifecycle year."
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
    as_of: Annotated[str, typer.Option("--as-of", help=_BUILD_AS_OF_HELP)] = "",
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

    A refusal partway through names the partitions already on disk rather than claiming none
    are: the writers are each all-or-nothing, the build across them is not.

    **One clock per invocation, and `--as-of` is how a caller keeps it across several.** The
    session loop is bounded at the horizon this instant implies, and the five targets are five
    invocations, so a build that crosses local midnight otherwise lands a panel that no `as_of`
    can assess cleanly. The default stays the wall clock -- pinning is the deliberate act, not
    the ordinary one -- and `_refuse_split_horizon` is what stops the default from being a trap:
    a build whose horizon disagrees with a partition already stored is refused before it fetches
    anything, with the exact `--as-of` that would resolve it.
    """
    with _panel_command("panel build"):
        targets = _build_targets(dataset)
        store = _panel_store(runtime_dir)
        now = _panel_as_of(as_of)
        # **One instant for the whole invocation, and the provider gets it too.** The clock this
        # command bounds its session loop with (`_build_sessions`) and the clock the writers run
        # their session census against (`panel_ingest._session_census`, which reads
        # `batch.fetched_at`) are the same rule applied at two layers, and they were being given
        # two different readings of `datetime.now()`. Within one invocation that is the
        # cross-midnight defect in miniature: a `price` build that starts at 23:34 fetches the
        # sessions up to yesterday and then, if it finishes after 00:39, hands the writer batches
        # stamped with today -- so the census requires a session the loop was never asked for and
        # refuses the partition the build correctly assembled. Passing the same resolved instant
        # to both closes that, and it is what makes `--as-of` mean anything at all: pinning the
        # loop while the census still moved would only relocate the disagreement.
        #
        # The cost is stated rather than hidden: `fetched_at` becomes the instant the build
        # *started* rather than the instant each request returned, understating it by up to the
        # build's duration (~45 minutes on a whole year). That is immaterial to every consumer of
        # the field -- the census, whose bound is a whole day, and provenance -- and the value is
        # reported as `as_of` in this command's own output rather than being left to be inferred.
        provider = TushareProvider(transport=_panel_transport(), clock=lambda: now)
        written: dict[str, list[PartitionRef]] = {}
        try:
            sessions, halt_state = _build_panel(
                store,
                provider,
                written=written,
                targets=targets,
                year=year,
                exchange=exchange,
                halts=halts,
                now=now,
            )
        except _PANEL_WRITE_REFUSALS as error:
            raise _panel_fail(
                PanelExit.unhealthy,
                f"the panel refused this build: {error}. {_stored_so_far(written)}",
            ) from error
        except Exception:
            # Everything else that can stop a build midway: a provider failure or a stated
            # refusal raised as `typer.Exit` from inside the loop, or something unanticipated
            # that `_panel_command` will turn into `internal_error`. None of them can know how
            # far the build got, and by then partitions are on disk. Re-raised untouched --
            # this clause adds the one fact the raiser did not have, and decides nothing.
            typer.echo(_stored_so_far(written), err=True)
            raise
        _audit_written_partitions(written, targets=targets, year=year)

        stored = [ref for group in written.values() for ref in group]
        payload = {
            "year": year,
            "exchange": exchange,
            "targets": sorted(targets),
            "halts": halt_state,
            # The instant this build's horizon came from, reported whether it was passed or
            # defaulted. It is what a later `--dataset`-at-a-time re-fetch of this same panel
            # has to be pinned to, and a value a caller can only get by being told: `sessions`
            # below names the last session, not the instant that bounded the loop.
            "as_of": now.isoformat(),
            "sessions": {
                "first": sessions[0].isoformat() if sessions else None,
                "last": sessions[-1].isoformat() if sessions else None,
                "count": len(sessions),
            },
            "partitions": [
                {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                for ref in stored
            ],
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return
        typer.echo(f"AS-OF {now.isoformat()}")
        if sessions:
            typer.echo(
                f"SESSIONS {len(sessions)} from {sessions[0].isoformat()} to "
                f"{sessions[-1].isoformat()}"
            )
        for ref in stored:
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
    with _panel_command("panel doctor"):
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
            typer.echo(
                json.dumps(health_report_payload(report), ensure_ascii=False, sort_keys=True)
            )
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
    with _panel_command("data-check"):
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
