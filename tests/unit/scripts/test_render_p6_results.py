"""The P6 results renderer prints what the ledger says and refuses what it cannot vouch for
(V2-P6-010).

`docs/research/p6-results.md` quotes this script's output, so a wrong count or a silently
defaulted verdict here would be a wrong number in the research record. Each test pins one rule
the independent review of that report asked for.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "render_p6_results.py"


def _renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_p6_results_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(config_id: str, result: dict[str, Any], stage: str = "discovery") -> dict[str, Any]:
    config = {
        "components": [["momentum_20_sessions/v1", "raw", "1"]],
        "holding_count": 50,
        "rebalance_every_sessions": 20,
        "buffer_rank": None,
        "max_industry_weight": None,
    }
    return {
        "stage": stage,
        "kind": "measurement",
        "config_id": config_id,
        "config": config,
        "result": result,
    }


MEASURED = {
    "information_ratio": 0.5,
    "annualized_mean_net_excess": 0.02,
    "p_excess": 0.3,
    "mean_turnover": 0.4,
    "reported_information_ratio": 0.1,
    "reported_compounded_annual_relative_return": 0.01,
}


def _verdict() -> dict[str, Any]:
    return {
        "registration_commit": "a" * 40,
        "registration_sha256": "b" * 64,
        "holdout_config_id": "c" * 64,
        "statement": "measured once, claimed at 2026-10-07T00:44:49+00:00",
        "criteria": {
            "one_sided_p_excess": {"value": 0.7, "threshold": 0.05, "passed": False},
        },
        "verdict": "不通过",
        "wording": "候选",
    }


def _holdout(period_count: int, excluded: int) -> dict[str, Any]:
    return _row(
        "h1",
        {
            **MEASURED,
            "period_count": period_count,
            "excluded_incomplete_periods": excluded,
            "excess_benchmark": "equal_weight_all_a_held",
            "reported_benchmark": "000905.SH",
        },
        stage="holdout",
    )


def test_the_holdout_line_counts_only_the_complete_periods_the_criteria_used() -> None:
    lines = _renderer().holdout_lines([_holdout(34, 1)], _verdict())

    text = "\n".join(lines)
    assert "33 个完整期" in text
    assert "另有 1 个不完整的末期" in text
    assert "34" not in text.split("测量（", 1)[1].split("）", 1)[0]


def test_a_ledger_with_two_holdout_measurements_is_refused() -> None:
    with pytest.raises(SystemExit, match="2 holdout measurements"):
        _renderer().holdout_lines([_holdout(34, 1), _holdout(34, 1)], _verdict())


def test_a_measured_row_the_fdr_table_does_not_name_is_refused_not_shown_as_not_rejected() -> None:
    renderer = _renderer()
    with pytest.raises(SystemExit, match="does not name x1"):
        renderer.stage_table([_row("x1", MEASURED)], {}, None, renderer.component)


def test_a_refused_row_is_shown_as_withheld_from_the_fdr_table() -> None:
    renderer = _renderer()
    refused = _row("r1", {"error": "StrategyRunBlockedError: nothing to rank"})

    lines = renderer.stage_table([refused], {}, None, renderer.component)

    cells = [cell.strip() for cell in lines[-1].strip("|").split("|")]
    assert cells[1] == "拒绝"
    assert cells[4] == "withheld"
    assert cells[5] == "否"


def test_a_reported_row_prints_its_q_value_verdict_and_the_reported_benchmark() -> None:
    renderer = _renderer()
    fdr = {"x1": {"hypothesis_id": "x1", "q_value": 0.0123, "rejected": True}}

    lines = renderer.stage_table([_row("x1", MEASURED)], fdr, None, renderer.component)

    cells = [cell.strip() for cell in lines[-1].strip("|").split("|")]
    assert cells[1:] == ["0.500", "0.0200", "0.3000", "0.0123", "是", "0.400", "0.100", "0.0100"]


def test_the_rendered_numbers_are_the_ledger_numbers(tmp_path: Path) -> None:
    renderer = _renderer()
    ledger = tmp_path / "p6-ledger.jsonl"
    ledger.write_text(json.dumps(_holdout(10, 0)) + "\n")

    lines = renderer.holdout_lines(renderer.rows(ledger), _verdict())

    text = "\n".join(lines)
    assert "信息比率 0.500" in text
    assert "| one_sided_p_excess | 0.700000 | 0.050000 | 否 |" in text
    assert "**不通过**（措辞：候选）" in text
