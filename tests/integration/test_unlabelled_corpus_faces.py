"""Three domain refusals a label reaches around, and the sentence each one reaches a user with.

`V2-P4-080` fixed one instance of a class it also named: a domain error **designed to be a
verdict**, raised outside every `_read` guard, laundered by both product faces into `exit 5` and a
bare `500`. This file is the sibling one seam over, and it is a single `except` clause.

`factor_view._PanelInputs.label` wrapped `label_outcome` in `except LabelError` **alone**.
`label_outcome` reaches three other refusals, and none of them is a `LabelError` -- all four are
independent `ValueError` subclasses:

    StockUniverse.security          -> StockUniverseError    "an absent code is not a security
                                                              that was never listed"
    AdjustmentHistory.factor_on     -> AdjustmentHorizonError "an unfetched factor is unknown
                                                              rather than equal to the earliest"
    daily_prices.session_returns    -> PriceDataError        "the two datasets disagree about
                                                              that session's corporate action"

Measured on this file's own stores, before the fix::

    factor run                    -> exit 5, "did not finish: it raised an unhandled
                                     StockUniverseError / AdjustmentHorizonError /
                                     PriceDataError ... The exception's own message is withheld"
    POST /api/v1/factors/run      -> 500, text/plain, "Internal Server Error"

## Why no generated panel could reach two of the three

`panel_fixtures._universe_batch` gave **every** security a listing row at `LISTED_ON` and drew
from the same `SECURITIES` tuple every price builder draws from, so no generated panel could
carry a subject with a bar and no `stock_basic` row. `_factor_batch` built the full
`sessions x securities` cross product with no `omit`, unlike `_bar_batch` and `_valuation_batch`,
so no security's factor series could stop short of its bars. Both are the shape `V2-P4-080`
recorded: a fixture hides a wall not only by stopping short of it but by making a dataset look
**better-formed than the corpus it stands for**.

`universe.priced_security_absent_from_the_registry` and
`adjustment.factor_series_stops_inside_the_window` are what drop those two rows, and both drop
rather than append -- appending a ninth code, or a second factor series, would have left the
flattery exactly where it was.

**The third needed no fixture change at all**, and that is the part worth reading:
`daily.uncorroborated_factor_step` has been in `PANEL_SHAPES` since `V2-P2-000`, and the only
thing standing between it and `exit 5` was a prediction day whose label window reaches the
panel's last session. Reachability was never the obstacle for that one; nobody had asked.

## Where the absent-versus-unreadable line is drawn, for each

`V2-P4-080` kept `history is None -> False` because most of the market has no rename in any one
year. Each of these three has the same line and it falls in a different place:

- **the registry.** A code the registry *can* place -- even to say it was delisted or not yet
  listed on the session -- is a `LabelRefusal` with its own code, and stays one. A code the
  registry has no row for at all is the unreadable case, and `StockUniverse.security` already
  refuses it for that exact reason rather than answering.
- **the factor series.** A security with **no** stored adjustment history is already `None` here
  and is counted by `ICCensus.unmatched_count` -- unmatched, not refused -- and that is left
  alone. A history that exists and stops before the window is the unreadable case.
- **the price panel.** A *missing* bar is a finding: `_session_refusals` codes it
  `REFUSAL_MISSING_BAR` and that is what a caller gets. Two stored datasets that **disagree**
  about one session is the unreadable case.

Absence is a finding in all three. Contradiction, and a corpus that does not reach, are not.

## Two seams that turned out not to be defects, and are measured here rather than fixed

`V2-P4-084` was filed for a fourth site as well: `shortlist_view`'s bare
`registry.listed_on(session)`, against `factor_view._computed`'s guarded twin. It **cannot
raise**, and the reason is which day each one hands over -- `factor_view` passes `as_of`'s own
calendar date, which `request.years` does not constrain, while this face passes a session out of
a calendar read over exactly those years. Swept rather than argued, below.

The `except LabelError` arm this issue widened is itself unreachable, found by a surviving mutant
rather than by reading. It is kept rather than deleted -- removing a guard is the fail-open
direction -- and both reasons it cannot fire are pinned in the last test here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    EXCHANGE,
    SECURITIES,
    TRUNCATED_FACTOR_SECURITY_INDEX,
    TRUNCATED_FACTOR_THROUGH_INDEX,
    UNREGISTERED_SECURITY_INDEX,
    YEAR,
    _batch,
    _midnight_shanghai,
    generate_panel,
    write_generated_panel,
)
from test_factor_interfaces import BASELINE, store_three_tiers
from typer.testing import CliRunner

from openalpha_cn.api.app import FACTOR_HTTP_STATUS, create_app
from openalpha_cn.cli import FACTOR_EXIT, PanelExit, app
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    REFUSAL_MISSING_BAR,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.stock_universe import (
    SecurityLifecycle,
    StockUniverseError,
    build_stock_universe,
)
from openalpha_cn.domain.trading_calendar import CalendarHorizonError
from openalpha_cn.factor_view import FACTOR_DATE_ZONE, FactorPanelUnreadableError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_stock_universe, write_stock_universe
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import (
    ShortlistPanelUnreadableError,
    ShortlistRunBlockedError,
    _pricing_session,
    _read_registry,
)

runner = CliRunner()

MOVING: Final[str] = "daily.close_moves_between_sessions"
"""Every case's control requirement rather than any case's shape: with a flat grid every
one-session reversal is the same number and the funnel answers `degenerate_scores`, which would
refuse the control below for a reason that has nothing to do with a corpus."""

UNREGISTERED: Final[str] = SECURITIES[UNREGISTERED_SECURITY_INDEX]
TRUNCATED: Final[str] = SECURITIES[TRUNCATED_FACTOR_SECURITY_INDEX]
DISAGREEING: Final[str] = SECURITIES[2]
"""`daily.uncorroborated_factor_step`'s own `UNCORROBORATED_SECURITY_INDEX`, restated positionally
because that constant names the security and this file needs the code."""

REGISTRY_DAYS: Final[tuple[date, ...]] = (date(2026, 1, 8), date(2026, 1, 9))
"""`test_factor_interfaces.PREDICTION_DAYS`. The registry refusal is raised for every session of
every window, so any pair of prediction days reaches it."""

FACTOR_DAYS: Final[tuple[date, ...]] = (date(2026, 1, 12), date(2026, 1, 13))
"""Windows `2026-01-13..14` and `2026-01-14..15`. The truncated series is written through
`sessions[5]`, which is `2026-01-12`, so both windows ask `factor_on` past `covered_through`."""

RETURN_PATH_DAYS: Final[tuple[date, ...]] = (date(2026, 1, 13), date(2026, 1, 14))
"""Windows `2026-01-14..15` and `2026-01-15..16`. The uncorroborated restatement is on the panel's
last session, `2026-01-16`, so the second window's exit link is the one that disagrees --
`UNCORROBORATED_SECURITY_INDEX`'s docstring is why the shape sits on the last session and not
somewhere a shorter range would have reached."""


def _runtime(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    *,
    shapes: tuple[str, ...],
    days: tuple[date, ...],
) -> Path:
    runtime = tmp_path_factory.mktemp(name)
    store_three_tiers(runtime, prediction_days=days, shapes=shapes)
    return runtime


@pytest.fixture(scope="module")
def unregistered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _runtime(
        tmp_path_factory,
        "unlabelled-registry",
        shapes=(MOVING, "universe.priced_security_absent_from_the_registry"),
        days=REGISTRY_DAYS,
    )


@pytest.fixture(scope="module")
def truncated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _runtime(
        tmp_path_factory,
        "unlabelled-factors",
        shapes=(MOVING, "adjustment.factor_series_stops_inside_the_window"),
        days=FACTOR_DAYS,
    )


EARLY_DAYS: Final[tuple[date, ...]] = (date(2026, 1, 8), date(2026, 1, 9))
"""Windows `2026-01-09..12` and `2026-01-12..13`, which stop three sessions short of the
uncorroborated restatement. The `disagreeing` store carries cross sections at these **and** at
`RETURN_PATH_DAYS`, so the two ranges can be asked of one panel."""


@pytest.fixture(scope="module")
def disagreeing(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _runtime(
        tmp_path_factory,
        "unlabelled-return-paths",
        shapes=(MOVING, "daily.uncorroborated_factor_step"),
        days=(*EARLY_DAYS, *RETURN_PATH_DAYS),
    )


@pytest.fixture(scope="module")
def whole(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The control: `RETURN_PATH_DAYS` over a panel carrying none of the three shapes.

    Not decoration. Without it every assertion above is satisfied by a face that refuses
    everything, which is the shape a fail-closed fix reaches for on its own."""
    return _runtime(tmp_path_factory, "unlabelled-whole", shapes=(MOVING,), days=RETURN_PATH_DAYS)


