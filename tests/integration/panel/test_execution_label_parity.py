"""Two contracts, one predicate, one stored panel (`V2-P2-007`).

`backtest/execution.py` and `domain/labels.py` both answer **"could this session have been
traded at its close"**, and they were written from different inputs and never compared.
`AShareExecutionPolicy` reads a `MarketBar` whose `suspended` is a `bool` the caller supplies
and whose band is either the exchange's published pair or one derived from the board;
`label_outcome` reads `suspend_d`'s three-valued `TradingState` plus its `suspend_timing`
window, `stk_limit`'s band through `limit_touch`, and the registry, and answers with eight
named refusal codes. Nothing imported anything from the other side.

## Not merged, and the reason is not conservatism

The roadmap's stated cause for this issue -- "`suspended`/`is_st` are always `False` in the
existing tests" -- stopped being true with `V2-P1-008`
(`tests/unit/backtest/test_execution_published_limits.py` sets `is_st=True` on a real ST
ChiNext row and `suspended=True` on an ordering assertion). The gap that remains is that the
two implementations are unrelated, and merging them would be the wrong repair for two reasons:
they have different arities -- one order against one bar, against one window of sessions -- and
this policy's verdicts are pinned by `tests/unit/backtest/test_execution.py` against the
*derived* band, which `execution.py`'s own docstring says must not be silently re-decided from
a new dataset. A red-team gate that rewrote the thing it measures would measure nothing.

So what is delivered is agreement, driven from one stored panel through both paths, plus the
three places agreement stops -- which are named in `KNOWN_EXECUTION_LIMITATIONS` rather than
asserted away.

## The one thing that had to be built

`MarketBar.suspended` is a bool and `TradingState` refuses to be one -- `__bool__` raises,
precisely so the collapse cannot happen by accident. It still has to happen somewhere, and the
obvious spelling is fail-open on the shape the data most often has: a security halted
`13:00-15:00` **traded**, so it is `interrupted` and not `halted`, while its `daily.close` is
the last print before 13:00. 39 of the 59 timed `S` rows served across 68 whole-market sessions
run through the close. `suspended_at_the_close` is that collapse made once, and the test below
drives the same stored rows through it and through the naive `state is TradingState.halted` and
shows the two verdicts coming apart on exactly that session.

## What is deliberately not asserted

That the two agree on a session with **no published band**, or on a security the registry did
not stand behind. They do not, they cannot, and the direction is opposite in each case: the
policy derives a band where the label refuses for want of one, and the policy has no registry
input at all. Both are `KNOWN_EXECUTION_LIMITATIONS` entries and both are pinned here as
disagreements, because a limitation nothing exercises is a sentence.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    AS_OF,
    DELISTED_SECURITY,
    EXCHANGE,
    LOCKED_SECURITY_INDEX,
    LOCKED_SESSION_INDEX,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.backtest.execution import (
    KNOWN_EXECUTION_LIMITATIONS,
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    REFUSAL_DELISTED,
    REFUSAL_HALTED_INTO_THE_CLOSE,
    REFUSAL_HALTED_SESSION,
    REFUSAL_LOCKED_AT_LIMIT,
    REFUSAL_MISSING_BAR,
    REFUSAL_UNPUBLISHED_BAND,
    HaltCorpus,
    LabelWindow,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit, TradingState
from openalpha_cn.domain.stock_universe import ListingStatus, StockUniverse
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

SHAPES = (
    "daily.close_moves_between_sessions",
    "price_limits.limit_free_sentinel",
    "price_limits.one_price_limit_up",
    "suspension.timed_interruption",
)
"""Four shapes, so one panel carries four different answers to the shared predicate.

The whole-day halt is not in the list because the generator writes one unconditionally
(`_halted_key`), which is what gives `suspend_d` a partition at all. `universe.delisted_security`
is left out for the opposite reason: the terminated name is in no session's cross section, so
there is no bar to hand the policy and the disagreement it produces is the *absence* of a
question rather than two answers to one -- `KNOWN_EXECUTION_LIMITATIONS`'
`the_registry_verdict_is_not_an_input` is where that is stated, and
`test_the_policy_cannot_be_asked_about_a_session_that_has_no_bar` is what pins it.
"""

LOT = 200
"""One order size for every bar, so the lot rules never decide a verdict.

