"""What `openalpha shortlist run` needs, and when it can answer (`V2-P4-077`, `V2-P4-078`).

Two defects the first end-to-end run over live data found, both ordinary use rather than edge
cases, and both about the same thing from two sides: **this command has prerequisites nothing
states, and refusals that do not say what to do about them.**

## `V2-P4-077`, as filed and as it reproduces now

As filed: `namechange` is announcement-dated and fetched whole at the build's `--as-of`, so an
overnight `panel build` stamped `datetime.now(UTC)` at 19:01Z -- already the next day in
Shanghai -- stored a rename announced 2026-08-20 while `daily` legitimately stopped at
2026-08-19. Two refusals with no gap between them: below the rename's own availability the
rename corpus was `not_yet_knowable`, and at or above it the cross section's Shanghai day was
2026-08-20, a session that had not published.

**The first half no longer reproduces, and that is `V2-P4-076`'s doing.** Measured here at
`33d52e6` on `generate_panel(shapes=WALLING_SHAPES)`: `load_name_histories` answers 8 histories
at 2026-01-15T09:00Z and at 2026-01-15T16:30Z, both below the `namechange` partition's own
`max_available_time` of 2026-01-15T16:00Z... which is to say the whole-partition gate it used
to take is gone. `076` moved it onto `_read_visible_event_dated_rows`, which filters rows and
reconciles them per event date, so a rename announced ahead of the read is simply not visible
rather than fatal to the year.

**The second half reproduces, and it is worse than filed.** The wedge is not `panel build`'s
`--as-of` at all -- it is `factor build`'s. Measured at `33d52e6`, one cross section stamped
2026-01-15T16:30Z (00:30 Asia/Shanghai on Friday 2026-01-16), swept over every `as_of`::

    2026-01-15T09:00Z  exit 1  no raw-tier cross section ... visible at ...
    2026-01-15T16:00Z  exit 1  no raw-tier cross section ... visible at ...
    2026-01-15T16:30Z  exit 1  daily cannot be read for 2026-01-16 ... had not published yet
    2026-01-16T08:30Z  exit 1  daily cannot be read for 2026-01-16 ... had not published yet
    2026-01-16T12:00Z  exit 1  daily cannot be read for 2026-01-16 ... had not published yet
    2026-01-20T12:00Z  exit 1  daily cannot be read for 2026-01-16 ... had not published yet

There is no gap, and there is no later instant that repairs it: `_resolve_instant` reads the
cross section's own stored `as_of`, so the session is decided once, at build time, and asking
again days afterwards asks the identical unanswerable question. Nor is 19:01Z special. The
window runs from midnight Asia/Shanghai to that session's 16:30, so **every** cross section
built during a trading day's own morning -- 09:00 in Shanghai, before the market opens -- was
permanently unscreenable.

## Why `_pricing_session` was asking the wrong question

It resolved the cross section's instant to that instant's own Shanghai *calendar day*, walking
back only when the day was not an open session. At 00:30 on Friday nothing about Friday was
knowable: the values were computed from Thursday's close, and asking the price plane for
Friday's bars was asking for a session the signal had never seen. The price plane refused,
correctly, and the refusal was the only thing standing between this face and a one-session
look-ahead.

The session is now the newest one that had **published** at that instant --
`panel_ingest.newest_published_session`, which reads the same 16:30 `DAILY_AVAILABILITY_TIME`
the provider dates `available_time` at. That is strictly more conservative than the old rule and
never later than it: for an instant at or after its own day's 16:30 the two agree exactly, and
they can differ only where the old rule refused. `test_a_cross_section_built_after_the_close_
still_prices_its_own_session` holds the half that must not move, and
`test_the_new_session_rule_is_never_later_than_the_one_it_replaces` computes both halves over a
whole year rather than arguing them -- 16,735 half-hourly instants, 0 later, 8,518 identical,
8,217 different and none of those on a session the old rule could have been answered for.

**8,217 of 16,735 is the number worth reading twice.** Just under half of every instant in the
year produced a permanently unscreenable cross section, so `V2-P4-077` arriving as a story about
a 19:01Z run understates it by a wide margin: the defect covered every build between midnight and
16:30 Asia/Shanghai, which is most of a working day.

**The guard is untouched.** `panel doctor --session 2026-01-16 --as-of <00:30 Friday>` still
refuses by name; what changed is that this face stopped asking it that question.

## `V2-P4-078`: five targets, and nothing said so

`shortlist run` reaches `NameHistory.risk_warning_on` through `_bars_on` for every `MarketBar`'s
`is_st`, so it **must** have `namechange`. `openalpha factor build --tier raw` neither needs nor
fetches it -- measured below -- which is how a green factor build and a red shortlist arrive on
the same store. The refusal named the partition and not the command:

    the name histories could not be read out of ...: the rename corpus cannot be read at
    2026-01-15T09:00:00+00:00: ['partition_missing', 'field_missing']; no partition is
    registered for namechange year=2026; 5 required field(s) are absent from namechange

The bar is `panel_view.NO_CALENDAR_REMEDY`, which the product acceptance called the standard for
the whole codebase, and it is met here for all five: `shortlist_view.SHORTLIST_PANEL_DATASETS`
maps each dataset this face reads to the `panel build` target that writes it, `_read` appends
the command when the store holds no partition of that dataset at all, and the command's own
`--help` names the five before a user can reach a refusal.

`adj_factor` is deliberately **not** among them, measured rather than assumed: with it omitted
both `factor build --tier raw` and `shortlist run` still answer.

All three faces are driven, `V2-P4-033`'s reason.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import PANEL_BUILD_TARGETS, app
from openalpha_cn.domain.trading_calendar import TradingCalendarError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import (
    _sessions_published_through,
    daily_requirement,
    newest_published_session,
)
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import SHORTLIST_DATE_ZONE, SHORTLIST_PANEL_DATASETS

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

WALLING_SHAPES: Final[tuple[str, ...]] = (
    "universe.termination_on_the_newest_session",
    "suspension.halt_on_the_newest_session",
    "name_history.announcement_on_the_newest_session",
)
"""`test_shortlist_whole_year_reads.py`'s corpus: every whole-year partition's newest row lands
on the newest priced session, which is the form a real panel has and no fixture had before
`V2-P4-076`."""

THURSDAY: Final[date] = date(2026, 1, 15)
FRIDAY: Final[date] = date(2026, 1, 16)
"""The panel's last two sessions. Friday is the newest one it holds."""

