"""The survivor book's vocabulary and configuration (`V2-P7-005`, `V2-P7-017`, `V2-P7-027`).

What is pinned here is that the configuration is a *contract* -- frozen, versioned, content-
addressed, with no default for the seed -- and that the two quantities every later layer leans on,
the share of a list a fraction covers and the lots a budget buys, round the way the design says.
`V2-P7-017` adds the configuration's whole digest, which a paper-book snapshot carries.

`V2-P7-027` adds the plan-A entry veto (`entry_veto: "none" | "market_bearish_5/v1"`) as a v2
field, and its load-bearing rule is that adding it must move **nothing** about v1. So the v1
default's stored JSON, `config_id` and whole digest are pasted below as literals computed on the
base commit (`539bd06`, before the field existed): a v1 registration, journal key, paper-book
snapshot and reading must stay readable and identical, and a test that compared the new code
with itself could not notice a change.
"""

import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Final, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import security_board
from openalpha_cn.domain.survivor_book import (
    DEFAULT_COMPOSITE_FACTORS,
    ELIGIBILITY_REASON_ORDER,
    Board,
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
    lot_quantity,
    survivor_config_digest,
)
from openalpha_cn.domain.survivor_veto import VETO_RULE_ID


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=7, **overrides)  # type: ignore[arg-type]


V1_STORED_JSON: Final[str] = (
    '{"schema_version":"survivor-book-config/v1","holding_count":10,'
    '"capital":"350000","boards":["main"],"liquidity_drop_fraction":"0.30",'
    '"min_listing_sessions":250,"loser_decile_count":2,"entry_floor_fraction":"0.25",'
    '"near_exit_fraction":"0.10","cooldown_sessions":20,'
    '"max_replacements_per_session":2,"buy_order_expiry_sessions":3,"lot_size":100,'
    '"cost_reserve_fraction":"0.005","selection_rule":"seeded_hash/v1","seed":7,'
    '"composite_factors":["residual_vol_60/v1","return_vol_60/v1","turnover_60/v1",'
    '"downside_vol_60/v1"]}'
)
"""`SurvivorBookConfig(seed=7)`'s stored JSON at `539bd06`, before `V2-P7-027` added a field.

Computed on the base commit with `model_dump_json(exclude_computed_fields=True)` -- the bytes a
registration stores and the journal/paper-book identity addresses. plan A must not move them, so
this string is a measurement of the old code rather than of the new one.
"""

V1_CONFIG_ID: Final[str] = "sbc_d87a16142738e229764b5c66"
"""`SurvivorBookConfig(seed=7).config_id` at `539bd06` (same anchor as `V1_STORED_JSON`)."""

V1_CONFIG_DIGEST: Final[str] = "d87a16142738e229764b5c66a4d14d853c997abb62b7f43493143fa4bf2bad48"
"""The whole digest at `539bd06`; `V1_CONFIG_ID` is its first 24 digits."""


def test_the_v1_stored_form_and_identity_are_pinned_byte_for_byte() -> None:
    """Adding plan A's field must not move one v1 byte (`AGENTS.md` hard rule 3).

    The three literals above were measured on the base commit, so a serializer that merely
    agrees with itself -- the failure mode of computing an expected value from the code under
    test -- cannot make this pass.
    """
    held = config()

    assert held.schema_version == "survivor-book-config/v1"
    assert held.entry_veto == "none"
    assert held.model_dump_json(exclude_computed_fields=True) == V1_STORED_JSON
    assert held.config_id == V1_CONFIG_ID
    assert survivor_config_digest(held) == V1_CONFIG_DIGEST
    assert SurvivorBookConfig.model_validate_json(V1_STORED_JSON) == held


def test_the_v1_field_is_excluded_from_every_stored_form_not_only_the_identity() -> None:
    """`entry_veto` is not a v1 field: it must not appear in a dump, a digest or an identity.

    `survivor_config_digest` and `stable_model_id` both read
    `model_dump(mode="json", exclude_computed_fields=True)`; `strategy_survivor`'s registration
    check reads it too and compares it, as JSON text, with the block a registration stores. A
    field that leaked into the dump would make every stored v1 registration stop being its own
    stored form -- refused by name at step 1 of the daily command.
    """
    held = config()

    assert "entry_veto" not in held.model_dump(mode="json", exclude_computed_fields=True)
    assert "entry_veto" not in json.loads(held.model_dump_json(exclude_computed_fields=True))
    assert "entry_veto" not in json.loads(held.model_dump_json())


