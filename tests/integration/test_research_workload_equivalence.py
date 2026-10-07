"""`V2-P6-022`: the research workload answers the same numbers with every saving switched off.

`V2-P6-022` made `factor_ic_series` and `backtest_strategy` fast by keeping answers that were
re-derived: a registry index, a factor-day index, `LabelSessionMemo`'s per-session label answers,
one window's session mappings, `PanelStore`'s fingerprinted partition states, a transform or
neutralisation id taken once per read, and a session cache one period deep. Each of those is
a claim that nothing changes but the time.

This file makes the claim executable on the generated corpora the strategy face is tested on --
the ten-session panel (a halted security with no bar, a security the stored build codes
`insufficient_history`, a one-day label), the two-year panel (horizons 1, 5 and 20 whose windows
overlap and cross New Year, a listing in the second year) and the three-tier panel (processed and
neutralized reads). Each question is answered twice -- as shipped, and with every saving replaced
by the arithmetic it replaced -- and the two answers are compared as the serialised JSON the faces
print: every IC point and census, every period, every fill, every weight.

The shapes a label can meet that these panels do not carry -- delistings, a suspension through
whole windows, `V2-P6-020`'s corroborated and unknowable sessions, upstream defects that refuse --
are held one level down, window by window, in `tests/unit/domain/test_label_session_memo.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import (
    COMMIT,
    PROBE_NEUTRALIZATION,
    PROBE_NEUTRALIZATIONS,
    PROBE_TRANSFORM,
    PROBE_TRANSFORMS,
    READ_AT,
    REVERSAL,
    TieredCorpus,
    write_strategy_corpus,
    write_tiered_corpus,
    write_two_year_corpus,
)

from openalpha_cn import model_view, strategy_view
from openalpha_cn.backtest.strategy_backtest import EQUAL_WEIGHT_ALL_A
from openalpha_cn.domain import labels
from openalpha_cn.domain.daily_prices import session_returns
from openalpha_cn.domain.labels import LabelSessionMemo, LabelWindow
from openalpha_cn.domain.stock_universe import (
    SecurityLifecycle,
    StockUniverse,
    StockUniverseError,
)
from openalpha_cn.panel import store as store_module
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.strategy_view import (
    backtest_strategy,
    backtest_view,
    factor_ic_series,
    ic_series_request,
    ic_series_view,
    strategy_request,
)

TRAILING: Final[dict[str, Any]] = {
    "components": ((REVERSAL.qualified_key, "raw"),),
    "ic_window_sessions": 5,
    "min_ic_observations": 1,
    "ic_method": "spearman",
    "horizon_sessions": 1,
    "negative_ic": "keep_sign",
    "min_ic_securities": 3,
}
WALK_FORWARD: Final[dict[str, Any]] = {
    "family": "cross_sectional_rank",
    "features": (f"{REVERSAL.qualified_key}@raw",),
    "seed": 0,
    "code_commit": COMMIT,
    "train_sessions": 5,
    "refit_every_sessions": 2,
    "embargo_sessions": 1,
    "horizon_sessions": 1,
}


def _scan_security(self: StockUniverse, ts_code: str) -> SecurityLifecycle:
    """`StockUniverse.security` before `V2-P6-022`: a walk of the registry."""
    for entry in self.securities:
        if entry.ts_code == ts_code:
            return entry
    raise StockUniverseError(
        f"{ts_code!r} is not in the {self.snapshot_date.isoformat()} registry snapshot; "
        "an absent code is not a security that was never listed"
    )


def _fresh_window_maps(self: Any, window: LabelWindow) -> Any:
    """`_LabelInputs._window_maps` holding nothing: every call reads its sessions again."""
    return (
        tuple((day, self.bars_on(day)) for day in window.sessions),
        tuple((day, self.limits_on(day)) for day in window.sessions),
    )


@contextmanager
def _without_the_savings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every `V2-P6-022` saving replaced by what it saved: scans, recomputation, re-reads."""
    with monkeypatch.context() as patch:
        patch.setattr(StockUniverse, "security", _scan_security)
        patch.setattr(
            LabelSessionMemo,
            "session_refusals",
            lambda self, day, **inputs: labels._session_refusals(day, **inputs),
        )
        patch.setattr(
            LabelSessionMemo,
            "session_returns",
            lambda self, bar, **inputs: session_returns(bar, **inputs),
        )
        patch.setattr(model_view._LabelInputs, "_window_maps", _fresh_window_maps)
        patch.setattr(PanelStore, "_held_partition_states", lambda self, *args: None)
        patch.setattr(strategy_view, "_session_cache_depth", lambda request: 8)
        yield


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _both(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[], Mapping[str, object]]
) -> tuple[str, str]:
    shipped = _json(answer())
    with _without_the_savings(monkeypatch):
        before = _json(answer())
    return shipped, before


