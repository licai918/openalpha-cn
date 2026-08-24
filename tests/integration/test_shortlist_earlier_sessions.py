"""Yesterday's cross section, screened against yesterday's market (`V2-P4-061`).

## The defect, as product acceptance found it

`load_shortlist_cross_section`'s docstring, `docs/api/http.md` and `README.md` all say the same
sentence: *a fortnight-old cross section is offered to the market of its own session and never to
a later one its factor values never saw*. On one store holding two cross sections at two
instants, only the **newest** could be screened at all::

    $ openalpha shortlist run ... --as-of 2026-01-16T12:00:00+00:00   # the newest session
    verdict  admitted   funnel 8 listed -> 8 scored -> 3 tradeable -> 2 shortlisted   exit 0

    $ openalpha shortlist run ... --as-of 2026-01-15T12:00:00+00:00   # yesterday's, stored
    the price bars for 2026-01-15 could not be read out of this service's panel store:
    daily cannot be read at 2026-01-15T09:00:00+00:00: ['not_yet_knowable']; daily holds
    information that first became available at 2026-01-16T08:30:00+00:00
       exit 1

(That last sentence is quoted as it stood when this file was written. `V2-P4-094` rewrote it,
because *"first became available"* described `max_available_time` -- the **newest** instant
anywhere in the partition -- as though the dataset as a whole had only just been published, and
it never said that the judgement is per partition or which `as_of` would work. The refusal is the
same refusal; only what it tells the reader changed.)

(Those are the two invocations that reproduced it. The `as_of`s the tests below use are a few
hours later, so that each run's `as_of` lands on a *different* session from the cross section it
resolves; `EARLIER_AS_OF` says why that matters and what it separates.)

`load_daily_bars` and `load_price_limits` read through `read_if_ready`, which decides
`not_yet_knowable` on a **partition's** newest `available_time` -- and a partition is a calendar
year. So one session added to the panel makes every earlier cross section in that year
unscreenable: two days' shortlists cannot be compared, yesterday's cannot be re-run, and nothing
can be audited after the fact. The sentence was not merely imprecise; the offer was not made at
all.

**Both loaders had to move, and the second one is measured rather than assumed.** With
`load_daily_bars` alone on the session read, the same invocation exits `1` with `stk_limit cannot
be read at 2026-01-15T09:00:00+00:00: ['not_yet_knowable']` -- `_bars_on` pairs a bar with a
published band three lines apart, so half a fix relocates the refusal instead of removing it.

## What replaces it, and the property it rests on

Both loaders now take `_read_visible_price_session` -- the as-of-sensitive session read
`V2-P4-026` built for exactly this and wired to `load_daily_valuations` alone. Its safety argument
is a measured shape rather than a claim: `providers/tushare.py::_daily_close_timeline` stamps
every row of the `daily_close` clock at `DAILY_AVAILABILITY_TIME` on its own `trade_date`, so all
rows of one session share one availability instant and a session read is either wholly visible or
wholly withheld. Re-measured for this issue on the generated fixture panel, over `daily`,
`stk_limit` and `daily_basic`: 10 of 10 sessions in each carry exactly one distinct
`available_time`, and it is 16:30 Asia/Shanghai on that session's own `trade_date`.

## The corpus below is built so withheld and absent collide

`V2-P4-034`'s cautionary tale is a fixture on which two situations produce one number. The four
builds here are chosen so the two situations this door must separate meet on **one store, at one
`as_of`, through one loader**:

- `HALTED_BUILD` prices 2026-01-09, the session the generator halts `601318.SH` on. The store
  holds **seven** rows for it and the eighth security's row is *genuinely absent*. The read
  answers, and the missing name is counted as `unbarred` by stage two.
- `UNPUBLISHED_BUILD` stands at noon Asia/Shanghai on 2026-01-16. The store holds **eight** rows
  for that session and every one is *withheld* at that instant. The read refuses by name.

Seven-and-nothing-withheld against nothing-and-eight-withheld: two different pairs of numbers,
two different answers, one function. A door that could not tell them apart would either refuse
`HALTED_BUILD` (which is what the tree did before this issue, for every session but the last) or
answer 2026-01-16 with an empty cross section -- a look-ahead dressed as a thin market.

**`V2-P4-077` moved which face asks the second one, and nothing else.** `_pricing_session` used
to resolve a cross section's instant to that instant's own Shanghai calendar day, so
`UNPUBLISHED_BUILD` asked the price plane for 2026-01-16 -- a session its own factor values had
never seen, since `daily_requirement` clamps them at 2026-01-15's close. The plane refused, and
because the instant is stored on the cross section the refusal was permanent at every `as_of`
anyone could then ask at. The session is now the newest one that had *published* at the instant,
so this build prices 2026-01-15 alongside `EARLIER_BUILD`, and the withheld half of the pair
above is asked through `panel doctor --session` -- same loader, same store, same instant, session
named rather than derived. The door is untouched; the question this face puts to it is not.

`EARLIER_BUILD` and `HALTED_BUILD` carry the **same** sign, so their two answers differ in exactly
one thing: whether the market had a bar for every listed name. The generator's closes do not move
between sessions, so nothing else can account for a difference.

## Why the guard tests drive `panel doctor` instead

`shortlist run` cannot name a session -- it derives one from the resolved cross section's instant
-- so the two directions acceptance asks about (an `as_of` before anything published, and a
session past the panel's newest) are driven through `openalpha panel doctor --session/--as-of`,
which takes both as arguments. Same loader, same store, and the report carries the refusal
verbatim in `skipped_reason`, so "refused by name" is readable from the surface rather than
inferred.

All three shipped faces are driven -- `CliRunner`, `OpenAlphaSDK` and `TestClient` -- against one
module-scoped store, because `V2-P4-033`'s finding was three faces of one declaration that could
drift apart, and a read this issue changed is exactly the kind of thing they would drift over.
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
from openalpha_cn.shortlist_view import ShortlistPanelUnreadableError, ShortlistRunBlockedError

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

HALTED_BUILD: Final[datetime] = datetime(2026, 1, 9, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-01-09 -- the session `601318.SH` is halted for."""

