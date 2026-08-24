"""The workflow this product exists for, on rows Tushare served: cut a list, cut a second one,
keep both, and be able to say what changed.

## Why this file exists (`V2-P4-072`)

At `d109109` the e2e suite held 33 tests and `grep -rn "shortlist" tests/e2e/` returned nothing.
Between `d703905` and here the repository grew `shortlist_view.py`, the whole `cross_section ->
candidate_ranking -> shortlist_gate` chain, `openalpha shortlist run|get|list`, three HTTP routes,
three SDK methods, the panel-to-cross-section adapter, `V2-P4-061`'s visible-instant pricing read
and `V2-P4-062`/`071`'s store and append -- while the only layer touching real data still ran the
panel and the point-in-time injection and stopped there. Every one of those rows was accepted on
`CliRunner`, `TestClient` and a generated panel, and the three product acceptances behind them
were each filed because a surface passed its tests and could not be reached by a user.

`tests/integration/test_shortlist_workflow.py` walks the same five steps over
`tests/panel_fixtures.py`'s generator. This walks them over a market: five thousand real
securities with real listings, real delistings and a real calendar, priced out of the partitions
`panel build` fetched.

## What the market did to the walk: one screenable session, and only ever one

The generated version screens two *consecutive sessions*. **On a real panel it cannot**, and
finding out why is the first thing this file does rather than the last.

`load_shortlist_cross_section` reads the registry, the adjustment histories, the halt corpus and
the rename corpus at the cross section's own instant, and all four go through `read_if_ready` --
which refuses a whole partition whose newest `available_time` is later than the `as_of`
(`panel/catalog.py`'s "Disclosure" section, which states the rule and names P3 and P4 as the
phases it constrains). Measured on the panel this suite fetched on 2026-08-19:

    trade_cal     knowable from 2026-01-01T00:00+08:00
    stock_basic   knowable from 2026-08-19T00:00+08:00   (two securities listed that day)
    adj_factor    knowable from 2026-08-19T16:30+08:00
    suspend_d     knowable from 2026-08-19T16:30+08:00
    stk_limit     knowable from 2026-08-19T16:30+08:00
    daily         knowable from 2026-08-19T16:30+08:00   (152 sessions, newest 2026-08-19)

`adj_factor` and `suspend_d` are whole-year partitions whose newest row is the newest session's,
so their availability instant *is* that session's close -- and therefore, **as measured on
2026-08-19**, the only `as_of` a factor could be built at or a shortlist cut at was at or after
the newest stored session's close. The registry was merely the first of them to say so, because
it is read first:

    $ openalpha factor build --factor reversal_1d/v1 --as-of 2026-08-18T17:30:00+08:00
    the security registry cannot be read at 2026-08-18T17:30+08:00: ['not_yet_knowable'];
    stock_basic holds information that first became available at 2026-08-18T16:00:00+00:00

That is `V2-P4-061`'s wall, standing on the datasets that fix did not move. `061` put `daily`,
`daily_basic` and `stk_limit` on the as-of-sensitive session read precisely so that "two days'
shortlists could not be compared, yesterday's could not be re-run, and a published list could not
be audited after the fact" would stop being true, and on the panel above all three were still
true, because the four planes read beside the prices were not moved with them.
`test_a_cross_section_earlier_than_the_registrys_own_availability_is_refused` is where that
finding lives, with its own contrast one instant later.

**`V2-P4-076` then moved three of those four, and the paragraph above is kept as a dated
measurement rather than as a standing claim.** `shortlist_view.load_shortlist_cross_section`'s own
table names them: `stock_basic`, `suspend_d` and `namechange` now take
`panel_ingest._read_visible_event_dated_rows`, the per-event-date census reconciliation, each with
its own availability rule -- so the transcript above is the *pre-`076`* refusal and the sentence
"every earlier session is refused, on every panel" is no longer the rule. What `076` left standing
for this chain is `adj_factor`, and it was left deliberately: `V2-P4-086` measured that
`compress_adjustment_batch` stores a step function, that a row predicate at an earlier instant
drops six of eight securities to a single row and pulls `covered_through` back far enough to turn
every question this chain asks into an `AdjustmentHorizonError`, and that `PartitionCoverage.dates`
carries no subject axis to decide it per security. That issue names the two exact edits the move
needs and neither is this file's.

**Not re-measured against a live panel since `076`.** The transcripts in this section, and
`test_a_cross_section_earlier_than_the_registrys_own_availability_is_refused`'s own reading that
the registry is what refuses, date from before it; that test skips when no stored session is
earlier than `registry_knowable_from`, so a panel on which `076` has moved the wall makes it skip
rather than fail. Re-running this suite against a fresh panel is what would settle which dataset
now answers first, and `V2-P4-100` fixed the sentence rather than claiming the measurement.

## The second finding, which had no instant left at all

The first build of this module against a real panel could not screen **any** instant, and the two
refusals it got back leave no gap between them:

    $ openalpha shortlist run --as-of 2026-08-19T18:30:00+08:00
    the rename corpus cannot be read at 2026-08-19T09:30:00+00:00: ['not_yet_knowable'];
    namechange holds information that first became available at 2026-08-19T16:00:00+00:00

    $ openalpha shortlist run --as-of 2026-08-20T01:30:00+08:00
    daily cannot be read for 2026-08-20 at 2026-08-19T16:30:00+00:00: that session had not
    published yet ... Reading it would be a look-ahead

The cause is that `panel build` fetches an **announcement-dated** dataset whole, at its own
`--as-of`, while its session-scoped datasets stop at the last session that published -- and this
suite's `built_panel` fixture passes `datetime.now(UTC)`, which on an overnight run is already
*tomorrow* in Asia/Shanghai. So the corpus legitimately carried a rename effective 2026-08-20
while `daily` legitimately stopped at 2026-08-19, and the shortlist chain, which reads both at one
instant, had nowhere to stand. `_refuse_split_horizon` exists to refuse exactly this shape of
incoherence between a build's targets and does not see it, because `namechange` is not one of the
session-scoped ones.

The remedy is the flag that already exists and is what `rename_corpus` does: build the corpus at
the instant the panel is *about* rather than at the wall clock, and the row nobody could have
known about is left out by `ColumnarPanelBatch`'s own visible-at-`as_of` check --

    $ openalpha panel build --dataset namechange --as-of 2026-08-19T17:30:00+08:00
    389 rows -> 388, and the same `shortlist run` answers with a list of ten

**So this module compares two answers about one session rather than two sessions.** Both are
stamped on the screenable session's own evening, and the second widens the universe -- which is
the only way a real panel can put two *different* cross sections into one partition, and which
makes the append a stricter test than a repeat would: sixty of the second build's subjects
collide with the first's, and `carry_stored_rows_forward`'s `identity_columns` are what keep the
first instant's rows anyway. What is lost is the "the market moved overnight" reading of the
diff, and it is lost because the product cannot deliver it, not because the test declined to ask.

## What is asserted, on a panel whose contents nobody chose

`e2e_support.py`'s "Determinism" rule, unchanged: no price, no session, no ticker and no row count
is written into an assertion here. What replaces them is the same three kinds of claim --

- **An internal agreement.** The first answer, re-run after the second instant was appended,
  carries the same `shortlist_id`; the command line, the HTTP route and the SDK hand out one
  document for one address.
- **An agreement with something this chain never computed.** The screened universe is picked by
  `amount` off the `daily` partition -- a column no factor, funnel or gate reads and no reader in
  this repository reassembles -- so a name's place on the shortlist cannot be an artefact of the
  thing under test.
- **A refusal produced on purpose.** The registry wall above; a security the *real* registry
  delisted inside the panel's own year, screened at a session after it was gone; an address
  nothing is held under, which is a `404` and not an empty document.

## The universe, and why it is 60 names and then 120 rather than the market

`panel build` fetches whole-market and has no `--subject`; that fetch belongs to the
session-scoped `built_panel` fixture and this module adds nothing to it (see "Cost"). What this
module *chooses* is how wide a cross section to derive a factor over, and that is the
most-traded `UNIVERSE_SIZE` names on the screenable session, and then the most-traded
`WIDER_UNIVERSE_SIZE`.

Turnover rather than an alphabetical slice, and sixty rather than five thousand, for four reasons
that are about what the test can then assert. The most-traded names are the ones that actually
carry a bar on the session, so `reversal_1d/v1` -- a one-day return -- is computable for all of
them rather than for whichever subset happened to trade. `amount` is a column nothing downstream
of the universe choice can read, so a name's place on a shortlist cannot be an artefact of how it
got into the universe. `--subject` keeps each `factor build` to seconds instead of evaluating
every code the registry knows. And sixty leaves a cut of `SHORTLIST_SIZE` room to move without
tripping `cut_exceeds_the_cross_section`, which a universe of twelve would do.

The one thing the narrowing costs is the tradable ratio: `tradable_ratio` divides the tradeable
count by the *registry's* listed set on the session, so a screen of this width over a
5,000-name market measures around 0.01 whatever the market does. `--min-tradable-ratio 0` is
declared for that reason and not to make a bar go away -- `researched_ratio` is the bar this file
drives, and it is driven in both directions.

## Cost

**One request, once per panel, and nothing after it.** `openalpha factor build` derives from the
stored panel and `openalpha shortlist run` reads it; neither constructs a provider, so every test
below runs against whatever `built_panel` already put on disk -- a panel
`OPENALPHA_E2E_RUNTIME_DIR` points at, or the one the fixture built for the other 33 tests in the
same session. The single exception is `rename_corpus`, which fetches the `namechange` year that
`e2e_support.BUILD_TARGETS` does not and `FactorPanelReader` cannot start without; it is one
whole-market request, it is skipped when the panel already holds the partition, and it is written
into the shared directory so a reused panel pays it once ever.

`private_panel` gives each test that writes a derived partition its own view of that panel rather
than writing into the shared one, and it is what keeps the expensive artifact reusable: a `factor
build` into `OPENALPHA_E2E_RUNTIME_DIR` would be a stored build that the *next* run of this module
meets as a restatement at an `as_of` the year already holds -- refused by name, correctly -- so
the directory the whole cost discipline rests on would work exactly once.

The Parquet files are hard-linked and only the catalog is copied. That is not premature economy:
the `daily` partition alone is around 800,000 rows, `conftest.py::withheld_partition` already
refuses to duplicate it once per test for that reason, and this module needs several views of it.
Linking is safe because nothing here rewrites a *fetched* partition -- `factor build` writes new
datasets beside them -- and `panel_ingest.write_panel_batch` replaces a file by rename in any
case, which breaks a link rather than writing through it.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import duckdb
import pytest
from e2e_support import (
    PANEL_ZONE,
    BuiltPanel,
    CLIResult,
    E2EEnvironmentError,
    catalogued_path,
    http_get,
    knowable_from,
    run_build,
    run_cli,
    stored_sessions,
)

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_AVAILABILITY_TIME, DAILY_DATASET
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_stock_universe
from openalpha_cn.panel_view import panel_store
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.shortlists import SHORTLIST_ID_PATTERN

pytestmark = pytest.mark.e2e


FACTOR: Final[str] = "reversal_1d/v1"
"""The one factor this module screens on.

