"""The real-data smoke's driver (`V2-P7-019`, task 4): the one way an invocation of the smoke is
made, and the only one under its request cap.

The daily command has no "stop at N requests" of its own, and its refusals print the commands that
repair a panel -- run as printed, they are not counted, and one of them is the whole-year rebuild
(about 1,200 requests at live scale). This driver puts the cap of `p7_smoke_requests` on the one
transport seam for the length of each invocation, records each in the smoke root's ledger, and sets
the runtime directory, the registration and the pinned worktree itself. **The driver never decides
what the day does: the day is `daily_selection.main`**, the morning re-check is the same with
`--morning-recheck`, the weekly report is `forward_report.main`, and a remedy is the `openalpha
panel build` or `factor build` a refusal printed -- each run in this process, as it is.

| subcommand | run in | what | cap rule (`RULES`) |
|---|---|---|---|
| `prepare ...` | the main checkout | `p7_smoke_env.prepare_smoke` | no request |
| `evening [-- ARGS]` | the pinned worktree | `daily_selection.main` | 22 to start, discards |
| `morning [-- ARGS]` | the pinned worktree | it, `--morning-recheck` | ceiling 3, 1 to start |
| `weekly [-- ARGS]` | the pinned worktree | `forward_report.main` | ceiling 0 |
| `remedy LINE`, `remedy -` | the pinned worktree | the printed line (`parse_remedy`) | its kind's |
| `status` | anywhere | phase, caps, spent, discarded, each invocation | -- |
| `phase --to 2 --reason TEXT` | anywhere | phase 1 to 2, once | -- |

`prepare` takes `--smoke-root DIR --epoch D --phase1-cap N --phase2-cap M` and `[--commit SHA]
[--source-panel DIR] [--uv PATH]`: it is `prepare_smoke` of this checkout -- the commit is its
`HEAD` by default and must be it, and the source panel is P6's, `~/openalpha-research/panel`, by
default, never the repository's own `runtime/panel`; it warns of a shallow checkout, and refuses
one whose `SMOKE_SCRIPTS` are not what `HEAD` holds. A remedy's
kind sets its rule: `panel-build` has no ceiling, needs `PAGED_SESSION_REQUESTS x (catch_up_bound
+ 1)` to start and discards on the cap; `factor-build` has a ceiling of 0; a day's rerun is an
`evening` or a `morning`. "To start" is the headroom: an invocation is refused, before anything is
written, unless that many requests of the phase's cap remain.

**Paths.** `--runtime-dir`, `--registration`, `--repo`, `--morning-recheck`, `--pin-worktree`,
`--launchd-plist` and `--factor-gaps` are the driver's to set: an extra argument naming one is
refused, and so is an abbreviation of one (the daily command's parser takes `--runtime` for
`--runtime-dir`, and its last occurrence) and a `--flag=value` spelling. A remedy is judged by the
options its command parses (click's own parse of `openalpha panel build` / `factor build`), not by
its words: `--as-of --incremental` gives `--as-of` the value `--incremental`, the last
`--runtime-dir` is the one taken, and `~` is not expanded. One the command would refuse as a usage
error is refused before anything is written: typer runs a click of its own, whose exceptions
`daily_selection.invoke` does not catch, so in the build it would be a fault that discards the
root. `--smoke-root` may be left out where the script is `<root>/pinned/scripts/p7_smoke.py` and
`<root>/smoke.json` exists.

**One invocation at a time, and its state not lost.** `run` takes `<root>/driver.lock`
(`openalpha_cn.panel.store._try_lock`) and holds it from reading the manifest again -- the caller's
read was before the lock -- through the ledger's last append. Under it, the ledger is held to more
than `SmokeManifest.read` holds it to: that read refuses a ledger that is gone, emptied or another
root's, but a ledger cut back to its `prepared` line, or by an invocation, still opens with that
line and reads as less spent. So the driver refuses a ledger whose invocations are not numbered 1
to N, whose `start` events do not each record the running total of the `end` events before them,
in which an invocation that writes the panel ended on a fault or the cap without the `discarded`
line `run_capped` writes just before that `end` (`Spent.discarded` only says such a line exists),
or that records fewer invocations than `<root>/logs` holds logs of -- every invocation's output is
written there as `<number>-<kind>.out|.err`, opened exclusively, never overwritten. A ledger event
the driver cannot read refuses the root by name. Not caught -- forgeries, not faults: a ledger cut
together with its logs, an edited event (an `end`'s count, `fault` or `reached` among them: with
its `discarded` line taken out and its `fault` set to null, a discarded root runs again), and a
hand-inserted `phase` line.

**Ends.** The exit is the wrapped command's own; `CAP_EXIT` (4) when the cap stopped a request --
read from the ledger's `end` event when the call then ended on another exception, which
`run_capped` records and raises; `REFUSED_EXIT` (5) for a refusal of the driver, the ledger or the
manifest; `COMMAND_FAILED_EXIT` (7) for a `panel build` or `factor build` that failed with a code
of its own the driver's would hide -- 3 (`PanelExit.bad_request`, which can come after the calendar
was fetched), 4 (`provider_failure`), 5 (`internal_error`) or 6 (`catalog_busy`): the closing line
names the command's own; 3 for a checkout that is not the pinned worktree's
(`ForeignPackageError`): nothing ran -- as the day's own 3 says, which only its registration
step's admission gives, before the paper book, the journal and any request, so it passes through;
130 for an interruption.

**Interruptions.** SIGINT, SIGTERM and SIGHUP raise `KeyboardInterrupt` while the driver runs, so
Ctrl-C, a SIGTERM and a closed terminal end an invocation alike -- its end is recorded and an
invocation that writes the panel discards the root -- and are put back after. A signal the caller
ignores (`nohup`, a background job) is left ignored. The handler also marks the interruption: the
daily command reports a `KeyboardInterrupt` inside its `panel build` as a refusal of step 2 (click
turns it into `Abort`), and `panel build` run alone exits 130 of its own, so an invocation that
returns after an interruption is ended as interrupted all the same. The log is written before the
terminal, and a terminal that can no longer be written to (`| head` that has had its lines) is
written to no more, the invocation going on.

**The cap's module.** The cap is put on `cli._panel_transport` by `p7_smoke_requests`'s own
`daily_selection`; it binds the day only if that is this driver's `daily_selection`, imported from
beside this file, whose `cli` is the one `panel build` runs (`_one_tree`).

Nothing the smoke prints is evidence: every order list it shows is a candidate under the banner
`smoke: do not place these orders`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import FrameType, MappingProxyType
from typing import Any, Final, TextIO, cast

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection as daily  # noqa: E402
import forward_report  # noqa: E402
import p7_smoke_env as env  # noqa: E402
import p7_smoke_requests as budget  # noqa: E402
import registry  # noqa: E402
from typer.main import get_command  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.panel.store import _try_lock, _unlock  # noqa: E402

REPOSITORY: Final[Path] = _SCRIPTS.parent
"""This driver's own checkout: what `prepare` clones. The smoke registration is written by
`p7_smoke_env.py` -- this checkout's, beside this file -- which is no bound path, so the code that
writes it must be the code the clone holds."""
REFUSED_EXIT: Final[int] = 5
COMMAND_FAILED_EXIT: Final[int] = 7
"""A `panel build` or `factor build` that failed with a code of its own the driver's would hide
(`SHADOWED_EXITS`); its own code is printed in the closing line."""
SHADOWED_EXITS: Final[frozenset[int]] = frozenset(
    {
        int(cli.PanelExit.bad_request),
        int(cli.PanelExit.provider_failure),
        int(cli.PanelExit.internal_error),
        int(cli.PanelExit.catalog_busy),
    }
)
"""3, 4, 5 and 6: `panel build`'s own exits that read as the driver's 3 (nothing ran),
`CAP_EXIT`, `REFUSED_EXIT` and the code next to them."""
INTERRUPTED_EXIT: Final[int] = 130
ORDINARY_EVENING_REQUESTS: Final[int] = 22
"""An ordinary evening's 16 requests, 20 once `stk_limit` pages (T6-T7 section 0), and 2 for a
year's data-dependent single requests (next year's calendar, the year before's names)."""
PAGED_SESSION_REQUESTS: Final[int] = 7
"""One session of the session-scoped targets once `stk_limit` pages: 1 + 3 + 3."""
DRIVER_LOCK: Final[str] = "driver.lock"
PATH_FLAGS: Final[tuple[str, ...]] = (
    "--runtime-dir",
    "--registration",
    "--repo",
    "--morning-recheck",
    "--pin-worktree",
    "--launchd-plist",
    "--factor-gaps",
)
DAY_KINDS: Final[tuple[str, ...]] = ("evening", "morning", "weekly")
OPENALPHA_KINDS: Final[tuple[str, ...]] = ("panel-build", "factor-build")
SMOKE_SCRIPTS: Final[tuple[str, ...]] = (
    "scripts/p7_smoke.py",
    "scripts/p7_smoke_env.py",
    "scripts/p7_smoke_requests.py",
)
"""The smoke's own scripts, no bound path of any registration: `prepare` refuses a checkout in
which one of them is not what `HEAD` holds, since the clone holds `HEAD`'s."""
_LOG: Final[re.Pattern[str]] = re.compile(r"(\d+)-[a-z-]+\.(?:out|err)")
_REFUSED_LEDGER: Final[str] = "ledger        refused: "
_INTERRUPTED: Final[list[bool]] = [False]
"""Set by the driver's signal handler: an interruption reached the invocation, whatever the
command it runs made of the `KeyboardInterrupt` (`_call`). A one-element cell, written and read
by single assignments and indexings, never under a lock: a `threading.Event` takes its own
non-reentrant lock in `set()`, so a signal handled while the main thread was inside `clear()`
would hang the driver for good."""


