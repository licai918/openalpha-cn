"""The real-data smoke's one-off environment (`V2-P7-019`, task 3): `prepare_smoke`.

The smoke runs the daily command on real Tushare data, one real evening at a time, in a root of its
own that touches nothing it was made from -- not the main checkout, not P6's run directory
(`~/openalpha-research`), not its panel. `prepare_smoke` makes that root and sends no request.
Ten steps, in this order; **every refusal in steps 1 to 5 makes nothing**, and from step 6 on the
root exists, holds only what this prepare made, and a refusal names it for the user to delete
(nothing here deletes anything):

1. **The root** (`PathPolicy.checked`). Resolved, it holds no whitespace -- uv splits `--env-file`
   on it, and the pinned commands are printed for a shell; it is under no temporary directory
   (`/tmp`, `/private/tmp`, `/var/folders`, `/private/var/folders`, `tempfile.gettempdir()`) --
   the smoke runs for weeks and the system clears those; it is not inside the main checkout -- the
   smoke never writes there; and it is not inside P6's run directory -- the smoke reads its panel
   once, by an APFS clone, and writes nothing there. Nor is it inside the source checkout or the
   source panel it is made from. Each place is compared by spelling and, where it exists, by
   identity (`_within`: APFS ignores case, and `resolve()` keeps the case it is given). It must
   not exist, and its parent must.
2. **The sources.** The main checkout holds a `.env` (`is_file()` only: it is never opened); the
   source panel holds a `catalog.duckdb`; the two request caps are whole numbers, phase 2's (the
   whole smoke's) no smaller than phase 1's.
3. **The commit** (`_check_commit`): a full 40-character id the source repository holds, that is
   the source checkout's `HEAD` -- the smoke registration is written by this checkout's code
   (`registry.register`), which must be the code the clone holds -- whose bound code
   (`registry.REGISTERED_PATHS` and the two scripts) has no uncommitted change, and that is on
   `main`'s history. Every git call here drops the inherited `GIT_*` variables (an inherited
   `GIT_DIR` would aim it at another repository), reads no global or system configuration, takes
   no optional lock, so `git status` never refreshes the main checkout's index, reads no stdin
   and is given `GIT_TIMEOUT_SECONDS`.
4. **One volume**: the source panel and the root's parent are on the same device, or `cp -c`
   would not clone but copy (6 GB of P6's panel). Necessary, not sufficient: on one device that
   cannot clone (HFS+, exFAT) `cp -c` copies too (`apfs_clone`).
5. **The configuration reads**: `strategy_survivor.read_survivor_registration` of
   `smoke_configuration` with the registered seed, so the evening's step 1 cannot refuse it.
6. **The clone**: `git clone --local --no-checkout` of the source into `<root>/repo`, the branch
   `v2-p7-019-smoke` at the commit, and its `origin` removed -- a clone keeps a remote pointing
   back at the main checkout, and one stray `git push` would deliver the smoke registration there.
7. **The registration**: `registry.register` of the smoke configuration, `SMOKE_CRITERIA` and
   `smoke_settings()` at `REGISTRATION_PATH` in the clone, committed there alone under a fixed
   identity (`SMOKE_IDENTITY`, no hook, no signature); `registry.registered_checkout` then names
   the registration's commit, the one the evening's admission holds the bytes to.
8. **The panel**: `copy_tree` (`apfs_clone`: macOS's `cp -c -R`, `clonefile(2)` on APFS) of the
   source panel to `<root>/runtime/panel`. The source's files, sizes and modification times are
   the same before and after (else it changed while it was cloned), the copy holds every file of
   the source at its size and the source's `catalog.duckdb` byte for byte (else it is not the
   panel), and that catalog's SHA-256 is recorded.
9. **The pinned worktree**: the daily command's own `--pin-worktree` steps --
   `daily_selection.pin_worktree` at the registration's commit in `<root>/pinned`,
   `sync_pinned_environment` (offline) and `link_env_file` -- except that the `.env` link points
   at the **main checkout's** `.env`: `--pin-worktree --repo <clone>` would link the clone's, which
   does not exist (`.env` is git-ignored), and the evenings would run without credentials.
10. **The record**: `<root>/smoke.json` (`SmokeManifest`, written once) and the request ledger's
    `prepared` event (`p7_smoke_requests.RequestLedger`). A ledger that is gone reads as nothing
    spent, so `SmokeManifest.read` refuses a root whose `requests.jsonl` does not open with its
    own `prepared` event.

Nothing here is evidence: the smoke registration is marked in its settings (`SMOKE_MARKER`, under
`daily_selection.SMOKE_SETTINGS_KEY`), its criteria say so, and its forward protocol is a
placeholder the weekly report needs to run at all -- not a `V2-P7-006` proposal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402
import grid  # noqa: E402
import p7_smoke_requests  # noqa: E402
import registry  # noqa: E402

from openalpha_cn import strategy_survivor  # noqa: E402
from openalpha_cn.domain.survivor_book import SurvivorBookConfig  # noqa: E402

SMOKE_SCHEMA: Final[str] = "openalpha-p7-smoke/v1"
MANIFEST_NAME: Final[str] = "smoke.json"
LEDGER_NAME: Final[str] = "requests.jsonl"
REPO_NAME: Final[str] = "repo"
PINNED_NAME: Final[str] = "pinned"
RUNTIME_NAME: Final[str] = "runtime"
LOGS_NAME: Final[str] = "logs"
PANEL_NAME: Final[str] = "panel"
"""The panel under a runtime directory, as the daily command opens it (`runtime_dir / "panel"`)."""
CATALOG_NAME: Final[str] = "catalog.duckdb"
REGISTRATION_PATH: Final[str] = "docs/research/smoke/v2-p7-019-smoke-registration.json"
SMOKE_BRANCH: Final[str] = "v2-p7-019-smoke"
FORWARD_REPORT_SCRIPT: Final[str] = "scripts/forward_report.py"
BOUND: Final[tuple[str, ...]] = (
    *registry.REGISTERED_PATHS,
    daily_selection.THIS_SCRIPT,
    FORWARD_REPORT_SCRIPT,
)
"""What the daily command and the weekly report admit a registration against: the registry's
paths and the two scripts. None of it may differ from `HEAD` in the source checkout."""
SMOKE_IDENTITY: Final[tuple[str, ...]] = (
    "-c",
    "user.name=V2-P7-019 smoke",
    "-c",
    "user.email=smoke@openalpha.invalid",
    "-c",
    "commit.gpgsign=false",
)
COMMIT_MESSAGE: Final[str] = (
    "smoke(V2-P7-019): a one-off smoke registration -- committed in this throwaway clone only, "
    "never pushed; not the V2-P7-006 registration"
)
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_PHASES: Final[tuple[int, int]] = (1, 2)
GIT_TIMEOUT_SECONDS: Final[float] = 60
"""Each git call's bound, as `registry._git`'s: the source repository is small (a local clone
hardlinks its objects), so a call past it is stuck, not slow."""
MACOS_CP: Final[frozenset[Path]] = frozenset({Path("/bin/cp"), Path("/usr/bin/cp")})
"""The only `cp` the panel is cloned with: macOS's own (`apfs_clone`)."""

