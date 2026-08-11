"""Command-line entry point for OpenAlpha CN."""

import json
import logging
import os
import platform
import sys
from collections.abc import Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import MAXYEAR, MINYEAR, UTC, date, datetime, time, timedelta
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
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    FinancialStatementError,
)
from openalpha_cn.domain.index_membership import (
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_INDEX_CODES,
    IndexMembershipError,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_TREE_DATASET,
    IndustryClassificationError,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
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
    load_industry_trees,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
    merge_panel_batches,
    split_panel_batch_by_year,
    write_adjustment_factors,
    write_daily_panel,
    write_financial_statements,
    write_index_weights,
    write_industry_memberships,
    write_industry_tree,
    write_name_history,
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
    CURRENT_INDUSTRY_MEMBERSHIP,
    SUPERSEDED_INDUSTRY_MEMBERSHIP,
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
        NAMECHANGE_DATASET: (NAMECHANGE_DATASET,),
        INDEX_WEIGHT_DATASET: (INDEX_WEIGHT_DATASET,),
        INCOME_DATASET: (INCOME_DATASET,),
        BALANCE_SHEET_DATASET: (BALANCE_SHEET_DATASET,),
        CASH_FLOW_DATASET: (CASH_FLOW_DATASET,),
        INDUSTRY_TREE_DATASET: (INDUSTRY_TREE_DATASET,),
        INDUSTRY_MEMBERSHIP_DATASET: (INDUSTRY_MEMBERSHIP_DATASET,),
        FINANCIAL_INDICATOR_DATASET: (FINANCIAL_INDICATOR_DATASET,),
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

The order this table declares is the order the two build phases run their targets in, and it is
a dependency order rather than an alphabetical one: `adj_factor`, `price` and `stk_limit` all
read the stored calendar, so `trade_cal` has to have been written first on a fresh store; the
three statement targets read the stored registry, so `stock_basic` comes before them; and
`index_member_all` reads the stored tree, so `index_classify` comes before it. The command
therefore ignores the order the `--dataset` flags arrived in, which
`tests/integration/test_cli_panel.py::
test_panel_build_runs_the_targets_in_dependency_order_and_not_in_flag_order` pins by driving them
backwards.

**Thirteen targets, and the last three are not per-year.** `V2-P1-015` shipped five and refused
the other eight by name, because each is a different fetch plan and wiring them from a transport
that issue could not exercise would have been surface with no test behind it. The consequence was
that `providers/tushare.py`'s fifteen datasets and `panel_ingest`'s twelve writers had eight
datasets nothing could build: `panel build --dataset income` said "not one of this command's
build targets" and `panel doctor --dataset income` therefore reported `partition_missing`
forever, which is the state P2's `002`/`003`/`004` gates and P3's whole factor stack were
specified against. The eight are wired here, each with its measured request shape:

- `namechange` -- one announcement year of the whole market per request. One request per `--year`.
- `index_weight` -- one index for one calendar month. `INDEX_WEIGHT_INDEX_CODES` x 12 months,
  36 requests per `--year`; see `_build_index_weights` for the interior-gap refusal.
- `income` / `balancesheet` / `cashflow` -- one `(security, announcement year)` window, and
  `ts_code` is mandatory, so one request per security in the stored registry: **5,881 requests
  per `--year`, per dataset** (measured 2026-08-11).
- `index_classify` -- one taxonomy vintage per request; two requests for the whole invocation.
- `index_member_all` -- one `(l1_code, is_new)` slice; 31 x 2 = 62 requests for the whole
  invocation.
- `fina_indicator` -- one `(security, report-period year)` window; 5,881 requests per period year,
  for the whole invocation.

`PANEL_BUILD_SPAN_TARGETS` is why the last three say "for the whole invocation" rather than "per
`--year`", and it is a fact about their requests rather than a convenience.
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

PANEL_BUILD_SPAN_TARGETS: Final[frozenset[str]] = frozenset(
    {INDUSTRY_TREE_DATASET, INDUSTRY_MEMBERSHIP_DATASET, FINANCIAL_INDICATOR_DATASET}
)
"""Targets whose unit of work is the **whole invocation** rather than one `--year`, and why.

`panel build` runs its targets year by year, oldest first. Three of the thirteen cannot be run
that way, and in each case the reason is a property of the endpoint rather than a preference:

- **`index_classify`** takes a `src` and nothing else. One request is one vintage's whole tree,
  dated at that vintage's own effective day, so running it once per year would fetch the same
  511 rows twelve times and write the same 2021 partition twelve times.
- **`index_member_all`** takes an `(l1_code, is_new)` slice and no date filter at all. One sweep
  of 62 requests is the whole 7,893-row corpus, which `write_industry_memberships` files into
  ~38 event-year partitions; a per-year loop would re-fetch all 62 for each year and, worse,
  hand the writer a corpus it would file into the same 38 partitions each time.
- **`fina_indicator`** is the one where a per-year loop would be *destructive*, and this is the
  finding that put this set here. Its request window filters `end_date` -- the report period --
  while its rows are dated and filed at `ann_date`. So a request for period year *P* returns
  rows announced in *P* (the three interim reports) and in *P+1* (the annual), and an
  announcement year *A* is assembled from **at least two** period years: the annual of *A-1*
  plus the interims of *A*. `PanelStore` replaces a partition whole, so a loop that wrote period
  year 2015 and then period year 2016 would replace announcement year 2016's annual-2015 rows
  with 2016's interims -- more rows than before, the same securities, and no guard anywhere that
  can see it. Accumulating every requested period year into one write is the only shape that is
  not silently lossy; `_refuse_shrinking_statement_years` covers the cross-invocation half.

Every member is also in `_UNPINNED_PARTITION_YEAR_TARGETS`, necessarily: a target that does not
run per year cannot write the year it was asked for.
"""

_UNPINNED_PARTITION_YEAR_TARGETS: Final[frozenset[str]] = frozenset(
    {
        STOCK_BASIC_DATASET,
        INDUSTRY_TREE_DATASET,
        INDUSTRY_MEMBERSHIP_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
)
"""Targets whose partitions are not the `--year` that was asked for, and why that is legitimate.

Four, and each is exempt for its own measured reason rather than by family resemblance. Every
other target writes the year it was asked for, which is what `_audit_written_partitions` checks;
this set is the exemption, stated once so a future target cannot acquire it by accident.

- **`stock_basic`** has no date filter, so one request is the whole registry and
  `write_stock_universe` splits it into one partition per *lifecycle* year -- a security listed
  in 1991 goes to 1991 whatever `--year` said.
- **`index_classify`** has no date column at all. `providers/tushare.py` dates every node at its
  vintage's effective day, so SW2014's tree is a 2014 partition and SW2021's is a 2021 one, on
  every invocation and for every `--year`.
- **`index_member_all`** is filed by *membership event* year: an assignment that opened in 1993
  and closed in 2017 puts one row in each of those partitions. A 62-request sweep therefore
  lands in roughly 38 years at once.
- **`fina_indicator`** is asked for a report-period year and filed by announcement year, and the
  two are not the same year even in the ordinary case (`001278.SZ` announced its 2018 annual on
  2022-01-06). See `PANEL_BUILD_SPAN_TARGETS`.

Renamed from `_LIFECYCLE_YEAR_TARGETS`, which described the one member it used to have.
"""

_NEEDS_STORED_UNIVERSE: Final[frozenset[str]] = frozenset(FINANCIAL_STATEMENT_DATASETS)
"""Targets that cannot name their own subjects and read them out of the stored registry.

All four statement endpoints, and the reason is `_financial_statement_params`': `ts_code` is
**mandatory** on every one of them -- a request without it fails `code=50101`, and a comma-joined
list answers zero rows with `code=0` on three of the four -- so there is no cross-section fetch
and the securities have to come from somewhere. They come from `stock_basic`, exactly as the
session-scoped targets' sessions come from `trade_cal`, which is what `_NEEDS_STORED_CALENDAR`
says one dependency over. A caller may narrow it with `--subject`; nothing infers it.
"""

_NEEDS_STORED_INDUSTRY_TREE: Final[frozenset[str]] = frozenset({INDUSTRY_MEMBERSHIP_DATASET})
"""`index_member_all`, whose 31 `l1_code` slices are read off the stored `index_classify` tree.

The alternative was a 31-entry literal in this module, which would be a second copy of a table
the panel already stores and would go stale the day Shenwan adds an industry -- and would go
stale *silently*, because a missing `l1_code` is a slice nobody fetched rather than an error. The
tree is two requests and it is the join target for every membership row anyway, so requiring it
first costs one dependency and removes a constant that could drift.

**The vintage matters and is not defaulted.** `INDUSTRY_MEMBERSHIP_TAXONOMY` is SW2021 --
measured: every one of `index_member_all`'s 7,893 rows carries an SW2021 L1 code, and the
endpoint takes no `src` -- while `index_classify`'s own default is **SW2014**. Slicing SW2021
memberships by SW2014's 28 L1 codes would silently fetch a corpus missing three whole industries.
"""

_REGISTERED_PARTITION_RESUME: Final[frozenset[str]] = frozenset(
    {INDEX_WEIGHT_DATASET, INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET}
)
"""Targets `--resume` skips on a registered partition alone, with no census behind the skip.

Deliberately a separate, named set rather than "everything the session rule cannot judge",
because the evidence really is weaker and the difference should be legible at the call site as
well as in `_resumable_targets`' docstring, which is where the residue is stated and the test
that measures it is named. Four members: `index_weight` at 36 requests a year and the three
announcement-year statement targets at 5,881 each, which is the scale that makes a weak resume
worth more than no resume.

`namechange` is not here even though it is the same shape, for `trade_cal`'s reason: it is one
request a year, so skipping it saves nothing and costs a corpus the resumed build did not verify.
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
    IndustryClassificationError,
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

**`IndustryClassificationError` is that tenth**, added when `panel build` gained the two industry
targets. It is raised by `write_industry_memberships`, `write_industry_tree`,
`build_industry_tree` (a vintage whose parent chain is broken -- which is what a partial read of
the tree partition looks like) and `load_industry_trees`, and `index_member_all`'s own fetch plan
goes *through* that loader to get its 31 `l1_code` slices. So a malformed stored tree stops a
build, and without this entry it would have stopped it as `internal_error` with the message
withheld -- `SuspensionError`'s defect exactly, in a dataset that had not been built yet when
that one was found. `panel_doctor._LOAD_FAILURES` learns it in the same edit, as that test
requires; no cross-check raises it today, and the set is one question's answer rather than two
modules' separate inventories of what they happen to catch.
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
    # Two lines and not one, keyed on whether an entry names a dataset. A boundary of
    # `adj_factor` and a boundary of the store itself are different claims with different
    # audiences -- one is answered by choosing a different dataset, the other never is -- and
    # merging them would tell a reader of `panel doctor --dataset namechange` that `namechange`
    # has limitations when what has them is the plane underneath it. `panel_doctor`'s
    # `known_limitations` / `storage_limitations` split is the same distinction one layer down.
    #
    # Deliberately a count and not the list on both lines: the dataset half returns up to 55
    # entries, each a paragraph, and a human report that buried its own findings under them
    # would teach its readers to skim both -- the exact failure `PanelHealthReport` keeps
    # `limitations` a sibling of `findings` to avoid.
    scoped = [item for item in report.limitations if item.datasets]
    plane = [item for item in report.limitations if not item.datasets]
    if scoped:
        typer.echo(
            f"INFO {len(scoped)} known limitation(s) of these datasets "
            "(structural, not defects of this fetch); --json carries them in full"
        )
    if plane:
        typer.echo(
            f"INFO {len(plane)} structural boundary(ies) of the panel store itself "
            "(true of every dataset alike); --json carries them in full"
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


def _build_subjects(requested: Sequence[str], targets: frozenset[str]) -> tuple[str, ...]:
    """Resolve `--subject`, refusing it for every target that does not take one.

    `--subject` narrows the securities the four statement targets fetch, which is the one place
    in this command where the caller can shrink a whole-market fetch: `income` for one year is
    one request per security in the stored registry, so naming three securities turns 5,881
    requests into 3. Everything it is good for is that; everything else it could be pointed at
    is a dataset whose partition **is** the whole market, and narrowing one of those does not
    produce a smaller panel but a wrong one -- `_stock_basic_params` refuses a filtered registry
    for that reason in the provider, and `PanelStore` replaces a partition whole, so a narrowed
    write destroys a full one.

    Refused rather than ignored. A flag the command silently drops is indistinguishable from a
    flag the caller never passed, which is `_build_years`' finding about `--year` and the reason
    that one became repeatable; the difference here is that the silently-ignored version would
    also have looked like it worked.
    """
    asked = tuple(dict.fromkeys(requested))
    if not asked:
        return ()
    stray = sorted(targets - _NEEDS_STORED_UNIVERSE)
    if stray:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--subject narrows the securities a statement target fetches and {stray} "
            f"{'do' if len(stray) > 1 else 'does'} not take one: every other target's partition "
            "is the whole market, and a partition is replaced whole, so a narrowed write would "
            f"destroy a full one rather than build a smaller panel. Statement targets are "
            f"{sorted(_NEEDS_STORED_UNIVERSE)}",
        )
    return asked


def _year_as_of(year: int) -> datetime:
    """Midnight on 1 January of `year`, in the panel's own zone.

    What `trade_cal` is asked at: `_trade_cal_params` derives the fetched year from `as_of`'s
    Asia/Shanghai year, and `_calendar_publication_timeline` dates every row of a year as
    available from the start of that year, so this instant sees the whole year and no more.
    """
    return datetime(year, 1, 1, tzinfo=PANEL_DATE_ZONE)


def _year_end_as_of(year: int, now: datetime) -> datetime:
    """The last instant of `year` in the panel's zone, never later than this build's clock.

    What `namechange`, `income`, `balancesheet` and `cashflow` are asked at, and it has to be
    the *end* of the year rather than `_year_as_of`'s start. All four take a `{start_date,
    end_date}` window that the provider derives from `as_of`'s Asia/Shanghai year, and all four
    are clocked at the **announcement**: a row announced on 14 June is available from that day
    and no earlier, so a request made at 1 January fetches exactly the right window and
    `_decode_panel_rows` then drops every row in it except any announced on 1 January itself.
    `trade_cal` is the contrast that makes this a per-dataset choice rather than a global one:
    `_calendar_publication_timeline` dates a whole year's sessions as available from the start of
    that year, so `_year_as_of` is right there and would be wrong here. Measured 2026-08-11:
    `namechange` asked at the end of 2012 returns 320 rows whose announcement dates run
    2012-01-05..2012-12-31, and at the end of 2024, 330.

    `min(..., now)` is what makes the current year work. `_decode_panel_rows` bounds its filter at
    the earlier of `as_of` and the wall clock, so an `as_of` in December would already be safe --
    but `ColumnarPanelBatch.as_of` is a stored provenance field, and a partition that claims to
    answer as of a December that has not happened is a claim this command should not write down.
    The measured behaviour is the same either way: 381 `namechange` rows for 2026 on 2026-08-11.

    Refuses a year that has not begun, for `_build_sessions`' reason at the other end of the same
    question -- without it, `--year 2030` would fetch 2026's window, store a 2026 partition, and
    be caught only by `_audit_written_partitions`' misfiled-year check, which would report a
    fetch fault for what is a plain fact about the calendar.
    """
    opens_on = datetime(year, 1, 1, tzinfo=PANEL_DATE_ZONE)
    if now < opens_on:
        raise _panel_fail(
            PanelExit.bad_request,
            f"{year} had not begun at {now.isoformat()}; there is nothing to build yet",
        )
    return min(datetime(year, 12, 31, 23, 59, 59, tzinfo=PANEL_DATE_ZONE), now)


def _month_end_as_of(year: int, month: int, now: datetime) -> datetime | None:
    """The last instant of one calendar month, clamped at `now`, or `None` if it has not begun.

    `index_weight`'s request window. `_index_weight_params` derives `{start_date, end_date}` from
    `as_of`'s Asia/Shanghai *month* and the endpoint publishes on that month's last open session,
    so the instant has to sit at or after that session's 16:30 -- which the month's own last
    instant always does, and its first never does. Measured on 2026-08-11 for `000300.SH`: 19
    month-end requests (all twelve months of 2024, January to July of 2026) each returned exactly
    one 300-row publication, and 20 first-of-month requests over the same span returned `no_data`
    every time.

    `None` rather than a refusal for a month that has not begun, because a partial current year
    is the ordinary case rather than a fault: `_build_index_weights` is what decides whether the
    resulting gap is legitimate, and it can only decide that by looking at all twelve.
    """
    opens_on = datetime(year, month, 1, tzinfo=PANEL_DATE_ZONE)
    if now < opens_on:
        return None
    # The first of the following month, less one microsecond, is this month's last instant --
    # no table of month lengths and no leap-year branch, which is the arithmetic
    # `_index_weight_params` uses for the same boundary.
    next_month = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=PANEL_DATE_ZONE)
    return min(next_month - timedelta(microseconds=1), now)


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
"""The smallest number of requests one `panel build` progress line may cover.

Ten, against a year-to-date build of ~145 sessions, so a target reports about fifteen times --
often enough that a stalled fetch is visible within a minute or two, rarely enough that the
lines are not themselves the output. The measured builds this was chosen against: `adj_factor`
386--444s, `price` ~1,000--2,374s, `stk_limit` ~330s, whole build ~30--50 minutes, during which
this command printed **nothing at all** and a caller could not tell a live fetch from a wedged
one without `lsof`.

A *floor* rather than the stride itself since the statement targets arrived: those loop over the
5,881 securities of the stored registry rather than over 145 sessions, and a line every ten would
be 589 of them for one dataset-year. `_progress_stride` is what keeps both readable, and this
constant is what keeps every loop that existed before them reporting exactly as it did.
"""

PANEL_PROGRESS_REPORTS: Final[int] = 40
"""At most about this many progress lines from one loop, whatever its length.

Forty, and the number is a compromise between two things that cannot both be had on a loop of
5,881 requests: few enough lines that they are not themselves the output, and short enough gaps
that a wedged fetch is visible. At the 1.1--4.6s per request measured on 2026-08-11 a whole-market
statement year is roughly two to seven hours, so forty reports is one every three to ten minutes;
ten reports would have been one every twenty, and one every two minutes would be nearly six
hundred lines. The `BUDGET` line is what covers the interval before the first report.

It binds only above 400 requests: below that `PANEL_PROGRESS_EVERY` is the larger of the two, so
every loop this command already ran keeps its existing cadence to the line -- 145 sessions and
even a 244-session year still report every ten, and
`tests/integration/test_cli_panel_horizon.py` pins the eleven-session case unchanged.
"""


def _progress_stride(total: int) -> int:
    """How often to report over a loop of `total` requests. See the two constants above."""
    return max(PANEL_PROGRESS_EVERY, -(-total // PANEL_PROGRESS_REPORTS))


def _echo_progress(
    datasets: Sequence[str], done: int, total: int, started: float, *, unit: str = "sessions"
) -> None:
    """One progress line on stderr: what, how far, how long, and how much longer.

    stderr rather than stdout, for `_panel_fail`'s reason at the other end of the same command:
    `--json` has to stay parseable on stdout, and a progress line interleaved into it would make
    every scripted caller's `json.loads` fail. The remaining estimate is linear in requests made,
    which is the right model for every loop that uses it -- each iteration is one round trip of
    the same shape -- and it is labelled `eta` rather than presented as a promise.

    `unit` names what is being counted, because the loops are no longer all sessions: the
    statement targets count `securities`, `index_weight` counts `index-months` and
    `index_member_all` counts `industry-slices`. The default keeps every existing line
    byte-identical.
    """
    elapsed = monotonic() - started
    rate = elapsed / done if done else 0.0
    typer.echo(
        f"FETCHING {'+'.join(datasets)} {done}/{total} {unit} "
        f"elapsed={elapsed:.0f}s eta={rate * (total - done):.0f}s",
        err=True,
    )


def _echo_budget(label: str, total: int, unit: str, reason: str) -> None:
    """State the size of a fetch **before** it starts, on stderr.

    The statement targets turned this command's unit of cost from minutes into hours: `income`
    for one year is one request per security in the registry, 5,881 of them, and at the 1.1--4.6s
    per round trip measured on 2026-08-11 that is between two and seven hours for one
    dataset-year. A caller who typed `--start 2015 --end 2026` has asked for twelve times that,
    per dataset, and the only honest moment to say so is before the first request rather than in
    an `eta` that appears after ten minutes.

    Printed for every fetch loop, not only the expensive ones, so that the number a caller reads
    is always the same kind of number. `_echo_progress` then tracks it.
    """
    typer.echo(f"BUDGET {label} {total} {unit} ({reason})", err=True)


def _session_batches(
    provider: TushareProvider, datasets: Sequence[str], sessions: Sequence[date]
) -> dict[str, list[ColumnarPanelBatch]]:
    """Fetch every named dataset for every session, one pass over the sessions.

    Takes a *set* of datasets rather than one, so the price target's three-in-one-loop shape --
    which `write_daily_panel`'s docstring predicted of this command, and which is what lets a
    session's halts be fetched beside the bars they explain -- is the same code path a
    single-dataset target uses. A second copy of this loop for the price panel would leave the
    `_EMPTY_SESSION_IS_ORDINARY` branch dead in one of the two.

    Reports progress every `_progress_stride` sessions and once at the end, after stating the
    size of the loop up front. This was the one loop in the command that took minutes rather
    than seconds; the statement targets' loop now takes hours, which is why the cadence and the
    budget line are shared rather than written here.
    """
    collected: dict[str, list[ColumnarPanelBatch]] = {name: [] for name in datasets}
    _echo_budget(
        "+".join(datasets),
        len(sessions) * len(datasets),
        "requests",
        f"{len(sessions)} sessions x {len(datasets)} whole-market cross sections",
    )
    started = monotonic()
    stride = _progress_stride(len(sessions))
    for index, day in enumerate(sessions, start=1):
        for name in datasets:
            batch = _fetch_panel(provider, name, as_of=_session_as_of(day))
            if batch.status == "no_data" and name in _EMPTY_SESSION_IS_ORDINARY:
                continue
            collected[name].append(batch)
        if index % stride == 0 or index == len(sessions):
            _echo_progress(datasets, index, len(sessions), started)
    return collected


def _subject_batches(
    provider: TushareProvider,
    dataset: str,
    *,
    subjects: Sequence[str],
    as_of: datetime,
    label: str,
    reason: str,
    extra: tuple[str, ...] = (),
) -> list[ColumnarPanelBatch]:
    """Fetch one dataset once per subject, keeping the batches that carried rows.

    The statement targets' loop. `_session_batches`' shape with the axes swapped: there the
    request is a whole-market cross section and the loop is over days, here the request is one
    security's window and the loop is over the registry, because `_financial_statement_params`'
    `ts_code` is mandatory and a comma-joined list answers zero rows rather than an error on
    three of the four endpoints.

    **A `no_data` subject is ordinary here, and that is measured rather than assumed.** It is the
    opposite of `_EMPTY_SESSION_IS_ORDINARY`, which is a closed set of one because every other
    session-scoped dataset publishes on every open session. A security that announced nothing in
    a given year is the common case, not the exception: `000013.SZ` served no `income` row for
    the 2024 window on 2026-08-11 while `000003.SZ` -- delisted in 2002 -- served three. So the
    filter is on the whole loop rather than on a named dataset, and what stands behind it is the
    caller's own refusal when *nothing at all* came back (see `_build_statement_panel`).

    `extra` carries the second subject `fina_indicator` needs, the report-period year. It is a
    request subject and never a stored one -- `subject_field` reads `ts_code` off the row -- which
    is the arrangement `index_member_all` already has for `is_new`.
    """
    collected: list[ColumnarPanelBatch] = []
    _echo_budget(label, len(subjects), "requests", reason)
    started = monotonic()
    stride = _progress_stride(len(subjects))
    for index, subject in enumerate(subjects, start=1):
        batch = _fetch_panel(provider, dataset, as_of=as_of, subjects=(subject, *extra))
        if batch.status == "success":
            collected.append(batch)
        if index % stride == 0 or index == len(subjects):
            _echo_progress((dataset,), index, len(subjects), started, unit="securities")
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


def _stored_universe(store: PanelStore, *, now: datetime) -> tuple[str, ...]:
    """Every security the stored registry knows about, or an exit naming what to build first.

    `_NEEDS_STORED_CALENDAR`'s shape for the statement targets, and the same argument: the
    request needs something the panel already holds, so the build reads it rather than inventing
    it. What it reads is `stock_basic`, the only whole-market security list this repository has.

    ## Every security, not the ones that were listed that year

    The obvious saving is to skip a security that had not listed yet, or had already died -- and
    it is measurably unsound in **both** directions. `688981.SH` listed on the A-share market in
    2020 and its `income` window for 2015 returns five rows; `000003.SZ` was delisted in 2002
    and its `income` window for 2024 returns three. Both measured on 2026-08-11. An announcement
    year is a disclosure calendar rather than a trading calendar: pre-listing filings enter the
    corpus when a company registers, and restatements keep arriving long after a delisting. So a
    lifecycle filter would drop real filings, and would drop them silently -- the missing
    security would simply have no row, which is what "this company did not file" looks like.

    ## `require_years_through` rather than the bare registered years

    `load_stock_universe`'s own docstring names `store.registered_years(...)` as "the natural
    argument -- and it is also the trap", because passing it makes the request exactly equal to
    whatever the store happens to hold and a missing year can never be noticed. The switch it
    provides is used here: the read must cover a contiguous window through this build's own year,
    which on a complete ingest costs nothing (a live probe found lifecycle events in every year
    from 1990 to 2026) and on an incomplete one refuses rather than handing back a universe in
    which everything that died in the gap is still listed.

    `max_staleness=None` for `_build_price_panel`'s reason at the same layer: this read is asking
    which securities *exist*, not how fresh the registry is, and a bound chosen here would be
    chosen for the caller. A registry that has missed a month of listings yields a slightly small
    universe rather than a wrong one, and `--dataset stock_basic` in the same invocation refreshes
    it before this runs.
    """
    years = store.registered_years(STOCK_BASIC_DATASET)
    if not years:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the statement targets fetch one request per security and {STOCK_BASIC_DATASET} is "
            f"not in {store.root}: the endpoints require a ts_code and there is no cross-section "
            "fetch, so the registry is where the securities come from. Build it first: "
            "`openalpha panel build --dataset stock_basic --year <year>`, or name the securities "
            "with --subject",
        )
    universe = load_stock_universe(
        store,
        years=years,
        as_of=now,
        max_staleness=None,
        require_years_through=now.astimezone(PANEL_DATE_ZONE).year,
    )
    return tuple(entry.ts_code for entry in universe.securities)


def _build_statement_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    dataset: str,
    subjects: Sequence[str],
    as_of: datetime,
    label: str,
    reason: str,
    extra: tuple[str, ...] = (),
) -> list[ColumnarPanelBatch]:
    """Fetch one statement dataset for every subject and refuse a sweep that served nothing.

    Returns the batches rather than writing them, because the three announcement-year targets
    and `fina_indicator` write at different moments -- per year and once per invocation
    respectively (`PANEL_BUILD_SPAN_TARGETS`) -- and a helper that wrote would have to know which.

    The refusal is the counterweight to `_subject_batches` treating `no_data` as ordinary. One
    security with nothing to report is the common case; **every** security with nothing to report
    is a fetch that did not work, and without this it would reach `write_financial_statements` as
    an empty list and be refused by `merge_panel_batches` with "needs at least one batch" -- a
    true sentence about a list, several layers from the fetch that produced it.
    """
    batches = _subject_batches(
        provider, dataset, subjects=subjects, as_of=as_of, label=label, reason=reason, extra=extra
    )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"{label}: none of the {len(subjects)} securities served a filing. A security that "
            "announced nothing in a window is ordinary and all of them is not, so this is a "
            "fetch to investigate rather than an empty partition to write",
        )
    return batches


def _build_index_weights(
    store: PanelStore, provider: TushareProvider, *, year: int, now: datetime
) -> list[PartitionRef]:
    """Fetch every index-month of one year and write them as one partition.

    One request is one index for one calendar month (`_index_weight_params`), and a year's
    partition has to arrive in **one** call: `PanelStore` replaces a partition whole and its key
    has no index dimension, so a per-index loop leaves the year holding whichever index went last
    -- which `write_index_weights`' own docstring names, along with the per-month loop its subject
    guard cannot see. This function is the caller that docstring asks for.

    ## Which months are allowed to be missing

    The endpoint publishes on the last open session of each month, so a full past year has twelve
    publications and the current year has one per month that has finished publishing. Measured on
    2026-08-11: `000300.SH` returns exactly one 300-row publication for each of 2024's twelve
    months and for 2026 January through July, and `no_data` for August, whose last open session
    had not arrived.

    A gap at either **end** is therefore legitimate -- an index that had not launched yet at the
    start of the year (`000852.SH` begins in 2014), and the months of the current year that have
    not published. A gap in the **middle** is not, and it is refused here rather than left to the
    read: `domain/index_membership.py::build_index_membership` does refuse a hole in the month
    sequence, but only when the partition is loaded, so without this the build reports success and
    the panel is unreadable from then on.

    An index that published in **no** month of the year contributes nothing and is not refused,
    because a year entirely before an index's launch has no interior to have a hole in. What
    stops that from quietly shrinking a stored year is `panel_ingest._refuse_to_drop_stored_
    subjects`, whose subject column is the index precisely so it can see one go missing.

    ## The three indices are the build's scope, and this command offers no way to widen it

    `INDEX_WEIGHT_INDEX_CODES` is not a limit on what the descriptor can fetch -- it takes any
    `index_code` -- but it is a limit on what has been *measured*: the response cap, the monthly
    cadence, the weight-sum tolerance and the constituent-count exceptions in
    `domain/index_membership.py` were all established on those three, and a fourth index would
    inherit the code without inheriting any of that. `--subject` is deliberately not repurposed
    here; adding an index is a measurement, not a flag.
    """
    batches: list[ColumnarPanelBatch] = []
    months = [month for month in range(1, 13) if _month_end_as_of(year, month, now) is not None]
    total = len(months) * len(INDEX_WEIGHT_INDEX_CODES)
    _echo_budget(
        f"{INDEX_WEIGHT_DATASET} year={year}",
        total,
        "requests",
        f"{len(INDEX_WEIGHT_INDEX_CODES)} indices x {len(months)} month-end publications",
    )
    started = monotonic()
    stride = _progress_stride(total)
    done = 0
    for index_code in INDEX_WEIGHT_INDEX_CODES:
        published: list[int] = []
        for month in months:
            instant = _month_end_as_of(year, month, now)
            assert instant is not None  # `months` is exactly the ones that resolved
            batch = _fetch_panel(
                provider, INDEX_WEIGHT_DATASET, as_of=instant, subjects=(index_code,)
            )
            done += 1
            if batch.status == "success":
                published.append(month)
                batches.append(batch)
            if done % stride == 0 or done == total:
                _echo_progress((INDEX_WEIGHT_DATASET,), done, total, started, unit="index-months")
        absent = [
            month
            for month in months
            if published and published[0] < month < published[-1] and month not in published
        ]
        if absent:
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{index_code} published in months {published} of {year} and served nothing for "
                f"{absent}, which lie between two publications. A month inside an index's life "
                "with no publication is a hole rather than a horizon, and "
                "build_index_membership refuses one on every read -- so the partition this build "
                "would write could never be loaded",
            )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"none of {list(INDEX_WEIGHT_INDEX_CODES)} published a constituent weighting in "
            f"{year}; the earliest of the three begins in 2005 and the newest in 2014, so a year "
            "before that has no partition to write rather than an empty one",
        )
    return [write_index_weights(store, batches)]


def _build_industry_tree(
    store: PanelStore, provider: TushareProvider, *, now: datetime
) -> list[PartitionRef]:
    """Fetch every measured taxonomy vintage's whole tree, one partition each.

    Two requests, one per entry in `INDUSTRY_TAXONOMY_EFFECTIVE_FROM`, and both of them rather
    than only the one `index_member_all` speaks. The endpoint refuses an unmeasured `src` and
    answers a bare request with **SW2014** while every membership row is SW2021, so the vintage
    can never be defaulted; having refused the default, the remaining question is which of the two
    measured vintages to fetch, and the answer is that the set is closed and two requests long.
    Fetching only SW2021 would leave `panel doctor --dataset index_classify --year 2014` reporting
    `partition_missing` for a vintage this repository has measured, dated and can read back.

    `--year` does not scope this and cannot: the response carries no date column at all, so
    `providers/tushare.py` dates every node at its vintage's effective day and the partition year
    is 2014 or 2021 whatever was asked for. `_UNPINNED_PARTITION_YEAR_TARGETS` is the exemption.
    Measured on 2026-08-11: SW2014 is 359 nodes (28 L1 / 104 L2 / 227 L3) filed under 2014, and
    SW2021 is 511 (31 / 134 / 346) filed under 2021.
    """
    written: list[PartitionRef] = []
    _echo_budget(
        INDUSTRY_TREE_DATASET,
        len(INDUSTRY_TAXONOMY_EFFECTIVE_FROM),
        "requests",
        "one per measured taxonomy vintage; the partition year is the vintage's, not --year",
    )
    for taxonomy in sorted(INDUSTRY_TAXONOMY_EFFECTIVE_FROM):
        batch = _fetch_panel(provider, INDUSTRY_TREE_DATASET, as_of=now, subjects=(taxonomy,))
        if batch.status != "success":
            raise _panel_fail(
                PanelExit.unhealthy,
                f"{INDUSTRY_TREE_DATASET} served no node for vintage {taxonomy}, whose effective "
                f"date is {INDUSTRY_TAXONOMY_EFFECTIVE_FROM[taxonomy].isoformat()}. An empty tree "
                "would read as a taxonomy with no industries in it",
            )
        written.append(write_industry_tree(store, batch))
    return written


def _stored_level_one_codes(store: PanelStore, *, now: datetime) -> tuple[str, ...]:
    """The `l1_code` slices `index_member_all` is fetched in, read off the stored tree.

    `_stored_calendar`'s shape one dataset over: the request needs something the panel already
    holds. See `_NEEDS_STORED_INDUSTRY_TREE` for why it is read rather than written down here,
    and why the vintage is `INDUSTRY_MEMBERSHIP_TAXONOMY` rather than the endpoint's own default.
    """
    vintage_year = INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY].year
    try:
        trees = load_industry_trees(store, years=(vintage_year,), as_of=now, max_staleness=None)
    except (PanelStorageError, IndustryClassificationError) as error:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {INDUSTRY_MEMBERSHIP_TAXONOMY} industry tree could not be read out of "
            f"{store.root}: {error}. {INDUSTRY_MEMBERSHIP_DATASET} is fetched one l1_code slice "
            "at a time and the tree is where those codes come from. Build it first: `openalpha "
            "panel build --dataset index_classify --year <year>`",
        ) from error
    tree = trees.get(INDUSTRY_MEMBERSHIP_TAXONOMY)
    if tree is None:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"the {vintage_year} tree partition holds "
            f"{sorted(trees)} and not {INDUSTRY_MEMBERSHIP_TAXONOMY}, which is the one vintage "
            f"every {INDUSTRY_MEMBERSHIP_DATASET} row is labelled with",
        )
    return tuple(node.index_code for node in tree.nodes_at("L1"))


