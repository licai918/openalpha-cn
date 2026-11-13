"""`scripts/p7_event_probe.py`: the V2-P7-010 event-interface probe (plan Task 2.2).

The probe is tooling only. Its default mode is `--dry-run`: it prints the planned Tushare
requests and sends nothing. Its `--live` mode needs the user's explicit go and an environment
token, and the only request budget it may spend is `PROBE_CAP` = 300, enforced by reusing
`scripts/p7_smoke_requests.py`'s capped counter. A report is metadata only -- interface names,
permission answers, row and page counts, field names, the earliest date seen, timestamp-like
field names, and the vendor's `msg` for a rejection (truncated, token-redacted) -- and never a
row value. A `40203` stops only its own interface while the other four continue; a `40101` stops
the whole run; `--skip-interface` leaves an interface out of the plan at zero requests. Every
transport below is scripted, so nothing reaches the network.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
TODAY: Final[date] = date(2026, 11, 12)
BUDGET_SCHEMA: Final[str] = "openalpha-p7-event-probe-budget/v1"
NAMED: Final[tuple[str, ...]] = (
    "moneyflow",
    "limit_list_d",
    "top_list",
    "top_inst",
    "anns_d",
)
PURPOSES: Final[frozenset[str]] = frozenset({"schema", "full_day", "depth", "pit_field"})


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


probe = _script("p7_event_probe")


class Recording:
    """A transport that records every request that reaches it, and answers the scripted ones.

    With no scripted response left it answers `code=0` with an empty data page, so a plan of any
    length can be driven through it without touching the network.
    """

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.posted: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(dict(payload))
        if self._responses:
            return self._responses.pop(0)
        return {"code": 0, "data": {"fields": ["trade_date", "ts_code"], "items": []}}


class Refusing:
    """A transport whose every answer is a transport-level exception."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise OSError("the scripted transport refused the request")


class Rejecting:
    """A transport whose every answer is a Tushare rejection carrying `code`."""

    def __init__(self, code: int, msg: str = "scripted rejection") -> None:
        self.code = code
        self.msg = msg
        self.posted: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(dict(payload))
        return {"code": self.code, "msg": self.msg, "data": None}


def _call(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "api_name": "moneyflow",
        "params": {"trade_date": "20261111"},
        "purpose": "full_day",
    }
    values.update(overrides)
    return probe.ProbeCall(**values)


def _page(rows: int, *, more: Any = None) -> dict[str, Any]:
    """One scripted Tushare response: `rows` rows of two columns.

    `more` is the server's `has_more` flag when given; `None` leaves the flag out entirely, which
    is the case the probe has to judge by the documented row limit.
    """
    data: dict[str, Any] = {
        "fields": ["trade_date", "ts_code"],
        "items": [["20261111", "600519.SH"]] * rows,
    }
    if more is not None:
        data["has_more"] = more
    return {"code": 0, "data": data}


# --- the plan -----------------------------------------------------------------------------------


def test_the_plan_never_exceeds_300_requests() -> None:
    assert probe.PROBE_CAP == 300

    at_the_cap = probe.probe_plan(today=TODAY, depth_years=tuple(range(1, 58)))
    assert len(at_the_cap) == probe.PROBE_CAP

    with pytest.raises(ValueError, match="300"):
        probe.probe_plan(today=TODAY, depth_years=tuple(range(1, 59)))

    with pytest.raises(ValueError, match="depth"):
        probe.probe_plan(today=TODAY, depth_years=(0,))
    with pytest.raises(ValueError, match="depth"):
        probe.probe_plan(today=TODAY, depth_years=(True,))


def test_the_plan_covers_the_five_named_interfaces() -> None:
    plan = probe.probe_plan(today=TODAY, depth_years=(1, 2))

    assert {call.api_name for call in plan} == set(NAMED)
    for api_name in NAMED:
        purposes = {call.purpose for call in plan if call.api_name == api_name}
        assert purposes == PURPOSES

    assert all(value for call in plan for value in call.params.values())
    assert all(isinstance(value, str) for call in plan for value in call.params.values())
    assert plan == probe.probe_plan(today=TODAY, depth_years=(2, 1))


