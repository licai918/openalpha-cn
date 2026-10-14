"""`strategy_survivor`, the configuration half: the survivor registration read and checked, and
the paper book it is settled in (`V2-P7-008`).

A survivor registration's `config` is three keys -- `kind`, the `survivor_book` block (the
book's own stored form) and the `adapter` block -- and every test here reads one the way the
daily command does: the registered JSON, decoded. The two refusal classes are the script's two
exits (2 for a registration it cannot read, 3 for one that contradicts itself), so each test says
which class it expects.

The paper book is decision D15: whole-order rejection under the registered limits, at the
protocol's costs. Its construction is pinned by what it does to one 30,000-yuan buy -- the trade
T5 measured -- against the P6 strategy book's fill of the same trade, as
`tests/unit/backtest/test_paper_costs.py` compares the two books.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

import pytest

from openalpha_cn.backtest.execution import ExecutionResult, MarketBar, at_the_open
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.paper import PAPER_EXECUTION_VENUE, PaperSessionResult
from openalpha_cn.backtest.portfolio import PortfolioOrder, PortfolioState, PortfolioTransition
from openalpha_cn.backtest.strategy_backtest import (
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyInputs,
    StrategySpec,
    component_key,
    run_strategy_backtest,
)
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, survivor_config_digest
from openalpha_cn.strategy_survivor import (
    MAX_CARRY_SESSIONS,
    MAX_PANEL_CATCH_UP_SESSIONS,
    SURVIVOR_KIND,
    PaperLimitsConfig,
    SurvivorRegistration,
    SurvivorRegistrationContradictionError,
    SurvivorRegistrationError,
    make_paper_portfolio,
    read_survivor_registration,
    registration_kind,
)
from openalpha_cn.strategy_view import PROTOCOL_COSTS, PROTOCOL_SLIPPAGE_RATE

D = Decimal
SEED: Final[int] = 20_260_926
"""The registration's own seed (`grid.PROTOCOL_RANDOM_SEED`, its `settings.random_seed`)."""
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=SEED)
ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "epoch_session": "2026-01-16",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {"max_position_weight": "1", "max_total_exposure": "1", "min_cash_weight": "0"},
    "unfilled_buy": "reject_whole_and_carry/v1",
}
"""The adapter block the design memo writes (Part D, "Survivor config")."""


def stored(book: SurvivorBookConfig = BOOK) -> dict[str, Any]:
    """The book block a registration stores: the model's own JSON, without computed fields."""
    return book.model_dump(mode="json", exclude_computed_fields=True)


def registered(*, book: dict[str, Any] | None = None, **adapter: Any) -> dict[str, Any]:
    """A survivor configuration as the daily command reads it: written as JSON, read back."""
    config = {
        "kind": SURVIVOR_KIND,
        "survivor_book": stored() if book is None else book,
        "adapter": {**ADAPTER, **adapter},
    }
    decoded: dict[str, Any] = json.loads(json.dumps(config))
    return decoded


def unreadable(config: dict[str, Any], *, seed: int = SEED) -> str:
    """The message of the refusal `config` meets, asserting it is the exit-2 class."""
    with pytest.raises(SurvivorRegistrationError) as refused:
        read_survivor_registration(config, seed=seed)
    assert not isinstance(refused.value, SurvivorRegistrationContradictionError)
    return str(refused.value)


# --- the kind ----------------------------------------------------------------------------------


def test_a_configuration_without_a_kind_has_none_and_a_kind_is_read_as_written() -> None:
    assert registration_kind({"start": "2026-01-12", "holding_count": 3}) is None
    assert registration_kind(registered()) == "survivor_book/v1" == SURVIVOR_KIND
    assert registration_kind({"kind": "survivor_book/v2"}) == "survivor_book/v2"


@pytest.mark.parametrize("kind", [None, 1, ["survivor_book/v1"], True])
def test_a_kind_that_is_not_a_name_is_refused(kind: object) -> None:
    with pytest.raises(SurvivorRegistrationError, match="kind"):
        registration_kind({"kind": kind})


def test_an_unknown_kind_is_a_registration_this_command_cannot_read() -> None:
    message = unreadable({**registered(), "kind": "survivor_book/v2"})
    assert "'survivor_book/v2'" in message
    assert SURVIVOR_KIND in message


def test_a_configuration_without_a_kind_is_not_a_survivor_registration() -> None:
    config = registered()
    del config["kind"]
    assert "kind" in unreadable(config)


# --- the registered form -----------------------------------------------------------------------


