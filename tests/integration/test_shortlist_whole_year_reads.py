"""The rest of the read `V2-P4-061` did not move (`V2-P4-076`).

## What was still walled, and how it was found

`V2-P4-061` put `daily`, `daily_basic` and `stk_limit` onto the as-of-sensitive session read so
that three sentences would stop being true: two days' shortlists could not be compared,
yesterday's could not be re-run, and a published list could not be audited after the fact. On a
real panel all three were **still** true, and nothing offline saw it -- four rounds of acceptance
and 4,031 tests -- because `load_shortlist_cross_section` reads four more things at the cross
section's own instant, and three of them still went through `read_if_ready`, the whole-partition
gate:

    trading calendar ... trade_cal      calendar_publication
    security registry .. stock_basic    calendar_static
    halts .............. suspend_d      daily_close
    name histories ..... namechange     calendar_static

The wall did not fall; it moved. Measured on this module's own corpus at
`d2878ca`, reading each at the earlier cross section's instant::

    stock_basic  BLOCKED  ['not_yet_knowable']; stock_basic holds information that first became
                          available at 2026-01-15T16:00:00+00:00
    suspend_d    BLOCKED  ['not_yet_knowable']; suspend_d holds information that first became
                          available at 2026-01-16T08:30:00+00:00
    namechange   BLOCKED  ['not_yet_knowable']; namechange holds information that first became
                          available at 2026-01-15T16:00:00+00:00

## Why four rounds of fixtures missed it, and what this corpus does about it

No generated panel had the shape that exposes it: a **whole-year partition whose newest row
lands on the newest session**. Measured on `generate_panel(shapes=EVERY_SHAPE)` at `d2878ca`,
partition-wide newest `available_time`::

    daily / daily_basic / stk_limit   2026-01-16T08:30Z   <- the newest session
    suspend_d                         2026-01-14T08:30Z
    namechange                        2026-01-05T16:00Z
    stock_basic                       2026-01-04T16:00Z
    trade_cal                         2025-12-31T16:00Z

Every dataset `V2-P4-061` moved reached the newest session and every dataset it did not stopped
short of it, so the residue was invisible by construction. `PANEL_SHAPES` now carries the form
for all three -- `universe.termination_on_the_newest_session`,
`suspension.halt_on_the_newest_session` and `name_history.announcement_on_the_newest_session` --
and `test_the_corpus_really_has_the_shape_that_four_rounds_of_fixtures_did_not` measures it here
rather than trusting the shape ids.

**The three do not stop at one instant, deliberately.** `suspend_d` is `daily_close` and stops at
16:30 on the newest session; `stock_basic` and `namechange` are `calendar_static` and stop at
that session's own midnight; `trade_cal` is `calendar_publication` and stops at 1 January. A
corpus in which they all stopped together could not say which door answered, and one in which
they all stopped early -- which is what every previous fixture was -- reproduces nothing at all.

## `trade_cal` is read at the same instant and is not part of the wall

Measured, not assumed: `_calendar_publication_timeline` dates every row's availability at
1 January of the row's own year, so a year partition's newest availability instant is the
*earliest* instant in it and `not_yet_knowable` cannot fire at any `as_of` inside the year. It
is the one of the four that is left where it is, and
`test_the_calendar_is_read_at_the_same_instant_and_is_not_part_of_the_wall` pins the measurement
so that a later clock change is a failure here rather than a fourth wall.

## `adj_factor` is not read by this face at all

Recorded because the brief this issue arrived with named it as one of the four.
`shortlist_view` imports six loaders from `panel_ingest` and `load_adjustment_histories` is not
among them; `test_the_shortlist_face_reads_no_adjustment_factor` holds the import graph to it.
The adjustment corpus really does have the walling shape -- it is `daily_close` over a whole
year -- which is why `test_shortlist_earlier_sessions.py::_doctor` narrows `panel doctor` to the
price pair to keep `adj_factor`'s own whole-partition refusals out of its report. It is simply
not on this path.

## Withheld against absent, per dataset

The objection every filtered read has to answer is *can this caller tell a withheld row from an
absent one*. For all three movers the availability instant is a **fixed function of the event
date** -- `calendar_static` makes them equal, `daily_close` adds exactly 90 minutes within the
same day -- so the per-event-date reconciliation cannot disagree on a partition whose rows carry
the provider's own clock, and it fires on one whose rows say something else. That is a backstop
rather than decoration, and it is what the three `..._is_refused_rather_than_answered_short`
tests below store a partition to reach. The *reachable* half is the other one: a date the census
never counted is **absent** and is answered, which for `suspend_d` is the crux -- an absent halt
row and a withheld one both read as "not halted".

All three shipped faces are driven, `V2-P4-033`'s reason.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement
from openalpha_cn.sdk import OpenAlphaSDK

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

WALLING_SHAPES: Final[tuple[str, ...]] = (
    "universe.termination_on_the_newest_session",
    "suspension.halt_on_the_newest_session",
    "name_history.announcement_on_the_newest_session",
)
"""The three forms that put a whole-year partition's newest row on the newest session.

