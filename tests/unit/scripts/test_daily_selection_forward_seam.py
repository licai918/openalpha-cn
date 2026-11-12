"""Characterisation of the forward-registration seam (`V2-P7-013`, R1).

These pin, byte for byte, the behaviour `scripts/daily_selection.py` has **before** the
family-agnostic protocol moves into `openalpha_cn.strategy_forward_session`: the refusal at the
next session's call auction, the reuse of a held record, the conflict with a different answer
under the same declaration, the calendar a window crossing New Year is placed on, and the four
names the capability ledger resolves at `scripts/daily_selection.py#<name>`.

They are written against the script's public surface on purpose. After the move the script keeps
these four names as top-level `def`/`Assign` entries that delegate to the new module, so the same
tests run green before and after; the exact refusal text and step numbering (P6 `STEPS`) and the
clock-call order are what they hold still. The batch is built the way
`tests/unit/scripts/test_daily_selection.py:2500-2530` builds it, with the helpers copied here
rather than imported from another test module.
"""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE
from panel_fixtures import _calendar_batch as calendar_batch
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus

from openalpha_cn.domain.prediction_record import outcome_known_at_for
from openalpha_cn.domain.trading_calendar import CalendarDay
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant, write_trading_calendar
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    StrategyRegistrationError,
    registration_cutoff,
    signal_day_batch,
)
from openalpha_cn.strategy_view import score_day, strategy_request

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


daily = _script("daily_selection", ROOT / "scripts")

REGISTERED: Final = RegisteredConfiguration(
    config_id="3" * 64, registration_sha256="0" * 64, code_commit="2" * 40, seed=20260926
)


def _evening(day: date, *, hours: int = 2) -> datetime:
    return session_publication_instant(day) + timedelta(hours=hours)


def _read_at(day: date) -> datetime:
    return max(READ_AT, _evening(day))


def _base(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "components": (),
        "combine": "zscore_sum",
        "transform": None,
        "neutralization": None,
        "exchange": EXCHANGE,
        "rebalance_every_sessions": 2,
        "holding_count": 3,
        "buffer_rank": None,
        "max_industry_weight": None,
    }
    return {**arguments, **overrides}


STATIC: Final[dict[str, Any]] = {
    "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
    "rebalance_every_sessions": 3,
}


def _static_request_for(configured: Mapping[str, Any]) -> Callable[[date], Any]:
    def request_for(day: date) -> Any:
        return strategy_request(
            **configured, start=day - timedelta(days=1), end=day, as_of=_read_at(day)
        )

    return request_for


def _batch_and_calendar(
    tmp_path: Path,
) -> tuple[Any, Any, date, Any]:
    """The session's scores as step 7 files them: a real corpus, the registered request, the
    batch `signal_day_batch` makes, and the calendar its outcome is placed on."""
    panel = write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    session = panel.sessions[1]
    request = _static_request_for(_base(**STATIC))(session)
    signal = score_day(store, request, day=session, anchor=panel.sessions[1])
    filed = _evening(session)
    batch = signal_day_batch(signal, request, REGISTERED, predicted_at=filed)
    assert batch is not None
    calendar = daily._outcome_calendar(store, request.exchange, session, request.as_of)
    return batch, calendar, session, filed


def _prediction_files(runtime_dir: Path) -> dict[str, bytes]:
    directory = runtime_dir / "predictions"
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def test_registering_at_the_next_call_auction_is_refused_by_name_before_anything_is_filed(
    tmp_path: Path,
) -> None:
    """At 09:15 on the next session the auction that prices the book's trade has begun, so the
    scores are no longer a prediction made before their outcome. The refusal is step 7 of P6's
    `STEPS` and says the whole reason; nothing reaches the predictions directory."""
    batch, calendar, session, _filed = _batch_and_calendar(tmp_path)
    cutoff = registration_cutoff(calendar, session)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=daily.SHANGHAI)

    with pytest.raises(daily.StepFailedError) as refused:
        daily.register_prediction(tmp_path, batch, calendar=calendar, clock=lambda: cutoff)

    error = refused.value
    detail = (
        f"the scores of {session.isoformat()} are traded at the next session's open, whose call "
        f"auction starts {cutoff.isoformat()}, and it is {cutoff.isoformat()}: part of their "
        f"outcome is published already (all of it by {deadline.isoformat()}), so registered "
        "now they would not be a prediction made before its outcome. Nothing was filed"
    )
    prefix = f"step {daily.STEPS.index('prediction') + 1} (prediction) failed: "
    assert error.step == "prediction"
    assert error.detail == detail
    assert str(error) == prefix + detail
    assert _prediction_files(tmp_path) == {}


