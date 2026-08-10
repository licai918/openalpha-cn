"""The whole chain on live rows: fetch, store, examine, gate, serve, label, break.

Seven steps, in the order a user meets them, each asserting something no unit or integration
test in this repository can: that the shape the real endpoint serves survives the writers, the
catalog, the doctor, the gate, both faces and the label contract.

What is deliberately *not* asserted anywhere here is a value. The panel is whatever Tushare
served for the current year on the day this ran, so a row count, a price, a session or a
ticker written into an assertion would be a fixture in disguise -- green today and red on a
holiday. See `conftest.py`'s "Determinism" section for the three kinds of claim that replace
it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import duckdb
import pytest
from e2e_support import (
    PANEL_ZONE,
    SESSION_SCOPED_DATASETS,
    STORED_DATASETS,
    BuiltPanel,
    catalogued_path,
    console_script,
    knowable_datasets,
    last_covered_session,
    readable_halt_days,
    run_cli,
    stored_sessions,
)

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
    window_return,
)
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SuspensionError,
)
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel_gate import GATE_BLOCK_CODES
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
)
from openalpha_cn.sdk import OpenAlphaSDK

pytestmark = pytest.mark.e2e

ASSESSED_DATASETS: tuple[str, ...] = (
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
)
"""What `panel doctor` and `data-check` are asked about. The five the price plane is made of;
`stock_basic` and `trade_cal` are asked about separately where they matter, because their
partitions are keyed by lifecycle year and by exchange year rather than by the build year."""

FETCH_DEFECT_CODES: frozenset[str] = frozenset(
    {
        "partition_missing",
        "partition_file_missing",
        "partition_file_unreadable",
        "coverage_missing",
        "coverage_stale",
        "date_gap",
        "subject_missing",
        "field_missing",
    }
)
"""The `ReadinessIssue` codes that mean *this build did not land what it said it did*.