def _build_industry_memberships(
    store: PanelStore, provider: TushareProvider, *, codes: Sequence[str], now: datetime
) -> list[PartitionRef]:
    """Sweep every `(l1_code, is_new)` slice and write the corpus as one partition per event year.

    Two subjects per request and both mandatory. The `l1_code` slice is what keeps a response
    under this table's lowest cap (3,000 rows against a 7,893-row corpus); the `is_new` state is
    the refusal the dataset exists for -- a bare request returns the 5,889 **current** assignments
    with no flag and no short count to notice the 2,004 superseded ones by, which is a current
    snapshot indistinguishable from a complete history.

    So this loop asks for both states, and it refuses a sweep that came back with no superseded
    assignment at all. That is the one shape `write_industry_memberships` names as invisible to
    its own subject guard: a current-only corpus carries *every* security and reads as a market in
    which nobody has ever been reclassified. Measured on 2026-08-11, `801010.SI` alone answers
    126 current and 116 superseded, so a whole sweep with none of the latter is not a quiet market.

    `codes` is resolved by the caller, from the stored tree, for the reason `_build_panel` resolves
    the calendar before the targets that need it: a store with no `index_classify` in it is refused
    after zero round trips rather than after some of the 62.
    """
    states = (CURRENT_INDUSTRY_MEMBERSHIP, SUPERSEDED_INDUSTRY_MEMBERSHIP)
    total = len(codes) * len(states)
    _echo_budget(
        INDUSTRY_MEMBERSHIP_DATASET,
        total,
        "requests",
        f"{len(codes)} {INDUSTRY_MEMBERSHIP_TAXONOMY} l1_code slices x {len(states)} membership "
        "states; the partition years are the membership events', not --year",
    )
    batches: list[ColumnarPanelBatch] = []
    superseded = 0
    started = monotonic()
    stride = _progress_stride(total)
    done = 0
    for code in codes:
        for state in states:
            batch = _fetch_panel(
                provider, INDUSTRY_MEMBERSHIP_DATASET, as_of=now, subjects=(code, state)
            )
            done += 1
            if batch.status == "success":
                batches.append(batch)
                if state == SUPERSEDED_INDUSTRY_MEMBERSHIP:
                    superseded += batch.row_count
            if done % stride == 0 or done == total:
                _echo_progress(
                    (INDUSTRY_MEMBERSHIP_DATASET,),
                    done,
                    total,
                    started,
                    unit="industry-slices",
                )
    if not batches:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"none of the {total} {INDUSTRY_MEMBERSHIP_DATASET} slices served a row; an empty "
            "corpus would read as a market in which nothing has ever been classified",
        )
    if not superseded:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"this sweep fetched {len(codes)} l1_code slices in both membership states and every "
            f"is_new={SUPERSEDED_INDUSTRY_MEMBERSHIP!r} slice came back empty, so the corpus is "
            "the current snapshot alone. That is the one shape write_industry_memberships' "
            "subject guard cannot see -- it carries every security and no history, and reads as "
            "a market in which nobody has ever been reclassified",
        )
    return list(write_industry_memberships(store, batches))