def test_the_registered_form_reads_back_as_the_book_and_the_adapter_it_was_written_from() -> None:
    registration = read_survivor_registration(registered(), seed=SEED)

    assert isinstance(registration, SurvivorRegistration)
    assert registration.book == BOOK
    assert registration.book.config_id == BOOK.config_id
    assert registration.digest == survivor_config_digest(BOOK)
    adapter = registration.adapter
    assert adapter.schema_version == "survivor-adapter/v1"
    assert adapter.epoch_session == date(2026, 1, 16)
    assert (adapter.exchange, adapter.transform, adapter.benchmark) == (
        "SSE",
        "cross_section_standard/v1",
        "000905.SH",
    )
    assert adapter.min_coverage == 0.5
    assert (adapter.max_carry_sessions, adapter.max_panel_catch_up_sessions) == (250, 10)
    assert (adapter.prediction_horizon, adapter.cost_policy, adapter.unfilled_buy) == (
        "20d",
        "protocol/v1",
        "reject_whole_and_carry/v1",
    )
    assert adapter.paper_limits == PaperLimitsConfig(
        max_position_weight=D("1"), max_total_exposure=D("1"), min_cash_weight=D("0")
    )


def test_a_book_block_reads_back_to_the_config_id_of_the_configuration_it_was_dumped_from() -> None:
    """`config_id` addresses the stored form: the registered block must come back as the very
    configuration that was dumped into it -- typed decimals, factor order and all -- and not as
    an equal-valued one with another identity."""
    intended = SurvivorBookConfig(
        seed=SEED,
        capital=D("350000.00"),
        liquidity_drop_fraction=D("0.3"),
        near_exit_fraction=D("0.100"),
        composite_factors=tuple(reversed(BOOK.composite_factors)),
    )
    same_numbers = SurvivorBookConfig(
        seed=SEED, capital=D("350000"), composite_factors=intended.composite_factors
    )
    assert intended.config_id != same_numbers.config_id, "the typed form is the identity"

    registration = read_survivor_registration(registered(book=stored(intended)), seed=SEED)

    assert registration.book.config_id == intended.config_id
    assert registration.digest == survivor_config_digest(intended)
    assert registration.book.composite_factors == intended.composite_factors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("liquidity_drop_fraction", 0.3, id="a decimal written as a float"),
        pytest.param("capital", 350000, id="a decimal written as an integer"),
        pytest.param("holding_count", 10.0, id="a count written as a float"),
    ],
)
def test_a_re_typed_book_block_is_refused_with_the_fields_that_read_back_otherwise(
    field: str, value: object
) -> None:
    block = {**stored(), field: value}
    SurvivorBookConfig.model_validate(block)  # it is a valid configuration -- of another identity

    message = unreadable(registered(book=block))

    assert "stored form" in message
    assert field in message


def test_a_book_block_that_leaves_a_default_out_is_refused_by_the_field_it_left_out() -> None:
    block = stored()
    del block["cooldown_sessions"]

    message = unreadable(registered(book=block))

    assert "stored form" in message
    assert "cooldown_sessions" in message


@pytest.mark.parametrize(
    ("change", "named"),
    [
        pytest.param({"start": "2026-01-12"}, "start", id="a P6 key beside the blocks"),
        pytest.param({"seed": SEED}, "seed", id="a duplicate of the book's seed"),
    ],
)
def test_a_top_level_key_beside_the_three_is_refused_by_name(
    change: dict[str, Any], named: str
) -> None:
    message = unreadable({**registered(), **change})
    assert repr(named) in message


@pytest.mark.parametrize("missing", ["survivor_book", "adapter"])
def test_a_registration_without_one_of_its_blocks_is_refused_by_name(missing: str) -> None:
    config = registered()
    del config[missing]
    assert repr(missing) in unreadable(config)


@pytest.mark.parametrize(
    ("block", "named"),
    [
        pytest.param({**stored(), "holding_count": 0}, "survivor_book.holding_count", id="bound"),
        pytest.param(
            {**stored(), "config_id": BOOK.config_id}, "survivor_book.config_id", id="key"
        ),
        pytest.param({**stored(), "boards": []}, "survivor_book", id="model rule"),
    ],
)
def test_a_book_block_that_does_not_validate_is_refused_naming_the_field(
    block: dict[str, Any], named: str
) -> None:
    assert named in unreadable(registered(book=block))


@pytest.mark.parametrize("block", [["seed", SEED], "survivor_book", None])
def test_a_book_block_that_is_not_a_mapping_is_refused(block: object) -> None:
    config = registered()
    config["survivor_book"] = block
    assert "survivor_book" in unreadable(config)


# --- the adapter's bounds ----------------------------------------------------------------------


