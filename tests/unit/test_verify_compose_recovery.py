"""`scripts/verify_compose_recovery.py` detects the Docker Compose CLI and falls back
instead of hardcoding the v2 plugin invocation (P0.B acceptance review, Finding 2).

The technical reviewer confirmed `deploy/compose.yml` itself is fine --
`docker-compose -f deploy/compose.yml config --quiet` succeeds -- the defect is narrowly
that `_compose()` hardcoded `["docker", "compose", ...]`, so a host with only the
standalone v1 `docker-compose` binary (no v2 CLI plugin) got a bare `CalledProcessError`
and exit 1, even though the README lists this script as a standard verification step.

`scripts/` has no package `__init__.py`, so the module is loaded by path -- the same
pattern `tests/unit/test_repository_assets.py::_load_verify_publication` already uses for
`scripts/verify_publication.py`.
"""

import importlib.util
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_verify_compose_recovery() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_compose_recovery", ROOT / "scripts" / "verify_compose_recovery.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_verify_compose_recovery()


def _which(available: dict[str, str]) -> Any:
    def _lookup(name: str) -> str | None:
        return available.get(name)

    return _lookup


def test_prefers_the_v2_compose_plugin_when_docker_compose_version_succeeds(
    module: ModuleType,
) -> None:
    which = _which({"docker": "/usr/bin/docker", "docker-compose": "/usr/local/bin/docker-compose"})

    def probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, returncode=0)

    assert module._resolve_compose_command(which=which, probe=probe) == ("docker", "compose")


def test_falls_back_to_the_standalone_v1_binary_when_the_v2_plugin_is_absent(
    module: ModuleType,
) -> None:
    """Finding 2's exact reproduction: `docker` exists but has no `compose` subcommand
    (the v2 CLI plugin is not installed), while the standalone `docker-compose` binary is
    on PATH. Before this fix, `_compose()` hardcoded `["docker", "compose", ...]` and a
    host in this shape got a bare `CalledProcessError` and exit 1."""
    which = _which({"docker": "/usr/bin/docker", "docker-compose": "/usr/local/bin/docker-compose"})

    def probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, returncode=1)

    assert module._resolve_compose_command(which=which, probe=probe) == ("docker-compose",)


def test_falls_back_when_the_docker_binary_itself_is_missing(module: ModuleType) -> None:
    which = _which({"docker-compose": "/usr/local/bin/docker-compose"})

    def probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("must not probe `docker compose version` when docker is absent")

    assert module._resolve_compose_command(which=which, probe=probe) == ("docker-compose",)


def test_falls_back_when_the_v2_probe_raises_instead_of_merely_failing(module: ModuleType) -> None:
    """A `docker` binary can exist but genuinely fail to execute (permissions, a broken
    install) -- not just report a non-zero exit. That must fall through to the v1 binary
    too, not propagate and crash the script."""
    which = _which({"docker": "/usr/bin/docker", "docker-compose": "/usr/local/bin/docker-compose"})

    def probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("exec format error")

    assert module._resolve_compose_command(which=which, probe=probe) == ("docker-compose",)


def test_fails_loudly_naming_what_to_install_when_neither_is_available(module: ModuleType) -> None:
    """Do not silently skip verification (the brief's explicit requirement): raise a
    clear, actionable error instead of returning something empty or swallowing the gap."""
    which = _which({})

    def probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("must not probe when docker itself is absent")

    with pytest.raises(RuntimeError) as exc_info:
        module._resolve_compose_command(which=which, probe=probe)

    message = str(exc_info.value)
    assert "docker compose" in message
    assert "docker-compose" in message


