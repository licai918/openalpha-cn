"""`scripts/forward_report.py`: the weekly forward-tracking report (`V2-P6-012`), fix round 2.

Fix round 2 rebuilds the report on the forward-evidence machinery `V2-P6-011` rounds 9-12 landed
after round 1 was written: which sessions the command actually rebalanced on is witnessed by the
append-only prediction store (`daily_selection.forward_rebalances`), a record filed after its
signal instant is admitted only when it recomputes from the stored builds at its own filing time
or an input it read was corrected since (`daily_selection.forward_record_check`), and what the
evidence means is `daily_selection.forward_summary`'s own reduction -- the headline book, the
same book's statistics excluding unverifiable periods, the unprovable holds, and the threat model.
This module is now a thin layer over that: it admits the registration (binding this file and
`scripts/daily_selection.py`), asks for the schedule and the check, prices the continuous book on
exactly the witnessed days, and renders `forward_summary` unmodified.

## Why the fixture changed from round 1

Round 1's fixture hand-built each day's `SignalDay` with chosen scores, bypassing the real
`reversal_1d/v1` factor panel entirely -- correct for round 1's own `run_strategy_backtest` call,
but incompatible with this round's `RecordCheck`, which **recomputes** a late-filed record's
scores from the stored factor builds (`strategy_view.score_day`) and refuses it if they no longer
match. A hand-built `SignalDay` has no factor panel behind it, so recomputation always finds "no
cross section" and every record is refused. This round's fixture therefore scores every day for
real, through `score_day` over `write_strategy_corpus`'s own `reversal_1d/v1` build -- exactly
`test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would`'s own path
(`tests/unit/scripts/test_daily_selection.py`), reused via `test_daily_selection._register_days`
rather than re-derived, with the input provenance it already writes.

## What is hand-verified, and what is reused

The book's own pricing (fills, fees, marks) is `V2-P6-007`'s, already proven correct by its own
suite; re-deriving it a second time here would not test this module. What this module adds is:
turning the store's witnessed schedule into a `prediction_ids` request on `rebalance_days`, and
`daily_selection.forward_summary`'s **compounding** of the resulting periods. So the test:

1. Prices the registered records both ways -- through `forward_report()` and directly through
   `backtest_strategy` of the *configuration itself* (no predictions) -- and asserts every period's
   fills, holdings and returns are identical, the same equivalence
   `test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would` proves.
2. Reads the resulting `net_return` off the real (already-equal) periods and independently
   recomputes `forward_summary`'s `compounded_net_return = prod(1 + net_return_i) - 1` in `Decimal`
   in this file's own assertion -- the one piece of arithmetic this module's reuse map adds beyond
   what `V2-P6-007`/`V2-P6-011` already prove.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final

import panel_fixtures
import pytest
import strategy_fixtures
from panel_fixtures import generate_panel
from strategy_fixtures import READ_AT, write_strategy_corpus, write_strategy_corpus_published_daily

from openalpha_cn import strategy_registration
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.panel_batch import PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.upstream_defects import UPSTREAM_DEFECT_DATA_COLUMNS
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import write_factor_panels
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    split_panel_batch_by_year,
    write_adjustment_factors,
    write_upstream_defects,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    input_provenance,
    signal_day_batch,
)
from openalpha_cn.strategy_view import backtest_strategy, score_day, strategy_request

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SCRIPTS: Final[Path] = ROOT / "scripts"
RESEARCH: Final[Path] = SCRIPTS / "research"


def _module(path: Path, name: str) -> ModuleType:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return importlib.import_module(name)


forward_report = _module(SCRIPTS, "forward_report")
daily = _module(SCRIPTS, "daily_selection")
grid = _module(RESEARCH, "grid")
registry = _module(RESEARCH, "registry")

# `test_daily_selection.py`'s own fixtures, reused rather than re-derived: sibling test modules
# import each other's helpers in this tree (`research_repo.py`'s own precedent).
import test_daily_selection as tds  # noqa: E402

RUNNING_PYTHON: Final[str] = f"{sys.version_info.major}.{sys.version_info.minor}"
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
    "scripts/daily_selection.py",
    "scripts/forward_report.py",
)
"""Every path this report's admission binds: `registry.REGISTERED_PATHS`' directories (one file
each), `scripts/daily_selection.py` and `scripts/forward_report.py` -- the fix-round point that
this report's own file must be bound exactly as the daily command's is."""
CLOCK0: Final[datetime] = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
CRITERIA: Final[dict[str, object]] = {"annualized_net_excess_above": "0"}


def _git(repo: Path, *args: str, at: datetime | None = None) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "forward-report-test",
            "GIT_AUTHOR_EMAIL": "forward-report-test@example.invalid",
            "GIT_COMMITTER_NAME": "forward-report-test",
            "GIT_COMMITTER_EMAIL": "forward-report-test@example.invalid",
        }
    )
    if at is not None:
        env["GIT_COMMITTER_DATE"] = at.isoformat()
        env["GIT_AUTHOR_DATE"] = at.isoformat()
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def _commit_file(repo: Path, path: Path, message: str, *, at: datetime) -> None:
    _git(repo, "add", path.relative_to(repo).as_posix())
    _git(repo, "commit", "-q", "-m", message, at=at)


def _head(repo_path: Path) -> str:
    return _git(repo_path, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repository with one file under each bound pathspec -- `scripts
    /forward_report.py` and `scripts/daily_selection.py` both included -- a real
    `.python-version` and `uv.lock` (`daily_selection.admit_environment`, reused rather than
    skipped), and the environment `installed_distributions` reports patched to match it exactly.
    """
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    _git(root, "init", "-q", "--template=")
    for relative in BOUND:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tds.LOCK if relative == "uv.lock" else "# fixture\n", encoding="utf-8")
    (root / ".python-version").write_text(f"{RUNNING_PYTHON}\n", encoding="utf-8")
    (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git(root, "add", ".python-version", ".gitignore")
    for relative in BOUND:
        _git(root, "add", relative)
    _git(root, "commit", "-q", "-m", "seed", at=CLOCK0)
    monkeypatch.setattr(
        registry, "_imported_package", lambda: root / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry,
        "_imported_scripts",
        lambda: (
            root / "scripts" / "research" / "grid.py",
            root / "scripts" / "research" / "registry.py",
        ),
    )
    monkeypatch.setattr(daily, "installed_distributions", lambda: dict(tds.LOCKED))
    return root


def register_config(
    repo_path: Path, config: dict[str, Any], *, settings: Mapping[str, object] | None = None
) -> Path:
    commit = _head(repo_path)
    path = repo_path / "docs" / "research" / "p6-registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    registry.register(
        config,
        CRITERIA,
        path,
        code_commit=commit,
        settings=grid.protocol_settings() if settings is None else settings,
    )
    _commit_file(repo_path, path, "register", at=CLOCK0 + timedelta(seconds=1))
    return path


def prediction_store(root: Path, *, clock_at: datetime) -> FilePredictionStore:
    return FilePredictionStore(root / "predictions", clock=lambda: clock_at)


def register_days(
    root: Path,
    request_for: Any,
    days: Any,
    anchor: date,
    *,
    registered: RegisteredConfiguration,
    store: PanelStore | None = None,
    provenance_store: PanelStore | None = None,
) -> list[str]:
    """`test_daily_selection._register_days`'s own body, parameterized on `registered`: that
    helper hardcodes its own module's placeholder `REGISTERED` constant (a fixed, unrelated
    `RegisteredConfiguration`), which cannot be reused here -- this report's records must be
    bound to the *real* admitted registration `forward_rebalances`/`forward_record_check` check
    them against. Every underlying primitive (`score_day`, `signal_day_batch`, `input_provenance`,
    `daily.write_provenance`, `daily.register_prediction`) is reused unchanged; only the constant
    `_register_days` closes over is made a parameter. `store` is the panel the days are scored
    on (default: `root`'s own) -- a store as it stood when they were filed, before the runtime's
    panel advanced past them. `provenance_store` is the one the input provenance is written from
    (default: `store`)."""
    store = PanelStore(root / "panel") if store is None else store
    provenance_store = store if provenance_store is None else provenance_store
    identifiers: list[str] = []
    for day in days:
        request = request_for(day)
        signal = score_day(store, request, day=day, anchor=anchor)
        calendar = daily._outcome_calendar(store, request.exchange, day, request.as_of)
        filed = tds._after_the_close(day, calendar.next_trading_day(day))
        batch = signal_day_batch(signal, request, registered, predicted_at=filed)
        assert batch is not None
        daily.write_provenance(
            root,
            input_provenance(
                provenance_store, request, registered, day=day, batch=batch, recorded_at=filed
            ),
        )
        record, outcome = daily.register_prediction(
            root, batch, calendar=calendar, clock=lambda filed=filed: filed
        )
        assert (outcome, record.standing) == ("created", "forward")
        identifiers.append(record.record_id)
    return identifiers