One per mover, and each is the *only* thing its dataset's shape adds: the termination is dated
on the last session so the name is listed for every session but that one, the halt is timed so
the name keeps its bar, and the rename carries no risk warning so it moves no screen verdict.
`tests/unit/test_panel_fixtures.py` holds each detector to its own dataset.
"""

EARLIER_BUILD: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-01-15 -- after that session's close and therefore about it."""

NEWEST_BUILD: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
"""The panel's newest session, after its close. The one instant that worked before this issue."""

BUILD_SIGNS: Final[tuple[tuple[datetime, float], ...]] = (
    (EARLIER_BUILD, 1.0),
    (NEWEST_BUILD, -1.0),
)
"""Two cross sections, at two instants, with opposite signs so the two shortlists differ."""

EARLIER_AS_OF: Final[datetime] = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
"""07:00 Asia/Shanghai on 2026-01-16, the morning after `EARLIER_BUILD` and before the newest
one exists -- so the run's own day is **not** the session it resolves, which is what separates a
face that prices on the day the question was asked from one that prices on the day the values
were computed."""

NEWEST_AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""After 2026-01-16 published; resolves `NEWEST_BUILD`."""

BEYOND_THE_PANEL_AS_OF: Final[datetime] = datetime(2026, 1, 19, 7, 0, tzinfo=UTC)
"""15:00 Asia/Shanghai on Monday 2026-01-19, ninety minutes before that session becomes
knowable and three days past anything the store holds."""

BEFORE_ANY_BUILD_AS_OF: Final[datetime] = datetime(2026, 1, 4, 9, 0, tzinfo=UTC)
"""Before the panel's first session published, and before any cross section was built."""

UNSTORED_SESSION: Final[date] = date(2026, 1, 19)
"""A Monday the stored calendar reports open and the price panel does not reach."""

BEFORE_THE_UNSTORED_SESSION_PUBLISHED: Final[datetime] = BEYOND_THE_PANEL_AS_OF

EARLIER_SESSION: Final[date] = date(2026, 1, 15)
NEWEST_SESSION: Final[date] = date(2026, 1, 16)

TERMINATED_SECURITY: Final[str] = SECURITIES[4]
"""The name `universe.termination_on_the_newest_session` delists on 2026-01-16.

`delist_date` is exclusive, so it is listed on 2026-01-15 and not on 2026-01-16 -- which is why
the two runs below answer on universes of different sizes, out of one registry partition.
"""

