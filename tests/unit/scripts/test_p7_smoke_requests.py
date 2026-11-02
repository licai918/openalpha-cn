"""`scripts/p7_smoke_requests.py`: the smoke's request cap and its ledger (`V2-P7-019`, task 2).

The cap is a `Counter` that refuses the increment past its allowance, handed to the daily
command's own `counted_transport` -- whose `CountingTransport` counts a request before it forwards
it -- so the request that would pass the cap never reaches the transport. The ledger is the
smoke root's append-only record of what each invocation sent. The market is the scripted one;
nothing reaches the network.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection_survivor as tss
from daily_market import Market

from openalpha_cn import cli

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


caps = _script("p7_smoke_requests")
daily = tss.daily
T0: Final[datetime] = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)


class Recording:
    """A transport that records the `api_name` of every request that reaches it."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(str(payload["api_name"]))
        return {"code": 0, "data": {"fields": [], "items": []}}


def _spend(*names: str) -> Callable[[], int]:
    """An invocation that sends one request per name through the panel's transport."""

    def call() -> int:
        transport = cli._panel_transport()
        for name in names:
            transport.post({"api_name": name, "params": {}})
        return 0

    return call


def _run(ledger: Any, call: Callable[[], int], **rules: Any) -> Any:
    keywords: dict[str, Any] = {
        "kind": "evening",
        "arguments": ["--json"],
        "cap": 10,
        "ceiling": None,
        "headroom": 1,
        "discard_on_cap": True,
    }
    keywords.update(rules)
    return caps.run_capped(ledger, call=call, clock=lambda: T0, **keywords)


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Recording:
    recording = Recording()
    monkeypatch.setattr(cli, "_panel_transport", lambda: recording)
    return recording


# --- the counter ---------------------------------------------------------------------------------


def test_a_capped_counter_counts_to_its_allowance_and_refuses_every_increment_past_it() -> None:
    counter = caps.CappedCounter(3)
    counter["daily"] += 1
    counter["daily"] += 1
    counter["adj_factor"] += 1

    with pytest.raises(caps.RequestCapReached, match="3 of 3"):
        counter["stk_limit"] += 1
    with pytest.raises(caps.RequestCapReached):
        counter["daily"] += 1

    assert dict(counter) == {"daily": 2, "adj_factor": 1}
    assert counter.sent == 3
    assert counter.reached == "stk_limit"  # the first request refused


@pytest.mark.parametrize("allowance", [-1, 1.5, True])
def test_an_allowance_is_a_whole_number_of_requests(allowance: object) -> None:
    with pytest.raises(ValueError, match="allowance"):
        caps.CappedCounter(allowance)


def test_the_request_past_the_cap_never_reaches_the_transport(transport: Recording) -> None:
    with caps.capped_requests(2) as counter:
        wrapped = cli._panel_transport()
        wrapped.post({"api_name": "daily", "params": {}})
        wrapped.post({"api_name": "daily", "params": {}})
        with pytest.raises(caps.RequestCapReached):
            wrapped.post({"api_name": "adj_factor", "params": {}})

    assert transport.posted == ["daily", "daily"]
    assert (counter.sent, counter.reached) == (2, "adj_factor")
    assert cli._panel_transport() is transport


def test_inside_the_day_s_own_counter_the_day_counts_the_refused_request_and_the_cap_does_not(
    transport: Recording,
) -> None:
    """The order `run_capped` and the daily command nest them in: the day's counter is inner, so
    it counts first -- its `tushare requests` line reads one more than was sent."""
    day: Counter[str] = Counter()
    with caps.capped_requests(1) as counter, daily.counted_transport(day):
        wrapped = cli._panel_transport()
        wrapped.post({"api_name": "daily", "params": {}})
        with pytest.raises(caps.RequestCapReached):
            wrapped.post({"api_name": "daily", "params": {}})

    assert transport.posted == ["daily"]
    assert counter.sent == 1
    assert day["daily"] == 2


@pytest.mark.parametrize("allowance", [16, 10])
def test_an_ordinary_day_under_the_cap_sends_at_most_its_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allowance: int
) -> None:
    """The real `panel build`, in process, for an ordinary survivor day (16 requests): a cap of 16
    lets it through; a cap of 10 stops it at step 2 with exactly 10 sent."""
    store = tss._seeded(
        tmp_path, monkeypatch, Market(open_days=tss.OPEN_2026), as_of=tss.SEEDED, year=2026
    )
    targets = daily.targets_for_survivor(tss.SURVIVOR_BUILDS)

    with caps.capped_requests(allowance) as counter:
        if allowance >= sum(tss.STEADY_SURVIVOR.values()):
            _panel, counts = tss._update(store, tss.DAY, targets=targets, survivor=tss._survivor())
            assert dict(counts) == tss.STEADY_SURVIVOR
        else:
            with pytest.raises(daily.StepFailedError):
                tss._update(store, tss.DAY, targets=targets, survivor=tss._survivor())

    assert len(store.market.payloads) == counter.sent == min(allowance, 16)
    assert (counter.reached is None) == (allowance >= 16)


# --- the ledger ----------------------------------------------------------------------------------