def write_journal_day(
    tmp_path: Path,
    admitted: Any,
    *,
    session: date,
    as_of: datetime,
    decision: str,
    reason: str,
    held: bool,
    record_id: str | None,
    weights: dict[str, str] | None = None,
) -> None:
    """One session's journal, the shape `journalled_days` reads (`daily_selection
    .DAILY_SELECTION_SCHEMA`), hand-built rather than run through the full command: the fields
    `journalled_days`/`Schedule` read are the session, `as_of`, the decision and its reason, the
    held flag and the record."""
    path = daily.journal_directory(tmp_path, admitted) / f"{session.isoformat()}.json"
    daily.write_journal(
        path,
        {
            "schema": daily.DAILY_SELECTION_SCHEMA,
            "result": {
                "session": session.isoformat(),
                "as_of": as_of.isoformat(),
                "targets": {
                    "decision": decision,
                    "reason": reason,
                    "weights": weights or {},
                    "previous_session": None,
                },
                "candidates": {"held": held, "ranked_count": 0, "candidates": []},
                "prediction": {"registered": record_id is not None, "record_id": record_id},
            },
        },
    )


def build_static_fixture(
    tmp_path: Path, repo_path: Path, *, settings: Mapping[str, object] | None = None
) -> tuple[Path, Any, tuple[date, ...], tuple[date, ...]]:
    """`STATIC` (`rebalance_every_sessions=3`) on `write_strategy_corpus`'s panel, registered
    through `test_daily_selection._register_days` -- real `score_day` scoring, filed at 18:30
    (`test_daily_selection._after_the_close`), with the input provenance the daily command writes
    -- at signal days 1, 4 and 7, each journalled as a rebalance.

    Returns `(panel_root, admitted_registration, sessions, signal_days)`.
    """
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.STATIC)
    anchor = sessions[1]
    config = dict(configured, start=anchor)
    registration_path = register_config(repo_path, config, settings=settings)
    admitted = daily.admit_registration(registration_path, repo_path)

    step = configured["rebalance_every_sessions"]
    signal_days = tuple(sessions[index] for index in range(1, len(sessions) - 1, step))

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._read_at(day)
        )

    identifiers = register_days(
        tmp_path, request_for, list(signal_days), anchor, registered=admitted.declared
    )
    for day, record_id in zip(signal_days, identifiers, strict=True):
        write_journal_day(
            tmp_path,
            admitted,
            session=day,
            as_of=tds._evening(day),
            decision="rebalanced",
            reason="scheduled",
            held=False,
            record_id=record_id,
        )
    return tmp_path, admitted, sessions, signal_days


# --- the book's end, at the exact boundary against its start ------------------------------------


def test_a_brand_new_registrations_first_record_with_nothing_yet_published_beyond_it_is_refused(
    tmp_path: Path, repo: Path
) -> None:
    """The book's end is `min(book_period_end(newest_record), newest_published_session)`. When
    the only witnessed rebalance is also the newest session the panel has published -- a report
    run the same evening a brand-new registration files its very first record, before any later
    session exists to hold a period open to -- that end equals `schedule.first_record` itself:
    there is no session after the first rebalance to trade a period on, and the report must
    refuse rather than ask `backtest_strategy` for a zero-width window. This is the exact
    boundary of `end <= schedule.first_record`, not only the case where `end` falls short of it."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.STATIC)
    anchor = sessions[-1]
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._read_at(day)
        )

    identifiers = register_days(
        tmp_path, request_for, [anchor], anchor, registered=admitted.declared
    )
    write_journal_day(
        tmp_path,
        admitted,
        session=anchor,
        as_of=tds._evening(anchor),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=identifiers[0],
    )

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="there is no session to trade"):
        forward_report.forward_report(
            store, store_predictions, tmp_path, registration=admitted.path, repo=repo, as_of=READ_AT
        )


# --- Step 1: the registered records price exactly as the configuration itself does ---------------


def test_the_continuous_book_prices_exactly_as_the_configuration_itself_does(
    tmp_path: Path, repo: Path
) -> None:
    panel_root, admitted, sessions, signal_days = build_static_fixture(tmp_path, repo)
    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=READ_AT)

    report = forward_report.forward_report(
        store, store_predictions, panel_root, registration=admitted.path, repo=repo, as_of=READ_AT
    )

    assert [session for session, _record in report.schedule.rebalances] == list(signal_days)
    assert report.schedule.first_record == signal_days[0]
    assert report.check.unverifiable == []
    assert report.check.verified == [record for _session, record in report.schedule.rebalances]

    configured = tds._base(**tds.STATIC)
    by_configuration = backtest_strategy(
        store,
        strategy_request(**configured, start=signal_days[0], end=sessions[-1], as_of=READ_AT),
    )
    assert not any(period.held for period in by_configuration.periods)
    assert any(period.fills for period in by_configuration.periods)
    assert tds._traded(report.backtest) == tds._traded(by_configuration)

    # The one piece of arithmetic this module adds beyond the already-proven book: compounding.
    net_returns = [period.net_return for period in report.backtest.periods]
    expected_compound = Decimal(1)
    for value in net_returns:
        expected_compound *= Decimal(1) + value
    expected_compound = (expected_compound - 1).quantize(Decimal("0.0000000001"))
    all_periods = report.summary["statistics"]["all_periods"]
    assert all_periods["periods"] == len(net_returns)
    assert Decimal(all_periods["compounded_net_return"]) == expected_compound

    assert report.summary[daily.UNVERIFIABLE]["count"] == 0
    assert report.summary["unprovable_holds"] == []
    assert report.summary["integrity"] == daily.INTEGRITY
    assert report.config_id == admitted.config_id


def test_forward_report_view_and_summary_lines_render_the_landed_summary_unmodified(
    tmp_path: Path, repo: Path
) -> None:
    panel_root, admitted, _sessions, _signal_days = build_static_fixture(tmp_path, repo)
    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=READ_AT)
    report = forward_report.forward_report(
        store, store_predictions, panel_root, registration=admitted.path, repo=repo, as_of=READ_AT
    )

    payload = forward_report.forward_report_view(report)
    assert payload["config_id"] == admitted.config_id
    assert payload["as_of"] == READ_AT.isoformat()
    for key in (daily.UNVERIFIABLE, "statistics", "unprovable_holds", "integrity"):
        assert payload[key] == report.summary[key]

    lines = forward_report.summary_lines(report)
    assert lines[0] == f"as_of              {READ_AT.isoformat()}"
    assert lines[1] == f"config_id          {admitted.config_id}"
    assert lines[2:] == daily.forward_summary_lines(report.summary)
    assert lines[-1] == daily.INTEGRITY


# --- Important 1 (review round 3): the holdout's own significance test ----------------------------

SETTINGS: Final[dict[str, object]] = {
    **grid.protocol_settings(),
    "bootstrap_samples": 4_321,
    "random_seed": 97,
}
"""Measurement settings no default carries, so a result read under them came from the
registration."""


def test_the_forward_summary_tests_both_statistic_sets_with_the_holdouts_own_function(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`grid.strategy_result` -- the holdout's sign-flip test of non-overlapping periods' net
    excess, one-sided p -- over the headline book and the sensitivity subset, against all-A
    equal weight (the registration's `excess_benchmark`), under the registration's own
    `bootstrap_samples`/`random_seed`, with 000905.SH reported beside it from the same result's
    `reported_*` keys (one call per set, one source; it tests nothing, as the protocol says).
    Only complete periods are tested, each one's session count shown; the short last one is
    shown and said to be left out."""
    panel_root, admitted, _sessions, _signal_days = build_static_fixture(
        tmp_path, repo, settings=SETTINGS
    )
    real = grid.strategy_result
    asked: list[tuple[str, int, int, int]] = []

    def recorded(backtest: Any, **keywords: Any) -> Any:
        asked.append(
            (
                keywords["excess_benchmark"],
                keywords["bootstrap_samples"],
                keywords["random_seed"],
                len(backtest.periods),
            )
        )
        return real(backtest, **keywords)

    monkeypatch.setattr(grid, "strategy_result", recorded)
    report = forward_report.forward_report(
        PanelStore(panel_root / "panel"),
        prediction_store(panel_root, clock_at=READ_AT),
        panel_root,
        registration=admitted.path,
        repo=repo,
        as_of=READ_AT,
    )
    monkeypatch.undo()

    periods = len(report.backtest.periods)
    assert periods == 3
    # Headline and sensitivity (nothing unverifiable, so the subset is every period): one call
    # each, against the registration's excess_benchmark.
    assert asked == [("equal_weight_all_a", 4_321, 97, periods)] * 2
    significance = report.summary["significance"]
    assert "complete" in significance["tested"] and "session count" in significance["tested"]
    for name in ("all_periods", "excluding_unverifiable"):
        shown = significance[name]["equal_weight_all_a"]
        expected = real(
            report.backtest,
            excess_benchmark="equal_weight_all_a",
            bootstrap_samples=4_321,
            random_seed=97,
        )
        for key in (
            "p_excess",
            "p_excess_one_sided",
            "mean_net_excess",
            "net_excess",
            "excluded_incomplete_periods",
        ):
            assert shown[key] == expected[key], (name, key)
        assert shown["excluded_incomplete_periods"] == 1
        assert shown["tested_periods"] == periods - 1
        assert shown["tested_period_sessions"] == [
            period.sessions for period in report.backtest.periods[:-1]
        ]
        assert (shown["bootstrap_samples"], shown["random_seed"]) == (4_321, 97)
        # 000905.SH from the same result's reported_* keys: the numbers a separate call against
        # it gives, so reading them is one source, not a second computation.
        reported = significance[name]["000905.SH"]
        separately = real(
            report.backtest, excess_benchmark="000905.SH", bootstrap_samples=4_321, random_seed=97
        )
        assert reported["reported"] is True
        assert reported["net_excess"] == separately["net_excess"]
        assert reported["mean_net_excess"] == separately["mean_net_excess"]
        assert "p_excess" not in reported
    json.dumps(forward_report.forward_report_view(report))
    # The sensitivity set is tested on its own periods: with the first record unverifiable, the
    # two periods after it, one of them complete.
    first = report.schedule.rebalances[0][1]
    sensitivity = daily.forward_summary(
        report.backtest,
        SimpleNamespace(unverifiable=[(first, ("factor_obs_reversal_1d_v1:2026",))]),
        report.schedule,
        settings=SETTINGS,
    )["significance"]["excluding_unverifiable"]["equal_weight_all_a"]
    assert (sensitivity["period_count"], sensitivity["tested_periods"]) == (2, 1)
    lines = daily.forward_summary_lines(report.summary)
    assert any("one-sided p" in line and "equal_weight_all_a" in line for line in lines)
    assert any("sessions [" in line for line in lines)
    assert any("000905.SH" in line and "tests nothing" in line for line in lines)
    assert any("complete" in line and "not tested" in line for line in lines)
    assert lines[-1] == daily.INTEGRITY


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        pytest.param({"random_seed": 1}, "bootstrap_samples", id="samples_missing"),
        pytest.param(
            {"bootstrap_samples": 100, "random_seed": True}, "random_seed", id="bool_seed"
        ),
        pytest.param(
            {"bootstrap_samples": False, "random_seed": 1}, "bootstrap_samples", id="bool"
        ),
    ],
)
def test_forward_summary_refuses_a_registration_without_its_measurement_settings(
    settings: dict[str, object], named: str
) -> None:
    """No default stands in for the registration's `bootstrap_samples`/`random_seed`, and a bool
    -- an int to Python -- is not one (`Registration.seed`'s rule, round 16)."""
    with pytest.raises(daily.StepFailedError, match=named):
        daily.forward_summary(
            SimpleNamespace(periods=()),
            SimpleNamespace(unverifiable=[]),
            daily.ForwardSchedule(
                rebalances=(), days=(), first_record=date(2026, 1, 5), unprovable_holds=()
            ),
            settings=settings,
        )