SHORTLIST_SIZE: Final[int] = 2
POSITION_CAPITAL: Final[str] = "1250"
HORIZON: Final[str] = "5d"

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": SHORTLIST_SIZE,
    "position_capital": POSITION_CAPITAL,
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "horizon": HORIZON,
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}
"""One declaration for all three faces, with every gate bar inert, so a refusal below is the
panel read's and never a gate's."""


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One store: the walling corpus and one raw factor partition holding two cross sections."""
    root = tmp_path_factory.mktemp("shortlist-whole-year-reads")
    store = PanelStore(root / "panel")
    panel = generate_panel(shapes=WALLING_SHAPES)
    write_generated_panel(store, panel)
    calendar = panel.calendar()
    builds: tuple[FactorPanel, ...] = tuple(
        compute_factor(
            store,
            REVERSAL,
            as_of=instant,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements={
                "daily": daily_requirement(
                    calendar, years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
                )
            },
            code_commit=COMMIT,
            built_at=instant,
            evaluators={
                REVERSAL.qualified_key: (
                    lambda context, sign=sign: (
                        sign * (SECURITIES.index(context.subject) + 1) / 100.0
                    )
                )
            },
        )
        for instant, sign in BUILD_SIGNS
    )
    write_factor_panels(store, builds)
    return root


def _cli_arguments(runtime_dir: Path, *, as_of: datetime) -> list[str]:
    return [
        "shortlist",
        "run",
        "--runtime-dir",
        str(runtime_dir),
        "--component",
        "reversal_1d/v1=1.0",
        "--tier",
        str(BASELINE["tier"]),
        "--shortlist-size",
        str(BASELINE["shortlist_size"]),
        "--position-capital",
        str(BASELINE["position_capital"]),
        "--year",
        str(YEAR),
        "--exchange",
        str(BASELINE["exchange"]),
        "--horizon",
        str(BASELINE["horizon"]),
        "--as-of",
        as_of.isoformat(),
        "--min-tradable-ratio",
        str(BASELINE["minimum_tradable_ratio"]),
        "--min-researched-ratio",
        str(BASELINE["minimum_researched_ratio"]),
        "--max-ranking-age-days",
        str(BASELINE["maximum_ranking_age_days"]),
        "--code-commit",
        COMMIT,
        "--config-digest",
        CONFIG_DIGEST,
        "--json",
    ]


def _run_cli(runtime_dir: Path, *, as_of: datetime) -> tuple[int, str]:
    result = CliRunner().invoke(app, _cli_arguments(runtime_dir, as_of=as_of))
    return result.exit_code, result.output


def _rest_body(as_of: datetime) -> dict[str, Any]:
    body = {key: value for key, value in BASELINE.items() if key not in {"years", "components"}}
    body["years"] = list(BASELINE["years"])
    body["components"] = [dict(component) for component in BASELINE["components"]]
    body["as_of"] = as_of.isoformat()
    return body


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime_dir)) as client:
        yield client


def _doctor(
    runtime_dir: Path, *, session: date, as_of: datetime, datasets: tuple[str, ...]
) -> dict[str, Any]:
    """`openalpha panel doctor` over `datasets`, as data.

    The one shipped face that takes a session as an argument, which is what the guard above
    needs and what `shortlist run` deliberately does not have.
    """
    arguments = ["panel", "doctor", "--runtime-dir", str(runtime_dir)]
    for dataset in datasets:
        arguments.extend(("--dataset", dataset))
    arguments.extend(
        (
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--session",
            session.isoformat(),
            "--as-of",
            as_of.isoformat(),
            "--json",
        )
    )
    result = CliRunner().invoke(app, arguments)
    payload: dict[str, Any] = json.loads(result.output)
    return payload


# --- the corpus really carries the form no previous fixture had ------------------------------


def test_the_corpus_really_has_the_shape_that_four_rounds_of_fixtures_did_not(
    runtime_dir: Path,
) -> None:
    """Every dataset this face reads, and where its whole-year partition stops.

    Read off the stored coverage records rather than off the shape ids, because "asking for the
    shape produces the shape" is the generator's contract and *this* corpus reproducing the
    defect is what these tests rest on. Three of the four stop at or after the earlier cross
    section's own instant, which is what makes the earlier run a wall rather than a pass.
    """
    store = PanelStore(runtime_dir / "panel")

    stops = {
        dataset: store.read_coverage(dataset, YEAR).max_available_time  # type: ignore[union-attr]
        for dataset in ("trade_cal", "stock_basic", "suspend_d", "namechange", "daily")
    }

    assert stops == {
        # 1 January of the partition's own year: the earliest instant in it, not the newest.
        "trade_cal": datetime(2025, 12, 31, 16, 0, tzinfo=UTC),
        # Midnight Asia/Shanghai on the newest session -- `calendar_static`, twice.
        "stock_basic": datetime(2026, 1, 15, 16, 0, tzinfo=UTC),
        "namechange": datetime(2026, 1, 15, 16, 0, tzinfo=UTC),
        # 16:30 Asia/Shanghai on the newest session -- `daily_close`, twice.
        "suspend_d": datetime(2026, 1, 16, 8, 30, tzinfo=UTC),
        "daily": datetime(2026, 1, 16, 8, 30, tzinfo=UTC),
    }
    assert stops["stock_basic"] > EARLIER_BUILD
    assert stops["namechange"] > EARLIER_BUILD
    assert stops["suspend_d"] > EARLIER_BUILD
    assert stops["trade_cal"] < EARLIER_BUILD


def test_the_calendar_is_read_at_the_same_instant_and_is_not_part_of_the_wall() -> None:
    """The fourth read, and the measurement that says it may stay on the unfiltered door.

    `trade_cal` is clocked `calendar_publication`: every row of year Y is dated available at
    1 January of Y, so a year partition's `max_available_time` is the earliest instant in it and
    `not_yet_knowable` cannot fire at any `as_of` inside the year. Moving it would have been
    three datasets' worth of machinery for a refusal that has no way to happen.

    Pinned against the provider's own rule rather than against the fixture, so a clock change
    fails here instead of quietly opening a fourth wall.
    """
    from openalpha_cn.providers.tushare import (
        TUSHARE_DATASETS,
        ClockStrategy,
        _calendar_publication_timeline,
    )

    descriptor = next(item for item in TUSHARE_DATASETS if item.dataset == "trade_cal")
    assert descriptor.clock is ClockStrategy.calendar_publication

    december = _calendar_publication_timeline(
        {"cal_date": "20261231"}, "cal_date", datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
    )
    january = _calendar_publication_timeline(
        {"cal_date": "20260105"}, "cal_date", datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
    )

    assert december.available_time == january.available_time
    assert december.available_time == datetime(2025, 12, 31, 16, 0, tzinfo=UTC)


def test_the_shortlist_face_reads_no_adjustment_factor() -> None:
    """`adj_factor` is not one of the four, and the brief this issue arrived with said it was.

    Held on the import graph rather than on a grep of the call sites, because a loader reached
    through an alias would satisfy the second and not the first.

    `newest_published_session` joined the set in `V2-P4-077` and is the one entry here that opens
    no partition: it takes a `TradingCalendar` and an instant and answers which session had
    published, off `_sessions_published_through` -- the same function `_read_visible_price_session`
    bounds its own refusal by, imported rather than restated so `_pricing_session` cannot come to
    disagree with the door it then knocks on.
    """
    import openalpha_cn.shortlist_view as module
    from openalpha_cn import panel_ingest

    reachable = {
        name
        for name, value in vars(module).items()
        if getattr(value, "__module__", None) == panel_ingest.__name__
    }

    assert "load_adjustment_histories" not in reachable
    assert reachable == {
        "load_daily_bars",
        "load_name_histories",
        "load_price_limits",
        "load_stock_universe",
        "load_suspensions",
        "load_trading_calendar",
        "newest_published_session",
    }


# --- acceptance 1: two cross sections, both screenable, each on its own session ---------------


def test_two_cross_sections_are_both_screenable_each_against_its_own_session(
    runtime_dir: Path,
) -> None:
    """The three sentences `V2-P4-061` set out to falsify, on a corpus with real shape.

    Before this issue the earlier run exited `1` with `the security registry could not be read
    ... ['not_yet_knowable']`; behind that sat `suspend_d` and `namechange` with the same
    verdict. Asserted as a pair on one store, because either half alone is passed by a tree that
    answers every question with the newest session's market.
    """
    earlier_exit, earlier_output = _run_cli(runtime_dir, as_of=EARLIER_AS_OF)
    newest_exit, newest_output = _run_cli(runtime_dir, as_of=NEWEST_AS_OF)

    assert earlier_exit == 0, earlier_output
    assert newest_exit == 0, newest_output
    earlier = json.loads(earlier_output)
    newest = json.loads(newest_output)
    assert earlier["cross_section"]["pricing_session"] == EARLIER_SESSION.isoformat()
    assert newest["cross_section"]["pricing_session"] == NEWEST_SESSION.isoformat()
    assert earlier["cross_section"]["as_of"] == EARLIER_BUILD.isoformat()
    assert newest["cross_section"]["as_of"] == NEWEST_BUILD.isoformat()
    earlier_names = [candidate["subject"] for candidate in earlier["funnel"]["shortlist"]]
    newest_names = [candidate["subject"] for candidate in newest["funnel"]["shortlist"]]
    assert len(earlier_names) == SHORTLIST_SIZE
    assert len(newest_names) == SHORTLIST_SIZE
    assert earlier_names != newest_names


def test_the_earlier_run_reads_the_registry_as_it_stood_and_not_as_it_ends(
    runtime_dir: Path,
) -> None:
    """The registry answer is the earlier session's, not the partition's.

    `TERMINATED_SECURITY` delists on 2026-01-16 and `delist_date` is exclusive, so it is in the
    2026-01-15 cross section and not in the 2026-01-16 one -- out of **one** lifecycle partition
    read at two instants. Without this the test above passes on a face that read the whole
    partition and then filtered a day off the end of it, which is a different thing: the row it
    must not see is one whose *availability* post-dates the read, and this is what says the
    filter is on the clock rather than on the value.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)

    earlier = sdk.run_shortlist(**BASELINE, as_of=EARLIER_AS_OF)
    newest = sdk.run_shortlist(**BASELINE, as_of=NEWEST_AS_OF)

    assert TERMINATED_SECURITY in earlier.universe
    assert TERMINATED_SECURITY not in newest.universe
    assert len(earlier.universe) == len(newest.universe) + 1


