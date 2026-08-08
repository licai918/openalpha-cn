"""Build the Compose stack and prove evidence survives a container restart."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose.yml"
AS_OF = "2026-07-24T10:30:00+00:00"

_NO_COMPOSE_CLI_MESSAGE = (
    "Neither the Docker Compose v2 CLI plugin (`docker compose`) nor the standalone v1 "
    "`docker-compose` binary was found on PATH. Install the Compose plugin -- see "
    "https://docs.docker.com/compose/install/ -- or the standalone `docker-compose` "
    "binary, then re-run this script."
)


def _resolve_compose_command(
    *,
    which: Callable[[str], str | None] | None = None,
    probe: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> tuple[str, ...]:
    """Return the Compose CLI invocation to use, preferring the v2 plugin.

    P0.B acceptance review, Finding 2: `_compose()` used to hardcode
    `["docker", "compose", ...]` -- the v2 CLI plugin's invocation. A host with only the
    standalone v1 `docker-compose` binary (no v2 plugin at all, still common on older CI
    images and some Linux package managers) has no `docker compose` subcommand, so every
    `_compose()` call exited 1 with a bare `CalledProcessError`, even though
    `deploy/compose.yml` itself is fine (`docker-compose -f deploy/compose.yml config
    --quiet` succeeds) -- confirmed by the technical reviewer.

    Detection: `docker compose version` is the standard way to probe for the plugin
    without side effects; a non-zero exit or an outright failure to execute (permissions,
    a broken install) both count as "not available" and fall through to the standalone
    binary. This does not silently skip verification -- if neither is available, this
    raises a `RuntimeError` naming exactly what to install, and `main()` reports it on
    stderr and exits non-zero instead of letting a bare traceback stand in for an
    explanation.

    `which`/`probe` default to `None` rather than binding `shutil.which`/`subprocess.run`
    directly as default values: a default bound at function-definition time is captured
    once and does not observe a test's `monkeypatch.setattr(module.shutil, "which", ...)`
    afterwards, since that patches the module attribute, not the already-bound default.
    Resolving them here, at call time, is what lets a test drive `main()` end to end
    (not just this function directly) through a patched `shutil`/`subprocess`.
    """
    which = shutil.which if which is None else which
    probe = subprocess.run if probe is None else probe
    if which("docker") is not None:
        try:
            result = probe(["docker", "compose", "version"], capture_output=True, check=False)
        except OSError:
            result = None
        if result is not None and result.returncode == 0:
            return ("docker", "compose")
    if which("docker-compose") is not None:
        return ("docker-compose",)
    raise RuntimeError(_NO_COMPOSE_CLI_MESSAGE)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _compose(
    compose_command: Sequence[str],
    project: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> None:
    subprocess.run(
        [
            *compose_command,
            "--project-name",
            project,
            "--file",
            str(COMPOSE_FILE),
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=check,
    )


def _request(url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urlopen(request, timeout=10) as response:
        return cast(dict[str, Any], json.loads(response.read()))


def _wait_for_health(base_url: str, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _request(f"{base_url}/health")["status"] == "ok":
                return
        except (OSError, URLError, ValueError):
            time.sleep(0.5)
    raise TimeoutError("container did not become healthy")


def _evidence_payload() -> dict[str, Any]:
    timeline = {
        "event_time": "2026-07-24T09:30:00+00:00",
        "available_time": "2026-07-24T10:00:00+00:00",
        "ingested_time": "2026-07-24T10:01:00+00:00",
        "revision_time": "2026-07-24T10:00:00+00:00",
    }
    return {
        "metadata": {
            "provider_id": "recovery.synthetic",
            "display_name": "Recovery verification fixture",
            "source_license": "CC0-1.0",
            "redistribution": "allowed",
            "credential_env_vars": [],
            "caching_policy": "local-permitted",
            "rate_limit": "not-applicable",
            "freshness": "frozen fixture",
            "failure_semantics": "Invalid fixture is an explicit failure.",
        },
        "batch": {
            "schema_version": "provider-batch/v1",
            "provider_id": "recovery.synthetic",
            "request": {
                "dataset": "events",
                "as_of": AS_OF,
                "subjects": ["000001.SZ"],
            },
            "fetched_at": "2026-07-24T10:05:00+00:00",
            "status": "success",
            "records": [
                {
                    "schema_version": "provider-record/v1",
                    "subject": "000001.SZ",
                    "kind": "limit_up",
                    "timeline": timeline,
                    "source_uri": "fixture://compose-recovery",
                    "summary": "Compose recovery verification evidence.",
                    "payload": {
                        "close": 10.5,
                        "pct_change": 9.99,
                        "board_count": 1,
                    },
                }
            ],
        },
    }


def main() -> int:
    try:
        compose_command = _resolve_compose_command()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    project = f"openalpha-recovery-{os.getpid()}"
    port = _free_port()
    env = {**os.environ, "OPENALPHA_PORT": str(port)}
    base_url = f"http://127.0.0.1:{port}"
    try:
        _compose(compose_command, project, env, "up", "--detach", "--build", "--wait")
        _wait_for_health(base_url)
        built = _request(f"{base_url}/api/v1/evidence/build", payload=_evidence_payload())
        evidence_id = built["items"][0]["evidence_id"]

        _compose(compose_command, project, env, "restart", "openalpha")
        _wait_for_health(base_url)
        query = urlencode({"as_of": AS_OF, "subject": "000001.SZ"})
        restored = _request(f"{base_url}/api/v1/evidence?{query}")
        restored_ids = {item["evidence_id"] for item in restored["items"]}
        if evidence_id not in restored_ids:
            raise RuntimeError("persistent evidence was missing after container restart")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "project": project,
                    "evidence_id": evidence_id,
                    "restored_items": len(restored_ids),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        _compose(
            compose_command,
            project,
            env,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
        )


if __name__ == "__main__":
    sys.exit(main())
