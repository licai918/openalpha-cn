"""The one place in this repository where a test is allowed to reach the network.

This module carries the constants, the `openalpha` subprocess runner and the store
readers; `conftest.py` beside it carries the fixtures that use them. The split is not
cosmetic: `tests/e2e/test_panel_chain_online.py` needs these helpers, and importing them
from `conftest` would resolve to whichever `conftest.py` pytest happened to import first
(there are three in this repository). `tests/offline_guard.py` exists for the same reason.

## Why this subtree exists at all

At `f3c5321` the suite held 1,966 unit, integration and contract tests and **not one of them
had ever fetched a row from Tushare**. `tests/integration/test_cli_panel.py` drives `panel
build` through `CliRunner` with a scripted `TushareTransport` injected at
`cli._panel_transport`; `tests/integration/panel/*` writes panels through
`tests/panel_fixtures.py`'s generator. Both are the right shape for what they test -- a
scripted transport makes a write-time guard reachable that live data may not produce for
months -- and both leave one question unasked: *does the chain hold when the rows come from
the real endpoint?* Every fixture in this repository was written by someone who already
believed they knew what the response looks like, and the twelve Criticals this project has
booked share one cause: a stated property that live evidence overturned.

So the tests under `tests/e2e/` fetch real rows with a real token, write them through the real
`panel_ingest` writers into a real `PanelStore`, and then run the doctor, the gate, the HTTP
app, the SDK and the `V2-P1-017` label contract over what landed. Nothing here injects a
transport, and nothing here reads `tests/panel_fixtures.py`.

## How that coexists with "the test suite does not touch the network"

Three independent switches, each of which alone is enough to keep a default run offline:

1. **The `e2e` marker is deselected by default.** `pyproject.toml`'s `addopts` carries
   `-m "not e2e"`, so `uv run pytest` -- the command CI runs -- collects these modules,
   deselects every test in them and executes none. A command-line `-m` overrides the one in
   `addopts` (pytest keeps the last `-m` it is given), which is what makes `uv run pytest
   -m e2e tests/e2e` the way in.
2. **`OPENALPHA_E2E=1` must be set.** `-m e2e` alone is not enough: without the variable every
   test here skips. A marker can be selected by accident (`-m ""`, a plugin, a future
   `addopts` edit); an environment variable that nothing in this repository sets cannot.
3. **A live socket guard runs over everything that is not marked `e2e`.**
   `tests/conftest.py::_refuse_outbound_connections` replaces `socket.socket.connect` for the
   duration of every unmarked test, so an offline suite is a *property of the run* rather
   than a claim about it. `tests/unit/test_offline_suite.py` proves the guard is live and
   proves this subtree is deselected by default.

Point 3 is the one that answers the question honestly. The first two are policy; only the
third is a measurement, and its limits are stated where it is defined.

## Determinism, when the input changes every session

The panel these tests read is not fixed: it is whatever Tushare served for the current year on
the day the suite ran, so no assertion here may name a price, a row count, a session or a
security. What is asserted instead is one of three kinds of thing:

- **An internal agreement.** The catalog's `row_count` against the Parquet file's own; the
  stored calendar's session listing against the sessions the `daily` partition holds; the
  three HTTP endpoints against the three SDK methods over one store.
- **An agreement with a number this repository never computed.** The label's return against
  Tushare's own `pct_chg`, compounded -- a column the label chain never reads.
- **A refusal produced on purpose.** A partition file deleted, truncated or replaced, each
  mapped to the one `ReadinessIssue` code the catalog documents for it.

Every date is derived from the panel that was built (the last session it holds, the calendar
it stored), never from `date.today()`.

## Cost

A full build is the year to date, whole market: `panel build` has no `--subject` and no way to
ask for a month (`cli._build_sessions` spans 1 January to the day before the fetch, because
`panel_ingest._session_census` refuses a partition that is missing a session the calendar
reports open). Measured on 2026-08-10 that is 144 sessions and 720 whole-market requests:
`trade_cal` 1.7s, `stock_basic` 9.5s, `adj_factor` 444s, `price` (three datasets in one pass)
~1000s, `stk_limit` ~330s -- around half an hour wall clock, dominated by the round trips.

That is why it is not in CI and why `OPENALPHA_E2E_RUNTIME_DIR` exists: point it at a panel a
previous run built and the whole chain runs against it in about a minute. When it is not set,
this fixture builds one and asserts the build report as it goes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SuspensionDay,
    SuspensionError,
    suspensions_from_panel_rows,
)
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_suspensions, load_trading_calendar
from openalpha_cn.panel_view import panel_store

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

E2E_SWITCH: str = "OPENALPHA_E2E"
"""The variable that has to be `1` for anything in this subtree to run. See point 2 above."""

RUNTIME_DIR_VARIABLE: str = "OPENALPHA_E2E_RUNTIME_DIR"
"""An already-built panel to reuse instead of spending half an hour building one."""

TOKEN_VARIABLE: str = "TUSHARE_TOKEN"
"""Read only to check that it is *present*. Its value is never read, logged, asserted on or
passed anywhere by this file: the child processes inherit `os.environ` and `TushareProvider`
resolves the variable itself, inside its own constructor."""

EXCHANGE: str = "SSE"

PANEL_ZONE: ZoneInfo = ZoneInfo("Asia/Shanghai")

BUILD_TARGETS: tuple[str, ...] = (
    "trade_cal",
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    "price",
    PRICE_LIMIT_DATASET,
)
"""`cli.PANEL_BUILD_TARGETS`' own order. `price` is three datasets in one pass."""