def _refuse_shrinking_statement_years(
    store: PanelStore, *, dataset: str, batches: Sequence[ColumnarPanelBatch]
) -> None:
    """Refuse a `fina_indicator` write that would replace a stored year with fewer rows.

    ## The partition this stops from being destroyed

    `fina_indicator`'s request window filters `end_date` and its rows are filed by `ann_date`, so
    an announcement year is assembled from at least two report-period years: the annual of *A-1*
    plus the three interim reports of *A*. A partition is replaced whole. So a build over period
    years 2015..2026 writes announcement year 2016 with ~23,000 rows, and a later `--year 2016`
    on its own would write the same partition with only that period year's interims -- fewer
    rows, the *same* securities, and therefore nothing for
    `panel_ingest._refuse_to_drop_stored_subjects` to see. The rows that vanish are every annual
    report in that year, which is the one filing a value factor cannot do without.

    `PANEL_BUILD_SPAN_TARGETS` closes the half of this that happens inside one invocation, by
    accumulating every requested period year into one write. This closes the half that happens
    *across* invocations, which no amount of accumulation can.

    ## Why a row count, and what it does not claim

    It is not a claim that upstream never withdraws a row. It is a statement about this build:
    a write that shrinks a stored announcement year is one whose period-year span does not
    reproduce what that year already holds, and the remedy -- widen `--start`/`--end` -- is the
    caller's. If the publisher genuinely did withdraw filings, the refusal names the partition
    and clearing it is a deliberate act rather than a side effect of a narrower re-run.

    Scoped to `fina_indicator` because it is the only target whose partitions straddle its
    requests. The three announcement-year statement endpoints write exactly the year they were
    asked for from one request per security, so the only way to shrink one of those is
    `--subject`, which `_refuse_to_drop_stored_subjects` already refuses by name.
    """
    merged = merge_panel_batches(batches)
    shrinking: list[str] = []
    for year, yearly in split_panel_batch_by_year(merged):
        existing = store.read_coverage(dataset, year)
        if existing is not None and yearly.row_count < existing.row_count:
            shrinking.append(f"{year} holds {existing.row_count} and would get {yearly.row_count}")
    if shrinking:
        raise _panel_fail(
            PanelExit.unhealthy,
            f"this {dataset} build would replace stored announcement years with fewer rows than "
            f"they hold: {shrinking}. Its request window filters the report period and its rows "
            "are filed by announcement date, so an announcement year is assembled from at least "
            "two period years -- the annual of the year before plus the interims of the year "
            "itself -- and a narrower span cannot reproduce a wider one. Widen --start/--end to "
            "cover the period years that fed those partitions, or clear them deliberately if the "
            "publisher has withdrawn the filings",
        )


