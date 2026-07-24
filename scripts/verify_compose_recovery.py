"""Build the Compose stack and prove evidence survives a container restart."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose.yml"
AS_OF = "2026-07-24T10:30:00+00:00"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _compose(
    project: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
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
    project = f"openalpha-recovery-{os.getpid()}"
    port = _free_port()
    env = {**os.environ, "OPENALPHA_PORT": str(port)}
    base_url = f"http://127.0.0.1:{port}"
    try:
        _compose(project, env, "up", "--detach", "--build", "--wait")
        _wait_for_health(base_url)
        built = _request(f"{base_url}/api/v1/evidence/build", payload=_evidence_payload())
        evidence_id = built["items"][0]["evidence_id"]

        _compose(project, env, "restart", "openalpha")
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
            project,
            env,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
        )


if __name__ == "__main__":
    sys.exit(main())
