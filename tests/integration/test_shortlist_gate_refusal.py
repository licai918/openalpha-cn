"""The shortlist-level gate (`V2-P4-023`), against the two gates that were already there.

The roadmap's acceptance for this issue is one sentence -- "return an explicit blocking state
when the bar is not cleared" -- and the whole difficulty is that *both* of the gates this
repository already ships say **go** on the fixture below:

- **Per-dataset (`V2-P1-013`).** A real `PanelStore`, written through the real `panel_ingest`
  writers from `tests/panel_fixtures.py`'s generator, cleared by a real `require_datasets` over
  all seven datasets a price screen reads. Not one block.
- **Per-name (`V2-P4-004`).** A real `CrossSectionScreen` over a real `AShareExecutionPolicy`,
  fed the session's real bars, real published bands and real halt corpus read back out of that
  store. Every name it shortlists carries `fill.status == "filled"` -- the hard filter admits a
  security only when the policy filled its buy, so each shortlisted name individually passed.

And the list those two produce is a list in which **five of the eight securities the registry
lists could not be bought at all**: `601318.SH` was halted, so it has no bar, so a price factor
has no value for it and it is excluded before the market is ever asked about it; and
`600519.SH`, `300750.SZ`, `688981.SH` and `002415.SZ` are each above what the declared
`position_capital` buys one board lot of. The three that survive are the three cheapest names,
and because this screen ranks on price level they are also the three the factor liked **least**
-- so what ships is a clean-looking top-2 whose members are the market's sixth and seventh
choices, presented with ranks 1 and 2.

Nothing in the tree at `5e18791` can refuse that list. `CrossSectionFunnel.coverage` is
`shortlisted`, because a funnel's five refusal codes are all about the *extremes* -- nobody
scored, every score tied, the cut inside the clip block, nobody tradeable, the cut selecting
everybody -- and 3 tradeable names with a cut of 2 is none of them. `CandidateRanking` then
joins the evidence plane's conclusions onto it and reports `researched_rate == 1.0`. Both
records are correct. Neither is a gate.

## The denominator is the universe, and this fixture is why

`test_the_funnels_own_tradeable_rate_is_raised_by_the_name_that_had_no_price` measures the
thing that decided `ShortlistGateSpec.minimum_tradable_ratio`'s shape. `CrossSectionFunnel`
already publishes `tradeability.tradeable_rate`, which is `tradeable / scored` -- and on this
panel that is **0.4286**, higher than the 0.375 the same market gives against the universe,
*because* the halted name was dropped one stage earlier. A bar read against `tradeable/scored`
would therefore be relieved by exactly the securities it exists to notice. So the gate divides
by `universe_count`, and `the_tradable_ratio_divides_by_the_universe_because_the_funnels_own
_denominator_can_be_shrunk` carries the two numbers.

## What is asserted here, and why each half is needed

`test_the_two_gates_that_already_exist_both_clear_this_list` is the load-bearing negative: if
the panel could not be cleared, or if a shortlisted name did not fill, then a shortlist gate
that refused everything would pass every other test in this file. It also pins the two measured
numbers the bars are read against -- `tradable_ratio == 0.375` and `ranking_age_days == 7` --
so the thresholds below are **read off a measurement** rather than chosen to make an assertion
go the way somebody wanted.

Each bar is then failed *alone*, on the same ranking, with the other bars cleared. That is the
separation this file exists to create: a fixture on which every bar failed together could not
tell a gate that reads the tradable ratio from one that reads nothing and refuses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

import pytest
from panel_fixtures import AS_OF, YEAR, GeneratedPanel, generate_panel, write_generated_panel

from openalpha_cn.backtest.candidate_ranking import (
    CandidateRanking,
    build_ranking_manifest,
    rank_candidates,
)
from openalpha_cn.backtest.cross_section import (
    ComponentCrossSection,
    CrossSectionFunnel,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
)
from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.shortlist_gate import (
    SHORTLIST_BLOCK_ORDER,
    ShortlistClearance,
    ShortlistGateError,
    ShortlistGateSpec,
    gate_shortlist,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_gate import DependencyClearance, DependencyRequest, require_datasets
from openalpha_cn.panel_ingest import (
    ADJ_FACTOR_DATASET,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRICE_LIMIT_DATASET,
    STOCK_BASIC_DATASET,
    SUSPENSION_DATASET,
    TRADING_CALENDAR_DATASET,
    load_daily_bars,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
)

DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
)
"""Every dataset a price-level screen over one session reads, and the whole of the request."""

SESSION_INDEX: Final[int] = 4
"""`sessions[4]` -- 2026-01-09 -- which is the session the generator halts `601318.SH` on.

