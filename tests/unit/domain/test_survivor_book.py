"""The survivor book's vocabulary and configuration (`V2-P7-005`).

What is pinned here is that the configuration is a *contract* -- frozen, versioned, content-
addressed, with no default for the seed -- and that the two quantities every later layer leans on,
the share of a list a fraction covers and the lots a budget buys, round the way the design says.
"""

from decimal import Decimal
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import security_board
from openalpha_cn.domain.survivor_book import (
    DEFAULT_COMPOSITE_FACTORS,
    ELIGIBILITY_REASON_ORDER,
    Board,
    EligibilityVerdict,
    RankedName,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
    lot_quantity,
)


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=7, **overrides)  # type: ignore[arg-type]


def test_the_defaults_are_the_values_the_design_names() -> None:
    held = config()

    assert held.schema_version == "survivor-book-config/v1"
    assert held.holding_count == 10
    assert held.capital == Decimal("350000")
    assert held.boards == ("main", "growth")
    assert held.liquidity_drop_fraction == Decimal("0.30")
    assert held.min_listing_sessions == 250
    assert held.loser_decile_count == 2
    assert held.entry_floor_fraction == Decimal("0.25")
    assert held.near_exit_fraction == Decimal("0.10")
    assert held.cooldown_sessions == 20
    assert held.max_replacements_per_session == 2
    assert held.buy_order_expiry_sessions == 3
    assert held.lot_size == 100
    assert held.cost_reserve_fraction == Decimal("0.005")
    assert held.selection_rule == "seeded_hash/v1"
    assert held.composite_factors == DEFAULT_COMPOSITE_FACTORS
    assert DEFAULT_COMPOSITE_FACTORS == (
        "residual_vol_60/v1",
        "return_vol_60/v1",
        "turnover_60/v1",
        "downside_vol_60/v1",
    )


def test_the_seed_has_no_default_so_a_registration_has_to_name_it() -> None:
    with pytest.raises(ValidationError, match="seed"):
        SurvivorBookConfig()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="seed"):
        SurvivorBookConfig(seed=-1)


def test_the_configuration_is_frozen_and_refuses_unknown_fields() -> None:
    held = config()

    with pytest.raises(ValidationError):
        held.holding_count = 11  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SurvivorBookConfig(seed=1, surprise=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"holding_count": 0}, "holding_count"),
        ({"holding_count": 51}, "holding_count"),
        ({"capital": Decimal("0")}, "capital"),
        ({"capital": Decimal("-1")}, "capital"),
        ({"min_listing_sessions": -1}, "min_listing_sessions"),
        ({"near_exit_fraction": Decimal("1")}, "near_exit_fraction"),
        ({"liquidity_drop_fraction": Decimal("1")}, "liquidity_drop_fraction"),
        ({"loser_decile_count": 10}, "loser_decile_count"),
        ({"entry_floor_fraction": Decimal("-0.1")}, "entry_floor_fraction"),
        ({"cooldown_sessions": -1}, "cooldown_sessions"),
        ({"max_replacements_per_session": 0}, "max_replacements_per_session"),
        ({"buy_order_expiry_sessions": 0}, "buy_order_expiry_sessions"),
        ({"lot_size": 0}, "lot_size"),
        ({"cost_reserve_fraction": Decimal("0.1")}, "cost_reserve_fraction"),
        ({"boards": ()}, "boards"),
        ({"boards": ("main", "main")}, "boards"),
        ({"boards": ("main", "nasdaq")}, "boards"),
        ({"composite_factors": ()}, "composite_factors"),
        ({"composite_factors": ("a/v1", "a/v1")}, "composite_factors"),
        ({"selection_rule": "highest_composite/v1"}, "selection_rule"),
    ],
)
def test_a_value_outside_what_the_design_allows_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        config(**overrides)


def test_the_identity_is_a_content_address_that_moves_with_every_parameter() -> None:
    base = config()

    assert base.config_id.startswith("sbc_")
    assert len(base.config_id) == len("sbc_") + 24
    assert config().config_id == base.config_id
    for changed in (
        config(holding_count=9),
        config(capital=Decimal("350001")),
        config(boards=("main",)),
        config(cooldown_sessions=21),
        SurvivorBookConfig(seed=8),
    ):
        assert changed.config_id != base.config_id


def test_the_stored_form_reads_back_without_the_computed_identity() -> None:
    base = config()
    stored = base.model_dump_json(exclude_computed_fields=True)

    assert SurvivorBookConfig.model_validate_json(stored) == base
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SurvivorBookConfig.model_validate_json(base.model_dump_json())


def test_the_sizing_budget_is_an_equal_share_less_the_cost_reserve() -> None:
    held = config()

    assert held.sizing_budget() == Decimal("34825")
    assert held.sizing_budget(Decimal("400000")) == Decimal("39800")
    assert config(cost_reserve_fraction=Decimal("0")).sizing_budget() == Decimal("35000")


@pytest.mark.parametrize(
    ("count", "fraction", "expected"),
    [
        (10, Decimal("0.30"), 3),
        (9, Decimal("0.30"), 2),
        (3, Decimal("0.30"), 0),
        (0, Decimal("0.30"), 0),
        (20, Decimal("0"), 0),
        (7, Decimal("0.999"), 6),
    ],
)
def test_a_fraction_of_a_list_rounds_down(count: int, fraction: Decimal, expected: int) -> None:
    assert floor_share(count, fraction) == expected