def test_the_plan_can_leave_out_an_interface_an_operator_skips() -> None:
    plan = probe.probe_plan(today=TODAY, depth_years=(1, 2), skip_interfaces=("anns_d",))

    assert len(plan) == 20  # 4 interfaces x (3 probes + 2 depth years)
    assert {call.api_name for call in plan} == set(NAMED) - {"anns_d"}

    with pytest.raises(ValueError, match="not_an_interface"):
        probe.probe_plan(today=TODAY, depth_years=(1,), skip_interfaces=("not_an_interface",))


# --- the cap ------------------------------------------------------------------------------------


def test_the_cap_stops_the_301st_request_unsent() -> None:
    plan = tuple(_call(params={"trade_date": f"{index:08d}"}) for index in range(1, 302))
    transport = Recording()

    run = probe.run_probe(plan, transport=transport)

    assert len(transport.posted) == probe.PROBE_CAP == 300
    assert len(run.findings) == probe.PROBE_CAP
    sent = [payload["params"]["trade_date"] for payload in transport.posted]
    assert sent == [f"{index:08d}" for index in range(1, 301)]
    assert "00000301" not in sent  # the request that would pass the cap was never even built

    stopped = probe.run_probe(plan, transport=Recording(), cap=2)
    assert len(stopped.findings) == 2


# --- a credential rejection stops the whole run (fix round 2, N3) -------------------------------


def test_a_credential_rejection_stops_the_run_after_one_request() -> None:
    transport = Rejecting(40101)
    plan = probe.probe_plan(today=TODAY, depth_years=(1, 2, 3, 4, 5))

    run = probe.run_probe(plan, transport=transport)

    assert len(transport.posted) == 1  # a rejected credential must not spend the whole plan
    assert len(run.findings) == 1
    finding = run.findings[0]
    assert finding.permitted is False
    assert finding.error_code == "40101"
    assert run.stop is not None
    assert run.stop.reason == "credential_refused"
    assert run.stop.api_name == plan[0].api_name
    assert run.not_sent == len(plan) - 1

    report = probe.render_report(run.findings, spent=1, stop=run.stop, not_sent=run.not_sent)
    assert "credential refused" in report
    assert f"{len(plan) - 1} planned calls not sent" in report


def test_a_40101_at_a_later_planned_call_stops_the_run_there() -> None:
    """A refused credential ends the run exactly where it is met, not at the first call only."""
    failed = "您的token不对"
    plan = probe.probe_plan(today=TODAY, depth_years=(1, 2, 3, 4, 5))
    transport = Recording(
        [
            _page(1),
            _page(1),
            {"code": 40101, "msg": failed, "data": None},
        ]
    )

    run = probe.run_probe(plan, transport=transport)

    assert len(transport.posted) == 3  # two permitted calls, then the rejected one
    assert len(run.findings) == 3
    assert run.findings[2].error_code == "40101"
    assert run.findings[2].msg == failed
    assert run.stop is not None
    assert run.stop.error_code == "40101"
    assert run.stop.api_name == plan[2].api_name
    assert run.not_sent == len(plan) - 3

    report = probe.render_report(run.findings, spent=3, stop=run.stop, not_sent=run.not_sent)
    assert failed in report
    assert "credential refused" in report


def test_a_40101_on_a_follow_up_page_also_stops_the_whole_run() -> None:
    """`40101` anywhere stops everything: a refused credential inside a paged call too."""
    transport = Recording(
        [
            _page(probe.PAGE_ROW_LIMIT, more=True),
            {"code": 40101, "msg": "您的token不对", "data": None},
        ]
    )

    run = probe.run_probe((_call(purpose="full_day"),), transport=transport)

    assert len(transport.posted) == 2
    assert run.stop is not None
    assert run.stop.reason == "credential_refused"
    assert run.findings[0].error_code == "40101"
    assert run.findings[0].truncated == "yes"


# --- a 40203 stops only its own interface (V2-P7-010, L1 fix) -----------------------------------