def _build_panel(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: dict[str, list[PartitionRef]],
    targets: frozenset[str],
    subjects: Sequence[str],
    year: int,
    exchange: str,
    halts: bool,
    now: datetime,
) -> tuple[tuple[date, ...], str]:
    """Run every requested **year-scoped** target in `PANEL_BUILD_TARGETS`' declared order.

    `written` is the caller's, keyed by target and filled as each partition lands, for two
    reasons that a returned value cannot serve. A build is a sequence of whole-partition writes
    with no transaction around them, so when a later target is refused the earlier ones are
    already on disk and the command has to be able to *name* them (`_stored_so_far`). And a
    target that this function accepted but wrote nothing for is only visible as an absent key
    (`_audit_written_partitions`) -- the failure a fourteenth entry in `PANEL_BUILD_TARGETS` with
    no branch below produces, which would otherwise be an exit 0 with an empty `partitions` list.

    The three `PANEL_BUILD_SPAN_TARGETS` are not run here: their unit of work is the whole
    invocation rather than one year, which `_build_span_targets` does once after this loop has
    finished. That set's docstring says why for each of them, and the reason is never
    convenience -- for `fina_indicator` a per-year loop is silently destructive.
    """
    sessions: tuple[date, ...] = ()
    calendar: TradingCalendar | None = None
    halt_state = "not-applicable"
    universe: tuple[str, ...] = ()

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
        # per lifecycle year. Recorded in the command's help, in
        # `_UNPINNED_PARTITION_YEAR_TARGETS` (which exempts it from the partition-year audit)
        # rather than papered over.
        written.setdefault(STOCK_BASIC_DATASET, []).extend(
            write_stock_universe(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now))
        )
    if targets & _NEEDS_STORED_UNIVERSE:
        # Resolved here rather than at the top of this function, and the position is the whole
        # point: after the `stock_basic` branch, so `--dataset stock_basic --dataset income` in
        # one invocation reads the registry this build just wrote; and before every remaining
        # target, so a store with no registry costs one round trip rather than an hour of
        # `price`. `_NEEDS_STORED_CALENDAR` sits at the same seam one line down for the same
        # reason.
        universe = tuple(subjects) or _stored_universe(store, now=now)
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
    if NAMECHANGE_DATASET in targets:
        written.setdefault(NAMECHANGE_DATASET, []).append(
            write_name_history(
                store,
                _fetch_panel(provider, NAMECHANGE_DATASET, as_of=_year_end_as_of(year, now)),
            )
        )
    if INDEX_WEIGHT_DATASET in targets:
        written.setdefault(INDEX_WEIGHT_DATASET, []).extend(
            _build_index_weights(store, provider, year=year, now=now)
        )
    for dataset in (INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        if dataset not in targets:
            continue
        written.setdefault(dataset, []).extend(
            write_financial_statements(
                store,
                _build_statement_panel(
                    store,
                    provider,
                    dataset=dataset,
                    subjects=universe,
                    # The **end** of the announcement year: these three filter `ann_date`, so a
                    # window taken at 1 January fetches the right year and the point-in-time
                    # filter then drops every row in it. See `_year_end_as_of`.
                    as_of=_year_end_as_of(year, now),
                    label=f"{dataset} year={year}",
                    reason=(
                        f"one per security; ts_code is mandatory on {dataset} and there is no "
                        "cross-section fetch"
                    ),
                ),
            )
        )
    return sessions, halt_state


def _build_span_targets(
    store: PanelStore,
    provider: TushareProvider,
    *,
    written: dict[str, list[PartitionRef]],
    targets: frozenset[str],
    subjects: Sequence[str],
    years: Sequence[int],
    now: datetime,
) -> None:
    """Run the `PANEL_BUILD_SPAN_TARGETS` once for the whole invocation, in table order.

    `_build_panel`'s counterpart for the three targets a per-year loop cannot serve, and it takes
    `years` rather than a year for the one of them that uses them at all: `fina_indicator`'s
    period years are exactly the years the caller named, accumulated into a single write because
    an announcement-year partition is assembled from several of them.

    Runs after the year loop so `stock_basic` and `index_classify` -- both of which a span target
    reads out of the store -- can have been written by the same invocation. That is the whole
    reason for the phase order rather than an accident of it: `openalpha panel build --dataset
    index_classify --dataset index_member_all` on a fresh store has to work, and it does, because
    the tree lands before the sweep that reads its `l1_code` slices.
    """
    universe: tuple[str, ...] = ()
    if targets & _NEEDS_STORED_UNIVERSE:
        # Before the first request of this phase rather than beside the branch that uses it: a
        # store with no registry would otherwise be refused after `index_member_all`'s 62 round
        # trips, which is the cost `_build_panel` resolves its own universe early to avoid.
        universe = tuple(subjects) or _stored_universe(store, now=now)
    if INDUSTRY_TREE_DATASET in targets:
        written.setdefault(INDUSTRY_TREE_DATASET, []).extend(
            _build_industry_tree(store, provider, now=now)
        )
    if targets & _NEEDS_STORED_INDUSTRY_TREE:
        codes = _stored_level_one_codes(store, now=now)
        written.setdefault(INDUSTRY_MEMBERSHIP_DATASET, []).extend(
            _build_industry_memberships(store, provider, codes=codes, now=now)
        )
    if FINANCIAL_INDICATOR_DATASET in targets:
        batches: list[ColumnarPanelBatch] = []
        for period_year in years:
            batches.extend(
                _build_statement_panel(
                    store,
                    provider,
                    dataset=FINANCIAL_INDICATOR_DATASET,
                    subjects=universe,
                    # `now`, not a year-derived instant. This endpoint's window comes from the
                    # period year in `extra`, so `as_of` does only its own job -- bounding what
                    # was knowable -- which is the split `_financial_indicator_params` exists for.
                    as_of=now,
                    label=f"{FINANCIAL_INDICATOR_DATASET} period-year={period_year}",
                    reason=(
                        "one per security; the report-period year is a request subject and the "
                        "partitions are announcement years"
                    ),
                    extra=(str(period_year),),
                )
            )
        _refuse_shrinking_statement_years(
            store, dataset=FINANCIAL_INDICATOR_DATASET, batches=batches
        )
        written.setdefault(FINANCIAL_INDICATOR_DATASET, []).extend(
            write_financial_statements(store, batches)
        )


def _all_refs(written: Mapping[str, Sequence[PartitionRef]]) -> list[PartitionRef]:
    """Every partition in a `written` mapping, flattened, in the order the targets ran."""
    return [ref for group in written.values() for ref in group]


def _stored_so_far(refs: Sequence[PartitionRef]) -> str:
    """What is on disk at the moment a build was refused, as a sentence a reader can act on.

    An earlier version of `panel_build` said "nothing partial was stored" here, and that was
    false whenever more than one partition was in flight. `_build_panel` writes whole partitions
    one after another with no transaction around them, so `panel build --dataset trade_cal
    --dataset price` that is refused by `write_daily_panel` leaves `trade_cal` **and**
    `suspend_d` in the store. Only the writer that raised is all-or-nothing; the build is not,
    and a caller deciding whether to re-run or to clear the runtime directory needs the
    difference.

    Takes a flat sequence rather than the per-year `written` mapping because a build now spans
    years (`--start`/`--end`): the partitions on disk when the fourth year is refused include
    the three that finished, and a mapping keyed by target alone cannot hold them.
    """
    if not refs:
        return "No partition had been written when this build stopped."
    listed = ", ".join(f"{ref.dataset}:{ref.year}({ref.row_count} rows)" for ref in refs)
    return (
        f"{len(refs)} partition(s) were written before this build stopped and are still "
        f"stored: {listed}. A build is a sequence of whole-partition writes with no transaction "
        "around them, so an earlier target's partition survives a later target's refusal."
    )


def _audit_written_partitions(
    written: Mapping[str, Sequence[PartitionRef]],
    *,
    targets: frozenset[str],
    year: int | None,
) -> None:
    """Refuse a build that answered `ok` without having built what it was asked for.

    Two failures, both of which reach a caller as an exit 0 with a `partitions` list that does
    not say what it looks like it says, and neither of which any writer below can see -- the
    writers are told what to store, not what was requested.

    `year` is `None` for the span phase, where there is no `--year` a partition could be checked
    against: `_build_span_targets` runs once per invocation and every one of its targets is in
    `_UNPINNED_PARTITION_YEAR_TARGETS` anyway. Check 1 still runs, and has to -- a fourteenth
    entry in `PANEL_BUILD_TARGETS` with no branch is exactly as invisible in that phase as in the
    other, and adding a phase without extending this audit is the same defect wearing a new coat.

    1. **A requested target wrote no partition at all.** This is what a fourteenth entry in
       `PANEL_BUILD_TARGETS` with no matching branch produces: `_build_targets`
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

       Four targets are exempt and only those four (`_UNPINNED_PARTITION_YEAR_TARGETS`), each
       because its partition year is a property of the rows rather than of the request:
       `stock_basic`'s are lifecycle years, `index_classify`'s are taxonomy vintages,
       `index_member_all`'s are membership-event years and `fina_indicator`'s are announcement
       years derived from a report-period request.
    """
    silent = sorted(name for name in targets if not written.get(name))
    if silent:
        raise _panel_fail(
            PanelExit.internal_error,
            f"{silent} is in PANEL_BUILD_TARGETS but produced no partition, so this build "
            "reported success without building it. That is a defect in this command -- a "
            "target the table accepts and no branch builds -- not a fact about the panel. "
            f"{_stored_so_far(_all_refs(written))}",
        )
    if year is None:
        return
    misfiled = [
        f"{ref.dataset}:{ref.year}"
        for name, group in written.items()
        if name not in _UNPINNED_PARTITION_YEAR_TARGETS
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
            f"{_stored_so_far(_all_refs(written))}",
        )


def _build_years(years: Sequence[int], start: int | None, end: int | None) -> tuple[int, ...]:
    """Resolve `--year` / `--start` / `--end` into the years this build will run, ascending.

    ## Why `--year` is repeatable, and why the old shape was the dangerous kind of wrong

    It used to be `Annotated[int, ...]`, which Click resolves by keeping the **last** value and
    discarding the rest without a word: `panel build --dataset trade_cal --year 2025 --year
    2026` printed `WROTE trade_cal year=2026` and 2025 simply never happened. Silently dropping
    an argument a caller passed is indistinguishable from that caller never having passed it,
    so nothing downstream -- not the exit code, not `--json`, not `panel doctor` run on a year
    nobody built -- can tell the two apart. It was also the same flag name `panel doctor`
    already took as a `list[int]`, so one word meant two things one command apart.

    ## Why the range form exists as well

    The P1 gate is `panel build --start 2015 --end 2026`, and writing that as twelve `--year`
    flags is a different sentence from the one the roadmap asks for. Both forms resolve here so
    there is exactly one place that decides what "the years" are; passing both is refused
    rather than merged, because `--year 2019 --start 2015 --end 2026` has two readings and
    neither is obviously the intended one.

    Every refusal below is `bad_request`: no re-fetch fixes an argument list.
    """
    named = tuple(dict.fromkeys(years))
    ranged = start is not None or end is not None
    if named and ranged:
        raise _panel_fail(
            PanelExit.bad_request,
            f"--year {list(named)} and --start/--end were both given, and a build cannot be "
            "scoped two ways at once: --year names the years, --start/--end names a closed "
            "range. Pass one form",
        )
    if not named and not ranged:
        raise _panel_fail(
            PanelExit.bad_request,
            "no year was given: pass --year (repeatable) or a closed --start/--end range. "
            "Nothing is inferred -- a build that picked its own years would be a fetch nobody "
            "asked for, against a quota this command does not own",
        )
    if ranged:
        if start is None or end is None:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--start and --end are a closed range and only "
                f"{'--start' if end is None else '--end'} was given; the open end has no "
                "defensible default, so name both",
            )
        if end < start:
            raise _panel_fail(
                PanelExit.bad_request,
                f"--start {start} is after --end {end}; a build runs oldest year first and "
                "this range is empty",
            )
        named = tuple(range(start, end + 1))
    for value in named:
        if not MINYEAR <= value <= MAXYEAR:
            raise _panel_fail(
                PanelExit.bad_request,
                f"{value} is not a year this calendar can represent ({MINYEAR}-{MAXYEAR})",
            )
    return tuple(sorted(named))