def _arguments(runtime: Path, days: tuple[date, ...]) -> list[str]:
    arguments = ["factor", "run", "--runtime-dir", str(runtime), "--json"]
    for key, value in BASELINE.items():
        rendered = value.isoformat() if hasattr(value, "isoformat") else str(value)
        arguments.extend((f"--{key.replace('_', '-')}", rendered))
    for index, flag in enumerate(("--start", "--end")):
        arguments[arguments.index(flag) + 1] = days[index].isoformat()
    return arguments


def _run(runtime: Path, days: tuple[date, ...]) -> Any:
    return runner.invoke(app, _arguments(runtime, days))


def _body(days: tuple[date, ...]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key, value in BASELINE.items():
        body[key] = value.isoformat() if hasattr(value, "isoformat") else value
        if key in {"position_capital", "participation_cap"}:
            body[key] = str(value)
    body["start"] = days[0].isoformat()
    body["end"] = days[-1].isoformat()
    return body


def _post(runtime: Path, days: tuple[date, ...]) -> Any:
    """The route's answer as a caller over the wire sees it.

    `raise_server_exceptions=False` for `test_unnamed_session_faces._post`'s reason: with the
    default, `TestClient` re-raises the unhandled exception inside the test and the status code
    the caller would have received is never observed."""
    with TestClient(create_app(runtime_dir=runtime), raise_server_exceptions=False) as client:
        return client.post("/api/v1/factors/run", json=_body(days))


# --- the registry a priced security is absent from ------------------------------------------


def test_a_priced_security_the_registry_cannot_place_is_named_rather_than_crashed_on(
    unregistered: Path,
) -> None:
    """`exit 1`, not `exit 5`, and the domain refusal's own sentence rather than a withheld one.

    `internal_error` is the row whose whole meaning is "nothing was judged and the remedy is a bug
    report" (`cli.PanelExit`). The remedy here is a registry partition that carries the security,
    which is data, so this is `unhealthy` with the sentence saying which code and why."""
    result = _run(unregistered, REGISTRY_DAYS)

    assert result.exit_code == FACTOR_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert "unhandled" not in result.stderr
    assert UNREGISTERED in result.stderr
    assert "is not in the" in result.stderr
    assert "stock_basic" in result.stderr


def test_the_http_face_answers_a_verdict_rather_than_five_hundred_for_the_registry(
    unregistered: Path,
) -> None:
    """Asserted against `FACTOR_HTTP_STATUS` rather than a literal, because the claim is that this
    situation is `panel_unreadable` -- and a caller branching on `detail.reason` has to find it
    there rather than on a `text/plain` body Starlette wrote."""
    response = _post(unregistered, REGISTRY_DAYS)

    assert response.status_code == FACTOR_HTTP_STATUS["panel_unreadable"]
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert UNREGISTERED in response.json()["detail"]["message"]


def test_the_registry_response_body_still_does_not_name_the_store_it_read(
    unregistered: Path,
) -> None:
    """`_read`'s arrangement, which a refusal raised beside it rather than through it inherits or
    defeats: the local message names the store and the body says `<panel store>` instead."""
    response = _post(unregistered, REGISTRY_DAYS)

    assert str(unregistered) not in response.text
    assert PANEL_STORE_PLACEHOLDER in response.json()["detail"]["message"]


def test_the_sdk_raises_the_named_refusal_rather_than_the_domain_exception(
    unregistered: Path,
) -> None:
    """The in-process face, which is the one that could have kept the raw exception.

    `V2-P4-033`'s three faces resolve through one function, so a `StockUniverseError` here would
    mean the CLI and the route were catching something the SDK's own callers cannot."""
    sdk = OpenAlphaSDK(runtime_dir=unregistered)
    parameters = dict(BASELINE)
    parameters["start"], parameters["end"] = REGISTRY_DAYS[0], REGISTRY_DAYS[-1]

    with pytest.raises(FactorPanelUnreadableError, match=UNREGISTERED):
        sdk.run_factor_experiment(**parameters)


# --- the factor series that stops before the window it is asked about ------------------------


def test_a_factor_series_that_stops_inside_the_window_is_named_rather_than_crashed_on(
    truncated: Path,
) -> None:
    """The security has an adjustment history; it simply does not reach. That is a different
    state from having none at all, which `label` already answers `None` for and the census
    already counts -- see this module's docstring for the line."""
    result = _run(truncated, FACTOR_DAYS)

    assert result.exit_code == FACTOR_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert "unhandled" not in result.stderr
    assert TRUNCATED in result.stderr
    assert "adjustment factor" in result.stderr
    assert "adj_factor" in result.stderr


def test_the_http_face_answers_a_verdict_rather_than_five_hundred_for_the_factors(
    truncated: Path,
) -> None:
    response = _post(truncated, FACTOR_DAYS)

    assert response.status_code == FACTOR_HTTP_STATUS["panel_unreadable"]
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert TRUNCATED in response.json()["detail"]["message"]
    assert str(truncated) not in response.text


# --- the two return paths that disagree about one session ------------------------------------


def test_two_datasets_that_disagree_about_a_session_are_named_rather_than_crashed_on(
    disagreeing: Path,
) -> None:
    """The one of the three that needed no fixture change: `daily.uncorroborated_factor_step` has
    been declared since `V2-P2-000` and `panel doctor` reports it as a `warning`. What no test had
    done is ask a *label window* to cross the session it sits on."""
    result = _run(disagreeing, RETURN_PATH_DAYS)

    assert result.exit_code == FACTOR_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert "unhandled" not in result.stderr
    assert DISAGREEING in result.stderr
    assert "pre_close" in result.stderr


def test_the_http_face_answers_a_verdict_rather_than_five_hundred_for_the_return_paths(
    disagreeing: Path,
) -> None:
    response = _post(disagreeing, RETURN_PATH_DAYS)

    assert response.status_code == FACTOR_HTTP_STATUS["panel_unreadable"]
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert DISAGREEING in response.json()["detail"]["message"]
    assert str(disagreeing) not in response.text


# --- the control ------------------------------------------------------------------------------


def test_a_corpus_that_reaches_every_window_is_still_measured_rather_than_refused(
    whole: Path,
) -> None:
    result = _run(whole, RETURN_PATH_DAYS)

    assert result.exit_code == PanelExit.ok, result.output + result.stderr
    assert _post(whole, RETURN_PATH_DAYS).status_code == FACTOR_HTTP_STATUS["answered"]


def test_the_three_refusals_each_name_their_own_security_and_none_of_the_others(
    unregistered: Path, truncated: Path, disagreeing: Path
) -> None:
    """One message across three different faults would be a face that had learned to say no.

    Each message has to name its own security and its own dataset, because the remedy differs:
    build `stock_basic`, extend `adj_factor`, or re-fetch the session the two datasets disagree
    about."""
    messages = {
        UNREGISTERED: _post(unregistered, REGISTRY_DAYS).json()["detail"]["message"],
        TRUNCATED: _post(truncated, FACTOR_DAYS).json()["detail"]["message"],
        DISAGREEING: _post(disagreeing, RETURN_PATH_DAYS).json()["detail"]["message"],
    }

    assert len(set(messages.values())) == 3
    for subject, message in messages.items():
        assert subject in message
        assert all(other not in message for other in messages if other != subject)


# --- what the fixtures had to stop flattering before two of the three were reachable ----------


def test_the_generated_registry_really_omits_a_security_the_price_panel_prices() -> None:
    """The shape is measured off the generated batches rather than trusted from the shape id, and
    both halves: the shaped panel's registry is short by exactly that one code, and the shapeless
    panel's is not short at all."""
    shaped = generate_panel(shapes=("universe.priced_security_absent_from_the_registry",))
    plain = generate_panel()

    priced = set(shaped.batch("daily").subjects)
    registered = set(shaped.batch("stock_basic").subjects)

    assert priced - registered == {UNREGISTERED}
    assert set(plain.batch("daily").subjects) - set(plain.batch("stock_basic").subjects) == set()


def test_the_generated_factor_series_really_stops_before_the_bars_do() -> None:
    """The same two halves for `adj_factor`, and the same reason: without the shapeless control
    the assertion above is satisfied by a generator that stores no factors at all."""
    shaped = generate_panel(shapes=("adjustment.factor_series_stops_inside_the_window",))
    plain = generate_panel()

    def newest(panel: Any, dataset: str, column: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for subject, day in panel.rows_of(dataset, column):
            found[str(subject)] = max(found.get(str(subject), str(day)), str(day))
        return found

    shaped_factors = newest(shaped, "adj_factor", "factor_date")
    shaped_bars = newest(shaped, "daily", "trade_date")
    plain_factors = newest(plain, "adj_factor", "factor_date")

    assert shaped_factors[TRUNCATED] == shaped.sessions[TRUNCATED_FACTOR_THROUGH_INDEX].isoformat()
    assert shaped_factors[TRUNCATED] < shaped_bars[TRUNCATED]
    assert {code for code, day in shaped_factors.items() if day < shaped_bars.get(code, day)} == {
        TRUNCATED
    }
    assert set(plain_factors.values()) == {plain.sessions[-1].isoformat()}


def test_the_return_path_shape_needed_no_fixture_change_only_a_window_that_reaches_it(
    disagreeing: Path,
) -> None:
    """The claim the module docstring makes about the third case, made falsifiable.

    `daily.uncorroborated_factor_step` sits on the panel's **last** session, so a run whose label
    windows stop short of it is measured rather than refused -- which is why every earlier factor
    test built on this shape passed. The prediction days are the whole difference, and both
    directions are driven against **one** store so the comparison cannot be an artefact of two.
    """
    assert _run(disagreeing, EARLY_DAYS).exit_code == PanelExit.ok, _run(
        disagreeing, EARLY_DAYS
    ).stderr
    assert _run(disagreeing, RETURN_PATH_DAYS).exit_code == FACTOR_EXIT["panel_unreadable"]


# --- the fourth seam, which is the same shape and cannot fire ---------------------------------


SWEEP_YEAR_SETS: Final[tuple[tuple[int, ...], ...]] = (
    (YEAR,),
    (YEAR - 1, YEAR),
    (YEAR, YEAR + 1),
    (YEAR - 1, YEAR, YEAR + 1),
)
"""Four year sets: the panel's own, one reaching below it, one above, and one spanning both.

Below and above are the two directions `listed_on`'s two horizons face, and the spanning set is
the one where `load_stock_universe`'s downward widening and `load_trading_calendar`'s exact read
disagree about which years they cover -- which is where an invariant resting on "they are the
same years" would break if it were resting on that."""


SWEPT_INSTANTS: Final[int] = 1014
INSTANTS_REACHING_THE_BARE_CALL: Final[int] = 962
"""Twenty-five months at eighteen-hour steps -- 2025-06 to 2027-07, a year either side of the one
the panel is about -- and how many of the (instant, year set) pairs got past both guarded reads to
the bare `listed_on`.

Eighteen hours rather than a divisor of twenty-four on purpose: the step walks the four six-hour
phases of the day in turn, so the sweep sees `_pricing_session` on both sides of
`DAILY_AVAILABILITY_TIME` -- the 16:30 Asia/Shanghai boundary that decides which session an
instant prices -- instead of sampling one side of it all year at a fixed hour."""


def _store_whose_registry_reaches_past_the_panel(root: Path) -> PanelStore:
    """The generated panel plus one `YEAR + 1` lifecycle row the panel itself does not carry.

    A registry refreshed further than a historical run asks about, which is the ordinary shape --
    `stock_basic` answers `now` and is partitioned by the year a security's life *changed*, so any
    store kept up to date holds lifecycle years above the one a backtest reads. It is what makes
    `load_stock_universe`'s `unread_after` clamp bite, and therefore the only store shape on which
    `listed_on`'s `snapshot_date` arm has anything to refuse.
    """
    store = PanelStore(root / "panel")
    write_generated_panel(store, generate_panel(shapes=("universe.delisted_security",)))
    newcomer_day = date(YEAR + 1, 3, 2)
    write_stock_universe(
        store,
        _batch(
            "stock_basic",
            subjects=["000005.SZ"],
            columns=[
                PanelColumn("lifecycle_event", "string", ("listing",)),
                PanelColumn("lifecycle_date", "string", (newcomer_day.isoformat(),)),
                PanelColumn("exchange", "string", (EXCHANGE,)),
            ],
            event_time=[_midnight_shanghai(newcomer_day)],
            available_time=[_midnight_shanghai(newcomer_day)],
            fetched_at=datetime(YEAR + 1, 6, 1, 4, 0, tzinfo=UTC),
        ),
    )
    return store


def test_the_session_this_face_prices_is_always_one_the_loaded_registry_can_answer_for(
    tmp_path: Path,
) -> None:
    """`shortlist_view`'s `registry.listed_on(session)` is bare where `factor_view`'s is guarded,
    and this is the measurement that says the missing guard is unreachable rather than untested.

    `listed_on` refuses exactly two horizons: a day past `snapshot_date`, and a day before the
    first lifecycle year the read covered. `factor_view._computed` hands it
    `as_of.astimezone(FACTOR_DATE_ZONE).date()`, which `request.years` does not constrain, and
    both arms fire there -- which is why that call site has an `except StockUniverseError`. This
    face hands it a **session**, and the session comes out of a calendar loaded over the same
    `request.years` the registry was: `load_trading_calendar` reads exactly the years it is given
    and `load_stock_universe` widens only downwards, so `years_read[0] <= years[0] <=
    session.year` and `session <= min(instant's own date, 31 December of years[-1]) <=
    snapshot_date`. `newest_published_session` is never later than its own `as_of`, and a day the
    calendar cannot place raises `CalendarHorizonError`, which `_pricing_session` already turns
    into a named `blocked`.

    Swept rather than argued, because the argument is about three functions in two modules and
    any one of them could move. Every instant that gets past the two guarded reads is checked,
    and the count of those is asserted too -- without it a sweep where the calendar refused
    everything would pass while measuring nothing.

    **The sweep goes through `_pricing_session` and not through `newest_published_session`, and
    the first draft did the opposite.** Calling the inner function restates the derivation instead
    of exercising the seam: a mutant replacing the whole of `_pricing_session`'s body with
    `instant.astimezone(SHORTLIST_DATE_ZONE).date()` -- which is exactly `factor_view`'s rule, the
    one whose guard fires -- **survived** it, because the sweep never called the function the
    mutation was in. It dies here.

    **The store carries a `YEAR + 1` lifecycle partition the run does not ask for**, which is what
    clamps `snapshot_date` to 31 December of the read's own last year (`load_stock_universe`'s
    `unread_after` rule) and is the ordinary shape of a panel whose registry has been refreshed
    further than a historical run asks about. Without it the `snapshot_date` arm is slack for
    every instant and the mutant above survives on that ground instead.

    **This is deliberately not a fix.** An `except` clause here would be a branch no store shape
    can enter, and this repository has spent two issues learning what unexercised refusal paths
    cost. What the bare call needs is for the invariant to be checked, which is what this is.
    """
    store = _store_whose_registry_reaches_past_the_panel(tmp_path)
    panel = generate_panel(shapes=("universe.delisted_security",))
    calendar = panel.calendar()

    answered = 0
    raised: list[str] = []
    instant = datetime(YEAR - 1, 6, 1, tzinfo=UTC)
    swept = 0
    while instant < datetime(YEAR + 1, 7, 1, tzinfo=UTC):
        swept += 1
        for years in SWEEP_YEAR_SETS:
            try:
                session = _pricing_session(instant, calendar=calendar)
                registry = _read_registry(
                    partial(
                        load_stock_universe,
                        store,
                        years=years,
                        as_of=instant,
                        max_staleness=None,
                    ),
                    store=store,
                )
            except (ShortlistRunBlockedError, ShortlistPanelUnreadableError):
                continue
            try:
                registry.listed_on(session)
                answered += 1
            except StockUniverseError as error:
                raised.append(f"{instant.isoformat()} {years} {session}: {error}")
        instant += timedelta(hours=18)

    assert raised == []
    assert swept == SWEPT_INSTANTS
    assert answered == INSTANTS_REACHING_THE_BARE_CALL


def test_the_guarded_twin_on_the_other_face_really_does_fire(tmp_path: Path) -> None:
    """The control for the test above, and the load-bearing half of it.

    Without this, "the bare call never raises" is equally well explained by `listed_on` having no
    reachable horizon at all -- in which case `factor_view`'s guard would be the dead branch and
    this issue would have the wrong one of the pair. It is not: handed the same registry and the
    same instant, the day `factor_view._computed` derives is refused and the session this face
    derives is not even reachable, because the calendar refuses first.

    The registry is read over `YEAR` while the store also holds a `YEAR + 1` lifecycle partition,
    which is what clamps `snapshot_date` down to 31 December of the read's own last year --
    `load_stock_universe`'s `unread_after` rule, and the ordinary shape of a panel whose registry
    has been refreshed further than the run asks about.
    """
    store = _store_whose_registry_reaches_past_the_panel(tmp_path)
    panel = generate_panel(shapes=("universe.delisted_security",))
    as_of = datetime(YEAR + 1, 6, 1, 4, 0, tzinfo=UTC)

    registry = load_stock_universe(store, years=(YEAR,), as_of=as_of, max_staleness=None)

    assert registry.years_read == (YEAR,)
    assert registry.snapshot_date == date(YEAR, 12, 31)
    with pytest.raises(StockUniverseError, match="beyond the"):
        registry.listed_on(as_of.astimezone(FACTOR_DATE_ZONE).date())
    with pytest.raises(ShortlistRunBlockedError):
        _pricing_session(as_of, calendar=panel.calendar())


# --- and the arm that was already there, which no store can enter ------------------------------


CALENDAR_SESSIONS_IN_THE_YEAR: Final[int] = 259
"""How many days the generated `trade_cal` partition reports open in `YEAR`.

Pinned so the sweep below is known to cover the whole year rather than the ten sessions the
price grid happens to carry -- which is the difference between a test that can tell a whole-year
halt corpus from a half-year one and a test that cannot."""


def test_the_label_error_arm_is_unreachable_from_this_face_and_here_is_each_reason() -> None:
    """`except LabelError` was `label`'s only guard, and it catches nothing a run can produce.

    Found by a surviving mutant rather than by reading: turning this arm's
    `FactorRunBlockedError` into `FactorPanelUnreadableError` -- which would file a window fault
    under the panel's row on the HTTP face -- changes no test in this suite, because nothing
    enters the branch. The arm is **kept** rather than deleted: removing a guard is the fail-open
    direction, and the reasoning below is about three functions in two modules, any one of which
    could move. Both reasons are pinned here so that a change making the arm live turns this red
    instead of arriving at a user as `exit 5`.

    `label_outcome` can raise `LabelError` in two places and this face closes both:

    1. **`HaltCorpus.require_coverage`.** `halt_corpus_for_years` spans
       `min(years)-01-01 .. max(years)-12-31` and `_PanelInputs` builds it from `request.years`,
       while `_label_window` derives every window from a calendar `load_trading_calendar` read
       over the *same* `request.years`. Every session of every window is therefore inside the
       span by construction, and that is swept over every prediction day the generated panel has.
    2. **`window_return`'s missing bar.** It is never reached with one: `_session_refusals` codes
       an absent bar `missing_bar`, `label_outcome` collects every refusal before computing any
       return, and `window_return` runs only when that collection is empty. Driven against the
       domain rather than argued, because it is the arm a reader is likeliest to think fires --
       the sentence it would raise names the security and the session, and reads exactly like
       something a short fetch would produce.
    """
    panel = generate_panel(shapes=(MOVING,))
    calendar = panel.calendar()
    corpus = halt_corpus_for_years({}, years=(YEAR,))

    windows = []
    refused_by_the_calendar = 0
    for day in (entry.calendar_date for entry in panel.calendar_days if entry.is_trading):
        try:
            windows.append(
                build_label_window(
                    as_of=datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC),
                    zone=FACTOR_DATE_ZONE,
                    horizon=parse_horizon("1d"),
                    calendar=calendar,
                )
            )
        except CalendarHorizonError:
            refused_by_the_calendar += 1

    # Every trading day of the year, not the ten the price grid covers: the calendar spans
    # 1 January to 31 December and the halt corpus's span is derived from the same years, so a
    # sweep confined to the priced window could not tell a whole-year span from a half-year one.
    # The two at the end are the prediction days whose exit would fall past 31 December --
    # `_label_window` turns that into a named `blocked` before `label` is reached at all.
    assert len(windows) == CALENDAR_SESSIONS_IN_THE_YEAR - 2
    assert refused_by_the_calendar == 2
    for window in windows:
        assert corpus.require_coverage(window.sessions) is None

    window = windows[0]
    subject = SECURITIES[0]
    outcome = label_outcome(
        window,
        ts_code=subject,
        bars={
            window.sessions[0]: DailyBar(
                ts_code=subject,
                trade_date=window.sessions[0],
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                pre_close=10.0,
                pct_chg=0.0,
                vol=1000.0,
                amount=10000.0,
            )
        },
        factors=build_adjustment_history(
            subject,
            tuple(
                FactorObservation(ts_code=subject, observed_on=day, factor=1.0)
                for day in window.sessions
            ),
        ),
        limits={},
        halts=corpus,
        universe=build_stock_universe(
            snapshot_date=date(YEAR, 12, 31),
            securities=(
                SecurityLifecycle(ts_code=subject, exchange=EXCHANGE, listed_on=date(YEAR, 1, 2)),
            ),
        ),
    )

    assert REFUSAL_MISSING_BAR in {refusal.code for refusal in outcome.refusals}
    assert outcome.window_return is None