def test_the_panel_catch_up_bound_is_sixteen_sessions() -> None:
    """16 sessions of five requests beside an ordinary day's 16 is 96, the last under the 100
    the design sized a day against; 17 would be 101."""
    assert MAX_PANEL_CATCH_UP_SESSIONS == 16
    for accepted in (0, 1, 10, 16):
        adapter = read_survivor_registration(
            registered(max_panel_catch_up_sessions=accepted), seed=SEED
        ).adapter
        assert adapter.max_panel_catch_up_sessions == accepted
    assert "adapter.max_panel_catch_up_sessions" in unreadable(
        registered(max_panel_catch_up_sessions=17)
    )


@pytest.mark.parametrize("accepted", [1, 1.0, 0.5, 0.001])
def test_a_coverage_floor_above_zero_up_to_one_is_accepted(accepted: float) -> None:
    adapter = read_survivor_registration(registered(min_coverage=accepted), seed=SEED).adapter
    assert adapter.min_coverage == accepted


@pytest.mark.parametrize("refused", [0, 0.0, -0.1, 1.01, "0.5", True])
def test_a_coverage_floor_of_zero_or_above_one_is_refused(refused: object) -> None:
    assert "adapter.min_coverage" in unreadable(registered(min_coverage=refused))


def test_a_carry_of_one_session_is_accepted_and_none_is_refused() -> None:
    adapter = read_survivor_registration(registered(max_carry_sessions=1), seed=SEED).adapter
    assert adapter.max_carry_sessions == 1
    assert "adapter.max_carry_sessions" in unreadable(registered(max_carry_sessions=0))


def test_a_carry_of_a_thousand_sessions_is_accepted_and_more_is_refused() -> None:
    """About four years of sessions: the design's 250 four times over. A carry longer than that
    is a typo, not a choice, and is refused by name before it reaches the facts adapter."""
    assert MAX_CARRY_SESSIONS == 1000
    adapter = read_survivor_registration(registered(max_carry_sessions=1000), seed=SEED).adapter
    assert adapter.max_carry_sessions == 1000
    assert "adapter.max_carry_sessions" in unreadable(registered(max_carry_sessions=1001))


def test_a_transform_this_build_does_not_declare_is_refused() -> None:
    message = unreadable(registered(transform="cross_section_rank/v1"))
    assert "adapter.transform" in message
    assert "cross_section_standard/v1" in message, "the refusal names what the build knows"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("max_panel_catch_up_sessions", True, id="a bool for a count"),
        pytest.param("max_panel_catch_up_sessions", "10", id="text for a count"),
        pytest.param("max_panel_catch_up_sessions", 10.0, id="a float for a count"),
        pytest.param("max_panel_catch_up_sessions", -1, id="a negative count"),
        pytest.param("max_carry_sessions", "250", id="text for the carry"),
        pytest.param("epoch_session", "2026-1-16", id="a date not in ISO form"),
        pytest.param("epoch_session", "2026-01-16T00:00:00", id="an instant for a date"),
        pytest.param("epoch_session", 20260116, id="a number for a date"),
        pytest.param("exchange", "", id="no exchange"),
        pytest.param("exchange", "SZSE", id="another exchange's calendar"),
        pytest.param("exchange", " SSE ", id="the calendar's name padded"),
        pytest.param("exchange", "CFFEX", id="a futures exchange"),
        pytest.param("benchmark", "", id="no benchmark"),
        pytest.param("benchmark", "000300.SH", id="a stored index that is not the protocol's"),
        pytest.param("benchmark", "nonsense", id="no index at all"),
        pytest.param("benchmark", "equal_weight_all_a_held", id="the P6 book's second benchmark"),
        pytest.param("schema_version", "survivor-adapter/v2", id="another schema"),
        pytest.param("prediction_horizon", "5d", id="another horizon"),
        pytest.param("cost_policy", "default", id="another cost policy"),
        pytest.param("unfilled_buy", "partial", id="another unfilled-buy rule"),
        pytest.param("paper_limits", {"max_position_weight": "1"}, id="limits left out"),
    ],
)
def test_an_adapter_field_out_of_its_vocabulary_is_refused_naming_it(
    field: str, value: object
) -> None:
    assert f"adapter.{field}" in unreadable(registered(**{field: value}))