def test_the_http_face_answers_the_earlier_cross_section_too(rest: TestClient) -> None:
    """`V2-P4-033`'s reason: three faces of one declaration that could drift apart."""
    answered = rest.post("/api/v1/shortlists/run", json=_rest_body(EARLIER_AS_OF))

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["cross_section"]["pricing_session"] == EARLIER_SESSION.isoformat()
    assert body["cross_section"]["as_of"] == EARLIER_BUILD.isoformat()
    assert len(body["funnel"]["shortlist"]) == SHORTLIST_SIZE


# --- acceptance 2: an instant past everything stored is still refused by name -----------------


def test_a_session_past_the_panel_is_still_refused_by_name_on_every_dataset_this_read_takes(
    runtime_dir: Path,
) -> None:
    """Making yesterday screenable must not make tomorrow readable.

    Driven through `panel doctor --session/--as-of` for the reason `test_shortlist_earlier_
    sessions.py` states: `shortlist run` cannot name a session, it derives one from the resolved
    cross section's instant, so the only way to ask about a session past the panel is the face
    that takes one as an argument. 2026-01-19 is a Monday the stored calendar reports open and
    the price panel does not reach, asked ninety minutes before its own 16:30.

    Every dataset this issue moved is in the `--dataset` list, so the refusal has to survive
    **all three** new doors rather than only the price one: if the registry, the halt corpus or
    the rename corpus had been widened into answering for a day past their own coverage, the
    report would carry a check that ran.
    """
    report = _doctor(
        runtime_dir,
        session=UNSTORED_SESSION,
        as_of=BEFORE_THE_UNSTORED_SESSION_PUBLISHED,
        datasets=("daily", "daily_basic", "stock_basic", "suspend_d", "namechange"),
    )
    checks = {check["name"]: check for check in report["cross_checks"]}

    assert checks["close_agreement"]["ran"] is False
    reason = str(checks["close_agreement"]["skipped_reason"])
    assert "daily cannot be read for 2026-01-19" in reason
    assert "that session had not published yet" in reason
    assert "look-ahead" in reason