A halted security in a *healthy* panel is the point: the per-dataset gate clears it, because a
halt is a market fact and not a defect, and the name is nonetheless one the list cannot hold.
"""

POSITION_CAPITAL: Final[Decimal] = Decimal("1250")
"""A budget that buys one 100-share lot of a name at 12.00 yuan and not one at 13.00.

The generator's closes are `10.0 + securities.index(code)`, so this splits the seven priced
names three/four at a place no assertion below has to name a price to describe.
"""

SHORTLIST_SIZE: Final[int] = 2
"""Strictly below the three tradeable names, so the funnel is `shortlisted` rather than
`cut_exceeds_the_cross_section` -- the code that already refuses a cut selecting everybody."""

HORIZON: Final[str] = "5d"
COMMIT: Final[str] = "5e18791"
CONFIG_DIGEST: Final[str] = "d" * 64

UNIVERSE_COUNT: Final[int] = 8
SCORED_COUNT: Final[int] = 7
TRADEABLE_COUNT: Final[int] = 3

TRADABLE_RATIO: Final[float] = TRADEABLE_COUNT / UNIVERSE_COUNT
"""0.375 -- three tradeable of the eight the registry listed. Measured below, not assumed."""

FUNNEL_TRADEABLE_RATE: Final[float] = TRADEABLE_COUNT / SCORED_COUNT
"""0.4286 -- the same market against the funnel's own denominator, which is the higher number."""

RANKING_AGE_DAYS: Final[int] = 7
"""2026-01-09 08:30 UTC to 2026-01-17 04:00 UTC, floored to whole days."""

PRICE_LEVEL: Final[FactorDefinition] = FactorDefinition(
    key="probe_price_level",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(FactorField(dataset="daily", column="close"),),
    lookback_sessions=1,
    max_window_sessions=1,
    lookback_periods=None,
    max_window_periods=None,
)
"""One raw component reading exactly the column this fixture feeds it.

A raw-tier screen may declare exactly one component and this is it, so the composite is the
stored close in yuan and nothing is standardised, imputed or winsorized anywhere in the chain.
That keeps every number in this file traceable to a row in the panel.
"""


def _board(ts_code: str) -> Literal["main", "star", "growth", "bse"]:
    if ts_code.startswith("688"):
        return "star"
    if ts_code.startswith("300"):
        return "growth"
    if ts_code.endswith(".BJ"):
        return "bse"
    return "main"