# --- Important 2 (review round 3): a book whose newest period crosses New Year --------------------


@pytest.mark.parametrize("newest", [date(2026, 12, 30), date(2026, 12, 31)], ids=str)
def test_a_forward_report_at_year_end_reads_the_next_years_calendar(
    tmp_path: Path, repo: Path, newest: date
) -> None:
    """On 2026-12-31, a record whose grid period ends in January needs 2027's calendar to place
    that end (`book_period_end`), and a record filed on the 31st needs it for its registration
    cutoff (09:15 on the next session). Both are loaded when stored, as `_outcome_calendar` does;
    a record on the 30th prices one period through the 31st, and one on the 31st -- the newest
    published session -- is refused as a book with no session to trade, never a raw calendar
    error."""
    strategy_fixtures.write_two_year_corpus(tmp_path)
    configured = tds._base(**tds.STATIC, benchmarks=("equal_weight_all_a",))
    admitted = daily.admit_registration(register_config(repo, dict(configured, start=newest)), repo)

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._evening(day)
        )

    identifiers = register_days(
        tmp_path, request_for, [newest], newest, registered=admitted.declared
    )
    write_journal_day(
        tmp_path,
        admitted,
        session=newest,
        as_of=tds._evening(newest),
        decision="rebalanced",
        reason="the first day: no book yet",
        held=False,
        record_id=identifiers[0],
    )
    as_of = tds._evening(date(2026, 12, 31), hours=4)

    def run() -> Any:
        return forward_report.forward_report(
            PanelStore(tmp_path / "panel"),
            prediction_store(tmp_path, clock_at=as_of),
            tmp_path,
            registration=admitted.path,
            repo=repo,
            as_of=as_of,
        )

    if newest == date(2026, 12, 31):
        with pytest.raises(forward_report.ForwardReportError, match="no session to trade"):
            run()
        return
    report = run()
    ((period,),) = [report.backtest.periods]
    assert (period.start, period.end) == (newest, date(2026, 12, 31))
    assert report.check.verified == identifiers


# --- production-shaped: a missed day, caught up ---------------------------------------------------


def test_a_missed_rebalance_is_caught_up_on_the_next_record_the_store_witnesses(
    tmp_path: Path, repo: Path
) -> None:
    """Signal day 1 rebalances (the book's first day); the scheduled rebalance at day 4 (three
    sessions later) is never registered -- the command missed it -- and day 5's record catches it
    up. Days 2, 3 and 4 carry no record and no journal at all: not a hold, simply not completed,
    exactly `witnessed_days`' "missed or refused" case."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.STATIC)
    anchor = sessions[1]
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._read_at(day)
        )

    filed_days = (sessions[1], sessions[5])
    identifiers = register_days(
        tmp_path, request_for, list(filed_days), anchor, registered=admitted.declared
    )
    for day, record_id in zip(filed_days, identifiers, strict=True):
        write_journal_day(
            tmp_path,
            admitted,
            session=day,
            as_of=tds._evening(day),
            decision="rebalanced",
            reason="scheduled" if day == anchor else "catching up the rebalance scheduled",
            held=False,
            record_id=record_id,
        )

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    report = forward_report.forward_report(
        store, store_predictions, tmp_path, registration=admitted.path, repo=repo, as_of=READ_AT
    )

    assert [session for session, _record in report.schedule.rebalances] == list(filed_days)
    assert report.schedule.first_record == sessions[1]
    assert len(report.backtest.periods) == 2
    assert [period.start for period in report.backtest.periods] == list(filed_days)
    # Day 5 caught up the rebalance scheduled on day 4; the grid's next is day 7, not day 5 plus
    # the interval (day 8), so the last period ends there (`V2-P6-012` review, minor 3).
    assert report.backtest.periods[-1].end == sessions[7]
    # A journalled book's periods are the days it rebalanced on: the tested one's length is
    # stated, not assumed to be the interval (round 16).
    shown = report.summary["significance"]["all_periods"]["equal_weight_all_a"]
    assert shown["tested_period_sessions"] == [4]
    assert shown["excluded_incomplete_periods"] == 1


# --- production-shaped: a held day ---------------------------------------------------------------


def test_a_held_day_carries_no_record_and_is_not_a_rebalance(tmp_path: Path, repo: Path) -> None:
    """Session 4 is forced to hold (the registered configuration, scored again, ranks nothing) --
    `strategy_registration.score_day` monkeypatched for that one day, `test_a_day_without_a_record_
    is_held_only_when_the_journal_and_the_configuration_agree`'s own technique. Held to a journal
    that agrees it held, `witnessed_days` -- and this report -- treats it as a completed, unranked
    day: no record, not a rebalance, and the next scheduled rebalance (session 7) still runs."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.STATIC)
    anchor = sessions[1]
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._read_at(day)
        )

    filed_days = (sessions[1], sessions[7])
    identifiers = register_days(
        tmp_path, request_for, list(filed_days), anchor, registered=admitted.declared
    )
    for day, record_id in zip(filed_days, identifiers, strict=True):
        write_journal_day(
            tmp_path,
            admitted,
            session=day,
            as_of=tds._evening(day),
            decision="rebalanced",
            reason="scheduled",
            held=False,
            record_id=record_id,
        )

    held_day = sessions[4]
    held_at = tds._evening(held_day)
    write_journal_day(
        tmp_path,
        admitted,
        session=held_day,
        as_of=held_at,
        decision="held",
        reason="the source held",
        held=True,
        record_id=None,
    )

    real_score_day = strategy_registration.score_day

    def holding(store: Any, request: Any, *, day: date, **keywords: Any) -> Any:
        signal = real_score_day(store, request, day=day, **keywords)
        if day == held_day:
            return dataclasses.replace(
                signal, scores=dataclasses.replace(signal.scores, ranked=None)
            )
        return signal

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(strategy_registration, "score_day", holding)
    try:
        store = PanelStore(tmp_path / "panel")
        store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
        report = forward_report.forward_report(
            store,
            store_predictions,
            tmp_path,
            registration=admitted.path,
            repo=repo,
            as_of=READ_AT,
        )
    finally:
        monkeypatch.undo()

    decisions = {day.session: day.decision for day in report.schedule.days}
    assert decisions[held_day] == "held"
    assert [session for session, _record in report.schedule.rebalances] == list(filed_days)
    assert len(report.backtest.periods) == 2