class SmokeDriverError(ValueError):
    """An invocation the driver refuses to make."""


class RemedyError(SmokeDriverError):
    """A printed remedy the driver does not run."""


@dataclass(frozen=True, slots=True)
class Remedy:
    kind: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KindRule:
    ceiling: int | None
    discard_on_cap: bool
    headroom: Callable[[env.SmokeManifest], int]


RULES: Final[Mapping[str, KindRule]] = MappingProxyType(
    {
        "evening": KindRule(None, True, lambda _manifest: ORDINARY_EVENING_REQUESTS),
        "panel-build": KindRule(
            None, True, lambda manifest: PAGED_SESSION_REQUESTS * (manifest.catch_up_bound + 1)
        ),
        "morning": KindRule(3, False, lambda _manifest: 1),
        "weekly": KindRule(0, False, lambda _manifest: 0),
        "factor-build": KindRule(0, False, lambda _manifest: 0),
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Tee(io.TextIOBase):
    """Writes the invocation's log, then the terminal while it can be written to: a terminal that
    went away (`| head` that has had its lines, a closed window) is written to no more, and stops
    nothing -- the log keeps every line."""

    def __init__(self, log: TextIO, terminal: TextIO) -> None:
        super().__init__()
        self._log = log
        self._terminal: TextIO | None = terminal

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._log.write(text)
        if self._terminal is not None:
            try:
                self._terminal.write(text)
            except (OSError, ValueError):  # a broken pipe; a closed stream
                self._terminal = None
        return len(text)

    def flush(self) -> None:
        self._log.flush()
        if self._terminal is not None:
            try:
                self._terminal.flush()
            except (OSError, ValueError):
                self._terminal = None


# --- remedies ------------------------------------------------------------------------------------


def _same(given: object, expected: Path) -> bool:
    """Whether `given` is `expected` as the command takes it: resolved, `~` not expanded --
    neither click nor argparse expands it, so `~/x` is a directory named `~`."""
    return given is not None and Path(str(given)).resolve() == expected.resolve()


def _refuse_joined(arguments: Sequence[str]) -> None:
    """A refusal prints every option and its value as two words; `openalpha` also reads
    `--runtime-dir=DIR`, and the last one given, so a joined one is never read past."""
    for word in arguments:
        if word.startswith("--") and "=" in word:
            raise RemedyError(
                f"`{word}` joins an option to its value, and the refusals print every option as "
                "its own word: paste the whole line as the refusal printed it"
            )


def parse_remedy(line: str, *, runtime: Path, registration: Path, pinned: Path) -> Remedy:
    """The remedy a refusal printed (`daily_selection.pinned_command`'s shape) as the driver's own
    invocation, or `RemedyError` naming why the smoke never runs it."""
    try:
        words = shlex.split(line)
    except ValueError as error:
        raise RemedyError(f"the remedy cannot be read as a shell line: {error}") from error
    environment = f"/{daily.PINNED_ENVIRONMENT}/bin/"
    for index, word in enumerate(words):
        if word.endswith(f"{environment}openalpha"):
            _refuse_joined(words[index + 1 :])
            return _openalpha_remedy(words[index + 1 :], runtime=runtime)
        if word.endswith(f"{environment}python") and words[index + 1 : index + 2] == [
            daily.THIS_SCRIPT
        ]:
            _refuse_joined(words[index + 2 :])
            return _day_remedy(
                words[index + 2 :], runtime=runtime, registration=registration, pinned=pinned
            )
    raise RemedyError(
        "no `openalpha` or `scripts/daily_selection.py` of the pinned worktree in this line: "
        "paste the whole line the refusal printed"
    )


_OPENALPHA_COMMANDS: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {("panel", "build"): "panel-build", ("factor", "build"): "factor-build"}
)


def _parse(arguments: Sequence[str], *, resilient: bool) -> tuple[str, Mapping[str, Any]]:
    """`arguments` (`panel build ...` or `factor build ...`) as the command itself parses them:
    its kind and its parameters -- click's own parse of the real command, without running it.
    `resilient`: no callback, no prompt and no usage error, for the parameters to be judged by;
    otherwise strictly, as the command will, so that a usage error it would raise refuses the
    remedy here -- `daily_selection.invoke` catches the other click's `ClickException`, and a usage
    error of this one would escape the build as a fault, after `start`, discarding the root."""
    head = tuple(arguments[:2])
    kind = _OPENALPHA_COMMANDS.get((head[0], head[1])) if len(head) == 2 else None
    if kind is None:
        raise RemedyError(
            f"`openalpha {' '.join(head)}` is not a remedy this driver runs: only `panel build` "
            "and `factor build`"
        )
    # Typer runs a click of its own (`typer._click`), so the tree is walked by what it holds, not
    # by click's classes.
    tree: Any = get_command(cli.app)
    group: Any = getattr(tree, "commands", {}).get(head[0])
    command: Any = getattr(group, "commands", {}).get(head[1])
    if command is None:
        raise RemedyError(f"this build has no `openalpha {' '.join(head)}`")
    name = f"openalpha {' '.join(head)}"
    try:
        context = command.make_context(name, list(arguments[2:]), resilient_parsing=resilient)
    except Exception as error:  # that click's own `ClickException`, or worse: refused either way
        shown = error.format_message() if hasattr(error, "format_message") else str(error)
        raise RemedyError(f"`{name}` refuses this remedy: {shown}") from error
    return kind, dict(context.params)


def _openalpha_remedy(arguments: Sequence[str], *, runtime: Path) -> Remedy:
    """The remedy, judged by the parameters the command will see (the module docstring), then
    parsed strictly: one the command would refuse as a usage error is refused here."""
    kind, parameters = _parse(arguments, resilient=True)
    given = parameters.get("runtime_dir")
    if not _same(given, runtime):
        raise RemedyError(
            f"this remedy's --runtime-dir, as the command reads it, is {given}, not this smoke's "
            f"runtime {runtime}; the smoke never builds another runtime directory, least of all "
            "~/openalpha-research"
        )
    datasets = {str(dataset) for dataset in parameters.get("dataset") or ()}
    if kind == "panel-build" and not parameters.get("incremental") and datasets != {"trade_cal"}:
        raise RemedyError(
            "this is the full rebuild of the session-scoped targets -- the whole-year rebuild "
            "remedy, about 1,200 requests at live scale -- which the smoke never runs: the "
            "panel copy is in a state only a discard repairs. Stop, ask the user, and prepare "
            "a new root from the original panel"
        )
    _parse(arguments, resilient=False)
    return Remedy(kind=kind, arguments=tuple(arguments))


def _day_remedy(
    arguments: Sequence[str], *, runtime: Path, registration: Path, pinned: Path
) -> Remedy:
    kept: list[str] = []
    named: dict[str, str] = {}
    words = iter(arguments)
    for word in words:
        if word in ("--runtime-dir", "--registration", "--repo"):
            named[word] = next(words, "")
        else:
            kept.append(word)
    for flag, expected in (("--runtime-dir", runtime), ("--registration", registration)):
        if flag not in named or not _same(named[flag], expected):
            raise RemedyError(
                f"this remedy's {flag} is {named.get(flag)!r}, not this smoke's {expected}"
            )
    if "--repo" in named and not _same(named["--repo"], pinned):
        raise RemedyError(f"this remedy's --repo is {named['--repo']!r}, not {pinned}")
    if "--morning-recheck" in kept:
        kept.remove("--morning-recheck")
        return Remedy(kind="morning", arguments=tuple(kept))
    return Remedy(kind="evening", arguments=tuple(kept))


# --- the driver's own guards ---------------------------------------------------------------------


def _refuse_path_flags(extra: Sequence[str]) -> None:
    """Refuse an extra argument that names a path flag, an abbreviation of one (argparse takes
    `--runtime` for `--runtime-dir`) or either joined to its value."""
    for word in extra:
        if not word.startswith("--") or word == "--":
            continue
        name = word.split("=", 1)[0]
        for flag in PATH_FLAGS:
            if flag.startswith(name):
                raise SmokeDriverError(
                    f"{word} is {flag}: the driver's to set, never an invocation's"
                )


def _one_tree() -> None:
    """The cap binds the day only when `p7_smoke_requests` holds this driver's `daily_selection`
    -- the one beside this file -- and that module's `cli` is the one `panel build` runs."""
    for name, module, where in (
        ("daily_selection", daily, _SCRIPTS),
        ("forward_report", forward_report, _SCRIPTS),
        ("p7_smoke_env", env, _SCRIPTS),
        ("p7_smoke_requests", budget, _SCRIPTS),
        ("registry", registry, _SCRIPTS / "research"),
    ):
        found = Path(module.__file__ or "").resolve().parent
        if found != where:
            raise SmokeDriverError(
                f"{name} was imported from {found}, not from {where} beside this driver: the "
                "smoke runs one tree's code"
            )
    # `getattr`: none of these modules re-exports what it imported (mypy's strict mode).
    holders = [getattr(module, "daily_selection", None) for module in (budget, env, forward_report)]
    if any(holder is not daily for holder in holders):
        raise SmokeDriverError(
            "p7_smoke_requests, p7_smoke_env and forward_report do not all hold this driver's "
            "daily_selection: the cap would be put on another module's cli and count none of the "
            "day's requests; nothing was run"
        )
    if getattr(daily, "cli", None) is not cli or sys.modules.get(cli.__name__) is not cli:
        raise SmokeDriverError(
            "daily_selection's cli is not the openalpha_cn.cli this driver runs: the cap would "
            "be put on a transport the day does not use; nothing was run"
        )


@contextlib.contextmanager
def _driver_lock(root: Path) -> Iterator[None]:
    """`<root>/driver.lock`, exclusively, for the body: one invocation at a time in a root."""
    path = root / DRIVER_LOCK
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(descriptor, exclusive=True):
            raise SmokeDriverError(
                f"another smoke invocation holds {path}: one invocation at a time in a smoke "
                "root; wait for it to end (`status` shows it as unclosed meanwhile)"
            )
        try:
            yield
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _signals_as_exit() -> Iterator[None]:
    """SIGINT, SIGTERM and SIGHUP mark the interruption (`_INTERRUPTED`) and raise
    `KeyboardInterrupt` for the body, in the main thread only; a signal the caller ignores
    (`nohup`, a background job) is left ignored, and the handlers that were there are put back
    after."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def interrupt(number: int, _frame: FrameType | None) -> None:
        _INTERRUPTED[0] = True  # one assignment: no lock is taken in a signal handler
        raise KeyboardInterrupt(f"{signal.Signals(number).name} received")

    previous: dict[int, Any] = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None and signal.getsignal(number) != signal.SIG_IGN:
            previous[number] = signal.signal(number, interrupt)
    _INTERRUPTED[0] = False
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)


def _reread(manifest: env.SmokeManifest) -> env.SmokeManifest:
    """The manifest read again -- under the lock -- and its ledger checked again with it."""
    current = env.SmokeManifest.read(manifest.root)
    if current != manifest:
        raise SmokeDriverError(
            f"{manifest.root / env.MANIFEST_NAME} is not the manifest this invocation was given; "
            "it is written once, so this root is refused"
        )
    return current


def _logged(logs: Path) -> list[int]:
    """The invocation numbers `logs` holds output of."""
    if not logs.is_dir():
        return []
    return sorted(
        {int(found.group(1)) for name in os.listdir(logs) if (found := _LOG.fullmatch(name))}
    )


def ledger_problem(events: Sequence[Mapping[str, Any]], *, logs: Path) -> str | None:
    """Why a ledger of `events` cannot be the whole record of the root whose invocation logs are
    in `logs` (the module docstring), or `None`."""
    starts = [event for event in events if event["event"] == "start"]
    numbers = [int(event["id"]) for event in starts]
    if numbers != list(range(1, len(numbers) + 1)):
        return (
            f"its invocations are numbered {numbers}, not 1 to {len(numbers)}: lines were taken "
            "out of it"
        )
    ends = {int(event["id"]): event for event in events if event["event"] == "end"}
    counted = 0
    for start in starts:
        number = int(start["id"])
        if int(start["spent_before"]) != counted:
            return (
                f"invocation {number} started with {start['spent_before']} request(s) spent, and "
                f"the invocations before it record {counted}: lines were taken out of it"
            )
        if number in ends:
            counted += int(ends[number]["count"])
    kinds = {int(start["id"]): str(start["kind"]) for start in starts}
    for index, end in enumerate(events):
        if end["event"] != "end" or (end.get("reached") is None and end.get("fault") is None):
            continue
        number = int(end["id"])
        rule = RULES.get(kinds.get(number, ""))
        if rule is None or not rule.discard_on_cap:
            continue
        before = events[index - 1] if index else {}
        if before.get("event") != "discarded" or not str(before.get("reason")).startswith(
            f"invocation {number} ("
        ):
            cause = "the cap" if end.get("reached") is not None else f"a {end.get('fault')}"
            return (
                f"invocation {number} ({kinds[number]}) wrote the panel and ended on {cause}, and "
                "the `discarded` line `run_capped` writes just before that end is not there: it "
                "was taken out, and this root was discarded"
            )
    logged = _logged(logs)
    if logged and logged[-1] > len(starts):
        return (
            f"{logs} holds the output of invocation {logged[-1]}, and the ledger records "
            f"{len(starts)}: it was cut back, and what it says was spent is too little"
        )
    return None


@contextlib.contextmanager
def _readable(path: Path) -> Iterator[None]:
    """A ledger event the driver cannot read -- a field missing, a phase with no cap -- refuses
    the root by name rather than escaping as a bare `KeyError`. The ledger is written by
    `p7_smoke_requests` and this driver alone."""
    try:
        yield
    except (budget.SmokeBudgetError, env.SmokeEnvironmentError, SmokeDriverError):
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SmokeDriverError(
            f"the request ledger {path} holds an event this driver cannot read "
            f"({type(error).__name__}: {error}); it is written by the driver alone, so this root "
            "is refused -- prepare a new one"
        ) from error


def _refuse_a_broken_ledger(manifest: env.SmokeManifest, ledger: budget.RequestLedger) -> None:
    problem = ledger_problem(ledger.events(), logs=manifest.logs)
    if problem is not None:
        raise SmokeDriverError(
            f"the request ledger {ledger.path} is not the whole record of this root: {problem}; "
            "this root is refused -- prepare a new one"
        )


# --- an invocation -------------------------------------------------------------------------------


def _call(
    manifest: env.SmokeManifest,
    kind: str,
    arguments: Sequence[str],
    *,
    number: int,
    clock: Callable[[], datetime],
) -> Callable[[], int]:
    base = ["--runtime-dir", str(manifest.runtime), "--registration", str(manifest.registration)]
    base += ["--repo", str(manifest.pinned)]

    def wrapped() -> int:
        if kind in OPENALPHA_KINDS:
            done = daily.invoke(list(arguments))
            sys.stdout.write(done.stdout)
            sys.stderr.write(done.stderr)
            return done.exit_code
        if kind == "weekly":
            return forward_report.main([*base, *arguments], clock=clock)
        flags = ["--morning-recheck"] if kind == "morning" else []
        return daily.main([*base, *arguments, *flags], clock=clock)

    def call() -> int:
        manifest.logs.mkdir(exist_ok=True)
        stem = manifest.logs / f"{number:04d}-{kind}"
        with (
            # `x`: a log is never overwritten -- the logs are the ledger's second record.
            stem.with_suffix(".out").open("x", encoding="utf-8") as out,
            stem.with_suffix(".err").open("x", encoding="utf-8") as err,
            # `redirect_*` is typed for an `IO[str]`; a `TextIOBase` is not one to mypy.
            contextlib.redirect_stdout(cast(TextIO, _Tee(out, sys.stdout))),
            contextlib.redirect_stderr(cast(TextIO, _Tee(err, sys.stderr))),
        ):
            _INTERRUPTED[0] = False
            try:
                code = wrapped()
            except KeyboardInterrupt:
                raise
            except BaseException as error:
                # click turns a `KeyboardInterrupt` inside a command into `Abort`.
                if _INTERRUPTED[0]:
                    raise KeyboardInterrupt(
                        f"an interruption reached the command, which ended on "
                        f"{type(error).__name__}"
                    ) from error
                raise
            if _INTERRUPTED[0]:
                # The day reports an interrupted `panel build` as step 2's refusal, and `panel
                # build` alone exits 130 of its own: either is an interruption here.
                raise KeyboardInterrupt(
                    f"an interruption reached the command, which exited {code} of its own"
                )
            return code

    return call


def _event(events: Sequence[Mapping[str, Any]], name: str, number: int) -> Mapping[str, Any] | None:
    return next(
        (event for event in events if event["event"] == name and event["id"] == number), None
    )


def _capped(
    ledger: budget.RequestLedger, number: int
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Invocation `number`'s `start` and `end` when its end records a request the cap stopped,
    else `None` -- a ledger that cannot be read included: the exception being handled is then
    the one to report."""
    try:
        events = ledger.events()
    except budget.SmokeBudgetError:
        return None
    start, end = _event(events, "start", number), _event(events, "end", number)
    if start is None or end is None or end.get("reached") is None:
        return None
    return start, end


def _cap_lines(
    manifest: env.SmokeManifest,
    *,
    kind: str,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    spent: budget.Spent,
) -> list[str]:
    """What the driver prints on stderr when the cap stopped an invocation."""
    cap = int(start["cap"])
    phase = int(start["phase"])
    allowance = int(start["allowance"])
    if allowance < cap - int(start["spent_before"]):
        limit = f"a {kind}'s ceiling of {allowance} request(s) (phase {phase}'s cap is {cap})"
    else:
        limit = f"phase {phase}'s cap of {cap}"
    lines = [
        f"REQUEST CAP REACHED  invocation {end['id']} stopped: the {end['reached']} request "
        f"that would have passed {limit} was not sent",
    ]
    if kind in ("evening", "morning"):
        lines.append(
            "  the day's own `tushare requests` line counts that unsent request; this ledger does "
            "not"
        )
    lines.append(f"  phase {spent.phase}: {spent.total} of {cap} requests spent")
    if spent.discarded is not None:
        lines += [
            f"  the panel copy {manifest.runtime / env.PANEL_NAME} may be half-written, so this "
            "smoke root is discarded: every later invocation in it is refused",
            "  run no `panel build` remedy on it -- above all not the whole-year rebuild one "
            "(about 1,200 requests at live scale)",
            "  ask the user before spending more; then prepare a new root from the original "
            "panel, with a new epoch, from the main checkout:",
            "  uv run --no-sync python scripts/p7_smoke.py prepare --smoke-root <new root> "
            "--epoch <the session before the new first evening> --phase1-cap <N> "
            f"--phase2-cap <M> --source-panel {manifest.source_panel}",
        ]
    else:
        lines.append(
            f"  the root is not discarded (a {kind} does not discard it); ask the user before the "
            "next invocation"
        )
    return lines


def _remain(cap: int, spent: budget.Spent) -> str:
    return f"{spent.total} of {cap} requests spent ({cap - spent.total} remain)"


def run(
    manifest: env.SmokeManifest,
    kind: str,
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime],
) -> int:
    """One invocation of `kind` with `arguments`, under the cap, recorded in the ledger (the module
    docstring); its exit code."""
    rule = RULES.get(kind)
    if rule is None:
        raise SmokeDriverError(f"{kind!r} is not an invocation the driver makes")
    _one_tree()
    if kind in OPENALPHA_KINDS:
        _refuse_joined(arguments)
        if _openalpha_remedy(arguments, runtime=manifest.runtime).kind != kind:
            raise SmokeDriverError(f"{shlex.join(arguments)} is not a {kind}")
    else:
        _refuse_path_flags(arguments)
    with _driver_lock(manifest.root):
        current = _reread(manifest)
        ledger = budget.RequestLedger(current.ledger_path)
        with _readable(ledger.path):
            spent = ledger.spent()
            _refuse_a_broken_ledger(current, ledger)
            cap = current.caps[spent.phase]
        if kind in OPENALPHA_KINDS:
            # The day, the morning and the weekly report admit their own code; `openalpha` here
            # runs whatever this process imported.
            registry._refuse_a_foreign_package(current.pinned)
        print(f"smoke {kind}: phase {spent.phase}, {_remain(cap, spent)}", file=sys.stderr)
        number = spent.invocations + 1
        try:
            done = budget.run_capped(
                ledger,
                kind=kind,
                arguments=arguments,
                cap=cap,
                ceiling=rule.ceiling,
                headroom=rule.headroom(current),
                discard_on_cap=rule.discard_on_cap,
                call=_call(current, kind, arguments, number=number, clock=clock),
                clock=clock,
            )
        except BaseException:
            # `run_capped` records the end and raises when the call ends on an exception -- also
            # after the cap stopped a request; the ledger says whether it did.
            capped = _capped(ledger, number)
            if capped is None:
                raise
            start, end = capped
            for line in _cap_lines(current, kind=kind, start=start, end=end, spent=ledger.spent()):
                print(line, file=sys.stderr)
            return budget.CAP_EXIT
        after = ledger.spent()
        code = done.exit_code
        if kind in OPENALPHA_KINDS and done.reached is None and code in SHADOWED_EXITS:
            code = COMMAND_FAILED_EXIT
        own = (
            ""
            if done.wrapped_exit_code == code
            else f" (the command's own {done.wrapped_exit_code})"
        )
        print(
            f"smoke {kind}: invocation {done.invocation} exited {code}{own}; "
            f"{sum(done.sent.values())} request(s) sent "
            f"{json.dumps(dict(done.sent), sort_keys=True)}; phase {after.phase}: "
            f"{_remain(cap, after)}",
            file=sys.stderr,
        )
        capped = _capped(ledger, number) if done.reached is not None else None
        if capped is not None:
            start, end = capped
            for line in _cap_lines(current, kind=kind, start=start, end=end, spent=after):
                print(line, file=sys.stderr)
        return code