def test_a_40203_stops_only_that_interface_and_the_other_four_continue() -> None:
    denied = "抱歉，您没有权限访问该接口"
    plan = probe.probe_plan(today=TODAY, depth_years=(1, 2, 3, 4, 5))
    transport = Recording(
        [
            _page(1),
            _page(1),
            _page(1),
            _page(1),
            {"code": 40203, "msg": denied, "data": None},
        ]
    )

    run = probe.run_probe(plan, transport=transport)

    sent = [payload["api_name"] for payload in transport.posted]
    assert len(transport.posted) == 33  # 40 planned; the seven later anns_d calls never built
    assert sent.count("anns_d") == 1  # the rejected one; no later anns_d request was sent
    assert [index for index, name in enumerate(sent, start=1) if name == "anns_d"] == [5]
    for api_name in ("moneyflow", "limit_list_d", "top_list", "top_inst"):
        assert sent.count(api_name) == 8  # every remaining call to the other four continued

    assert run.stop is None  # a 40203 no longer ends the run
    assert run.not_sent == 7
    assert len(run.findings) == 33
    assert run.interfaces_stopped == (
        probe.ProbeInterfaceStop(api_name="anns_d", error_code="40203", msg=denied),
    )

    report = probe.render_report(run.findings, spent=33, interfaces_stopped=run.interfaces_stopped)
    assert "stopped: 40203" in report
    assert denied in report


def test_a_live_run_stopped_by_one_40203_still_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    denied = "抱歉，您没有权限访问该接口"
    budget = tmp_path / "p7-event-probe-budget.json"
    transport = Recording(
        [
            _page(1),
            _page(1),
            _page(1),
            _page(1),
            {"code": 40203, "msg": denied, "data": None},
        ]
    )
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(
        [
            "--live",
            "--i-have-the-users-go",
            "--budget-file",
            str(budget),
            "--depth-years",
            "1",
        ]
    )
    captured = capsys.readouterr()

    assert code == 0  # the run finishes; only the one interface is refused
    assert len(transport.posted) == 17  # 20 planned, three later anns_d calls never built
    assert probe.read_probe_budget(budget) == 17
    assert "stopped: 40203" in captured.out
    assert denied in captured.out
    assert "anns_d" in captured.err


def test_an_other_rejection_code_is_a_finding_that_continues() -> None:
    plan = tuple(_call(params={"trade_date": f"{index:08d}"}) for index in range(1, 4))
    transport = Rejecting(40001)

    run = probe.run_probe(plan, transport=transport)

    assert len(transport.posted) == 3
    assert len(run.findings) == 3
    assert {finding.error_code for finding in run.findings} == {"40001"}
    assert run.stop is None


def test_a_live_credential_rejection_records_one_request_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = tmp_path / "p7-event-probe-budget.json"
    transport = Rejecting(40101)
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(
        [
            "--live",
            "--i-have-the-users-go",
            "--budget-file",
            str(budget),
            "--depth-years",
            "1",
        ]
    )
    captured = capsys.readouterr()

    assert len(transport.posted) == 1
    assert probe.read_probe_budget(budget) == 1  # exactly what was sent, not the 20 planned
    assert code != 0
    assert code == probe.STOPPED_EXIT
    assert "credential" in captured.err
    assert "40101" in captured.err
    assert "credential refused" in captured.out
    assert "19 planned calls not sent" in captured.out


# --- a full market day is paged, and a page at the limit may be truncated (fix round 1, F7) ----


def test_a_full_page_buys_an_offset_follow_up_and_reports_every_page() -> None:
    limit = probe.PAGE_ROW_LIMIT
    assert limit == 6000
    transport = Recording([_page(limit, more=True), _page(7, more=False)])

    run = probe.run_probe((_call(purpose="full_day"),), transport=transport)

    assert len(transport.posted) == 2
    first, second = transport.posted
    assert first["params"] == {"trade_date": "20261111"}
    assert second["params"] == {
        "trade_date": "20261111",
        "limit": str(limit),
        "offset": str(limit),
    }
    finding = run.findings[0]
    assert finding.pages == 2
    assert finding.rows == limit + 7
    assert finding.truncated == "no"


