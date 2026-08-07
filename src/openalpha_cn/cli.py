"""Command-line entry point for OpenAlpha CN."""

import json
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
from openalpha_cn.evidence.service import build_file_evidence, parse_serialized_evidence
from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)
from openalpha_cn.providers.chainlin import ChainLinDataProvider
from openalpha_cn.providers.tushare import TushareProvider
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

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
    """Check runtime requirements, provider credentials, and declared capabilities."""
    timezone_ok = datetime.now().astimezone().utcoffset() is not None
    python_ok = sys.version_info >= (3, 11)
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
        "status": "ok" if python_ok and timezone_ok else "error",
        "checks": checks,
        "providers": providers,
        "warnings": warnings,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return

    for name, check in checks.items():
        typer.echo(f"{'PASS' if check['ok'] else 'FAIL'} {name}")
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


@research_app.command("run")
def research_run(
    evidence_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    run_id: Annotated[str, typer.Option("--run-id")] = "local-run",
    mode: Annotated[RunMode, typer.Option("--mode")] = RunMode.live,
    subject: Annotated[str, typer.Option("--subject")] = "",
    as_of: Annotated[str, typer.Option("--as-of")] = "",
    code_commit: Annotated[str, typer.Option("--code-commit")] = "development",
    config_digest: Annotated[str, typer.Option("--config-digest")] = "0" * 64,
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
            code_commit=code_commit,
            config_digest=config_digest,
            random_seed=random_seed,
        )
    )
    typer.echo(result.model_dump_json())


@replay_app.command("run")
def replay_run(
    corpus_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] = Path("./runtime"),
    code_commit: Annotated[str, typer.Option("--code-commit")] = "development",
    config_digest: Annotated[str, typer.Option("--config-digest")] = "0" * 64,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 7,
) -> None:
    """Run and validate a frozen replay corpus."""
    report = OpenAlphaSDK(runtime_dir=runtime_dir).replay(
        corpus=ReplayCorpus.load(corpus_path),
        code_commit=code_commit,
        config_digest=config_digest,
        random_seed=random_seed,
    )
    typer.echo(report.model_dump_json())


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.", min=1, max=65535)] = 8000,
) -> None:
    """Serve the versioned REST API."""
    uvicorn.run("openalpha_cn.api.app:app", host=host, port=port)


def main() -> None:
    """Run the command-line application."""
    app()


if __name__ == "__main__":
    main()