# --- status and phase ----------------------------------------------------------------------------


def _shanghai(at: object) -> str:
    return datetime.fromisoformat(str(at)).astimezone(daily.SHANGHAI).isoformat(timespec="seconds")


def status_lines(manifest: env.SmokeManifest) -> list[str]:
    """The root's state, read from its ledger without the lock: an invocation that is running
    shows as unclosed. A ledger that is not the whole record of the root shows a
    `ledger        refused: ...` line, and `status` then exits `REFUSED_EXIT`."""
    with _readable(manifest.ledger_path):
        return _status_lines(manifest)


def _status_lines(manifest: env.SmokeManifest) -> list[str]:
    ledger = budget.RequestLedger(manifest.ledger_path)
    events = ledger.events()
    spent = ledger.spent()
    cap = manifest.caps[spent.phase]
    floor = "at least " if spent.unclosed else ""
    ceiling = "at most " if spent.unclosed else ""
    lines = [
        f"smoke root    {manifest.root}",
        f"registration  sha256 {manifest.registration_sha256[:12]}  config_id "
        f"{manifest.config_id[:16]}  epoch {manifest.epoch.isoformat()}",
        f"phase         {spent.phase} (its cap {cap}; phase 1's {manifest.caps[1]}, phase 2's "
        f"{manifest.caps[2]})",
        f"spent         {floor}{spent.total} of {cap} ({ceiling}{cap - spent.total} remain)",
        f"discarded     {spent.discarded if spent.discarded is not None else 'no'}",
    ]
    if spent.unclosed:
        lines.append(
            f"unclosed      invocation(s) {list(spent.unclosed)} started and recorded no end: "
            "running now, or killed -- what they sent is not in the count above"
        )
    problem = ledger_problem(events, logs=manifest.logs)
    if problem is not None:
        lines.append(f"{_REFUSED_LEDGER}{problem}")
    ends = {int(event["id"]): event for event in events if event["event"] == "end"}
    by_api: Counter[str] = Counter()
    for event in events:
        if event["event"] != "start":
            continue
        number = int(event["id"])
        head = f"  #{number:04d} {event['kind']:<12} {_shanghai(event['at'])}"
        end = ends.get(number)
        if end is None:
            lines.append(f"{head}  no end")
            continue
        by_api.update({str(api): int(count) for api, count in end["requests"].items()})
        exit_code = "-" if end["exit_code"] is None else end["exit_code"]
        fault = f"  fault {end['fault']}" if end.get("fault") else ""
        reached = "  CAP REACHED" if end.get("reached") is not None else ""
        lines.append(
            f"{head}  {float(end['seconds']):.1f} s  exit {exit_code}  {end['count']} request(s)"
            f"{fault}{reached}"
        )
    lines.append(f"by api        {json.dumps(dict(sorted(by_api.items())), sort_keys=True)}")
    return lines


