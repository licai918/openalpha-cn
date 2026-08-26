"""The model chain on rows Tushare served: fit a schedule, register today's answer, and be able
to hand it back.

## Why this file exists (`V2-P4-072`, one plane over)

`V2-P4-072` filed that P4's shipped surface had no end-to-end coverage, and closing it for the
*shortlist* chain immediately proved `V2-P4-061` was not actually fixed. The model chain --
`V2-P4-010` through `V2-P4-017` and `V2-P4-021` -- was left in exactly the position the shortlist
chain had been in: nine issues, a technical acceptance, and no test that touches real data.
Measured rather than assumed, at `8fd132d`: `grep -rn "model\\|prediction" tests/e2e/` returned
nine matches, and every one was incidental -- `model_dump_json`, a local named `prediction_day`,
the shortlist module's `_prediction_instants`. Not one reached `openalpha model`.

So this module drives `factor build` -> `model evaluate` -> `model daily-run` -> `model
prediction` over the panel `built_panel` fetched, and reads the registered answer back out of all
three faces that serve it.

## The first finding: `factor build --subject` does not narrow what `model evaluate` labels

`feature_matrix.py`'s "Where this module differs from that one, deliberately: **the rows are the
universe**" is the whole of it. A `factor build --subject a --subject b` stores two observations,
and a `model evaluate` reading that partition still builds a cross-section row for **every
security the registry lists** -- between 5,515 and 5,547 of them on this panel, depending on which
instant the registry is read at -- and then labels every one of them.
`--subject` therefore buys a cheaper *build* and buys nothing at all on the *evaluation*, which is
the opposite of what the cost note in `test_shortlist_workflow_online.py` establishes for the
shortlist chain, where `--subject` really does keep the work proportional to the universe.

Two consequences this module is shaped by. `scored_ratio` is measured against the whole listed
market, so a sixty-name factor scores **348 of the 33,090 security-days it was offered -- 1.05%**
-- and **no meaningful `--min-scored-ratio` can be cleared**; `NO_FLOOR` is declared for that
reason and
`test_a_narrow_universe_cannot_clear_a_floor_the_whole_market_sets` drives the other direction
rather than letting the bar quietly go away. And a corporate action anywhere in the market can
refuse the run, which is the second finding.

## The second finding: two price datasets disagreeing about one corporate action refuses the run

The first evaluation this module ever ran, over the twenty sessions ending on the panel's newest,
did not reach a statistic. It got this, on exit 1::

    689009.SH's outcome over 2026-07-31..2026-08-07 could not be priced out of ...: 689009.SH on
    2026-08-07: the implied pre_close from 2026-08-06's close and the adjustment factors is
    41.358834586466166, and daily published 41.38 -- a gap of 0.021165413533836386, past the
    0.01789086791226186 that one tick of pre_close (0.01) plus one tick of each adj_factor
    (0.0001, carried into price space at this price and these factors) allows. The two datasets
    disagree about that session's corporate action, so neither return can be trusted

`689009.SH` is not in this module's universe and never was: it is a row of the *cross section*,
which is the registry. `session_returns` is doing exactly what it documents -- `daily` and
`adj_factor` are two independent statements about one corporate action, and it refuses rather than
returning a number that is wrong -- and no synthetic fixture in this repository had produced the
shape, because a generated panel's factors and closes are generated together and therefore agree
by construction.

**Measured across the whole stored year: five sessions out of 151, one security each.**
`_contested_sessions` below is the scan, and it uses the repository's own `pre_close_tolerance`
rather than a re-derivation of the bound. Those five sessions are what `_clean_run` steers the
evaluation window around, and steering around them is the only reason this module reaches a
per-fold statistic at all. The same scan supplies the window that deliberately meets one, which is
`test_an_outcome_window_the_two_price_datasets_disagree_about_is_refused_by_name` -- the
wrong-answer control for the headline test, and the one refusal here that real data alone
produces.

## What is asserted, on a panel whose contents nobody chose

`e2e_support.py`'s "Determinism" rule, unchanged: no price, no session, no ticker and no row count
is written into an assertion. Every date below is derived from the panel -- the sessions the
`daily` partition holds, the instants the catalog says its partitions became knowable, the
sessions the two price datasets agree about. What replaces a fixed expectation is:

- **An internal agreement.** One `record_id` fetched three ways -- `openalpha model prediction`, a
  real `openalpha serve` over `GET /api/v1/predictions/{record_id}`, and `OpenAlphaSDK.
  held_prediction` -- is one document.
- **An agreement with a rule the record carries its own inputs for.** `standing` is recomputed
  from the record's own `predicted_at`, `recorded_at` and `outcome_known_at` and checked against
  the badge the store put on it, so the assertion holds on a panel of any age.
- **A refusal produced on purpose.** A window containing a contested corporate action; an address
  nothing is held under; a floor a narrow universe cannot clear; a prediction about a day the
  panel's price plane does not reach.

## `standing`, and why it is not spelled `forward` in an assertion

`PredictionRecord.standing` is computed, never stored::

    if self.batch.predicted_at >= self.outcome_known_at: return "backfill"
    return "forward" if self.recorded_at < self.outcome_known_at else "unwitnessed"

`recorded_at` is stamped by the store's own clock and `predicted_at` by the process's, so which
of the three a real run gets is decided by **how old the panel is when the suite runs**. On the
panel this was written against -- newest session 2026-08-19, run on 2026-08-21 -- a daily run
about that session's evening has an outcome knowable at 2026-08-27T15:00+08:00, which had not
happened, and the standing is `forward`. Run the same command against the same reused panel after
that instant and the honest answer is `unwitnessed`; the record has not changed and the world has.

`test_a_registered_prediction_carries_the_standing_its_own_clocks_imply` therefore asserts the
*rule* against the record's own numbers, which is true whenever it runs, and
`test_a_prediction_about_an_outcome_the_world_has_not_reached_yet_stands_forward` asserts the
`forward` badge specifically and raises `E2EEnvironmentError` naming the remedy -- rebuild the
panel -- when the panel has aged past being able to demonstrate it. A `pytest.skip` is the wrong
shape here for the reason `E2EEnvironmentError` exists.

## What this module does not cover

`--family boosted_rank_trees` (the stdlib gradient-boosted baseline) is declared and not driven:
it is a second implementation behind the same face, and the face is what has no coverage. The
`processed` and `neutralized` tiers are not built -- `neutralized` is refused by this face by name
(`a_neutralized_feature_column_is_refused_by_this_face`) and `processed` would double the build
without changing which faces are reached. `POST /api/v1/models/evaluate` and `POST
/api/v1/models/daily-run` are not driven; the HTTP face is exercised on the retrieval route,
which is the one `V2-P4-017` built a store for and the one a reader of a published prediction
actually calls.

## Cost

**Zero requests.** Everything below derives from the stored panel: `factor build` computes from
partitions, `model evaluate` and `model daily-run` read them, and none of the three constructs a
provider. `built_panel` is whatever `OPENALPHA_E2E_RUNTIME_DIR` points at, and this module adds no
build target to it -- unlike `test_shortlist_workflow_online.py`, which fetches `namechange`,
nothing on this face reads a name history (`model evaluate --help` says so, measured rather than
assumed: nothing here builds a `MarketBar`).

Measured on the panel this was written against: one `factor build` over 22 instants x 60 subjects
is about 150s, one `model evaluate` about 120s, one `model daily-run` about 60s. The module-scoped
`fitted` fixture runs each of those **once** and every assertion below reads its cached answer;
only the four refusal tests spend their own invocation, and each of those aborts early.

`private_panel` hard-links the Parquet files and copies only the catalog, `_link_panel`'s idiom
from the shortlist module and for its reason: the `daily` partition is around 835,000 rows, and a
`factor build` into the *shared* `OPENALPHA_E2E_RUNTIME_DIR` would be a stored build the next run
meets as a restatement at an `as_of` the year already holds -- refused by name, correctly -- so
the directory the whole cost discipline rests on would work exactly once.
"""