`REVERSAL_1D.required_fields` names one column -- `daily.close` -- and its lookback is two
sessions, so every input it needs is in a partition `panel build` fetched. The other twenty
registered factors reach `income`, `balancesheet`, `cashflow` or `fina_indicator`, and none of
those is among this suite's build targets: a screen on one of them would be measuring which
datasets the fixture happens to fetch rather than whether the chain holds.

What the *reader* underneath it opens is wider than what the formula reads -- the registry, the
adjustments, the renames and the halts all come along -- and that gap is this module's headline
finding rather than an aside. See `FETCHED_BLOCKING_DATASETS`.
"""

TIER: Final[str] = "raw"
"""`processed` narrows by a transform whose `min_cross_section` this module's market does not
reach -- see `test_a_processed_screen_of_this_market_is_refused_by_the_transforms_own_floor`,
which drives that rather than leaving it as a claim -- and `neutralized` is refused by
`run_shortlist` by name, because the exposure cross section it would need reads `index_member_all`
and `panel build` fetches no membership corpus at all."""

HORIZON: Final[str] = "5d"

UNIVERSE_SIZE: Final[int] = 60
"""How many real securities the first cross section is derived over. See this module's
docstring."""

WIDER_UNIVERSE_SIZE: Final[int] = 120
"""How many the second one is derived over: the same names and the next `UNIVERSE_SIZE` by
turnover.

A superset rather than a disjoint set, so the second build's subjects *collide* with the first's
on sixty of them -- which is what makes the append test strict. And wider rather than narrower
because the funnel would otherwise be choosing from a subset of names it already ranked, where a
changed top ten would be arithmetic rather than a different question.

Doubling is what produces a different list: `reversal_1d/v1` orders on a one-day return and not on
turnover, so the sixty added names are drawn from the same return distribution as the first sixty
and the chance that none of them reaches a top ten of a hundred and twenty is under one in a
thousand. That is the whole reason the second screen's list differs from the first's on a panel
where a second *session* is unreachable."""

SHORTLIST_SIZE: Final[int] = 10
"""The cut. Strictly below the tradeable count, or the funnel answers
`cut_exceeds_the_cross_section` and shortlists nobody."""

POSITION_CAPITAL: Final[str] = "100000"
"""The notional one buy is sized against. Large enough that a 100-share lot of an ordinary A-share
clears `below_board_minimum`, which is a stage-two verdict this module is not about."""

BUILD_HOUR_AFTER_CLOSE: Final[timedelta] = timedelta(hours=1)
"""How long after a session's data becomes knowable a cross section about it is stamped.

`DAILY_AVAILABILITY_TIME` is 16:30 Asia/Shanghai, so a build lands at 17:30 that evening -- after
the session published and on the day it is about, which is what makes
`shortlist_view._pricing_session` resolve to that session rather than to the one before it."""

SECOND_INSTANT_TIME: Final[time] = time(23, 30)
"""When the second cross section is stamped.

Late on the screenable session's own evening: still that session's Shanghai day, so both cross
sections price it, and later than any instant the first one could have been pushed to.
`V2-P4-071` is about a second `as_of` reaching a partition that is written whole, and this is the
second `as_of` -- the only one a real panel offers, for the reason this module's docstring
measures."""

FETCHED_BLOCKING_DATASETS: Final[tuple[str, ...]] = (
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
)
"""The partitions `e2e_support.BUILD_TARGETS` fetches that the shortlist chain reads through
`read_if_ready`, and therefore the ones whose own availability instants bound every prediction
instant below.

`daily`, `daily_basic` and `stk_limit` are **not** here, and their absence is the measurement
rather than an omission: `V2-P4-026` and `V2-P4-061` moved all three onto
`panel_ingest._read_visible_price_session`, so a session read of the price plane is answerable at
an earlier `as_of` and these are what is left. That is the whole shape of this module's headline
finding -- the price plane was freed and the planes beside it were not.

`namechange` is the fourth and is missing here because `rename_corpus` builds it *at* the instant
these three yield, so it cannot bound it. See that fixture, and the second finding in this
module's docstring for what happens when it does."""