def test_run_capped_records_what_reached_the_transport_and_nothing_else(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    run = _run(ledger, _spend("daily", "daily", "stk_limit"))

    assert (run.invocation, run.exit_code, run.reached) == (1, 0, None)
    assert dict(run.sent) == {"daily": 2, "stk_limit": 1}
    spent = ledger.spent()
    assert (spent.total, spent.phase, spent.discarded, spent.unclosed) == (3, 1, None, ())
    events = _events(tmp_path / "requests.jsonl")
    assert [event["event"] for event in events] == ["start", "end"]
    assert (events[0]["allowance"], events[0]["spent_before"], events[1]["count"]) == (10, 0, 3)
    assert all(event["schema"] == caps.LEDGER_SCHEMA for event in events)


def test_the_cap_stops_the_invocation_marks_the_root_discarded_and_refuses_every_later_one(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    _run(ledger, _spend(*["daily"] * 8))

    run = _run(ledger, _spend(*["daily"] * 5))

    assert (run.exit_code, run.reached) == (caps.CAP_EXIT, "daily")
    assert len(transport.posted) == 10
    spent = ledger.spent()
    assert spent.total == 10
    assert spent.discarded is not None
    with pytest.raises(caps.SmokeBudgetError, match="discarded"):
        _run(ledger, _spend("daily"))
    assert len(transport.posted) == 10


def test_a_ceiling_bounds_one_invocation_and_its_breach_need_not_discard(
    tmp_path: Path, transport: Recording
) -> None:
    """The weekly report's ceiling is 0: any request it tries is refused, and since it writes no
    panel the root is not discarded."""
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    run = _run(ledger, _spend("daily"), kind="weekly", ceiling=0, headroom=0, discard_on_cap=False)

    assert run.exit_code == caps.CAP_EXIT
    assert transport.posted == []
    assert ledger.spent().discarded is None


def test_an_invocation_with_less_than_its_headroom_left_is_refused_before_it_starts(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    _run(ledger, _spend(*["daily"] * 8))
    before = (tmp_path / "requests.jsonl").read_bytes()

    with pytest.raises(caps.SmokeBudgetError, match="2 request"):
        _run(ledger, _spend("daily"), headroom=3)

    assert (tmp_path / "requests.jsonl").read_bytes() == before
    assert len(transport.posted) == 8


def test_an_interrupted_invocation_that_writes_the_panel_discards_the_root(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def interrupted() -> int:
        cli._panel_transport().post({"api_name": "daily", "params": {}})
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(ledger, interrupted)

    events = _events(tmp_path / "requests.jsonl")
    assert [event["event"] for event in events] == ["start", "end", "discarded"]
    assert (events[1]["fault"], events[1]["count"]) == ("KeyboardInterrupt", 1)


def test_an_invocation_that_never_recorded_its_end_refuses_every_later_one(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    ledger.append(
        {
            "event": "start",
            "id": 1,
            "kind": "panel-build",
            "arguments": [],
            "phase": 1,
            "cap": 190,
            "allowance": 190,
            "spent_before": 0,
            "at": T0.isoformat(),
        }
    )

    assert ledger.spent().unclosed == (1,)
    with pytest.raises(caps.SmokeBudgetError, match="never recorded"):
        _run(ledger, _spend("daily"))
    assert transport.posted == []


def test_the_phase_is_the_last_phase_event_and_starts_at_one(tmp_path: Path) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    assert ledger.spent().phase == 1

    ledger.append({"event": "phase", "phase": 2, "reason": "the user's go", "at": T0.isoformat()})

    assert ledger.spent().phase == 2


def test_a_ledger_line_that_is_not_its_own_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    path.write_text('{"event": "start"}\nnot json\n', encoding="utf-8")

    with pytest.raises(caps.SmokeBudgetError, match=r"requests\.jsonl, line 1"):
        caps.RequestLedger(path).spent()


def test_a_fault_of_the_invocation_is_recorded_and_raised(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def broken() -> int:
        raise OSError("the disk is full")

    with pytest.raises(OSError, match="disk"):
        _run(ledger, broken, discard_on_cap=False)

    events = _events(tmp_path / "requests.jsonl")
    assert (events[-1]["event"], events[-1]["fault"], events[-1]["count"]) == ("end", "OSError", 0)


def test_a_system_exit_of_the_invocation_is_its_exit_code_and_neither_a_fault_nor_a_discard(
    tmp_path: Path, transport: Recording
) -> None:
    """The daily command's `main` may end in `sys.exit(n)`: that is an exit code, and a day that
    exited non-zero without touching the cap is not a reason to discard the root."""
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def exits() -> int:
        cli._panel_transport().post({"api_name": "daily", "params": {}})
        raise SystemExit(2)

    run = _run(ledger, exits)

    assert (run.exit_code, run.wrapped_exit_code, run.reached) == (2, 2, None)
    events = _events(tmp_path / "requests.jsonl")
    assert [event["event"] for event in events] == ["start", "end"]
    assert (events[1]["exit_code"], events[1]["fault"], events[1]["count"]) == (2, None, 1)
    assert ledger.spent().discarded is None


def test_a_non_zero_return_passes_through_and_the_cap_still_wins_over_a_zero_exit(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def returns_three() -> int:
        return 3

    run = _run(ledger, returns_three)
    assert (run.exit_code, run.wrapped_exit_code) == (3, 3)

    def swallowed() -> int:
        """A day that catches the refusal and goes on to finish with a zero exit."""
        wrapped = cli._panel_transport()
        for _ in range(20):
            try:
                wrapped.post({"api_name": "daily", "params": {}})
            except caps.RequestCapReached:
                continue
        return 0

    run = _run(ledger, swallowed)
    assert (run.exit_code, run.wrapped_exit_code, run.reached) == (caps.CAP_EXIT, 0, "daily")
    assert ledger.spent().discarded is not None