DESIGN_ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
}
"""The design's adapter block (Part D), less its epoch: `test_daily_selection_dispatch.py`'s
`SURVIVOR_CONFIG`, held equal to it by
`test_the_smoke_configuration_is_the_design_s_with_the_epoch`."""

SMOKE_PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 2,
    "bootstrap_samples": 1000,
    "random_seed": grid.PROTOCOL_RANDOM_SEED,
    "q": 0.1,
}
"""A placeholder the weekly report needs to run at all (`read_forward_protocol` has no default):
smoke values, not a `V2-P7-006` proposal -- the primary horizon is the one every survivor record
declares (20)."""

SMOKE_MARKER: Final[dict[str, str]] = {
    "issue": "V2-P7-019",
    "note": (
        "a one-off real-data smoke registration, committed only in its throwaway clone and never "
        "pushed; not a V2-P7-006 proposal; its forward protocol is a placeholder the weekly report "
        "needs to run"
    ),
}
SMOKE_CRITERIA: Final[dict[str, str]] = {
    "smoke": "V2-P7-019: no criterion; nothing this registration produces is evidence"
}


class SmokeEnvironmentError(ValueError):
    """A smoke environment that cannot be prepared as asked; nothing is made before the clone."""


def _within(path: Path, root: Path) -> bool:
    """Whether `path` is `root` or under it, by spelling (`root` as given and resolved) and, for a
    `root` that exists, by identity: APFS ignores case by default and `Path.resolve()` keeps the
    case it is given, so `~/OpenAlpha-Research/smoke` spells a path outside `~/openalpha-research`
    to a directory inside it. An ancestor that does not exist is no directory to compare."""
    if any(path == base or base in path.parents for base in {root, root.resolve()}):
        return True
    try:
        identity = os.stat(root)
    except OSError:
        return False
    for ancestor in (path, *path.parents):
        try:
            if os.path.samestat(os.stat(ancestor), identity):
                return True
        except OSError:
            continue
    return False