@pytest.mark.parametrize(
    ("limits", "named"),
    [
        pytest.param({"max_position_weight": 1.0}, "max_position_weight", id="a float"),
        pytest.param({"max_total_exposure": 1}, "max_total_exposure", id="an integer"),
        pytest.param({"max_position_weight": "0"}, "max_position_weight", id="no position"),
        pytest.param({"max_total_exposure": "1.1"}, "max_total_exposure", id="leverage"),
        pytest.param({"min_cash_weight": "1"}, "min_cash_weight", id="all cash"),
        pytest.param({"min_cash_weight": "NaN"}, "min_cash_weight", id="not a number"),
        pytest.param({"leverage": "2"}, "leverage", id="an extra limit"),
    ],
)
def test_paper_limits_are_decimals_written_as_text_within_the_portfolio_bounds(
    limits: dict[str, Any], named: str
) -> None:
    paper = {**ADAPTER["paper_limits"], **limits}
    assert f"adapter.paper_limits.{named}" in unreadable(registered(paper_limits=paper))


def test_an_adapter_key_nobody_reads_is_refused() -> None:
    assert "adapter.capital" in unreadable(registered(capital="350000"))


def test_every_adapter_field_has_to_be_named() -> None:
    config = registered()
    del config["adapter"]["benchmark"]
    assert "adapter.benchmark" in unreadable(config)


# --- the seed ----------------------------------------------------------------------------------


def test_a_book_seed_that_is_not_the_registrations_own_contradicts_the_registration() -> None:
    """A registration that names two seeds is read whole and refused as contradicting itself --
    the exit-3 class, as a `config_id` that does not match its own config is."""
    other = SurvivorBookConfig(seed=SEED + 1)

    with pytest.raises(SurvivorRegistrationContradictionError) as refused:
        read_survivor_registration(registered(book=stored(other)), seed=SEED)

    assert str(SEED + 1) in str(refused.value)
    assert str(SEED) in str(refused.value)
    assert issubclass(SurvivorRegistrationContradictionError, SurvivorRegistrationError)
    assert not issubclass(SurvivorRegistrationError, SurvivorRegistrationContradictionError)
    assert read_survivor_registration(registered(book=stored(other)), seed=SEED + 1).book == other


# --- the composite factors ---------------------------------------------------------------------


def test_a_composite_factor_this_build_does_not_declare_is_refused_at_registration() -> None:
    """Otherwise the first evening's factor build is where it fails, every night, on a
    registration frozen with it."""
    unknown = SurvivorBookConfig(
        seed=SEED,
        composite_factors=("residual_vol_60/v1", "residual_vol_60/v2", "turnover_60"),
    )

    message = unreadable(registered(book=stored(unknown)))

    assert "survivor_book.composite_factors" in message
    assert "'residual_vol_60/v2'" in message
    assert "'turnover_60'" in message
    assert "'residual_vol_60/v1'" not in message


# --- the paper book (decision D15) -------------------------------------------------------------

X, Y = "600010.SH", "600011.SH"
T1, T2 = date(2021, 3, 1), date(2021, 3, 2)
SHANGHAI: Final[timezone] = timezone(timedelta(hours=8))
CSI500: Final[str] = "000905.SH"
FULL: Final[PaperLimitsConfig] = PaperLimitsConfig.model_validate(ADAPTER["paper_limits"])


def bar(code: str, day: date, price: str) -> MarketBar:
    """A flat main-board bar at `price`, inside the 10% band the exchange publishes."""
    cent = D("0.01")
    close = D(price)
    return MarketBar(
        subject=code,
        trade_date=day,
        board="main",
        previous_close=close,
        open=close,
        high=close,
        low=close,
        close=close,
        suspended=False,
        is_st=False,
        up_limit=(close * D("1.1")).quantize(cent, rounding=ROUND_HALF_UP),
        down_limit=(close * D("0.9")).quantize(cent, rounding=ROUND_HALF_UP),
    )


class ListLedger:
    """The smallest `PortfolioLedger`: one `append`, kept in a list."""

    def __init__(self) -> None:
        self.appended: list[PortfolioTransition] = []

    def append(self, transition: PortfolioTransition) -> None:
        self.appended.append(transition)


def one_buy(*, cash: str, limits: PaperLimitsConfig = FULL) -> PaperSessionResult:
    """2,000 x 15.00 = 30,000.00 of X bought at T2's open from a cash-only book, through the
    paper book the survivor day builds."""
    ledger = ListLedger()
    result = make_paper_portfolio(ledger, limits).advance(
        state=PortfolioState(as_of=T1, cash=D(cash)),
        session=PortfolioBacktestStep(
            trade_date=T2,
            bars=(at_the_open(bar(X, T2, "15.00")),),
            orders=(PortfolioOrder(order_id="buy-x@T2", subject=X, side="buy", quantity=2_000),),
            benchmark_close=D("6000.00"),
        ),
        observed_on=T2,
    )
    assert ledger.appended == list(result.transitions), "the book records into the ledger given"
    return result