def test_a_page_at_the_limit_with_no_budget_left_is_possibly_truncated() -> None:
    transport = Recording([_page(probe.PAGE_ROW_LIMIT)])

    run = probe.run_probe((_call(purpose="full_day"),), transport=transport, cap=1)

    assert len(transport.posted) == 1
    finding = run.findings[0]
    assert finding.pages == 1
    assert finding.rows == probe.PAGE_ROW_LIMIT
    assert finding.truncated == "possibly"


def test_the_servers_has_more_flag_is_a_hard_truncation_when_the_budget_ends() -> None:
    transport = Recording([_page(probe.PAGE_ROW_LIMIT, more=True)])

    run = probe.run_probe((_call(purpose="full_day"),), transport=transport, cap=1)

    assert len(transport.posted) == 1
    assert run.findings[0].truncated == "yes"


# --- the report ---------------------------------------------------------------------------------


def test_the_report_contains_field_names_and_counts_but_no_row_values() -> None:
    transport = Recording(
        [
            {
                "code": 0,
                "data": {
                    "fields": ["trade_date", "ts_code", "first_time", "net_mf_amount"],
                    "items": [["20261111", "600519.SH", "09:31:00", "12345.67"]],
                },
            }
        ]
    )
    plan = (_call(api_name="limit_list_d", params={"trade_date": "20261111"}),)

    run = probe.run_probe(plan, transport=transport)
    report = probe.render_report(run.findings, spent=len(run.findings))

    assert "limit_list_d" in report
    assert "Requests spent: 1 of PROBE_CAP (300)." in report
    assert "trade_date" in report
    assert "first_time" in report
    assert "net_mf_amount" in report
    assert "4 field(s)" in report
    assert "2026-11-11" in report  # the earliest date seen is metadata, not a row value
    assert "permitted" in report.lower()

    assert "600519.SH" not in report
    assert "12345.67" not in report
    assert "09:31:00" not in report


def test_the_report_flags_truncation_and_names_the_page_limit() -> None:
    transport = Recording([_page(probe.PAGE_ROW_LIMIT)])
    run = probe.run_probe((_call(purpose="full_day"),), transport=transport, cap=1)

    report = probe.render_report(run.findings, spent=1)

    assert "truncated" in report.lower()
    assert "possibly" in report
    assert str(probe.PAGE_ROW_LIMIT) in report
    assert "has_more" in report


# --- refusals are findings ----------------------------------------------------------------------


def test_a_permission_error_is_a_finding_not_a_crash() -> None:
    refused = Recording([{"code": 40101, "msg": "your token is wrong", "data": None}])

    run = probe.run_probe((_call(),), transport=refused)

    assert len(run.findings) == 1
    finding = run.findings[0]
    assert finding.api_name == "moneyflow"
    assert finding.permitted is False
    assert finding.error_code == "40101"
    assert finding.rows == 0
    assert finding.pages == 0
    assert finding.fields == ()
    assert finding.earliest_date_seen is None
    assert "40101" in probe.render_report(run.findings, spent=1)

    failed = probe.run_probe((_call(),), transport=Refusing()).findings
    assert failed[0].permitted is False
    assert failed[0].error_code == "OSError"


# --- the vendor's message is recorded as metadata (V2-P7-010, L1 fix) ---------------------------


def test_the_vendor_message_is_recorded_for_a_continuing_rejection() -> None:
    transport = Rejecting(40001, msg="抱歉，您没有权限访问该接口")

    run = probe.run_probe((_call(), _call()), transport=transport)

    assert len(run.findings) == 2
    assert run.findings[0].msg == "抱歉，您没有权限访问该接口"
    assert "抱歉，您没有权限访问该接口" in probe.render_report(run.findings, spent=2)


def test_a_rejection_message_is_truncated_to_200_characters() -> None:
    assert probe.MAX_MESSAGE_CHARS == 200
    transport = Recording([{"code": 40203, "msg": "x" * 500, "data": None}])

    run = probe.run_probe((_call(),), transport=transport)
    finding = run.findings[0]

    assert finding.msg is not None
    assert len(finding.msg) == probe.MAX_MESSAGE_CHARS
    report = probe.render_report(run.findings, spent=1, interfaces_stopped=run.interfaces_stopped)
    assert "x" * probe.MAX_MESSAGE_CHARS in report
    assert "x" * (probe.MAX_MESSAGE_CHARS + 1) not in report