@pytest.mark.parametrize(
    ("budget", "price", "expected"),
    [
        (Decimal("34825"), Decimal("10"), 3400),
        (Decimal("34825"), Decimal("348.25"), 100),
        (Decimal("34825"), Decimal("348.26"), 0),
        (Decimal("999"), Decimal("10"), 0),
        (Decimal("1000"), Decimal("10"), 100),
        (Decimal("0"), Decimal("10"), 0),
    ],
)
def test_a_budget_buys_whole_lots_and_no_more(
    budget: Decimal, price: Decimal, expected: int
) -> None:
    assert lot_quantity(budget, price, 100) == expected


def test_a_lot_is_not_priced_at_zero() -> None:
    with pytest.raises(ValueError, match="price must be positive"):
        lot_quantity(Decimal("1000"), Decimal("0"), 100)


def facts(**overrides: object) -> SecurityFacts:
    settings: dict[str, object] = {
        "ts_code": "600000.SH",
        "board": "main",
        "listed_sessions": 300,
        "special_treatment": False,
        "suspended": False,
        "close": Decimal("10"),
        "avg_amount": Decimal("100"),
        "oriented_values": (1.0, 2.0, None, 4.0),
    }
    settings.update(overrides)
    return SecurityFacts(**settings)  # type: ignore[arg-type]


def test_a_security_fact_keeps_a_missing_factor_value_as_none() -> None:
    assert facts().oriented_values == (1.0, 2.0, None, 4.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"oriented_values": (float("nan"),)},
        {"oriented_values": (float("inf"),)},
        {"close": Decimal("0")},
        {"avg_amount": Decimal("-1")},
        {"listed_sessions": -1},
        {"board": "nasdaq"},
        {"ts_code": ""},
    ],
)
def test_a_security_fact_that_cannot_be_true_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        facts(**overrides)


def test_a_verdict_is_eligible_exactly_when_it_has_no_reason() -> None:
    assert EligibilityVerdict(ts_code="600000.SH", composite=1.0).eligible
    assert not EligibilityVerdict(ts_code="600000.SH", reasons=("suspended",)).eligible


def test_the_reason_order_is_the_order_the_design_lists_them_in() -> None:
    assert ELIGIBILITY_REASON_ORDER == (
        "board_not_allowed",
        "listing_too_recent",
        "special_treatment",
        "suspended",
        "missing_factor_value",
        "unaffordable_lot",
        "illiquid",
    )


def test_a_ranked_name_starts_at_rank_one_and_has_a_positive_close() -> None:
    RankedName(ts_code="600000.SH", composite=0.5, rank=1, close=Decimal("10"))
    with pytest.raises(ValidationError):
        RankedName(ts_code="600000.SH", composite=0.5, rank=0, close=Decimal("10"))
    with pytest.raises(ValidationError):
        RankedName(ts_code="600000.SH", composite=0.5, rank=1, close=Decimal("0"))


def test_a_retyped_decimal_is_a_different_identity_so_a_registration_is_read_back_verbatim() -> (
    None
):
    # Numerically the same, but the stored form differs ("0.3" against the default "0.30").
    assert config(liquidity_drop_fraction=Decimal("0.3")).config_id != config().config_id
    assert config(boards=("growth", "main")).config_id != config().config_id


def test_the_board_vocabulary_is_the_one_the_execution_policy_answers_in() -> None:
    answered = get_args(get_type_hints(security_board)["return"])

    assert set(get_args(Board)) == set(answered)


@pytest.mark.parametrize(
    ("reasons", "composite"),
    [
        (("suspended", "board_not_allowed"), None),
        (("suspended", "suspended"), None),
        ((), None),
    ],
)
def test_a_verdict_must_list_unique_reasons_in_order_and_an_eligible_one_must_be_scored(
    reasons: tuple[str, ...], composite: float | None
) -> None:
    with pytest.raises(ValidationError):
        EligibilityVerdict(ts_code="600000.SH", reasons=reasons, composite=composite)  # type: ignore[arg-type]


def test_the_helpers_refuse_inputs_that_would_give_a_negative_or_nonsense_count() -> None:
    with pytest.raises(ValueError, match="budget must not be negative"):
        lot_quantity(Decimal("-2000"), Decimal("10"), 100)
    with pytest.raises(ValueError, match="price must be positive"):
        lot_quantity(Decimal("1000"), Decimal("NaN"), 100)
    with pytest.raises(ValueError, match="lot_size must be at least 1"):
        lot_quantity(Decimal("1000"), Decimal("10"), 0)
    with pytest.raises(ValueError, match="fraction must lie between 0 and 1"):
        floor_share(10, Decimal("1.5"))
    with pytest.raises(ValueError, match="fraction must lie between 0 and 1"):
        floor_share(10, Decimal("-0.35"))
    with pytest.raises(ValueError, match="count must not be negative"):
        floor_share(-1, Decimal("0.3"))
    with pytest.raises(ValueError, match="equity must be positive"):
        config().sizing_budget(Decimal("-100000"))