200 is a multiple of 100 and at least 200, so it satisfies both branches of
`_rejection_reason`'s quantity check and the board a bar carries cannot change the answer.
"""


def _board(ts_code: str) -> Literal["main", "star", "growth", "bse"]:
    """The board a code belongs to, from its prefix.

    Only reached by the derived-band path, which the parity tests never take -- every bar they
    build carries the panel's published band. It is derived anyway rather than hard-coded to
    `"main"`, because a `688*`/`300*` name silently filed under the main board would be a
    fixture that quietly disagrees with the registry it came from.
    """
    if ts_code.startswith("688"):
        return "star"
    if ts_code.startswith("300"):
        return "growth"
    if ts_code.endswith(".BJ"):
        return "bse"
    return "main"


class _Read:
    """One generated panel, stored and read back once, with both paths' inputs beside it."""

    def __init__(self, tmp_path: Path, *, shapes: tuple[str, ...] = SHAPES) -> None:
        self.panel: GeneratedPanel = generate_panel(shapes=shapes)
        store = PanelStore(tmp_path / "panel")
        write_generated_panel(store, self.panel)
        self.calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(YEAR,), as_of=AS_OF)
        self.bars: dict[date, Mapping[str, DailyBar]] = {}
        self.limits: dict[date, Mapping[str, PriceLimit]] = {}
        for day in self.panel.sessions:
            self.bars[day] = load_daily_bars(
                store, day=day, calendar=self.calendar, as_of=AS_OF, max_staleness=None
            )
            self.limits[day] = load_price_limits(
                store, day=day, calendar=self.calendar, as_of=AS_OF, max_staleness=None
            )
        self.halts: HaltCorpus = halt_corpus_for_years(
            load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None), years=(YEAR,)
        )
        self.universe: StockUniverse = load_stock_universe(
            store, years=(YEAR,), as_of=AS_OF, max_staleness=None
        )
        self.factors = load_adjustment_histories(
            store, years=(YEAR,), as_of=AS_OF, max_staleness=None
        )

    def window_entering_on(self, session: date) -> LabelWindow:
        """The shortest window whose **entry** is `session`.

        A label has no zero-length window -- `1d` spans the entry and the session after it --
        so the parity below reads only the refusals `LabelRefusal.day` files against the entry.
        That is the session both contracts are being asked about, and the exit's own refusals
        are a different question asked one session later.
        """
        position = self.panel.sessions.index(session)
        previous = (
            self.panel.sessions[position - 1]
            if position
            else self.panel.sessions[0] - timedelta(days=1)
        )
        return build_label_window(
            as_of=datetime(previous.year, previous.month, previous.day, 8, 30, tzinfo=SHANGHAI),
            zone=SHANGHAI,
            horizon=parse_horizon("1d"),
            calendar=self.calendar,
        )

    def label_refusals_on(self, ts_code: str, session: date) -> frozenset[str]:
        """Every code `label_outcome` files against `session` itself, for this security."""
        window = self.window_entering_on(session)
        label = label_outcome(
            window,
            ts_code=ts_code,
            bars={
                day: self.bars[day][ts_code] for day in window.sessions if ts_code in self.bars[day]
            },
            factors=self.factors[ts_code],
            limits={
                day: self.limits[day][ts_code]
                for day in window.sessions
                if ts_code in self.limits[day]
            },
            halts=self.halts,
            universe=self.universe,
        )
        return frozenset(item.code for item in label.refusals if item.day == session)

    def bar_for(self, ts_code: str, session: date, *, suspended: bool | None = None) -> MarketBar:
        """The stored bar and band as the execution policy's own input type.

        `suspended` defaults to `suspended_at_the_close`'s answer for this security on this
        session, which is the point of the whole file: the two contracts agree only when the
        halt corpus reaches `MarketBar` through the one function that knows the corpus is
        three-valued. A caller may pass its own to show what a different collapse does.
        """
        bar = self.bars[session][ts_code]
        limit = self.limits[session][ts_code]
        if suspended is None:
            suspended = suspended_at_the_close(
                self.halts.state_on(session, ts_code), self.halts.timing_on(session, ts_code)
            )
        return MarketBar(
            subject=ts_code,
            trade_date=session,
            board=_board(ts_code),
            previous_close=Decimal(str(bar.pre_close)),
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            suspended=suspended,
            is_st=False,
            **published_limit_fields(limit),
        )

    def priced_pairs(self) -> tuple[tuple[str, date], ...]:
        """Every `(security, session)` both contracts can be asked about.

        A pair needs a bar, a published band, and a session that is some window's entry -- the
        last session of the panel is nobody's entry at a 1d horizon, because its exit would
        fall outside the year the halt corpus was read over.
        """
        return tuple(
            (ts_code, session)
            for session in self.panel.sessions[:-1]
            for ts_code in self.panel.securities
            if ts_code in self.bars[session] and ts_code in self.limits[session]
        )


