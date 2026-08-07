"""Command-line entry point for OpenAlpha CN."""

import json
import logging
import os
import platform
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus
from openalpha_cn.config import ConfigError, load_config, load_dotenv, load_log_level
from openalpha_cn.evidence.service import build_file_evidence, parse_serialized_evidence
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)
from openalpha_cn.providers.chainlin import ChainLinDataProvider
from openalpha_cn.providers.tushare import TushareProvider
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.migrations import MigrationFailedError, read_status

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
    response = build_file_evidence(
        path=path,
        as_of=point_in_time,
        metadata=metadata,
    )
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