def test_compose_invocation_uses_the_resolved_command_prefix_not_a_hardcoded_one(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_compose()` must build its subprocess argv from the resolved command it is given,
    not the old hardcoded `["docker", "compose"]` literal."""
    captured: dict[str, list[str]] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._compose(("docker-compose",), "proj", {}, "config", "--quiet")

    assert captured["args"][:3] == ["docker-compose", "--project-name", "proj"]


def test_main_fails_with_a_clear_message_instead_of_a_bare_calledprocesserror(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Structural regression guard for the finding's headline symptom: on a host with
    neither compose CLI, `main()` must return a non-zero exit with a readable message
    printed to stderr -- not let a `CalledProcessError`/`FileNotFoundError` traceback
    surface as the only explanation."""
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    exit_code = module.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "docker-compose" in captured.err


def test_compose_output_returns_what_the_command_printed_through_the_same_invocation(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The posture probe's report comes back on stdout; stderr is left alone, so a failing exec
    explains itself in the log instead of vanishing into a captured buffer."""
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"], captured["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, returncode=0, stdout="probe output\n")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    output = module._compose_output(
        ("docker-compose",), "proj", {}, "exec", "-T", "openalpha", "id"
    )

    assert output == "probe output\n"
    assert captured["args"][:3] == ["docker-compose", "--project-name", "proj"]
    assert captured["args"][-4:] == ["exec", "-T", "openalpha", "id"]
    assert captured["kwargs"]["stdout"] == subprocess.PIPE
    assert "stderr" not in captured["kwargs"]


# --- the running container's security posture: docs/deployment/production.zh-CN.md §8 ----------
#
# `main()` execs a probe inside the container Compose starts and judges its report with pure
# functions, so every judgement below is tested without Docker. `_shipped_report()` is the report
# the probe returned from a container `deploy/compose.yml` started on the image this repository
# builds, with two things cut: each `/proc/<pid>/status` down to the fields the judge reads, and
# the overlay's `lowerdir`/`upperdir`/`workdir` paths out of `/`'s mount options.

_ZERO_CAPS = "0000000000000000"
# `CapBnd` of a uid-10001 process in the shipped image run *without* `cap_drop: ALL`: Docker's
# default fourteen capabilities. Measured, as is the fact that `CapEff` stays zero in that run.
_DOCKER_DEFAULT_BOUNDING_SET = "00000000a80425fb"
_SHIPPED_SHADOW = "openalpha:!:20708::::::\n"
_TMPFS = ["rw", "nosuid", "nodev", "noexec", "relatime", "size=65536k", "inode64"]
_PROC_READ_ONLY = ["ro", "nosuid", "nodev", "noexec", "relatime"]
_PROC_MASK = ["rw", "nosuid", "size=65536k", "mode=755", "inode64"]
_MASK_READ_ONLY = ["ro", "relatime", "inode64"]


# pid: (Name, PPid). `init: true` makes docker-init PID 1, uvicorn runs under it, and the probe is
# exec'd, so it has no parent inside the container.
_SHIPPED_PROCESSES = {"1": ("docker-init", "0"), "7": ("python", "1"), "18": ("python", "0")}


def _status(pid: str, **fields: str) -> str:
    name, parent = _SHIPPED_PROCESSES.get(pid, ("python", "0"))
    values = {
        "Name": name,
        "PPid": parent,
        "Uid": "10001\t10001\t10001\t10001",
        "Gid": "10001\t10001\t10001\t10001",
        "Groups": "10001 ",
        "CapInh": _ZERO_CAPS,
        "CapPrm": _ZERO_CAPS,
        "CapEff": _ZERO_CAPS,
        "CapBnd": _ZERO_CAPS,
        "CapAmb": _ZERO_CAPS,
        "NoNewPrivs": "1",
        "Seccomp": "2",
    }
    values.update(fields)
    return "".join(f"{key}:\t{value}\n" for key, value in values.items())


def _shipped_report() -> dict[str, Any]:
    return {
        "self": "18",
        "processes": {pid: _status(pid) for pid in _SHIPPED_PROCESSES},
        "writes": {"/": "EROFS", "/data": "ok", "/tmp": "ok"},
        "mounts": [
            ["/", "overlay", ["ro", "relatime", "nouserxattr"], False],
            ["/proc", "proc", ["rw", "nosuid", "nodev", "noexec", "relatime"], False],
            ["/dev", "tmpfs", ["rw", "nosuid", "size=65536k", "mode=755", "inode64"], False],
            [
                "/dev/pts",
                "devpts",
                ["rw", "nosuid", "noexec", "relatime", "gid=5", "mode=620", "ptmxmode=666"],
                False,
            ],
            ["/sys", "sysfs", _PROC_READ_ONLY, False],
            [
                "/sys/fs/cgroup",
                "cgroup2",
                [*_PROC_READ_ONLY, "nsdelegate", "memory_recursiveprot"],
                False,
            ],
            ["/dev/mqueue", "mqueue", ["rw", "nosuid", "nodev", "noexec", "relatime"], True],
            ["/dev/shm", "tmpfs", _TMPFS, True],
            [
                "/usr/sbin/docker-init",
                "ext4",
                ["ro", "relatime", "discard", "errors=remount-ro", "commit=30"],
                False,
            ],
            ["/tmp", "tmpfs", _TMPFS, True],
            ["/data", "ext4", ["rw", "relatime"], True],
            ["/etc/resolv.conf", "ext4", ["ro", "relatime"], False],
            ["/etc/hostname", "ext4", ["ro", "relatime"], False],
            ["/etc/hosts", "ext4", ["ro", "relatime"], False],
            ["/proc/bus", "proc", _PROC_READ_ONLY, False],
            ["/proc/fs", "proc", _PROC_READ_ONLY, False],
            ["/proc/irq", "proc", _PROC_READ_ONLY, False],
            ["/proc/sys", "proc", _PROC_READ_ONLY, False],
            ["/proc/sysrq-trigger", "proc", _PROC_READ_ONLY, False],
            ["/proc/acpi", "tmpfs", _MASK_READ_ONLY, False],
            ["/proc/interrupts", "tmpfs", _PROC_MASK, True],
            ["/proc/kcore", "tmpfs", _PROC_MASK, True],
            ["/proc/keys", "tmpfs", _PROC_MASK, True],
            ["/proc/latency_stats", "tmpfs", _PROC_MASK, True],
            ["/proc/scsi", "tmpfs", _MASK_READ_ONLY, False],
            ["/proc/timer_list", "tmpfs", _PROC_MASK, True],
            ["/sys/firmware", "tmpfs", _MASK_READ_ONLY, False],
        ],
        "passwd": ["openalpha:x:10001:10001::/nonexistent:/usr/sbin/nologin"],
        "group": ["openalpha:x:10001:"],
        "home_exists": False,
    }


def test_the_posture_measured_in_the_shipped_container_has_no_problems(module: ModuleType) -> None:
    assert module._posture_problems(_shipped_report(), _SHIPPED_SHADOW) == []


def test_the_probe_is_python_the_container_can_run(module: ModuleType) -> None:
    """The probe travels as a string, so no linter or type checker reads it; this at least
    compiles it, and the real run in CI's `container` job executes it."""
    compile(module._POSTURE_PROBE, "<posture probe>", "exec")


def test_a_process_the_service_starts_is_judged_as_the_service(module: ModuleType) -> None:
    """Anything in PID 1's tree -- a worker uvicorn forks, a subprocess the API spawns -- is the
    service, whatever it was started as."""
    report = _shipped_report()
    report["processes"]["50"] = _status("50", PPid="7", Uid="0\t0\t0\t0")

    problems = module._posture_problems(report, _SHIPPED_SHADOW)

    assert any(problem.startswith("pid 50 ") and "Uid" in problem for problem in problems)


def test_a_process_exec_d_alongside_the_probe_is_not_taken_for_the_service(
    module: ModuleType,
) -> None:
    """The healthcheck is exec'd into the container every ten seconds and can be alive when the
    probe lists /proc. Like the probe it has no parent inside the container; unlike the probe it
    is not judged, since the probe already stands for the exec path. It is given uid 0 here only
    so that judging it would show."""
    report = _shipped_report()
    report["processes"]["63"] = _status("63", Uid="0\t0\t0\t0")

    assert module._posture_problems(report, _SHIPPED_SHADOW) == []


Mutation = Callable[[dict[str, Any]], None]


def _in_process(pid: str, **fields: str) -> Mutation:
    def mutate(report: dict[str, Any]) -> None:
        report["processes"][pid] = _status(pid, **fields)

    return mutate


def _write(directory: str, outcome: str) -> Mutation:
    def mutate(report: dict[str, Any]) -> None:
        report["writes"][directory] = outcome

    return mutate


def _mount(target: str, entry: list[Any] | None) -> Mutation:
    """Replace the mount at `target` with `entry` (mounted last, so on top), or unmount it."""

    def mutate(report: dict[str, Any]) -> None:
        kept = [mount for mount in report["mounts"] if mount[0] != target]
        report["mounts"] = kept if entry is None else [*kept, entry]

    return mutate


def _field(key: str, value: Any) -> Mutation:
    def mutate(report: dict[str, Any]) -> None:
        report[key] = value

    return mutate


def _both(first: Mutation, second: Mutation) -> Mutation:
    def mutate(report: dict[str, Any]) -> None:
        first(report)
        second(report)

    return mutate


_NOLOGIN_ACCOUNT = "openalpha:x:10001:10001::/nonexistent:/usr/sbin/nologin"


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        pytest.param(_in_process("7", Uid="10002\t10002\t10002\t10002"), "Uid", id="uid-10002"),
        pytest.param(_in_process("7", Uid="10001\t0\t0\t0"), "Uid", id="effective-uid-root"),
        pytest.param(_in_process("7", Gid="10002\t10002\t10002\t10002"), "Gid", id="gid-10002"),
        pytest.param(
            _in_process("7", Groups="10001 100 "), "Groups is 10001 100,", id="supplementary-group"
        ),
        pytest.param(_in_process("7", Groups="10002 "), "Groups is 10002,", id="other-group-only"),
        pytest.param(
            _in_process("7", CapBnd=_DOCKER_DEFAULT_BOUNDING_SET), "CapBnd", id="cap_drop-removed"
        ),
        pytest.param(_in_process("7", CapAmb="0000000000000400"), "CapAmb", id="ambient-cap"),
        pytest.param(_in_process("1", NoNewPrivs="0"), "NoNewPrivs", id="no-new-privileges-off"),
        pytest.param(
            _both(_write("/", "EACCES"), _mount("/", ["/", "overlay", ["rw", "relatime"], False])),
            "EROFS",
            id="read_only-removed",
        ),
        pytest.param(_write("/data", "EACCES"), "/data", id="data-unwritable"),
        pytest.param(_mount("/data", None), "/data", id="data-not-a-volume"),
        pytest.param(
            _mount("/tmp", ["/tmp", "tmpfs", [o for o in _TMPFS if o != "size=65536k"], True]),
            "size=65536k",
            id="tmp-unbounded",
        ),
        pytest.param(
            _mount("/tmp", ["/tmp", "tmpfs", [o for o in _TMPFS if o != "noexec"], True]),
            "noexec",
            id="tmp-executable",
        ),
        pytest.param(_mount("/tmp", None), "/tmp", id="tmp-not-a-tmpfs"),
        pytest.param(_write("/tmp", "ENOSPC"), "/tmp", id="tmp-unwritable"),
        pytest.param(
            _mount("/app/logs", ["/app/logs", "ext4", ["rw", "relatime"], True]),
            "/app/logs",
            id="second-writable-persistent-mount",
        ),
        pytest.param(
            _field("passwd", [_NOLOGIN_ACCOUNT.replace(":10001:10001:", ":10002:10001:")]),
            "10002",
            id="account-uid-10002",
        ),
        pytest.param(
            _field("passwd", [_NOLOGIN_ACCOUNT.replace("/usr/sbin/nologin", "/bin/bash")]),
            "/bin/bash",
            id="account-shell",
        ),
        pytest.param(
            _field("passwd", [_NOLOGIN_ACCOUNT.replace("/nonexistent", "/home/openalpha")]),
            "/home/openalpha",
            id="account-home",
        ),
        pytest.param(_field("home_exists", True), "/nonexistent", id="home-created"),
        pytest.param(_field("group", ["openalpha:x:10002:"]), "10002", id="group-gid-10002"),
        pytest.param(_field("passwd", []), "passwd", id="no-account"),
    ],
)
def test_each_way_the_posture_can_break_is_reported(
    module: ModuleType, mutate: Mutation, named: str
) -> None:
    report = _shipped_report()
    mutate(report)

    problems = module._posture_problems(report, _SHIPPED_SHADOW)

    assert any(named in problem for problem in problems), problems


@pytest.mark.parametrize(
    "shadow",
    [
        pytest.param("openalpha:$6$salt$hash:20708::::::\n", id="password-hash"),
        pytest.param("openalpha:!$6$salt$hash:20708::::::\n", id="locked-hash"),
        pytest.param("openalpha::20708::::::\n", id="empty-password"),
        pytest.param("", id="no-shadow-entry"),
    ],
)
def test_an_account_with_any_password_behind_it_is_reported(
    module: ModuleType, shadow: str
) -> None:
    """`!` alone is what the account has: locked, and no hash behind the lock. `!$6$...` is a
    real password that `usermod -U` would restore, so it is reported too; an empty field is a
    login with no password at all."""
    problems = module._posture_problems(_shipped_report(), shadow)

    assert any("password" in problem for problem in problems), problems


def test_a_container_without_cap_drop_is_caught_by_its_bounding_set_not_its_effective_one(
    module: ModuleType,
) -> None:
    """Measured in the shipped image run without `cap_drop: ALL`: a uid-10001 process still
    reads `CapEff: 0000000000000000`, because the kernel clears the permitted and effective sets
    of a non-root process when it execs a binary without file capabilities. Only `CapBnd` moves,
    to Docker's default fourteen. A check of `CapEff` alone passes the very edit it is for."""
    report = _shipped_report()
    report["processes"]["7"] = _status("7", CapBnd=_DOCKER_DEFAULT_BOUNDING_SET)

    problems = module._posture_problems(report, _SHIPPED_SHADOW)

    assert problems
    assert all("CapBnd" in problem for problem in problems), problems


def test_a_writable_root_filesystem_is_caught_by_its_errno_not_by_the_write_failing(
    module: ModuleType,
) -> None:
    """Measured in the shipped image run without `read_only: true`: creating `/probe` as uid
    10001 still fails -- `/` belongs to root -- but with `Permission denied`, not `Read-only file
    system`, while a directory the account owns on the same filesystem (`/app/.venv`) takes the
    write. A check that the write fails passes the very edit it is for; one on EROFS does not."""
    report = _shipped_report()
    report["writes"]["/"] = "EACCES"

    problems = module._posture_problems(report, _SHIPPED_SHADOW)

    assert any("EROFS" in problem and "EACCES" in problem for problem in problems), problems


def _fake_stack(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, Any],
    requests: Callable[..., dict[str, Any]],
) -> list[tuple[str, ...]]:
    compose_calls: list[tuple[str, ...]] = []
    outputs = iter([json.dumps(report), _SHIPPED_SHADOW])

    def compose(_command: object, _project: object, _env: object, *args: str, **_: object) -> None:
        compose_calls.append(args)

    def compose_output(
        _command: object, _project: object, _env: object, *_args: str, **_: object
    ) -> str:
        return next(outputs)

    monkeypatch.setattr(module, "_resolve_compose_command", lambda: ("docker-compose",))
    monkeypatch.setattr(module, "_free_port", lambda: 8000)
    monkeypatch.setattr(module, "_compose", compose)
    monkeypatch.setattr(module, "_compose_output", compose_output)
    monkeypatch.setattr(module, "_wait_for_health", lambda _url, **_: None)
    monkeypatch.setattr(module, "_request", requests)
    return compose_calls


def test_main_refuses_a_container_whose_posture_is_wrong_before_trusting_it_with_evidence(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _shipped_report()
    report["processes"]["7"] = _status("7", NoNewPrivs="0")

    def no_requests(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("the recovery check ran against a container whose posture was wrong")

    compose_calls = _fake_stack(module, monkeypatch, report, no_requests)

    with pytest.raises(RuntimeError, match="NoNewPrivs"):
        module.main()

    assert compose_calls[0][0] == "up"
    assert compose_calls[-1][:2] == ("down", "--volumes"), "the stack must still be torn down"


def test_main_reports_the_posture_it_checked_next_to_the_recovery_it_proved(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def requests(_url: str, **_kwargs: object) -> dict[str, Any]:
        return {"items": [{"evidence_id": "ev_fixture"}]}

    _fake_stack(module, monkeypatch, _shipped_report(), requests)

    assert module.main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "ok"
    assert printed["posture"]["writes"] == {"/": "EROFS", "/data": "ok", "/tmp": "ok"}
    assert printed["posture"]["writable_persistent_mounts"] == ["/data"]