@dataclass(frozen=True, slots=True, kw_only=True)
class PathPolicy:
    """Where a smoke root may not be (step 1 of the module docstring)."""

    temporary_roots: tuple[Path, ...]
    main_checkout: Path
    research_runtime: Path

    @classmethod
    def default(cls, main_checkout: Path) -> PathPolicy:
        roots = {
            Path("/tmp"),
            Path("/private/tmp"),
            Path("/var/folders"),
            Path("/private/var/folders"),
            Path(tempfile.gettempdir()),
        }
        spelled = roots | {root.resolve() for root in roots}
        return cls(
            temporary_roots=tuple(sorted(spelled, key=str)),
            main_checkout=main_checkout.resolve(),
            research_runtime=(Path.home() / "openalpha-research").resolve(),
        )

    def checked(self, name: str, path: Path) -> Path:
        """`path` resolved, or `SmokeEnvironmentError` naming the rule it breaks."""
        resolved = path.expanduser().resolve()
        for spelling in (str(path), str(resolved)):
            if any(character.isspace() for character in spelling):
                raise SmokeEnvironmentError(
                    f"{name} {spelling!r} contains whitespace: uv splits --env-file on it and the "
                    "pinned commands are printed for a shell; choose a path without spaces"
                )
        for root in self.temporary_roots:
            if _within(resolved, root):
                raise SmokeEnvironmentError(
                    f"{name} {resolved} is under {root}, a temporary directory the system may "
                    "clear: the smoke runs for weeks and its paper book must survive them"
                )
        if _within(resolved, self.main_checkout):
            raise SmokeEnvironmentError(
                f"{name} {resolved} is inside the main checkout {self.main_checkout}; the smoke "
                "never writes there"
            )
        if _within(resolved, self.research_runtime):
            raise SmokeEnvironmentError(
                f"{name} {resolved} is inside {self.research_runtime}, P6's runtime directory; "
                "the smoke reads its panel once, by an APFS clone, and writes nothing there"
            )
        return resolved