# --- corpora -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ten_sessions(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("equivalence-ten")
    panel = write_strategy_corpus(root)
    return PanelStore(root / "panel"), panel


@pytest.fixture(scope="module")
def two_years(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("equivalence-two-years")
    panel = write_two_year_corpus(root)
    return PanelStore(root / "panel"), panel


@pytest.fixture(scope="module")
def tiered(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, TieredCorpus]:
    root = tmp_path_factory.mktemp("equivalence-tiered")
    corpus = write_tiered_corpus(root)
    return PanelStore(root / "panel"), corpus


def _ic(
    store: PanelStore,
    *,
    tier: str = "raw",
    horizon: int,
    start: date,
    end: date,
    as_of: Any,
) -> Mapping[str, object]:
    request = ic_series_request(
        factor=REVERSAL.qualified_key,
        tier=tier,
        transform=None if tier == "raw" else PROBE_TRANSFORM.qualified_key,
        neutralization=PROBE_NEUTRALIZATION.qualified_key if tier == "neutralized" else None,
        horizon_sessions=horizon,
        ic_method="spearman",
        min_securities=3,
        start=start,
        end=end,
        as_of=as_of,
        exchange=EXCHANGE,
        transforms=PROBE_TRANSFORMS,
        neutralizations=PROBE_NEUTRALIZATIONS,
    )
    series = factor_ic_series(store, request)
    assert series.points, "an empty series compares nothing"
    return ic_series_view(series)


def _backtest(store: PanelStore, **arguments: Any) -> Mapping[str, object]:
    settings: dict[str, Any] = {
        "combine": "zscore_sum",
        "transform": None,
        "neutralization": None,
        "exchange": EXCHANGE,
        "holding_count": 3,
        "buffer_rank": None,
        "max_industry_weight": None,
        "transforms": PROBE_TRANSFORMS,
        "neutralizations": PROBE_NEUTRALIZATIONS,
        **arguments,
    }
    result = backtest_strategy(store, strategy_request(**settings))
    assert result.periods, "a backtest with no period compares nothing"
    return backtest_view(result)


# --- the IC series -----------------------------------------------------------------------------


@pytest.mark.parametrize(("horizon", "last"), [(1, 7), (5, 3)])
def test_the_ten_session_ic_series_is_unchanged(
    ten_sessions: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    horizon: int,
    last: int,
) -> None:
    store, panel = ten_sessions
    shipped, before = _both(
        monkeypatch,
        lambda: _ic(
            store,
            horizon=horizon,
            start=panel.sessions[1],
            end=panel.sessions[last],
            as_of=READ_AT,
        ),
    )

    assert shipped == before


@pytest.mark.parametrize(
    ("horizon", "last"), [(1, date(2027, 1, 19)), (5, date(2027, 1, 13)), (20, date(2026, 12, 18))]
)
def test_the_two_year_ic_series_across_new_year_is_unchanged(
    two_years: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    horizon: int,
    last: date,
) -> None:
    store, panel = two_years
    shipped, before = _both(
        monkeypatch,
        lambda: _ic(store, horizon=horizon, start=date(2026, 12, 1), end=last, as_of=panel.as_of),
    )

    assert shipped == before


@pytest.mark.parametrize("tier", ["processed", "neutralized"])
def test_a_derived_tiers_ic_series_is_unchanged(
    tiered: tuple[PanelStore, TieredCorpus], monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    store, corpus = tiered
    panel = corpus.panel
    shipped, before = _both(
        monkeypatch,
        lambda: _ic(
            store,
            tier=tier,
            horizon=1,
            start=panel.sessions[1],
            end=panel.sessions[7],
            as_of=READ_AT,
        ),
    )

    assert shipped == before


# --- the backtest ------------------------------------------------------------------------------

SOURCES: Final[dict[str, dict[str, Any]]] = {
    "static": {"components": ((REVERSAL.qualified_key, "raw", Decimal("1")),)},
    "trailing_ic": {"components": (), "trailing_ic": TRAILING},
    "walk_forward": {"components": (), "walk_forward": WALK_FORWARD},
}


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_ten_session_backtest_is_unchanged(
    ten_sessions: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    store, panel = ten_sessions
    shipped, before = _both(
        monkeypatch,
        lambda: _backtest(
            store,
            start=panel.sessions[1],
            end=panel.sessions[-1],
            as_of=READ_AT,
            rebalance_every_sessions=2 if source != "static" else 3,
            **SOURCES[source],
        ),
    )

    assert shipped == before


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_two_year_backtest_across_new_year_is_unchanged(
    two_years: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    store, panel = two_years
    settings = dict(SOURCES[source])
    if source == "trailing_ic":
        settings["trailing_ic"] = {**TRAILING, "ic_window_sessions": 10}
    if source == "walk_forward":
        settings["walk_forward"] = {**WALK_FORWARD, "train_sessions": 8, "refit_every_sessions": 4}
    shipped, before = _both(
        monkeypatch,
        lambda: _backtest(
            store,
            start=date(2026, 12, 15),
            end=panel.sessions[-1],
            as_of=panel.as_of,
            rebalance_every_sessions=4,
            benchmarks=(EQUAL_WEIGHT_ALL_A,),
            **settings,
        ),
    )

    assert shipped == before


@pytest.mark.parametrize("tier", ["processed", "neutralized"])
def test_a_derived_tiers_backtest_is_unchanged(
    tiered: tuple[PanelStore, TieredCorpus], monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    store, corpus = tiered
    panel = corpus.panel
    shipped, before = _both(
        monkeypatch,
        lambda: _backtest(
            store,
            components=((REVERSAL.qualified_key, tier, Decimal("1")),),
            transform=PROBE_TRANSFORM.qualified_key,
            neutralization=PROBE_NEUTRALIZATION.qualified_key if tier == "neutralized" else None,
            start=panel.sessions[1],
            end=panel.sessions[-1],
            as_of=READ_AT,
            rebalance_every_sessions=3,
        ),
    )

    assert shipped == before


def test_the_savings_switch_really_switches(
    two_years: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, a `_without_the_savings` that patched names nothing reads would make every
    comparison above an answer compared with itself: shipped, the memo is consulted and a held
    partition's coverage is not read again; switched off, the memo is never consulted and every
    session read assesses its partition afresh."""
    store, panel = two_years
    counts = {"memo": 0, "coverage": 0}
    real_session = LabelSessionMemo._session
    real_coverage = store_module._read_coverage

    def session(self: LabelSessionMemo, day: date) -> Any:
        counts["memo"] += 1
        return real_session(self, day)

    def coverage(*args: Any, **kwargs: Any) -> Any:
        counts["coverage"] += 1
        return real_coverage(*args, **kwargs)

    monkeypatch.setattr(LabelSessionMemo, "_session", session)
    monkeypatch.setattr(store_module, "_read_coverage", coverage)

    def run() -> dict[str, int]:
        counts.update(memo=0, coverage=0)
        _ic(store, horizon=5, start=date(2026, 12, 1), end=date(2027, 1, 13), as_of=panel.as_of)
        return dict(counts)

    run()  # every partition this question reads is now held
    shipped = run()
    with _without_the_savings(monkeypatch):
        before = run()

    assert shipped["memo"] > 0
    assert before["memo"] == 0
    assert before["coverage"] > 2 * shipped["coverage"], (shipped, before)


def test_a_backtest_reads_each_session_once_when_its_periods_are_longer_than_eight(
    two_years: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A period is walked twice -- marked by the book, then compounded for the equal-weight
    benchmark -- so a session cache shallower than the period read every session twice."""
    store, panel = two_years
    reads: list[date] = []
    real = strategy_view.load_daily_bars

    def counted(*args: Any, **kwargs: Any) -> Any:
        reads.append(kwargs["day"])
        return real(*args, **kwargs)

    monkeypatch.setattr(strategy_view, "load_daily_bars", counted)
    _backtest(
        store,
        start=date(2026, 12, 15),
        end=panel.sessions[-1],
        as_of=panel.as_of,
        rebalance_every_sessions=10,
        benchmarks=(EQUAL_WEIGHT_ALL_A,),
        **SOURCES["static"],
    )

    assert reads
    assert len(reads) == len(set(reads)), sorted(day for day in set(reads) if reads.count(day) > 1)
