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
import subprocess
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