from __future__ import annotations

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
    run_cli,
    stored_sessions,
)

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_DATASET,
    pre_close_tolerance,
)
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.model_view import MODEL_VIEW_LIMITATION_CODES, ModelNotHeldError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_view import panel_store
from openalpha_cn.sdk import OpenAlphaSDK

pytestmark = pytest.mark.e2e


FACTOR: Final[str] = "reversal_1d/v1"
"""The one factor this module fits on.

A one-session return, so it is computable for any name that carries two consecutive bars, and it
is the factor `model evaluate --help` itself reaches for. Which factor is not what is under
test here -- the face is."""

FEATURE: Final[str] = f"{FACTOR}@raw"
"""The declared feature column. `raw` because `processed` would need a transform tier built beside
it and `neutralized` is refused by this face by name."""

MODEL_NAME: Final[str] = "reversal-rank"
MODEL_FAMILY: Final[str] = "cross_sectional_rank"
"""The stdlib rank baseline. `boosted_rank_trees` is the other declared family and is deliberately
not driven; see this module's docstring."""

HORIZON_SESSIONS: Final[int] = 5
HORIZON: Final[str] = f"{HORIZON_SESSIONS}d"
"""The span every outcome here is measured over. A label reaches `HORIZON_SESSIONS + 1` sessions
past its prediction day, because `entry_day` is the session *after* it."""

SEED: Final[int] = 7

FOLDS: Final[int] = 2
TEST_DAYS_PER_FOLD: Final[int] = 3
EMBARGO_SESSIONS: Final[int] = 1
"""The declared schedule. `TEST_DAYS_PER_FOLD` is 3 rather than the 2 that
`alpha_baseline.MINIMUM_FOLD_DAYS` requires, so a fold that loses one day to an abstention is
still `measured` rather than `insufficient_as_ofs`."""

PREDICTION_DAYS: Final[int] = 20
"""How many stored cross sections the evaluation reads.

The floor `walk_forward_folds` imposes is `FOLDS * TEST_DAYS_PER_FOLD + HORIZON_SESSIONS + 1` --
`{FOLDS * TEST_DAYS_PER_FOLD + HORIZON_SESSIONS + 1}` here -- being `folds * test_days` tested
days at the tail, the purge's reach of one label window behind them, and at least one prediction
day left to train the first fold on. Twenty leaves the first fold seven training days after the
purge and the embargo have taken theirs, which is margin rather than a bound; see
`_MINIMUM_PREDICTION_DAYS`, which states the arithmetic where a reader can check it."""

_MINIMUM_PREDICTION_DAYS: Final[int] = FOLDS * TEST_DAYS_PER_FOLD + HORIZON_SESSIONS + 1

UNIVERSE_SIZE: Final[int] = 60
"""How many names the factor is *built* for.

`V2-P4-072`'s precedent, and its argument holds here unchanged: the most-traded names are the ones
that actually carry a bar, and `amount` is a column no factor, funnel or gate reads, so a name's
score cannot be an artefact of how it got into the universe. What does **not** hold here is the
cost half of that argument -- see this module's first finding. Sixty is kept because the *build*
is still proportional to it and because a wider one would not change a single assertion."""

BUILD_HOUR_AFTER_CLOSE: Final[timedelta] = timedelta(hours=1)
"""How long after a session's rows become knowable a cross section about it is stamped.
`DAILY_AVAILABILITY_TIME` is 16:30 Asia/Shanghai, so a build lands at 17:30 that evening."""

SECOND_INSTANT_TIME: Final[time] = time(23, 30)
"""When the second daily run is stamped: late on the same evening, still that session's Shanghai
day, so both runs price the same session and the only thing that differs is the instant. That is
what makes `test_a_second_daily_run_on_a_later_instant_does_not_destroy_the_first` a test of the
append and not of two unrelated writes."""

MAX_STALENESS_DAYS: Final[int] = 7
"""The freshness bound every `factor build` here states.

Stated rather than waived because **`--waive-max-staleness` does not work on this path**, measured:
`factor build --factor reversal_1d/v1 --tier raw --waive-max-staleness` refuses with *"the daily
requirement waives max_staleness, and this engine reads through read_visible_at ... State a
bound"*. The flag's own help offers the waiver as one of two options and this engine accepts only
one of them; the refusal is right and the help text is what is behind."""

NO_FLOOR: Final[str] = "0.0"
"""The declared `--min-scored-ratio` for the runs that are meant to be admitted.

Zero is a statement, not a way of making a bar go away: the scored ratio here is a sixty-name
factor over a five-thousand-name cross section, so it is around 0.01 whatever the model does, and
`the_scored_ratio_floor_is_a_coverage_bar_and_never_a_quality_one` is the limitation that says a
number cleared here means nothing about quality. The bar is driven in the other direction by
`test_a_narrow_universe_cannot_clear_a_floor_the_whole_market_sets`."""

