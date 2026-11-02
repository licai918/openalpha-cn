"""`scripts/p7_smoke_env.py`: the real-data smoke's one-off environment (`V2-P7-019`, task 3).

`prepare_smoke` builds a smoke root that touches nothing it was made from: a `git clone --local` of
a `main` commit with the smoke registration committed in the clone only and no remote, an APFS
clone of the panel (here `shutil.copytree`, injected: the source is a tiny tree), and a worktree
pinned at the registration with its own environment (the offline sync is replaced through
`daily_selection._run_sync`, the seam the daily command's own tests use) and a `.env` linked to the
source checkout's, never read. Every refusal before the clone makes nothing. The source checkout
is a throwaway `main` repository; nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
from research_repo import git, head

from openalpha_cn import strategy_survivor
from openalpha_cn.domain.survivor_book import SurvivorBookConfig

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


env = _script("p7_smoke_env")
daily = tds.daily
SENTINEL: Final[str] = "sk-smoke-env-sentinel-never-read-01919"
"""The source checkout's `.env` holds this: it must appear in nothing the prepare writes."""
NOW: Final[datetime] = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EPOCH: Final[date] = survivor_day.EPOCH
BOOK: Final[SurvivorBookConfig] = survivor_day.BOOK
CAPS: Final[dict[int, int]] = {1: 190, 2: 460}