def test_an_as_of_before_anything_was_knowable_is_still_refused_by_name(
    runtime_dir: Path,
) -> None:
    """The other end of the same guard, on the shortlist face itself.

    At an `as_of` before the panel's first session published there is no stored cross section to
    resolve, and the refusal names the tier, the factor and the instant rather than handing back
    an empty list -- which is what a read widened by one session too many would do.
    """
    exit_code, output = _run_cli(runtime_dir, as_of=BEFORE_ANY_BUILD_AS_OF)

    assert exit_code != 0
    assert "no raw-tier cross section of ['reversal_1d/v1'] is stored" in output
    assert "visible at 2026-01-04T09:00:00+00:00" in output


def test_an_as_of_past_the_panel_still_answers_only_the_newest_stored_cross_section(
    runtime_dir: Path,
) -> None:
    """The direction the widening could have leaked in, asserted rather than assumed.

    `shortlist run` at an `as_of` three days past anything the store holds resolves the **newest
    stored** cross section, prices it on that cross section's own session, and says so. What it
    must not do is price it on 2026-01-19 -- the day the question was asked -- which is what a
    face that resolved the session from `request.as_of` would do, and which every dataset here
    would now answer for.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)

    answered = sdk.run_shortlist(**BASELINE, as_of=BEYOND_THE_PANEL_AS_OF)

    assert answered.pricing_session == NEWEST_SESSION
    assert answered.cross_section_as_of == NEWEST_BUILD