UNREACHABLE_FLOOR: Final[str] = "0.5"
"""A floor a cross section this narrow cannot clear, for the refusal half of the pair."""

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
"""Declared on every invocation rather than resolved, because both reach the artifact's address
and the prediction's. `resolve_code_commit` reads git -- and appends a literal `-dirty` when the
workspace has uncommitted changes -- so leaving it out would make "the same question re-asked
produces the same address" a claim about the checkout the suite happened to run from."""

UNHELD_PREDICTION: Final[str] = "prd_000000000000000000000000"
"""A well-formed `record_id` no run produced. The wrong-answer control for retrieval."""

EXIT_OK: Final[int] = 0
EXIT_UNHEALTHY: Final[int] = 1
"""The two rows of `cli.MODEL_EXIT` this module observes, spelled out rather than imported so that
a test asserting `1` is asserting the contract a scheduled job switches on."""

HTTP_OK: Final[int] = 200
HTTP_NOT_HELD: Final[int] = 404


# --- the panel, the sessions it agrees with itself about, and the window they leave -------------


def _stamped(session: date) -> datetime:
    """The instant a cross section about `session` is stamped at. See `BUILD_HOUR_AFTER_CLOSE`."""
    return (
        datetime.combine(session, DAILY_AVAILABILITY_TIME, tzinfo=PANEL_ZONE)
        + BUILD_HOUR_AFTER_CLOSE
    )


def _contested_sessions(store: PanelStore, *, year: int) -> frozenset[date]:
    """Every stored session on which some security's two price datasets disagree about it.

    `daily` publishes `pre_close` and `adj_factor` publishes the corporate action behind it, and
    `session_returns` refuses a pair whose implied and published `pre_close` differ by more than
    the two rows' own publication precision allows. A label window containing such a session
    cannot be priced, and `model evaluate` refuses the whole run rather than the one security --
    so which sessions these are decides which windows this module may evaluate at all.

    Read straight off the partitions with DuckDB, `_most_traded`'s idiom next door, and scored
    with the repository's **own** `pre_close_tolerance` rather than a bound restated here: a
    re-derivation that drifted would silently choose windows the chain still refuses.

    `adj_factor` is stored only where the factor changes, so the factor in force on a day is the
    latest one dated at or before it -- an ASOF join, which is `AdjustmentHistory.factor_on`'s
    step function expressed in SQL.
    """
    daily = catalogued_path(store, dataset=DAILY_DATASET, year=year)
    adjustments = catalogued_path(store, dataset=ADJ_FACTOR_DATASET, year=year)
    with duckdb.connect() as reader:
        rows = reader.execute(
            "WITH factors AS (SELECT subject, factor_date, adj_factor FROM read_parquet(?)), "
            "bars AS (SELECT subject, trade_date, pre_close, "
            "  LAG(close) OVER (PARTITION BY subject ORDER BY trade_date) AS previous_close, "
            "  LAG(trade_date) OVER (PARTITION BY subject ORDER BY trade_date) AS previous_day "
            "  FROM read_parquet(?)), "
            "pairs AS (SELECT * FROM bars WHERE previous_day IS NOT NULL "
            "  AND previous_close IS NOT NULL AND pre_close IS NOT NULL), "
            "on_the_day AS (SELECT p.*, f.adj_factor AS factor FROM pairs p "
            "  ASOF LEFT JOIN factors f "
            "  ON p.subject = f.subject AND p.trade_date >= f.factor_date) "
            "SELECT d.trade_date, d.pre_close, d.previous_close, d.factor, "
            "       f.adj_factor AS previous_factor "
            "FROM on_the_day d ASOF LEFT JOIN factors f "
            "  ON d.subject = f.subject AND d.previous_day >= f.factor_date",
            [str(adjustments), str(daily)],
        ).fetchall()
    contested: set[date] = set()
    for day, pre_close, previous_close, factor, previous_factor in rows:
        if not factor or not previous_factor or not previous_close:
            continue
        implied = previous_close * previous_factor / factor
        tolerance = pre_close_tolerance(implied, factor=factor, previous_factor=previous_factor)
        if abs(implied - pre_close) > tolerance:
            contested.add(date.fromisoformat(str(day)))
    return frozenset(contested)


def _label_span(sessions: Sequence[date], index: int, *, horizon: int) -> tuple[int, int] | None:
    """The half-open span of session indices one prediction day's label reads, or `None` when the
    panel does not reach the end of it.

    `[index, index + horizon + 1]` inclusive rather than `[index + 1, ...]`: the label runs from
    the session *after* the prediction day to `horizon` sessions past that, and the return on its
    first session is computed against the close of the one before it -- which is the prediction
    day itself. Keeping the prediction day inside the span is what makes a window chosen here
    safe rather than nearly safe.
    """
    last = index + horizon + 1
    return None if last >= len(sessions) else (index, last)


def _clean_run(
    sessions: Sequence[date], contested: frozenset[date], *, horizon: int
) -> tuple[int, ...]:
    """The longest run of consecutive prediction days whose whole label span is uncontested.

    Returned as indices into `sessions` so that a caller can ask what follows the run, which is
    what `_contested_horizon` needs.
    """
    usable: list[int] = []
    for index in range(len(sessions)):
        span = _label_span(sessions, index, horizon=horizon)
        if span is None:
            break
        first, last = span
        if any(sessions[step] in contested for step in range(first, last + 1)):
            continue
        usable.append(index)
    runs: list[list[int]] = []
    current: list[int] = []
    for index in usable:
        if current and index != current[-1] + 1:
            runs.append(current)
            current = []
        current.append(index)
    if current:
        runs.append(current)
    if not runs:
        raise E2EEnvironmentError(
            f"every prediction day this panel stores has a {horizon}-session label span "
            f"containing one of the {len(contested)} session(s) whose price datasets disagree "
            "about a corporate action; no window can be evaluated at all"
        )
    return tuple(max(runs, key=len))


def _contested_horizon(
    sessions: Sequence[date], window: Sequence[int], contested: frozenset[date]
) -> int:
    """The shortest horizon at which `window`'s own label spans reach a contested session.

    The point of asking is cost: a refusal test that chose a *different* window would need its own
    twenty `factor build` instants, and lengthening the horizon over the window already built
    reaches the same refusal for nothing. It is also the more honest probe -- "the same twenty days
    a longer outcome" is a thing a reader would actually try next.
    """
    for horizon in range(HORIZON_SESSIONS + 1, len(sessions)):
        if len(window) < FOLDS * TEST_DAYS_PER_FOLD + horizon + 1:
            break
        spans = (_label_span(sessions, index, horizon=horizon) for index in window)
        for span in spans:
            if span is None:
                continue
            first, last = span
            if any(sessions[step] in contested for step in range(first, last + 1)):
                return horizon
    raise E2EEnvironmentError(
        "no horizon this window can still carry a schedule at reaches a session whose price "
        "datasets disagree; the refusal this panel produced when the module was written is not "
        "reproducible on it"
    )