# --- Important 3: a walk-forward source, bound through its provenance, not a hand-rolled check ---


def test_a_walk_forward_records_binding_is_checked_through_its_provenance_not_a_declaration_diff(
    tmp_path: Path, repo: Path
) -> None:
    """A walk-forward record's declaration is the fitted model's own and names no registration
    (`strategy_registration`'s module docstring); it is bound only through the input provenance
    the daily command wrote for its batch (`record_is_bound`). This report does not compare
    declarations itself -- it reuses `daily_selection.forward_rebalances`, which calls
    `record_is_bound` internally -- so a walk-forward registration is witnessed exactly like a
    static one, with no separate code path here to fall out of step with `V2-P6-011`.

    This is checked at `forward_rebalances` rather than through the full `forward_report()`
    (schedule, check and priced book together), because `write_strategy_corpus` stamps its whole
    `adj_factor` partition knowable only from `READ_AT` (after every session, per `_read_at`'s own
    docstring in `test_daily_selection.py`) -- a walk-forward source's `score_day` reads it *for
    the fit itself*, so recomputing at a record's own same-day 18:30 filing time
    (`RecordCheck.__call__`, which `forward_record_check` always uses -- it has no `_at_read`-style
    escape hatch, correctly, since production must recompute at the real filing time) refuses with
    `not_yet_knowable` regardless of which signal day is chosen: every session in the fixture's
    window is earlier than `READ_AT`. `test_daily_selection.py`'s own equality test hits the same
    wall and sidesteps it with `_at_read` for a plain equivalence check; it never runs
    `forward_record_check` itself against `TRAILING`/`WALK_FORWARD` either. So `record_is_bound`'s
    use for a walk-forward record is verified here where it is reachable -- schedule witnessing --
    rather than forced through a check this fixture cannot support for a label-fitting source."""
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.WALK_FORWARD)
    anchor = sessions[1]
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)

    step = configured["rebalance_every_sessions"]
    first = (
        5  # the walk-forward source's first admissible fit, per test_daily_selection's own table
    )
    signal_days = tuple(sessions[index] for index in range(first, len(sessions) - 1, step))

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._read_at(day)
        )

    identifiers = register_days(
        tmp_path, request_for, list(signal_days), anchor, registered=admitted.declared
    )
    for day, record_id in zip(signal_days, identifiers, strict=True):
        write_journal_day(
            tmp_path,
            admitted,
            session=day,
            as_of=tds._evening(day),
            decision="rebalanced",
            reason="scheduled",
            held=False,
            record_id=record_id,
        )

    schedule = daily.forward_rebalances(tmp_path, admitted, through=sessions[-1], as_of=READ_AT)
    assert [session for session, _record in schedule.rebalances] == list(signal_days)
    assert schedule.first_record == signal_days[0]
    assert schedule.unprovable_holds == ()
    assert [record_id for _session, record_id in schedule.rebalances] == identifiers

    # The declaration each record carries is the fitted model's own, naming no registration --
    # exactly what makes a hand-rolled declaration comparison unable to bind it, and what makes
    # `record_is_bound`'s provenance check (which `forward_rebalances` used above) necessary.
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    for _session, record_id in schedule.rebalances:
        record = store_predictions.get(record_id)
        assert record is not None
        assert record.batch.artifact.declaration.name != strategy_registration.COMPOSITE_MODEL_NAME


# --- binding: mandatory, config_id checked, and this file bound too -------------------------------


def test_registration_and_repo_are_mandatory(tmp_path: Path, repo: Path) -> None:
    write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(TypeError):
        forward_report.forward_report(store, store_predictions, tmp_path, as_of=READ_AT)  # type: ignore[call-arg]


def test_a_registration_whose_config_id_does_not_match_its_own_config_is_refused(
    tmp_path: Path, repo: Path
) -> None:
    panel = write_strategy_corpus(tmp_path)
    sessions = panel.sessions
    configured = tds._base(**tds.STATIC)
    config = dict(configured, start=sessions[1])
    registration_path = register_config(repo, config)
    payload = registration_path.read_text(encoding="utf-8")
    doctored = payload.replace(grid.config_id(config), "0" * 64)
    assert doctored != payload
    registration_path.write_text(doctored, encoding="utf-8")
    _commit_file(repo, registration_path, "doctor the config_id", at=CLOCK0 + timedelta(seconds=2))

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="config_id"):
        forward_report.forward_report(
            store,
            store_predictions,
            tmp_path,
            registration=registration_path,
            repo=repo,
            as_of=READ_AT,
        )
    # One place: the daily command's own admission refuses it the same way (review minor 1).
    with pytest.raises(daily.StepFailedError, match="config_id") as refused:
        daily.admit_registration(registration_path, repo)
    assert refused.value.exit_code == daily.DailyExit.code_not_registered == 3


def test_a_change_to_only_daily_selections_own_file_after_registration_refuses(
    tmp_path: Path, repo: Path
) -> None:
    panel_root, admitted, _sessions, _signal_days = build_static_fixture(tmp_path, repo)
    (repo / "scripts" / "daily_selection.py").write_text("# moved\n", encoding="utf-8")
    _commit_file(
        repo, repo / "scripts" / "daily_selection.py", "move", at=CLOCK0 + timedelta(seconds=2)
    )

    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="SourceChangedError"):
        forward_report.forward_report(
            store,
            store_predictions,
            panel_root,
            registration=admitted.path,
            repo=repo,
            as_of=READ_AT,
        )


def test_forward_reports_own_file_is_bound_the_way_daily_selections_own_file_is(
    tmp_path: Path, repo: Path
) -> None:
    """`scripts/forward_report.py` must be as bound as `scripts/daily_selection.py` -- a change to
    *only* this file after registration refuses the report, the same way a change to `src/` or to
    `scripts/daily_selection.py` does."""
    panel_root, admitted, _sessions, _signal_days = build_static_fixture(tmp_path, repo)
    (repo / "scripts" / "forward_report.py").write_text("# moved\n", encoding="utf-8")
    _commit_file(
        repo, repo / "scripts" / "forward_report.py", "move", at=CLOCK0 + timedelta(seconds=2)
    )

    store = PanelStore(panel_root / "panel")
    store_predictions = prediction_store(panel_root, clock_at=READ_AT)
    with pytest.raises(forward_report.ForwardReportError, match="SourceChangedError"):
        forward_report.forward_report(
            store,
            store_predictions,
            panel_root,
            registration=admitted.path,
            repo=repo,
            as_of=READ_AT,
        )


# --- a runnable entry: main() -----------------------------------------------------------------