ASK_AFTER_BUILD: Final[timedelta] = timedelta(hours=1)
"""How long after a build this module asks about it: late enough to see that cross section and
early enough not to see the next one, which is the whole of the point-in-time claim
`test_a_second_instant_is_added_without_destroying_the_first` makes."""

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
"""Declared on every invocation rather than resolved, because both are inside `manifest_id` and
inside `shortlist_id`. `_resolved_code_commit(None)` reads git and `_resolved_config_digest(None)`
reads the process's configuration; either would make "the same question re-asked produces the same
address" a claim about what the child process happened to inherit."""

UNSTORED_RUN: Final[str] = "run_000000000000000000000000"
"""A well-formed `run_manifest_id` that resolves to nothing. `V2-P4-049`'s own probe."""

UNHELD_SHORTLIST: Final[str] = "sla_000000000000000000000000"
"""A well-formed `shortlist_id` no run produced. The wrong-answer control for retrieval."""

EXIT_OK: Final[int] = 0
EXIT_UNHEALTHY: Final[int] = 1
EXIT_BAD_REQUEST: Final[int] = 3
"""The three rows of `cli.PanelExit` this module observes, spelled out rather than imported so
that a test asserting `1` is asserting the contract a scheduled job switches on and not whatever
the enum currently evaluates to."""


# --- the panel this module screens, and the instants it may screen at --------------------------