def _git(repo: Path, *args: str) -> str:
    """git in `repo`, hermetic: no inherited `GIT_*`, no global or system configuration, no
    optional lock (a status never refreshes the source checkout's index), no prompt."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        finished = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise SmokeEnvironmentError(
            f"git {' '.join(args)} in {repo} did not finish within {GIT_TIMEOUT_SECONDS} s"
        ) from error
    except OSError as error:
        raise SmokeEnvironmentError(
            f"git {' '.join(args)} in {repo} could not run: {error}"
        ) from error
    if finished.returncode != 0:
        raise SmokeEnvironmentError(
            f"git {' '.join(args)} in {repo} failed: {finished.stderr.strip() or 'no message'}"
        )
    return finished.stdout.strip()


def _check_commit(source: Path, commit: str) -> None:
    """Step 3 of the module docstring."""
    if not _FULL_COMMIT.fullmatch(commit):
        raise SmokeEnvironmentError(f"{commit!r} is not a full 40-character commit id")
    top = Path(_git(source, "rev-parse", "--show-toplevel"))
    if not top.samefile(source):
        raise SmokeEnvironmentError(
            f"{source} is not the top of its repository ({top}); the smoke is cloned from, and "
            "links the .env of, the main checkout itself"
        )
    _git(source, "cat-file", "-e", f"{commit}^{{commit}}")
    standing = _git(source, "rev-parse", "HEAD")
    if standing != commit:
        raise SmokeEnvironmentError(
            f"the checkout stands at {standing}, not at {commit}: the smoke registration is "
            "written by this checkout's code, which must be the code the clone holds"
        )
    try:
        _git(source, "merge-base", "--is-ancestor", commit, "refs/heads/main")
    except SmokeEnvironmentError as error:
        raise SmokeEnvironmentError(
            f"{commit} is not on main: the smoke runs main's code"
        ) from error
    dirty = _git(source, "status", "--porcelain", "--untracked-files=all", "--", *BOUND)
    if dirty:
        raise SmokeEnvironmentError(
            f"the bound code of {source} has uncommitted changes ({dirty.splitlines()[0]} ...): "
            "the registration would be written by code the clone does not hold"
        )


def _checked_caps(caps: Mapping[int, int]) -> dict[int, int]:
    """Step 2's caps: `{1: phase 1's, 2: the whole smoke's}`, whole numbers, 0 or more, the
    second no smaller than the first."""
    if not isinstance(caps, Mapping) or set(caps) != set(_PHASES):
        raise SmokeEnvironmentError(
            f"the request caps are {{1: phase 1's, 2: the whole smoke's}}, not {caps!r}"
        )
    checked: dict[int, int] = {}
    for phase in _PHASES:
        value: object = caps[phase]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SmokeEnvironmentError(
                f"phase {phase}'s request cap is a whole number of requests, 0 or more, not "
                f"{value!r}"
            )
        checked[phase] = value
    if checked[2] < checked[1]:
        raise SmokeEnvironmentError(
            f"phase 2's request cap ({checked[2]}) is smaller than phase 1's ({checked[1]}): "
            "phase 2's cap counts every request of the smoke, phase 1's included"
        )
    return checked


def smoke_configuration(epoch: date, *, book: SurvivorBookConfig | None = None) -> dict[str, Any]:
    """The smoke registration's configuration: the design's book (`book`, by default the design's
    with the registered seed) and adapter block, with `epoch` as the paper book's epoch."""
    chosen = book if book is not None else SurvivorBookConfig(seed=grid.PROTOCOL_RANDOM_SEED)
    return {
        strategy_survivor.KIND_KEY: strategy_survivor.SURVIVOR_KIND,
        strategy_survivor.BOOK_BLOCK: chosen.model_dump(mode="json", exclude_computed_fields=True),
        strategy_survivor.ADAPTER_BLOCK: {**DESIGN_ADAPTER, "epoch_session": epoch.isoformat()},
    }


def smoke_settings() -> dict[str, Any]:
    """The smoke registration's settings: the protocol's, the placeholder forward protocol and the
    smoke marker the banner is printed for."""
    return {
        **grid.protocol_settings(),
        "survivor_forward_protocol": dict(SMOKE_PROTOCOL),
        daily_selection.SMOKE_SETTINGS_KEY: dict(SMOKE_MARKER),
    }


def clone_command(source: Path, target: Path) -> list[str]:
    return ["cp", "-c", "-R", str(source), str(target)]