def _verdict(read: _Read, ts_code: str, session: date, side: Literal["buy", "sell"]) -> str:
    policy = AShareExecutionPolicy()
    request = ExecutionRequest(side=side, quantity=LOT)
    return policy.execute(request, read.bar_for(ts_code, session)).status


# --- the parity itself ------------------------------------------------------------------------


def test_the_two_contracts_agree_on_every_session_either_of_them_can_price(
    tmp_path: Path,
) -> None:
    """The whole claim, over every `(security, session)` pair of a four-shape panel at once.

    A buy is the side a label's **entry** assumes, so it is the side the two contracts are
    comparable on: the label refuses the entry when the session had no counterparty to buy
    from, and `_rejection_reason` rejects a buy for the same reason. Asserted as one mapping
    rather than as a loop of asserts so a single pair coming apart names itself in the diff,
    and asserted over *every* pair rather than over the interesting ones so a shape that
    stopped arriving shows up as agreement in the wrong place.
    """
    read = _Read(tmp_path)
    pairs = read.priced_pairs()

    label_refuses = {pair: bool(read.label_refusals_on(*pair)) for pair in pairs}
    policy_refuses = {pair: _verdict(read, *pair, side="buy") == "rejected" for pair in pairs}

    assert len(pairs) == 71
    assert label_refuses == policy_refuses
    # And the agreement is not the trivial one where neither ever refuses.
    assert sum(label_refuses.values()) == 2


def test_the_two_sessions_they_both_refuse_are_the_halt_and_the_lock(tmp_path: Path) -> None:
    """Naming them, because "two refusals" is satisfied by any two.

    `securities[0]` on `sessions[2]` is the `13:00-15:00` halt and `securities[3]` on
    `sessions[1]` is the one-price limit-up session; the label names a different code for each
    and the policy a different sentence, which is what makes the agreement above an agreement
    about *these* two facts rather than a coincidence of counts.
    """
    read = _Read(tmp_path)
    halted_code, halted_day = read.panel.securities[0], read.panel.sessions[2]
    locked_code = read.panel.securities[LOCKED_SECURITY_INDEX]
    locked_day = read.panel.sessions[LOCKED_SESSION_INDEX]
    policy = AShareExecutionPolicy()

    assert read.label_refusals_on(halted_code, halted_day) == {REFUSAL_HALTED_INTO_THE_CLOSE}
    assert read.label_refusals_on(locked_code, locked_day) == {REFUSAL_LOCKED_AT_LIMIT}
    assert read.halts.timing_on(halted_day, halted_code) == "13:00-15:00"
    assert (
        policy.execute(
            ExecutionRequest(side="buy", quantity=LOT), read.bar_for(halted_code, halted_day)
        ).reason
        == "security is suspended"
    )
    assert (
        policy.execute(
            ExecutionRequest(side="buy", quantity=LOT), read.bar_for(locked_code, locked_day)
        ).reason
        == "buy cannot fill on a one-price limit-up bar"
    )


