"""`scripts/p7_register.py`: the `V2-P7-006b` registration's body and writer.

The registration is written once, by hand, after the smoke's phase 1; the body is frozen here as
literals so that a change to a default in `src/` cannot move the registered numbers without this
file going red. The writer's refusals are run against throwaway repositories built the way
`test_p7_smoke_env.py` builds its source checkout: every git call drops the inherited `GIT_*`
variables and pins the commit time.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest
import research_repo
import test_daily_selection as tds

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


register = _script("p7_register")
env = _script("p7_smoke_env")
grid = _script("grid")
registry = _script("registry")

from openalpha_cn import (  # noqa: E402
    strategy_survivor,
    strategy_survivor_continuation,
    strategy_survivor_forward,
)

EPOCH: Final[date] = date(2026, 12, 18)
REGISTRATION: Final[str] = "docs/research/p7-registration.json"
READING: Final[Path] = ROOT / "docs/research/p7-readings/v2-p7-024-validation.registration.json"

BOOK: Final[dict[str, Any]] = {
    "schema_version": "survivor-book-config/v1",
    "holding_count": 10,
    "capital": "350000",
    "boards": ["main"],
    "liquidity_drop_fraction": "0.30",
    "min_listing_sessions": 250,
    "loser_decile_count": 2,
    "entry_floor_fraction": "0.25",
    "near_exit_fraction": "0.10",
    "cooldown_sessions": 20,
    "max_replacements_per_session": 2,
    "buy_order_expiry_sessions": 3,
    "lot_size": 100,
    "cost_reserve_fraction": "0.005",
    "selection_rule": "seeded_hash/v1",
    "seed": 20_260_926,
    "composite_factors": [
        "residual_vol_60/v1",
        "return_vol_60/v1",
        "turnover_60/v1",
        "downside_vol_60/v1",
    ],
}
"""`SurvivorBookConfig(seed=20260926)` in its stored form (plan section 1.1), written out by hand:
a changed default in `src/` must fail here rather than move the registered book."""

ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
    "epoch_session": "2026-12-18",
}
"""The design's adapter block with the planned epoch (plan section 1.1)."""

PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 36,
    "bootstrap_samples": 100_000,
    "random_seed": 20_260_926,
    "q": 0.1,
}
"""Part E's recommended protocol (plan section 1.2), written out by hand."""

POWER: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-power/v1",
    "test_level": 0.05,
    "target_power": 0.80,
    "critical_value": "t",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 30,
    "planned": [
        {
            "horizon": 20,
            "metric": "rank_ic",
            "planning_sd": 0.18,
            "planning_means": [0.037, 0.05, 0.10],
            "sd_source": "研究期约 0.14 与验证读观测 0.182 取较大者向下取两位；单次观测不是规划值",
        }
    ],
}
"""The power plan with the user's D-1/D-2 values (plan section 1.2), written out by hand."""

STOP_RULE: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-stop-rule/v1",
    "metric": "rank_ic",
    "horizon": 20,
    "mean_at_or_below": 0.02,
    "tail_metric": "ew_minus_worst_decile",
    "tail_mean_at_or_below": 0.0,
    "interim_periods": 18,
    "interim_mean_at_or_below": 0.0,
}
"""`adopted_forward_stop_rule()` (plan section 1.2), written out by hand."""

CRITERIA_KEYS: Final[frozenset[str]] = frozenset(
    {"primary", "stop_rule", "interim", "secondary_family", "evidence", "run_in", "eligibility", "wording"}
)
"""Plan section 1.3's eight criteria keys."""

FORBIDDEN_WORDING: Final[tuple[str, ...]] = ("proven", "验证有效", "已证实", "已验证", "证实有效")


# --- Task 1: the body ---------------------------------------------------------------------------


def test_the_registration_body_is_the_frozen_one() -> None:
    config, criteria, settings = register.registration_body(EPOCH)

    assert config == env.smoke_configuration(EPOCH)
    assert config["kind"] == "survivor_book/v1"
    assert config["survivor_book"] == BOOK
    assert config["adapter"] == ADAPTER
    assert settings == {
        **grid.protocol_settings(),
        "survivor_forward_protocol": PROTOCOL,
        "survivor_forward_power": POWER,
        "survivor_forward_stop_rule": STOP_RULE,
    }
    assert settings["survivor_forward_protocol"] == PROTOCOL
    assert settings["survivor_forward_power"] == POWER
    assert settings["survivor_forward_stop_rule"] == (
        strategy_survivor_forward.adopted_forward_stop_rule()
    )
    assert settings["survivor_forward_stop_rule"] == STOP_RULE
    assert (
        settings["random_seed"]
        == settings["survivor_forward_protocol"]["random_seed"]
        == config["survivor_book"]["seed"]
        == 20_260_926
    )
    assert "smoke_run" not in settings
    assert "survivor_continuation" not in settings
    assert set(criteria) == CRITERIA_KEYS


def test_the_body_reads_back() -> None:
    config, _, settings = register.registration_body(EPOCH)
    seed = settings["random_seed"]
    assert isinstance(seed, int)

    strategy_survivor.read_survivor_registration(config, seed=seed)
    protocol = strategy_survivor_forward.read_forward_protocol(settings)
    assert strategy_survivor_forward.read_forward_power(settings, protocol=protocol) is not None
    assert strategy_survivor_forward.read_forward_stop_rule(settings, protocol=protocol) is not None
    assert strategy_survivor_continuation.read_survivor_continuation(settings) is None


def test_the_scoring_definition_is_the_one_read_once() -> None:
    reading = json.loads(READING.read_text(encoding="utf-8"))
    config, _, _ = register.registration_body(EPOCH)
    adapter = config["adapter"]
    book = config["survivor_book"]

    assert adapter["exchange"] == reading["config"]["exchange"] == "SSE"
    assert adapter["transform"] == reading["config"]["transform"] == "cross_section_standard/v1"
    assert adapter["min_coverage"] == reading["config"]["min_coverage"] == 0.5
    assert adapter["max_carry_sessions"] == reading["config"]["max_carry_sessions"] == 250
    assert book["loser_decile_count"] == reading["request"]["loser_decile_count"] == 2


def test_the_criteria_name_the_run_in_and_the_unknown_rule() -> None:
    _, criteria, _ = register.registration_body(EPOCH)

    assert set(criteria) == CRITERIA_KEYS
    assert "22" in criteria["run_in"]
    assert "unknown" in criteria["eligibility"]
    for value in criteria.values():
        for forbidden in FORBIDDEN_WORDING:
            assert forbidden not in value
    assert "候选" in criteria["wording"]