def apfs_clone(source: Path, target: Path) -> None:
    """`source` cloned to `target` with macOS's `cp -c -R`, which asks for `clonefile(2)`.

    On an APFS volume no data block is copied. Where the file system cannot clone -- another
    volume, or one device that is HFS+ or exFAT -- `cp -c` falls back to `copyfile(2)` and copies
    every byte (`man cp`); step 4's same-device check rules out the first only. `cp` is looked up
    on `PATH` once and must resolve to macOS's own (`MACOS_CP`): GNU coreutils' `cp` (Homebrew's
    `gnubin` ahead on `PATH`) reads `-c` as `--preserve=context` and would copy the whole panel.
    No stdin, and no timeout: a copy that fell back is slow, not stuck.
    """
    if sys.platform != "darwin":
        raise SmokeEnvironmentError(
            "the panel is cloned with cp -c (APFS clonefile), which is macOS's; nothing was copied"
        )
    found = shutil.which("cp")
    cp = Path(found).resolve() if found is not None else None
    if cp not in MACOS_CP:
        raise SmokeEnvironmentError(
            f"the cp on PATH is {found or 'nowhere'}, not macOS's /bin/cp: GNU coreutils' cp "
            "reads -c as --preserve=context and would copy the whole panel; put /bin ahead of it "
            "on PATH. Nothing was copied"
        )
    finished = subprocess.run(
        [str(cp), *clone_command(source, target)[1:]],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if finished.returncode != 0:
        raise SmokeEnvironmentError(f"cp -c failed: {finished.stderr.strip()}")


def tree_manifest(root: Path) -> dict[str, int]:
    """Every file under `root` by its POSIX relative path, with its size."""
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stamps(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root` with its size and modification time: what a write during the
    clone changes, even one that keeps the size (a DuckDB catalog is rewritten in place)."""
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _device(path: Path) -> int:
    return path.stat().st_dev


def _text(body: Mapping[str, Any], key: str) -> str:
    value: object = body[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} is {value!r}, not a string")
    return value


def _count(body: Mapping[str, Any], key: str) -> int:
    value: object = body[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} is {value!r}, not a whole number")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class SmokeManifest:
    """`<root>/smoke.json`: what `prepare_smoke` made, written once (`openalpha-p7-smoke/v1`)."""

    root: Path
    commit: str
    registration_commit: str
    registration_sha256: str
    config_id: str
    epoch: date
    catch_up_bound: int
    source_panel: Path
    source_catalog_sha256: str
    panel_files: int
    caps: Mapping[int, int]
    created_at: datetime

    @property
    def repo(self) -> Path:
        return self.root / REPO_NAME

    @property
    def pinned(self) -> Path:
        return self.root / PINNED_NAME

    @property
    def runtime(self) -> Path:
        return self.root / RUNTIME_NAME

    @property
    def registration(self) -> Path:
        """The registration the evenings admit: the pinned worktree's copy, at its commit."""
        return self.pinned / REGISTRATION_PATH

    @property
    def ledger_path(self) -> Path:
        return self.root / LEDGER_NAME

    @property
    def logs(self) -> Path:
        return self.root / LOGS_NAME

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SMOKE_SCHEMA,
            "root": str(self.root),
            "commit": self.commit,
            "registration_commit": self.registration_commit,
            "registration_sha256": self.registration_sha256,
            "config_id": self.config_id,
            "epoch": self.epoch.isoformat(),
            "catch_up_bound": self.catch_up_bound,
            "source_panel": str(self.source_panel),
            "source_catalog_sha256": self.source_catalog_sha256,
            "panel_files": self.panel_files,
            "caps": {str(phase): cap for phase, cap in sorted(self.caps.items())},
            "created_at": self.created_at.isoformat(),
        }

    def _prepared(self) -> dict[str, Any]:
        """The ledger's first event: this manifest's, so a ledger is read back with its root
        only."""
        return {
            "event": "prepared",
            "root": str(self.root),
            "commit": self.commit,
            "registration_commit": self.registration_commit,
            "registration_sha256": self.registration_sha256,
            "config_id": self.config_id,
            "epoch": self.epoch.isoformat(),
            "caps": {str(phase): cap for phase, cap in sorted(self.caps.items())},
            "at": self.created_at.isoformat(),
        }

    def write(self) -> None:
        """Write `<root>/smoke.json`; one that exists is refused and left as it is."""
        path = self.root / MANIFEST_NAME
        text = json.dumps(self.to_json(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise SmokeEnvironmentError(
                f"{path} is written once; it exists and was left as it is"
            ) from error

    @classmethod
    def read(cls, root: Path) -> SmokeManifest:
        """The manifest of the smoke root `root`, refused unless it is a whole
        `openalpha-p7-smoke/v1` manifest prepared at `root` and the root's ledger opens with its
        own `prepared` event (step 10 of the module docstring)."""
        path = root / MANIFEST_NAME
        if not path.is_file():
            raise SmokeEnvironmentError(
                f"{path} is not a file: {root} is not a prepared smoke root"
            )
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SmokeEnvironmentError(f"{path} cannot be read as JSON: {error}") from error
        if not isinstance(body, dict) or body.get("schema") != SMOKE_SCHEMA:
            raise SmokeEnvironmentError(f"{path} is not an {SMOKE_SCHEMA} manifest")
        try:
            caps = body["caps"]
            if not isinstance(caps, dict) or set(caps) != {str(phase) for phase in _PHASES}:
                raise TypeError(f"caps is {caps!r}, not one cap for each of phases 1 and 2")
            manifest = cls(
                root=Path(_text(body, "root")),
                commit=_text(body, "commit"),
                registration_commit=_text(body, "registration_commit"),
                registration_sha256=_text(body, "registration_sha256"),
                config_id=_text(body, "config_id"),
                epoch=date.fromisoformat(_text(body, "epoch")),
                catch_up_bound=_count(body, "catch_up_bound"),
                source_panel=Path(_text(body, "source_panel")),
                source_catalog_sha256=_text(body, "source_catalog_sha256"),
                panel_files=_count(body, "panel_files"),
                caps=_checked_caps({phase: caps[str(phase)] for phase in _PHASES}),
                created_at=datetime.fromisoformat(_text(body, "created_at")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SmokeEnvironmentError(
                f"{path} is not a whole {SMOKE_SCHEMA} manifest: {error}"
            ) from error
        here = root.resolve()
        if manifest.root != here:
            raise SmokeEnvironmentError(
                f"{path} was prepared at {manifest.root}, and is read at {here}: the pinned "
                "worktree and the clone are linked by the paths they were made at; prepare a new "
                "root rather than move one"
            )
        manifest._check_ledger()
        return manifest

    def _check_ledger(self) -> None:
        """Refuse a ledger that does not open with this manifest's own `prepared` event: a ledger
        that is gone, emptied or replaced reads as nothing spent (`RequestLedger.events`)."""
        path = self.ledger_path
        if not path.is_file():
            raise SmokeEnvironmentError(
                f"{path} is not a file: the request ledger this root was prepared with is gone, "
                "and a ledger that is gone reads as nothing spent; this root is refused -- "
                "prepare a new one"
            )
        try:
            events = p7_smoke_requests.RequestLedger(path).events()
        except p7_smoke_requests.SmokeBudgetError as error:
            raise SmokeEnvironmentError(str(error)) from error
        own = {**self._prepared(), "schema": p7_smoke_requests.LEDGER_SCHEMA}
        prepared = [event for event in events if event["event"] == "prepared"]
        if not events or events[0] != own or len(prepared) != 1:
            raise SmokeEnvironmentError(
                f"{path} does not open with this root's one `prepared` event (registration "
                f"{self.registration_sha256[:12]}, prepared {own['at']}): it is not the ledger "
                "this root was prepared with, and what it says was spent cannot be trusted; this "
                "root is refused -- prepare a new one"
            )


def _made(root: Path) -> str:
    return (
        f"The smoke root {root} was made by this prepare and holds nothing else; delete it, then "
        "prepare again (nothing is deleted here)"
    )


_LATE_FAILURES: Final[tuple[type[BaseException], ...]] = (
    SmokeEnvironmentError,
    daily_selection.StepFailedError,
    registry.RegistrationError,
    registry.HoldoutRefusedError,
    OSError,
    subprocess.SubprocessError,
)
"""What a step from 6 on refuses with: each is reported as a `SmokeEnvironmentError` naming the
root it made."""


def prepare_smoke(
    *,
    root: Path,
    source_repo: Path,
    commit: str,
    source_panel: Path,
    epoch: date,
    caps: Mapping[int, int],
    policy: PathPolicy,
    uv: Path,
    clock: Callable[[], datetime],
    book: SurvivorBookConfig | None = None,
    copy_tree: Callable[[Path, Path], None] = apfs_clone,
    device_of: Callable[[Path], int] = _device,
) -> SmokeManifest:
    """Make the smoke root `root` from `source_repo` at `commit` and `source_panel`; see the
    module docstring for the ten steps and every refusal. Sends no request."""
    # 1. the root
    target = policy.checked("the smoke root", root)
    source = source_repo.resolve()
    panel = source_panel.resolve()
    for name, base in (("the source panel", panel), ("the source checkout", source)):
        if _within(target, base):
            raise SmokeEnvironmentError(
                f"the smoke root {target} is inside {name} {base}; the smoke writes nothing into "
                "what it is made from"
            )
    if root.is_symlink() or root.exists() or target.exists():
        raise SmokeEnvironmentError(
            f"the smoke root {target} exists; a smoke root is made new by its prepare, and "
            "nothing in it was changed"
        )
    if not target.parent.is_dir():
        raise SmokeEnvironmentError(
            f"the smoke root's parent {target.parent} is not a directory; create it first"
        )
    # 2. the sources
    env_file = source / daily_selection.ENV_FILE
    if not env_file.is_file():
        raise SmokeEnvironmentError(
            f"{env_file} is not a file: the pinned worktree's {daily_selection.ENV_FILE} links to "
            "the main checkout's, and without it the evenings have no credentials; create it, "
            "then prepare again (it is never opened here)"
        )
    if not (panel / CATALOG_NAME).is_file():
        raise SmokeEnvironmentError(
            f"{panel} holds no {CATALOG_NAME}: it is not a panel the smoke can clone"
        )
    chosen_caps = _checked_caps(caps)
    # 3. the commit
    _check_commit(source, commit)
    # 4. one volume
    if device_of(panel) != device_of(target.parent):
        raise SmokeEnvironmentError(
            f"the source panel {panel} and the smoke root's parent {target.parent} are on "
            "different volumes: cp -c clones only within one APFS volume, and across two it "
            "would copy the whole panel; nothing was made"
        )
    # 5. the configuration reads
    configuration = smoke_configuration(epoch, book=book)
    try:
        registration = strategy_survivor.read_survivor_registration(
            configuration, seed=grid.PROTOCOL_RANDOM_SEED
        )
    except strategy_survivor.SurvivorRegistrationError as error:
        raise SmokeEnvironmentError(
            f"the smoke configuration is not one this build's daily command reads: {error}; "
            "nothing was made"
        ) from error
    # 6. on: the root exists
    target.mkdir()
    try:
        return _make(
            target,
            source=source,
            commit=commit,
            panel=panel,
            configuration=configuration,
            catch_up_bound=registration.adapter.max_panel_catch_up_sessions,
            epoch=epoch,
            caps=chosen_caps,
            uv=uv,
            clock=clock,
            copy_tree=copy_tree,
        )
    except _LATE_FAILURES as error:
        raise SmokeEnvironmentError(f"{error}. {_made(target)}") from error


def _make(
    target: Path,
    *,
    source: Path,
    commit: str,
    panel: Path,
    configuration: Mapping[str, Any],
    catch_up_bound: int,
    epoch: date,
    caps: Mapping[int, int],
    uv: Path,
    clock: Callable[[], datetime],
    copy_tree: Callable[[Path, Path], None],
) -> SmokeManifest:
    """Steps 6 to 10 of the module docstring, in the root `target` step 6 made."""
    # 6. the clone, on its own branch, with no remote
    repo = target / REPO_NAME
    _git(target, "clone", "--quiet", "--local", "--no-checkout", str(source), str(repo))
    _git(repo, "checkout", "--quiet", "-b", SMOKE_BRANCH, commit)
    _git(repo, "remote", "remove", "origin")
    # 7. the registration, committed in the clone only
    registration = repo / REGISTRATION_PATH
    digest = registry.register(
        configuration,
        SMOKE_CRITERIA,
        registration,
        code_commit=commit,
        settings=smoke_settings(),
    )
    _git(repo, "add", "--", REGISTRATION_PATH)
    _git(repo, *SMOKE_IDENTITY, "commit", "--quiet", "--no-verify", "-m", COMMIT_MESSAGE)
    _root, registration_commit, code_commit = registry.registered_checkout(
        registration, repo, also_bound=(daily_selection.THIS_SCRIPT, FORWARD_REPORT_SCRIPT)
    )
    if (registration_commit, code_commit) != (
        _git(repo, "rev-parse", "HEAD"),
        commit,
    ) or _git(repo, "rev-parse", "HEAD^") != commit:
        raise SmokeEnvironmentError(
            f"the clone's registration commit {registration_commit} is not the one commit on "
            f"{commit} that holds the smoke registration"
        )
    config_id = json.loads(registration.read_bytes())["config_id"]
    # 8. the panel, cloned whole from a source that did not change meanwhile
    runtime = target / RUNTIME_NAME
    runtime.mkdir()
    copy = runtime / PANEL_NAME
    before = _stamps(panel)
    catalog_sha256 = _sha256(panel / CATALOG_NAME)
    copy_tree(panel, copy)
    if _stamps(panel) != before:
        raise SmokeEnvironmentError(
            f"the source panel {panel} changed while it was cloned; the copy at {copy} is not "
            "one state of it. Stop whatever writes the panel"
        )
    if tree_manifest(copy) != {name: size for name, (size, _mtime) in before.items()}:
        raise SmokeEnvironmentError(
            f"the copy at {copy} is not the panel {panel}: its files or their sizes differ"
        )
    if not (copy / CATALOG_NAME).is_file() or _sha256(copy / CATALOG_NAME) != catalog_sha256:
        raise SmokeEnvironmentError(
            f"the copy at {copy} is not the panel {panel}: its {CATALOG_NAME} is not the "
            "source's, byte for byte"
        )
    # 9. the pinned worktree, its environment, and the main checkout's .env linked
    pinned = target / PINNED_NAME
    try:
        pinned_at = daily_selection.pin_worktree(registration, repo, pinned)
        daily_selection.sync_pinned_environment(pinned, uv=uv)
        linked = daily_selection.link_env_file(source, pinned)
    except daily_selection.StepFailedError as error:
        raise SmokeEnvironmentError(f"the pinned worktree {pinned}: {error}") from error
    if pinned_at != registration_commit:
        raise SmokeEnvironmentError(
            f"the pinned worktree {pinned} stands at {pinned_at}, not at the registration's "
            f"commit {registration_commit}"
        )
    if not (pinned / daily_selection.ENV_FILE).is_symlink():
        raise SmokeEnvironmentError(linked)
    # 10. the manifest, once, and the ledger's first event
    manifest = SmokeManifest(
        root=target,
        commit=commit,
        registration_commit=registration_commit,
        registration_sha256=digest,
        config_id=config_id,
        epoch=epoch,
        catch_up_bound=catch_up_bound,
        source_panel=panel,
        source_catalog_sha256=catalog_sha256,
        panel_files=len(before),
        caps=dict(caps),
        created_at=clock(),
    )
    manifest.write()
    p7_smoke_requests.RequestLedger(manifest.ledger_path).append(manifest._prepared())
    return manifest