def test_removing_the_halt_lets_the_same_session_through_on_both_paths(tmp_path: Path) -> None:
    """The other half of the injection. Same security, same session, same writer, same reads --
    only the `suspend_d` row is gone -- and both contracts go from refusing to answering.

    Without this the agreement above is one-directional: two implementations that refused
    everything would agree perfectly. The halt is the injectable one of the two refusals
    because `suspension.timed_interruption` is a shape the generator can be asked to drop; the
    lock is the same pairing one shape over (`price_limits.one_price_limit_up`), and
    `test_the_lock_is_the_shape_and_not_the_panel` is that half.
    """
    without = _Read(tmp_path / "without", shapes=("daily.close_moves_between_sessions",))
    code, session = without.panel.securities[0], without.panel.sessions[2]

    assert without.halts.state_on(session, code) is None
    assert without.label_refusals_on(code, session) == frozenset()
    assert _verdict(without, code, session, "buy") == "filled"
    assert without.bar_for(code, session).suspended is False


def test_the_lock_is_the_shape_and_not_the_panel(tmp_path: Path) -> None:
    """The same pairing for the limit lock: with the shape, both refuse the buy; without it the
    same cell of the same panel fills on both paths.

    The band is the only thing that moves -- the generator publishes an upper limit *at* the
    session's own close rather than restating the close -- so the bar handed to the policy is
    byte-for-byte the same one in both halves.
    """
    with_lock = _Read(tmp_path / "with", shapes=("price_limits.one_price_limit_up",))
    without = _Read(tmp_path / "without", shapes=())
    code = with_lock.panel.securities[LOCKED_SECURITY_INDEX]
    session = with_lock.panel.sessions[LOCKED_SESSION_INDEX]

    assert with_lock.bar_for(code, session).close == without.bar_for(code, session).close
    assert with_lock.bar_for(code, session).up_limit != without.bar_for(code, session).up_limit
    assert with_lock.label_refusals_on(code, session) == {REFUSAL_LOCKED_AT_LIMIT}
    assert _verdict(with_lock, code, session, "buy") == "rejected"
    assert without.label_refusals_on(code, session) == frozenset()
    assert _verdict(without, code, session, "buy") == "filled"


# --- where the collapse into one bool decides the answer --------------------------------------


def test_the_naive_collapse_of_the_halt_corpus_fills_at_a_close_no_order_could_reach(
    tmp_path: Path,
) -> None:
    """`state is TradingState.halted` is the spelling a reader reaches for, and it is wrong on
    the majority shape.

    The security *traded* on this session -- it is `interrupted`, it has a bar, and
    `TradingState.interrupted`'s own docstring says all 31 of 2015-07-08's timed rows had one
    -- so the naive collapse hands the policy `suspended=False` and the order fills at a close
    that was the last print before 13:00. 39 of the 59 timed `S` rows measured across 68
    whole-market sessions run through the close, so this is not the corner case.

    Same stored rows on both sides of the assertion; the only difference is which function
    computed one bool.
    """
    read = _Read(tmp_path)
    code, session = read.panel.securities[0], read.panel.sessions[2]
    state = read.halts.state_on(session, code)
    naive = state is TradingState.halted
    policy = AShareExecutionPolicy()

    assert state is TradingState.interrupted
    assert code in read.bars[session]
    assert naive is False
    assert suspended_at_the_close(state, read.halts.timing_on(session, code)) is True
    assert (
        policy.execute(
            ExecutionRequest(side="buy", quantity=LOT),
            read.bar_for(code, session, suspended=naive),
        ).status
        == "filled"
    )
    assert read.label_refusals_on(code, session) == {REFUSAL_HALTED_INTO_THE_CLOSE}


def test_a_resumption_and_an_absent_row_are_both_tradeable_and_a_whole_day_halt_is_not() -> None:
    """The other three inputs to the collapse, at the contract rather than through a panel.

    A three-valued state plus an optional window is four cases and the panel above exercises
    two of them; asserting the remaining two here keeps the function's own closure visible
    instead of leaving `resumed` and `None` to be inferred from the ones that are stored.
    """
    assert suspended_at_the_close(TradingState.halted, None) is True
    assert suspended_at_the_close(TradingState.resumed, None) is False
    assert suspended_at_the_close(None, None) is False
    assert suspended_at_the_close(TradingState.interrupted, "09:30-10:30") is False
    assert suspended_at_the_close(TradingState.interrupted, "13:00-15:00") is True
    assert suspended_at_the_close(TradingState.interrupted, None) is True


# --- the three places the agreement stops -----------------------------------------------------