AFTER_THE_CLOSE: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on Thursday: after that session's 16:30 and therefore about it. The
instant the whole design was written for, and the half of `_pricing_session` that must not
move."""

OVERNIGHT: Final[datetime] = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
"""00:30 Asia/Shanghai on Friday 2026-01-16. The rollover `V2-P4-077` is about: the calendar day
has turned over onto an open session, and nothing about that session is knowable for another
sixteen hours."""

MORNING: Final[datetime] = datetime(2026, 1, 16, 1, 0, tzinfo=UTC)
"""09:00 Asia/Shanghai on Friday, half an hour before the market opens. The same defect, at an
hour nobody would call overnight, which is why the window matters more than the rollover."""

SHORTLIST_SIZE: Final[int] = 2
MISSING_DATASET_YEARS: Final[tuple[int, ...]] = (YEAR,)

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": SHORTLIST_SIZE,
    "position_capital": "1250",
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "horizon": "5d",
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}
"""One declaration for all three faces, every gate bar inert, so a refusal below is a panel
read's and never a gate's."""


def _cross_section(store: PanelStore, *, instant: datetime) -> FactorPanel:
    """One raw cross section at `instant`, computed against `store`.

    The evaluator is the subject's own index so the scores are distinct and the funnel cuts a
    real shortlist; a factor computed off the fixture's flat closes is degenerate and every
    answer below would be `degenerate_scores` rather than a list.
    """
    panel = generate_panel(shapes=WALLING_SHAPES)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: (
                lambda context: (SECURITIES.index(context.subject) + 1) / 100.0
            )
        },
    )


def _panel(root: Path, *, datasets: tuple[str, ...] | None = None, halts: bool = True) -> Path:
    store = PanelStore(root / "panel")
    write_generated_panel(
        store, generate_panel(shapes=WALLING_SHAPES), datasets=datasets, halts=halts
    )
    return root


def _arguments(
    runtime_dir: Path,
    *,
    as_of: datetime,
    exchange: str = str(BASELINE["exchange"]),
    json_output: bool = True,
) -> list[str]:
    arguments = [
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
        exchange,
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
    ]
    if json_output:
        arguments.append("--json")
    return arguments


def _cli(runtime_dir: Path, *, as_of: datetime, json_output: bool = True) -> tuple[int, str]:
    result = CliRunner().invoke(app, _arguments(runtime_dir, as_of=as_of, json_output=json_output))
    return result.exit_code, result.output


def _rest_body(as_of: datetime) -> dict[str, Any]:
    body = {key: value for key, value in BASELINE.items() if key not in {"years", "components"}}
    body["years"] = list(BASELINE["years"])
    body["components"] = [dict(component) for component in BASELINE["components"]]
    body["as_of"] = as_of.isoformat()
    return body


# --- V2-P4-077: the overnight cross section -------------------------------------------------


@pytest.fixture(scope="module")
def overnight_runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One store holding exactly one cross section, stamped 00:30 Asia/Shanghai on Friday.

    One and not two, deliberately: with a second cross section built after Thursday's close an
    `as_of` between the two answers, and the finding is that there is **no** such instant.
    """
    root = tmp_path_factory.mktemp("shortlist-overnight")
    _panel(root)
    store = PanelStore(root / "panel")
    write_factor_panels(store, (_cross_section(store, instant=OVERNIGHT),))
    return root