def test_the_versions_and_the_declared_veto_rule_are_their_own_closed_vocabularies() -> None:
    """The two `Literal`s are the contract a registration is read against."""
    versions = get_args(SurvivorBookConfig.model_fields["schema_version"].annotation)
    vetoes = get_args(SurvivorBookConfig.model_fields["entry_veto"].annotation)

    assert set(versions) == {"survivor-book-config/v1", "survivor-book-config/v2"}
    assert set(vetoes) == {"none", VETO_RULE_ID}


def test_a_v2_configuration_must_name_its_entry_veto_explicitly() -> None:
    """v2 is the version that carries the veto, so it is not allowed to lean on a default."""
    with pytest.raises(ValidationError, match="entry_veto"):
        SurvivorBookConfig(seed=7, schema_version="survivor-book-config/v2")  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="entry_veto"):
        SurvivorBookConfig.model_validate({"schema_version": "survivor-book-config/v2", "seed": 7})


def test_a_v1_configuration_may_only_carry_the_none_veto() -> None:
    """A rule that predates the field cannot claim to be acted on: v1 means "no veto"."""
    with pytest.raises(ValidationError, match="entry_veto"):
        config(entry_veto="market_bearish_5/v1")
    # Naming the only legal value explicitly is accepted, but it is not v1's stored form: the
    # registration check below is why that is the right answer rather than a second spelling.
    assert config(entry_veto="none").model_dump_json(exclude_computed_fields=True) == V1_STORED_JSON


def test_a_v2_configuration_serializes_the_veto_it_names_and_has_its_own_identity() -> None:
    """The other half: once the version allows a rule, the field is stored and addressed."""
    held = SurvivorBookConfig(
        seed=7,
        schema_version="survivor-book-config/v2",
        entry_veto=VETO_RULE_ID,
    )

    stored = held.model_dump(mode="json", exclude_computed_fields=True)
    assert stored["entry_veto"] == VETO_RULE_ID
    stored_json = held.model_dump_json(exclude_computed_fields=True)
    assert SurvivorBookConfig.model_validate_json(stored_json) == held
    assert held.config_id != V1_CONFIG_ID
    assert survivor_config_digest(held) != V1_CONFIG_DIGEST
    # `none` is a legal v2 spelling: v2 answers "this book has no veto" explicitly.
    assert (
        SurvivorBookConfig(
            seed=7, schema_version="survivor-book-config/v2", entry_veto="none"
        ).entry_veto
        == "none"
    )


def test_a_v1_registration_block_naming_the_default_veto_is_not_its_stored_form() -> None:
    """Why v1 accepts but strips the field: one book keeps one registered spelling.

    `strategy_survivor.read_survivor_registration` validates the block, dumps it again and
    requires the text to be equal. With the field stripped from v1's dump, a block that names
    `"entry_veto": "none"` re-dumps without it and is refused as not the stored form -- the same
    rule that already refuses a block restating any other default.
    """
    block = {**config().model_dump(mode="json", exclude_computed_fields=True), "entry_veto": "none"}

    assert SurvivorBookConfig.model_validate(block) == config()
    assert (
        SurvivorBookConfig.model_validate(block).model_dump(
            mode="json", exclude_computed_fields=True
        )
        != block
    )


def test_the_defaults_are_the_values_the_design_names() -> None:
    held = config()

    assert held.schema_version == "survivor-book-config/v1"
    assert held.holding_count == 10
    assert held.capital == Decimal("350000")
    assert held.boards == ("main",)  # ChiNext is not tradable from the account (D12)
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
        config(boards=("main", "growth")),
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


def ranked(code: str, rank: int, composite: float) -> RankedName:
    return RankedName(ts_code=code, composite=composite, rank=rank, close=Decimal("10"))


def scored(code: str, composite: float) -> EligibilityVerdict:
    return EligibilityVerdict(ts_code=code, composite=composite)