class _Screened:
    """One healthy panel, cleared by `V2-P1-013`, screened by `V2-P4-004`, ranked by `V2-P4-005`.

    Built once per test rather than as a session fixture: each test writes its own store under
    its own `tmp_path`, which is what keeps a test that damaged one from reaching another's.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.panel: GeneratedPanel = generate_panel()
        self.session: date = self.panel.sessions[SESSION_INDEX]
        self.as_of: datetime = datetime(
            self.session.year, self.session.month, self.session.day, 8, 30, tzinfo=UTC
        )
        store = PanelStore(tmp_path / "panel")
        write_generated_panel(store, self.panel, datasets=DATASETS)
        self.calendar = self.panel.calendar()

        self.clearance: DependencyClearance = require_datasets(
            store,
            DependencyRequest(
                datasets=DATASETS,
                as_of=AS_OF,
                years=(YEAR,),
                sessions=(self.session,),
                calendar=self.calendar,
                index_codes=(),
            ),
        )

        bars = load_daily_bars(
            store, day=self.session, calendar=self.calendar, as_of=AS_OF, max_staleness=None
        )
        limits = load_price_limits(
            store, day=self.session, calendar=self.calendar, as_of=AS_OF, max_staleness=None
        )
        halts = halt_corpus_for_years(
            load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None), years=(YEAR,)
        )
        registry = load_stock_universe(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)

        self.universe: tuple[str, ...] = tuple(sorted(registry.listed_on(self.session)))
        self.closes: dict[str, float] = {code: bar.close for code, bar in bars.items()}
        self.bars: dict[str, MarketBar] = {
            code: MarketBar(
                subject=code,
                trade_date=self.session,
                board=_board(code),
                previous_close=Decimal(str(bar.pre_close)),
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                suspended=suspended_at_the_close(
                    halts.state_on(self.session, code), halts.timing_on(self.session, code)
                ),
                is_st=False,
                **published_limit_fields(limits[code]),
            )
            for code, bar in bars.items()
        }
        self.spec: ShortlistSpec

    def funnel(self, *, shortlist_size: int = SHORTLIST_SIZE) -> CrossSectionFunnel:
        self.spec = ShortlistSpec(
            components=(ScoreComponent(definition=PRICE_LEVEL, weight=1.0),),
            tier="raw",
            shortlist_size=shortlist_size,
            position_capital=POSITION_CAPITAL,
        )
        return CrossSectionScreen(self.spec, execution=AShareExecutionPolicy()).select(
            as_of=self.as_of,
            universe=self.universe,
            components=[
                ComponentCrossSection(
                    factor_id=PRICE_LEVEL.factor_id,
                    values=tuple(
                        (code, self.closes.get(code), "computed") for code in self.universe
                    ),
                    clipped_subjects=frozenset(),
                )
            ],
            bars=self.bars,
        )

    def ranking(
        self,
        *,
        built_at: datetime | None = None,
        researched: bool = True,
        **kwargs: Any,
    ) -> CandidateRanking:
        cut = self.funnel(**kwargs)
        manifest = build_ranking_manifest(
            as_of=self.as_of,
            horizon=HORIZON,
            universe=list(self.universe),
            scoring_policy=self.spec,
            code_commit=COMMIT,
            config_digest=CONFIG_DIGEST,
            built_at=built_at if built_at is not None else AS_OF,
        )
        subjects = tuple(entry.subject for entry in cut.shortlist) if researched else ()
        return rank_candidates(
            manifest=manifest,
            funnel=cut,
            signals={code: self._signal(code) for code in subjects},
            run_manifest_ids={code: self._run_manifest_id(code) for code in subjects},
            exposures=None,
            predictions={},
        )

    def _signal(self, subject: str) -> SignalFrame:
        return SignalFrame(
            subject=subject,
            as_of=self.as_of,
            direction="bullish",
            strength=0.4,
            confidence=0.7,
            horizon=HORIZON,
            evidence_ids=("evd_000000000000000000000001",),
        )

    def _run_manifest_id(self, subject: str) -> str:
        return RunManifest(
            run_id=f"run-{subject}",
            mode="backtest",
            as_of=self.as_of,
            code_commit=COMMIT,
            config_digest=CONFIG_DIGEST,
            random_seed=7,
            started_at=self.as_of,
            finished_at=AS_OF,
            status="succeeded",
        ).run_manifest_id


def _spec(
    *,
    tradable: float = 0.0,
    researched: float = 0.0,
    age: int = 3_650,
) -> ShortlistGateSpec:
    """One declared set of bars. Every argument is named at every call site that cares.

    The defaults are the *inert* ones -- a floor of zero and a decade of staleness -- so a test
    that names one bar is measurably testing that bar alone, and a gate that ignored the
    argument it was given would have to pass every test in this file to hide.
    """
    return ShortlistGateSpec(
        minimum_tradable_ratio=tradable,
        minimum_researched_ratio=researched,
        maximum_ranking_age_days=age,
    )


# --- the two gates that already exist, and what they both say ---------------------------------


def test_the_two_gates_that_already_exist_both_clear_this_list(tmp_path: Path) -> None:
    """The negative every other test in this file rests on.

    Three separate claims, because a shortlist gate that refused everything would satisfy any
    two of them: the datasets are healthy, each shortlisted name filled, and the list that
    results is a `shortlisted` funnel with a complete research rate.
    """
    read = _Screened(tmp_path)

    assert read.clearance.is_blocked is False
    assert read.clearance.blocks == ()
    assert tuple(entry.dataset for entry in read.clearance.cleared) == DATASETS

    ranking = read.ranking()

    assert ranking.funnel.coverage == "shortlisted"
    assert ranking.funnel.scores.universe_count == UNIVERSE_COUNT
    assert ranking.funnel.scores.scored_count == SCORED_COUNT
    assert ranking.funnel.tradeability.tradeable_count == TRADEABLE_COUNT
    assert [entry.subject for entry in ranking.funnel.shortlist] == ["600000.SH", "000002.SZ"]
    assert all(entry.fill.status == "filled" for entry in ranking.funnel.shortlist)
    assert ranking.unresearched == ()
    assert ranking.researched_rate == 1.0

    assert dict(ranking.funnel.tradeability.refused_by_verdict) == {
        "unbarred": 0,
        "unbanded": 0,
        "below_board_minimum": 4,
        "rejected": 0,
    }
    assert dict(ranking.funnel.scores.excluded_by_coverage)["not_valued"] == 1


def test_the_funnels_own_tradeable_rate_is_raised_by_the_name_that_had_no_price(
    tmp_path: Path,
) -> None:
    """The measurement that chose this gate's denominator.

    `601318.SH` is halted, so it has no bar, so the price factor has no value for it, so stage
    one files it under `not_valued` and it never reaches the market at all. That *shrinks* the
    funnel's denominator: `tradeable / scored` is 3/7 and `tradeable / universe` is 3/8. A bar
    read against the first would be relieved by exactly the security it exists to notice, so
    this gate divides by the universe and the two numbers are pinned apart here.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    assert ranking.funnel.tradeability.tradeable_rate == FUNNEL_TRADEABLE_RATE
    assert TRADABLE_RATIO == 0.375
    assert FUNNEL_TRADEABLE_RATE > TRADABLE_RATIO

    clearance = gate_shortlist(ranking=ranking, spec=_spec())
    assert clearance.measurement.tradable_ratio == TRADABLE_RATIO
    assert clearance.measurement.universe_count == UNIVERSE_COUNT
    assert clearance.measurement.scored_count == SCORED_COUNT
    assert clearance.measurement.tradeable_count == TRADEABLE_COUNT