@pytest.fixture(scope="module")
def after_the_close_runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same store with the cross section stamped 17:00 Asia/Shanghai on Thursday."""
    root = tmp_path_factory.mktemp("shortlist-after-the-close")
    _panel(root)
    store = PanelStore(root / "panel")
    write_factor_panels(store, (_cross_section(store, instant=AFTER_THE_CLOSE),))
    return root


def test_the_rename_corpus_no_longer_refuses_the_whole_year_below_its_newest_row(
    overnight_runtime: Path,
) -> None:
    """`V2-P4-077`'s first half, re-measured: it does not reproduce, and `V2-P4-076` is why.

    The `namechange` partition's newest row is announced on the newest session and is knowable
    only from that session's own midnight, which is **after** both instants asked here. Under
    the whole-partition gate the corpus answered neither; under the per-event-date read it
    answers both, with the row that is not yet knowable simply absent from the histories.

    Asserted through `load_name_histories` rather than through a face, because the point is
    which door the read takes and a green `shortlist run` would pass on any door that answers.
    """
    from openalpha_cn.panel_ingest import load_name_histories

    store = PanelStore(overnight_runtime / "panel")
    stop = store.read_coverage("namechange", YEAR)
    assert stop is not None
    assert stop.max_available_time == datetime(2026, 1, 15, 16, 0, tzinfo=UTC)

    below = load_name_histories(store, years=(YEAR,), as_of=AFTER_THE_CLOSE, max_staleness=None)
    at = load_name_histories(store, years=(YEAR,), as_of=OVERNIGHT, max_staleness=None)

    assert stop.max_available_time > AFTER_THE_CLOSE
    assert len(below) == len(SECURITIES)
    assert len(at) == len(SECURITIES)


def test_an_overnight_cross_section_is_priced_on_the_session_that_had_published(
    overnight_runtime: Path,
) -> None:
    """`V2-P4-077`'s acceptance: the rollover leaves an answerable instant.

    The cross section stands at 00:30 on Friday and is priced against **Thursday**, the newest
    session that had published when its values were computed. Before this it asked for Friday
    and was refused at every `as_of` a caller could name.
    """
    exit_code, output = _cli(overnight_runtime, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC))

    assert exit_code == 0, output
    answered = json.loads(output)
    assert answered["cross_section"]["as_of"] == OVERNIGHT.isoformat()
    assert answered["cross_section"]["pricing_session"] == THURSDAY.isoformat()
    assert len(answered["funnel"]["shortlist"]) == SHORTLIST_SIZE


def test_a_cross_section_built_after_the_close_still_prices_its_own_session(
    after_the_close_runtime: Path,
) -> None:
    """The half of `_pricing_session` that must not move, held beside the half that did.

    17:00 Asia/Shanghai on Thursday is after that session's 16:30, so the newest published
    session is Thursday itself. A rule that walked back unconditionally -- or that resolved the
    session off the request rather than off the cross section -- would price Wednesday here and
    still pass the test above.
    """
    exit_code, output = _cli(
        after_the_close_runtime, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
    )

    assert exit_code == 0, output
    answered = json.loads(output)
    assert answered["cross_section"]["as_of"] == AFTER_THE_CLOSE.isoformat()
    assert answered["cross_section"]["pricing_session"] == THURSDAY.isoformat()


def test_a_cross_section_built_in_the_morning_is_priced_on_the_previous_session(
    tmp_path: Path,
) -> None:
    """The window, not the rollover: 09:00 Asia/Shanghai on an open session, before it opens.

    `V2-P4-077` arrived as a story about a run that started at 19:01Z, which reads as an odd
    hour. The defect covered every instant from midnight to 16:30 Asia/Shanghai, so a build
    during the morning of a trading day was as unscreenable as one at half past midnight, and
    this is the case a reader would not have guessed from the report.
    """
    root = _panel(tmp_path / "runtime")
    store = PanelStore(root / "panel")
    write_factor_panels(store, (_cross_section(store, instant=MORNING),))

    exit_code, output = _cli(root, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC))

    assert exit_code == 0, output
    assert json.loads(output)["cross_section"]["pricing_session"] == THURSDAY.isoformat()


def test_the_overnight_cross_section_has_no_as_of_it_cannot_answer(
    overnight_runtime: Path,
) -> None:
    """The finding stated positively: there is no longer a region with no gap in it.

    Every `as_of` from before the build to days after it either answers, or is refused for the
    one reason that is not this defect -- that no cross section was visible yet. The sweep is
    what says so; a single instant would pass on a fix that moved the wall rather than removing
    it, which is what `V2-P4-061` did and `V2-P4-076` measured.
    """
    swept = {
        as_of: _cli(overnight_runtime, as_of=as_of)
        for as_of in (
            datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2026, 1, 15, 16, 0, tzinfo=UTC),
            OVERNIGHT,
            datetime(2026, 1, 16, 8, 30, tzinfo=UTC),
            datetime(2026, 1, 16, 12, 0, tzinfo=UTC),
            datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
        )
    }

    answered = {as_of for as_of, (code, _out) in swept.items() if code == 0}
    refused = {as_of: out for as_of, (code, out) in swept.items() if code != 0}

    assert answered == {as_of for as_of in swept if as_of >= OVERNIGHT}
    assert set(refused) == {as_of for as_of in swept if as_of < OVERNIGHT}
    for as_of, output in refused.items():
        assert "no raw-tier cross section" in output, (as_of, output)


def test_the_new_session_rule_is_never_later_than_the_one_it_replaces() -> None:
    """The safety argument for `V2-P4-077`, computed rather than asserted in prose.

    Two claims stand behind the fix, and both are about *every* instant rather than the handful
    the runs above name. Over a whole year of the fixture calendar at half-hourly steps:

    - the new session is **never later** than the calendar-day rule, so nothing that already
      answered can now be priced against a later market than it was;
    - where the two differ, the old rule named a session that had **not published** at that
      instant -- which is to say every difference is a case that used to be refused outright.

    Measured at `33d52e6`: 16,735 instants, 0 later, 8,518 identical, 8,217 different and 0 of
    those on a session the old rule could have been answered for. **Half of every instant in the
    year produced an unscreenable cross section**, which is the number that says `V2-P4-077` was
    an ordinary-use defect and not an overnight one.

    Held against the old rule spelled out here rather than against a stored expectation, because
    what has to stay true is a relation between two rules and a stored list of sessions would go
    stale the moment the fixture calendar moved.
    """
    calendar = generate_panel(shapes=WALLING_SHAPES).calendar()
    later: list[datetime] = []
    differ: list[datetime] = []
    identical = 0
    differed_on_a_published_session: list[datetime] = []

    instant = datetime(2026, 1, 5, tzinfo=UTC)
    while instant < datetime(2026, 12, 20, tzinfo=UTC):
        asked = instant
        instant += timedelta(minutes=30)
        day = asked.astimezone(SHORTLIST_DATE_ZONE).date()
        try:
            new = newest_published_session(calendar, as_of=asked)
            old = day if calendar.is_trading_day(day) else calendar.previous_trading_day(day)
        except TradingCalendarError:
            continue
        if new > old:
            later.append(asked)
        elif new < old:
            differ.append(asked)
            if old <= _sessions_published_through(asked, SHORTLIST_DATE_ZONE):
                differed_on_a_published_session.append(asked)
        else:
            identical += 1

    assert later == []
    assert differed_on_a_published_session == []
    assert len(differ) > 0
    assert identical > 0
    assert len(differ) + identical == 16_735


def test_the_look_ahead_guard_that_refused_the_overnight_build_is_untouched(
    overnight_runtime: Path,
) -> None:
    """What must not have been weakened to make the tests above pass.

    Driven through `panel doctor --session`, the one shipped face that takes a session as an
    argument -- `shortlist run` derives one and can no longer be made to ask this question, so
    asking it directly is the only way to see that the answer is still no.
    """
    result = CliRunner().invoke(
        app,
        [
            "panel",
            "doctor",
            "--runtime-dir",
            str(overnight_runtime),
            "--dataset",
            "daily",
            "--dataset",
            "daily_basic",
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--session",
            FRIDAY.isoformat(),
            "--as-of",
            OVERNIGHT.isoformat(),
            "--json",
        ],
    )
    report = json.loads(result.output)
    checks = {check["name"]: check for check in report["cross_checks"]}

    assert checks["close_agreement"]["ran"] is False
    reason = str(checks["close_agreement"]["skipped_reason"])
    assert "daily cannot be read for 2026-01-16" in reason
    assert "that session had not published yet" in reason
    assert "look-ahead" in reason


def test_the_sdk_and_the_http_face_price_the_overnight_build_the_same_way(
    overnight_runtime: Path,
) -> None:
    """`V2-P4-033`: one declaration, three faces, one session."""
    sdk = OpenAlphaSDK(runtime_dir=overnight_runtime)
    answered = sdk.run_shortlist(**BASELINE, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC))

    with TestClient(create_app(runtime_dir=overnight_runtime)) as client:
        response = client.post(
            "/api/v1/shortlists/run", json=_rest_body(datetime(2026, 1, 16, 12, 0, tzinfo=UTC))
        )

    assert answered.pricing_session == THURSDAY
    assert answered.cross_section_as_of == OVERNIGHT
    assert response.status_code == 200, response.text
    assert response.json()["cross_section"]["pricing_session"] == THURSDAY.isoformat()


# --- V2-P4-078: the five targets, and the command that writes each ---------------------------


OMISSIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "trade_cal": ("trade_cal",),
    "stock_basic": ("stock_basic",),
    "price": ("suspend_d", "daily", "daily_basic"),
    "stk_limit": ("stk_limit",),
    "namechange": ("namechange",),
}
"""Which datasets to leave out of a panel to remove one `panel build` target from it.