STORED_DATASETS: tuple[str, ...] = (
    "trade_cal",
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The seven datasets the five targets above put on disk."""

SESSION_SCOPED_DATASETS: tuple[str, ...] = (
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The datasets whose partition is keyed by the build year. `stock_basic` is excluded because
its partitions are lifecycle years (`cli._LIFECYCLE_YEAR_TARGETS`), and `suspend_d` because a
year with no halt event in it legitimately stores nothing for a given session."""

MINIMUM_SESSIONS: int = 12
"""How many sessions the build year must already have published for the chain to be worth
running: the label window needs `horizon.sessions` plus an entry, plus a session on either
side so the window sits strictly inside the stored range. A run in the first week of January
falls back to the previous year rather than building a panel too short to label."""


class E2EEnvironmentError(RuntimeError):
    """The run was asked for and cannot be given -- a missing token, a build that failed.

    Deliberately not a `pytest.skip`: the whole point of this subtree is that it is opt-in, so
    once someone has opted in, a silent skip is the failure mode to avoid.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class CLIResult:
    """One `openalpha` invocation, as the three things a caller can assert about it."""

    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    def payload(self) -> Any:
        """The `--json` document on stdout.

        Every panel command prints its structured output to stdout and everything else to
        stderr precisely so this is a parse and not a search; a command that mixed them would
        fail here rather than being worked around.
        """
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as error:  # pragma: no cover - a failure is the finding
            raise AssertionError(
                f"`openalpha {' '.join(self.args)}` exited {self.exit_code} and its stdout is "
                f"not JSON: {self.stdout[:400]!r}"
            ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltPanel:
    """A real panel on disk, and what is known about how it got there."""

    runtime_dir: Path
    year: int
    exchange: str
    build_reports: Mapping[str, Any] | None
    """One `panel build --json` payload per target, or `None` when an existing panel was
    reused. Nothing in the suite branches on it -- the tests re-derive every fact from the
    store so that both modes assert the same things -- but it is what the fixture itself
    checks while building."""

    halt_waiver: CLIResult | None = None
    """The refusal `panel build --dataset price` produced with its own default flags, on the
    run where the fixture had to fall back to `--no-halts` to get a panel at all.

    `None` means the default build succeeded, which is what is supposed to happen.
    `test_the_price_build_completes_with_the_halt_guard_it_defaults_to` is the assertion; this
    field exists so that one *test* carries the finding rather than the whole subtree being
    unable to start. See that test for the live evidence.
    """

    @property
    def store(self) -> PanelStore:
        return panel_store(self.runtime_dir)

    @property
    def calendar(self) -> TradingCalendar:
        return load_trading_calendar(
            self.store, exchange=self.exchange, years=(self.year,), as_of=_now()
        )

    def sessions(self) -> tuple[date, ...]:
        """Every session the *stored calendar* reports open between 1 January and this panel's
        last stored bar.

        The right endpoint is the panel's own last session rather than "yesterday", and the
        difference matters twice. It makes the sessions this suite reasons about a property of
        the artifact instead of a property of the clock, so a panel reused through
        `OPENALPHA_E2E_RUNTIME_DIR` days after it was built answers exactly as it did on the
        day -- with a wall-clock bound, every session published since the build would read as a
        hole in the partition and the whole suite would rot after one night. And it keeps the
        comparison honest: taking the *endpoint* from the partition and the *listing* from the
        calendar is what makes "does the partition have a hole in it" a real question, where
        taking both from the partition would answer itself.
        """
        last = stored_sessions(self.store, dataset=DAILY_DATASET, year=self.year)[-1]
        return self.calendar.trading_days_between(date(self.year, 1, 1), last)


def _now() -> datetime:
    return datetime.now(UTC)


def console_script() -> list[str]:
    """The command that starts the real `openalpha` process.

    The installed console script when there is one, because that is the entry point a user
    has: it runs `cli.main()`, which loads `.env`, configures logging and only then dispatches
    -- three behaviours an in-process `CliRunner` never exercises. The `-c` form is the
    fallback for an environment where the package is importable but its script is not on the
    expected path.
    """
    for name in ("openalpha", "openalpha.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.exists():
            return [str(candidate)]
    return [sys.executable, "-c", "from openalpha_cn.cli import main; main()"]


def run_cli(*args: str, cwd: Path, timeout: float = 3600.0) -> CLIResult:
    """Run one `openalpha` command in a child process and report all three of its outputs.

    A child process rather than `CliRunner`, for the reason this whole subtree exists: the
    exit code is the deliverable of `data-check` and of `panel doctor`, and an exit code is
    only observable from outside the process that raises it.

    `cwd` is a directory with no `.env` in it, on purpose. `cli.main()` merges `.env` from the
    process's working directory, so running from the repository root would make these tests
    pass on a developer's machine because of a file and fail in any environment that exports
    the same variables properly. The token reaches the child through the inherited environment
    and nowhere else.
    """
    completed = subprocess.run(
        [*console_script(), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=os.environ.copy(),
        timeout=timeout,
        check=False,
    )
    return CLIResult(
        args=args,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def require_opt_in() -> None:
    if os.environ.get(E2E_SWITCH) != "1":
        pytest.skip(
            f"{E2E_SWITCH}=1 is not set. These tests fetch real rows from Tushare; see this "
            "file's docstring for the three switches that keep a default run offline."
        )
    if not os.environ.get(TOKEN_VARIABLE):
        raise E2EEnvironmentError(
            f"{E2E_SWITCH}=1 was set and {TOKEN_VARIABLE} is empty. Export it (`set -a; "
            "source .env; set +a`) rather than letting the run skip: an opted-in run that "
            "quietly does nothing is the failure this subtree exists to remove."
        )


def build_year(workspace: Path, runtime_dir: Path) -> int:
    """Which year to build, and the `trade_cal` partition for it, already stored.

    The current year unless too little of it has published to carry a label window, in which
    case the previous one -- a run on 3 January would otherwise build a panel with four
    sessions in it and every window would fall off the end of the stored range.
    """
    for year in (_now().astimezone(PANEL_ZONE).year, _now().astimezone(PANEL_ZONE).year - 1):
        run_build(workspace, runtime_dir, target="trade_cal", year=year)
        calendar = load_trading_calendar(
            panel_store(runtime_dir), exchange=EXCHANGE, years=(year,), as_of=_now()
        )
        published = calendar.trading_days_between(
            date(year, 1, 1),
            min(date(year, 12, 31), _now().astimezone(PANEL_ZONE).date() - timedelta(days=1)),
        )
        if len(published) >= MINIMUM_SESSIONS:
            return year
    raise E2EEnvironmentError(  # pragma: no cover - two consecutive years cannot both be short
        f"neither the current year nor the previous one has published {MINIMUM_SESSIONS} sessions"
    )


def attempt_build(
    workspace: Path, runtime_dir: Path, *, target: str, year: int, extra: Sequence[str] = ()
) -> CLIResult:
    """Run one `panel build` and hand back the whole result, refusal included."""
    return run_cli(
        "panel",
        "build",
        "--dataset",
        target,
        "--year",
        str(year),
        "--runtime-dir",
        str(runtime_dir),
        "--exchange",
        EXCHANGE,
        "--json",
        *extra,
        cwd=workspace,
    )


def run_build(
    workspace: Path, runtime_dir: Path, *, target: str, year: int, extra: Sequence[str] = ()
) -> Any:
    """Run one `panel build` and refuse to continue unless it wrote something."""
    result = attempt_build(workspace, runtime_dir, target=target, year=year, extra=extra)
    if result.exit_code != 0:
        raise E2EEnvironmentError(
            f"`panel build --dataset {target} --year {year}` exited {result.exit_code}. "
            f"stderr: {result.stderr[:2000]}"
        )
    payload = result.payload()
    if not payload["partitions"]:
        raise E2EEnvironmentError(
            f"`panel build --dataset {target} --year {year}` exited 0 and wrote no partition; "
            "that is the empty success `cli._audit_written_partitions` exists to refuse"
        )
    return payload


def require_the_report_matches_what_landed(
    store: PanelStore, reports: Mapping[str, Any], *, year: int
) -> None:
    """The `price` build's own session report against the sessions its partition holds.

    Checked here rather than in a test because it is only answerable on the build path: a
    panel reached through `OPENALPHA_E2E_RUNTIME_DIR` has no report attached to it. What it
    catches is the one thing the store alone cannot say -- that the loop which fetched the
    sessions and the partition that stored them are describing the same set. `panel build`
    prints `sessions.first`/`sessions.last` from `cli._build_sessions`, i.e. from the
    *calendar*, while the partition's dates come from the rows the provider served.
    """
    report = reports["price"]
    stored = stored_sessions(store, dataset=DAILY_DATASET, year=year)
    if report["sessions"]["first"] != stored[0].isoformat():
        raise E2EEnvironmentError(
            f"`panel build --dataset price` reported its first session as "
            f"{report['sessions']['first']} and the partition's earliest bar is "
            f"{stored[0].isoformat()}"
        )
    if report["sessions"]["last"] != stored[-1].isoformat():
        raise E2EEnvironmentError(
            f"`panel build --dataset price` reported its last session as "
            f"{report['sessions']['last']} and the partition's latest bar is "
            f"{stored[-1].isoformat()}"
        )


def catalogued_path(store: PanelStore, *, dataset: str, year: int) -> Path:
    """Where the *catalog* says one partition's Parquet file is.

    Read out of `panel_partitions.relative_path` rather than rebuilt from the documented
    `<dataset>/<year>/data.parquet` layout, because the defect tests below exist to prove
    what the catalog does when the file it points at stops being what it says. Re-deriving
    the path would test the layout convention instead, and would keep passing if a write
    ever registered a row pointing somewhere else.
    """
    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT relative_path FROM panel_partitions WHERE dataset = ? AND year = ?",
            [dataset, year],
        ).fetchone()
    if row is None:
        raise AssertionError(f"the catalog has no partition for {dataset} year={year}")
    return store.root / str(row[0])


def stored_sessions(store: PanelStore, *, dataset: str, year: int) -> tuple[date, ...]:
    """Every event date the catalog's coverage census says that partition carries."""
    coverage = store.read_coverage(dataset, year)
    if coverage is None:
        raise AssertionError(f"{dataset} year={year} has no coverage record")
    return tuple(sorted(entry.event_date for entry in coverage.dates))


def readable_halt_days(
    store: PanelStore, *, year: int, as_of: datetime
) -> tuple[Mapping[date, SuspensionDay], frozenset[str]]:
    """The year's halt corpus, and the securities whose rows had to be dropped to get one.

    `load_suspensions` is the documented reader and is tried first; on a partition it can read,
    the second element is empty and this is exactly that call.

    When it raises `SuspensionError`, this drops every `(security, session)` whose rows
    disagree about the trading state and rebuilds from what is left. That is **not** a
    repair and nothing here treats it as one -- `label_outcome`'s five market inputs each have
    to be able to say what they do not cover, and dropping a row silently converts "this
    security's state is contradictory" into "nothing happened to it", which is the fail-open
    direction. So the dropped securities come back as the second element and the caller is
    expected to refuse to label them.

    It exists so that one test carries the finding (`test_the_panel_can_read_back_the_halt_
    corpus_it_stored`) instead of the label step, the REST step and the SDK step all erroring
    for the same root cause. On the 2026 year to date, 17 `(security, session)` pairs carry
    both an `R` and an `S` row -- one `suspend_d` request each, one upstream, no reconciliation
    anywhere -- and `build_suspension_day` refuses the lot.
    """
    try:
        return load_suspensions(store, years=(year,), as_of=as_of, max_staleness=None), frozenset()
    except SuspensionError:
        pass

    path = catalogued_path(store, dataset=SUSPENSION_DATASET, year=year)
    with duckdb.connect() as reader:
        rows = reader.execute(
            "SELECT subject, trade_date, suspend_type, suspend_timing "
            "FROM read_parquet(?) ORDER BY trade_date, subject",
            [str(path)],
        ).fetchall()
    states: dict[tuple[object, object], set[object]] = {}
    for subject, day, suspend_type, _timing in rows:
        states.setdefault((subject, day), set()).add(suspend_type)
    contradicted = frozenset(
        str(subject) for (subject, _day), kinds in states.items() if len(kinds) > 1
    )
    kept = [row for row in rows if str(row[0]) not in contradicted]
    return suspensions_from_panel_rows(kept), contradicted


def last_covered_session(store: PanelStore, *, dataset: str, year: int) -> date:
    """The newest event date the catalog's census records for that partition."""
    return stored_sessions(store, dataset=dataset, year=year)[-1]


def knowable_datasets(
    store: PanelStore, datasets: Sequence[str], *, year: int, as_of: datetime
) -> tuple[str, ...]:
    """Those of `datasets` whose partition holds nothing newer than `as_of`.

    The doctor's own `not_yet_knowable` rule, applied here so the gate and health tests ask
    about a coherent set. A dataset excluded by this is not being excused: it is excluded
    *because* `test_every_session_scoped_dataset_reaches_the_same_last_session` asserts it
    should not have been, and that test is where the finding lives. Without the split, one
    build that crossed local midnight would make five tests red for one cause and none of them
    would say what the cause was.
    """
    knowable: list[str] = []
    for dataset in datasets:
        coverage = store.read_coverage(dataset, year)
        if coverage is not None and coverage.max_available_time <= as_of:
            knowable.append(dataset)
    return tuple(knowable)
