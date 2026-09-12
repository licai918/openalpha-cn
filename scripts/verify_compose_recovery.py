"""Build the Compose stack, judge the running container's security posture, and prove evidence
survives a container restart."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose.yml"
AS_OF = "2026-07-24T10:30:00+00:00"

# What `docs/deployment/production.zh-CN.md` §8 says the container is, in the terms the kernel
# and the account files report it.
SERVICE = "openalpha"
ACCOUNT = "openalpha"
SERVICE_UID = 10001
SERVICE_GID = 10001
ACCOUNT_HOME = "/nonexistent"
ACCOUNT_SHELL = "/usr/sbin/nologin"
# `/tmp:size=64m` in deploy/compose.yml is `size=65536k` in /proc/self/mounts; the other three are
# the options Docker gives every tmpfs it mounts, and they are part of what makes /tmp restricted.
TMP_OPTIONS = frozenset({"size=65536k", "nosuid", "nodev", "noexec"})
# Filesystems whose contents do not outlive the container: memory, or a view of the kernel.
VOLATILE_FILESYSTEMS = frozenset(
    {"tmpfs", "proc", "sysfs", "devpts", "mqueue", "cgroup", "cgroup2"}
)
_CAPABILITY_SETS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
_PASSWD_EXPECTED = (
    (2, "uid", str(SERVICE_UID)),
    (3, "gid", str(SERVICE_GID)),
    (5, "home", ACCOUNT_HOME),
    (6, "shell", ACCOUNT_SHELL),
)

# Runs inside the container as the service's own user and only *collects*; every judgement is made
# by the pure functions below, which tests/unit/test_verify_compose_recovery.py exercises without
# Docker. It travels as a `python -c` argument because python is the one interpreter the image is
# certain to have.
_POSTURE_PROBE = r"""
import errno
import json
import os
import re


def read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def attempt_write(directory):
    path = os.path.join(directory, ".posture-probe-%d" % os.getpid())
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as error:
        return errno.errorcode.get(error.errno, str(error.errno))
    os.unlink(path)
    return "ok"


processes = {}
for pid in os.listdir("/proc"):
    if pid.isdigit():
        try:
            processes[pid] = read("/proc/%s/status" % pid)
        except OSError:
            pass  # it exited between the listing and the read

mounts = []
for line in read("/proc/self/mounts").splitlines():
    _, target, fstype, options = line.split()[:4]
    target = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), target)
    mounts.append([target, fstype, options.split(","), os.access(target, os.W_OK)])