def test_a_rejection_message_cannot_break_the_report_table() -> None:
    transport = Recording(
        [{"code": 40203, "msg": "no | permission\nfor this interface", "data": None}]
    )

    run = probe.run_probe((_call(),), transport=transport)
    finding = run.findings[0]

    assert finding.msg == "no \\| permission for this interface"  # folded, pipe escaped
    report = probe.render_report(run.findings, spent=1, interfaces_stopped=run.interfaces_stopped)
    row = next(line for line in report.splitlines() if line.startswith("| `moneyflow`"))
    body = row.removeprefix("|").removesuffix("|")
    # `\\|` is the only escape GitHub honours, so the escaped pipe stays inside its cell.
    assert len(re.split(r"(?<!\\)\|", body)) == 9
    assert "no \\| permission for this interface" in report


def test_the_token_never_appears_in_the_report_or_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "probe-token-that-must-not-leak"
    budget = tmp_path / "p7-event-probe-budget.json"
    transport = Recording([{"code": 40101, "msg": f"bad credential {token}", "data": None}])
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", token)

    code = probe.main(
        [
            "--live",
            "--i-have-the-users-go",
            "--budget-file",
            str(budget),
            "--depth-years",
            "1",
        ]
    )
    captured = capsys.readouterr()

    assert code == probe.STOPPED_EXIT
    assert len(transport.posted) == 1
    assert probe.read_probe_budget(budget) == 1
    assert token not in captured.out
    assert token not in captured.err
    assert "[redacted]" in captured.out  # the defensive redaction, not the token

    rejected = probe.run_probe(
        (_call(),),
        transport=Recording([{"code": 40203, "msg": f"denied {token}", "data": None}]),
    )
    report = probe.render_report(
        rejected.findings, spent=1, interfaces_stopped=rejected.interfaces_stopped
    )
    assert token not in report
    assert "[redacted]" in report


# --- the dry run writes and sends nothing -------------------------------------------------------


def test_dry_run_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> Any:
        raise AssertionError("a dry run asked for a Tushare transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--dry-run"])
    captured = capsys.readouterr()

    assert code == 0
    assert "nothing is sent" in captured.out
    assert "moneyflow" in captured.out
    assert "limit_list_d" in captured.out
    assert captured.err == ""

    assert probe.main([]) == 0  # --dry-run is the default


def test_nothing_is_written_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the probe wrote to disk")

    for name in ("open", "write_text", "write_bytes", "mkdir", "touch"):
        monkeypatch.setattr(Path, name, refuse)

    assert probe.main(["--dry-run"]) == 0
    capsys.readouterr()

    run = probe.run_probe((_call(),), transport=Recording())
    probe.render_report(run.findings, spent=len(run.findings))

    assert list(tmp_path.iterdir()) == []


# --- --skip-interface plans and sends nothing for that interface --------------------------------


def test_skip_interface_reduces_the_dry_run_plan_and_sends_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert probe.main(["--dry-run", "--skip-interface", "anns_d"]) == 0
    dry = capsys.readouterr().out
    assert "planned calls: 32 of PROBE_CAP (300)" in dry
    assert "skipped by operator" in dry
    assert "| `anns_d` |" not in dry  # no planned row for the skipped interface

    budget = tmp_path / "p7-event-probe-budget.json"
    transport = Recording()

    def refuse() -> Any:
        raise AssertionError("a dry run asked for a Tushare transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    assert probe.main(["--dry-run", "--skip-interface", "anns_d"]) == 0  # still sends none
    capsys.readouterr()

    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")
    code = probe.main(
        [
            "--live",
            "--i-have-the-users-go",
            "--budget-file",
            str(budget),
            "--skip-interface",
            "anns_d",
        ]
    )
    live = capsys.readouterr()

    assert code == 0
    assert len(transport.posted) == 32
    assert all(payload["api_name"] != "anns_d" for payload in transport.posted)
    assert probe.read_probe_budget(budget) == 32  # zero requests for the skipped interface
    assert "skipped by operator" in live.out
    assert "anns_d" in live.out


def test_skip_interface_is_repeatable_and_rejects_a_name_it_does_not_know(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        probe.main(["--dry-run", "--skip-interface", "anns_d", "--skip-interface", "top_inst"]) == 0
    )
    out = capsys.readouterr().out
    assert "planned calls: 24 of PROBE_CAP (300)" in out
    assert "| `anns_d` |" not in out
    assert "| `top_inst` |" not in out

    with pytest.raises(SystemExit):
        probe.main(["--dry-run", "--skip-interface", "not_an_interface"])


# --- --live is gated, and its token comes from the environment ----------------------------------


def test_live_without_the_users_go_refuses_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> Any:
        raise AssertionError("--live without the go asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--live"])
    captured = capsys.readouterr()

    assert code != 0
    assert "i-have-the-users-go" in captured.err
    assert "nothing was sent" in captured.err