def _source_repository(root: Path) -> Path:
    """A throwaway `main` checkout: one file under each bound path (the weekly report's too), a
    `.python-version`, the ignore rules for `.venv/` and `.env`, LF line ends -- and a `.env`."""
    repo = root / "main"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--template=", "-b", "main")
    for name in (*tds.BOUND, "scripts/forward_report.py"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(tds.LOCK if name == "uv.lock" else "RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    (repo / ".python-version").write_text(f"{tds.RUNNING_PYTHON}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    git(repo, "add", ".python-version", ".gitignore", ".gitattributes")
    git(repo, "commit", "-q", "-m", "initial", at=tds.COMMITTED)
    (repo / ".env").write_text(f"TUSHARE_TOKEN={SENTINEL}\n", encoding="utf-8")
    return repo


def _panel(root: Path) -> Path:
    """A tiny tree shaped like a panel: the research runtime directory holds it."""
    panel = root / "research" / "panel"
    (panel / "daily" / "2026").mkdir(parents=True)
    (panel / "daily" / "2026" / "data.parquet").write_bytes(b"\x00" * 64)
    (panel / "catalog.duckdb").write_bytes(b"catalog")
    (panel / "catalog.duckdb.lock").write_bytes(b"")
    return panel


def _offline_sync(
    command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    """`daily_selection._run_sync` without uv: the environment's `pyvenv.cfg`, as uv writes it."""
    venv = Path(environment["UV_PROJECT_ENVIRONMENT"])
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(f"version_info = {tds.RUNNING_PYTHON}.0\n", encoding="utf-8")
    return subprocess.CompletedProcess(list(command), 0, b"", b"")


def _policy(tmp_path: Path, source: Path) -> Any:
    return env.PathPolicy(
        temporary_roots=(tmp_path / "volatile",),
        main_checkout=source,
        research_runtime=tmp_path / "research",
    )


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
    panel: Path,
    *,
    root: Path | None = None,
    commit: str | None = None,
    caps: Mapping[int, int] = CAPS,
    copy_tree: Callable[[Path, Path], None] = shutil.copytree,
    device_of: Callable[[Path], int] | None = None,
) -> Any:
    monkeypatch.setattr(daily, "_run_sync", _offline_sync)
    keywords: dict[str, Any] = {} if device_of is None else {"device_of": device_of}
    return env.prepare_smoke(
        root=root or tmp_path / "smoke",
        source_repo=source,
        commit=commit or head(source),
        source_panel=panel,
        epoch=EPOCH,
        caps=caps,
        policy=_policy(tmp_path, source),
        uv=Path("uv"),
        clock=lambda: NOW,
        book=BOOK,
        copy_tree=copy_tree,
        **keywords,
    )


def _refs(repo: Path) -> tuple[str, str, str, str]:
    return (
        head(repo),
        git(repo, "for-each-ref"),
        git(repo, "status", "--porcelain", "--ignored"),
        git(repo, "worktree", "list", "--porcelain"),
    )


# --- the registration ----------------------------------------------------------------------------


def test_the_smoke_configuration_is_the_design_s_with_the_epoch() -> None:
    configuration = env.smoke_configuration(EPOCH, book=BOOK)

    assert configuration == survivor_day.survivor_config(epoch=EPOCH, book=BOOK)
    default = env.smoke_configuration(EPOCH)
    assert default["survivor_book"] == SurvivorBookConfig(
        seed=tds.grid.PROTOCOL_RANDOM_SEED
    ).model_dump(mode="json", exclude_computed_fields=True)
    strategy_survivor.read_survivor_registration(default, seed=tds.grid.PROTOCOL_RANDOM_SEED)


def test_the_smoke_settings_carry_the_marker_a_placeholder_protocol_and_the_registered_seed() -> (
    None
):
    settings = env.smoke_settings()

    assert settings[daily.SMOKE_SETTINGS_KEY]["issue"] == "V2-P7-019"
    assert "not a V2-P7-006 proposal" in settings[daily.SMOKE_SETTINGS_KEY]["note"]
    protocol = settings["survivor_forward_protocol"]
    assert (protocol["schema_version"], protocol["primary_horizon"]) == (
        "survivor-forward-protocol/v1",
        20,
    )
    assert settings["random_seed"] == tds.grid.PROTOCOL_RANDOM_SEED
    assert {key: settings[key] for key in tds.grid.protocol_settings()} == (
        tds.grid.protocol_settings()
    )


def test_prepare_clones_the_commit_and_commits_a_smoke_registration_in_the_clone_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    before = _refs(source)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    repo = manifest.repo
    registration = repo / env.REGISTRATION_PATH
    assert git(repo, "rev-parse", "HEAD^").strip() == head(source) == manifest.commit
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == env.SMOKE_BRANCH
    assert git(repo, "remote").strip() == ""
    assert git(repo, "diff", "--name-only", "HEAD^", "HEAD").split() == [env.REGISTRATION_PATH]
    assert git(repo, "show", f"HEAD:{env.REGISTRATION_PATH}") == registration.read_text(
        encoding="utf-8"
    )
    assert _refs(source) == before
    body = json.loads(registration.read_text(encoding="utf-8"))
    assert body["config"] == env.smoke_configuration(EPOCH, book=BOOK)
    assert body["config_id"] == manifest.config_id == tds.grid.config_id(body["config"])
    assert body["code_commit"] == head(source)
    assert body["settings"] == env.smoke_settings()
    assert body["criteria"] == env.SMOKE_CRITERIA
    assert hashlib.sha256(registration.read_bytes()).hexdigest() == manifest.registration_sha256
    assert manifest.registration_commit == head(repo)
    assert manifest.catch_up_bound == 10


# --- the panel -----------------------------------------------------------------------------------


def test_prepare_clones_the_panel_whole_and_leaves_the_source_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    stamps = {
        path.relative_to(panel).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in panel.rglob("*")
        if path.is_file()
    }
    copied: list[tuple[Path, Path]] = []

    def copy(from_: Path, to: Path) -> None:
        copied.append((from_, to))
        shutil.copytree(from_, to)

    manifest = _prepare(tmp_path, monkeypatch, source, panel, copy_tree=copy)

    assert copied == [(panel.resolve(), manifest.runtime / "panel")]
    assert env.tree_manifest(manifest.runtime / "panel") == env.tree_manifest(panel)
    assert manifest.panel_files == 3
    assert manifest.source_catalog_sha256 == hashlib.sha256(b"catalog").hexdigest()
    assert {
        path.relative_to(panel).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in panel.rglob("*")
        if path.is_file()
    } == stamps


def test_prepare_refuses_a_clone_that_is_not_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def short(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        (to / "daily" / "2026" / "data.parquet").unlink()

    with pytest.raises(env.SmokeEnvironmentError, match="not the panel"):
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=short)


def test_prepare_refuses_a_source_that_changed_while_it_was_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def racing(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        (from_ / "daily" / "2026" / "data.parquet").write_bytes(b"\x00" * 65)

    with pytest.raises(env.SmokeEnvironmentError, match="changed while it was cloned"):
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=racing)


def test_prepare_refuses_a_panel_on_another_volume_before_it_makes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    with pytest.raises(env.SmokeEnvironmentError, match="different volumes"):
        _prepare(
            tmp_path,
            monkeypatch,
            source,
            panel,
            device_of=lambda path: 1 if "research" in path.parts else 2,
        )

    assert not (tmp_path / "smoke").exists()


def test_the_panel_is_cloned_with_cp_c() -> None:
    assert env.clone_command(Path("a"), Path("b")) == ["cp", "-c", "-R", "a", "b"]


# --- the pinned worktree -------------------------------------------------------------------------


def test_prepare_pins_a_worktree_with_its_own_environment_and_links_the_env_file_without_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    pinned = manifest.pinned
    assert head(pinned) == manifest.registration_commit
    assert (pinned / ".venv").is_dir()
    assert not (pinned / ".venv").is_symlink()
    link = pinned / ".env"
    assert link.is_symlink()
    target = Path(os.path.normpath(link.parent / daily.link_text_as_path(os.readlink(link))))
    assert target == source.resolve() / ".env"
    assert git(pinned, "status", "--porcelain").strip() == ""
    assert manifest.registration == pinned / env.REGISTRATION_PATH
    assert manifest.registration.is_file()
    for written in (manifest.root / env.MANIFEST_NAME, manifest.root / env.LEDGER_NAME):
        assert SENTINEL not in written.read_text(encoding="utf-8")


def test_the_manifest_and_the_ledger_say_what_was_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    assert env.SmokeManifest.read(manifest.root) == manifest
    events = [
        json.loads(line)
        for line in (manifest.root / env.LEDGER_NAME).read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["prepared"]
    assert events[0]["caps"] == {"1": 190, "2": 460}
    with pytest.raises(env.SmokeEnvironmentError, match="written once"):
        manifest.write()


# --- refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("where", "named"),
    [
        ("space", "whitespace"),
        ("volatile", "temporary"),
        ("main", "main checkout"),
        ("research", "P6's runtime"),
        ("exists", "exists"),
    ],
)
def test_prepare_refuses_an_unsafe_smoke_root_before_it_makes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str, named: str
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    (tmp_path / "volatile").mkdir()
    (tmp_path / "taken").mkdir()
    root = {
        "space": tmp_path / "smoke root",
        "volatile": tmp_path / "volatile" / "smoke",
        "main": source / "smoke",
        "research": tmp_path / "research" / "smoke",
        "exists": tmp_path / "taken",
    }[where]
    before = _refs(source)

    with pytest.raises(env.SmokeEnvironmentError, match=named):
        _prepare(tmp_path, monkeypatch, source, panel, root=root)

    assert not (root / env.REPO_NAME).exists()
    assert where == "exists" or not root.exists()
    assert _refs(source) == before


@pytest.mark.parametrize("case", ["short", "side branch", "behind HEAD", "dirty"])
def test_prepare_refuses_a_commit_that_is_not_the_checkout_s_clean_main_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    commit = head(source)
    if case == "short":
        commit = commit[:12]
    if case == "side branch":
        git(source, "checkout", "-q", "-b", "side")
        (source / "notes.txt").write_text("side\n", encoding="utf-8")
        git(source, "add", "notes.txt")
        git(source, "commit", "-q", "-m", "side", at=tds.COMMITTED)
        commit = head(source)
    if case == "behind HEAD":
        (source / "notes.txt").write_text("later\n", encoding="utf-8")
        git(source, "add", "notes.txt")
        git(source, "commit", "-q", "-m", "later", at=tds.COMMITTED)
    if case == "dirty":
        bound = source / "src" / "openalpha_cn" / "strategy.py"
        bound.write_text("RULES = 2\n", encoding="utf-8")

    with pytest.raises(env.SmokeEnvironmentError):
        _prepare(tmp_path, monkeypatch, source, panel, commit=commit)

    assert not (tmp_path / "smoke").exists()


def test_prepare_refuses_without_the_env_file_it_links_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    (source / ".env").unlink()

    with pytest.raises(env.SmokeEnvironmentError, match=r"\.env"):
        _prepare(tmp_path, monkeypatch, source, panel)

    assert not (tmp_path / "smoke").exists()


@pytest.mark.parametrize("caps", [{1: 190}, {1: 190, 2: 100}, {1: -1, 2: 400}, {1: 1.5, 2: 400}])
def test_prepare_refuses_caps_that_are_not_two_whole_numbers_the_second_no_smaller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caps: Mapping[int, Any]
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    with pytest.raises(env.SmokeEnvironmentError, match="cap"):
        _prepare(tmp_path, monkeypatch, source, panel, caps=caps)


def test_the_default_policy_forbids_the_temporary_roots_and_the_research_runtime(
    tmp_path: Path,
) -> None:
    policy = env.PathPolicy.default(tmp_path)

    for root in (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir()).resolve()):
        assert root in policy.temporary_roots or root.resolve() in policy.temporary_roots
    assert policy.research_runtime == (Path.home() / "openalpha-research").resolve()
    assert policy.main_checkout == tmp_path.resolve()


# --- beyond the plan's tests: what the driver reads back, and what a late refusal leaves -------


def test_the_smoke_protocol_is_one_the_weekly_report_reads() -> None:
    from openalpha_cn import strategy_survivor_forward

    protocol = strategy_survivor_forward.read_forward_protocol(env.smoke_settings())

    assert protocol.primary_horizon == 20
    assert protocol.random_seed == tds.grid.PROTOCOL_RANDOM_SEED


@pytest.mark.parametrize("ledger", ["removed", "emptied", "not prepared first", "another root's"])
def test_a_root_whose_ledger_does_not_open_with_its_own_prepared_event_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: str
) -> None:
    """A missing or removed `requests.jsonl` reads as nothing spent (`RequestLedger.events`), so
    the manifest is read back only beside the ledger its prepare opened."""
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    manifest = _prepare(tmp_path, monkeypatch, source, panel)
    path = manifest.root / env.LEDGER_NAME
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if ledger == "removed":
        path.unlink()
    if ledger == "emptied":
        path.write_text("", encoding="utf-8")
    if ledger == "not prepared first":
        phase = json.dumps({"event": "phase", "phase": 2, "schema": "openalpha-p7-smoke-ledger/v1"})
        path.write_text(phase + "\n" + "".join(lines), encoding="utf-8")
    if ledger == "another root's":
        event = {**json.loads(lines[0]), "registration_sha256": "0" * 64}
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(env.SmokeEnvironmentError, match=env.LEDGER_NAME):
        env.SmokeManifest.read(manifest.root)