def test_main_writes_a_json_report_under_the_runtime_directory(tmp_path: Path, repo: Path) -> None:
    panel_root, admitted, _sessions, signal_days = build_static_fixture(tmp_path, repo)

    exit_code = forward_report.main(
        [
            "--runtime-dir",
            str(panel_root),
            "--registration",
            str(admitted.path),
            "--repo",
            str(repo),
            "--as-of",
            READ_AT.isoformat(),
            "--json",
        ]
    )

    assert exit_code == 0
    reports = sorted((panel_root / "reports").glob("forward-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["config_id"] == admitted.config_id
    assert payload["statistics"]["all_periods"]["periods"] == len(signal_days)
    assert payload["integrity"] == daily.INTEGRITY


# --- Fix round 3: trailing-IC / walk-forward, filed the real daily-command way -------------------
#
# Round 2's walk-forward test stopped at `forward_rebalances` (schedule witnessing) because
# `RecordCheck`'s real recompute -- at the record's own filing time -- could not be exercised
# end to end for a label-consuming source (`strategy_view.LABEL_INPUTS`: `adj_factor`, `daily`,
# `suspend_d`, `upstream_defects`) against `write_strategy_corpus`: that fixture writes each of
# those as one whole-year partition, so the partition's own `max_available_time` is the *tenth*
# session's regardless of which session is asked about, and every `as_of` before it refuses with
# `not_yet_knowable` (`panel_ingest.load_adjustment_histories`'s own docstring names this,
# `V2-P4-079`/`086`/`094`). `strategy_fixtures.write_strategy_corpus_published_daily` builds the
# same panel through growing-window writes instead, so a record checked the evening it was filed
# -- before the panel had grown past it -- is reachable. These tests drive that record through
# the real filing path (`score_day` + `signal_day_batch` + `daily.register_prediction`, at
# ~18:30, exactly `scripts/daily_selection.py`'s own step order) and `forward_report()`
# end to end, for both label-consuming sources.


def _label_consuming_scenario(
    tmp_path: Path, repo: Path, *, source: Mapping[str, Any]
) -> tuple[Path, Any, Any, date, Callable[[date], Any]]:
    """A `source` (`test_daily_selection.TRAILING` or `.WALK_FORWARD`) registration, admitted,
    on an incrementally-published corpus built exactly through the signal day for
    `adj_factor`/`suspend_d` and two sessions further for `daily`/`daily_basic`/`stk_limit` --
    enough for the book to hold one period past it (`forward_report`'s own boundary: the book's
    end must be after its first rebalance day).

    Returns `(runtime_dir, admitted, panel, signal_day, request_for)`; nothing is filed yet.
    """
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor = probe_sessions[1]
    signal_day = probe_sessions[6]
    book_through = probe_sessions[8]
    configured = tds._base(**source)
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)
    panel = write_strategy_corpus_published_daily(
        tmp_path, label_inputs_through=signal_day, through=book_through
    )

    def request_for(day: date) -> Any:
        # The daily command's own instant: the signal day's evening, when it files.
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=tds._evening(day)
        )

    return tmp_path, admitted, panel, signal_day, request_for


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(tds.TRAILING, id="trailing_ic"),
        pytest.param(tds.WALK_FORWARD, id="walk_forward"),
    ],
)
def test_an_honest_late_record_of_a_label_consuming_source_is_verified(
    tmp_path: Path, repo: Path, source: Mapping[str, Any]
) -> None:
    """Filed at 18:30 through the real path, scored from the stored builds exactly as the
    registered configuration would score it: `RecordCheck` recomputes the same scores at the
    record's own filing time and admits it verified, not merely unrefused."""
    runtime_dir, admitted, panel, signal_day, request_for = _label_consuming_scenario(
        tmp_path, repo, source=source
    )
    identifiers = register_days(
        runtime_dir, request_for, [signal_day], panel.sessions[1], registered=admitted.declared
    )
    write_journal_day(
        runtime_dir,
        admitted,
        session=signal_day,
        as_of=tds._evening(signal_day),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=identifiers[0],
    )

    store = PanelStore(runtime_dir / "panel")
    store_predictions = prediction_store(runtime_dir, clock_at=panel.as_of)
    report = forward_report.forward_report(
        store,
        store_predictions,
        runtime_dir,
        registration=admitted.path,
        repo=repo,
        as_of=panel.as_of,
    )

    assert report.check.verified == identifiers
    assert report.check.unverifiable == []
    assert len(report.backtest.periods) == 1


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(tds.TRAILING, id="trailing_ic"),
        pytest.param(tds.WALK_FORWARD, id="walk_forward"),
    ],
)
def test_a_record_whose_stored_scores_do_not_match_a_recompute_is_refused(
    tmp_path: Path, repo: Path, source: Mapping[str, Any]
) -> None:
    """The same honest scoring, but the *stored* record carries scores no build in the panel
    produced -- a corrupted write, not a stale one. Nothing in the panel changed since filing,
    so `RecordCheck` finds no correction to explain the mismatch and refuses the whole report,
    rather than pricing a number nobody can reproduce."""
    runtime_dir, admitted, panel, signal_day, request_for = _label_consuming_scenario(
        tmp_path, repo, source=source
    )
    store = PanelStore(runtime_dir / "panel")
    anchor = panel.sessions[1]
    request = request_for(signal_day)
    signal = score_day(store, request, day=signal_day, anchor=anchor)
    filed_at = tds._after_the_close(
        signal_day, panel.sessions[panel.sessions.index(signal_day) + 1]
    )
    honest = signal_day_batch(signal, request, admitted.declared, predicted_at=filed_at)
    assert honest is not None
    # Corrupted at the stored batch itself, not at `SignalDay`: a walk-forward source's
    # `predictions` come from the fitted model's own output, not from `SignalDayScores.scores`/
    # `.ranked` (perturbing those left `signal_day_batch`'s own output byte-identical, measured
    # directly). Negating every stored score is source-agnostic and, since a security's score is
    # never exactly its own negation here, guaranteed to disagree with any correct recompute.
    assert honest.predictions and all(row.score is not None for row in honest.predictions)
    batch = honest.model_copy(
        update={
            "predictions": tuple(
                row.model_copy(update={"score": -row.score}) for row in honest.predictions
            )
        }
    )
    daily.write_provenance(
        runtime_dir,
        input_provenance(
            store, request, admitted.declared, day=signal_day, batch=batch, recorded_at=filed_at
        ),
    )
    calendar = daily._outcome_calendar(store, request.exchange, signal_day, request.as_of)
    record, outcome = daily.register_prediction(
        runtime_dir, batch, calendar=calendar, clock=lambda: filed_at
    )
    assert (outcome, record.standing) == ("created", "forward")
    write_journal_day(
        runtime_dir,
        admitted,
        session=signal_day,
        as_of=tds._evening(signal_day),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=record.record_id,
    )

    store_predictions = prediction_store(runtime_dir, clock_at=panel.as_of)
    with pytest.raises(
        forward_report.ForwardReportError, match="no input it read has been corrected since"
    ):
        forward_report.forward_report(
            store,
            store_predictions,
            runtime_dir,
            registration=admitted.path,
            repo=repo,
            as_of=panel.as_of,
        )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(tds.TRAILING, id="trailing_ic"),
        pytest.param(tds.WALK_FORWARD, id="walk_forward"),
    ],
)
def test_a_label_restated_after_a_late_record_files_marks_it_unverifiable_and_still_prices_it(
    tmp_path: Path, repo: Path, source: Mapping[str, Any]
) -> None:
    """Honest at filing; then the upstream restates an earlier close inside the source's own
    lookback (`test_daily_selection._restate_a_close`, reused unchanged). The record no longer
    recomputes, its provenance names `daily` as corrected since, and it is admitted -- priced,
    not refused -- and listed under `unverifiable_inputs_corrected_after_filing`."""
    runtime_dir, admitted, panel, signal_day, request_for = _label_consuming_scenario(
        tmp_path, repo, source=source
    )
    identifiers = register_days(
        runtime_dir, request_for, [signal_day], panel.sessions[1], registered=admitted.declared
    )
    write_journal_day(
        runtime_dir,
        admitted,
        session=signal_day,
        as_of=tds._evening(signal_day),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=identifiers[0],
    )
    tds._restate_a_close(runtime_dir, panel, panel.sessions[3])

    store = PanelStore(runtime_dir / "panel")
    store_predictions = prediction_store(runtime_dir, clock_at=panel.as_of)
    report = forward_report.forward_report(
        store,
        store_predictions,
        runtime_dir,
        registration=admitted.path,
        repo=repo,
        as_of=panel.as_of,
    )

    assert report.check.verified == []
    ((flagged, changes),) = report.check.unverifiable
    assert flagged == identifiers[0]
    assert "daily:2026" in changes
    summary = report.summary["unverifiable_inputs_corrected_after_filing"]
    assert summary["count"] == 1
    assert len(report.backtest.periods) == 1
    assert report.summary["statistics"]["excluding_unverifiable"]["periods"] == 0
    # Not tested, and said so: the sensitivity set has no period at all.
    significance = report.summary["significance"]
    assert "no complete" in significance["excluding_unverifiable"]["equal_weight_all_a"]["refused"]


# --- Fix round 14: an older label-consuming record, checked after the panel has advanced ---------
#
# Each record here is filed the way the daily command files it: scored on the panel as it stood
# that evening (`_filed_on_its_own_evening`: a corpus built through the record's own day, read at
# its 18:30 filing time), then checked by a report run on a panel that has since ingested more
# sessions. `adj_factor`/`suspend_d` gate a year partition on its newest row, so that panel can no
# longer be read at any earlier record's filing time.


LABEL_CONSUMING: Final = [
    pytest.param(tds.TRAILING, id="trailing_ic"),
    pytest.param(tds.WALK_FORWARD, id="walk_forward"),
]