def test_live_without_a_token_refuses_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> Any:
        raise AssertionError("--live without a token asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    code = probe.main(["--live", "--i-have-the-users-go"])
    captured = capsys.readouterr()

    assert code != 0
    assert "TUSHARE_TOKEN" in captured.err
    assert "nothing was sent" in captured.err


def test_the_token_comes_from_the_environment_and_is_never_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "probe-token-from-the-environment")
    transport = Recording()

    run = probe.run_probe((_call(),), transport=transport)

    assert transport.posted[0]["token"] == "probe-token-from-the-environment"
    assert "probe-token-from-the-environment" not in probe.render_report(run.findings, spent=1)


# --- the cumulative budget across invocations (fix round 1, F12) --------------------------------


def _budget_file(path: Path, spent: int) -> Path:
    """A budget file holding `spent` cumulative requests."""
    path.write_text(
        json.dumps({"schema": BUDGET_SCHEMA, "spent": spent}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_live_without_a_budget_file_refuses_by_name_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> Any:
        raise AssertionError("--live without a budget file asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--live", "--i-have-the-users-go"])
    captured = capsys.readouterr()

    assert code != 0
    assert "--budget-file" in captured.err
    assert "nothing was sent" in captured.err


def test_a_budget_file_with_299_spent_allows_exactly_one_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert probe.PROBE_BUDGET_SCHEMA == BUDGET_SCHEMA
    budget = _budget_file(tmp_path / "p7-event-probe-budget.json", 299)
    transport = Recording()
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--live", "--i-have-the-users-go", "--budget-file", str(budget)])

    captured = capsys.readouterr()
    assert code == 0
    assert len(transport.posted) == 1
    assert probe.read_probe_budget(budget) == 300
    assert json.loads(budget.read_text(encoding="utf-8"))["spent"] == 300
    assert "Requests spent: 1 of PROBE_CAP (300)." in captured.out
    assert "Cumulative budget: 300 of PROBE_CAP (300)" in captured.out


def test_a_budget_file_with_300_spent_refuses_with_zero_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = _budget_file(tmp_path / "p7-event-probe-budget.json", 300)

    def refuse() -> Any:
        raise AssertionError("a spent budget asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--live", "--i-have-the-users-go", "--budget-file", str(budget)])
    captured = capsys.readouterr()

    assert code != 0
    assert "budget" in captured.err.lower()
    assert "nothing was sent" in captured.err
    assert probe.read_probe_budget(budget) == 300


def test_two_live_runs_add_their_requests_to_one_budget_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = tmp_path / "p7-event-probe-budget.json"
    assert not budget.exists()
    assert probe.read_probe_budget(budget) == 0  # a file that does not exist yet is 0 spent
    transport = Recording()
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")
    arguments = [
        "--live",
        "--i-have-the-users-go",
        "--budget-file",
        str(budget),
        "--depth-years",
        "1",
    ]

    assert probe.main(arguments) == 0
    capsys.readouterr()
    assert len(transport.posted) == 20  # 5 interfaces x (3 probes + 1 depth year)
    assert probe.read_probe_budget(budget) == 20

    assert probe.main(arguments) == 0
    capsys.readouterr()
    assert len(transport.posted) == 40
    assert probe.read_probe_budget(budget) == 40