def _phase(manifest: env.SmokeManifest, *, reason: str, clock: Callable[[], datetime]) -> int:
    """Phase 1 to 2, the one change: a `phase` event in the ledger."""
    text = reason.strip()
    if not text:
        raise SmokeDriverError("--reason is the user's go, in words, with its date; it is empty")
    with _driver_lock(manifest.root):
        current = _reread(manifest)
        ledger = budget.RequestLedger(current.ledger_path)
        with _readable(ledger.path):
            spent = ledger.spent()
            _refuse_a_broken_ledger(current, ledger)
        if spent.phase != 1:
            raise SmokeDriverError(
                f"this smoke is in phase {spent.phase} already: phase 1 to 2 is the one change, "
                "made once"
            )
        if spent.discarded is not None:
            raise SmokeDriverError(
                f"this smoke root was discarded ({spent.discarded}); nothing more runs in it"
            )
        if spent.unclosed:
            raise SmokeDriverError(
                f"invocation(s) {list(spent.unclosed)} recorded no end; nothing more runs in "
                "this root"
            )
        ledger.append(
            {
                "event": "phase",
                "phase": 2,
                "from": 1,
                "reason": text,
                "spent": spent.total,
                "at": clock().isoformat(),
            }
        )
    print(f"smoke phase: 1 -> 2; {_remain(current.caps[2], spent)}; reason: {text}")
    return 0