Keyed by target rather than by dataset because that is the vocabulary the remedy has to be
spelled in: `panel build --dataset daily` is refused by name -- `write_daily_panel` takes the
bars, the valuations and the halts together -- so a message naming `daily` would name a command
that does not run.
"""

EVERY_TARGET: Final[tuple[str, ...]] = tuple(sorted(set(SHORTLIST_PANEL_DATASETS.values())))


@pytest.fixture(scope="module")
def a_built_cross_section(tmp_path_factory: pytest.TempPathFactory) -> FactorPanel:
    """One cross section, computed once against a complete panel and written into each store.

    Computed elsewhere on purpose: a store with no calendar or no registry cannot have a factor
    built against it at all, so a per-store build would refuse before `shortlist run` could be
    reached and two of the five targets below would be untestable.
    """
    root = tmp_path_factory.mktemp("shortlist-prerequisite-source")
    _panel(root)
    return _cross_section(PanelStore(root / "panel"), instant=AFTER_THE_CLOSE)


@pytest.mark.parametrize("target", EVERY_TARGET)
def test_a_missing_panel_dataset_is_refused_by_the_command_that_writes_it(
    target: str, a_built_cross_section: FactorPanel, tmp_path: Path
) -> None:
    """`V2-P4-078`: every one of the five, and the `panel build` line that fixes it.

    `panel_view.NO_CALENDAR_REMEDY` is the bar the product acceptance set for the whole
    codebase, and before this only the panel gate met it. Parametrised over the whole table
    rather than over `namechange` alone: the issue was found on the rename corpus and is a
    property of every dataset this face reads.
    """
    omitted = OMISSIONS[target]
    kept = tuple(
        name
        for name in (
            "trade_cal",
            "stock_basic",
            "namechange",
            "adj_factor",
            "suspend_d",
            "daily",
            "daily_basic",
            "stk_limit",
        )
        if name not in omitted
    )
    root = _panel(tmp_path / "runtime", datasets=kept, halts="suspend_d" in kept)
    write_factor_panels(PanelStore(root / "panel"), (a_built_cross_section,))

    exit_code, output = _cli(
        root, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC), json_output=False
    )

    assert exit_code != 0, output
    assert f"openalpha panel build --dataset {target} --year <year>" in output


def test_a_partition_the_panel_holds_is_never_reported_as_one_it_never_built(
    a_built_cross_section: FactorPanel, tmp_path: Path
) -> None:
    """The bound on the remedy, and the reason it is a bound rather than a missing case.

    `openalpha shortlist run --exchange SSE` against a panel whose calendar holds SZSE refuses
    with `subject_missing`: the `trade_cal` 2026 partition is right there, and what is absent is
    a *subject* inside it. `openalpha panel build --dataset trade_cal --year 2026` is what that
    panel already ran, so appending it here would answer a caller with the command that produced
    the state they are stuck in.

    That is why `_unbuilt_dataset_remedy` keys on "this panel holds no partition of this dataset
    at all" rather than on the requested year: a refusal that names a command which does not help
    is worse than one that names none, and it is `stock_basic` that makes the year unusable as
    the key -- its partitions are lifecycle years, so "the requested year is absent" is a state
    a healthy registry reaches on its own.
    """
    root = _panel(tmp_path / "runtime")
    write_factor_panels(PanelStore(root / "panel"), (a_built_cross_section,))
    store = PanelStore(root / "panel")

    result = CliRunner().invoke(
        app,
        _arguments(
            root, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC), exchange="SSE", json_output=False
        ),
    )

    assert store.registered_years("trade_cal") == (YEAR,)
    assert result.exit_code != 0, result.output
    assert "subject_missing" in result.output
    assert "openalpha panel build" not in result.output
    assert "registered in this panel at all" not in result.output


def test_a_panel_carrying_exactly_those_targets_is_enough_to_cut_a_shortlist(
    a_built_cross_section: FactorPanel, tmp_path: Path
) -> None:
    """The other direction, which is what makes the table a claim rather than a list.

    A panel holding the five targets and nothing else answers. `adj_factor` is omitted here and
    that is the measurement: it is the sixth target `tests/e2e/e2e_support.py::BUILD_TARGETS`
    fetches, this face does not read it (`test_shortlist_whole_year_reads.py::
    test_the_shortlist_face_reads_no_adjustment_factor` holds the import graph to that), and a
    table that named it would send a user on a build measured in hours for nothing.
    """
    kept = tuple(
        dataset
        for dataset in (
            "trade_cal",
            "stock_basic",
            "namechange",
            "suspend_d",
            "daily",
            "daily_basic",
            "stk_limit",
        )
    )
    root = _panel(tmp_path / "runtime", datasets=kept)
    write_factor_panels(PanelStore(root / "panel"), (a_built_cross_section,))

    exit_code, output = _cli(root, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC))

    assert exit_code == 0, output
    assert len(json.loads(output)["funnel"]["shortlist"]) == SHORTLIST_SIZE


def test_a_raw_factor_build_needs_no_rename_corpus_and_the_shortlist_does(
    tmp_path: Path,
) -> None:
    """The asymmetry `V2-P4-078` is: a green build and a red shortlist on one store.

    Driven through the shipped `openalpha factor build`, because the claim is about that command
    and not about `compute_factor`. This is how a user reaches the refusal at all -- every
    earlier step succeeded.
    """
    kept = (
        "trade_cal",
        "stock_basic",
        "adj_factor",
        "suspend_d",
        "daily",
        "daily_basic",
        "stk_limit",
    )
    root = _panel(tmp_path / "runtime", datasets=kept)

    built = CliRunner().invoke(
        app,
        [
            "factor",
            "build",
            "--runtime-dir",
            str(root),
            "--factor",
            "reversal_1d/v1",
            "--tier",
            "raw",
            "--as-of",
            AFTER_THE_CLOSE.isoformat(),
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--max-staleness-days",
            "30",
            "--code-commit",
            COMMIT,
        ],
    )
    exit_code, output = _cli(
        root, as_of=datetime(2026, 1, 16, 12, 0, tzinfo=UTC), json_output=False
    )

    assert built.exit_code == 0, built.output
    assert exit_code != 0
    assert "openalpha panel build --dataset namechange --year <year>" in output


def test_the_missing_dataset_remedy_crosses_the_http_boundary_too(
    a_built_cross_section: FactorPanel, tmp_path: Path
) -> None:
    """The remedy is on `disclosable` as well as on the local message.

    A response body may not carry the store's filesystem path and must still carry the thing the
    caller has to act on; those are two separate strings in `ShortlistViewError` and a remedy
    appended to one of them is a remedy half the callers never see.
    """
    kept = (
        "trade_cal",
        "stock_basic",
        "adj_factor",
        "suspend_d",
        "daily",
        "daily_basic",
        "stk_limit",
    )
    root = _panel(tmp_path / "runtime", datasets=kept)
    write_factor_panels(PanelStore(root / "panel"), (a_built_cross_section,))

    with TestClient(create_app(runtime_dir=root)) as client:
        response = client.post(
            "/api/v1/shortlists/run", json=_rest_body(datetime(2026, 1, 16, 12, 0, tzinfo=UTC))
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "panel_unreadable"
    assert "openalpha panel build --dataset namechange --year <year>" in detail["message"]
    assert str(root) not in response.text


def test_the_command_names_every_target_it_needs_before_a_refusal_can_be_reached() -> None:
    """Discoverability, which is the half of `V2-P4-078` a better refusal does not answer.

    `--help` is where a user looks before running the command, and it named the factor tier as a
    prerequisite and none of the panel targets. Asserted on the rendered help with whitespace
    collapsed, because the docstring is wrapped to the terminal and a substring of the source
    would not survive that -- and it is the rendered text that has to carry it.
    """
    rendered = CliRunner().invoke(app, ["shortlist", "run", "--help"]).output
    collapsed = re.sub(r"\s+", " ", rendered)

    for target in EVERY_TARGET:
        assert f"--dataset {target}" in collapsed, target
    assert "--dataset adj_factor" not in collapsed


def test_every_dataset_this_face_reads_maps_to_a_target_panel_build_accepts() -> None:
    """The table is held to `cli.PANEL_BUILD_TARGETS` rather than restating it.

    A remedy naming a `--dataset` value the command refuses is worse than no remedy, and the two
    tables live in two modules that cannot import each other in that direction.
    """
    for dataset, target in SHORTLIST_PANEL_DATASETS.items():
        assert target in PANEL_BUILD_TARGETS, (dataset, target)
        assert dataset in PANEL_BUILD_TARGETS[target], (dataset, target)
    assert set(SHORTLIST_PANEL_DATASETS) == {
        "trade_cal",
        "stock_basic",
        "daily",
        "stk_limit",
        "suspend_d",
        "namechange",
    }