def fill_of(result: PaperSessionResult) -> ExecutionResult:
    (transition,) = result.transitions
    assert transition.status == "filled", transition.reason
    assert transition.execution is not None
    return transition.execution


def test_the_paper_book_charges_the_protocol_costs_and_lets_cash_alone_bind_a_buy() -> None:
    """D15. Commission q(30,000 x 0.00025) = 7.50, slippage q(30,000 x 0.001) = 30.00 in the
    transfer-fee slot, no stamp duty on a buy: 37.50, so the buy needs 30,037.50 and fills on
    exactly that -- a whole-equity position, which the default limits (25% of a name, 80% in
    all) would refuse. A fen less is refused whole for cash."""
    result = one_buy(cash="30037.50")
    fill = fill_of(result)

    assert (fill.notional, fill.commission, fill.transfer_fee, fill.stamp_duty) == (
        D("30000.00"),
        D("7.50"),
        D("30.00"),
        D("0.00"),
    )
    assert fill.total_cost == D("37.50")
    assert result.after.cash == D("0.00")
    assert result.execution_venue == PAPER_EXECUTION_VENUE == "paper -- no broker was contacted"

    short = one_buy(cash="30037.49")
    (refused,) = short.transitions
    assert (refused.status, refused.reason) == ("rejected", "insufficient cash")


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        pytest.param({"max_position_weight": "0.5"}, "maximum position weight exceeded", id="pos"),
        pytest.param({"max_total_exposure": "0.5"}, "maximum total exposure exceeded", id="all"),
        pytest.param({"min_cash_weight": "0.5"}, "minimum cash weight breached", id="cash"),
    ],
)
def test_each_registered_limit_is_the_portfolio_limit_of_its_own_name(
    limits: dict[str, str], reason: str
) -> None:
    """Each field reaches `PortfolioLimits` under its own keyword: tightening one refuses the
    whole-equity buy for that limit's reason and no other's."""
    tightened = PaperLimitsConfig.model_validate({**ADAPTER["paper_limits"], **limits})

    (refused,) = one_buy(cash="30037.50", limits=tightened).transitions

    assert (refused.status, refused.reason) == ("rejected", reason)


def test_the_paper_books_fees_are_the_p6_books_for_the_same_buy() -> None:
    """The same 30,000-yuan buy through the P6 strategy book under the protocol (`PROTOCOL_COSTS`
    beside `PROTOCOL_SLIPPAGE_RATE`): capital 30,100 buys floor(30,100 / 1,500) = 20 lots of X at
    T2's open, and its fees are the paper book's, component for component."""

    def instant(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 16, 30, tzinfo=SHANGHAI)

    def row(subject: str, value: float) -> ScoreRow:
        return ScoreRow(
            component=component_key("toy/v1", "raw"),
            subject=subject,
            signal_day=T1,
            value=value,
            available_time=instant(T1),
            revision_time=instant(T1),
        )

    sessions = (T1, T2)
    report = run_strategy_backtest(
        StrategyInputs(
            source=ScoreSource(components=(("toy/v1", "raw", D(1)),), combine="zscore_sum"),
            sessions=sessions,
            signal_instants={day: instant(day) for day in sessions},
            scores=(row(X, 2.0), row(Y, 1.0)),
            quotes={
                day: {
                    code: SessionQuote(
                        bar=bar(code, day, "15.00"),
                        turnover_yuan=D("100000000"),
                        adj_factor=D(1),
                    )
                    for code in (X, Y)
                }
                for day in sessions
            },
            benchmark_returns={CSI500: {day: D(0) for day in sessions}},
        ),
        StrategySpec(
            rebalance_every_sessions=1,
            holding_count=1,
            buffer_rank=None,
            max_industry_weight=None,
            position_capital=D("30100"),
            participation_cap=D("0.01"),
            costs=PROTOCOL_COSTS,
            slippage_rate=PROTOCOL_SLIPPAGE_RATE,
            benchmarks=(CSI500,),
        ),
    )
    (p6,) = [fill for period in report.periods for fill in period.fills]
    paper = fill_of(one_buy(cash="30100"))

    assert (p6.day, p6.subject, p6.side, p6.quantity, p6.notional) == (
        T2,
        X,
        "buy",
        2_000,
        D("30000.00"),
    )
    assert (paper.commission, paper.transfer_fee, paper.stamp_duty) == (
        p6.commission,
        p6.slippage,
        p6.stamp_duty,
    )
    assert paper.total_cost == p6.fees == D("37.50")