def test_a_manifest_read_from_another_place_than_its_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    manifest = _prepare(tmp_path, monkeypatch, source, panel)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for name in (env.MANIFEST_NAME, env.LEDGER_NAME):
        shutil.copyfile(manifest.root / name, elsewhere / name)

    with pytest.raises(env.SmokeEnvironmentError, match="prepared at"):
        env.SmokeManifest.read(elsewhere)


@pytest.mark.parametrize("inside", ["the source panel", "the source checkout"])
def test_prepare_refuses_a_root_inside_what_it_is_made_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside: str
) -> None:
    """Beside the policy's places: a root inside its own sources would write into them -- the
    clone into the panel it then copies, or into the checkout it clones."""
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    root = (panel if inside == "the source panel" else source) / "smoke"
    monkeypatch.setattr(daily, "_run_sync", _offline_sync)
    stamps = env.tree_manifest(panel)

    with pytest.raises(env.SmokeEnvironmentError, match=inside):
        env.prepare_smoke(
            root=root,
            source_repo=source,
            commit=head(source),
            source_panel=panel,
            epoch=EPOCH,
            caps=CAPS,
            policy=env.PathPolicy(
                temporary_roots=(tmp_path / "volatile",),
                main_checkout=tmp_path / "elsewhere",
                research_runtime=tmp_path / "elsewhere",
            ),
            uv=Path("uv"),
            clock=lambda: NOW,
            book=BOOK,
            copy_tree=shutil.copytree,
        )

    assert not root.exists()
    assert env.tree_manifest(panel) == stamps