def _most_traded(store: PanelStore, *, year: int, session: date, count: int) -> tuple[str, ...]:
    """The `count` securities with the largest turnover on `session`, descending.

    `amount` is a column no reader in this repository reassembles -- `load_daily_bars` answers
    `MarketBar`, which carries five prices and two flags and no turnover -- so a name's place in
    this universe cannot be an artefact of anything the chain under test computes.
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
            f"builds {count}"
        )
    return universe


def _link_panel(source: Path, destination: Path) -> None:
    """Copy one built panel's tree, hard-linking every file but the catalog.

    The catalog is a DuckDB database `write_panel_batch` opens for writing, so it is the one file
    that must be independent; the Parquet partitions are replaced by rename when they are written
    at all, so a link cannot be written through. See this module's "Cost".
    """

    def _each(origin: str, target: str) -> None:
        if origin.endswith(".duckdb"):
            shutil.copy2(origin, target)
        else:
            os.link(origin, target)

    shutil.copytree(source, destination, copy_function=_each)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelPanel:
    """One private view of the built panel, and everything this module derived from it."""

    runtime_dir: Path
    workspace: Path
    year: int
    exchange: str
    sessions: tuple[date, ...]
    contested: frozenset[date]
    """Every stored session the two price datasets disagree about. See `_contested_sessions`."""

    window: tuple[date, ...]
    """The prediction days the evaluation reads, ascending: `PREDICTION_DAYS` consecutive
    sessions whose label spans are all uncontested."""

    contested_horizon: int
    """The shortest horizon at which `window`'s own spans reach a contested session."""

    universe: tuple[str, ...]
    prediction_session: date
    """The newest session this panel stores -- the one a daily run predicts about."""

    prediction_instants: tuple[datetime, datetime]
    """The two instants on `prediction_session`'s evening, earlier first."""

    label_as_of: datetime
    """The instant every evaluation and daily run below reads its labels at.

    After the newest stored session's close, because the outcomes behind the training range are
    read here and a partition may not be read before it became knowable."""

    @property
    def store(self) -> PanelStore:
        return panel_store(self.runtime_dir)

    @property
    def start(self) -> str:
        return self.window[0].isoformat()

    @property
    def end(self) -> str:
        return self.window[-1].isoformat()


def _model_panel(built_panel: BuiltPanel, root: Path, workspace: Path) -> ModelPanel:
    """Derive the window, build the factor tier over it, and hand back what the tests read."""
    _link_panel(built_panel.runtime_dir / "panel", root / "panel")
    workspace.mkdir(parents=True, exist_ok=True)
    store = panel_store(root)
    year = built_panel.year
    sessions = stored_sessions(store, dataset=DAILY_DATASET, year=year)
    if len(sessions) < _MINIMUM_PREDICTION_DAYS + HORIZON_SESSIONS + 1:
        raise E2EEnvironmentError(
            f"the {DAILY_DATASET} partition for {year} holds {len(sessions)} session(s); a "
            f"schedule of {FOLDS} fold(s) of {TEST_DAYS_PER_FOLD} test day(s) at a "
            f"{HORIZON} horizon needs at least {_MINIMUM_PREDICTION_DAYS} prediction days and a "
            "label span past the last of them"
        )
    contested = _contested_sessions(store, year=year)
    run = _clean_run(sessions, contested, horizon=HORIZON_SESSIONS)
    if len(run) < PREDICTION_DAYS:
        raise E2EEnvironmentError(
            f"the longest run of consecutive prediction days whose {HORIZON} label spans avoid "
            f"all {len(contested)} contested session(s) is {len(run)} day(s); this module reads "
            f"{PREDICTION_DAYS}"
        )
    chosen = run[-PREDICTION_DAYS:]
    window = tuple(sessions[index] for index in chosen)
    prediction_session = sessions[-1]
    universe = _most_traded(store, year=year, session=prediction_session, count=UNIVERSE_SIZE)
    instants = (
        _stamped(prediction_session),
        datetime.combine(prediction_session, SECOND_INSTANT_TIME, tzinfo=PANEL_ZONE),
    )
    build = run_cli(
        "factor",
        "build",
        "--factor",
        FACTOR,
        "--tier",
        "raw",
        "--year",
        str(year),
        "--exchange",
        built_panel.exchange,
        "--max-staleness-days",
        str(MAX_STALENESS_DAYS),
        "--runtime-dir",
        str(root),
        "--code-commit",
        COMMIT,
        "--json",
        *(arg for day in window for arg in ("--as-of", _stamped(day).isoformat())),
        *(arg for instant in instants for arg in ("--as-of", instant.isoformat())),
        *(arg for subject in universe for arg in ("--subject", subject)),
        cwd=workspace,
    )
    if build.exit_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"`openalpha factor build` over {len(window) + len(instants)} instant(s) exited "
            f"{build.exit_code}: {build.stderr[:1500]}"
        )
    return ModelPanel(
        runtime_dir=root,
        workspace=workspace,
        year=year,
        exchange=built_panel.exchange,
        sessions=sessions,
        contested=contested,
        window=window,
        contested_horizon=_contested_horizon(sessions, chosen, contested),
        universe=universe,
        prediction_session=prediction_session,
        prediction_instants=instants,
        label_as_of=instants[1] + timedelta(hours=1),
    )


@pytest.fixture(scope="module")
def model_panel(built_panel: BuiltPanel, tmp_path_factory: pytest.TempPathFactory) -> ModelPanel:
    """The view every test below reads, with the factor tier built into it once."""
    return _model_panel(
        built_panel,
        tmp_path_factory.mktemp("model-online"),
        tmp_path_factory.mktemp("model-online-cwd"),
    )


# --- the three answers the whole module reads ---------------------------------------------------


