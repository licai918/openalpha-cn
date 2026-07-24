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
from openalpha_cn.evidence.service import build_file_evidence
from openalpha_cn.providers.base import ProviderMetadata

app = typer.Typer(
    name="openalpha",
    help="Evidence-traceable A-share research.",
    no_args_is_help=True,
)
evidence_app = typer.Typer(help="Build and inspect point-in-time evidence.")
app.add_typer(evidence_app, name="evidence")


class Redistribution(StrEnum):
    """Allowed source-data redistribution states."""

    allowed = "allowed"
    restricted = "restricted"
    unknown = "unknown"


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
