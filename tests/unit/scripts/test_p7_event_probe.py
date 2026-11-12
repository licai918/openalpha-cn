"""`scripts/p7_event_probe.py`: the V2-P7-010 event-interface probe (plan Task 2.2).

The probe is tooling only. Its default mode is `--dry-run`: it prints the planned Tushare
requests and sends nothing. Its `--live` mode needs the user's explicit go and an environment
token, and the only request budget it may spend is `PROBE_CAP` = 300, enforced by reusing
`scripts/p7_smoke_requests.py`'s capped counter. A report is metadata only -- interface names,
permission answers, row and page counts, field names, the earliest date seen and timestamp-like
field names -- and never a row value. Every transport below is scripted, so nothing reaches the
network.
"""

from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
TODAY: Final[date] = date(2026, 11, 12)
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


def _call(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "api_name": "moneyflow",
        "params": {"trade_date": "20261111"},
        "purpose": "full_day",
    }
    values.update(overrides)
    return probe.ProbeCall(**values)


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


# --- the cap ------------------------------------------------------------------------------------


def test_the_cap_stops_the_301st_request_unsent() -> None:
    plan = tuple(_call(params={"trade_date": f"{index:08d}"}) for index in range(1, 302))
    transport = Recording()

    findings = probe.run_probe(plan, transport=transport)

    assert len(transport.posted) == probe.PROBE_CAP == 300
    assert len(findings) == probe.PROBE_CAP
    sent = [payload["params"]["trade_date"] for payload in transport.posted]
    assert sent == [f"{index:08d}" for index in range(1, 301)]
    assert "00000301" not in sent  # the request that would pass the cap was never even built

    stopped = probe.run_probe(plan, transport=Recording(), cap=2)
    assert len(stopped) == 2


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

    findings = probe.run_probe(plan, transport=transport)
    report = probe.render_report(findings, spent=len(findings))

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


# --- refusals are findings ----------------------------------------------------------------------


def test_a_permission_error_is_a_finding_not_a_crash() -> None:
    refused = Recording([{"code": 40101, "msg": "your token is wrong", "data": None}])

    findings = probe.run_probe((_call(),), transport=refused)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.api_name == "moneyflow"
    assert finding.permitted is False
    assert finding.error_code == "40101"
    assert finding.rows == 0
    assert finding.pages == 0
    assert finding.fields == ()
    assert finding.earliest_date_seen is None
    assert "40101" in probe.render_report(findings, spent=1)

    failed = probe.run_probe((_call(),), transport=Refusing())
    assert failed[0].permitted is False
    assert failed[0].error_code == "OSError"


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

    findings = probe.run_probe((_call(),), transport=Recording())
    probe.render_report(findings, spent=len(findings))

    assert list(tmp_path.iterdir()) == []


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

    findings = probe.run_probe((_call(),), transport=transport)

    assert transport.posted[0]["token"] == "probe-token-from-the-environment"
    assert "probe-token-from-the-environment" not in probe.render_report(findings, spent=1)