def _evaluate(
    model_panel: ModelPanel, *, floor: str, horizon: str = HORIZON, cwd: Path | None = None
) -> CLIResult:
    """One `openalpha model evaluate` over the derived window."""
    return run_cli(
        "model",
        "evaluate",
        "--feature",
        FEATURE,
        "--name",
        MODEL_NAME,
        "--family",
        MODEL_FAMILY,
        "--horizon",
        horizon,
        "--seed",
        str(SEED),
        "--start",
        model_panel.start,
        "--end",
        model_panel.end,
        "--year",
        str(model_panel.year),
        "--exchange",
        model_panel.exchange,
        "--folds",
        str(FOLDS),
        "--test-days-per-fold",
        str(TEST_DAYS_PER_FOLD),
        "--embargo-sessions",
        str(EMBARGO_SESSIONS),
        "--min-scored-ratio",
        floor,
        "--as-of",
        model_panel.label_as_of.isoformat(),
        "--code-commit",
        COMMIT,
        "--config-digest",
        CONFIG_DIGEST,
        "--runtime-dir",
        str(model_panel.runtime_dir),
        "--json",
        cwd=cwd if cwd is not None else model_panel.workspace,
    )


def _daily_run(
    model_panel: ModelPanel, *, predict_at: datetime, floor: str = NO_FLOOR, cwd: Path | None = None
) -> CLIResult:
    """One `openalpha model daily-run` about `predict_at`."""
    return run_cli(
        "model",
        "daily-run",
        "--feature",
        FEATURE,
        "--name",
        MODEL_NAME,
        "--family",
        MODEL_FAMILY,
        "--horizon",
        HORIZON,
        "--seed",
        str(SEED),
        "--start",
        model_panel.start,
        "--end",
        model_panel.end,
        "--year",
        str(model_panel.year),
        "--exchange",
        model_panel.exchange,
        "--predict-at",
        predict_at.isoformat(),
        "--as-of",
        max(model_panel.label_as_of, predict_at).isoformat(),
        "--min-scored-ratio",
        floor,
        "--code-commit",
        COMMIT,
        "--config-digest",
        CONFIG_DIGEST,
        "--runtime-dir",
        str(model_panel.runtime_dir),
        "--json",
        cwd=cwd if cwd is not None else model_panel.workspace,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Fitted:
    """The one evaluation and the two daily runs every assertion below reads.

    A record rather than three fixtures because they are made together and cost minutes; see
    this module's "Cost"."""

    evaluation: Mapping[str, Any]
    first: Mapping[str, Any]
    second: Mapping[str, Any]
    held_after_the_evaluation: tuple[str, ...]
    """Every address the prediction store held after the evaluation and before the first daily
    run. Captured in the fixture because it is only observable in that gap."""

    @property
    def record_ids(self) -> tuple[str, str]:
        return (
            str(self.first["prediction"]["record_id"]),
            str(self.second["prediction"]["record_id"]),
        )


@pytest.fixture(scope="module")
def fitted(model_panel: ModelPanel) -> Fitted:
    """Run the schedule once and register both predictions once.

    The two daily runs are in **order**, earlier instant first, because the second one's whole
    job is to land beside a store that already holds the first.
    """
    evaluation = _evaluate(model_panel, floor=NO_FLOOR)
    if evaluation.exit_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"`openalpha model evaluate` exited {evaluation.exit_code}: {evaluation.stderr[:1500]}"
        )
    listed = run_cli(
        "model",
        "predictions",
        "--runtime-dir",
        str(model_panel.runtime_dir),
        "--json",
        cwd=model_panel.workspace,
    )
    if listed.exit_code != EXIT_OK:
        raise E2EEnvironmentError(
            f"`openalpha model predictions` exited {listed.exit_code}: {listed.stderr[:1500]}"
        )
    runs = []
    for instant in model_panel.prediction_instants:
        answer = _daily_run(model_panel, predict_at=instant)
        if answer.exit_code != EXIT_OK:
            raise E2EEnvironmentError(
                f"`openalpha model daily-run` about {instant.isoformat()} exited "
                f"{answer.exit_code}: {answer.stderr[:1500]}"
            )
        runs.append(answer.payload())
    return Fitted(
        evaluation=evaluation.payload(),
        first=runs[0],
        second=runs[1],
        held_after_the_evaluation=tuple(listed.payload()["record_ids"]),
    )


@pytest.fixture(scope="module")
def serve_runtime_dir(model_panel: ModelPanel) -> Path:
    """Point `conftest.py`'s `served` at this module's private view rather than the shared panel,
    which is the seam that fixture takes its directory from."""
    return model_panel.runtime_dir


# --- the walk-forward evaluation ---------------------------------------------------------------


