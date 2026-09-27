"""`openalpha strategy backtest` and `OpenAlphaSDK.run_strategy_backtest` are one answer
(`V2-P6-007`).

Both faces resolve through `strategy_view.strategy_request` and run through
`strategy_view.backtest_strategy`; this file drives them from where a user stands -- a
`CliRunner` and an `OpenAlphaSDK` -- over one generated runtime directory and requires the same
bytes, then drives the refusals the command must not answer with an empty success.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus
from typer.testing import CliRunner

from openalpha_cn.backtest.strategy_backtest import STRATEGY_BACKTEST_LIMITATION_CODES
from openalpha_cn.cli import STRATEGY_EXIT, PanelExit, app
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.strategy_view import (
    StrategyPanelUnreadableError,
    StrategyRequestError,
    StrategyRunBlockedError,
    StrategyViewError,
    backtest_view,
)

runner: Final[CliRunner] = CliRunner()


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-cli")
    return root, write_strategy_corpus(root)


@pytest.fixture(scope="module")
def late_runtime(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-cli-late")
    return root, write_strategy_corpus(root, late=True)


def _arguments(root: Path, panel: GeneratedPanel, *extra: str) -> list[str]:
    return [
        "strategy",
        "backtest",
        "--component",
        f"{REVERSAL.qualified_key}@raw",
        "--combine",
        "zscore_sum",
        "--start",
        panel.sessions[1].isoformat(),
        "--end",
        panel.sessions[-1].isoformat(),
        "--rebalance-every-sessions",
        "3",
        "--holding-count",
        "3",
        "--as-of",
        READ_AT.isoformat(),
        "--exchange",
        EXCHANGE,
        "--runtime-dir",
        str(root),
        *extra,
    ]


def _sdk_arguments(panel: GeneratedPanel) -> dict[str, Any]:
    return {
        "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
        "combine": "zscore_sum",
        "start": panel.sessions[1],
        "end": panel.sessions[-1],
        "as_of": READ_AT,
        "exchange": EXCHANGE,
        "rebalance_every_sessions": 3,
        "holding_count": 3,
        "buffer_rank": None,
        "max_industry_weight": None,
    }


def test_the_command_line_and_the_sdk_answer_one_request_with_the_same_bytes(
    runtime: tuple[Path, GeneratedPanel],
) -> None:
    root, panel = runtime
    outcome = runner.invoke(app, [*_arguments(root, panel), "--json"])

    assert outcome.exit_code == 0, outcome.output
    sdk_result = OpenAlphaSDK(runtime_dir=root).run_strategy_backtest(**_sdk_arguments(panel))
    assert json.loads(outcome.stdout) == json.loads(
        json.dumps(backtest_view(sdk_result), ensure_ascii=False, sort_keys=True)
    )
    body = json.loads(outcome.stdout)
    assert len(body["periods"]) == 3
    assert set(body["limitations"]) == STRATEGY_BACKTEST_LIMITATION_CODES
    assert body["spec"]["costs"]["commission_rate"] == "0.00025"


def test_the_text_answer_prints_one_line_per_period(runtime: tuple[Path, GeneratedPanel]) -> None:
    root, panel = runtime
    outcome = runner.invoke(app, _arguments(root, panel))

    assert outcome.exit_code == 0, outcome.output
    assert outcome.stdout.count(" net ") == 3
    assert "limitations:" in outcome.stdout


def test_a_look_ahead_build_exits_unhealthy_with_a_json_refusal(
    late_runtime: tuple[Path, GeneratedPanel],
) -> None:
    root, panel = late_runtime
    outcome = runner.invoke(app, [*_arguments(root, panel), "--json"])

    assert outcome.exit_code == int(PanelExit.unhealthy)
    refusal = json.loads(outcome.stdout)
    assert refusal["exit_code"] == int(PanelExit.unhealthy)
    assert "not visible" in refusal["detail"]


def test_the_sdk_raises_the_same_refusal_the_command_exits_on(
    late_runtime: tuple[Path, GeneratedPanel],
) -> None:
    root, panel = late_runtime
    with pytest.raises(StrategyRunBlockedError, match="not visible"):
        OpenAlphaSDK(runtime_dir=root).run_strategy_backtest(**_sdk_arguments(panel))


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--transform", "cross_section_standard/v1"), "--transform"),
        (("--max-industry-weight", "abc"), "--max-industry-weight"),
        (("--buffer-rank", "1"), "buffer_rank"),
    ],
)
def test_a_request_that_cannot_be_put_exits_bad_request(
    runtime: tuple[Path, GeneratedPanel], extra: tuple[str, ...], message: str
) -> None:
    root, panel = runtime
    outcome = runner.invoke(app, [*_arguments(root, panel, *extra), "--json"])

    assert outcome.exit_code == int(PanelExit.bad_request), outcome.output
    assert message in json.loads(outcome.stdout)["detail"]


def test_a_malformed_component_exits_bad_request(runtime: tuple[Path, GeneratedPanel]) -> None:
    root, panel = runtime
    arguments = _arguments(root, panel)
    arguments[arguments.index("--component") + 1] = "reversal_1d/v1"
    outcome = runner.invoke(app, arguments)

    assert outcome.exit_code == int(PanelExit.bad_request)


def test_an_empty_runtime_directory_is_panel_unreadable(tmp_path: Path) -> None:
    arguments = [
        "strategy",
        "backtest",
        "--component",
        f"{REVERSAL.qualified_key}@raw",
        "--combine",
        "rank_sum",
        "--start",
        date(2026, 1, 6).isoformat(),
        "--end",
        date(2026, 1, 16).isoformat(),
        "--rebalance-every-sessions",
        "3",
        "--holding-count",
        "3",
        "--as-of",
        READ_AT.isoformat(),
        "--exchange",
        EXCHANGE,
        "--runtime-dir",
        str(tmp_path),
        "--json",
    ]
    outcome = runner.invoke(app, arguments)

    assert outcome.exit_code == int(PanelExit.unhealthy), outcome.output
    assert json.loads(outcome.stdout)["exit_code"] == int(PanelExit.unhealthy)


def test_every_strategy_view_fault_has_a_row_in_the_exit_table() -> None:
    """Looked up by `reason`, so a fault added with no row raises at the boundary, not here."""
    reasons = {subclass.reason for subclass in StrategyViewError.__subclasses__()}

    assert reasons == {"bad_request", "panel_unreadable", "blocked"}
    assert reasons <= set(STRATEGY_EXIT)
    assert StrategyRequestError.reason == "bad_request"
    assert StrategyPanelUnreadableError.reason == "panel_unreadable"
    assert StrategyRunBlockedError.reason == "blocked"
    assert STRATEGY_EXIT["blocked"] is PanelExit.unhealthy