def test_a_second_identical_call_reuses_the_record_and_writes_nothing(tmp_path: Path) -> None:
    """A daily command re-run the same evening finds its own record under the same declaration:
    the same id comes back marked `unchanged`, and the store's bytes do not move."""
    batch, calendar, _session, filed = _batch_and_calendar(tmp_path)
    clock = lambda: filed  # noqa: E731  (the command's clock, held still)

    record, outcome = daily.register_prediction(tmp_path, batch, calendar=calendar, clock=clock)
    filed_files = _prediction_files(tmp_path)
    assert (outcome, record.standing) == ("created", "forward")
    assert filed_files

    again, again_outcome = daily.register_prediction(
        tmp_path, batch, calendar=calendar, clock=clock
    )

    assert (again.record_id, again_outcome) == (record.record_id, "unchanged")
    assert _prediction_files(tmp_path) == filed_files


def test_a_held_record_with_other_scores_for_the_same_declaration_is_refused(
    tmp_path: Path,
) -> None:
    """A second answer to one session would be a revision. The registered record stands, and the
    script raises step 7 wrapping the registration error it came from, message unchanged."""
    batch, calendar, _session, filed = _batch_and_calendar(tmp_path)
    record, _outcome = daily.register_prediction(
        tmp_path, batch, calendar=calendar, clock=lambda: filed
    )
    altered = batch.model_copy(update={"predictions": batch.predictions[:-1]})

    with pytest.raises(daily.StepFailedError) as refused:
        daily.register_prediction(
            tmp_path, altered, calendar=calendar, clock=lambda: filed + timedelta(minutes=1)
        )

    error = refused.value
    cause = error.__cause__
    assert isinstance(cause, StrategyRegistrationError)
    expected = (
        f"{record.record_id} already registers {batch.as_of.isoformat()} under this declaration "
        "with other scores; the registered record stands and nothing was filed. The panel or a "
        "factor build changed after it was registered"
    )
    assert error.step == "prediction"
    assert error.detail == expected
    assert str(cause) == expected
    assert str(error) == (
        f"step {daily.STEPS.index('prediction') + 1} (prediction) failed: {expected}"
    )


def _january_2027() -> tuple[CalendarDay, ...]:
    first = date(2027, 1, 1)
    days = [first + timedelta(days=offset) for offset in range(31)]
    return tuple(
        CalendarDay(
            calendar_date=day,
            is_trading=day.weekday() < 5 and day != date(2027, 1, 1),
        )
        for day in days
    )


def test_the_outcome_calendar_loads_next_year_only_when_that_year_is_stored(
    tmp_path: Path,
) -> None:
    """A window crossing New Year is placed on the next year's calendar -- but only when the
    store holds it. The year is chosen from the store's own registered years, not asked for
    blind (which would refuse a record whose window ends inside the session's year)."""
    write_strategy_corpus(tmp_path)
    store = PanelStore(tmp_path / "panel")
    session = date(2026, 12, 31)

    without_next_year = daily._outcome_calendar(store, EXCHANGE, session, READ_AT)

    assert without_next_year.horizon.last_date == date(2026, 12, 31)

    write_trading_calendar(store, calendar_batch(_january_2027()))
    with_next_year = daily._outcome_calendar(store, EXCHANGE, session, READ_AT)

    assert without_next_year.horizon.last_date.year == 2026
    assert with_next_year.horizon.last_date >= date(2027, 1, 1)
    assert set(without_next_year.trading_days) < set(with_next_year.trading_days)


def test_the_capability_ledger_names_are_top_level_definitions_of_the_script() -> None:
    """`scripts/build_feature_coverage.py::_module_symbols` resolves only top-level
    `def`/`class`/`Assign`/`AnnAssign` -- never `from x import y`. The ledger names
    `scripts/daily_selection.py#_survivor_prediction` and `#register_prediction` (OA-OPS-041);
    R1 adds `#_outcome_calendar` and `#_prediction_block` as `Assign` aliases of the new module,
    so all four have to stay resolvable at the script."""
    tree = ast.parse((ROOT / "scripts" / "daily_selection.py").read_text(encoding="utf-8"))
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    assert {
        "register_prediction",
        "_survivor_prediction",
        "_outcome_calendar",
        "_prediction_block",
    } <= defined