def test_the_budget_counts_every_sent_request_including_pages_and_rejections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = _budget_file(tmp_path / "p7-event-probe-budget.json", 10)
    denied = "no permission for this interface"
    transport = Recording(
        [
            _page(1),
            _page(1),
            _page(1),
            _page(1),
            _page(1),
            _page(probe.PAGE_ROW_LIMIT, more=True),  # call 6, full_day page 1: buys a follow-up
            _page(2, more=False),  # call 6, follow-up page
            {"code": 40203, "msg": denied, "data": None},  # call 7, refused: counted and stopped
        ]
    )
    monkeypatch.setattr(probe, "_default_transport", lambda: transport)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(
        [
            "--live",
            "--i-have-the-users-go",
            "--budget-file",
            str(budget),
            "--depth-years",
            "1",
        ]
    )
    captured = capsys.readouterr()

    # 20 planned calls; 18 sent, one of them paged twice, so 19 requests: a rejected request
    # and a follow-up page are each counted too, and the two later limit_list_d calls are not.
    assert code == 0
    assert len(transport.posted) == 19
    assert probe.read_probe_budget(budget) == 29
    assert "Requests spent: 19 of PROBE_CAP (300)." in captured.out
    assert "Cumulative budget: 29 of PROBE_CAP (300) requests spent." in captured.out
    assert denied in captured.out


def test_a_dry_run_leaves_a_preexisting_budget_file_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = _budget_file(tmp_path / "p7-event-probe-budget.json", 7)
    original = budget.read_bytes()
    absent = tmp_path / "never-created.json"

    def refuse() -> Any:
        raise AssertionError("a dry run asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)

    assert probe.main(["--dry-run", "--budget-file", str(budget)]) == 0
    assert probe.main(["--dry-run", "--budget-file", str(absent)]) == 0
    capsys.readouterr()

    assert budget.read_bytes() == original
    assert not absent.exists()


def test_an_unreadable_budget_file_refuses_by_name_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = tmp_path / "p7-event-probe-budget.json"
    budget.write_text("not json\n", encoding="utf-8")

    def refuse() -> Any:
        raise AssertionError("an unreadable budget file asked for a transport")

    monkeypatch.setattr(probe, "_default_transport", refuse)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-real-token")

    code = probe.main(["--live", "--i-have-the-users-go", "--budget-file", str(budget)])
    captured = capsys.readouterr()

    assert code != 0
    assert str(budget) in captured.err
    assert "nothing was sent" in captured.err


DOCUMENTED_BUDGET_FILES: Final[tuple[str, ...]] = (
    "docs/runbooks/agent-evening.zh-CN.md",
    "docs/research/p7-c-protocol.md",
)
"""The two documents that tell the L1 operator what the starting budget file must contain."""


@pytest.mark.parametrize("relative", DOCUMENTED_BUDGET_FILES)
def test_the_documented_l1_budget_literal_is_a_file_the_probe_reads(
    relative: str, tmp_path: Path
) -> None:
    """F6 (fix round 1): the documented L1 starting literal must be a budget file the probe reads.

    The 2026-11-12 refused attempts count against L1's 300-request cap, so L1's budget file must
    start at `spent` 20. The operator copies the literal the runbook/protocol prints; this test
    extracts it and parses it with the probe's own reader, so a literal the reader would refuse
    (the batch-3 first version documented `{"spent": 20}`, which lacks the schema) fails here
    instead of at the gated L1 run. The live file is never created here.
    """
    text = (ROOT / relative).read_text(encoding="utf-8")
    literals = re.findall(r'\{[^{}]*"spent"\s*:\s*20[^{}]*\}', text)

    # A document may also quote the old, schema-less literal while explaining why it is
    # refused; what it must contain is at least one literal the probe's own reader accepts.
    valid = [literal for literal in literals if json.loads(literal).get("schema") == BUDGET_SCHEMA]
    assert valid, (
        f"{relative} documents no {BUDGET_SCHEMA} literal with spent 20; a literal the probe "
        f"reader refuses cannot be the L1 starting budget (found {literals})"
    )
    for index, literal in enumerate(valid):
        path = tmp_path / f"documented-budget-{index}.json"
        path.write_text(literal + "\n", encoding="utf-8")
        assert probe.read_probe_budget(path) == 20