def test_a_one_price_session_refuses_one_side_here_and_both_ends_there(tmp_path: Path) -> None:
    """The asymmetry `KNOWN_EXECUTION_LIMITATIONS` names, measured on the stored lock.

    Selling into a limit-up lock is the trade that *had* a counterparty, so the policy fills
    it; the label refuses the session outright because it prices a round trip and does not know
    which side it will be on. The label is therefore strictly the more conservative of the two,
    and the two verdicts are interchangeable only on the buy side of an entry.
    """
    read = _Read(tmp_path)
    code = read.panel.securities[LOCKED_SECURITY_INDEX]
    session = read.panel.sessions[LOCKED_SESSION_INDEX]

    assert read.label_refusals_on(code, session) == {REFUSAL_LOCKED_AT_LIMIT}
    assert _verdict(read, code, session, "buy") == "rejected"
    assert _verdict(read, code, session, "sell") == "filled"


def test_an_absent_band_is_derived_rather_than_refused(
    tmp_path: Path,
) -> None:
    """`up_limit=None` means "the caller supplied no band", never "the exchange published
    none", so the policy falls back to the board rule and answers; the label refuses.

    Built by withholding the band from the `MarketBar` rather than from the store, because a
    partition missing a `stk_limit` row for one name is a shape the write-time guards refuse --
    which is itself the reason the disagreement is reachable only this way, and the reason it
    is stated as a limitation rather than repaired. The two are fail-open and fail-closed on
    one input, and the direction here is the one that answers.
    """
    read = _Read(tmp_path)
    code, session = read.panel.securities[0], read.panel.sessions[0]
    stored = read.bars[session][code]
    without_a_band = MarketBar(
        subject=code,
        trade_date=session,
        board=_board(code),
        previous_close=Decimal(str(stored.pre_close)),
        open=Decimal(str(stored.open)),
        high=Decimal(str(stored.high)),
        low=Decimal(str(stored.low)),
        close=Decimal(str(stored.close)),
        suspended=False,
        is_st=False,
    )
    window = read.window_entering_on(session)
    label = label_outcome(
        window,
        ts_code=code,
        bars={day: read.bars[day][code] for day in window.sessions},
        factors=read.factors[code],
        limits={},
        halts=read.halts,
        universe=read.universe,
    )

    assert without_a_band.has_published_limits is False
    assert {item.code for item in label.refusals if item.day == session} == {
        REFUSAL_UNPUBLISHED_BAND
    }
    assert (
        AShareExecutionPolicy()
        .execute(ExecutionRequest(side="buy", quantity=LOT), without_a_band)
        .status
        == "filled"
    )


def test_the_registry_verdict_is_not_an_input(tmp_path: Path) -> None:
    """A name the registry terminated, through both contracts.

    The bar here is **hand-built**, and that is the finding rather than a shortcut: `daily`
    carries no row for a terminated name, so there is no stored bar to read -- and
    `AShareExecutionPolicy` accepts the hand-built one and fills the order, because `MarketBar`
    has no field that could carry a listing verdict and `_rejection_reason` has no registry to
    consult. What is being shown is a property of the *input type*: this contract cannot
    express the fact that the label refuses on, so no data could make it notice.

    The label, reading the same stored registry, refuses the session three times over -- the
    termination, the absent bar, and the absent band -- each reported separately for
    `explain_unpriced`'s reason. `KNOWN_ADJUSTMENT_LIMITATIONS`'
    `delisted_securities_carry_unstable_factors` is why this is the sharp one of the three
    registry verdicts: 600069.SH's factor oscillated between 6.415 and 0.6604 for years after
    its last bar.
    """
    read = _Read(tmp_path, shapes=(*SHAPES, "universe.delisted_security"))
    session = read.panel.sessions[0]
    window = read.window_entering_on(session)
    label = label_outcome(
        window,
        ts_code=DELISTED_SECURITY,
        bars={},
        factors=build_adjustment_history(
            DELISTED_SECURITY,
            [FactorObservation(ts_code=DELISTED_SECURITY, observed_on=session, factor=1.0)],
        ),
        limits={},
        halts=read.halts,
        universe=read.universe,
    )
    invented = MarketBar(
        subject=DELISTED_SECURITY,
        trade_date=session,
        board=_board(DELISTED_SECURITY),
        previous_close=Decimal("10.00"),
        open=Decimal("10.00"),
        high=Decimal("10.00"),
        low=Decimal("10.00"),
        close=Decimal("10.00"),
        suspended=False,
        is_st=False,
        up_limit=Decimal("11.00"),
        down_limit=Decimal("9.00"),
    )

    assert read.universe.status_on(DELISTED_SECURITY, session) is ListingStatus.delisted
    assert DELISTED_SECURITY not in read.bars[session]
    assert {item.code for item in label.refusals if item.day == session} == {
        REFUSAL_DELISTED,
        REFUSAL_MISSING_BAR,
        REFUSAL_UNPUBLISHED_BAND,
    }
    assert "subject" in MarketBar.model_fields
    assert not {"listed", "delisted", "listing", "status"} & set(MarketBar.model_fields)
    assert (
        AShareExecutionPolicy().execute(ExecutionRequest(side="buy", quantity=LOT), invented).status
        == "filled"
    )