def test_the_chain_reaches_per_fold_statistics_over_a_window_the_market_agrees_about(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """`panel build` -> `factor build` -> `model evaluate` reaches a measured statistic per fold.

    The headline this module was written to produce, and the thing no test in this repository had
    ever produced on real rows. Every fold is asserted `measured` rather than one of them, because
    `insufficient_as_ofs` is what a fold answers when its test block lost days to abstentions and
    a run where only the first fold measured would be a weaker claim quietly passing.
    """
    evaluation = fitted.evaluation
    schedule = evaluation["schedule"]
    assert schedule["folds"] == FOLDS
    assert schedule["test_days_per_fold"] == TEST_DAYS_PER_FOLD
    assert schedule["embargo_sessions"] == EMBARGO_SESSIONS
    assert len(schedule["prediction_days"]) == PREDICTION_DAYS
    assert schedule["prediction_days"] == [day.isoformat() for day in model_panel.window]

    assert evaluation["is_blocked"] is False
    assert evaluation["blocks"] == []
    assert evaluation["measurement"]["fold_count"] == FOLDS
    assert evaluation["measurement"]["measured_fold_count"] == FOLDS

    folds = evaluation["folds"]
    assert len(folds) == FOLDS
    for fold in folds:
        assert fold["coverage"] == "measured"
        assert fold["test_day_count"] == TEST_DAYS_PER_FOLD
        assert isinstance(fold["mean_rank_ic"], float)
        assert isinstance(fold["stdev_rank_ic"], float)
        assert fold["training_example_count"] > 0
        assert fold["artifact_id"].startswith("mdl_")

    admitted = evaluation["admitted"]
    assert admitted is not None
    assert [fold["artifact_id"] for fold in folds] == list(admitted)


def test_every_fold_was_fitted_on_examples_the_purge_and_the_embargo_left_behind(
    fitted: Fitted,
) -> None:
    """A later fold trains on at least as much as an earlier one, and none trains on nothing.

    The property walk-forward exists for, read off the answer: each fold's training cutoff moves
    forward with its test block, so the set it may fit on only grows. A schedule that silently
    handed every fold the same examples -- or handed one of them none -- would pass every other
    assertion in this module.
    """
    folds = fitted.evaluation["folds"]
    cutoffs = [fold["training_cutoff"] for fold in folds]
    assert cutoffs == sorted(cutoffs)
    assert len(set(cutoffs)) == len(cutoffs)
    counts = [fold["training_example_count"] for fold in folds]
    assert all(count > 0 for count in counts)
    assert counts == sorted(counts)


def test_an_outcome_window_the_two_price_datasets_disagree_about_is_refused_by_name(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """The wrong-answer run for the headline: the same twenty days, an outcome long enough to
    reach a corporate action `daily` and `adj_factor` tell differently.

    **The one refusal here that only real data produces.** A generated panel's closes and
    adjustment factors are generated together and therefore agree by construction, which is why
    four rounds of offline acceptance never met this shape. What it proves about the test above is
    that its window was *chosen* -- lengthen the outcome by a session or two and the same command
    over the same partitions stops answering.

    Exit 1 and a message naming the security and both numbers, rather than a traceback: this is
    `panel_unreadable`, a statement about the stored corpus, and `V2-P4-084`'s separation of that
    from a `LabelError` about the window is what puts it on this row.
    """
    del fitted  # ordering only: the shared build must have happened before this spends a run.
    refused = _evaluate(model_panel, floor=NO_FLOOR, horizon=f"{model_panel.contested_horizon}d")
    assert refused.exit_code == EXIT_UNHEALTHY
    # `V2-P5-047`: this read `refused.stdout == ""` until `--json` started answering a refusal
    # with a document. `_evaluate` passes `--json`, so the document is what a machine caller
    # gets here; `status: refused` is what tells it apart from an answer, and the sentence it
    # carries is the same one stderr prints, asserted below.
    assert refused.payload()["status"] == "refused"
    assert refused.payload()["detail"] in refused.stderr
    message = refused.stderr
    assert "could not be priced out of" in message
    assert "the implied pre_close from" in message
    assert "disagree about that session's corporate action" in message
    assert str(model_panel.runtime_dir) not in message


def test_a_narrow_universe_cannot_clear_a_floor_the_whole_market_sets(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """The same admitted measurement, refused when the floor is raised.

    Two things at once. It drives `--min-scored-ratio` in the direction `NO_FLOOR` cannot, so the
    zero declared there is a statement rather than a bar quietly removed. And it measures this
    module's first finding from the other side: the ratio is `scored / offered` where *offered* is
    the whole listed registry, so sixty names cannot reach a half no matter how well they score --
    `factor build --subject` narrowed the build and did not narrow this.
    """
    refused = _evaluate(model_panel, floor=UNREACHABLE_FLOOR)
    assert refused.exit_code == EXIT_UNHEALTHY
    payload = refused.payload()
    assert payload["is_blocked"] is True
    assert payload["admitted"] is None
    assert [block["code"] for block in payload["blocks"]] == ["scored_ratio_below_floor"]
    block = payload["blocks"][0]
    assert block["required"] == float(UNREACHABLE_FLOOR)
    assert block["measured"] < block["required"]
    # The same run, the same numbers, and only the declared floor differs.
    assert (
        payload["measurement"]["scored_count"] == (fitted.evaluation["measurement"]["scored_count"])
    )
    assert (
        payload["measurement"]["offered_count"]
        == (fitted.evaluation["measurement"]["offered_count"])
    )


def test_an_evaluation_registers_no_prediction_and_says_so_in_its_own_limitations(
    fitted: Fitted,
) -> None:
    """`model evaluate` files nothing in the prediction store, and the body carries the reason.

    Every record an evaluation could write would be `unwitnessed` -- it is fitting over outcomes
    that already closed -- so it writes none. That is a named limitation rather than an omission,
    and asserting the code keeps the two in step: a future evaluation that started registering
    would fail here rather than quietly filling a store `V2-P4-017` built for something else.

    The store is measured in the gap between the evaluation and the first daily run, which is the
    only moment the claim is observable at all -- see `Fitted.held_after_the_evaluation`.
    """
    assert fitted.held_after_the_evaluation == ()
    codes = {limitation["code"] for limitation in fitted.evaluation["limitations"]}
    assert (
        "an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed"
        in codes
    )
    assert "the_scored_ratio_floor_is_a_coverage_bar_and_never_a_quality_one" in codes
    assert "an_evaluation_writes_no_run_manifest_because_it_took_no_decision" in codes


# --- the daily run, its address, and the three faces that serve it ------------------------------


def test_a_daily_run_registers_a_prediction_and_files_a_manifest_naming_its_artifact(
    fitted: Fitted,
) -> None:
    """The command Story S32 is about, on real rows: an answer stored before its outcome is known.

    `run_id` is derived from the prediction's own address rather than drawn independently, which
    is what makes an identical day `unchanged` on both stores instead of a duplicate on one of
    them -- asserted directly, because it is the join between the prediction store and the run
    store and nothing else on the body would notice if it came apart.
    """
    first = fitted.first
    prediction = first["prediction"]
    record_id = prediction["record_id"]
    assert record_id.startswith("prd_")
    assert first["write_outcome"] == "created"
    assert first["run_outcome"] == "created"
    assert first["run_id"] == f"daily-{record_id}"
    assert first["run_manifest_id"].startswith("run_")
    assert first["is_blocked"] is False

    artifact_id = prediction["artifact_id"]
    assert artifact_id.startswith("mdl_")
    assert [slot["artifact_id"] for slot in first["alpha_model_versions"]] == [artifact_id]
    assert [slot["name"] for slot in first["alpha_model_versions"]] == [MODEL_NAME]
    assert prediction["scored_count"] > 0
    assert prediction["offered_count"] >= prediction["scored_count"]


def test_one_registered_prediction_is_one_document_through_all_three_faces(
    model_panel: ModelPanel, fitted: Fitted, served: str
) -> None:
    """`openalpha model prediction`, `GET /api/v1/predictions/{record_id}` and
    `OpenAlphaSDK.held_prediction` hand out the one document the daily run registered.

    The command line, a real `openalpha serve` child process and the Python API over one store.
    Asserted as whole-body equality rather than field by field, so a key that appears on one face
    and not another is a failure here rather than a difference nobody measured.
    """
    record_id, _ = fitted.record_ids

    printed = run_cli(
        "model",
        "prediction",
        record_id,
        "--runtime-dir",
        str(model_panel.runtime_dir),
        cwd=model_panel.workspace,
    )
    assert printed.exit_code == EXIT_OK
    over_the_command_line = printed.payload()

    status, over_http = http_get(served, f"/api/v1/predictions/{record_id}", ())
    assert status == HTTP_OK

    sdk = OpenAlphaSDK(runtime_dir=model_panel.runtime_dir)
    held = sdk.held_prediction(record_id)
    assert isinstance(held, PredictionRecord)

    assert over_the_command_line == over_http
    assert over_the_command_line == fitted.first["prediction"]
    assert held.record_id == record_id
    assert over_http["standing"] == held.standing


def test_an_address_nothing_is_held_under_is_a_named_refusal_and_not_an_empty_document(
    model_panel: ModelPanel, fitted: Fitted, served: str
) -> None:
    """The wrong-answer control for retrieval: ask for a `record_id` no run ever produced.

    A well-formed address, so this is `not_held` and not `bad_request` -- exit 1, `404`, and an
    SDK that raises rather than answering an empty record. The three faces have to agree about
    absence as well as about presence, and a store that answered `{}` would satisfy every
    assertion in the test above.
    """
    del fitted  # ordering only: the store must hold something for "not this one" to mean anything.
    refused = run_cli(
        "model",
        "prediction",
        UNHELD_PREDICTION,
        "--runtime-dir",
        str(model_panel.runtime_dir),
        cwd=model_panel.workspace,
    )
    assert refused.exit_code == EXIT_UNHEALTHY
    assert "no prediction is held under" in refused.stderr
    assert str(model_panel.runtime_dir) not in refused.stderr

    status, body = http_get(served, f"/api/v1/predictions/{UNHELD_PREDICTION}", ())
    assert status == HTTP_NOT_HELD
    assert body["detail"]["reason"] == "not_held"

    sdk = OpenAlphaSDK(runtime_dir=model_panel.runtime_dir)
    with pytest.raises(ModelNotHeldError):
        sdk.held_prediction(UNHELD_PREDICTION)


def test_a_second_daily_run_on_a_later_instant_does_not_destroy_the_first(
    model_panel: ModelPanel, fitted: Fitted, served: str
) -> None:
    """`V2-P4-071`'s append path, on real data: two instants, two addresses, both still held.

    Both runs are about the same session and differ only in the instant they were asked at, which
    is the strictest form of the question -- a store keyed on anything coarser than the instant
    would collide them. The first record is re-fetched **after** the second was written, through
    the HTTP face, so this is a statement about what survived rather than about what was returned
    at the time.
    """
    first_id, second_id = fitted.record_ids
    assert first_id != second_id
    assert fitted.second["write_outcome"] == "created"
    assert fitted.second["run_outcome"] == "created"

    earlier, later = model_panel.prediction_instants
    assert earlier < later
    assert fitted.first["prediction"]["as_of"] != fitted.second["prediction"]["as_of"]

    listed = run_cli(
        "model",
        "predictions",
        "--runtime-dir",
        str(model_panel.runtime_dir),
        "--json",
        cwd=model_panel.workspace,
    )
    assert listed.exit_code == EXIT_OK
    payload = listed.payload()
    held = payload["record_ids"]
    assert first_id in held
    assert second_id in held
    # Custody order since `V2-P4-098`, not the digest order this line used to assert. The two
    # runs were filed in this process's own order, so the earlier one lists first -- which is the
    # question a register is read for and the one a `sorted()` over content addresses answered
    # with a shuffle.
    assert held.index(first_id) < held.index(second_id)
    assert [row["record_id"] for row in payload["predictions"]] == held
    assert {row["model_name"] for row in payload["predictions"]} == {MODEL_NAME}

    status, survivor = http_get(served, f"/api/v1/predictions/{first_id}", ())
    assert status == HTTP_OK
    # `held_prediction_view` is `prediction_view` plus the registry (`V2-P4-098`): this route and
    # `openalpha model prediction` hand out a record with no run's answer beside it, so they carry
    # the boundaries the daily run's own body carried.
    assert survivor == {**fitted.first["prediction"], "limitations": fitted.first["limitations"]}
    model = survivor["model"]
    assert model["feature_ids"] == [f"{FACTOR}@raw"]
    assert model["code_commit"] and model["feature_version"].startswith("feat_")


def test_re_asking_one_day_on_the_command_line_files_a_second_record_rather_than_unchanged(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """The identical day, re-asked through the same command, is a **new** address.

    **This contradicted `openalpha model daily-run --help`**, which said re-running an identical
    day is *"`unchanged` on both stores rather than a duplicate on one of them"*. It is not, and
    cannot be, through this face: `predicted_at` reaches the record's content address
    (`prediction_record.py`'s "and `predicted_at` reaches the address"), and the CLI takes it from
    **this process's clock**, which is a different instant on every invocation. So the address is
    different, `put` lands beside rather than on top, and a daily job re-run after a transient
    failure files a second record for one day rather than recognising the first.

    The behaviour is right for what the store is -- a content-addressed document is what its bytes
    say -- and the help text was what was behind it. `V2-P4-100` corrected the sentence and filed
    the consequence as
    `model_view.KNOWN_MODEL_VIEW_LIMITATIONS`'
    `a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_address`, which
    carries the argument against each of the three repairs that look obvious -- taking
    `predicted_at` out of the address, offering a flag to set it, and scanning the register before
    every write. What is genuinely unreachable from the command line is the `unchanged` path, and
    the test below reaches it the one way the SDK documents.
    """
    again = _daily_run(model_panel, predict_at=model_panel.prediction_instants[0])
    assert again.exit_code == EXIT_OK
    payload = again.payload()
    assert payload["write_outcome"] == "created"
    assert payload["prediction"]["record_id"] != fitted.record_ids[0]
    # Everything the declaration decides is identical; only the two clocks moved.
    assert payload["prediction"]["as_of"] == fitted.first["prediction"]["as_of"]
    assert payload["prediction"]["artifact_id"] == fitted.first["prediction"]["artifact_id"]
    assert payload["prediction"]["predicted_at"] != fitted.first["prediction"]["predicted_at"]


def test_the_unchanged_answer_is_reachable_when_the_clock_the_address_reads_is_pinned(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """The control the test above cannot be: one day re-asked at one instant is one document.

    `OpenAlphaSDK`'s own remedy -- *"a caller who wants to drive the three standings constructs
    the SDK with the clock it wants"* -- pinned to the instant the command line's first run
    recorded. Two things follow, and both are load-bearing.

    The store really is content-addressed rather than append-on-every-call: with `predicted_at`
    held still the second write is `unchanged` and hands back the document already held, so
    `test_a_second_daily_run_on_a_later_instant_does_not_destroy_the_first` is a statement about
    two *different* questions and not about a store that never collides anything.

    And the SDK reproduces an address the **command line** filed, from the same declaration over
    the same panel -- which is the strongest form of "one question has one answer" this chain can
    be asked, because the two faces share no process.
    """
    first_id, _ = fitted.record_ids
    predicted_at = datetime.fromisoformat(fitted.first["prediction"]["predicted_at"])
    sdk = OpenAlphaSDK(runtime_dir=model_panel.runtime_dir, clock=lambda: predicted_at)
    instant = model_panel.prediction_instants[0]
    result = sdk.run_daily_model(
        features=[{"factor": FACTOR, "tier": "raw"}],
        name=MODEL_NAME,
        family=MODEL_FAMILY,
        horizon=HORIZON,
        seed=SEED,
        start=model_panel.window[0],
        end=model_panel.window[-1],
        predict_at=instant,
        as_of=max(model_panel.label_as_of, instant),
        years=[model_panel.year],
        exchange=model_panel.exchange,
        minimum_scored_ratio=float(NO_FLOOR),
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
    )
    view = sdk.daily_view(result)
    assert view["write_outcome"] == "unchanged"
    assert view["prediction"]["record_id"] == first_id
    assert view["prediction"] == fitted.first["prediction"]


# --- what `standing` says, and what it does not -------------------------------------------------


def test_a_registered_prediction_carries_the_standing_its_own_clocks_imply(
    fitted: Fitted,
) -> None:
    """`standing` recomputed from the record's own three instants, against the badge it carries.

    The rule is `PredictionRecord.standing`'s, restated here rather than imported so that this is
    a check on the contract and not a second call to the same expression. It holds on a panel of
    any age, which is what lets this module assert something about `standing` unconditionally --
    see the module docstring, and the test below for the `forward` badge specifically.
    """
    for answer in (fitted.first, fitted.second):
        prediction = answer["prediction"]
        predicted_at = datetime.fromisoformat(prediction["predicted_at"])
        recorded_at = datetime.fromisoformat(prediction["recorded_at"])
        outcome_known_at = datetime.fromisoformat(prediction["outcome_known_at"])
        if predicted_at >= outcome_known_at:
            expected = "backfill"
        else:
            expected = "forward" if recorded_at < outcome_known_at else "unwitnessed"
        assert prediction["standing"] == expected
        # The badge never travels without the two sentences that bound it.
        assert prediction["standing_proves"]
        assert prediction["standing_does_not_prove"]
        assert prediction["supersedes"] is None


def test_a_prediction_about_an_outcome_the_world_has_not_reached_yet_stands_forward(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """The `forward` badge itself, on a real prediction about the panel's newest session.

    `forward` means this store held the bytes before the outcome became knowable, and it is the
    standing the product exists to be able to claim. It is reachable only while the panel is
    younger than one horizon: the outcome of a prediction about the newest stored session becomes
    knowable `HORIZON_SESSIONS + 1` sessions later, and a panel reused past that instant honestly
    answers `unwitnessed`. The `E2EEnvironmentError` names the remedy rather than skipping,
    because a silent skip in an opt-in subtree is the failure mode this suite exists to avoid.
    """
    prediction = fitted.first["prediction"]
    outcome_known_at = datetime.fromisoformat(prediction["outcome_known_at"])
    recorded_at = datetime.fromisoformat(prediction["recorded_at"])
    if recorded_at >= outcome_known_at:
        raise E2EEnvironmentError(
            f"this panel's newest session is {model_panel.prediction_session.isoformat()}, so a "
            f"{HORIZON} outcome about it became knowable at {outcome_known_at.isoformat()}, and "
            f"this run recorded at {recorded_at.isoformat()} -- after it. The panel is older "
            "than one horizon and cannot demonstrate `forward`; rebuild it by unsetting "
            "OPENALPHA_E2E_RUNTIME_DIR"
        )
    assert prediction["standing"] == "forward"
    assert datetime.fromisoformat(prediction["predicted_at"]) < outcome_known_at
    assert "before the instant the outcome became knowable" in prediction["standing_proves"]
    assert "predicted_at" in prediction["standing_does_not_prove"]


def test_a_daily_run_about_a_day_the_panel_does_not_reach_is_refused_by_name(
    model_panel: ModelPanel, fitted: Fitted
) -> None:
    """A prediction instant past the newest session this panel stores, refused rather than served.

    `V2-P4-088`'s territory approached from real data, and what it finds is that the refusal a
    year-end run actually meets on a panel built mid-year is **not** the calendar-horizon one. The
    stored `trade_cal` reaches the end of the year, so the calendar can size the outcome window
    perfectly well; what cannot be done is *reading the price plane* for sessions the build never
    fetched. `date_gap` gets there first, and it names the first absent session.

    What `V2-P4-088` claimed and what holds here is the part that matters: this is exit 1 with a
    sentence a scheduled job can switch on, not a 500 and not a traceback.
    """
    del fitted  # ordering only.
    beyond = model_panel.sessions[-1] + timedelta(days=120)
    refused = _daily_run(
        model_panel,
        predict_at=datetime.combine(beyond, SECOND_INSTANT_TIME, tzinfo=PANEL_ZONE),
    )
    assert refused.exit_code == EXIT_UNHEALTHY
    # `V2-P5-047`, as in `_evaluate`'s refusal above: `--json` now answers with a document.
    assert refused.payload()["status"] == "refused"
    assert refused.payload()["detail"] in refused.stderr
    assert "date_gap" in refused.stderr
    assert "absent from" in refused.stderr
    # `V2-P5-045`: the `date_gap` names the clock that decided the required dates.
    assert "--as-of" in refused.stderr
    assert str(model_panel.runtime_dir) not in refused.stderr
    assert str(model_panel.runtime_dir) not in refused.stdout


def test_the_answer_carries_every_limitation_this_face_declares(fitted: Fitted) -> None:
    """Both faces emit the whole of `KNOWN_MODEL_VIEW_LIMITATIONS`, unconditionally.

    Nine codes, on the evaluation and on the daily run alike, whether or not the run was admitted.
    A face that emitted only the limitations it thought applied would be deciding for the reader
    which caveats are relevant to them, and the two bodies would drift apart.
    """
    for body in (fitted.evaluation, fitted.first):
        codes = {limitation["code"] for limitation in body["limitations"]}
        assert codes == set(MODEL_VIEW_LIMITATION_CODES)
        assert all(limitation["detail"] for limitation in body["limitations"])