def test_the_shortlisted_names_are_the_two_the_factor_liked_least_of_the_three_it_could_buy(
    tmp_path: Path,
) -> None:
    """The reason a coverage number is not cosmetic, stated as prices rather than as prose.

    This screen ranks on price level and the four most expensive priced names are exactly the
    four it cannot buy, so the surviving order is the *inverse* of the declared factor over the
    part of the market that was reachable. A reader handed the shortlist alone sees ranks 1, 2.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    assert [entry.rank for entry in ranking.funnel.shortlist] == [1, 2]
    assert [read.closes[entry.subject] for entry in ranking.funnel.shortlist] == [12.0, 11.0]
    assert max(read.closes.values()) == 16.0
    assert "601318.SH" not in read.closes


# --- the gate this issue adds ------------------------------------------------------------------


def test_a_list_every_name_of_which_passed_is_refused_for_the_ratio_the_whole_list_carries(
    tmp_path: Path,
) -> None:
    """`V2-P4-023`'s acceptance, in one test.

    The bar is `0.60` against a measured `0.375`, and the other two bars are inert, so the
    single block this raises is attributable to the tradable ratio and to nothing else.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    clearance = gate_shortlist(ranking=ranking, spec=_spec(tradable=0.60))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["tradable_ratio_below_floor"]
    assert clearance.blocks[0].measured == TRADABLE_RATIO
    assert clearance.blocks[0].required == 0.60
    assert "3" in clearance.blocks[0].detail
    assert "8" in clearance.blocks[0].detail