def _filed_on_its_own_evening(
    tmp_path: Path,
    runtime_dir: Path,
    configured: Mapping[str, Any],
    days: list[date],
    anchor: date,
    *,
    registered: RegisteredConfiguration,
    provenance_from: Path | None = None,
) -> list[str]:
    """Each of `days` scored on a corpus built through it alone, at its own filing time, and
    filed -- record and provenance -- into `runtime_dir`, as the daily command filed it then.

    `provenance_from` is a corpus to write the input provenance from instead: the advanced one,
    written before any correction a test then makes. The two fixture corpora are not one store's
    history -- a step function's newest stored row, and every build's own input manifest, depend
    on how far the corpus reaches -- so a provenance from the shorter one would name those
    fixture differences as corrections, and a test of *one* correction must compare against
    the store it corrects."""
    identifiers: list[str] = []
    for day in days:
        own = tmp_path / f"own-{day.isoformat()}"
        write_strategy_corpus_published_daily(own, label_inputs_through=day, through=day)

        def request_for(session: date) -> Any:
            return strategy_request(
                **configured,
                start=session - timedelta(days=1),
                end=session,
                as_of=tds._evening(session),
            )

        identifiers += register_days(
            runtime_dir,
            request_for,
            [day],
            anchor,
            registered=registered,
            store=PanelStore(own / "panel"),
            provenance_store=(
                None if provenance_from is None else PanelStore(provenance_from / "panel")
            ),
        )
    return identifiers


def _supersede_the_build_of(root: Path, day: date, *, through: date) -> None:
    """Rewrite the factor year -- every build through `through` -- with `day`'s replaced, at its
    own instant, by the reversed one, named in `supersedes`: the only way `write_factor_panels`
    lets a stored build be replaced."""
    store = PanelStore(root / "panel")
    full = generate_panel(shapes=("daily.close_moves_between_sessions",))
    sessions = full.sessions[1 : full.sessions.index(through) + 1]
    builds = [strategy_fixtures._build(store, full, session, late=False) for session in sessions]
    original = builds[sessions.index(day)]
    replacement = strategy_fixtures._build(store, full, day, late=False, reversed_=True)
    assert replacement.manifest.manifest_id != original.manifest.manifest_id
    write_factor_panels(
        store,
        [replacement if build is original else build for build in builds],
        supersedes=(original.manifest.manifest_id,),
    )


@pytest.mark.parametrize(
    "builds_stop", [False, True], ids=["builds_advanced", "builds_stop_at_the_day"]
)
@pytest.mark.parametrize("source", LABEL_CONSUMING)
def test_an_older_label_consuming_record_is_verified_once_the_panel_has_since_advanced(
    tmp_path: Path, repo: Path, source: Mapping[str, Any], builds_stop: bool
) -> None:
    """Once the panel has ingested sessions past a trailing-IC or walk-forward record's own
    signal day, `adj_factor`/`suspend_d` can no longer be read at the record's filing time:
    `load_adjustment_histories`/`load_suspensions` judge `not_yet_knowable` on the *partition's*
    newest row (`V2-P4-079`/`086`/`094`), so one later row refuses the whole year to every earlier
    `as_of`. `RecordCheck` recomputed there and refused every honest older record of the two
    label-consuming sources (`V2-P6-011` fix round 14). It now recomputes, when the filing time
    cannot be read, at the earliest instant every partition the day's source reads can be
    (`strategy_registration.readable_instant`). Sessions ingested since change nothing the day's
    scoring sees (`test_a_label_consuming_days_filing_time_scores_equal_the_advanced_stores`), so
    the honest record is verified.

    `builds_stop`: the factor builds stop at the signal day while every label input reaches two
    sessions further, so only the label inputs make the filing time unreadable -- the readable
    instant must be theirs (fix round 15)."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor, signal_day, advanced = probe_sessions[1], probe_sessions[6], probe_sessions[8]
    configured = tds._base(**source)
    admitted = daily.admit_registration(register_config(repo, dict(configured, start=anchor)), repo)
    identifiers = _filed_on_its_own_evening(
        tmp_path, tmp_path, configured, [signal_day], anchor, registered=admitted.declared
    )
    write_journal_day(
        tmp_path,
        admitted,
        session=signal_day,
        as_of=tds._evening(signal_day),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=identifiers[0],
    )
    panel = write_strategy_corpus_published_daily(
        tmp_path,
        label_inputs_through=advanced,
        through=advanced,
        builds_through=signal_day if builds_stop else None,
    )

    store = PanelStore(tmp_path / "panel")
    report = forward_report.forward_report(
        store,
        prediction_store(tmp_path, clock_at=panel.as_of),
        tmp_path,
        registration=admitted.path,
        repo=repo,
        as_of=tds._evening(advanced),
    )

    assert report.check.verified == identifiers
    assert report.check.unverifiable == []
    assert len(report.backtest.periods) == 1


@pytest.mark.parametrize("source", LABEL_CONSUMING)
def test_a_label_consuming_days_filing_time_scores_equal_the_advanced_stores(
    tmp_path: Path, source: Mapping[str, Any]
) -> None:
    """The invariance the round-14 recompute instant rests on. A trailing-IC or walk-forward day
    scored at its own filing time on the panel as it stood then (built through that day), and
    scored on a panel that has since ingested two more sessions -- and a re-run of the day's own
    build, reversed, stamped after the filing -- at the earliest instant every partition the day
    reads is readable there (`strategy_registration.readable_instant`), is the same batch:
    instant, artifact and every score. Rows of later sessions enter no counted IC, no training
    label and no scored cross section, and a build filed after the signal instant is never taken
    in place of the one the day had (`strategy_view._chosen_build`)."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor, day, advanced = probe_sessions[1], probe_sessions[6], probe_sessions[8]
    configured = tds._base(**source)
    filed = tds._evening(day)

    def scored(store: PanelStore, as_of: datetime) -> Any:
        request = strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=as_of
        )
        batch = signal_day_batch(
            score_day(store, request, day=day, anchor=anchor),
            request,
            tds.REGISTERED,
            predicted_at=filed,
        )
        assert batch is not None
        return batch

    write_strategy_corpus_published_daily(tmp_path / "own", label_inputs_through=day, through=day)
    write_strategy_corpus_published_daily(
        tmp_path / "advanced", label_inputs_through=advanced, through=advanced
    )
    full = generate_panel(shapes=("daily.close_moves_between_sessions",))
    advanced_store = PanelStore(tmp_path / "advanced" / "panel")
    rerun_at = filed + timedelta(hours=2)
    write_factor_panels(
        advanced_store,
        [
            *(
                strategy_fixtures._build(advanced_store, full, session, late=False)
                for session in full.sessions[1 : full.sessions.index(advanced) + 1]
            ),
            strategy_fixtures._build(
                advanced_store, full, day, late=False, reversed_=True, at=rerun_at
            ),
        ],
    )
    probe = strategy_request(**configured, start=day - timedelta(days=1), end=day, as_of=filed)
    readable = strategy_registration.readable_instant(advanced_store, probe, day=day)
    assert readable is not None and readable > rerun_at

    at_filing = scored(PanelStore(tmp_path / "own" / "panel"), filed)
    later = scored(advanced_store, readable)

    assert (later.as_of, later.artifact, later.predictions) == (
        at_filing.as_of,
        at_filing.artifact,
        at_filing.predictions,
    )