EARLIER_BUILD: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
"""The cross section the defect made unscreenable: a session the panel holds, fully published,
sitting behind a newer one in the same year partition."""

UNPUBLISHED_BUILD: Final[datetime] = datetime(2026, 1, 16, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on 2026-01-16, four and a half hours before that session's own 16:30
publication. Every stored row for 2026-01-16 is withheld at it.

It was chosen because `_pricing_session` then called it 2026-01-16 and the shortlist face could
be made to ask for a withheld session that way. `V2-P4-077` is what that cost in the field, so
the instant now resolves to 2026-01-15 -- the session its factor values were computed from -- and
the withheld read is asked through `panel doctor --session` instead."""

NEWEST_BUILD: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
"""The panel's newest session, after its close. The one instant that worked before this issue."""

BUILD_SIGNS: Final[tuple[tuple[datetime, float], ...]] = (
    (HALTED_BUILD, 1.0),
    (EARLIER_BUILD, 1.0),
    (UNPUBLISHED_BUILD, -1.0),
    (NEWEST_BUILD, -1.0),
)
"""The four cross sections, and the sign each one's values carry.

`HALTED_BUILD` and `EARLIER_BUILD` share a sign deliberately; see this module's docstring.
"""

HALTED_SECURITY: Final[str] = "601318.SH"
HALTED_SESSION: Final[date] = date(2026, 1, 9)
EARLIER_SESSION: Final[date] = date(2026, 1, 15)
NEWEST_SESSION: Final[date] = date(2026, 1, 16)

HALTED_AS_OF: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on 2026-01-12 -- the session *after* the weekend, and the newest cross
section visible here is still 2026-01-09's."""

EARLIER_AS_OF: Final[datetime] = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
"""07:00 Asia/Shanghai on 2026-01-16: the morning after `EARLIER_BUILD`, and before
`UNPUBLISHED_BUILD` exists.

**Both of these ask on a day that is not the session they resolve**, deliberately. If a run's
`as_of` and its cross section's instant fell on one session, a face that priced on the day the
*question* was asked would be indistinguishable from one that priced on the day the *values* were
computed -- and the second is what the sentence this issue is about promises. Measured: with
`load_shortlist_cross_section` resolving the session from `request.as_of` instead of the stored
instant, both runs are refused, because 2026-01-12 and 2026-01-16 had not published at the
instants their cross sections carry.
"""

WITHHELD_AS_OF: Final[datetime] = datetime(2026, 1, 16, 5, 0, tzinfo=UTC)
"""13:00 Asia/Shanghai on 2026-01-16, resolving `UNPUBLISHED_BUILD` -- an intraday cross section,
priced on the session before its own day."""

NEWEST_AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""After 2026-01-16 published; resolves `NEWEST_BUILD`. The one instant that worked before."""

BEFORE_ANY_BUILD_AS_OF: Final[datetime] = datetime(2026, 1, 4, 9, 0, tzinfo=UTC)
"""Before the panel's first session published, and before any cross section was built."""

BEFORE_THE_UNSTORED_SESSION_PUBLISHED: Final[datetime] = datetime(2026, 1, 19, 7, 0, tzinfo=UTC)
"""15:00 Asia/Shanghai on 2026-01-19, ninety minutes before that session becomes knowable."""

AFTER_THE_UNSTORED_SESSION_PUBLISHED: Final[datetime] = datetime(2026, 1, 20, 9, 0, tzinfo=UTC)
"""The next evening. The same silence in the store, and a different fact about it: the census
now requires 2026-01-19, so the partition is short rather than early."""

CLOSED_DAY: Final[date] = date(2026, 1, 1)
"""New Year's Day, which the stored calendar reports the exchange shut for."""

UNSTORED_SESSION: Final[date] = date(2026, 1, 19)
"""A Monday the stored calendar reports open and the price panel does not reach.

The window the generator writes stops at 2026-01-16, so this is the first open session the store
holds no bar for -- which is what makes it able to ask the two questions acceptance separates:
before its 16:30 it is a look-ahead, and after it, it is a hole.
"""

SHORTLIST_SIZE: Final[int] = 2
POSITION_CAPITAL: Final[str] = "1250"
"""One 100-share lot of a name at 12.00 yuan and not one at 13.00; the generator's closes run
10.0 to 17.0 in `SECURITIES` order and do not move between sessions."""

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
"""One declaration for both faces, with every bar inert, so a refusal below is the panel read's
and never a gate's."""


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One store: a generated panel and one raw factor partition holding four cross sections.

    Module-scoped because nothing here mutates it and the real `generate_panel` /
    `compute_factor` pair is the expensive half.

    The evaluator is `compute_factor`'s own documented seam, because what is under test is which
    session's market a stored cross section is offered to -- not the factor arithmetic, which two
    builds computing the same numbers could not show.
    """
    root = tmp_path_factory.mktemp("shortlist-earlier-sessions")
    store = PanelStore(root / "panel")
    panel = generate_panel()
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
    """`BASELINE` as `openalpha shortlist run`'s argv."""
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
    """`BASELINE` as `POST /api/v1/shortlists/run`'s body."""
    body = {key: value for key, value in BASELINE.items() if key not in {"years", "components"}}
    body["years"] = list(BASELINE["years"])
    body["components"] = [dict(component) for component in BASELINE["components"]]
    body["as_of"] = as_of.isoformat()
    return body


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    """The HTTP face over the same store the CLI and the SDK tests drive."""
    with TestClient(create_app(runtime_dir=runtime_dir)) as client:
        yield client


def _doctor(runtime_dir: Path, *, session: date, as_of: datetime) -> dict[str, Any]:
    """`openalpha panel doctor` over the price pair alone, as data.

    Narrowed to `daily` and `daily_basic` so the only cross-check that runs is
    `close_agreement`, whose first read is `load_daily_bars` on the named session. A wider
    `--dataset` list drags `adj_factor`'s own whole-partition refusals into the report and the
    assertion could no longer say which door answered.
    """
    result = CliRunner().invoke(
        app,
        [
            "panel",
            "doctor",
            "--runtime-dir",
            str(runtime_dir),
            "--dataset",
            "daily",
            "--dataset",
            "daily_basic",
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--session",
            session.isoformat(),
            "--as-of",
            as_of.isoformat(),
            "--json",
        ],
    )
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def _close_agreement(report: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in report["cross_checks"] if check["name"] == "close_agreement"]
    assert len(checks) == 1, report["cross_checks"]
    return dict(checks[0])


def test_two_cross_sections_in_one_year_are_both_screenable_each_on_its_own_session(
    runtime_dir: Path,
) -> None:
    """`V2-P4-061`'s whole subject: yesterday's shortlist and today's, out of one store.

    Both cross sections sit in the same `daily` year partition, and before this issue the newer
    one's rows refused the older one's read -- `read_if_ready` judges `not_yet_knowable` on the
    partition's newest `available_time`, so the panel advancing one session made every earlier
    cross section unscreenable.

    Asserted as a **pair on one store**, because either half alone is passed by a tree that
    answers every question with the newest session's market: the two runs must come back on two
    different sessions, and the two shortlists must differ.
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


def test_a_withheld_session_is_refused_while_a_session_missing_one_bar_is_answered(
    runtime_dir: Path,
) -> None:
    """The two situations this door exists to keep apart, on one store and one loader.

    2026-01-09 holds seven rows and the eighth security has none -- an **absent** row, and the
    read answers with it counted as `unbarred`. 2026-01-16 read from inside its own morning holds
    eight rows and every one is **withheld** -- and the read refuses rather than handing back the
    empty cross section that would say the market was shut.

    The pair is the assertion. A door that answered both would have shipped a look-ahead as a thin
    market; one that refused both is the tree this issue was filed against.

    ## The withheld half is asked through `panel doctor` now (`V2-P4-077`)

    It used to be asked through `shortlist run` at `WITHHELD_AS_OF`, and that route is gone
    because the question it put was itself wrong. `UNPUBLISHED_BUILD` stands at noon Asia/Shanghai
    on 2026-01-16 and its factor values were computed from 2026-01-15's close -- that is what
    `daily_requirement` clamps them to -- so resolving it onto 2026-01-16 offered a cross section
    to a session its own values had never seen, which is the sentence this whole module exists to
    make true, inverted. The price plane refused it, correctly, and because the instant is stored
    **on the cross section** the refusal was permanent: `V2-P4-077` swept every `as_of` from
    before that build to four days after it and every one exited `1`. `_pricing_session` now
    resolves the newest session that had *published* at the instant, so this build prices
    2026-01-15 and the two clocks agree.

    **Nothing about the door changed, and this is where that is measured.** The same loader, the
    same store and the same instant, asked with the session named rather than derived, still
    refuses by name -- so the withheld half is exactly as live as it was, and what moved is which
    question this face puts to it. `test_a_session_whose_bars_are_not_yet_published_is_refused_
    rather_than_answered_empty` is the same refusal one session further out.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)

    answered = sdk.run_shortlist(**BASELINE, as_of=HALTED_AS_OF)

    assert answered.pricing_session == HALTED_SESSION
    assert HALTED_SECURITY in answered.universe
    assert dict(answered.funnel.tradeability.refused_by_verdict)["unbarred"] == 1

    withheld = _close_agreement(
        _doctor(runtime_dir, session=NEWEST_SESSION, as_of=UNPUBLISHED_BUILD)
    )

    assert withheld["ran"] is False
    reason = str(withheld["skipped_reason"])
    assert "daily cannot be read for 2026-01-16" in reason
    assert "that session had not published yet" in reason
    assert "look-ahead" in reason


