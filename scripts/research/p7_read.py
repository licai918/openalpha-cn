"""The command for the one read of a protected P7 window (`V2-P7-024`).

`register [--dry-run]` writes the reading's registration: the adopted stop rule (one number per
named constant) and `expected_periods`, the exact number of non-overlapping periods the plan
expects the window's calendar to hold. `--dry-run` computes them and writes nothing. `read` checks
the same count first -- reading the calendar, never outcome data, and claiming nothing -- and then
calls `p7_reading.read_once`, the only library path into the window.

Exit codes, the operator's signal:

* 0 -- the registration or the result was written (the verdict is printed as usual);
* 2 -- usage, or a `ReadingError` the registration cannot be written with;
* 3 -- refused before the claim (runtime directory, registration not committed, code changed,
  foreign package or script, already claimed, `second_use` before the validation window, a shallow
  repository or grafts, a preflight problem): nothing was spent;
* 4 -- the calendar's exact count is not the `expected_periods` the registration records: stop and
  report it. Nothing is written and nothing is claimed; the prior is never edited to match.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import p7_reading
import p7_windows
import registry
from p7_diagnostics import EXCHANGE

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TradingCalendarError
from openalpha_cn.panel.catalog import PanelStorageError

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2
EXIT_REFUSED: Final = 3
EXIT_PERIODS: Final = 4


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand and return its exit code; see the module docstring for what they mean."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "register":
            return _register(arguments)
        return _read(arguments)
    except p7_reading.ExpectedPeriodsMismatch as error:
        return _refuse(arguments.command, error, EXIT_PERIODS)
    except p7_reading.ReadingError as error:
        return _refuse(arguments.command, error, EXIT_USAGE)
    except (
        registry.HoldoutRefusedError,
        p7_windows.DiagnosticWindowError,
        TradingCalendarError,
        PanelStorageError,
    ) as error:
        return _refuse(arguments.command, error, EXIT_REFUSED)
    except ValueError as error:
        # A date, a book or a transform the arguments cannot parse; `ReadingError`,
        # `ExpectedPeriodsMismatch`, `DiagnosticWindowError` and `TradingCalendarError` are
        # ValueErrors too and are caught above, in that order.
        return _refuse(arguments.command, error, EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p7_read.py",
        description=(
            "The one read of a protected P7 window: register it (pre-registration, stop rule and "
            "expected period count), then read it once."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser(
        "register", help="write the reading's registration, or with --dry-run only print it"
    )
    register.add_argument("--repo", type=Path, required=True)
    register.add_argument("--runtime-dir", type=Path, required=True)
    register.add_argument("--window", required=True, help="'validation' or 'second_use'")
    register.add_argument("--reading", default=None, help="defaults to v2-p7-024-<window>")
    register.add_argument("--issue", default="V2-P7-024")
    register.add_argument("--as-of", required=True, help="the knowledge clock, an ISO instant")
    register.add_argument("--as-of-reason", required=True, help="one line: why that as_of")
    register.add_argument(
        "--expected-periods",
        type=int,
        default=None,
        help="primary only: the exact count the plan expects the calendar to make",
    )
    register.add_argument("--stride", type=int, required=True)
    register.add_argument("--horizons", type=int, nargs="+", required=True)
    register.add_argument("--tails", type=int, nargs="+", default=[1, 2, 3])
    register.add_argument("--score", required=True)
    register.add_argument("--book", default='{"seed": 0}', help="the survivor book as JSON")
    register.add_argument("--transform", required=True)
    register.add_argument("--min-coverage", type=float, default=0.5)
    register.add_argument("--max-carry-sessions", type=int, default=250)
    register.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    read = commands.add_parser("read", help="check the expected count, then read the window once")
    read.add_argument("--repo", type=Path, required=True)
    read.add_argument("--runtime-dir", type=Path, required=True)
    read.add_argument("--registration", type=Path, required=True)
    return parser


def _refuse(command: str, error: Exception, code: int) -> int:
    print(f"{command} refused: {error}", file=sys.stderr)
    return code


def _window_named(name: object) -> p7_windows.DiagnosticWindow:
    window = p7_windows.WINDOWS.get(name) if isinstance(name, str) else None
    if window is None:
        raise p7_reading.ReadingError(
            f"the window {name!r} is not one a reading opens; it opens 'validation' or 'second_use'"
        )
    return window


def _register(arguments: argparse.Namespace) -> int:
    if arguments.window == p7_windows.RESEARCH.name:
        raise p7_reading.ReadingError(
            "the research window is not read once: it is read through p7_diagnostics explore"
        )
    window = _window_named(arguments.window)
    role = "primary" if window.name == p7_windows.VALIDATION.name else "secondary"
    book_form: Any = json.loads(arguments.book)
    book = SurvivorBookConfig.model_validate(book_form)
    request = {
        "first": window.first.isoformat(),
        "last": window.last.isoformat(),
        "stride": arguments.stride,
        "horizons": list(arguments.horizons),
        "scores": [arguments.score],
        "tails": list(arguments.tails),
        "loser_decile_count": book.loser_decile_count,
    }
    config = {
        "book": book_form,
        "transform": arguments.transform,
        "exchange": EXCHANGE,
        "min_coverage": arguments.min_coverage,
        "max_carry_sessions": arguments.max_carry_sessions,
        "as_of": arguments.as_of,
    }
    expected = arguments.expected_periods
    stop_rule: p7_reading.StopRule | None
    if role == "primary":
        if expected is None:
            raise p7_reading.ReadingError(
                "a primary reading records --expected-periods, the exact number of non-overlapping "
                "periods the plan expects the window's calendar to hold"
            )
        stop_rule = p7_reading.adopted_stop_rule(score=arguments.score, expected_periods=expected)
    else:
        stop_rule = None
    if stop_rule is not None:
        most = p7_reading.counted_periods(
            arguments.runtime_dir,
            window=window,
            request=request,
            config=config,
            horizon=stop_rule.horizon,
        )
        problem = p7_reading.expected_periods_problem(
            expected, most, window=window, horizon=stop_rule.horizon
        )
        free = p7_reading._most_periods(
            window.first, window.last, stride=arguments.stride, horizon=stop_rule.horizon
        )
        print(f"window {window.name} {window.first}..{window.last}")
        print(f"the panel's calendar makes exactly {most} non-overlapping periods")
        print(f"the data-free bound is {free} periods")
        print(f"the registration expects {expected}")
        # The dry run writes nothing, so it stops here; a real registration is held to the same
        # count by `register_reading`, which refuses before writing (an `ExpectedPeriodsMismatch`).
        if arguments.dry_run and problem is not None:
            raise p7_reading.ExpectedPeriodsMismatch(problem)
    reading = arguments.reading
    if reading is None:
        reading = f"v2-p7-024-{window.name.replace('_', '-')}"
    path = arguments.repo / p7_reading.READING_DIRECTORY / f"{reading}.registration.json"
    if arguments.dry_run:
        print(f"the registration that would be written is {path}")
        print("dry run: nothing was written and nothing was claimed")
        return EXIT_OK
    resolved = registry._git(arguments.repo, "rev-parse", "HEAD")
    if resolved.returncode != 0:
        said = resolved.stderr.decode(errors="replace").strip()
        raise p7_reading.ReadingError(f"{arguments.repo} is not a git repository: {said}")
    digest = p7_reading.register_reading(
        path=path,
        reading=reading,
        issue=arguments.issue,
        role=role,  # type: ignore[arg-type]
        window=window.name,
        request=request,
        config=config,
        stop_rule=stop_rule,
        expected_periods=expected,
        code_commit=resolved.stdout.decode().strip(),
        as_of_reason=arguments.as_of_reason,
        runtime_dir=arguments.runtime_dir,
    )
    print(f"the registration is {path}")
    print(f"its sha256 is {digest}")
    print("commit it before the read; the read refuses bytes not in HEAD")
    return EXIT_OK


def _read(arguments: argparse.Namespace) -> int:
    body: Any = json.loads(arguments.registration.read_text(encoding="utf-8"))
    if not isinstance(body, Mapping):
        raise p7_reading.ReadingError(f"{arguments.registration} is not a JSON object")
    window = _window_named(body.get("window"))
    rule = body.get("stop_rule")
    if (
        isinstance(rule, Mapping)
        and type(rule.get("horizon")) is int
        and isinstance(body.get("request"), Mapping)
        and isinstance(body.get("config"), Mapping)
    ):
        most = p7_reading.counted_periods(
            arguments.runtime_dir,
            window=window,
            request=body["request"],
            config=body["config"],
            horizon=rule["horizon"],
        )
        problem = p7_reading.expected_periods_problem(
            body.get("expected_periods"), most, window=window, horizon=rule["horizon"]
        )
        if problem is not None:
            raise p7_reading.ExpectedPeriodsMismatch(problem)
        print(
            f"window {window.name}: the panel's calendar makes exactly {most} non-overlapping "
            f"periods, and the registration expects {body.get('expected_periods')}"
        )
    result = p7_reading.read_once(
        arguments.registration, repo=arguments.repo, runtime_dir=arguments.runtime_dir
    )
    print("\n".join(p7_reading.reading_lines(result, body)))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
