"""The daily `illiquid` rule (`V2-P7-006a`): one named threshold, the same one the weekly report
counts, and option (a) -- a holding below it is a forced exit.

The rule is a *rank* rule, not an absolute turnover band: the design fixes "the top 70% by 20-day
average traded amount among the names that pass every static reason", and `SecurityFacts.avg_amount`
is only ordered (`domain/survivor_book.py:162-163`, "only its order matters"), so no unit or data
beyond the panel's own is invented here.
"""

from decimal import Decimal

from openalpha_cn.domain.survivor_book import (
    ILLIQUID_DROP_FRACTION,
    ILLIQUID_POLICY,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
)
from openalpha_cn.domain.survivor_policy import BookState, Holding, decide_book
from openalpha_cn.domain.survivor_screen import screen

D = Decimal


def _fact(code: str, amount: int, *, suspended: bool = False) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board="main",
        listed_sessions=1000,
        special_treatment=False,
        suspended=suspended,
        close=D("10.00"),
        avg_amount=D(amount),
        oriented_values=(0.5,),
    )


def _config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "composite_factors": ("f/v1",),
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def test_the_liquidity_cut_is_the_named_fraction_of_the_statically_eligible_population() -> None:
    # 40 statically eligible names with amounts 1..40, plus a halted name with no amount at all.
    # The cut is the bottom floor(40 x 0.30) = 12 taken over the eligible 40 only; the halted
    # name is not in the population, so its zero amount cannot enter the cut.
    n = 40
    facts = [_fact(f"6001{i:02d}.SH", i) for i in range(1, n + 1)]
    halted = _fact("600999.SH", 0, suspended=True)
    result = screen([*facts, halted], _config())

    illiquid = [v.ts_code for v in result.verdicts if "illiquid" in v.reasons]
    assert len(illiquid) == floor_share(n, ILLIQUID_DROP_FRACTION) == 12
    assert set(illiquid) == {fact.ts_code for fact in facts[:12]}
    assert halted.ts_code not in illiquid


def test_the_named_fraction_is_what_the_shipped_configuration_already_stores() -> None:
    # The constant names the value the content-addressed configuration already had: re-typing the
    # default through it must not change a configuration's identity.
    default = SurvivorBookConfig(seed=1)
    explicit = SurvivorBookConfig(seed=1, liquidity_drop_fraction=Decimal("0.30"))

    assert default.liquidity_drop_fraction == ILLIQUID_DROP_FRACTION
    assert default == explicit
    assert default.config_id == explicit.config_id


def test_a_holding_that_falls_below_the_liquidity_line_is_a_forced_exit() -> None:
    # The held name is the thinnest of the eligible population tonight. The screen gives it
    # `illiquid` and nothing else; the policy sells the whole holding at the next open, with
    # `EXIT_ELIGIBILITY_LOST` -- option (a), the rule the exploratory period measured.
    thin = "600199.SH"
    facts = [_fact(f"6001{i:02d}.SH", i) for i in range(1, 11)] + [_fact(thin, 0)]
    config = _config(holding_count=3, loser_decile_count=0)
    screened = screen(facts, config)
    verdict = next(v for v in screened.verdicts if v.ts_code == thin)
    assert verdict.reasons == ("illiquid",)

    state = BookState(
        session=10,
        initialized=True,
        equity=D("350000"),
        holdings=(Holding(ts_code=thin, quantity=100),),
    )
    result = decide_book(config=config, screen=screened, state=state)

    assert [(exit_.ts_code, exit_.reason) for exit_ in result.exits] == [
        (thin, "EXIT_ELIGIBILITY_LOST")
    ]
    assert ILLIQUID_POLICY == "forced_exit"
