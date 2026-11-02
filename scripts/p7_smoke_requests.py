"""The request budget of the real-data smoke (`V2-P7-019`, task 2): a hard cap on the Tushare
requests one smoke invocation may send, and the append-only ledger that adds them up.

**The cap rides the daily command's own counter.** `daily_selection.counted_transport(counts)`
routes every request `panel build` makes -- the one seam that reaches Tushare,
`cli._panel_transport` -- through a `CountingTransport`, which increments `counts[api_name]` and
only then forwards the request (`scripts/daily_selection.py:1219-1221`). `CappedCounter` is a
`Counter` that refuses the increment past its allowance by raising `RequestCapReached`: entered
around a whole invocation, the request that would pass the cap is never forwarded, and neither is
any retry of it -- `TushareProvider` retries a `ProviderFailure` or a network `OSError` only, and
every retry would be refused the same way. No transport is written here.

**The day's own count reads one more.** The daily command enters its own `counted_transport`
inside this one, so its counter increments first: when the cap stops a request, the day's
`tushare requests` line counts that unsent request, and this ledger -- which counts what was
forwarded -- does not. `panel build` reports the exception as an internal error with its message
withheld, so a capped day refuses at step 2 with `` `panel build` exited ... RequestCapReached ``.

**The ledger** (`requests.jsonl` in the smoke root) is one JSON event a line, flushed and synced:
`prepared`, `phase`, `start`, `end` and `discarded`. What was spent is the sum of the `end`
counts. An invocation that started and recorded no end was killed: what it sent is unknown and
the panel copy may be half-written, so nothing more runs in the root. An invocation that writes
the panel and stops on the cap, or on any fault or interruption, discards the root.

**`discarded` is written before `end`.** They are two appends, and a second interruption (a
second Ctrl-C, or a SIGTERM turned into `KeyboardInterrupt`) can land between them. In this order
it leaves a root that is discarded and has no end -- refused twice over; in the other it would
leave a `start` and an `end` and nothing to refuse the next evening on a half-written panel.

**One writer at a time, and nothing skipped.** The driver holds `<root>/driver.lock` (task 4)
from reading `spent()` to writing `end`, so the ledger has a single writer. Two that read the same
count would both write `start` with the same number: `spent()` refuses a number that appears
twice among the `start` events, or twice among the `end` events, instead of folding it into one.
A line is split at a newline alone -- `str.splitlines()` would also split at U+2028, U+2029 and
U+0085, which a string field may hold and `ensure_ascii=False` leaves as it is -- and a line that
cannot be read (a cut-off last write included) refuses the ledger, never gets skipped: it may be
the `discarded` line.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402

LEDGER_SCHEMA: Final[str] = "openalpha-p7-smoke-ledger/v1"
CAP_EXIT: Final[int] = 4
"""What an invocation the cap stopped exits with: none of the daily command's exits (0 to 3)."""


class RequestCapReached(RuntimeError):
    """The request that would have passed the cap. It was not sent."""

    def __init__(self, *, allowance: int, api_name: str) -> None:
        super().__init__(
            f"the V2-P7-019 smoke's request cap: {allowance} of {allowance} requests of this "
            f"invocation's allowance are spent, and this {api_name} request was not sent"
        )
        self.allowance = allowance
        self.api_name = api_name


class SmokeBudgetError(ValueError):
    """An invocation the ledger refuses to start, or a ledger that cannot be read."""


class CappedCounter(Counter[str]):
    """A `Counter` of requests by `api_name` that refuses the increment past `allowance`."""

    def __init__(self, allowance: int) -> None:
        if type(allowance) is not int or allowance < 0:
            raise ValueError(
                f"an allowance is a whole number of requests, 0 or more, not {allowance!r}"
            )
        self.allowance = allowance
        self.reached: str | None = None
        """The `api_name` of the first request refused, or `None`."""
        super().__init__()

    def __setitem__(self, key: str, value: int, /) -> None:
        total = sum(self.values()) - self.get(key, 0) + value
        if total > self.allowance:
            if self.reached is None:
                self.reached = key
            raise RequestCapReached(allowance=self.allowance, api_name=key)
        super().__setitem__(key, value)

    @property
    def sent(self) -> int:
        return sum(self.values())


@contextlib.contextmanager
def capped_requests(allowance: int) -> Iterator[CappedCounter]:
    """Every request through `cli._panel_transport` for the body, counted, and refused past
    `allowance` (the module docstring)."""
    counter = CappedCounter(allowance)
    with daily_selection.counted_transport(counter):
        yield counter


@dataclass(frozen=True, slots=True, kw_only=True)
class Spent:
    total: int
    phase: int
    discarded: str | None
    unclosed: tuple[int, ...]
    invocations: int