def _resumable_targets(
    store: PanelStore,
    *,
    targets: frozenset[str],
    year: int,
    exchange: str,
    now: datetime,
) -> tuple[frozenset[str], tuple[date, ...]]:
    """Which of `targets` this year already holds in full, and the sessions that proves it.

    ## What `--resume` is, and what it deliberately is not

    It is **year-granular** and it reads only what the store already recorded. A target is
    skipped for a year when every session-scoped dataset it writes already reaches the last
    session this build would fetch -- `_stored_horizon` against `_build_sessions`, which is
    `_refuse_split_horizon`'s own comparison turned around.

    The horizon rather than the whole session set, and that is not a weaker check: both
    write-time censuses (`panel_ingest._session_census`, used by the price panel and by
    `_refuse_missing_factor_sessions`) require **every** open session from 1 January to the day
    before the fetch, so a partition that exists at all is complete up to the horizon its build
    ran to, and the horizon is the only remaining degree of freedom. Comparing the full census
    instead would be wrong rather than stricter: `adj_factor` is stored as a *compressed* step
    function whose census holds only the load-bearing sessions, so an equality against the
    calendar would never match and `--resume` would silently never resume. So the evidence is
    the data the writers already accepted -- no progress file, no second on-disk format,
    nothing to go stale.

    It is **not** intra-year resumption, and that is a judgement rather than an omission. A
    `PanelStore` partition is written whole (there is no append) and `_session_census` requires
    every open session from 1 January, so a half-fetched year cannot be stored as a readable
    partition at all -- it would have to live in a second format with its own staleness and
    integrity questions, and the failure mode of getting that wrong is a partition that *looks*
    complete and is not, which is the one failure this whole module is built to make impossible.
    What is bought instead is that the unit of loss for `--start 2015 --end 2026` is one year
    rather than twelve; the bounded retry in `TushareProvider._post` is what keeps a transient
    socket error from costing even that.

    `trade_cal`, `stock_basic` and `namechange` are never skipped and are not evidence for
    skipping anything else. Each is a **single** request -- twelve of them across the whole gate
    range, against the ~2,900 a year of `price` costs -- so skipping them would save nothing
    measurable while making a resumed build read a calendar it did not verify. `suspend_d` is not
    evidence either, for `_EMPTY_SESSION_IS_ORDINARY`'s reason: a session on which nothing was
    halted serves zero rows, so its census is a fact about the market. A complete `daily`
    partition is the stronger witness anyway -- `write_daily_panel` refuses a session whose
    missing bars nothing accounts for, so that partition existing means the halt corpus was read.

    ## The second rule, and it is weaker on purpose rather than by oversight

    `_REGISTERED_PARTITION_RESUME` -- `index_weight` and the three announcement-year statement
    targets -- is skipped on a **registered partition** alone, because there is nothing stronger
    to read. Their datasets have no session census: a security that announced nothing in a year
    is absent from the partition and indistinguishable from one that was never fetched, so
    "which securities should be here" has no answer the store can give. What the rule buys is the
    only thing that matters at this scale -- `income` alone is 5,881 requests per year, so a
    twelve-year build that dies in the eleventh costs one year rather than eleven.

    What it cannot see is a partition an earlier `--subject` run narrowed: that partition is
    registered, so `--resume` skips it, and the year stays narrow. The residue is left visible
    rather than argued away -- `tests/integration/test_cli_panel.py` pins exactly that case, so
    it is a measured limitation and not a claim -- and it is bounded on the other side by
    `panel_ingest._refuse_to_drop_stored_subjects`, which refuses the narrowing write itself
    whenever a wider partition is already there. Re-running without `--resume` is the remedy and
    is always available.

    ## `PANEL_BUILD_SPAN_TARGETS` are never skipped, and `fina_indicator` cannot be

    `index_classify` is two requests and `index_member_all` is 62, so for both the answer is
    `trade_cal`'s. `fina_indicator` is 5,881 requests per period year and is the one target here
    that would genuinely benefit -- and it is *structurally* unresumable, not merely unimplemented:
    it writes nothing until every requested period year has been fetched, because an announcement
    year is assembled from several of them, so there is no intermediate state for a resume to
    read. The lever a caller has instead is a narrower `--start`/`--end`, at the cost
    `_refuse_shrinking_statement_years` states.
    """
    skippable = {name for name in targets if name in _NEEDS_STORED_CALENDAR}
    registered = {
        name
        for name in targets & _REGISTERED_PARTITION_RESUME
        if year in store.registered_years(name)
    }
    if not skippable:
        return frozenset(registered), ()
    if year not in store.registered_years(TRADING_CALENDAR_DATASET):
        return frozenset(registered), ()
    calendar = _stored_calendar(store, exchange=exchange, years=(year,), as_of=now)
    sessions = _build_sessions(calendar, year, now)
    reached = sessions[-1]
    resumed = {
        target
        for target in skippable
        if all(
            _stored_horizon(store, name, year) == reached
            for name in PANEL_BUILD_TARGETS[target]
            if name in SESSION_SCOPED_DATASETS
        )
    }
    return frozenset(resumed | registered), sessions