# --- prepare -------------------------------------------------------------------------------------


def _prepare(arguments: argparse.Namespace, *, clock: Callable[[], datetime]) -> int:
    """`prepare_smoke` of this checkout, at its `HEAD` unless `--commit` names it, and of P6's
    panel unless `--source-panel` names another; sends no request."""
    source = REPOSITORY
    if daily.REPOSITORY.resolve() != source.resolve():
        raise SmokeDriverError(
            f"daily_selection's checkout {daily.REPOSITORY} is not this driver's {source}"
        )
    if env._git(source, "rev-parse", "--is-shallow-repository") == "true":
        print(
            f"smoke: warning: {source} is a shallow repository: `git clone --local` falls back to "
            "a transport clone of it, and the check that the commit is on main reads a cut "
            "history; the prepare goes on as it would otherwise",
            file=sys.stderr,
        )
    dirty = env._git(source, "status", "--porcelain", "--untracked-files=all", "--", *SMOKE_SCRIPTS)
    if dirty:
        raise SmokeDriverError(
            f"the smoke's own scripts in {source} are not what HEAD holds "
            f"({dirty.splitlines()[0].strip()} ...): the registration is written by "
            "scripts/p7_smoke_env.py, which is no bound path, and must be the code the clone "
            "holds; commit or drop the change. Nothing was made"
        )
    commit = arguments.commit or env._git(source, "rev-parse", "HEAD")
    panel = arguments.source_panel or Path.home() / "openalpha-research" / env.PANEL_NAME
    uv = (arguments.uv or Path(shutil.which("uv") or "uv")).resolve()
    manifest = env.prepare_smoke(
        root=arguments.smoke_root.expanduser(),
        source_repo=source,
        commit=commit,
        source_panel=panel.expanduser(),
        epoch=arguments.epoch,
        caps={1: arguments.phase1_cap, 2: arguments.phase2_cap},
        policy=env.PathPolicy.default(daily.REPOSITORY),
        uv=uv,
        clock=clock,
        copy_tree=env.apfs_clone,
    )
    smoke = (
        f"smoke() {{ ( cd {shlex.quote(str(manifest.pinned))} && "
        f'UV_PROJECT_ENVIRONMENT="$PWD/{daily.PINNED_ENVIRONMENT}" uv run --no-sync --env-file '
        f'{daily.ENV_FILE} "$PWD/{daily.PINNED_ENVIRONMENT}/bin/python" scripts/p7_smoke.py "$@" '
        ") }"
    )
    lines = [
        f"smoke root    {manifest.root}",
        f"commit        {manifest.commit} (the registration's: {manifest.registration_commit})",
        f"registration  {manifest.registration}",
        f"              sha256 {manifest.registration_sha256}",
        f"              config_id {manifest.config_id}",
        f"epoch         {manifest.epoch.isoformat()}; catch-up bound {manifest.catch_up_bound} "
        "session(s)",
        f"caps          phase 1: {manifest.caps[1]} requests; phase 2 (the whole smoke): "
        f"{manifest.caps[2]}",
        f"panel         {manifest.panel_files} file(s) cloned from {manifest.source_panel} to "
        f"{manifest.runtime / env.PANEL_NAME}; catalog sha256 {manifest.source_catalog_sha256}",
        f"ledger        {manifest.ledger_path}: prepared, 0 requests spent",
        "next          every invocation from now on runs in the pinned worktree, through this "
        "driver:",
        f"  {smoke}",
        "  smoke status    -- 0 requests spent",
        "  smoke evening   -- the first evening: after 18:30 Asia/Shanghai of the session after "
        "the epoch, before 09:15 of the session after that",
    ]
    print("\n".join(lines))
    return 0