@pytest.mark.parametrize(
    ("spelled", "named"), [("RESEARCH", "P6's runtime"), ("VOLATILE", "temporary")]
)
def test_prepare_refuses_a_root_spelled_in_another_case_on_a_volume_that_ignores_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelled: str, named: str
) -> None:
    """APFS ignores case by default and `Path.resolve()` keeps the case it is given, so
    `~/OpenAlpha-Research/smoke` is inside P6's run directory and spelled outside it."""
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    (tmp_path / "volatile").mkdir()
    if not (tmp_path / spelled).exists():
        pytest.skip("this volume tells case apart: another case names another directory")
    root = tmp_path / spelled / "smoke"

    with pytest.raises(env.SmokeEnvironmentError, match=named):
        _prepare(tmp_path, monkeypatch, source, panel, root=root)

    assert not root.exists()


def test_prepare_refuses_a_source_rewritten_in_place_while_it_was_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DuckDB catalog is rewritten in place: a write during the clone can keep every size."""
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def rewriting(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        catalog = from_ / "catalog.duckdb"
        stamp = catalog.stat()
        catalog.write_bytes(b"CATALOG")
        os.utime(catalog, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))

    with pytest.raises(env.SmokeEnvironmentError, match="changed while it was cloned"):
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=rewriting)


def test_prepare_refuses_a_source_that_is_not_the_top_of_its_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    inner = source / "scripts"
    (inner / ".env").write_text(f"TUSHARE_TOKEN={SENTINEL}\n", encoding="utf-8")

    with pytest.raises(env.SmokeEnvironmentError, match="not the top of its repository"):
        _prepare(tmp_path, monkeypatch, inner, panel)

    assert not (tmp_path / "smoke").exists()


def test_a_refusal_after_the_root_is_made_names_it_and_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def short(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        (to / "catalog.duckdb").write_bytes(b"CATALOG")

    with pytest.raises(env.SmokeEnvironmentError, match="not the panel") as refused:
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=short)

    root = (tmp_path / "smoke").resolve()
    assert f"{root} was made by this prepare" in str(refused.value)
    assert (root / env.REPO_NAME).is_dir()
    assert not (root / env.MANIFEST_NAME).exists()
    assert not (root / env.LEDGER_NAME).exists()


# --- the git calls: hermetic, whatever the caller's environment holds ---------------------------

AIMING: Final[tuple[str, ...]] = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
"""The inherited variables that aim git at another repository: a hook's `GIT_DIR` once let a test
set `core.bare=true` on the shared repository."""
HERMETIC: Final[dict[str, str]] = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


def _subcommand(command: Sequence[str]) -> str:
    """The git subcommand of `command`, past its `-c name=value` pairs."""
    words = iter(command[1:])
    for word in words:
        if word == "-c":
            next(words)
        elif not word.startswith("-"):
            return word
    return ""


@dataclass
class _GitCall:
    caller: str
    command: list[str]
    environment: dict[str, str]
    keywords: dict[str, Any]


def _recording_git(monkeypatch: pytest.MonkeyPatch) -> list[_GitCall]:
    """Every `subprocess.run` of git from here on -- the real call, with the module that made it,
    its environment as the child receives it and its keywords -- and then the real run."""
    calls: list[_GitCall] = []
    real = subprocess.run

    def recording(*args: Any, **keywords: Any) -> Any:
        command = [str(word) for word in (args[0] if args else keywords["args"])]
        if command and Path(command[0]).name == "git":
            calls.append(
                _GitCall(
                    caller=Path(sys._getframe(1).f_code.co_filename).name,
                    command=command,
                    environment=dict(keywords.get("env") or os.environ),
                    keywords={key: value for key, value in keywords.items() if key != "env"},
                )
            )
        return real(*args, **keywords)

    monkeypatch.setattr(subprocess, "run", recording)
    return calls


def test_an_inherited_git_dir_aims_no_git_call_of_the_prepare_at_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    git(decoy, "init", "-q", "--template=", "-b", "main")
    (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
    git(decoy, "add", "decoy.txt")
    git(decoy, "commit", "-q", "-m", "decoy", at=tds.COMMITTED)

    def decoy_state() -> tuple[bytes, bytes, bytes, str, str]:
        dot = decoy / ".git"
        return (
            (dot / "config").read_bytes(),
            (dot / "HEAD").read_bytes(),
            (dot / "index").read_bytes(),
            git(decoy, "config", "--local", "--list"),
            git(decoy, "for-each-ref"),
        )

    before, commit = decoy_state(), head(source)
    calls = _recording_git(monkeypatch)
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))

    manifest = _prepare(tmp_path, monkeypatch, source, panel, commit=commit)

    for name in AIMING:
        monkeypatch.delenv(name)
    assert decoy_state() == before
    assert head(manifest.pinned) == manifest.registration_commit
    assert git(manifest.repo, "remote").strip() == ""
    for call in calls:
        assert not set(AIMING) & set(call.environment), (call.caller, call.command)
    own = [call for call in calls if call.caller == "p7_smoke_env.py"]
    assert {_subcommand(call.command) for call in own} >= {
        "rev-parse", "cat-file", "merge-base", "status", "clone", "checkout", "remote", "add",
        "commit",
    }  # fmt: skip
    for call in own:
        assert {name: call.environment.get(name) for name in HERMETIC} == HERMETIC, call.command