_BUILD_AS_OF_HELP = (
    "ISO-8601 instant this build's session horizon is derived from; defaults to the wall "
    "clock. The session loop runs to this instant's Asia/Shanghai date minus one day, so "
    "passing the same value to every target of one panel is what makes them stop on the same "
    "session -- see `_refuse_split_horizon`, which refuses the build rather than letting them "
    "diverge. Every run reports the instant it used, as `as_of` in --json and as the AS-OF "
    "line otherwise, so a later re-fetch of one target can be pinned to it. It pins what this "
    "build *stamps* -- fetched_at and every row's ingested_time, which is what makes a re-fetch "
    "of an unchanged year a true no-op -- and never what the provider's point-in-time filter "
    "judges rows against, which is always the wall clock."
)

_BUILD_DATASET_HELP = (
    "A build target, repeatable. The thirteen this command builds, in the order it runs them: "
    f"{', '.join(PANEL_BUILD_TARGETS)}. Anything else is refused by name. One target is one "
    "unit of work a panel_ingest writer accepts, which is not always one dataset: 'price' is "
    "daily + daily_basic + suspend_d, because write_daily_panel takes the pair together and its "
    f"halts argument has no default. Four of them do not write the --year they were given, "
    f"because their partition year comes from the rows rather than from the request "
    f"({', '.join(sorted(_UNPINNED_PARTITION_YEAR_TARGETS))}): the registry is split by "
    "lifecycle year, the industry tree by taxonomy vintage, the memberships by event year, and "
    "fina_indicator is asked for a report-period year and filed by announcement year. The last "
    f"three of those ({', '.join(sorted(PANEL_BUILD_SPAN_TARGETS))}) run once for the whole "
    "invocation rather than once per year."
)