class RequestLedger:
    """The smoke root's `requests.jsonl` (the module docstring)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        found: list[dict[str, Any]] = []
        lines = self.path.read_text(encoding="utf-8").split("\n")
        if lines[-1] == "":
            lines.pop()  # what follows the last newline
        for number, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except ValueError as error:
                raise SmokeBudgetError(
                    f"{self.path.name}, line {number}, is not JSON ({error}): the ledger is "
                    "written by this module alone and is left as it is"
                ) from error
            if (
                not isinstance(event, dict)
                or event.get("schema") != LEDGER_SCHEMA
                or not isinstance(event.get("event"), str)
            ):
                raise SmokeBudgetError(
                    f"{self.path.name}, line {number}, is not a {LEDGER_SCHEMA} event; the ledger "
                    "is left as it is"
                )
            found.append(event)
        return found

    def append(self, event: Mapping[str, Any]) -> None:
        line = json.dumps({**event, "schema": LEDGER_SCHEMA}, sort_keys=True, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def spent(self) -> Spent:
        events = self.events()
        numbered: set[tuple[str, int]] = set()
        for event in events:
            if event["event"] in ("start", "end"):
                key = (str(event["event"]), int(event["id"]))
                if key in numbered:
                    raise SmokeBudgetError(
                        f"{self.path.name} has two events for invocation {key[1]}, both "
                        f"`{key[0]}`: two writers took the same number, so what was spent cannot "
                        "be told. The ledger is left as it is"
                    )
                numbered.add(key)
        ended = {event["id"]: event for event in events if event["event"] == "end"}
        starts = [event for event in events if event["event"] == "start"]
        phases = [int(event["phase"]) for event in events if event["event"] == "phase"]
        discarded = [str(event["reason"]) for event in events if event["event"] == "discarded"]
        return Spent(
            total=sum(int(event["count"]) for event in ended.values()),
            phase=phases[-1] if phases else 1,
            discarded=discarded[-1] if discarded else None,
            unclosed=tuple(int(event["id"]) for event in starts if event["id"] not in ended),
            invocations=len(starts),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CappedRun:
    invocation: int
    exit_code: int
    wrapped_exit_code: int | None
    sent: Mapping[str, int]
    reached: str | None
    seconds: float


def _requests(name: str, value: object) -> int:
    """`value` as a count of requests: a whole number, 0 or more. A `bool` is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SmokeBudgetError(
            f"`{name}` is a whole number of requests, 0 or more, not {value!r}; nothing was "
            "written to the ledger"
        )
    return value


def run_capped(
    ledger: RequestLedger,
    *,
    kind: str,
    arguments: Sequence[str],
    cap: int,
    ceiling: int | None,
    headroom: int,
    discard_on_cap: bool,
    call: Callable[[], int],
    clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> CappedRun:
    """`call` under the cap, recorded in `ledger`; see the module docstring for every refusal.

    Everything that can refuse happens before `start` is written: a `start` with no `end` refuses
    the root for good, as a killed process would.
    """
    _requests("cap", cap)
    _requests("headroom", headroom)
    if ceiling is not None:
        _requests("ceiling", ceiling)
    spent = ledger.spent()
    if spent.discarded is not None:
        raise SmokeBudgetError(
            f"this smoke root was discarded ({spent.discarded}); nothing more runs in it -- "
            "prepare a new one"
        )
    if spent.unclosed:
        raise SmokeBudgetError(
            f"invocation(s) {list(spent.unclosed)} started and never recorded their end -- the "
            "process was killed: what they sent is unknown and the panel copy may be "
            "half-written. Discard this root and prepare a new one"
        )
    remaining = cap - spent.total
    if remaining < headroom:
        raise SmokeBudgetError(
            f"{remaining} request(s) of phase {spent.phase}'s cap of {cap} remain, and a {kind} "
            f"needs {headroom} to start; ask the user before the cap is raised"
        )
    allowance = remaining if ceiling is None else min(remaining, ceiling)
    counter = CappedCounter(allowance)
    invocation = spent.invocations + 1
    ledger.append(
        {
            "event": "start",
            "id": invocation,
            "kind": kind,
            "arguments": list(arguments),
            "phase": spent.phase,
            "cap": cap,
            "allowance": allowance,
            "spent_before": spent.total,
            "at": clock().isoformat(),
        }
    )
    started = monotonic()
    wrapped: int | None = None
    fault: str | None = None
    try:
        with daily_selection.counted_transport(counter):
            try:
                wrapped = call()
            except RequestCapReached:
                wrapped = None
            except SystemExit as error:
                wrapped = error.code if isinstance(error.code, int) else 1
    except BaseException as error:
        fault = type(error).__name__
        raise
    finally:
        seconds = round(monotonic() - started, 3)
        # `discarded` first, then `end`: an interruption between the two appends must leave a root
        # that is discarded (and without an end), never one with an end and nothing to refuse it.
        if discard_on_cap and (counter.reached is not None or fault is not None):
            cause = (
                f"the cap stopped its {counter.reached} request"
                if counter.reached is not None
                else f"it ended on {fault}"
            )
            ledger.append(
                {
                    "event": "discarded",
                    "reason": f"invocation {invocation} ({kind}) wrote the panel and {cause}",
                    "at": clock().isoformat(),
                }
            )
        ledger.append(
            {
                "event": "end",
                "id": invocation,
                "exit_code": wrapped,
                "fault": fault,
                "requests": dict(sorted(counter.items())),
                "count": counter.sent,
                "reached": counter.reached,
                "seconds": seconds,
                "at": clock().isoformat(),
            }
        )
    return CappedRun(
        invocation=invocation,
        exit_code=CAP_EXIT if counter.reached is not None else int(wrapped or 0),
        wrapped_exit_code=wrapped,
        sent=dict(sorted(counter.items())),
        reached=counter.reached,
        seconds=seconds,
    )