@pytest.mark.parametrize("source", LABEL_CONSUMING)
def test_a_build_superseded_after_an_older_record_files_marks_it_unverifiable(
    tmp_path: Path, repo: Path, source: Mapping[str, Any]
) -> None:
    """The later instant does not let a superseded build through. Filed honestly; then the panel
    advances and the signal day's own build is superseded in place (`write_factor_panels(...,
    supersedes=...)`, the same instant, other values). The recompute -- at the readable instant,
    the filing time no longer being readable -- reads the replacement, differs, and the record's
    provenance names the factor year as corrected since: `UNVERIFIABLE`, priced and listed, never
    `verified`."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor, signal_day, advanced = probe_sessions[1], probe_sessions[6], probe_sessions[8]
    configured = tds._base(**source)
    admitted = daily.admit_registration(register_config(repo, dict(configured, start=anchor)), repo)
    panel = write_strategy_corpus_published_daily(
        tmp_path, label_inputs_through=advanced, through=advanced
    )
    identifiers = _filed_on_its_own_evening(
        tmp_path,
        tmp_path,
        configured,
        [signal_day],
        anchor,
        registered=admitted.declared,
        provenance_from=tmp_path,
    )
    write_journal_day(
        tmp_path,
        admitted,
        session=signal_day,
        as_of=tds._evening(signal_day),
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=identifiers[0],
    )
    _supersede_the_build_of(tmp_path, signal_day, through=advanced)

    report = forward_report.forward_report(
        PanelStore(tmp_path / "panel"),
        prediction_store(tmp_path, clock_at=panel.as_of),
        tmp_path,
        registration=admitted.path,
        repo=repo,
        as_of=tds._evening(advanced),
    )

    assert report.check.verified == []
    ((flagged, changes),) = report.check.unverifiable
    assert flagged == identifiers[0]
    # The supersession and nothing else: the provenance is the advanced store's own.
    assert changes == (
        "factor_manifest_reversal_1d_v1:2026",
        "factor_obs_reversal_1d_v1:2026",
    )


def _record_return_path_decisions(
    root: Path, decisions: Sequence[tuple[date, datetime]], *, full: Any = None
) -> None:
    """`pre_close_contradicts_adj_factor` decisions (`V2-P6-020`), each about a session of the
    corpus's first security and stored under its confirming build's clock -- the `stk_limit`
    target's whole record for the year, as that target writes it (one call owns its rows).
    `full` is the corpus's generated panel (default: the ten-session one)."""
    if full is None:
        full = generate_panel(shapes=("daily.close_moves_between_sessions",))
    subject = full.securities[0]
    closes = {
        (str(code), str(day)): float(close)  # type: ignore[arg-type]
        for code, day, close in zip(
            full.batch("daily").subjects,
            full.column("daily", "trade_date"),
            full.column("daily", "close"),
            strict=True,
        )
    }
    kinds = {
        "trade_date": "string",
        "source_dataset": "string",
        "defect_kind": "string",
        "valuation_repeats_previous_close": "boolean",
        "list_date": "string",
    }
    rows: list[dict[str, object]] = []
    for session, _recorded in decisions:
        previous = full.sessions[full.sessions.index(session) - 1]
        rows.append(
            {
                "trade_date": session.isoformat(),
                "source_dataset": PRICE_LIMIT_DATASET,
                "defect_kind": "pre_close_contradicts_adj_factor",
                "bar_close": closes[(subject, session.isoformat())],
                "valuation_close": closes[(subject, previous.isoformat())],
                "previous_bar_close": closes[(subject, previous.isoformat())],
                "up_limit": None,
                "down_limit": None,
                "valuation_repeats_previous_close": None,
                "list_date": None,
            }
        )
    newest = max(recorded for _session, recorded in decisions)
    write_upstream_defects(
        PanelStore(root / "panel"),
        panel_fixtures._batch(
            UPSTREAM_DEFECTS_DATASET,
            subjects=tuple(subject for _ in decisions),
            columns=[
                PanelColumn(
                    name,
                    kinds.get(name, "float"),  # type: ignore[arg-type]
                    tuple(row[name] for row in rows),
                )
                for name in UPSTREAM_DEFECT_DATA_COLUMNS
            ],
            event_time=tuple(
                datetime.combine(session, time(15, 0), tzinfo=daily.SHANGHAI)
                for session, _recorded in decisions
            ),
            available_time=tuple(recorded for _session, recorded in decisions),
            fetched_at=newest,
        ),
        year=decisions[0][0].year,
        source_datasets=frozenset({PRICE_LIMIT_DATASET}),
    )


@pytest.mark.parametrize("decided", [False, True], ids=["nothing_recorded", "decision_recorded"])
@pytest.mark.parametrize("source", LABEL_CONSUMING)
def test_a_return_path_decision_recorded_after_filing_is_a_correction_not_a_refusal(
    tmp_path: Path, repo: Path, source: Mapping[str, Any], decided: bool
) -> None:
    """A `V2-P6-020` return-path decision carries the confirming build's clock, so one recorded
    after a record was filed is invisible to its provenance digest (rows visible at the signal
    instant) -- yet the recompute, reading the labels at the later readable instant, follows it.
    A record whose recompute then differs is `UNVERIFIABLE`, naming `upstream_defects`, not
    refused (fix round 15). Here the stored scores are negated so the recompute differs whatever
    the decision does; with nothing recorded since, the same record is refused.

    The year's `upstream_defects` partition already holds a decision recorded before the signal
    instant, so the later one moves the partition without moving what the day saw at its signal
    instant -- the case the provenance digest alone cannot see (a partition that did not exist
    at filing is already "stored since")."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor, signal_day, advanced = probe_sessions[1], probe_sessions[6], probe_sessions[8]
    configured = tds._base(**source)
    admitted = daily.admit_registration(register_config(repo, dict(configured, start=anchor)), repo)
    own = tmp_path / "own"
    write_strategy_corpus_published_daily(own, label_inputs_through=signal_day, through=signal_day)
    own_store = PanelStore(own / "panel")
    request = strategy_request(
        **configured,
        start=signal_day - timedelta(days=1),
        end=signal_day,
        as_of=tds._evening(signal_day),
    )
    filed = tds._evening(signal_day)
    honest = signal_day_batch(
        score_day(own_store, request, day=signal_day, anchor=anchor),
        request,
        admitted.declared,
        predicted_at=filed,
    )
    assert honest is not None
    batch = honest.model_copy(
        update={
            "predictions": tuple(
                row.model_copy(update={"score": -row.score}) for row in honest.predictions
            )
        }
    )
    # The advanced corpus first: the provenance is its own, as `_filed_on_its_own_evening`'s
    # `provenance_from` explains.
    panel = write_strategy_corpus_published_daily(
        tmp_path, label_inputs_through=advanced, through=advanced
    )
    before = (probe_sessions[3], tds._evening(probe_sessions[5]))
    _record_return_path_decisions(tmp_path, [before])
    daily.write_provenance(
        tmp_path,
        input_provenance(
            PanelStore(tmp_path / "panel"),
            request,
            admitted.declared,
            day=signal_day,
            batch=batch,
            recorded_at=filed,
        ),
    )
    calendar = daily._outcome_calendar(own_store, request.exchange, signal_day, request.as_of)
    record, _outcome = daily.register_prediction(
        tmp_path, batch, calendar=calendar, clock=lambda: filed
    )
    write_journal_day(
        tmp_path,
        admitted,
        session=signal_day,
        as_of=filed,
        decision="rebalanced",
        reason="scheduled",
        held=False,
        record_id=record.record_id,
    )
    if decided:
        _record_return_path_decisions(
            tmp_path, [before, (probe_sessions[4], tds._evening(advanced, hours=-1))]
        )

    def run() -> Any:
        return forward_report.forward_report(
            PanelStore(tmp_path / "panel"),
            prediction_store(tmp_path, clock_at=panel.as_of),
            tmp_path,
            registration=admitted.path,
            repo=repo,
            as_of=tds._evening(advanced),
        )

    if not decided:
        with pytest.raises(
            forward_report.ForwardReportError, match="no input it read has been corrected since"
        ):
            run()
        return
    report = run()
    ((flagged, changes),) = report.check.unverifiable
    assert flagged == record.record_id
    assert changes == ("upstream_defects:2026 (rows of the window recorded after filing)",)


@pytest.mark.parametrize("source", LABEL_CONSUMING)
def test_a_journalled_hold_before_the_panel_advanced_is_still_witnessed(
    tmp_path: Path, repo: Path, source: Mapping[str, Any]
) -> None:
    """`witnessed_days` scores a journalled hold again at the instant its run pinned; once the
    panel has advanced, a label-consuming source cannot be read there either, and the held day
    was dropped as a day the command never completed -- its scheduled rebalance then "caught up"
    on the next record. It is scored where the store can be read (`_score_when_readable`) and
    witnessed as held. `strategy_registration.score_day` is forced to rank nothing on the held
    day, `test_a_held_day_carries_no_record_and_is_not_a_rebalance`'s own technique."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor = probe_sessions[1]
    first, held_day, last, advanced = probe_sessions[6:10]
    configured = tds._base(**source)
    admitted = daily.admit_registration(register_config(repo, dict(configured, start=anchor)), repo)
    identifiers = _filed_on_its_own_evening(
        tmp_path, tmp_path, configured, [first, last], anchor, registered=admitted.declared
    )
    journalled = (
        ("rebalanced", "the first day: no book yet"),
        (
            "not a rebalance day",
            f"the book set on {held_day.isoformat()} holds until the next rebalance",
        ),
    )
    for day, record_id, (decision, reason) in zip(
        (first, last), identifiers, journalled, strict=True
    ):
        write_journal_day(
            tmp_path,
            admitted,
            session=day,
            as_of=tds._evening(day),
            decision=decision,
            reason=reason,
            held=False,
            record_id=record_id,
        )
    write_journal_day(
        tmp_path,
        admitted,
        session=held_day,
        as_of=tds._evening(held_day),
        decision="held",
        reason="the source held",
        held=True,
        record_id=None,
    )
    panel = write_strategy_corpus_published_daily(
        tmp_path, label_inputs_through=advanced, through=advanced
    )
    real_score_day = strategy_registration.score_day

    def holding(store: Any, request: Any, *, day: date, **keywords: Any) -> Any:
        signal = real_score_day(store, request, day=day, **keywords)
        if day == held_day:
            return dataclasses.replace(
                signal, scores=dataclasses.replace(signal.scores, ranked=None)
            )
        return signal

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(strategy_registration, "score_day", holding)
    try:
        report = forward_report.forward_report(
            PanelStore(tmp_path / "panel"),
            prediction_store(tmp_path, clock_at=panel.as_of),
            tmp_path,
            registration=admitted.path,
            repo=repo,
            as_of=tds._evening(advanced),
        )
    finally:
        monkeypatch.undo()

    decisions = {day.session: day.decision for day in report.schedule.days}
    assert decisions == {
        first: "rebalanced",
        held_day: "held",
        last: "not a rebalance day",
    }
    assert [session for session, _record in report.schedule.rebalances] == [first]
    assert report.check.verified == identifiers[:1]