def test_the_policy_cannot_be_asked_about_a_session_that_has_no_bar(tmp_path: Path) -> None:
    """A whole-day halt and a delisting both reach the policy as an absence, not as a verdict.

    `MarketBar` requires five prices, all `gt=0`, so a session the security did not trade
    cannot be represented at all -- there is nothing to hand `execute`. The label answers about
    exactly that session, with two codes, one for the halt and one for the absent bar reported
    separately from it. So the policy's silence here is structural rather than a disagreement,
    which is why `priced_pairs` excludes these and why the parity above is a claim about the
    sessions both can price.
    """
    read = _Read(tmp_path)
    code, session = read.panel.securities[-1], read.panel.sessions[4]

    assert code not in read.bars[session]
    assert read.label_refusals_on(code, session) == {
        REFUSAL_HALTED_SESSION,
        REFUSAL_MISSING_BAR,
    }
    assert read.halts.state_on(session, code) is TradingState.halted


def test_every_declared_limitation_is_exercised_by_a_test_named_after_it() -> None:
    """The rule `KNOWN_LABEL_LIMITATIONS` has never had, applied to the three sentences this
    issue added.

    A limitation registry is prose, and prose that nothing runs is the drift this repository
    has booked twelve times -- `KNOWN_LABEL_LIMITATIONS`' own docstring concedes that "the only
    thing that mechanically holds them to the code is tests/unit/domain/test_labels.py", which
    is a convention rather than a check. Here it is a check: each code must be the suffix of a
    test function declared in this module, verified off this module's AST for
    `tests/unit/test_offline_suite.py`'s reason -- a deleted or renamed test reads exactly like
    one that still holds, and only the structure can tell them apart.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    declared = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }

    assert {item.code for item in KNOWN_EXECUTION_LIMITATIONS} == {
        "the_registry_verdict_is_not_an_input",
        "an_absent_band_is_derived_rather_than_refused",
        "a_one_price_session_refuses_one_side_here_and_both_ends_there",
    }
    assert {f"test_{item.code}" for item in KNOWN_EXECUTION_LIMITATIONS} <= declared, (
        "a limitation with no test named after it is a sentence, which is what this file "
        "exists to stop it being"
    )
    assert len({item.detail for item in KNOWN_EXECUTION_LIMITATIONS}) == 3


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_limit_free_sentinel_fills_on_both_sides_and_does_not_refuse(
    side: Literal["buy", "sell"], tmp_path: Path
) -> None:
    """The band that is not a band, through both contracts.

    `(99999.999, 0.01)` is what SSE has published since 2023-06-21 for a session with no limit,
    and it needs no special case on either side: `limit_touch`'s four flags all fall out
    `False` from the arithmetic and the policy's two comparisons are simply never satisfied.
    A contract that classified the sentinel by value rather than letting it through would be
    the one that broke here.
    """
    read = _Read(tmp_path)
    code, session = read.panel.securities[0], read.panel.sessions[0]

    assert read.limits[session][code].up_limit == 99999.999
    assert read.label_refusals_on(code, session) == frozenset()
    assert _verdict(read, code, session, side) == "filled"