_BUILD_SUBJECT_HELP = (
    "A ts_code the statement targets fetch, repeatable. Only income, balancesheet, cashflow and "
    "fina_indicator take one -- naming it for any other target is refused rather than ignored, "
    "because their partitions are the whole market and a partition is replaced whole. Without "
    "it the securities come from the stored stock_basic registry, which is 5,881 requests per "
    "dataset per year; with it, one per name. Nothing is inferred from --year: a security that "
    "had not listed yet can still have filings announced in a window (688981.SH answers the "
    "2015 window) and one delisted in 2002 can still have filings announced in 2024 "
    "(000003.SZ), both measured, so no lifecycle filter is applied."
)


_BUILD_YEAR_HELP = (
    "A partition year to build, repeatable and mutually exclusive with --start/--end. Years "
    "run oldest first. Until V2-P1-019 this was a single value, which Click resolved by "
    "keeping the last one and discarding the rest in silence."
)

_BUILD_START_HELP = (
    "First year of a closed range to build, oldest first. Requires --end and refuses --year."
)

_BUILD_END_HELP = "Last year of a closed range to build, inclusive. Requires --start."

_BUILD_RESUME_HELP = (
    "Skip a target for a year whose stored partitions already reach the last session this "
    "build would fetch, so an interrupted multi-year build costs one year rather than all of "
    "them. Year-granular and evidence-based -- the census the writers already validated, not a "
    "progress file. trade_cal, stock_basic and namechange are never skipped, and there is no "
    "intra-year resumption. index_weight and the three announcement-year statement targets are "
    "skipped on a registered partition alone, which is weaker: it cannot tell a whole-market "
    "year from one an earlier --subject run narrowed. fina_indicator cannot be resumed at all, "
    "because it writes nothing until every requested period year has been fetched. See "
    "`_resumable_targets`. Off by default: a rebuild that quietly fetched nothing would be the "
    "wrong default for a command whose ordinary job is to replace what is there."
)


