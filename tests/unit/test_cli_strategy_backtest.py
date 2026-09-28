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
from math import nan
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import READ_AT, REVERSAL, write_strategy_corpus
from typer.testing import CliRunner

from openalpha_cn import strategy_view
from openalpha_cn.backtest.execution import CostSchedule
from openalpha_cn.backtest.strategy_backtest import limitation_codes_for
from openalpha_cn.cli import STRATEGY_EXIT, PanelExit, app
from openalpha_cn.panel_ingest import session_publication_instant
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
    assert set(body["limitations"]) == set(limitation_codes_for("static"))
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


@pytest.mark.parametrize(
    "flag",
    ["--commission-rate", "--minimum-commission", "--transfer-fee-rate", "--stamp-duty-rate"],
)
def test_a_negative_cost_exits_bad_request(runtime: tuple[Path, GeneratedPanel], flag: str) -> None:
    """A negative fee would make the backtest look better; it is refused, not priced."""
    root, panel = runtime
    outcome = runner.invoke(app, [*_arguments(root, panel, flag, "-0.001"), "--json"])

    assert outcome.exit_code == int(PanelExit.bad_request), outcome.output
    assert json.loads(outcome.stdout)["exit_code"] == int(PanelExit.bad_request)


def test_the_sdk_refuses_a_negative_cost_as_a_request_error(
    runtime: tuple[Path, GeneratedPanel],
) -> None:
    """The SDK takes a `CostSchedule` object; one built without validation is still refused."""
    root, panel = runtime
    rebate = CostSchedule.model_construct(
        commission_rate=Decimal("-0.001"),
        minimum_commission=Decimal("5.00"),
        transfer_fee_rate=Decimal("0"),
        sell_stamp_duty_rate=Decimal("0.0005"),
    )
    with pytest.raises(StrategyRequestError, match="negative"):
        OpenAlphaSDK(runtime_dir=root).run_strategy_backtest(**_sdk_arguments(panel), costs=rebate)