print(json.dumps({
    "self": str(os.getpid()),
    "processes": processes,
    "writes": {directory: attempt_write(directory) for directory in ("/", "/data", "/tmp")},
    "mounts": mounts,
    "passwd": [line for line in read("/etc/passwd").splitlines() if line.startswith("openalpha:")],
    "group": [line for line in read("/etc/group").splitlines() if line.startswith("openalpha:")],
    "home_exists": os.path.lexists("/nonexistent"),
}))
"""

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


def _compose_argv(compose_command: Sequence[str], project: str, *args: str) -> list[str]:
    return [*compose_command, "--project-name", project, "--file", str(COMPOSE_FILE), *args]


def _compose(
    compose_command: Sequence[str],
    project: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> None:
    subprocess.run(_compose_argv(compose_command, project, *args), cwd=ROOT, env=env, check=check)


def _compose_output(
    compose_command: Sequence[str],
    project: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> str:
    """Run one Compose command and return its stdout. stderr is not captured: it goes to the
    log, so a failing `exec` explains itself there."""
    completed = subprocess.run(
        _compose_argv(compose_command, project, *args),
        cwd=ROOT,
        env=env,
        check=check,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )
    return completed.stdout


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


def _status_fields(status: str) -> dict[str, str]:
    """`/proc/<pid>/status` as `{field: value}`."""
    fields: dict[str, str] = {}
    for line in status.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key] = value.strip()
    return fields


def _judged_processes(processes: Mapping[str, str], probe: str) -> list[str]:
    """The pids the posture is judged on: PID 1, everything descended from it, and the probe.

    PID 1's tree is the service -- `docker-init`, and uvicorn under it. A process `docker exec`
    starts has no parent inside the container, so it is outside that tree: the probe is judged
    because it stands for the exec path, and anything else exec'd alongside it (the healthcheck
    runs every ten seconds) is not the service and is left out.
    """
    parents = {pid: _status_fields(status).get("PPid", "") for pid, status in processes.items()}

    def in_service(pid: str) -> bool:
        seen: set[str] = set()
        while pid not in seen:
            if pid == "1":
                return True
            seen.add(pid)
            pid = parents.get(pid, "")
        return False

    return sorted((pid for pid in processes if pid == probe or in_service(pid)), key=int)


def _process_problems(pid: str, status: str) -> list[str]:
    fields = _status_fields(status)
    process = f"pid {pid} ({fields.get('Name', 'unnamed')})"
    problems: list[str] = []
    for key, expected in (("Uid", SERVICE_UID), ("Gid", SERVICE_GID)):
        ids = fields.get(key, "").split()
        if ids != [str(expected)] * 4:
            problems.append(
                f"{process}: {key} is {' '.join(ids) or 'missing'}, not {expected} as its real, "
                "effective, saved and filesystem id"
            )
    groups = fields.get("Groups", "").split()
    if set(groups) - {str(SERVICE_GID)}:
        problems.append(
            f"{process}: Groups is {' '.join(groups)}, and {SERVICE_GID} is the only group it "
            "may carry"
        )
    # All five sets, and `CapBnd` is the one that carries `cap_drop: ALL`: a non-root process's
    # effective and permitted sets are emptied by the kernel at exec whatever Compose drops.
    for key in _CAPABILITY_SETS:
        value = fields.get(key, "")
        if not re.fullmatch(r"0+", value):
            problems.append(
                f"{process}: {key} is {value or 'missing'}; cap_drop: ALL leaves every "
                "capability set empty"
            )
    if fields.get("NoNewPrivs") != "1":
        problems.append(
            f"{process}: NoNewPrivs is {fields.get('NoNewPrivs', 'missing')}, not the 1 that "
            "no-new-privileges:true sets"
        )
    return problems


def _mount_table(mounts: Sequence[Sequence[Any]]) -> dict[str, tuple[str, frozenset[str], bool]]:
    """`{mount point: (filesystem, options, writable by the service's account)}`. A later mount
    on the same point hides an earlier one, so the last entry for a point is the one in effect."""
    return {
        str(target): (str(fstype), frozenset(options), bool(writable))
        for target, fstype, options, writable in mounts
    }


def _writable_persistent_mounts(table: Mapping[str, tuple[str, frozenset[str], bool]]) -> list[str]:
    return sorted(
        target
        for target, (fstype, _, writable) in table.items()
        if writable and fstype not in VOLATILE_FILESYSTEMS
    )


def _filesystem_problems(writes: Mapping[str, str], mounts: Sequence[Sequence[Any]]) -> list[str]:
    table = _mount_table(mounts)
    problems: list[str] = []
    root = table.get("/")
    if root is None or "ro" not in root[1]:
        problems.append("/ is not mounted read-only, which is what read_only: true does")
    if writes.get("/") != "EROFS":
        problems.append(
            f"creating a file in / gave {writes.get('/', 'no answer')}, not EROFS; as uid "
            f"{SERVICE_UID}, EACCES says only that / belongs to root, which it does on a "
            "writable root filesystem too"
        )
    if writes.get("/data") != "ok":
        problems.append(
            f"creating a file in /data gave {writes.get('/data', 'no answer')}; /data is the "
            "one directory the service has to be able to write"
        )
    data = table.get("/data")
    if data is None or data[0] in VOLATILE_FILESYSTEMS:
        problems.append("/data is not a mount of a persistent filesystem (the runtime volume)")
    tmp = table.get("/tmp")
    if tmp is None or tmp[0] != "tmpfs":
        found = "not a mount point" if tmp is None else f"a {tmp[0]} mount"
        problems.append(f"/tmp is {found}, not a tmpfs")
    elif missing := sorted(TMP_OPTIONS - tmp[1]):
        problems.append(f"the /tmp tmpfs lacks {', '.join(missing)}")
    if writes.get("/tmp") != "ok":
        problems.append(f"creating a file in /tmp gave {writes.get('/tmp', 'no answer')}")
    others = [target for target in _writable_persistent_mounts(table) if target != "/data"]
    if others:
        problems.append(
            "the service's account can write persistent filesystems besides /data: "
            + ", ".join(others)
        )
    return problems


def _account_problems(
    passwd: Sequence[str], group: Sequence[str], home_exists: bool, shadow: str
) -> list[str]:
    problems: list[str] = []
    fields = passwd[0].split(":") if len(passwd) == 1 else []
    if len(fields) != 7:
        problems.append(f"/etc/passwd has no single well-formed {ACCOUNT} entry: {list(passwd)}")
    for position, name, expected in _PASSWD_EXPECTED if len(fields) == 7 else ():
        if fields[position] != expected:
            problems.append(f"the {ACCOUNT} account's {name} is {fields[position]}, not {expected}")
    if home_exists:
        problems.append(f"{ACCOUNT_HOME} exists; the account's home is meant never to be created")
    if [entry.split(":")[2:3] for entry in group] != [[str(SERVICE_GID)]]:
        problems.append(f"/etc/group does not give {ACCOUNT} gid {SERVICE_GID}: {list(group)}")
    entries = [line for line in shadow.splitlines() if line.startswith(f"{ACCOUNT}:")]
    if len(entries) != 1:
        problems.append(
            f"/etc/shadow has {len(entries)} {ACCOUNT} entries, so whether the account has a "
            "password is unknown"
        )
    elif not re.fullmatch(r"[!*]+", entries[0].split(":")[1]):
        # Never printed: if this is a hash, it stays out of the CI log.
        problems.append(
            f"the {ACCOUNT} account has a password: its /etc/shadow field is more than a lock"
        )
    return problems


def _posture_problems(report: Mapping[str, Any], shadow: str) -> list[str]:
    """Every way the container differs from what production.zh-CN.md §8 says it is; empty when
    it does not. `report` is the probe's output, `shadow` the account's `/etc/shadow` line."""
    processes: Mapping[str, str] = report["processes"]
    problems: list[str] = []
    if "1" not in processes:
        problems.append("the probe saw no pid 1, so the service's own processes went unjudged")
    for pid in _judged_processes(processes, report["self"]):
        problems.extend(_process_problems(pid, processes[pid]))
    problems.extend(_filesystem_problems(report["writes"], report["mounts"]))
    problems.extend(
        _account_problems(report["passwd"], report["group"], report["home_exists"], shadow)
    )
    return problems


def _posture_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """What was judged, printed with the result so that a green run shows its evidence."""
    processes: Mapping[str, str] = report["processes"]
    table = _mount_table(report["mounts"])
    tmp = table.get("/tmp")
    return {
        "processes": [
            f"{pid} {_status_fields(processes[pid]).get('Name', 'unnamed')}"
            for pid in _judged_processes(processes, report["self"])
        ],
        "writes": dict(report["writes"]),
        "tmp": None if tmp is None else ",".join([tmp[0], *sorted(TMP_OPTIONS & tmp[1])]),
        "writable_persistent_mounts": _writable_persistent_mounts(table),
        "account": report["passwd"][0] if report["passwd"] else None,
    }


def _verify_posture(
    compose_command: Sequence[str], project: str, env: dict[str, str]
) -> dict[str, Any]:
    """Judge the running container against production.zh-CN.md §8; raise if it falls short.

    The probe is exec'd with no `--user`, so it runs as the service's own account under the
    service's own restrictions: what it can write, the service can write. `/etc/shadow` is not
    readable by that account, so whether the account has a password is read by a second exec,
    as uid 0.
    """
    report = json.loads(
        _compose_output(
            compose_command, project, env, "exec", "-T", SERVICE, "python", "-c", _POSTURE_PROBE
        )
    )
    shadow = _compose_output(
        compose_command,
        project,
        env,
        *("exec", "-T", "--user", "0", SERVICE, "getent", "shadow", ACCOUNT),
        check=False,
    )
    problems = _posture_problems(report, shadow)
    if problems:
        raise RuntimeError(
            "the running container is not what docs/deployment/production.zh-CN.md §8 says it "
            "is:\n" + "\n".join(f"  - {problem}" for problem in problems)
        )
    return _posture_summary(report)


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
        posture = _verify_posture(compose_command, project, env)
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
                    "posture": posture,
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