def test_the_same_list_clears_the_same_gate_under_a_bar_the_market_actually_met(
    tmp_path: Path,
) -> None:
    """The other direction on one fixture, which is what makes the bar a threshold.

    Same ranking, same measurement, a floor of `0.25` instead of `0.60`. A gate that refused
    whenever anything was untradeable, or that read a module constant, fails here.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    clearance = gate_shortlist(ranking=ranking, spec=_spec(tradable=0.25))

    assert clearance.is_blocked is False
    assert clearance.blocks == ()
    assert [candidate.subject for candidate in clearance.admitted] == ["600000.SH", "000002.SZ"]
    assert clearance.measurement.tradable_ratio == TRADABLE_RATIO


def test_the_same_list_is_refused_for_its_age_with_every_coverage_bar_cleared(
    tmp_path: Path,
) -> None:
    """The freshness half, failed alone.

    Both coverage floors are inert, so `ranking_is_stale` is the only block -- which is the
    assertion a fixture that failed two bars at once could not make.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    clearance = gate_shortlist(ranking=ranking, spec=_spec(age=3))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["ranking_is_stale"]
    assert clearance.blocks[0].measured == RANKING_AGE_DAYS
    assert clearance.blocks[0].required == 3
    assert clearance.measurement.ranking_age_days == RANKING_AGE_DAYS


def test_the_freshness_bar_moves_with_the_wall_clock_the_ranking_was_assembled_on(
    tmp_path: Path,
) -> None:
    """Two rankings of one declaration, six months apart, under one bar.

    `built_at` is the one `CandidateRankingManifest` field `RANKING_MANIFEST_UNADDRESSED_FIELDS`
    keeps out of `ranking_manifest_id`, so both of these carry the **same** declaration address
    and this gate still separates them. That is the split working rather than a hole in it: the
    manifest addresses what was asked for and the clearance answers what was measured.
    """
    read = _Screened(tmp_path)
    fresh = read.ranking(built_at=AS_OF)
    stale = read.ranking(built_at=AS_OF + timedelta(days=180))

    assert fresh.manifest.ranking_manifest_id == stale.manifest.ranking_manifest_id

    bar = _spec(age=30)
    assert gate_shortlist(ranking=fresh, spec=bar).is_blocked is False
    assert gate_shortlist(ranking=stale, spec=bar).is_blocked is True
    assert gate_shortlist(ranking=stale, spec=bar).measurement.ranking_age_days == 187