def test_the_session_with_every_bar_answers_one_more_name_than_the_halted_one(
    runtime_dir: Path,
) -> None:
    """The absent row is a number, not an absence of one.

    `HALTED_BUILD` and `EARLIER_BUILD` carry the same sign and the generator's closes do not move
    between sessions, so the two cross sections order the market identically. The one thing that
    differs is that 2026-01-09 has no bar for `601318.SH` -- and it shows up as exactly one
    `unbarred` name against zero, on the same universe of eight.

    Without this the test above could pass on a reader that dropped the halted name a stage
    earlier and never counted it.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)
    halted = sdk.run_shortlist(**BASELINE, as_of=HALTED_AS_OF)
    whole = sdk.run_shortlist(**BASELINE, as_of=EARLIER_AS_OF)

    assert len(halted.universe) == len(whole.universe)
    assert halted.funnel.scores.scored_count == whole.funnel.scores.scored_count
    assert dict(halted.funnel.tradeability.refused_by_verdict)["unbarred"] == 1
    assert dict(whole.funnel.tradeability.refused_by_verdict)["unbarred"] == 0


def test_the_http_face_answers_the_earlier_cross_section_and_refuses_what_is_not_there(
    rest: TestClient, runtime_dir: Path
) -> None:
    """The same two answers over HTTP, because three faces that disagree is `V2-P4-033`'s finding.

    `200` on a cross section the panel can price on its own session, and `409` -- not `200` with
    an empty `admitted` -- on an `as_of` at which no cross section exists at all. The status code
    is the half a JSON body cannot make: a caller reading `admitted: []` cannot ask a dict whether
    it was refused.

    The refusing half used to be `WITHHELD_AS_OF`, and `V2-P4-077` is why it is not: that
    question is no longer askable through this face, because asking it was the defect. The
    refusal it drove is asserted where it is still reachable --
    `test_a_withheld_session_is_refused_while_a_session_missing_one_bar_is_answered` -- and what
    this test needs is any refusal at all, to hold the status code and the envelope apart from a
    thin `200`.
    """
    answered = rest.post("/api/v1/shortlists/run", json=_rest_body(EARLIER_AS_OF))
    refused = rest.post("/api/v1/shortlists/run", json=_rest_body(BEFORE_ANY_BUILD_AS_OF))
    intraday = rest.post("/api/v1/shortlists/run", json=_rest_body(WITHHELD_AS_OF))

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["cross_section"]["pricing_session"] == EARLIER_SESSION.isoformat()
    assert body["cross_section"]["as_of"] == EARLIER_BUILD.isoformat()
    assert len(body["funnel"]["shortlist"]) == SHORTLIST_SIZE

    assert refused.status_code == 409, refused.text
    fault = refused.json()["detail"]
    assert fault["reason"] == "blocked"
    assert "no raw-tier cross section" in fault["message"]
    assert "admitted" not in refused.json()
    assert str(runtime_dir) not in fault["message"]

    assert intraday.status_code == 200, intraday.text
    assert intraday.json()["cross_section"]["as_of"] == UNPUBLISHED_BUILD.isoformat()
    assert intraday.json()["cross_section"]["pricing_session"] == EARLIER_SESSION.isoformat()


def test_an_as_of_before_anything_was_knowable_is_still_refused_by_name(
    runtime_dir: Path,
) -> None:
    """Reaching back past the panel's first session is refused, and says so.

    The other direction of the same guard: making yesterday readable must not make *before the
    beginning* readable. At an `as_of` before the panel's first session published there is no
    stored cross section to resolve, and the refusal names the tier, the factor and the instant
    rather than returning an empty list.
    """
    exit_code, output = _run_cli(runtime_dir, as_of=BEFORE_ANY_BUILD_AS_OF)

    assert exit_code != 0
    assert "no raw-tier cross section of ['reversal_1d/v1'] is stored" in output
    assert "visible at 2026-01-04T09:00:00+00:00" in output


def test_a_session_whose_bars_are_not_yet_published_is_refused_rather_than_answered_empty(
    runtime_dir: Path,
) -> None:
    """Tomorrow's bars stay unreadable, and the refusal is about publication.

    The failure this whole data plane exists to prevent, asked of the loader directly through
    `panel doctor --session`. 2026-01-19 is an open session the price panel does not reach; asked
    before its own 16:30, the answer must be a refusal naming the publication instant.

    **This is a hole the whole-partition door left open rather than one it closed.** At this
    `as_of` the year partition's newest row (2026-01-16T08:30Z) predates the read, so
    `read_if_ready` found nothing to object to and the `trade_date` filter returned `()` -- an
    empty cross section for a session that had not happened. The session read refuses it instead.
    """
    report = _doctor(
        runtime_dir, session=UNSTORED_SESSION, as_of=BEFORE_THE_UNSTORED_SESSION_PUBLISHED
    )
    check = _close_agreement(report)

    assert check["ran"] is False
    reason = str(check["skipped_reason"])
    assert "daily cannot be read for 2026-01-19" in reason
    assert "that session had not published yet" in reason
    assert "look-ahead" in reason


def test_a_session_the_panel_never_stored_is_refused_differently_from_one_it_withheld(
    runtime_dir: Path,
) -> None:
    """Absent and withheld are two refusals with two names, at session scope.

    The same session, one day apart in the read's `as_of`. Before 2026-01-19's 16:30 the store's
    silence is *not yet knowable* and the refusal is about publication; after it, the same silence
    is a **hole** the calendar can prove -- `required_dates` now reaches that session -- and the
    refusal is `date_gap`, naming the first date that is missing.

    Asserted against each other rather than one at a time: a door that collapsed the two would
    still pass either assertion alone, and collapsing them is how a look-ahead ships as thin data.
    """
    unpublished = _close_agreement(
        _doctor(runtime_dir, session=UNSTORED_SESSION, as_of=BEFORE_THE_UNSTORED_SESSION_PUBLISHED)
    )
    absent = _close_agreement(
        _doctor(runtime_dir, session=UNSTORED_SESSION, as_of=AFTER_THE_UNSTORED_SESSION_PUBLISHED)
    )

    assert unpublished["ran"] is False
    assert absent["ran"] is False
    unpublished_reason = str(unpublished["skipped_reason"])
    absent_reason = str(absent["skipped_reason"])
    assert "daily cannot be read for 2026-01-19" in unpublished_reason
    assert "had not published yet" in unpublished_reason
    assert "date_gap" not in unpublished_reason
    assert "daily cannot be read at 2026-01-20T09:00:00+00:00" in absent_reason
    assert "date_gap" in absent_reason
    assert "2 required date(s) are absent from daily, starting at 2026-01-19" in absent_reason
    assert "had not published yet" not in absent_reason


def test_a_day_the_exchange_was_shut_is_still_refused_before_any_partition_is_read(
    runtime_dir: Path,
) -> None:
    """A closed day is refused before any partition is touched, and the door change kept it.

    2026-01-01 is in the stored calendar and is not an open session, and the answer must stay a
    named refusal rather than an empty cross section -- the check that survived the move from one
    door to the other unchanged.
    """
    report = _doctor(runtime_dir, session=CLOSED_DAY, as_of=AFTER_THE_UNSTORED_SESSION_PUBLISHED)
    check = _close_agreement(report)

    assert check["ran"] is False
    assert "is not an open session" in str(check["skipped_reason"])


def test_the_earlier_run_is_blocked_today_for_the_reason_this_issue_was_filed(
    runtime_dir: Path,
) -> None:
    """The reproduction, kept as a test so the defect cannot come back unnoticed.

    Deleted-and-restored guard rather than documentation: after the fix this asserts that the
    earlier cross section is *not* refused with a whole-partition `not_yet_knowable`, which is the
    one string the defect's output carried.
    """
    exit_code, output = _run_cli(runtime_dir, as_of=EARLIER_AS_OF)

    assert exit_code == 0, output
    assert "not_yet_knowable" not in output


def test_a_blocked_read_never_comes_back_as_an_empty_shortlist(runtime_dir: Path) -> None:
    """The refusal reaches the SDK as an exception, never as `shortlist: []`.

    `ShortlistPanelUnreadableError` rather than a result whose funnel happens to be empty, because
    a caller reading zero names cannot ask a dataclass whether it was refused -- the distinction
    `V2-P4-023` built and the one a look-ahead would hide behind.

    Driven at an `as_of` before any cross section exists rather than at `WITHHELD_AS_OF`, for
    `test_the_http_face_answers_the_earlier_cross_section_and_refuses_what_is_not_there`'s reason:
    `V2-P4-077` removed the second question from this face, and what this test needs is a refusal
    rather than that particular one.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)

    with pytest.raises((ShortlistPanelUnreadableError, ShortlistRunBlockedError)):
        sdk.run_shortlist(**BASELINE, as_of=BEFORE_ANY_BUILD_AS_OF)
