"""Command-line entry point for OpenAlpha CN."""

import json
import platform
import sys
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus
from openalpha_cn.evidence.service import build_file_evidence, parse_serialized_evidence
from openalpha_cn.providers.base import ProviderMetadata
from openalpha_cn.runtime.engine import ResearchRunRequest
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


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable health report."),
    ] = False,
) -> None:
    """Check the minimum local runtime requirements."""
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
    payload = {
        "status": "ok" if python_ok and timezone_ok else "error",
        "checks": checks,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return

    for name, check in checks.items():
        typer.echo(f"{'PASS' if check['ok'] else 'FAIL'} {name}")
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