def _stamped(session: date) -> datetime:
    """The instant a cross section about `session` is stamped at. See `BUILD_HOUR_AFTER_CLOSE`."""
    return (
        datetime.combine(session, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_ZONE)
        + BUILD_HOUR_AFTER_CLOSE
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Screened:
    """One view of the built panel, the two instants it may be screened at, and the universe.

    A record rather than five fixtures because the five are only meaningful together: the universe
    is the names that traded on *these* sessions, and both instants are derived from what the
    panel's own registry made knowable.
    """

    runtime_dir: Path
    workspace: Path
    year: int
    exchange: str
    sessions: tuple[date, ...]
    """Every session of the build year the `daily` partition holds, ascending."""

    registry_knowable_from: datetime
    """The earliest instant `stock_basic` alone may be read at.

    Kept beside `knowable_from_every_blocking_partition` rather than folded into it because
    `test_a_cross_section_earlier_than_the_registrys_own_availability_is_refused` names the
    registry in its assertion, and the registry is only the *first* of the four to refuse --
    `FactorPanelReader` reads it before the adjustments, the names and the halts. A test that
    took the maximum would be asserting a message whichever partition happened to be latest."""

    knowable_from_every_blocking_partition: datetime
    """The earliest instant every partition this chain reads through `read_if_ready` may be read
    at. Both instants below are at or after it, which is what makes them screenable at all."""

    session: date
    """The one session this panel may be screened about: its newest. See the docstring."""

    instants: tuple[datetime, datetime]
    """The two prediction instants, both on `session`'s own evening, earlier first."""

    universes: tuple[tuple[str, ...], tuple[str, ...]]
    """What each of those instants derives the factor over: the most-traded `UNIVERSE_SIZE`
    names, and the most-traded `WIDER_UNIVERSE_SIZE`, the second containing the first."""

    @property
    def store(self) -> PanelStore:
        return panel_store(self.runtime_dir)

    def asked_at(self, instant: datetime) -> datetime:
        """When this module asks about a build stamped at `instant`. See `ASK_AFTER_BUILD`."""
        return instant + ASK_AFTER_BUILD


def _instant_for(session: date, *, knowable: datetime) -> datetime | None:
    """The instant a cross section about `session` may be stamped at, or `None` if there is none.

    17:30 on the session's own evening, pushed forward to `knowable` when a partition this chain
    reads became available later that same day -- and `None` when `knowable` has run past the
    session's day altogether, because an instant on the next day prices the next session and this
    one is then not screenable at all.
    """
    candidate = max(_stamped(session), knowable)
    return candidate if candidate.astimezone(PANEL_ZONE).date() == session else None


def _prediction_instants(
    sessions: Sequence[date], *, knowable: datetime
) -> tuple[date, tuple[datetime, datetime]]:
    """The one session this panel may be screened about, and the two instants on its evening.

    The newest session with an instant at all, scanned from the end -- which on a panel whose
    `adj_factor` and `suspend_d` partitions are whole years is always the newest session and never
    a second one. Written as a scan rather than as `sessions[-1]` so that the *rule* is stated and
    the "always" is a consequence of it: the refusal that makes it unavoidable is
    `test_a_cross_section_earlier_than_the_registrys_own_availability_is_refused`.
    """
    for session in reversed(sessions):
        first = _instant_for(session, knowable=knowable)
        if first is None:
            continue
        second = datetime.combine(session, SECOND_INSTANT_TIME, tzinfo=PANEL_ZONE)
        if second <= first:
            raise E2EEnvironmentError(
                f"the screenable session {session.isoformat()} can first be stamped at "
                f"{first.isoformat()}, at or after {SECOND_INSTANT_TIME.isoformat()} on its own "
                "evening, so this panel offers no second instant inside it"
            )
        return session, (first, second)
    raise E2EEnvironmentError(
        f"every partition this chain reads through read_if_ready first became knowable at "
        f"{knowable.isoformat()}, and the newest session this panel stores is "
        f"{sessions[-1].isoformat()}, whose evening ends before it. No instant this panel can "
        "price is one its own registry may be read at, so no shortlist can be cut from it at "
        "all; that is the wall this module's docstring describes, reached in its worst form"
    )


def _link_panel(source: Path, destination: Path) -> None:
    """Copy one built panel's directory tree, hard-linking every file but the catalog.

    The catalog is a DuckDB database that `write_panel_batch` opens for writing, so it is the one
    file that must be an independent copy; the Parquet partitions are only ever read here, and are
    replaced by rename when they are written at all, so a link cannot be written through.

    See this module's docstring for why several independent views of one panel are wanted and why
    duplicating an 800,000-row partition to get them is not.
    """

    def _each(origin: str, target: str) -> None:
        if origin.endswith(".duckdb"):
            shutil.copy2(origin, target)
        else:
            os.link(origin, target)

    shutil.copytree(source, destination, copy_function=_each)


def _blocking_instants(store: PanelStore, *, year: int) -> tuple[datetime, datetime]:
    """`(the registry's own availability instant, every fetched blocking partition's)`.

    Read off the catalog rather than from a clock, so a panel reused through
    `OPENALPHA_E2E_RUNTIME_DIR` answers the same next week as it did on the day it was fetched.
    """
    registry_years = sorted(store.registered_years(STOCK_BASIC_DATASET))
    return (
        knowable_from(store, {STOCK_BASIC_DATASET: registry_years}),
        knowable_from(
            store,
            {
                dataset: registry_years if dataset == STOCK_BASIC_DATASET else [year]
                for dataset in FETCHED_BLOCKING_DATASETS
            },
        ),
    )


def _screened(built_panel: BuiltPanel, root: Path, workspace: Path) -> Screened:
    """One `Screened` over a fresh view of `built_panel`, with everything read off it."""
    _link_panel(built_panel.runtime_dir / "panel", root / "panel")
    workspace.mkdir(parents=True, exist_ok=True)
    store = panel_store(root)
    sessions = stored_sessions(store, dataset=DAILY_DATASET, year=built_panel.year)
    if len(sessions) < 2:
        raise E2EEnvironmentError(
            f"the {DAILY_DATASET} partition for {built_panel.year} holds {len(sessions)} "
            "session(s); a one-day return needs two"
        )
    registry_knowable, fetched_knowable = _blocking_instants(store, year=built_panel.year)
    knowable = max(
        fetched_knowable,
        knowable_from(store, {NAMECHANGE_DATASET: [built_panel.year]}),
    )
    session, instants = _prediction_instants(sessions, knowable=knowable)
    wider = _most_traded(store, year=built_panel.year, session=session, count=WIDER_UNIVERSE_SIZE)
    return Screened(
        runtime_dir=root,
        workspace=workspace,
        year=built_panel.year,
        exchange=built_panel.exchange,
        sessions=sessions,
        registry_knowable_from=registry_knowable,
        knowable_from_every_blocking_partition=knowable,
        session=session,
        instants=instants,
        universes=(wider[:UNIVERSE_SIZE], wider),
    )


@pytest.fixture(scope="session")
def rename_corpus(built_panel: BuiltPanel, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The one dataset this module needs and `BUILD_TARGETS` does not fetch, built at the instant
    the panel is about.

    **`openalpha shortlist run` requires it, and `V2-P4-078` is that nothing said so.**
    `_bars_on` builds each `MarketBar` with an `is_st` read off `NameHistory.risk_warning_on`, so a
    screen over a market it cannot say the risk warnings of would treat every ST name as ordinary;
    `load_name_histories` refuses a year that was never ingested rather than answering a shorter
    corpus. A panel built from `e2e_support.BUILD_TARGETS` -- five targets, seven datasets -- holds
    no such year, and the refusal a user met did not name the command that fixes it. That was
    `V2-P4-067`'s (b), measured here from the other side. `openalpha factor build --tier raw` does
    **not** need it, which is why this suite reached a green factor build and a red shortlist.

    That half is closed. `shortlist_view.SHORTLIST_PANEL_DATASETS` maps the six datasets this face
    reads to the five `panel build` targets that write them, the refusal for a panel holding no
    partition of one of them now carries `openalpha panel build --dataset namechange --year
    <year>`, and `shortlist run --help`, `README.md` and `docs/api/http.md` name all five before a
    caller can reach a refusal. `tests/integration/test_shortlist_build_prerequisites.py` drives
    each target's absence through a face; **this fixture is left as it is**, because what it
    demonstrates is that the corpus has to be *fetched* and no amount of documentation fetches it.

    **`--as-of` is the screenable instant and not the wall clock**, which was this module's second
    finding and is `V2-P4-077`. `namechange` is announcement-dated and fetched whole, so a build
    stamped `datetime.now(UTC)` on an overnight run carries a rename announced *tomorrow* while
    `daily` legitimately stops at the last session that published. Built at the instant the price
    plane can answer for, `ColumnarPanelBatch`'s own visible-at-`as_of` check leaves that row out.

    **Neither half of the wall that made is still standing, and both were re-measured offline.**
    `V2-P4-076` moved `load_name_histories` onto the per-event-date read, so a rename announced
    ahead of the read is now absent from the corpus rather than fatal to its year -- the "below"
    refusal does not reproduce at all. `V2-P4-077` then found that the surviving half was never
    about `namechange`: `_pricing_session` resolved a cross section to its instant's own Shanghai
    calendar day, so *any* cross section built between midnight and 16:30 asked for a session that
    had not published, and the refusal was permanent because the instant is stored on the cross
    section. The session is now the newest one that had published at that instant. Pinning the
    instant here therefore no longer decides whether this suite can screen at all; it stays because
    one instant for a build spanning tens of minutes is what `built_panel` already promises, and
    because a corpus fetched at the panel's own instant is the honest artifact to reason about.

    **It is one request, and only when the panel does not already hold a usable corpus.**
    `namechange` is one announcement year of the whole market (`cli.PANEL_BUILD_TARGETS`: "one
    request per `--year`"). It is written into the *shared* runtime directory rather than into a
    private view, so a panel reused through `OPENALPHA_E2E_RUNTIME_DIR` pays it once ever -- the
    opposite of what a derived factor partition would do to that directory, and the difference is
    that this one is **fetched**: a panel dataset `panel build` owns and replaces whole, not a
    stored answer a later build would meet as a restatement.
    """
    store = built_panel.store
    sessions = stored_sessions(store, dataset=DAILY_DATASET, year=built_panel.year)
    _registry, fetched_knowable = _blocking_instants(store, year=built_panel.year)
    instant = _instant_for(sessions[-1], knowable=fetched_knowable)
    if instant is None:
        raise E2EEnvironmentError(
            f"the newest session this panel stores is {sessions[-1].isoformat()} and the "
            f"partitions fetched beside it are knowable only from {fetched_knowable.isoformat()}, "
            "which is past that session's own evening; no instant this panel can price is one it "
            "may be read at, so there is no as_of to build the rename corpus for either"
        )
    held = store.read_coverage(NAMECHANGE_DATASET, built_panel.year)
    if held is not None and held.max_available_time <= instant:
        return
    run_build(
        tmp_path_factory.mktemp("shortlist-online-namechange"),
        built_panel.runtime_dir,
        target=NAMECHANGE_DATASET,
        year=built_panel.year,
        as_of=instant,
    )


@pytest.fixture(scope="module")
def screened(
    built_panel: BuiltPanel, rename_corpus: None, tmp_path_factory: pytest.TempPathFactory
) -> Screened:
    """The view the walk below builds its two cross sections into, made once."""
    del rename_corpus  # ordering only: the corpus has to be on disk before the view is linked.
    return _screened(
        built_panel,
        tmp_path_factory.mktemp("shortlist-online"),
        tmp_path_factory.mktemp("shortlist-online-cwd"),
    )


@pytest.fixture
def private_panel(built_panel: BuiltPanel, rename_corpus: None, tmp_path: Path) -> Screened:
    """A view of the same panel with no derived partition in it, for one test.

    The tests below that write a factor at an instant the walk also builds at cannot share the
    walk's store: a second build of one factor at one `as_of` under a different declaration is a
    *restatement*, which `V2-P4-071` deliberately still refuses. Its own view is what lets each of
    them state its own thing rather than the drop guard's.
    """
    del rename_corpus  # ordering only: see `screened`.
    return _screened(built_panel, tmp_path / "runtime", tmp_path / "cwd")


def _most_traded(store: PanelStore, *, year: int, session: date, count: int) -> tuple[str, ...]:
    """The `count` securities with the largest turnover on `session`, descending.

    Read straight off the partition with DuckDB, `e2e_support.readable_halt_days`' idiom, because
    `amount` is a column no reader in this repository reassembles: `load_daily_bars` answers
    `MarketBar`, which carries five prices and two flags and no turnover. That is exactly what
    makes it the right column to pick a universe with -- nothing downstream of here can see it, so
    a name's place on a shortlist cannot be an artefact of how it got into the universe.

    Ordered by turnover and then by `subject`, so the widening below is a prefix relationship
    rather than two independent draws: the first `UNIVERSE_SIZE` of this list are exactly the
    first screen's universe.

    `trade_date` is bound as an ISO string because that is how it is stored:
    `providers/tushare.py::_calendar_date_text` parses `YYYYMMDD` into `YYYY-MM-DD` text rather
    than into an instant, so a calendar date stays a date.
    """
    path = catalogued_path(store, dataset=DAILY_DATASET, year=year)
    with duckdb.connect() as reader:
        rows = reader.execute(
            "SELECT subject FROM read_parquet(?) WHERE trade_date = ? "
            "ORDER BY amount DESC NULLS LAST, subject LIMIT ?",
            [str(path), session.isoformat(), count],
        ).fetchall()
    universe = tuple(str(row[0]) for row in rows)
    if len(universe) < count:
        raise E2EEnvironmentError(
            f"only {len(universe)} securities carry a bar on {session.isoformat()}; this module "
            f"screens {count}"
        )
    return universe


@pytest.fixture(scope="module")
def serve_runtime_dir(screened: Screened) -> Path:
    """Point `conftest.py`'s `served` at this module's view rather than at the shared panel.

    An override of the seam that fixture takes its directory from, rather than a second fixture
    that spawns a server: `served` resolves a port, starts a real `openalpha serve` and polls it,
    and two copies of that is two things to keep in step.
    """
    return screened.runtime_dir


# --- driving the three commands ----------------------------------------------------------------


def _build_factor(
    screened: Screened,
    *instants: datetime,
    subjects: Sequence[str] | None = None,
    tier: str = TIER,
    transform: str | None = None,
) -> CLIResult:
    """`openalpha factor build` at `instants`, over `subjects`.

    `--max-staleness-days` is stated rather than waived because the panel underneath is a real one
    and its age is a real fact: every instant this module builds at is at least an hour after a
    session the partition holds, so a bound of one day is met with room -- and a build that had
    silently read a month-old partition would not have met it.
    """
    arguments = [
        "factor",
        "build",
        "--factor",
        FACTOR,
        "--tier",
        tier,
        "--year",
        str(screened.year),
        "--exchange",
        screened.exchange,
        "--max-staleness-days",
        "1",
        "--code-commit",
        COMMIT,
        "--runtime-dir",
        str(screened.runtime_dir),
        "--json",
    ]
    if transform is not None:
        arguments.extend(["--transform", transform])
    for instant in instants:
        arguments.extend(["--as-of", instant.isoformat()])
    for subject in screened.universes[0] if subjects is None else subjects:
        arguments.extend(["--subject", subject])
    return run_cli(*arguments, cwd=screened.workspace)


def _require_built(result: CLIResult, *, what: str) -> Any:
    """One `factor build` that has to have written something, or the run stops here."""
    if result.exit_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"`openalpha factor build` ({what}) exited {result.exit_code}: {result.stderr[:2000]}"
        )
    return result.payload()


def _shortlist(
    screened: Screened,
    *,
    as_of: datetime,
    tier: str = TIER,
    transform: str | None = None,
    shortlist_size: int = SHORTLIST_SIZE,
    minimum_researched_ratio: float = 0.0,
    evidence: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """`openalpha shortlist run --json`, as its exit code and its body.

    A *gate refusal* exits `1` and still prints the whole verdict; a *fault* exits non-zero with a
    sentence on stderr and no body at all. So the body is parsed when there is one and the raw
    output comes back under `output` when there is not, rather than this helper raising and hiding
    which of the two happened -- `tests/integration/test_shortlist_workflow.py::_run_shortlist`'s
    rule, and the distinction the whole `SHORTLIST_EXIT` table exists for.

    `--min-tradable-ratio 0` and `--max-ranking-age-days 3650` are inert on purpose, so that a
    refusal below is the one the test at hand asked for and never a leftover from a 60-name screen
    over a 5,000-name market, or from a panel this module was pointed at a fortnight after it was
    fetched.
    """
    arguments = [
        "shortlist",
        "run",
        "--component",
        f"{FACTOR}=1.0",
        "--tier",
        tier,
        "--shortlist-size",
        str(shortlist_size),
        "--position-capital",
        POSITION_CAPITAL,
        "--year",
        str(screened.year),
        "--horizon",
        HORIZON,
        "--min-tradable-ratio",
        "0.0",
        "--min-researched-ratio",
        str(minimum_researched_ratio),
        "--max-ranking-age-days",
        "3650",
        "--exchange",
        screened.exchange,
        "--as-of",
        as_of.isoformat(),
        "--code-commit",
        COMMIT,
        "--config-digest",
        CONFIG_DIGEST,
        "--runtime-dir",
        str(screened.runtime_dir),
        "--json",
    ]
    if transform is not None:
        arguments.extend(["--transform", transform])
    if evidence is not None:
        arguments.extend(["--evidence", str(evidence)])
    result = run_cli(*arguments, cwd=screened.workspace)
    body = result.stdout.strip()
    if not body.startswith("{"):
        return result.exit_code, {"output": f"{result.stdout}\n{result.stderr}"}
    return result.exit_code, json.loads(body)


def _subjects(answer: Mapping[str, Any]) -> tuple[str, ...]:
    """The shortlisted names, in the funnel's own order."""
    return tuple(entry["subject"] for entry in answer["funnel"]["shortlist"])


def _instant(rendered: object) -> datetime:
    """One rendered ISO-8601 instant as a `datetime`.

    Compared as instants rather than as strings because an `as_of` that goes through a Parquet
    `TIMESTAMPTZ` comes back normalised to UTC: `--as-of 2026-08-19T17:30:00+08:00` is rendered
    `2026-08-19T09:30:00+00:00`, which is the same instant and a different spelling. Asserting the
    spelling would be asserting DuckDB's storage convention.
    """
    return datetime.fromisoformat(str(rendered))


# --- the walk, run once --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Walk:
    """The walk's outputs, so each test below can hold one property of one walk."""

    first: dict[str, Any]
    second: dict[str, Any]
    first_again: dict[str, Any]
    """The first question, re-asked **after** the second instant was appended to the same
    partition. That it is still answerable is the whole of `V2-P4-071`."""


@pytest.fixture(scope="module")
def walk(screened: Screened) -> Walk:
    """Build a cross section, screen it, build the next one, screen the first again, screen the
    second -- in that order, from the command line, once.

    Module-scoped because it is the workflow rather than a setup step: five child processes over
    one store, in an order the store remembers. A function-scoped version would rebuild the same
    partition for each test, and the append this file exists to drive would become a restatement
    of itself.
    """
    first_instant, second_instant = screened.instants
    narrow, wider = screened.universes

    _require_built(
        _build_factor(screened, first_instant, subjects=narrow),
        what=f"the first cross section, at {first_instant.isoformat()}",
    )
    first_code, first = _shortlist(screened, as_of=screened.asked_at(first_instant))
    if first_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"the first `shortlist run` exited {first_code} rather than answering: {first}"
        )

    # The second invocation into the same `(dataset, year)` partition, which is the step that was
    # refused before `V2-P4-071` and is refused again the moment `carry_stored_rows_forward` is
    # taken back out of `panel_factors.write_factor_panels`. Sixty of these subjects are the first
    # build's, so the arriving batch collides with what is stored on subject and not on
    # `event_time` -- which is the pair `identity_columns` exists to tell apart.
    _require_built(
        _build_factor(screened, second_instant, subjects=wider),
        what=f"the second invocation, at {second_instant.isoformat()}",
    )

    again_code, first_again = _shortlist(screened, as_of=screened.asked_at(first_instant))
    if again_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"the first question, re-asked after the second instant was appended, exited "
            f"{again_code}: {first_again}"
        )
    second_code, second = _shortlist(screened, as_of=screened.asked_at(second_instant))
    if second_code != EXIT_OK:
        raise E2EEnvironmentError(f"the second `shortlist run` exited {second_code}: {second}")
    return Walk(first=first, second=second, first_again=first_again)


# --- the chain, from a fetched panel to a list of real securities --------------------------------


def test_the_chain_from_a_fetched_panel_reaches_a_shortlist_of_real_securities(
    screened: Screened, walk: Walk
) -> None:
    """`panel build` -> `factor build` -> `shortlist run` answers, and the answer is a list.

    The claim no offline test in this repository can make: the funnel cut `SHORTLIST_SIZE` names
    out of a cross section derived from rows the endpoint served, over the registry's real listed
    set on a real session, and every name it cut came back out of the universe this module
    screened rather than out of some other partition the store happens to hold.

    `universe_count` is the *registry's* market, and it is asserted to exceed the screened
    universe. That is what separates "the screen ran over the market" from "the screen ran over
    its own input list", and it is the shape `V2-P4-059` measured going wrong: a `--year 2026`
    registry read that answered eleven securities, and a list published out of those eleven.
    """
    for label, answer, universe in (
        ("the first", walk.first, screened.universes[0]),
        ("the second", walk.second, screened.universes[1]),
    ):
        shortlisted = _subjects(answer)
        assert len(shortlisted) == SHORTLIST_SIZE, f"{label}: {answer['funnel']}"
        assert set(shortlisted) <= set(universe), (
            f"{label} shortlisted a name outside the universe it was derived over: "
            f"{sorted(set(shortlisted) - set(universe))}"
        )
        assert answer["funnel"]["coverage"] == "shortlisted", label
        assert answer["measurement"]["universe_count"] > WIDER_UNIVERSE_SIZE, (
            f"{label}: the funnel's universe is {answer['measurement']['universe_count']}, which "
            "is not the market the registry lists on that session"
        )
        assert SHORTLIST_ID_PATTERN.fullmatch(str(answer["shortlist_id"])), label
        assert answer["declaration"]["components"][0]["factor"] == FACTOR, label
        assert answer["tier"] == TIER, label


def test_each_answer_names_the_cross_section_its_own_build_wrote(
    screened: Screened, walk: Walk
) -> None:
    """The two answers name two instants, and each prices the session that instant is about.

    `cross_section.as_of` is the resolved instant -- the newest stored build visible at the
    request's `as_of` -- and `pricing_session` is the session stage two priced against. Asserting
    both is what stops "the two answers differ" from being satisfied by a second run that resolved
    the same cross section twice and merely stamped it differently.

    Both are derived from the panel rather than written down. What is pinned is the
    *correspondence*: the instant a build was stamped at, the instant the answer says it read, and
    the session the calendar maps that instant onto.
    """
    first_instant, second_instant = screened.instants
    assert _instant(walk.first["cross_section"]["as_of"]) == first_instant
    assert walk.first["cross_section"]["pricing_session"] == screened.session.isoformat()
    assert _instant(walk.second["cross_section"]["as_of"]) == second_instant
    assert walk.second["cross_section"]["pricing_session"] == screened.session.isoformat()
    assert _instant(walk.first["as_of"]) == screened.asked_at(first_instant)
    assert _instant(walk.second["as_of"]) == screened.asked_at(second_instant)
    assert walk.first["cross_section"]["components"][0]["row_count"] == UNIVERSE_SIZE
    assert walk.second["cross_section"]["components"][0]["row_count"] == WIDER_UNIVERSE_SIZE


# --- the second instant, and the first answer intact ---------------------------------------------


def test_a_second_instant_is_added_without_destroying_the_first(
    screened: Screened, walk: Walk
) -> None:
    """`V2-P4-071` on live rows: the second `factor build` **added** to the year's partition.

    Two claims, and the second is what makes the first mean anything.

    The append landed: the second screen resolves its cross section to the second build, so a
    second instant is in a partition that is written whole. And the first survived it: the *same*
    question re-asked afterwards comes back with the first instant, the first ranked list and the
    first `shortlist_id` -- identical content addresses over an answer computed twice, one build
    apart.

    A `--supersedes-raw` escape passes the first claim and fails the second, and that escape is
    what the two available routes to a second instant were: recompute the first into the same
    invocation, or erase it. The wrong-answer run is `carry_stored_rows_forward` deleted from
    `panel_factors.write_factor_panels`, where the second `factor build` exits `1` with `already
    holds N subject(s) and this write carries N; it would drop [...]` and this walk never reaches
    its third step.
    """
    first_instant, _second = screened.instants
    assert _instant(walk.first_again["cross_section"]["as_of"]) == first_instant
    assert walk.first_again["shortlist_id"] == walk.first["shortlist_id"]
    assert walk.first_again["funnel"]["shortlist"] == walk.first["funnel"]["shortlist"]
    assert walk.first_again["ranking_manifest_id"] == walk.first["ranking_manifest_id"]


def test_the_two_answers_are_two_documents_over_two_different_cross_sections(
    screened: Screened, walk: Walk
) -> None:
    """Two instants, two addresses, two lists -- and the second one names the wider market.

    The two answers are two documents because `shortlist_id` addresses the whole rendered body and
    the two carry different `as_of`s. That much would hold for a repeat, so it is not what this
    test is for.

    What it is for is that the two cross sections are *different*: the second was derived over
    `WIDER_UNIVERSE_SIZE` securities and the first over `UNIVERSE_SIZE`, both stored in one
    partition, and the second's top ten has to reach into the names the first never ranked. A
    second screen that had merely re-read the first cross section would come back with the first
    ten, which is exactly the shape a broken append or a mis-resolved instant produces -- so the
    assertion is written as "at least one of these names was not available to the first screen"
    rather than as "the two differ", which a tie-break could satisfy.
    """
    narrow, wider = screened.universes
    added = set(wider) - set(narrow)
    assert walk.first["shortlist_id"] != walk.second["shortlist_id"]
    assert _instant(walk.first["cross_section"]["as_of"]) != _instant(
        walk.second["cross_section"]["as_of"]
    )
    assert len(_subjects(walk.first)) == len(_subjects(walk.second)) == SHORTLIST_SIZE
    assert set(_subjects(walk.second)) & added, (
        f"the second screen ranked {WIDER_UNIVERSE_SIZE} names and its top {SHORTLIST_SIZE} came "
        f"entirely out of the {UNIVERSE_SIZE} the first screen ranked; over a one-day return "
        "that is a read that did not widen rather than a market that did not move"
    )
    assert _subjects(walk.first) != _subjects(walk.second)


# --- the answers are held, and retrievable by address --------------------------------------------


def test_one_stored_answer_comes_back_the_same_from_the_command_line_the_route_and_the_sdk(
    screened: Screened, walk: Walk, served: str
) -> None:
    """`V2-P4-062` on live rows: three faces, one address, one document.

    Before it there was no store and no route: the answer carried three content addresses with
    nothing to address, and "what did we say yesterday" ended at redirecting `--json` into a file.

    The three are compared to each other rather than to a literal, which is `panel_view`'s own
    argument -- two renderings of one verdict that disagree about which fields exist is how a
    caller comes to believe a bar was cleared when the key was merely dropped. The re-derivation
    is the other half: `open_shortlist` recomputes the digest from the content before handing it
    over, so `shortlist_id == address` is a statement about the bytes on disk rather than about
    the filename they are under.
    """
    address = str(walk.first["shortlist_id"])

    from_cli = run_cli(
        "shortlist",
        "get",
        address,
        "--runtime-dir",
        str(screened.runtime_dir),
        cwd=screened.workspace,
    )
    assert from_cli.exit_code == EXIT_OK, from_cli.stderr[:2000]
    held = from_cli.payload()

    status, over_http = http_get(served, f"/api/v1/shortlists/{address}", ())
    assert status == 200, over_http

    from_sdk = OpenAlphaSDK(runtime_dir=screened.runtime_dir).held_shortlist(address)

    assert held == over_http == from_sdk
    assert held["shortlist_id"] == address
    assert _subjects(held) == _subjects(walk.first)
    assert held["funnel"]["shortlist"] == walk.first["funnel"]["shortlist"]


def test_the_two_answers_can_be_compared_after_the_fact(screened: Screened, walk: Walk) -> None:
    """ "What did we say last time, and what do we say now", answered out of what is held.

    The comparison is made from the *stored* documents rather than from the two run outputs the
    walk still has in hand, because that is the question the product could not answer: a caller
    comes back tomorrow holding two addresses and nothing else. `openalpha shortlist list` is what
    hands them those addresses, so both of the walk's answers have to be in it.

    What the diff itself is asserted to be is a fact about *lists of equal length* -- a name that
    entered displaced one that left -- rather than a named security, which would be a fixture in
    disguise. Which names moved is a fact about the A-share market and is not this repository's to
    predict; that both documents say which session they are about is what makes the diff readable
    a week later rather than a pair of anonymous lists.
    """
    listed = run_cli(
        "shortlist",
        "list",
        "--runtime-dir",
        str(screened.runtime_dir),
        "--json",
        cwd=screened.workspace,
    )
    assert listed.exit_code == EXIT_OK, listed.stderr[:2000]
    addresses = set(listed.payload()["shortlist_ids"])
    assert {walk.first["shortlist_id"], walk.second["shortlist_id"]} <= addresses

    sdk = OpenAlphaSDK(runtime_dir=screened.runtime_dir)
    before_document = sdk.held_shortlist(str(walk.first["shortlist_id"]))
    after_document = sdk.held_shortlist(str(walk.second["shortlist_id"]))

    before, after = set(_subjects(before_document)), set(_subjects(after_document))
    entered, left = sorted(after - before), sorted(before - after)
    assert entered and left, (
        f"the two held answers describe one list; entered {entered}, left {left}"
    )
    assert len(entered) == len(left), (
        f"the two lists are the same length, so a name that entered must have displaced one that "
        f"left; entered {entered}, left {left}"
    )
    assert set(entered) | set(left) <= set(screened.universes[1])
    for document in (before_document, after_document):
        assert document["cross_section"]["pricing_session"] == screened.session.isoformat()


def test_an_address_nothing_is_held_under_is_a_named_refusal_and_not_an_empty_answer(
    screened: Screened, walk: Walk, served: str
) -> None:
    """The wrong-answer control for retrieval, over the same store the walk filled.

    Two failures rather than one, in the order the code checks them: a **malformed** address is
    `3`/`422` and a **well-formed** address nothing is held under is `1`/`404`. The order is
    load-bearing -- a caller who mistyped an address must not be told their answer was lost -- and
    the pair is what makes the retrieval above a real assertion. Without it, `shortlist get`
    returning a document would be consistent with a command that returns one for anything.
    """
    del walk  # the store has to hold something, or "not held" is the only answer it could give.
    unheld = run_cli(
        "shortlist",
        "get",
        UNHELD_SHORTLIST,
        "--runtime-dir",
        str(screened.runtime_dir),
        cwd=screened.workspace,
    )
    assert unheld.exit_code == EXIT_UNHEALTHY, unheld.stdout[:2000]
    assert unheld.stdout.strip() == ""

    malformed = run_cli(
        "shortlist",
        "get",
        "not-an-address",
        "--runtime-dir",
        str(screened.runtime_dir),
        cwd=screened.workspace,
    )
    assert malformed.exit_code == EXIT_BAD_REQUEST, malformed.stdout[:2000]

    assert http_get(served, f"/api/v1/shortlists/{UNHELD_SHORTLIST}", ())[0] == 404
    assert http_get(served, "/api/v1/shortlists/not-an-address", ())[0] == 422


# --- what it takes to publish one, and what refuses it -------------------------------------------


def _signal(subject: str, *, as_of: datetime) -> dict[str, Any]:
    """One evidence-plane conclusion, as the wire carries it.

    `as_of` and `horizon` have to be the ranking's own: `CandidateRanking` refuses a list holding
    two horizons or two instants, which is a rule about the question rather than about the market.
    """
    frame = SignalFrame(
        subject=subject,
        as_of=as_of,
        direction="bullish",
        strength=0.4,
        confidence=0.7,
        horizon=HORIZON,
        evidence_ids=("evd_000000000000000000000001",),
    )
    return dict(json.loads(frame.model_dump_json()))


def _evidence_file(
    path: Path, subjects: Sequence[str], *, as_of: datetime, runs: Mapping[str, str]
) -> Path:
    """`--evidence`'s document: one `{signal, run_manifest_id}` per researched name."""
    path.write_text(
        json.dumps(
            {
                subject: {
                    "signal": _signal(subject, as_of=as_of),
                    "run_manifest_id": runs[subject],
                }
                for subject in subjects
            }
        ),
        encoding="utf-8",
    )
    return path


def _store_runs(screened: Screened, subjects: Sequence[str], *, as_of: datetime) -> dict[str, str]:
    """Append one `RunManifest` per name through the SDK, and hand back their addresses.

    The SDK's repository rather than a hand-written row, and the address read off the manifest
    rather than invented: `run_manifest_id` is a `computed_field` over the manifest's own content,
    so a test that spelled one out would be asserting against its own arithmetic while
    `stored_run_manifest_ids` resolves what the store actually holds.
    """
    repository = OpenAlphaSDK(runtime_dir=screened.runtime_dir).repository
    addresses: dict[str, str] = {}
    for subject in subjects:
        manifest = RunManifest(
            run_id=f"run-{subject}-{as_of.isoformat()}",
            mode="backtest",
            as_of=as_of,
            code_commit=COMMIT,
            config_digest=CONFIG_DIGEST,
            random_seed=7,
            started_at=as_of,
            finished_at=as_of,
            status="succeeded",
        )
        repository.append_run(manifest)
        addresses[subject] = manifest.run_manifest_id
    return addresses


def test_a_list_nobody_has_researched_is_admitted_empty_and_refused_by_name_under_a_floor(
    screened: Screened, walk: Walk
) -> None:
    """The pair the third product acceptance found collapsed into one, on real rows.

    A shortlist of ten names nobody has researched, under `--min-researched-ratio 0`, is
    *admitted*: exit `0`, `is_blocked: false`, and `admitted` an **empty JSON array** -- nothing
    refused it and there was nothing to publish. The identical run under a floor of `1.0` is
    *refused*: exit `1`, `is_blocked: true`, `admitted: null`, and one block naming the bar and
    both sides of it.

    `null` against `[]` and `0` against `1` are the two ways those answers are told apart, and the
    defect was that at no surface could they be told apart at all. The floor is the only thing
    that moves between the two runs.
    """
    first_instant, _second = screened.instants
    assert walk.first["is_blocked"] is False
    assert walk.first["admitted"] == []
    assert walk.first["blocks"] == []
    assert walk.first["measurement"]["researched_ratio"] == 0.0
    assert sorted(walk.first["unresearched"]) == sorted(_subjects(walk.first))

    code, refused = _shortlist(
        screened, as_of=screened.asked_at(first_instant), minimum_researched_ratio=1.0
    )
    assert code == EXIT_UNHEALTHY, refused
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None
    assert [block["code"] for block in refused["blocks"]] == ["researched_ratio_below_floor"]
    assert refused["blocks"][0]["required"] == 1.0
    assert refused["blocks"][0]["measured"] == 0.0
    # A refused list is a different answer and therefore a different document, which is the whole
    # of what content addressing buys: one question under two gates cannot share one address.
    assert refused["shortlist_id"] != walk.first["shortlist_id"]


def test_a_published_list_needs_evidence_naming_a_run_this_deployment_holds(
    screened: Screened, walk: Walk, tmp_path: Path
) -> None:
    """`V2-P4-049` on live rows: the same conclusions publish or do not, on one field.

    Two evidence documents, identical in every byte except the `run_manifest_id`. Against
    addresses the store holds, the ten real securities the first screen shortlisted clear a
    `--min-researched-ratio 1.0` and come back on `admitted`, each carrying the run it was
    researched under. Against `run_000000000000000000000000` -- well-formed, stored nowhere -- the
    same ten names are dropped before the ranking sees them, `researched_ratio` is `0.0` rather
    than `1.0`, the list is refused, and every name is reported on `evidence_without_a_stored_run`
    rather than silently.

    The second run is this test's own wrong answer, and it is the probe the acceptance filed:
    before `049` this exact document published 25 candidates at `researched_ratio: 1.0`.
    """
    first_instant, _second = screened.instants
    asked = screened.asked_at(first_instant)
    shortlisted = _subjects(walk.first)

    stored = _store_runs(screened, shortlisted, as_of=asked)
    resolvable = _evidence_file(tmp_path / "resolvable.json", shortlisted, as_of=asked, runs=stored)
    code, published = _shortlist(
        screened, as_of=asked, minimum_researched_ratio=1.0, evidence=resolvable
    )
    assert code == EXIT_OK, published
    assert published["is_blocked"] is False
    assert published["measurement"]["researched_ratio"] == 1.0
    assert published["admitted"] is not None
    assert sorted(entry["subject"] for entry in published["admitted"]) == sorted(shortlisted)
    assert {entry["run_manifest_id"] for entry in published["admitted"]} == set(stored.values())
    assert published["evidence_without_a_stored_run"] == []
    assert published["unresearched"] == []

    unstored = _evidence_file(
        tmp_path / "unstored.json",
        shortlisted,
        as_of=asked,
        runs=dict.fromkeys(shortlisted, UNSTORED_RUN),
    )
    code, refused = _shortlist(
        screened, as_of=asked, minimum_researched_ratio=1.0, evidence=unstored
    )
    assert code == EXIT_UNHEALTHY, refused
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None
    assert refused["measurement"]["researched_ratio"] == 0.0
    assert refused["evidence_without_a_stored_run"] == sorted(shortlisted)
    assert [block["code"] for block in refused["blocks"]] == ["researched_ratio_below_floor"]


# --- the refusals a real market produces ---------------------------------------------------------


def test_a_cross_section_earlier_than_the_registrys_own_availability_is_refused(
    private_panel: Screened,
) -> None:
    """`V2-P4-061`'s wall, still standing on the dataset that fix did not move.

    `061` put `daily` and `stk_limit` on `panel_ingest._read_visible_price_session` so that an
    earlier session could be priced, and stated what the old behaviour cost: "two days' shortlists
    could not be compared, yesterday's could not be re-run, and a published list could not be
    audited after the fact". On a real panel all three are still true, because the *registry* is
    read at the same instant and still goes through `read_if_ready` -- which refuses a whole
    partition whose newest `available_time` is later than the `as_of`.

    `stock_basic`'s build-year partition carries a row per listing available at that listing's own
    midnight, and A-shares list on most sessions, so its availability instant is normally the
    midnight of the panel's newest session or later. Every prediction instant before it is refused
    for the registry -- not for the prices, which `061` fixed.

    **The contrast is in this test.** The same command, the same store, the same market: at the
    session immediately before the earliest screenable one it is refused by name, and at the
    screenable instant it exits `0` and writes a partition. The only thing that differs is which
    side of `stock_basic`'s own availability instant the `as_of` falls on.
    """
    first_instant, _second = private_panel.instants
    earlier_sessions = [
        session
        for session in private_panel.sessions
        if _stamped(session) < private_panel.registry_knowable_from
    ]
    if not earlier_sessions:
        pytest.skip(
            "every stored session of this panel is stamped at or after "
            f"{private_panel.registry_knowable_from.isoformat()}, so no security listed inside "
            "the build year late enough to put the registry ahead of the price plane"
        )
    refused = _build_factor(private_panel, _stamped(earlier_sessions[-1]))
    assert refused.exit_code == EXIT_UNHEALTHY, refused.stdout[:2000]
    assert "not_yet_knowable" in refused.stderr, refused.stderr[:2000]
    assert STOCK_BASIC_DATASET in refused.stderr, refused.stderr[:2000]

    _require_built(
        _build_factor(private_panel, first_instant),
        what=f"the same command one instant later, at {first_instant.isoformat()}",
    )


def test_a_security_the_registry_delisted_inside_this_year_is_refused_by_name(
    private_panel: Screened,
) -> None:
    """The refusal no generated panel in this repository can produce.

    `tests/panel_fixtures.py` writes a registry of names that list once and never die. The real
    `stock_basic` corpus carries hundreds of securities with a `delist_date`, and `V2-P4-060`
    exists because a mid-window delisting made a registry read structurally invalid. This takes
    one of them -- a security whose **real** delisting fell inside the panel's own build year, so
    it traded into this year's `daily` partition and was gone by the session screened here --
    derives `reversal_1d/v1` over exactly that name, and screens it.

    `CrossSectionScreen.select` bounds the whole funnel by the registry's listed set, so the
    stored value is never offered to either stage: the component admits nothing, and
    `_refuse_a_component_the_panel_never_valued` refuses the run rather than returning a verdict
    whose only block would be `researched_ratio_not_measurable` -- a bar on the evidence plane,
    whose implied remedy is to research a security that no longer exists.

    **The wrong answer is in this test.** The same store, the same command and the same market:
    the universe is built at the second instant, and asking there answers with a list. The only
    thing that changes between the two calls is whether the names the screen finds are ones the
    registry still lists.
    """
    first_instant, second_instant = private_panel.instants
    registry = load_stock_universe(
        private_panel.store,
        years=(private_panel.year,),
        as_of=first_instant,
        max_staleness=None,
    )
    gone = sorted(
        (entry.delisted_on, entry.ts_code)
        for entry in registry.securities
        if entry.delisted_on is not None
        and entry.delisted_on <= private_panel.session
        and entry.delisted_on.year == private_panel.year
    )
    if not gone:
        pytest.skip(
            f"the registry records no security delisted between 1 January {private_panel.year} "
            f"and {private_panel.session.isoformat()}; this refusal needs a real one"
        )
    _delisted_on, subject = gone[-1]
    assert subject not in registry.listed_on(private_panel.session)

    _require_built(
        _build_factor(private_panel, first_instant, subjects=(subject,)),
        what=f"one delisted security at {first_instant.isoformat()}",
    )
    code, refused = _shortlist(
        private_panel, as_of=private_panel.asked_at(first_instant), shortlist_size=1
    )
    assert code == EXIT_UNHEALTHY, refused
    message = str(refused["output"])
    assert "admits no value this screen can order" in message, message
    assert FACTOR in message, message

    _require_built(
        _build_factor(private_panel, second_instant),
        what=f"the listed universe at {second_instant.isoformat()}",
    )
    answered_code, answered = _shortlist(
        private_panel, as_of=private_panel.asked_at(second_instant)
    )
    assert answered_code == EXIT_OK, answered
    assert len(_subjects(answered)) == SHORTLIST_SIZE
    assert subject not in _subjects(answered)


def test_a_processed_screen_of_this_market_is_refused_by_the_transforms_own_floor(
    private_panel: Screened,
) -> None:
    """`cross_section_standard/v1` declines a 60-name cross section, and says so by name.

    The transform declares `min_cross_section=100` and this module derives a factor over
    `UNIVERSE_SIZE` securities, so every stored processed row carries `insufficient_cross_section`,
    the component admits nothing, and the refusal names the floor, the width this market offered
    and the three remedies. A named refusal is a legitimate end of this workflow and it is the one
    a narrow screen reaches.

    It is here rather than left as a claim in `TIER`'s docstring because the sentence it prints is
    the deliverable: `V2-P4-044` is the issue where the same shape answered
    `researched_ratio_not_measurable` -- pointing a caller at the evidence plane for a fact about
    the market's width -- and neither `min_cross_section` nor `insufficient_cross_section`
    appeared anywhere in the answer.
    """
    first_instant, _second = private_panel.instants
    transform = "cross_section_standard/v1"
    _require_built(
        _build_factor(private_panel, first_instant, tier="processed", transform=transform),
        what=f"the processed tier at {first_instant.isoformat()}",
    )
    code, refused = _shortlist(
        private_panel,
        as_of=private_panel.asked_at(first_instant),
        tier="processed",
        transform=transform,
    )
    assert code == EXIT_UNHEALTHY, refused
    message = str(refused["output"])
    assert "insufficient_cross_section" in message, message
    assert "min_cross_section" in message, message
    assert str(UNIVERSE_SIZE) in message, message