def test_a_consistent_screen_result_is_accepted() -> None:
    result = ScreenResult(
        verdicts=(
            scored("A", 3.0),
            scored("B", 2.0),
            EligibilityVerdict(ts_code="C", reasons=("suspended",)),
        ),
        survivors=(ranked("A", 1, 3.0), ranked("B", 2, 2.0)),
    )

    assert [name.ts_code for name in result.survivors] == ["A", "B"]


@pytest.mark.parametrize(
    "survivors",
    [
        (ranked("B", 1, 2.0), ranked("A", 2, 3.0)),  # not best first
        (ranked("A", 2, 3.0), ranked("B", 3, 2.0)),  # ranks do not start at 1
        (ranked("A", 1, 3.0), ranked("C", 2, 1.0)),  # C has a reason against it
        (ranked("A", 1, 3.0), ranked("Z", 2, 1.0)),  # Z was never assessed
    ],
)
def test_a_screen_result_that_contradicts_itself_is_refused(
    survivors: tuple[RankedName, ...],
) -> None:
    verdicts = (
        scored("A", 3.0),
        scored("B", 2.0),
        EligibilityVerdict(ts_code="C", reasons=("suspended",)),
    )
    with pytest.raises(ValidationError):
        ScreenResult(verdicts=verdicts, survivors=survivors)


def test_verdicts_must_be_unique_and_ordered_by_code() -> None:
    with pytest.raises(ValidationError, match="ordered by ts_code"):
        ScreenResult(verdicts=(scored("B", 1.0), scored("A", 2.0)), survivors=())
    with pytest.raises(ValidationError, match="ordered by ts_code"):
        ScreenResult(verdicts=(scored("A", 1.0), scored("A", 1.0)), survivors=())


def test_a_screen_result_with_a_security_twice_among_the_survivors_is_refused() -> None:
    with pytest.raises(ValidationError, match="more than once among the survivors"):
        ScreenResult(
            verdicts=(scored("A", 3.0),),
            survivors=(ranked("A", 1, 3.0), ranked("A", 2, 3.0)),
        )


def test_whole_lots_are_counted_exactly_however_large_the_budget() -> None:
    budget = Decimal(10) ** 25 + Decimal(7)

    assert lot_quantity(budget, Decimal(3), 100) == int(budget // 300) * 100


SOURCE_ROOT = str(Path(__file__).resolve().parents[3] / "src")
"""The tree under test, handed to a child process so it imports this copy (an isolated worktree,
say) rather than whichever one the interpreter's own path finds first."""


def test_the_config_digest_is_the_whole_sha256_of_the_bytes_the_config_id_addresses() -> None:
    """One canonicalisation read at two widths: `config_id` is `sbc_` and the first 24 hex
    digits of it, and the digest a paper-book snapshot quotes is all 64."""
    base = config()
    digest = survivor_config_digest(base)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert base.config_id == f"sbc_{digest[:24]}"
    assert survivor_config_digest(config()) == digest


def test_the_config_digest_moves_with_any_one_field() -> None:
    base = survivor_config_digest(config())

    for changed in (
        config(holding_count=9),
        config(capital=Decimal("350001")),
        config(boards=("main", "growth")),
        config(cost_reserve_fraction=Decimal("0.006")),
        SurvivorBookConfig(seed=8),
    ):
        assert survivor_config_digest(changed) != base


def test_the_config_digest_is_the_same_in_processes_with_different_hash_seeds() -> None:
    program = (
        "from openalpha_cn.domain.survivor_book import SurvivorBookConfig, survivor_config_digest;"
        "print(survivor_config_digest(SurvivorBookConfig(seed=7, boards=('main', 'growth'))))"
    )
    answers = set()
    for hash_seed in ("0", "1"):
        completed = subprocess.run(
            [sys.executable, "-c", program],
            env={
                **os.environ,
                "PYTHONHASHSEED": hash_seed,
                "PYTHONPATH": os.pathsep.join(
                    part for part in (SOURCE_ROOT, os.environ.get("PYTHONPATH")) if part
                ),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        answers.add(completed.stdout.strip())

    assert answers == {survivor_config_digest(config(boards=("main", "growth")))}