def test_a_shortlist_nobody_researched_is_refused_for_the_coverage_and_not_for_the_market(
    tmp_path: Path,
) -> None:
    """The second coverage bar, failed alone on a market that met the first.

    Both shortlisted names came back with no signal, so the ranking is two `unresearched`
    entries and no candidates. The tradable floor is met and the age bar is inert.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking(researched=False)

    assert ranking.candidate_count == 0
    assert len(ranking.unresearched) == 2
    assert ranking.researched_rate == 0.0

    clearance = gate_shortlist(ranking=ranking, spec=_spec(tradable=0.25, researched=0.5))

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["researched_ratio_below_floor"]
    assert clearance.blocks[0].measured == 0.0


def test_both_bars_can_fail_at_once_and_the_blocks_are_reported_in_the_declared_order(
    tmp_path: Path,
) -> None:
    """A refusal names everything that failed, not the first thing that failed.

    A gate that returned on the first block would leave a caller fixing the coverage and
    discovering the staleness on the next run.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking()

    clearance = gate_shortlist(ranking=ranking, spec=_spec(tradable=0.60, age=3))

    codes = tuple(block.code for block in clearance.blocks)
    assert set(codes) == {"tradable_ratio_below_floor", "ranking_is_stale"}
    assert codes == tuple(code for code in SHORTLIST_BLOCK_ORDER if code in set(codes))


# --- blocked is not empty ----------------------------------------------------------------------


def test_a_blocked_clearance_and_an_empty_one_are_reached_by_different_code_paths(
    tmp_path: Path,
) -> None:
    """The failure mode this issue exists to remove, driven as a **cross** rather than a pair.

    The blocked clearance sits on a ranking that has two candidates under it, and the cleared
    one on a ranking that has none. So "blocked" cannot be read as "there was nothing", and
    "nothing" cannot be read as "it was refused" -- and the two are told apart by `is_blocked`
    and by `admitted` raising, never by a length or a truth value, both of which raise on each.
    """
    read = _Screened(tmp_path)

    blocked = gate_shortlist(ranking=read.ranking(), spec=_spec(tradable=0.60))
    empty = gate_shortlist(ranking=read.ranking(researched=False), spec=_spec())

    assert blocked.is_blocked is True
    assert empty.is_blocked is False

    assert empty.admitted == ()
    with pytest.raises(ShortlistGateError, match="tradable_ratio_below_floor"):
        _ = blocked.admitted

    assert blocked.admitted_or_none is None
    assert empty.admitted_or_none == ()

    for clearance in (blocked, empty):
        for attempt in (bool, len, list):
            with pytest.raises(ShortlistGateError, match="verdict, not a collection"):
                attempt(clearance)  # type: ignore[arg-type,operator]


def test_a_funnel_that_shortlisted_nobody_blocks_rather_than_clearing_an_empty_list(
    tmp_path: Path,
) -> None:
    """The third state, and the fail-closed one.

    A cut of 3 against 3 tradeable names is `cut_exceeds_the_cross_section`: a funnel that
    legitimately carries no shortlist over a market that was there. `researched_rate` is `None`
    rather than `0.0`, because "nobody was researched" and "there was nothing to research" are
    different findings -- and a ratio that cannot be measured is refused rather than passed,
    with the funnel's own code named in the refusal.
    """
    read = _Screened(tmp_path)
    ranking = read.ranking(shortlist_size=3)

    assert ranking.funnel.coverage == "cut_exceeds_the_cross_section"
    assert ranking.researched_rate is None

    clearance = gate_shortlist(ranking=ranking, spec=_spec())

    assert clearance.is_blocked is True
    assert [block.code for block in clearance.blocks] == ["researched_ratio_not_measurable"]
    assert clearance.blocks[0].measured is None
    assert "cut_exceeds_the_cross_section" in clearance.blocks[0].detail


def test_the_ranking_underneath_a_refusal_is_the_full_one_and_is_not_truncated(
    tmp_path: Path,
) -> None:
    """A refusal is not a shorter list. The blocked clearance still carries the whole ranking's
    address, so a caller can say *which* list was refused rather than only that one was."""
    read = _Screened(tmp_path)
    ranking = read.ranking()

    clearance: ShortlistClearance = gate_shortlist(ranking=ranking, spec=_spec(tradable=0.60))

    assert clearance.ranking_content_digest == ranking.content_digest
    assert clearance.manifest.ranking_manifest_id == ranking.manifest.ranking_manifest_id
    assert clearance.measurement.candidate_count == 2
    assert ranking.candidate_count == 2
