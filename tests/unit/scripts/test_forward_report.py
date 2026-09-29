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
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import generate_panel
from strategy_fixtures import READ_AT, write_strategy_corpus, write_strategy_corpus_published_daily

from openalpha_cn import strategy_registration
from openalpha_cn.panel.store import PanelStore
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


def register_config(repo_path: Path, config: dict[str, Any]) -> Path:
    commit = _head(repo_path)
    path = repo_path / "docs" / "research" / "p6-registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    registry.register(config, CRITERIA, path, code_commit=commit, settings=grid.protocol_settings())
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
) -> list[str]:
    """`test_daily_selection._register_days`'s own body, parameterized on `registered`: that
    helper hardcodes its own module's placeholder `REGISTERED` constant (a fixed, unrelated
    `RegisteredConfiguration`), which cannot be reused here -- this report's records must be
    bound to the *real* admitted registration `forward_rebalances`/`forward_record_check` check
    them against. Every underlying primitive (`score_day`, `signal_day_batch`, `input_provenance`,
    `daily.write_provenance`, `daily.register_prediction`) is reused unchanged; only the constant
    `_register_days` closes over is made a parameter."""
    store = PanelStore(root / "panel")
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
            input_provenance(store, request, registered, day=day, batch=batch, recorded_at=filed),
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
    tmp_path: Path, repo_path: Path
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
    registration_path = register_config(repo_path, config)
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
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=panel.as_of
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


def test_an_older_label_consuming_record_is_refused_once_the_panel_has_since_advanced(
    tmp_path: Path, repo: Path
) -> None:
    """A known, standing limitation, held here as evidence rather than left implicit
    (`panel_ingest.load_adjustment_histories`'s own docstring, `V2-P4-079`/`086`/`094`): once the
    panel has ingested sessions past a trailing-IC or walk-forward record's own signal day,
    `RecordCheck`'s recompute -- at the record's own filing time -- can no longer even read
    `adj_factor`/`suspend_d`, because `load_adjustment_histories`/`load_suspensions` judge
    `not_yet_knowable` on the *partition's* newest row, not the row the record's own day needs;
    the store has since advanced, and one late row refuses the whole year for every earlier
    `as_of`. `provenance_changes` correctly finds nothing the record read was corrected (its own
    row through its own day is unchanged), so the record is refused, not marked unverifiable --
    the whole report fails rather than silently dropping or mispricing it, but a review of more
    than the single newest day's record of either source cannot currently complete at all. The
    fix is not this module's: it is the two-part repair `load_adjustment_histories`'s own
    docstring names (`answerable_through` plus a per-subject `last_event_date` census) and that
    module's own history has measured and deliberately deferred three times over; nothing here
    should quietly route around it."""
    probe_sessions = generate_panel(shapes=("daily.close_moves_between_sessions",)).sessions
    anchor = probe_sessions[1]
    signal_day = probe_sessions[6]
    advanced_through = probe_sessions[8]
    configured = tds._base(**tds.TRAILING)
    config = dict(configured, start=anchor)
    registration_path = register_config(repo, config)
    admitted = daily.admit_registration(registration_path, repo)
    # Built once, through `advanced_through`: a store is one physical state at read time, so
    # whether the later sessions arrived before or after the record was filed makes no
    # difference to what a check run now can see -- only how far the store currently reaches
    # does. Filing still reads at `signal_day`'s own instant (`request_for`, below), exactly as
    # the daily command would have on `signal_day` itself, before any of this had happened.
    panel = write_strategy_corpus_published_daily(
        tmp_path, label_inputs_through=advanced_through, through=advanced_through
    )

    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=panel.as_of
        )

    identifiers = register_days(
        tmp_path, request_for, [signal_day], anchor, registered=admitted.declared
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

    store = PanelStore(tmp_path / "panel")
    store_predictions = prediction_store(tmp_path, clock_at=panel.as_of)
    later = tds._evening(advanced_through)
    with pytest.raises(
        forward_report.ForwardReportError, match="no input it read has been corrected since"
    ):
        forward_report.forward_report(
            store, store_predictions, tmp_path, registration=admitted.path, repo=repo, as_of=later
        )