# --- the command line ----------------------------------------------------------------------------


def default_smoke_root(script: Path) -> Path | None:
    """`<root>` when `script` is `<root>/pinned/scripts/p7_smoke.py` and `<root>/smoke.json`
    exists, else `None`."""
    scripts = script.parent
    pinned = scripts.parent
    root = pinned.parent
    if (
        scripts.name == "scripts"
        and pinned.name == env.PINNED_NAME
        and (root / env.MANIFEST_NAME).is_file()
    ):
        return root
    return None


def _root_of(given: Path | None) -> Path:
    if given is not None:
        return given.expanduser()
    found = default_smoke_root(Path(__file__).resolve())
    if found is None:
        raise SmokeDriverError(
            "--smoke-root is needed: this driver is not the copy in a smoke root's pinned "
            "worktree (<root>/pinned/scripts/p7_smoke.py beside <root>/smoke.json)"
        )
    return found


def _one_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RemedyError(
            f"a remedy is the one line the refusal printed; {len(lines)} line(s) were given"
        )
    return lines[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p7_smoke.py",
        description=__doc__.splitlines()[0] if __doc__ else None,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Make a smoke root (no request).")
    prepare.add_argument("--smoke-root", type=Path, required=True)
    prepare.add_argument("--epoch", type=date.fromisoformat, required=True)
    prepare.add_argument("--phase1-cap", type=int, required=True)
    prepare.add_argument("--phase2-cap", type=int, required=True)
    prepare.add_argument("--commit", default=None, help="Default: this checkout's HEAD.")
    prepare.add_argument(
        "--source-panel", type=Path, default=None, help="Default: ~/openalpha-research/panel."
    )
    prepare.add_argument("--uv", type=Path, default=None, help="Default: the uv on PATH.")
    for name, text in (
        ("evening", "The day: scripts/daily_selection.py."),
        ("morning", "The morning re-check: scripts/daily_selection.py --morning-recheck."),
        ("weekly", "The weekly report: scripts/forward_report.py."),
    ):
        day = commands.add_parser(name, help=text)
        day.add_argument("--smoke-root", type=Path, default=None)
        day.add_argument("extra", nargs=argparse.REMAINDER, help="After `--`: its own options.")
    remedy = commands.add_parser("remedy", help="Run the line a refusal printed.")
    remedy.add_argument("--smoke-root", type=Path, default=None)
    remedy.add_argument("line", help="The line, or `-` to read it from stdin.")
    status = commands.add_parser("status", help="What the root has spent, and on what.")
    status.add_argument("--smoke-root", type=Path, default=None)
    phase = commands.add_parser("phase", help="Enter phase 2, once.")
    phase.add_argument("--smoke-root", type=Path, default=None)
    phase.add_argument("--to", type=int, choices=(2,), required=True)
    phase.add_argument("--reason", required=True)
    return parser


def _dispatch(arguments: argparse.Namespace, *, clock: Callable[[], datetime]) -> int:
    if arguments.command == "prepare":
        return _prepare(arguments, clock=clock)
    manifest = env.SmokeManifest.read(_root_of(arguments.smoke_root))
    if arguments.command == "status":
        lines = status_lines(manifest)
        print("\n".join(lines))
        refused = any(line.startswith(_REFUSED_LEDGER) for line in lines)
        return REFUSED_EXIT if refused else 0
    if arguments.command == "phase":
        return _phase(manifest, reason=arguments.reason, clock=clock)
    if arguments.command == "remedy":
        text = sys.stdin.read() if arguments.line == "-" else arguments.line
        remedy = parse_remedy(
            _one_line(text),
            runtime=manifest.runtime,
            registration=manifest.registration,
            pinned=manifest.pinned,
        )
        return run(manifest, remedy.kind, remedy.arguments, clock=clock)
    extra = list(arguments.extra)
    if extra[:1] == ["--"]:
        extra = extra[1:]
    return run(manifest, arguments.command, extra, clock=clock)


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    arguments = _parser().parse_args(argv)
    try:
        with _signals_as_exit():
            return _dispatch(arguments, clock=clock)
    except (env.SmokeEnvironmentError, budget.SmokeBudgetError, SmokeDriverError) as error:
        print(f"smoke: refused: {error}", file=sys.stderr)
        return REFUSED_EXIT
    except registry.HoldoutRefusedError as error:
        print(f"smoke: refused: {type(error).__name__}: {error}", file=sys.stderr)
        return int(daily.DailyExit.code_not_registered)
    except KeyboardInterrupt:
        if arguments.command == "prepare":
            print(
                "smoke: interrupted; the smoke root may be half made: delete it, then prepare "
                "again",
                file=sys.stderr,
            )
        else:
            print(
                "smoke: interrupted; run `status` before anything else. An invocation that had "
                "started has its end in the ledger -- and one that writes the panel discarded "
                "the root -- unless the interruption fell between its `start` and its call, or "
                "between its `discarded` and its `end`: then it has no end, which refuses the "
                "root as a killed one's would",
                file=sys.stderr,
            )
        return INTERRUPTED_EXIT


if __name__ == "__main__":
    sys.exit(main())