@panel_app.command("build")
def panel_build(
    dataset: Annotated[list[str], typer.Option("--dataset", help=_BUILD_DATASET_HELP)],
    year: Annotated[list[int] | None, typer.Option("--year", help=_BUILD_YEAR_HELP)] = None,
    start: Annotated[int | None, typer.Option("--start", help=_BUILD_START_HELP)] = None,
    end: Annotated[int | None, typer.Option("--end", help=_BUILD_END_HELP)] = None,
    subject: Annotated[
        list[str] | None, typer.Option("--subject", help=_BUILD_SUBJECT_HELP)
    ] = None,
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
    resume: Annotated[bool, typer.Option("--resume", help=_BUILD_RESUME_HELP)] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit a machine-readable build report.")
    ] = False,
) -> None:
    """Fetch and store one or more years of the panel plane through the real writers.

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
    anything, with the exact `--as-of` that would resolve it. `--as-of` pins what this command
    *stamps*, never what the provider's point-in-time filter judges rows against; see the
    comment at the `TushareProvider` construction below and `TushareProvider._stamp`.

    **Years run oldest first, one `_build_panel` each, and a refusal stops the whole run.**
    That order is what makes `--resume` mean anything -- the years before the refusal are the
    ones already on disk -- and it is why the refusal names both what landed and the exact
    command that carries on from there. There is no transaction across years any more than
    there is one across targets.

    **Two phases, and the second is not an optimisation.** The year loop runs the ten year-scoped
    targets; `_build_span_targets` then runs the three in `PANEL_BUILD_SPAN_TARGETS` once for the
    whole invocation, because their requests carry no year (`index_classify`, `index_member_all`)
    or carry one that is not the partition's (`fina_indicator`, whose announcement years are
    assembled from several report-period years and which a per-year loop would silently truncate).
    The span phase runs second so that `stock_basic` and `index_classify`, which it reads out of
    the store, can have been written by the same invocation.

    **What a whole-market build now costs.** The five original targets were ~2,900 requests for a
    year. `income`, `balancesheet` and `cashflow` are one request per security each -- 5,881 on
    2026-08-11 -- so a single year of the four statement endpoints is ~23,500 round trips, and
    `--start 2015 --end 2026` is ~282,000. At the 1.1--4.6s per request measured on that date the
    twelve-year statement backfill is days rather than hours, and the account's own 500-per-minute
    quota is not the binding constraint at that latency. Every fetch loop therefore states its
    size before it starts (`_echo_budget`) and reports progress with an `eta` while it runs, and
    `--subject` is the lever that turns the registry sweep into a named handful.
    """
    with _panel_command("panel build"):
        targets = _build_targets(dataset)
        subjects = _build_subjects(subject or (), targets)
        years = _build_years(year or (), start, end)
        year_targets = targets - PANEL_BUILD_SPAN_TARGETS
        span_targets = targets & PANEL_BUILD_SPAN_TARGETS
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
        #
        # **`stamped_at`, not `clock`.** `V2-P1-018` passed the resolved instant as the
        # provider's `clock`, which pinned what the provider *stamps* -- the point above -- and
        # also, silently, what it *judges* rows against: `TushareProvider._decode_panel_rows`
        # keeps a row only if it was knowable both at the request's `as_of` and at the instant
        # the fetch ran, and the second half read the same clock. A `--as-of` ahead of the wall
        # clock therefore stored a cross section that had not published. The provider now takes
        # the two apart: `stamped_at` is pinned here, `clock` stays the wall clock this module
        # reads its own default from, and the caller can no longer raise the ceiling on what a
        # fetch may know. `_panel_clock` rather than the provider's own default so that a test
        # which moves this module's clock moves the provider's too.
        provider = TushareProvider(transport=_panel_transport(), clock=_panel_clock, stamped_at=now)
        stored: list[PartitionRef] = []
        builds: list[dict[str, object]] = []
        for index, one_year in enumerate(years):
            resumed, covered = (
                _resumable_targets(
                    store, targets=year_targets, year=one_year, exchange=exchange, now=now
                )
                if resume
                else (frozenset[str](), ())
            )
            fetched = year_targets - resumed
            written: dict[str, list[PartitionRef]] = {}
            sessions: tuple[date, ...] = covered
            halt_state = "resumed" if resumed and not fetched else "not-applicable"
            try:
                if fetched:
                    sessions, halt_state = _build_panel(
                        store,
                        provider,
                        written=written,
                        targets=fetched,
                        subjects=subjects,
                        year=one_year,
                        exchange=exchange,
                        halts=halts,
                        now=now,
                    )
                    sessions = sessions or covered
            except _PANEL_WRITE_REFUSALS as error:
                raise _panel_fail(
                    PanelExit.unhealthy,
                    f"the panel refused this build: {error}. "
                    f"{_stored_so_far([*stored, *_all_refs(written)])}"
                    f"{_years_left(years, index)}",
                ) from error
            except Exception:
                # Everything else that can stop a build midway: a provider failure or a stated
                # refusal raised as `typer.Exit` from inside the loop, or something
                # unanticipated that `_panel_command` will turn into `internal_error`. None of
                # them can know how far the build got, and by then partitions are on disk.
                # Re-raised untouched -- this clause adds the one fact the raiser did not have,
                # and decides nothing.
                typer.echo(
                    f"{_stored_so_far([*stored, *_all_refs(written)])}{_years_left(years, index)}",
                    err=True,
                )
                raise
            _audit_written_partitions(written, targets=fetched, year=one_year)
            landed = _all_refs(written)
            stored.extend(landed)
            builds.append(
                {
                    "year": one_year,
                    "halts": halt_state,
                    "resumed": sorted(resumed),
                    "sessions": {
                        "first": sessions[0].isoformat() if sessions else None,
                        "last": sessions[-1].isoformat() if sessions else None,
                        "count": len(sessions),
                    },
                    "partitions": [
                        {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                        for ref in landed
                    ],
                }
            )

        span_written: dict[str, list[PartitionRef]] = {}
        if span_targets:
            try:
                _build_span_targets(
                    store,
                    provider,
                    written=span_written,
                    targets=span_targets,
                    subjects=subjects,
                    years=years,
                    now=now,
                )
            except _PANEL_WRITE_REFUSALS as error:
                raise _panel_fail(
                    PanelExit.unhealthy,
                    f"the panel refused this build: {error}. "
                    f"{_stored_so_far([*stored, *_all_refs(span_written)])}",
                ) from error
            except Exception:
                # `_build_panel`'s clause, for the phase that has no year to carry on from: the
                # span targets are one unit of work across the whole invocation, so there is no
                # `--start` that resumes them and `_years_left` would name a range that means
                # nothing here.
                typer.echo(
                    _stored_so_far([*stored, *_all_refs(span_written)]),
                    err=True,
                )
                raise
            _audit_written_partitions(span_written, targets=span_targets, year=None)
            stored.extend(_all_refs(span_written))

        payload = {
            "years": list(years),
            "exchange": exchange,
            "targets": sorted(targets),
            "halts": _one_halt_state(builds),
            # What the span phase wrote, kept apart from `builds` because it belongs to no year:
            # `index_classify`'s partitions are taxonomy vintages, `index_member_all`'s are
            # membership-event years and `fina_indicator`'s are announcement years assembled from
            # every requested period year. Folding them into one year's entry would attribute
            # them to a year that did not produce them.
            "span": {
                "targets": sorted(span_targets),
                "partitions": [
                    {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                    for ref in _all_refs(span_written)
                ],
            },
            # The instant this build's horizon came from, reported whether it was passed or
            # defaulted. It is what a later `--dataset`-at-a-time re-fetch of this same panel
            # has to be pinned to, and a value a caller can only get by being told: `sessions`
            # below names the last session, not the instant that bounded the loop.
            "as_of": now.isoformat(),
            # The span across every year this invocation covered. Identical to the single
            # year's own entry when one year was asked for, which is every caller that existed
            # before `--start`/`--end`; `builds` is where a multi-year run is legible.
            "sessions": _session_span(builds),
            "builds": builds,
            "partitions": [
                {"dataset": ref.dataset, "year": ref.year, "row_count": ref.row_count}
                for ref in stored
            ],
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return
        typer.echo(f"AS-OF {now.isoformat()}")
        for entry in builds:
            span = cast(Mapping[str, object], entry["sessions"])
            if span["count"]:
                typer.echo(
                    f"SESSIONS {entry['year']} {span['count']} from {span['first']} to "
                    f"{span['last']}"
                )
            for name in cast(Sequence[str], entry["resumed"]):
                typer.echo(f"RESUMED {name} year={entry['year']} ({_resume_evidence(name)})")
            for ref in cast(Sequence[Mapping[str, object]], entry["partitions"]):
                typer.echo(f"WROTE {ref['dataset']} year={ref['year']} rows={ref['row_count']}")
        for landed_ref in _all_refs(span_written):
            typer.echo(
                f"WROTE {landed_ref.dataset} year={landed_ref.year} "
                f"rows={landed_ref.row_count} (span)"
            )
        typer.echo(f"HALTS {_one_halt_state(builds)}")


def _resume_evidence(target: str) -> str:
    """Why `--resume` skipped this target, in the output rather than only in a docstring.

    The two rules are not equally strong (`_resumable_targets`), and a caller reading `RESUMED
    income year=2024` has no way to know which one applied. Saying so on the line is what makes
    the weaker one a disclosure instead of a silence: a partition an earlier `--subject` run
    narrowed is registered, and this is the only place the difference is visible at the moment it
    matters.
    """
    if target in _REGISTERED_PARTITION_RESUME:
        return "partition registered; this rule does not check which securities it holds"
    return "already complete"


def _years_left(years: Sequence[int], index: int) -> str:
    """The command that carries on from the year a multi-year build stopped in, or nothing.

    Printed beside `_stored_so_far` for the same reason that sentence exists: a build across
    twelve years that dies in the fourth has eight left, and "re-run it" is not a remedy when
    the first three cost hours. Empty for a single-year build, which has nothing to carry on
    from and where the extra sentence would be noise.

    The finished years are named only when there are some. `Years [] finished` is a true
    sentence and a bad one, and the range below already says everything a build that stopped in
    its first year needs -- which is the same range it was given.
    """
    if len(years) == 1:
        return ""
    finished = list(years[:index])
    stopped = f"Years {finished} finished; {years[index]}" if finished else f"{years[index]}"
    return (
        f" {stopped} is the one that stopped. Carry on with "
        f"--start {years[index]} --end {years[-1]} --resume."
    )


def _one_halt_state(builds: Sequence[Mapping[str, object]]) -> str:
    """The halt state every built year reported, or `mixed` when they disagree.

    One invocation carries one `--halts` flag, so the states agree unless `--resume` skipped a
    year (`resumed`) beside one that was fetched. Total rather than "the last one wins": a
    single word that silently described one year of twelve is exactly the reporting this
    command's own `_audit_written_partitions` exists to refuse elsewhere.
    """
    states = {str(entry["halts"]) for entry in builds}
    return states.pop() if len(states) == 1 else "mixed"


def _session_span(builds: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """First session, last session and total count across every year of one invocation."""
    spans = [cast(Mapping[str, object], entry["sessions"]) for entry in builds]
    covered = [span for span in spans if span["count"]]
    return {
        "first": covered[0]["first"] if covered else None,
        "last": covered[-1]["last"] if covered else None,
        "count": sum(cast(int, span["count"]) for span in spans),
    }


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