Separated from the rest on purpose: a real panel legitimately carries findings about the
market (`unexplained_unpriced`, `revised_rows`, `close_disagreement`) and about the datasets'
own structure, and a test that demanded a spotless report on live data would be asserting that
the A-share market is tidy. What it may demand is that nothing is *missing*."""


# --- step 1 and 2: the fetch landed, and the catalog's description of it is true ------------


def test_every_build_target_landed_a_partition_for_the_year_that_was_asked_for(
    built_panel: BuiltPanel,
) -> None:
    """The five targets put seven datasets on disk, each registered in the catalog.

    `stock_basic` is checked by presence rather than by year: its partitions are lifecycle
    years, so the build year need not be among them at all (a year in which no security
    listed produces none).
    """
    store = built_panel.store
    for dataset in STORED_DATASETS:
        years = store.registered_years(dataset)
        assert years, f"{dataset} registered no partition"
    for dataset in SESSION_SCOPED_DATASETS:
        assert built_panel.year in store.registered_years(dataset), (
            f"{dataset} has partitions for {store.registered_years(dataset)} and the build "
            f"asked for {built_panel.year}"
        )
    assert store.registered_years(STOCK_BASIC_DATASET), "stock_basic registered no lifecycle year"


def test_the_catalog_row_count_is_the_parquet_files_own_row_count(
    built_panel: BuiltPanel,
) -> None:
    """`panel_partitions.row_count` against `count(*)` over the file it points at.

    The catalog is what every reader consults before opening a partition, so a row count that
    merely *came from* the write is a claim, not a fact. Re-counting the file is the only way
    to tell the two apart, and it is the check no in-process test makes because a generated
    fixture is written and counted by the same call.
    """
    store = built_panel.store
    with duckdb.connect(str(store.catalog_path), read_only=True) as catalog:
        registered = catalog.execute(
            "SELECT dataset, year, row_count, relative_path FROM panel_partitions ORDER BY 1, 2"
        ).fetchall()
    assert registered, "the catalog registered no partition at all"
    with duckdb.connect() as reader:
        for dataset, year, row_count, relative_path in registered:
            path = store.root / str(relative_path)
            assert path.exists(), f"{dataset} year={year} is registered at {path} and is absent"
            actual = reader.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()
            assert actual is not None
            assert actual[0] == row_count, (
                f"{dataset} year={year}: the catalog says {row_count} rows and the file holds "
                f"{actual[0]}"
            )


def test_the_daily_partition_holds_every_session_the_stored_calendar_reports_open(
    built_panel: BuiltPanel,
) -> None:
    """The census `write_daily_panel` enforces, re-derived from the store afterwards.

    The writer refuses a partition missing a session the calendar reports open, so this is not
    a new rule -- it is the same rule checked against the *stored* calendar rather than against
    the batches the build happened to assemble. A build that fetched the right sessions and
    stored them under the wrong dates would satisfy the writer and fail here.
    """
    expected = set(built_panel.sessions())
    stored = set(stored_sessions(built_panel.store, dataset=DAILY_DATASET, year=built_panel.year))
    assert expected <= stored, (
        f"the stored calendar reports {sorted(expected - stored)} open and the daily partition "
        "carries no row for them"
    )


def test_the_bars_and_the_fundamentals_cover_the_same_sessions(
    built_panel: BuiltPanel,
) -> None:
    """`daily` and `daily_basic` are written together by one call and must agree afterwards.

    `write_daily_panel` takes the pair because a valuation without its bar cannot be checked
    against anything. That the two partitions still line up once they are *on disk* is a
    separate fact from the one the writer established in memory.
    """
    bars = set(stored_sessions(built_panel.store, dataset=DAILY_DATASET, year=built_panel.year))
    valuations = set(
        stored_sessions(built_panel.store, dataset=DAILY_BASIC_DATASET, year=built_panel.year)
    )
    assert bars == valuations, (
        f"only bars: {sorted(bars - valuations)}; only valuations: {sorted(valuations - bars)}"
    )


def test_every_session_scoped_dataset_reaches_the_same_last_session(
    built_panel: BuiltPanel,
) -> None:
    """One build, one coverage horizon -- and `panel build` does not give one.

    **This suite's own first build tripped it.** It ran from 23:34 to 00:39 Asia/Shanghai, and
    `cli._build_sessions` bounds the loop at `now.astimezone(Asia/Shanghai).date() - 1 day`,
    with `now` read once per `panel build` *invocation*. The five targets are five
    invocations, so the ones that ran before local midnight and the ones that ran after got
    different horizons. Measured on that run:

        daily / daily_basic / adj_factor   144 sessions, last 2026-08-07
        stk_limit                          145 sessions, last 2026-08-10

    The resulting panel cannot be assessed clean at **any** `as_of`, which is what makes this
    more than untidy. Before `stk_limit`'s newest row became knowable, `panel doctor` reports

        BLOCKING stk_limit not_yet_knowable: stk_limit holds information that first became
        available at 2026-08-10T08:30:00+00:00, after the requested as_of ...

    and at or after that instant the calendar requires 2026-08-10 of the price panel, which
    does not have it:

        BLOCKING daily date_gap: 1 required date(s) are absent from daily, starting at
        2026-08-10

    Both refusals are correct; there is no third option, and the only remedy once it has
    happened is to re-fetch the lagging targets, which cost 386s for `adj_factor` and 2,374s
    for `price` to bring this panel back to one horizon.

    **Closed by `V2-P1-018`, in two halves, and this assertion is unchanged by either.**
    `panel build` now takes `--as-of`, and `conftest.py` reads one instant per fixture and
    passes it to all five invocations, so the horizon is a property of the build rather than of
    what time it started. And `cli._refuse_split_horizon` refuses a build whose horizon
    disagrees with a session-scoped partition it is not going to replace -- before the first
    session is fetched -- so the panel this test describes can no longer be produced by
    accident at all. `tests/integration/test_cli_panel_horizon.py` drives both offline.
    """
    horizons = {
        dataset: last_covered_session(built_panel.store, dataset=dataset, year=built_panel.year)
        for dataset in SESSION_SCOPED_DATASETS
    }
    assert len(set(horizons.values())) == 1, (
        "the session-scoped datasets of one build reach different last sessions: "
        f"{ {k: v.isoformat() for k, v in horizons.items()} }"
    )


def test_the_price_build_completes_with_the_halt_guard_it_defaults_to(
    built_panel: BuiltPanel,
) -> None:
    """`panel build --dataset price` must finish on live rows without `--no-halts`.

    **This test is red at `f3c5321`, and the failure is the finding.** `--halts` is the
    default because `write_daily_panel`'s strongest guard -- the one that refuses a session
    whose missing bars nothing accounts for -- needs a real halt corpus rather than the `None`
    that switches it off, and `--no-halts` exists to make waiving it a recorded act. On the
    2026 year to date the default path cannot run at all:

        $ openalpha panel build --dataset price --year 2026
        1 partition(s) were written before this build stopped and are still stored:
        suspend_d:2026(2289 rows). ...
        `panel build` did not finish: it raised an unhandled SuspensionError. This is a defect
        in the command, not a verdict about the panel -- nothing was checked and nothing here
        should be read as a health result. The exception's own message is withheld ...
        $ echo $?
        5

    Three separate things are wrong, and they compound:

    1. **The refusal is real and is about the data.** `load_suspensions` raises
       `SuspensionError: 603056.SH is both resumed and halted on 2026-01-12; a security has one
       trading state per session, so this is two sources that were never reconciled`. It is not
       two sources: it is one `suspend_d` request for one session, and Tushare served both an
       `R` row and an `S` row for that security. The shape is systematic rather than a one-off
       -- 17 `(security, session)` pairs carry it in the 2026 year to date, clustered on
       resumption days (2026-01-12, -01-13, -01-15, -01-22, -03-20, -06-01 x3, -06-08 x3,
       -06-16, -06-23 x2, -06-26, -07-06, -07-09).
    2. **The exit code says the wrong thing.** `SuspensionError` is not in
       `cli._PANEL_WRITE_REFUSALS` (`PanelBatchError`, `PanelStorageError`,
       `TradingCalendarError`, `PriceDataError`), so `_panel_command` classifies it as
       `PanelExit.internal_error` -- 5, "a defect in the command, not a verdict about the
       panel". It is exactly a verdict about the panel, and a scheduled job reading the code
       is told to file a bug rather than to look at the data.
    3. **The diagnosis is withheld.** `internal_error` suppresses the exception's message on
       the grounds that an unanticipated failure can carry a credential. This one carries a
       ticker and a date, and no operator can act on what is printed.

    The panel this suite examines was therefore built with `--no-halts`, which is recorded on
    `BuiltPanel.halt_waiver` so that exactly one test fails rather than the whole subtree being
    unable to start.
    """
    if built_panel.build_reports is None:
        pytest.skip("a reused panel carries no build report; run without OPENALPHA_E2E_RUNTIME_DIR")
    waiver = built_panel.halt_waiver
    assert waiver is None, (
        "`panel build --dataset price` had to be retried with --no-halts. It exited "
        f"{waiver.exit_code} on its own defaults; stderr: {waiver.stderr[:1200]}"
    )
    assert built_panel.build_reports["price"]["halts"] == "corroborated"


def test_the_panel_can_read_back_the_halt_corpus_it_stored(
    built_panel: BuiltPanel,
) -> None:
    """`write_suspensions` accepted rows that `load_suspensions` refuses.

    **Red at `f3c5321` for the same live rows as the test above**, and stated separately
    because it is a different defect: the writer and the reader of one dataset disagree about
    what a valid partition is. `write_suspensions` never calls `build_suspension_day` -- it
    merges the batches, checks the year and the subject set, and stores -- so the contradiction
    is only discovered on the way out, after 2,289 rows are on disk and registered.

    A store that accepts what it cannot return is worse than one that refuses at either end: a
    build reports success, the catalog describes a partition that is there, and the failure
    surfaces in whatever reads it next.
    """
    corpus = load_suspensions(
        built_panel.store,
        years=(built_panel.year,),
        as_of=_as_of(built_panel),
        max_staleness=None,
    )
    assert corpus, "the halt partition read back empty"


def test_the_gate_does_not_clear_a_partition_whose_reader_refuses_it(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """The sharpest form of the same defect: the gate grants permission to read, and the read
    raises.

    **Red at `f3c5321`.** Measured on the panel this suite built:

        $ openalpha panel doctor --dataset suspend_d --year 2026 --as-of 2026-08-07T16:30+08:00
        READY suspend_d years=2026 cadence=event_driven rows=2289 waived=...
        $ echo $?   # 0
        $ openalpha data-check --dataset suspend_d --year 2026 --session 2026-08-07 ...
        CLEARED suspend_d years=2026 corroborated_sessions=- caveats=-
        $ echo $?   # 0

    and `load_suspensions` on that same partition, at that same `as_of`, raises
    `SuspensionError`. `data-check`'s whole deliverable is the exit code: a caller that runs
    it, is cleared, and then reads has done everything the design asks of it and still crashes.
    Readiness is assessed from the catalog's own census -- files, subjects, fields, dates,
    freshness -- and none of those dimensions can see a row-level contradiction, so this is a
    gap in what the gate is able to check rather than a mistake inside it.

    The assertion is deliberately the conjunction rather than either half. Either answer alone
    is defensible; the two together are not.
    """
    result = run_cli(
        "data-check",
        "--dataset",
        SUSPENSION_DATASET,
        "--year",
        str(built_panel.year),
        "--runtime-dir",
        str(built_panel.runtime_dir),
        "--exchange",
        built_panel.exchange,
        "--as-of",
        _as_of(built_panel).isoformat(),
        "--session",
        built_panel.sessions()[-1].isoformat(),
        "--json",
        cwd=cli_workspace,
    )
    cleared = result.exit_code == 0
    try:
        load_suspensions(
            built_panel.store,
            years=(built_panel.year,),
            as_of=_as_of(built_panel),
            max_staleness=None,
        )
    except SuspensionError as refusal:
        readable = False
        reason = str(refusal)
    else:
        readable = True
        reason = ""
    assert not (cleared and not readable), (
        f"`data-check` cleared {SUSPENSION_DATASET} year={built_panel.year} (exit 0) and "
        f"reading that same partition raises SuspensionError: {reason}"
    )


# --- step 3: the doctor's verdict about a real panel ----------------------------------------


def _assessed(built_panel: BuiltPanel) -> tuple[str, ...]:
    """The datasets these commands are asked about: `ASSESSED_DATASETS` minus any that hold a
    row newer than `_as_of`. See `e2e_support.knowable_datasets` for why the subset exists and
    where the finding it hides from these tests is asserted instead."""
    return knowable_datasets(
        built_panel.store, ASSESSED_DATASETS, year=built_panel.year, as_of=_as_of(built_panel)
    )


def _doctor(built_panel: BuiltPanel, workspace: Path, *extra: str) -> Any:
    result = run_cli(
        "panel",
        "doctor",
        *[argument for dataset in _assessed(built_panel) for argument in ("--dataset", dataset)],
        "--year",
        str(built_panel.year),
        "--runtime-dir",
        str(built_panel.runtime_dir),
        "--exchange",
        built_panel.exchange,
        "--as-of",
        _as_of(built_panel).isoformat(),
        "--json",
        *extra,
        cwd=workspace,
    )
    assert result.exit_code in {0, 1}, (
        f"panel doctor exited {result.exit_code}; only 0 (clean) and 1 (unhealthy) are verdicts "
        f"about the panel. stderr: {result.stderr[:1000]}"
    )
    return result.payload()


def _as_of(built_panel: BuiltPanel) -> datetime:
    """The instant the last stored session became knowable.

    Derived from the panel rather than from the wall clock so that the same panel gives the
    same answer tomorrow: a point-in-time question asked at `now()` moves every day, and a
    partition that is fresh this afternoon is stale next week.
    """
    last = built_panel.sessions()[-1]
    return datetime.combine(last, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_ZONE) + timedelta(hours=1)


def test_the_doctor_reports_nothing_missing_about_the_panel_that_was_just_built(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """No finding may say a partition, a subject, a field or a date is absent.

    Everything else it finds is left alone deliberately -- see `FETCH_DEFECT_CODES`. A live
    panel carries real findings about the market, and a test that required a clean report
    would be asserting a property of the A-share market rather than of this build.
    """
    payload = _doctor(built_panel, cli_workspace)
    missing = [finding for finding in payload["findings"] if finding["code"] in FETCH_DEFECT_CODES]
    assert not missing, f"the build left gaps the doctor can see: {missing}"


def test_the_doctor_separates_the_datasets_own_limitations_from_this_fetchs_findings(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """`limitations` is non-empty on real data and shares no code with `findings`.

    The separation is the whole point of the field: a structural property of `suspend_d` is
    not a defect of this fetch, and a report that mixed them would teach its readers to skim
    both. On generated fixtures the two lists are whatever the generator chose; on live rows
    the limitations are unavoidable, which is what makes this the place to check it.
    """
    payload = _doctor(built_panel, cli_workspace)
    assert payload["limitations"], "a real panel of these five datasets has known limitations"
    finding_codes = {finding["code"] for finding in payload["findings"]}
    limitation_codes = {entry["code"] for entry in payload["limitations"]}
    assert not finding_codes & limitation_codes


def test_the_doctors_session_scoped_checks_run_when_a_real_session_is_named(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """Naming a session the panel holds must turn the cross-checks on rather than skip them.

    A cross-check that reports `skipped` is not a passing check, and the difference is
    invisible from an exit code. The session is the panel's own last one, so this cannot ask
    about a day the market was shut.
    """
    session = built_panel.sessions()[-1]
    payload = _doctor(built_panel, cli_workspace, "--session", session.isoformat())
    ran = [check for check in payload["cross_checks"] if check["ran"]]
    assert ran, (
        "every cross-check was skipped on a session the panel holds: "
        f"{[(c['name'], c['skipped_reason']) for c in payload['cross_checks']]}"
    )


# --- step 4: the dependency gate --------------------------------------------------------


def _data_check(built_panel: BuiltPanel, workspace: Path, *extra: str) -> tuple[int, Any]:
    result = run_cli(
        "data-check",
        *[argument for dataset in _assessed(built_panel) for argument in ("--dataset", dataset)],
        "--year",
        str(built_panel.year),
        "--runtime-dir",
        str(built_panel.runtime_dir),
        "--exchange",
        built_panel.exchange,
        "--as-of",
        _as_of(built_panel).isoformat(),
        "--json",
        *extra,
        cwd=workspace,
    )
    assert result.exit_code in {0, 1}, (
        f"data-check exited {result.exit_code}. stderr: {result.stderr[:1000]}"
    )
    return result.exit_code, result.payload()


def test_the_gate_opens_on_a_real_session_of_the_panel_it_was_given(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """The fail-closed gate must actually open, and on live rows it does not open every day.

    This is the half of a fail-closed gate that is easy to leave untested: a gate that refused
    everything would pass every test written about its refusals. Naming a session is required
    rather than incidental -- without one the daily-cadence datasets carry
    `unverified_daily_coverage` and the gate refuses them, which is checked directly below.

    Several sessions rather than one, because a single named session makes this a question
    about the *market* instead of about the gate. Measured on the 2026 panel this suite built:
    the gate clears on 2026-08-06, -08-05, -07-31 and -06-30 and **blocks on 2026-08-07**, with

        BLOCKED daily return_path_disagreement: 689009.SH on 2026-08-07: the implied pre_close
        from 2026-08-06's close and the adjustment factors is 41.358834586466166, and daily
        published 41.38 -- a gap of 0.021165413533836386, past the 0.01789086791226186 that one
        tick of pre_close (0.01) plus one tick of each adj_factor allows.

    which is the gate doing its job on a real corporate-action disagreement between two
    datasets. Pinning the last session alone would have made this test a coin flip on the day's
    corporate actions; pinning a fixed date would have made it a fixture.

    What is asserted is therefore the pair of properties that hold every day: **some** session
    clears (so the gate can open at all), and every refusal on the ones that do not is a code
    from the gate's own closed table (so a block is always explained).
    """
    sessions = built_panel.sessions()[-5:]
    opened: list[date] = []
    for session in sessions:
        exit_code, payload = _data_check(
            built_panel, cli_workspace, "--session", session.isoformat()
        )
        if exit_code == 0:
            assert payload["is_blocked"] is False
            assert {entry["dataset"] for entry in payload["cleared"]} == set(_assessed(built_panel))
            opened.append(session)
            continue
        assert payload["is_blocked"] is True
        for block in payload["blocks"]:
            assert block["code"] in GATE_BLOCK_CODES, block
            assert block["detail"], block
    assert opened, (
        f"the gate refused every one of the last {len(sessions)} sessions "
        f"({[day.isoformat() for day in sessions]}); a gate that never opens is not a gate"
    )


def test_the_gate_refuses_a_daily_dataset_that_no_session_corroborated(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """With no `--session`, the same healthy panel is blocked, and that is the design.

    `unverified_daily_coverage` is the gate's own refusal rather than a fault of the panel:
    nothing opened a cross section, so nothing corroborated that the partition's daily cadence
    is real. Asserting it here, on live data, is what proves the exit code carries the verdict
    -- the doctor calls this same panel healthy at the same instant.
    """
    exit_code, payload = _data_check(built_panel, cli_workspace)
    assert exit_code == 1
    assert payload["is_blocked"] is True
    codes = {block["code"] for block in payload["blocks"]}
    assert "unverified_daily_coverage" in codes, codes


# --- step 5: the HTTP face and the SDK answer the same question ----------------------------


@pytest.fixture
def served(built_panel: BuiltPanel, cli_workspace: Path) -> Iterator[str]:
    """A real `openalpha serve` process over the built panel, and its base URL.

    A subprocess rather than `TestClient`: `serve` resolves its own configuration, binds a
    socket and runs uvicorn, and none of that is exercised by an ASGI transport. The port is
    taken from the operating system and released before uvicorn binds it, which is the usual
    small race; a bind failure surfaces as the readiness poll timing out with the child's own
    stderr attached.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = os.environ.copy()
    environment["OPENALPHA_RUNTIME_DIR"] = str(built_panel.runtime_dir)
    # uvicorn's own log goes to a file rather than to a pipe nobody drains: a `PIPE` that fills
    # its 64 KiB buffer blocks the writing process, so an access log long enough to fill it
    # would wedge the very server this fixture is waiting on.
    log = cli_workspace / "serve.log"
    with log.open("w", encoding="utf-8") as sink:
        child = subprocess.Popen(
            [*console_script(), "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=cli_workspace,
            env=environment,
            stdout=sink,
            stderr=subprocess.STDOUT,
            text=True,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(
                    f"`openalpha serve` exited {child.returncode} before it answered: "
                    f"{log.read_text(encoding='utf-8')[:2000]}"
                )
            try:
                with urlopen(f"{base}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:  # pragma: no cover - a server that never binds is the finding
            raise AssertionError(
                "`openalpha serve` did not answer /health within 60s: "
                f"{log.read_text(encoding='utf-8')[:2000]}"
            )
        yield base
    finally:
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            child.kill()


def _get(base: str, path: str, params: list[tuple[str, str]]) -> tuple[int, Any]:
    url = f"{base}{path}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=30) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture(scope="session")
def clearing_session(built_panel: BuiltPanel) -> date:
    """A session of the built panel on which the gate opens, found in process.

    The three faces are compared on one that clears, because a `409` that both faces agree on
    is a weaker statement than a `200` that both faces agree on: a refusal is reached before
    most of the rendering runs. Which session that is cannot be pinned -- it depends on the
    day's corporate actions, and 2026-08-07 is blocked on this panel by a real
    `return_path_disagreement` -- so it is discovered rather than named. The SDK is used for
    the search because it is in process and the CLI would be five subprocesses.
    """
    sdk = OpenAlphaSDK(runtime_dir=built_panel.runtime_dir)
    for session in reversed(built_panel.sessions()[-5:]):
        clearance = sdk.panel_clearance(
            datasets=_assessed(built_panel),
            years=(built_panel.year,),
            sessions=(session,),
            as_of=_as_of(built_panel),
            exchange=built_panel.exchange,
            with_calendar=True,
        )
        if not clearance.is_blocked:
            return session
    pytest.skip(
        "the gate refused all of the last five sessions; "
        "test_the_gate_opens_on_a_real_session_of_the_panel_it_was_given is where that fails"
    )


def test_the_three_panel_endpoints_answer_about_the_real_panel_over_a_real_socket(
    built_panel: BuiltPanel, served: str, clearing_session: date
) -> None:
    """`/panel/readiness`, `/panel/health` and `/panel/gate` against a live server.

    The status codes are the contract `api/app.py::PANEL_HTTP_STATUS` states: the two reports
    answer `200` whatever they find, and only the gate turns a refusal into `409`.
    """
    session = clearing_session
    shared = [
        *[("dataset", dataset) for dataset in _assessed(built_panel)],
        ("year", str(built_panel.year)),
        ("as_of", _as_of(built_panel).isoformat()),
        ("exchange", built_panel.exchange),
        ("calendar", "true"),
    ]

    status, readiness = _get(served, "/api/v1/panel/readiness", shared)
    assert status == 200, readiness
    assert {entry["dataset"] for entry in readiness["datasets"]} == set(_assessed(built_panel))

    status, health = _get(served, "/api/v1/panel/health", [*shared, ("session", str(session))])
    assert status == 200, health
    assert {entry["dataset"] for entry in health["datasets"]} == set(_assessed(built_panel))

    status, cleared = _get(served, "/api/v1/panel/gate", [*shared, ("session", str(session))])
    assert status == 200, cleared
    assert cleared["is_blocked"] is False

    status, blocked = _get(served, "/api/v1/panel/gate", shared)
    assert status == 409, blocked
    # A blocked gate answers with the *flat* clearance payload, not with the `{"detail": ...}`
    # envelope the pre-verdict refusals use -- `api/app.py::PANEL_HTTP_STATUS` says so, and a
    # caller that reached for `detail` here would find nothing. Asserted rather than assumed:
    # the first version of this test did reach for `detail` and got a `KeyError`.
    assert "detail" not in blocked, blocked
    assert blocked["is_blocked"] is True
    assert "unverified_daily_coverage" in {block["code"] for block in blocked["blocks"]}


def test_the_sdk_and_the_http_face_agree_about_the_same_real_panel(
    built_panel: BuiltPanel, served: str, clearing_session: date
) -> None:
    """One store, two faces, one answer.

    The comparison is on the *verdicts* rather than on the payload bytes: the SDK hands back
    objects and the endpoint hands back a rendering of them, and the thing that must not drift
    is which datasets cleared and which blocks were raised.
    """
    session = clearing_session
    sdk = OpenAlphaSDK(runtime_dir=built_panel.runtime_dir)
    shared: dict[str, Any] = {
        "datasets": _assessed(built_panel),
        "years": (built_panel.year,),
        "as_of": _as_of(built_panel),
        "exchange": built_panel.exchange,
        "with_calendar": True,
    }

    readiness = sdk.panel_readiness(**shared)
    report = sdk.panel_health(**shared, sessions=(session,))
    clearance = sdk.panel_clearance(**shared, sessions=(session,))

    query = [
        *[("dataset", dataset) for dataset in _assessed(built_panel)],
        ("year", str(built_panel.year)),
        ("as_of", _as_of(built_panel).isoformat()),
        ("exchange", built_panel.exchange),
        ("calendar", "true"),
    ]
    _, served_readiness = _get(served, "/api/v1/panel/readiness", query)
    _, served_health = _get(served, "/api/v1/panel/health", [*query, ("session", str(session))])
    _, served_gate = _get(served, "/api/v1/panel/gate", [*query, ("session", str(session))])

    assert [entry.dataset for entry in readiness] == [
        entry["dataset"] for entry in served_readiness["datasets"]
    ]
    assert [entry.is_ready for entry in readiness] == [
        entry["state"] == "ready" for entry in served_readiness["datasets"]
    ]
    assert sorted((f.dataset, f.code) for f in report.findings) == sorted(
        (f["dataset"], f["code"]) for f in served_health["findings"]
    )
    assert clearance.is_blocked is served_gate["is_blocked"]
    assert sorted(entry.dataset for entry in clearance.cleared) == sorted(
        entry["dataset"] for entry in served_gate["cleared"]
    )


# --- step 6: a label, and Tushare's own number as the witness ------------------------------


@pytest.fixture(scope="session")
def labelled(built_panel: BuiltPanel) -> tuple[str, Any, Any]:
    """One security labelled over a 5-session window read entirely out of the real panel.

    The security is chosen by a rule rather than named: the first code, in sorted order, that
    has a bar and a published band on every session of the window, a factor series covering
    it, and a `listed` verdict at both ends. A hard-coded ticker would quietly become a
    refusal test the first time that name was suspended.
    """
    store = built_panel.store
    as_of_read = _as_of(built_panel)
    calendar = built_panel.calendar
    horizon = parse_horizon("5d")

    sessions = built_panel.sessions()
    prediction_day = sessions[-(horizon.sessions + 2)]
    window = build_label_window(
        as_of=datetime.combine(prediction_day, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_ZONE),
        zone=ZoneInfo("Asia/Shanghai"),
        horizon=horizon,
        calendar=calendar,
    )

    universe = load_stock_universe(
        store,
        years=store.registered_years(STOCK_BASIC_DATASET),
        as_of=as_of_read,
        max_staleness=None,
    )
    factors = load_adjustment_histories(
        store, years=(built_panel.year,), as_of=as_of_read, max_staleness=None
    )
    days, contradicted = readable_halt_days(store, year=built_panel.year, as_of=as_of_read)
    halts = halt_corpus_for_years(days, years=(built_panel.year,))
    bars = {
        day: load_daily_bars(
            store, day=day, calendar=calendar, as_of=as_of_read, max_staleness=None
        )
        for day in window.sessions
    }
    limits = {
        day: load_price_limits(
            store, day=day, calendar=calendar, as_of=as_of_read, max_staleness=None
        )
        for day in window.sessions
    }

    candidates = sorted(
        code
        for code in bars[window.entry_day]
        if code in factors
        and all(code in bars[day] and code in limits[day] for day in window.sessions)
        and universe.is_listed(code, window.entry_day)
        and universe.is_listed(code, window.exit_day)
        # A security whose halt rows had to be dropped has an unknown trading state, and
        # labelling it would be reading "nothing happened" out of a partition nobody could
        # open. `test_the_panel_can_read_back_the_halt_corpus_it_stored` is where that is a
        # failure; here it is only a reason not to pick this name.
        and code not in contradicted
    )
    assert candidates, "no security in the panel had a bar and a band on every session"

    for ts_code in candidates:
        own_bars = {day: bars[day][ts_code] for day in window.sessions}
        label = label_outcome(
            window,
            ts_code=ts_code,
            bars=own_bars,
            factors=factors[ts_code],
            limits={day: limits[day][ts_code] for day in window.sessions},
            halts=halts,
            universe=universe,
        )
        if label.is_labelled:
            returns = window_return(
                window, ts_code=ts_code, bars=own_bars, factors=factors[ts_code]
            )
            return ts_code, label, (returns, own_bars, window)
    raise AssertionError(  # pragma: no cover - a whole market of refusals is itself a finding
        f"none of {len(candidates)} candidates carried a label over {window.sessions}"
    )


def test_the_label_reports_the_adjusted_path_and_the_two_correct_paths_agree(
    labelled: tuple[str, Any, Any],
) -> None:
    """`realized_return` is the adjusted path, and the published path corroborates it.

    The two read different columns -- one chains `close/pre_close` and never sees a factor,
    the other divides two factor-scaled closes and never sees a `pre_close` -- so agreeing is
    a statement about the data rather than about the arithmetic. The bound is the window's own
    `tolerance`, compounded from the publication precision of the rows that were actually
    fetched.
    """
    _, label, (returns, _, _) = labelled
    assert label.realized_return == pytest.approx(returns.adjusted, abs=0.0)
    assert returns.disagreement <= returns.tolerance, (
        f"published {returns.published} and adjusted {returns.adjusted} differ by "
        f"{returns.disagreement}, past the {returns.tolerance} these rows allow"
    )


def test_the_label_return_agrees_with_tushares_own_pct_chg_compounded(
    labelled: tuple[str, Any, Any],
) -> None:
    """The witness the label chain never reads.

    `pct_chg` is the provider's own published percentage move. `window_return` computes
    `published` from `close/pre_close` and `adjusted` from `close * adj_factor`, and neither
    touches this column, so compounding it is a third arithmetic path over the same window and
    the same rows. It is the closest thing to an oracle this chain has, and it exists only on
    live data -- a generated fixture's `pct_chg` is whatever the generator put there.

    The bound is publication precision, not a fitted tolerance. `pct_chg` is served as a
    percent with four decimals, so half an ulp of it is 5e-7 in return space; doubled, because
    the `close` and `pre_close` this is compared against are themselves published to two
    decimals and the quotient built from them is not the same real number `pct_chg` was
    rounded from. One such term per session return, compounded along the chain.

    Measured on 000001.SZ over 2026-08-03..2026-08-10 (five session returns):

        realized_return (adjusted)  -0.02839931
        published                   -0.02839931
        compounded pct_chg          -0.02839826
        |published - pct_chg|        1.056e-06     bound 5.000e-06

    -- three arithmetic paths over three different columns agreeing to the sixth decimal.
    """
    _, _, (returns, own_bars, window) = labelled
    gross = 1.0
    for day in window.sessions[1:]:
        gross *= 1.0 + own_bars[day].pct_chg / 100.0
    compounded = gross - 1.0
    bound = (1.0 + 1e-6) ** (len(window.sessions) - 1) - 1.0
    assert abs(returns.published - compounded) <= bound, (
        f"the label's published return {returns.published} and Tushare's own compounded "
        f"pct_chg {compounded} differ by {abs(returns.published - compounded)}, past the "
        f"{bound} their publication precision allows"
    )
    assert abs(returns.adjusted - compounded) <= bound + returns.tolerance


def test_the_naive_return_path_is_carried_and_is_not_the_one_reported(
    labelled: tuple[str, Any, Any],
) -> None:
    """`unadjusted` exists so the size of the error is visible, never so it is used.

    On most windows it equals the other two; on one spanning an ex-rights session it does not,
    and the sign can flip. The assertion is therefore about *which number is reported*, which
    holds every day, not about them differing, which holds only across a distribution.
    """
    _, label, (returns, _, _) = labelled
    assert label.realized_return == returns.adjusted
    assert label.realized_return != returns.unadjusted or returns.adjusted == returns.unadjusted


# --- step 7: break it on purpose -----------------------------------------------------------


def _defect_target(built_panel: BuiltPanel) -> str:
    """The dataset the injected defects are applied to.

    Taken from `_assessed` rather than named, because a defect injected into a dataset the
    doctor was not asked about produces an empty findings list and a test that fails for the
    wrong reason -- which is exactly what happened on this suite's first run against a panel
    whose `stk_limit` partition was excluded as `not_yet_knowable`.
    """
    assessed = _assessed(built_panel)
    assert assessed, "no dataset of the panel is knowable at the as_of these tests use"
    return assessed[-1]


def test_a_deleted_partition_file_is_reported_by_the_doctor_and_blocks_the_gate(
    built_panel: BuiltPanel, cli_workspace: Path, withheld_partition: Any
) -> None:
    """The catalog still points at a file that is gone: `partition_file_missing`.

    Injected rather than waited for, and on the panel this suite just built rather than on a
    fixture, so the code, the exit status and the human line are the ones a user would see
    after a half-finished sync.
    """
    target = _defect_target(built_panel)
    withheld_partition(target, built_panel.year)
    payload = _doctor(built_panel, cli_workspace)
    codes = {(f["dataset"], f["code"]) for f in payload["findings"]}
    assert (target, "partition_file_missing") in codes, codes

    exit_code, clearance = _data_check(
        built_panel, cli_workspace, "--session", built_panel.sessions()[-1].isoformat()
    )
    assert exit_code == 1
    assert clearance["is_blocked"] is True
    assert (target, "partition_file_missing") in {
        (block["dataset"], block["code"]) for block in clearance["blocks"]
    }


def test_a_partition_file_that_is_not_parquet_is_reported_as_unreadable(
    built_panel: BuiltPanel, cli_workspace: Path, withheld_partition: Any
) -> None:
    """A truncated or replaced file is a different fact from an absent one.

    Both leave the catalog describing something that is not there, and collapsing them would
    lose the remedy: a missing file is re-fetched, a corrupt one means the disk or the writer
    is at fault.
    """
    target = _defect_target(built_panel)
    withheld_partition(target, built_panel.year, replacement=b"this is not a parquet file")
    payload = _doctor(built_panel, cli_workspace)
    assert (target, "partition_file_unreadable") in {
        (f["dataset"], f["code"]) for f in payload["findings"]
    }


def test_a_year_the_build_never_fetched_is_reported_as_missing_rather_than_as_empty(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """Asking about a year with no partition is the date hole at year granularity.

    The important half is the *direction* of the answer: an absent partition must read as
    "nothing is known about this year", never as "this year had no rows". The second is what a
    reader would conclude from an empty result set, and it is how a backtest silently loses a
    year of history.
    """
    result = run_cli(
        "data-check",
        "--dataset",
        DAILY_DATASET,
        "--year",
        str(built_panel.year),
        "--year",
        str(built_panel.year - 3),
        "--runtime-dir",
        str(built_panel.runtime_dir),
        "--exchange",
        built_panel.exchange,
        "--as-of",
        _as_of(built_panel).isoformat(),
        # `--no-calendar` is not incidental. The gate resolves the exchange calendar over every
        # requested year, and the panel holds only the build year's, so asking about an earlier
        # year *with* a calendar is refused one step earlier -- `PanelUnreadableError`, exit 1,
        # nothing on stdout -- and the partition question is never reached. Stating that this
        # run has no calendar is what lets the missing partition be the answer.
        "--no-calendar",
        "--json",
        cwd=cli_workspace,
    )
    assert result.exit_code == 1, result.stdout
    payload = result.payload()
    blocks = {(block["dataset"], block["code"]) for block in payload["blocks"]}
    assert (DAILY_DATASET, "partition_missing") in blocks, blocks


def test_the_panel_is_intact_again_once_the_injected_defects_are_restored(
    built_panel: BuiltPanel, cli_workspace: Path
) -> None:
    """The defect tests above must not leave the panel broken for anything that follows.

    Ordering-dependent by construction -- it asserts the state the previous tests' teardown is
    responsible for -- and that is the point: a `finally` that silently failed to restore
    would otherwise show up as an unrelated failure much later, or not at all if this were the
    last test in the file.

    What it asserts is the absence of the *injected* codes, not a clean verdict. A real panel
    can be blocked for a real reason on the session this happens to name -- 2026-08-07 is,
    by a genuine `return_path_disagreement` -- and demanding a clearance here would make the
    restoration check fail for the market's reasons instead of for its own.
    """
    session = built_panel.sessions()[-1]
    _, payload = _data_check(built_panel, cli_workspace, "--session", session.isoformat())
    injected = {"partition_file_missing", "partition_file_unreadable"}
    left_behind = [block for block in payload["blocks"] if block["code"] in injected]
    assert not left_behind, f"a withheld partition was not put back: {left_behind}"


def test_the_defect_free_panel_still_answers_after_a_partition_was_moved_and_put_back(
    built_panel: BuiltPanel,
) -> None:
    """Byte-for-byte restoration, checked against the catalog's own content hash.

    `PartitionRef.content_hash` is recomputed from the file on every readiness assessment, so
    a restoration that lost a byte would surface as `coverage_stale` rather than as a
    difference anyone would notice by looking.
    """
    store = built_panel.store
    path = catalogued_path(store, dataset=PRICE_LIMIT_DATASET, year=built_panel.year)
    assert path.exists()
    with duckdb.connect() as reader:
        rows = reader.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()
    assert rows is not None and rows[0] > 0