# --- Fix round 16: the correction check measures what the day actually reads ---------------------

TWO_YEAR_WALK_FORWARD: Final[dict[str, Any]] = {
    "walk_forward": {
        **tds.WALK_FORWARD["walk_forward"],
        "train_sessions": 5,
        "refit_every_sessions": 25,
    }
}
"""A refit every 25 sessions: a day 23 sessions after its anchor is scored by the fit refitted on
the anchor, trained on the window before it."""
LATE: Final[datetime] = datetime(2027, 1, 15, 21, 0, tzinfo=daily.SHANGHAI)
"""After every filing below, before the two-year corpus's newest row."""


def _corrupted_late_record(
    root: Path, source: Mapping[str, Any], *, day: date, anchor: date
) -> tuple[Any, Any]:
    """`day`'s record under `source` on the two-year corpus, filed at its evening with every score
    negated -- so its recompute differs whatever else does -- and the provenance written then;
    and the registration's check of it. The recompute reads the corpus at its readable instant:
    the 2027 partitions reach past the filing, so the filing time cannot read them."""
    store = PanelStore(root / "panel")
    configured = tds._base(**source)
    filed = tds._evening(day)

    def request_for(session: date, at: datetime) -> Any:
        return strategy_request(
            **configured, start=session - timedelta(days=1), end=session, as_of=at
        )

    readable = strategy_registration.readable_instant(store, request_for(day, filed), day=day)
    assert readable is not None and readable > filed
    request = request_for(day, readable)
    honest = signal_day_batch(
        score_day(store, request, day=day, anchor=anchor),
        request,
        tds.REGISTERED,
        predicted_at=filed,
    )
    assert honest is not None
    assert any(row.score for row in honest.predictions)
    batch = honest.model_copy(
        update={
            "predictions": tuple(
                row.model_copy(update={"score": None if row.score is None else -row.score})
                for row in honest.predictions
            )
        }
    )
    held = input_provenance(store, request, tds.REGISTERED, day=day, batch=batch, recorded_at=filed)
    calendar = daily._outcome_calendar(store, request.exchange, day, readable)
    record, _outcome = daily.register_prediction(
        root, batch, calendar=calendar, clock=lambda: filed
    )
    check = strategy_registration.RecordCheck(
        store,
        tds.REGISTERED,
        request_for=request_for,
        anchor=anchor,
        provenance_for=lambda _record: held,
    )
    return record, check


def _a_late_factor_step(root: Path, subject: str, *, on: date, back_on: date) -> None:
    """`subject`'s `adj_factor` for `on` restated upward and back on `back_on` -- a corporate
    action the upstream published late, both rows stamped `LATE`. The year is rewritten whole
    through the real writer, which stores the steps: the two new change points are the only
    rows that move."""
    panel = strategy_fixtures._two_year_panel()
    part = dict(split_panel_batch_by_year(panel.batch(ADJ_FACTOR_DATASET)))[on.year]
    dates = next(column for column in part.columns if column.name == "factor_date").values
    marks = [
        code == subject and day in {on.isoformat(), back_on.isoformat()}
        for code, day in zip(part.subjects, dates, strict=True)
    ]

    def stamped(values: Sequence[datetime]) -> tuple[datetime, ...]:
        return tuple(LATE if mark else value for value, mark in zip(values, marks, strict=True))

    timeline = part.timeline
    write_adjustment_factors(
        PanelStore(root / "panel"),
        [
            dataclasses.replace(
                part,
                as_of=LATE,
                fetched_at=LATE,
                columns=tuple(
                    PanelColumn(
                        column.name,
                        column.kind,
                        tuple(
                            value * 1.1
                            if column.name == "adj_factor" and mark and day == on.isoformat()
                            else value
                            for value, mark, day in zip(column.values, marks, dates, strict=True)
                        ),
                    )
                    for column in part.columns
                ),
                timeline=TimelineColumns(
                    event_time=timeline.event_time,
                    available_time=stamped(timeline.available_time),
                    ingested_time=stamped(timeline.ingested_time),
                    revision_time=stamped(timeline.revision_time),
                ),
            )
        ],
        calendar=panel.calendar(),
    )


@pytest.mark.parametrize("stepped", [False, True], ids=["nothing_restated", "factor_restated"])
def test_a_late_factor_step_in_force_before_the_window_is_a_correction_not_a_refusal(
    tmp_path: Path, stepped: bool
) -> None:
    """A trailing-IC day of 2027-01-12 reads its window from early December, and a compressed
    `adj_factor` answers every day of it from the row in force when it opens -- the last change
    before it. A change dated 2026-11-30, published after the record was filed, moves that row
    though no row inside the window moved: the record, whose recompute differs, is `UNVERIFIABLE`
    naming `adj_factor:2026` (fix round 16). With nothing restated it is refused."""
    strategy_fixtures.write_two_year_corpus(tmp_path)
    day = date(2027, 1, 12)
    record, check = _corrupted_late_record(tmp_path, tds.TRAILING, day=day, anchor=day)
    if stepped:
        subject = strategy_fixtures._two_year_panel().securities[1]
        _a_late_factor_step(tmp_path, subject, on=date(2026, 11, 30), back_on=date(2026, 12, 1))

    refusal = check(record)

    if not stepped:
        assert refusal is not None and "no input it read has been corrected since" in refusal
        return
    assert refusal is None
    ((flagged, changes),) = check.unverifiable
    assert flagged == record.record_id
    assert changes == ("adj_factor:2026 (rows of the window recorded after filing)",)


@pytest.mark.parametrize("decided", [False, True], ids=["nothing_recorded", "decision_recorded"])
def test_a_walk_forward_records_window_is_counted_from_its_refit_day(
    tmp_path: Path, decided: bool
) -> None:
    """A walk-forward day scored 23 sessions after its fit's refit day (every 25) reads the
    training window before that refit day. A return-path decision about 2026-12-04 -- before the
    window counted from the day itself, inside the one counted from the refit day -- recorded
    after filing makes the record, whose recompute differs, `UNVERIFIABLE` naming
    `upstream_defects:2026` (fix round 16); with nothing recorded it is refused."""
    strategy_fixtures.write_two_year_corpus(tmp_path)
    full = strategy_fixtures._two_year_panel()
    anchor, day = date(2026, 12, 14), date(2027, 1, 15)
    assert len(full.calendar().trading_days_between(anchor, day)) - 1 == 23
    early = (date(2026, 11, 2), tds._evening(date(2026, 11, 2)))
    _record_return_path_decisions(tmp_path, [early], full=full)
    record, check = _corrupted_late_record(tmp_path, TWO_YEAR_WALK_FORWARD, day=day, anchor=anchor)
    if decided:
        _record_return_path_decisions(tmp_path, [early, (date(2026, 12, 4), LATE)], full=full)

    refusal = check(record)

    if not decided:
        assert refusal is not None and "no input it read has been corrected since" in refusal
        return
    assert refusal is None
    ((flagged, changes),) = check.unverifiable
    assert flagged == record.record_id
    assert changes == ("upstream_defects:2026 (rows of the window recorded after filing)",)


def test_a_step_series_is_read_from_the_year_before_its_window_opens() -> None:
    """A step series answers the first day of a window from the row in force then, which --
    when the window opens before a year's first session -- sits in the year before; its readers
    take that year too (`strategy_view._PanelDays.adjustments`). So the partitions a day is
    fingerprinted over reach one year further back for `adj_factor`/`suspend_d` than for a
    dataset read by the date (`V2-P6-011` fix round 16)."""
    day = date(2027, 1, 12)
    request = strategy_request(
        **tds._base(**tds.TRAILING),
        start=day - timedelta(days=1),
        end=day,
        as_of=tds._evening(day),
    )
    assert strategy_registration._dataset_years(request, day, "daily") == (2026, 2027)
    for step in ("adj_factor", "suspend_d"):
        assert strategy_registration._dataset_years(request, day, step) == (2025, 2026, 2027)
