"""Command-line entry point for OpenAlpha CN."""

import json
import platform
import sys
from datetime import datetime
from typing import Annotated

import typer

from openalpha_cn import __version__

app = typer.Typer(
    name="openalpha",
    help="Evidence-traceable A-share research.",
    no_args_is_help=True,
)


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


def main() -> None:
    """Run the command-line application."""
    app()


if __name__ == "__main__":
    main()