def test_a_refusal_raised_while_the_inputs_are_assembled_is_blocked_not_internal(
    runtime: tuple[Path, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored score the book's own `ScoreRow` refuses (here a NaN) is a refusal, exit 1.

    The stored tier is replaced at the one seam that reads it, so every other step -- the
    orientation, the `ScoreRow` constructor that raises, the envelope -- is the shipped code.
    Before the fix the `StrategyBacktestError` escaped `backtest_strategy`'s refusal boundary
    and the command exited `internal_error`.
    """
    root, panel = runtime

    def not_a_number(*_: object) -> list[strategy_view._Observed]:
        instant = session_publication_instant(panel.sessions[1])
        return [
            strategy_view._Observed(
                subject=panel.securities[0], as_of=instant, value=nan, coverage="computed"
            )
        ]

    monkeypatch.setattr(strategy_view, "_tier_rows", not_a_number)
    outcome = runner.invoke(app, [*_arguments(root, panel), "--json"])

    assert outcome.exit_code == int(PanelExit.unhealthy), outcome.output
    assert "non-finite" in json.loads(outcome.stdout)["detail"]


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


TRAILING_IC: Final[dict[str, Any]] = {
    "components": [[REVERSAL.qualified_key, "raw"]],
    "ic_window_sessions": 5,
    "min_ic_observations": 1,
    "ic_method": "spearman",
    "horizon_sessions": 1,
    "negative_ic": "keep_sign",
    "min_ic_securities": 3,
}
WALK_FORWARD: Final[dict[str, Any]] = {
    "family": "cross_sectional_rank",
    "features": [f"{REVERSAL.qualified_key}@raw"],
    "seed": 0,
    "code_commit": "abcdef1234567",
    "train_sessions": 5,
    "refit_every_sessions": 2,
    "embargo_sessions": 1,
    "horizon_sessions": 1,
}


@pytest.mark.parametrize(
    ("source", "kind", "held"),
    [
        ({"trailing_ic": TRAILING_IC}, "trailing_ic", [True, False, False, False]),
        ({"walk_forward": WALK_FORWARD}, "walk_forward", [True, True, False, False]),
    ],
)
def test_the_sdk_takes_a_dynamic_source_as_plain_configuration(
    runtime: tuple[Path, GeneratedPanel], source: dict[str, Any], kind: str, held: list[bool]
) -> None:
    """`V2-P6-014`: the research grid passes a source as JSON-shaped data, lists and all."""
    root, panel = runtime
    arguments = {**_sdk_arguments(panel), "components": (), "rebalance_every_sessions": 2}
    result = OpenAlphaSDK(runtime_dir=root).run_strategy_backtest(**arguments, **source)

    assert result.source.kind == kind
    assert [period.held for period in result.periods] == held
    assert set(result.limitations) == set(limitation_codes_for(kind))
    echoed = json.loads(json.dumps(backtest_view(result), sort_keys=True))["source"][kind]
    assert echoed["horizon_sessions"] == 1


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"trailing_ic": {**TRAILING_IC, "ic_method": "kendall"}}, "ic_method"),
        (
            {
                "walk_forward": {
                    **WALK_FORWARD,
                    "features": [f"{REVERSAL.qualified_key}@neutralized"],
                }
            },
            "neutralized",
        ),
        ({"walk_forward": {**WALK_FORWARD, "embargo_sessions": 0}}, "embargo"),
        ({"trailing_ic": TRAILING_IC, "walk_forward": WALK_FORWARD}, "exactly one"),
    ],
)
def test_the_sdk_refuses_a_dynamic_source_that_cannot_be_put_as_a_request_error(
    runtime: tuple[Path, GeneratedPanel], source: dict[str, Any], message: str
) -> None:
    root, panel = runtime
    arguments = {**_sdk_arguments(panel), "components": ()}
    with pytest.raises(StrategyRequestError, match=message):
        OpenAlphaSDK(runtime_dir=root).run_strategy_backtest(**arguments, **source)


def test_the_sdk_answers_a_per_instant_ic_series_with_its_census(
    runtime: tuple[Path, GeneratedPanel],
) -> None:
    """`V2-P6-014` for `V2-P6-008`: one point per prediction day, as JSON-shaped data."""
    root, panel = runtime
    series = OpenAlphaSDK(runtime_dir=root).factor_ic_series(
        factor=REVERSAL.qualified_key,
        tier="raw",
        horizon_sessions=1,
        ic_method="spearman",
        min_securities=3,
        start=panel.sessions[1],
        end=panel.sessions[7],
        as_of=READ_AT,
        exchange=EXCHANGE,
    )
    body = json.loads(json.dumps(strategy_view.ic_series_view(series)))

    assert [point["prediction_day"] for point in body["points"]] == [
        day.isoformat() for day in panel.sessions[1:8]
    ]
    assert {point["coverage"] for point in body["points"]} == {"measured"}
    assert all(
        point["n_securities"] == point["census"]["admitted_count"] for point in body["points"]
    )


def test_the_sdk_refuses_an_ic_series_that_cannot_be_asked_as_a_request_error(
    runtime: tuple[Path, GeneratedPanel],
) -> None:
    root, panel = runtime
    with pytest.raises(StrategyRequestError, match="ic_method"):
        OpenAlphaSDK(runtime_dir=root).factor_ic_series(
            factor=REVERSAL.qualified_key,
            tier="raw",
            horizon_sessions=1,
            ic_method="kendall",
            min_securities=3,
            start=panel.sessions[1],
            end=panel.sessions[7],
            as_of=READ_AT,
            exchange=EXCHANGE,
        )


def test_every_strategy_view_fault_has_a_row_in_the_exit_table() -> None:
    """Looked up by `reason`, so a fault added with no row raises at the boundary, not here."""
    reasons = {subclass.reason for subclass in StrategyViewError.__subclasses__()}

    assert reasons == {"bad_request", "panel_unreadable", "blocked"}
    assert reasons <= set(STRATEGY_EXIT)
    assert StrategyRequestError.reason == "bad_request"
    assert StrategyPanelUnreadableError.reason == "panel_unreadable"
    assert StrategyRunBlockedError.reason == "blocked"
    assert STRATEGY_EXIT["blocked"] is PanelExit.unhealthy
