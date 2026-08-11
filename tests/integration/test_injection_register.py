"""The P2 injection register, and the regression gate it is (`V2-P2-009`).

`V2-P2-001`..`008` each inject something a point-in-time contract must refuse. Until this file
they were eight separate arguments in seven files, and the only thing resembling a harness was
`tests/integration/test_research_cycle.py`'s single hand-written vector -- one `Evidence`
dated after the request's `as_of`, driven through a bare `try/except ValueError/else` whose
match was the substring `"not visible"`. That vector is **row one of this table now** and has
been removed from where it was; the roadmap asks for exactly that replacement.

## The table is data, and the data is what runs

Every row carries the two callables that perform its own halves. There is no dispatch to
forget: `test_the_injected_half_is_refused_and_names_its_own_rule` and
`test_the_same_read_without_the_injection_answers` are parametrized over the whole tuple, so a
row added without an implementation is a row whose `inject` does not refuse and whose test goes
red. That is the shape `V2-P1-015`'s review named -- a table and an implementation drifting
apart with nothing failing -- inverted, so that the table *is* the call.

Four audits sit on top of it, and each closes a different way a register rots:

- `test_no_vectors_refusal_matches_another_vectors_refusal` runs every injection and
  cross-matches every declared pattern against every *other* vector's actual refusal text. A
  pattern loose enough to match a neighbour's refusal is a pattern that does not prove which
  rule refused, which is this repository's `pytest.raises(match=...)` rule made mechanical.
- `test_every_p2_injection_issue_is_a_vector_or_a_disclosed_exclusion` requires all eight
  issues to be accounted for, and
  `test_each_disclosed_exclusion_names_a_test_that_exists` AST-parses the file each exclusion
  points at, so an exclusion whose evidence was deleted or renamed goes red instead of
  continuing to excuse the issue.
- `test_every_vector_is_driven_by_this_modules_own_parametrized_harness` parses this module and
  requires both direction tests to be parametrized over `INJECTION_VECTORS` itself, so a later
  refactor to a hand-written dispatch cannot quietly drop rows.

## The gate

Not a new job and not a new marker. `pyproject.toml`'s `addopts` deselects exactly one marker
(`e2e`), this module declares none, and the `python` job runs `uv run pytest` on four matrix
cells of every push and pull request -- so the register is already unskippable, and
`test_the_register_is_a_regression_gate_the_default_run_cannot_skip` is what holds those three
facts together. A separate marker would make the register *more* skippable, not less, which is
the opposite of what "all of 001-008 pass" being a P3/P4 precondition needs.

## What a row is not

It is not a replacement for the issue's own tests. `test_lookahead_injection.py` pairs each
look-ahead injection against **the same partition with exactly those rows pruned**, which is a
finer instrument than this one: a row here pairs a panel built with a shape against a panel
built without it. That pairing is sound because the generator is additive by construction --
`tests/unit/test_panel_fixtures.py` asserts every detector answers `False` on the shapeless
panel and `True` on its own shape -- but it is coarser, and this file says so rather than
claiming to subsume the eight.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    AS_OF,
    BASE_ANNOUNCEMENT,
    BASE_PERIOD,
    EXCHANGE,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from pydantic import ValidationError

from openalpha_cn.domain.daily_prices import (
    DAILY_DATASET,
    PriceDataError,
    SessionReturns,
    session_returns,
)
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.financial_statements import (
    INCOME_DATASET,
    AmbiguousReportError,
    StatementHistory,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET, IndexMembership
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    SecurityIndustryHistory,
)
from openalpha_cn.domain.labels import (
    LabelError,
    LabelSample,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
    overlapping_windows,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.time import Timeline
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendar
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_index_membership,
    load_industry_histories,
    load_price_limits,
    load_statement_histories,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
)
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

ROOT = Path(__file__).resolve().parents[2]
SHANGHAI = ZoneInfo("Asia/Shanghai")
DISPUTED_COLUMN = "total_revenue"
"""`income`'s first stored column, which every generated statement disagreement rides on."""

P2_INJECTION_ISSUES = tuple(f"V2-P2-{number:03d}" for number in range(1, 9))
"""The eight issues `V2-P2-009` is the roadmap's collector for. `009` is itself this file."""


# --- the panel plane's shared plumbing ---------------------------------------------------------


def _stored(root: Path, panel: GeneratedPanel, *datasets: str) -> PanelStore:
    """One panel, or a named subset of it, through the real writers into a fresh store."""
    store = PanelStore(root / "panel")
    write_generated_panel(store, panel, datasets=datasets or None)
    return store


def _income(store: PanelStore) -> Mapping[str, StatementHistory]:
    return load_statement_histories(
        store, dataset=INCOME_DATASET, years=(YEAR,), as_of=AS_OF, max_staleness=None
    )


def _calendar(store: PanelStore) -> TradingCalendar:
    return load_trading_calendar(store, exchange=EXCHANGE, years=(YEAR,), as_of=AS_OF)


# --- V2-P2-001 (evidence plane): the vector test_research_cycle.py used to hold ----------------

_EVIDENCE_NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
"""`tests/conftest.py`'s `FROZEN_NOW`, restated because a vector is a callable and not a test.

Restated rather than imported so this module has no fixture graph at all -- every row has to be
runnable from a `Path` alone, which is what lets one harness drive nine of them.
"""
_EVIDENCE_LATER = datetime(2026, 7, 24, 11, 0, tzinfo=UTC)
"""Half an hour after the request's `as_of`, which is the whole injection."""


def _evidence(available_at: datetime) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=_EVIDENCE_NOW,
            available_time=available_at,
            ingested_time=available_at,
            revision_time=available_at,
        ),
        source_id="synthetic.a-share",
        source_uri="fixture://limit_up/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit_up.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )


def _cycle(root: Path, evidence: EvidenceSnapshot) -> str:
    request = ResearchRunRequest(
        run_id="run_p2_009",
        mode="replay",
        subject="000001.SZ",
        as_of=_EVIDENCE_NOW,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest="b" * 64,
        random_seed=7,
    )
    engine = ResearchEngine(
        repository=SQLiteRunRepository(root / "state.sqlite3"),
        memory=InMemoryResearchMemory(),
        clock=lambda: _EVIDENCE_NOW,
        recovery_store=SQLiteRecoveryStore(root / "state.sqlite3"),
    )
    result = engine.run_cycle(request)
    return f"{result.signal.direction} on {sorted(result.signal.evidence_ids)}"


def _inject_future_evidence(root: Path) -> object:
    return _cycle(root, _evidence(_EVIDENCE_LATER))


def _clear_future_evidence(root: Path) -> str:
    return _cycle(root, _evidence(_EVIDENCE_NOW))


# --- V2-P2-001 (panel plane): a filing announced after the read --------------------------------


def _inject_future_filing(root: Path) -> object:
    panel = generate_panel(shapes=("financials.announced_after_the_as_of",))
    return _income(_stored(root, panel, INCOME_DATASET))


def _clear_future_filing(root: Path) -> str:
    histories = _income(_stored(root, generate_panel(), INCOME_DATASET))
    return f"the income panel answered for {len(histories)} securities"


# --- V2-P2-002: two versions of one key, on one announcement day -------------------------------


def _value_of_the_disputed_column(store: PanelStore) -> float:
    histories = _income(store)
    code = sorted(histories)[0]
    return histories[code].filing_for(BASE_PERIOD, BASE_ANNOUNCEMENT).value_of(DISPUTED_COLUMN)


def _inject_duplicate_versions(root: Path) -> object:
    panel = generate_panel(shapes=("financials.same_day_duplicate_versions",))
    return _value_of_the_disputed_column(_stored(root, panel, INCOME_DATASET))


def _clear_duplicate_versions(root: Path) -> str:
    store = _stored(root, generate_panel(), INCOME_DATASET)
    return f"the one stored version answers {_value_of_the_disputed_column(store)!r}"


# --- V2-P2-003: a publication dated after the read ---------------------------------------------


def _index_membership(store: PanelStore) -> IndexMembership:
    return load_index_membership(
        store, index_code="000300.SH", years=(YEAR,), as_of=AS_OF, max_staleness=None
    )


def _inject_future_publication(root: Path) -> object:
    panel = generate_panel(shapes=("index.publication_after_the_as_of",))
    return _index_membership(_stored(root, panel, INDEX_WEIGHT_DATASET))


def _clear_future_publication(root: Path) -> str:
    membership = _index_membership(_stored(root, generate_panel(), INDEX_WEIGHT_DATASET))
    return f"000300.SH answered for publications {membership.publication_dates}"


# --- V2-P2-004: an assignment that takes effect after the read ---------------------------------


def _industry(store: PanelStore) -> Mapping[str, SecurityIndustryHistory]:
    return load_industry_histories(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)


def _inject_future_reclassification(root: Path) -> object:
    panel = generate_panel(shapes=("industry.reclassification_after_the_as_of",))
    return _industry(_stored(root, panel, INDUSTRY_MEMBERSHIP_DATASET))


def _clear_future_reclassification(root: Path) -> str:
    histories = _industry(_stored(root, generate_panel(), INDUSTRY_MEMBERSHIP_DATASET))
    return f"the industry panel answered for {len(histories)} securities"


# --- V2-P2-005: two samples of one security on one prediction day ------------------------------


def _samples(
    root: Path, *, horizons: tuple[str, ...], offsets: tuple[int, ...]
) -> tuple[LabelSample, ...]:
    """`len(horizons)` samples of one security, off the stored calendar.

    `offsets` indexes the panel's own sessions for each sample's prediction day, so two zeros
    are two samples of one day -- the shape refused -- and `(0, 1)` is the ordinary consecutive
    pair every daily-frequency label set is made of.
    """
    panel = generate_panel()
    calendar = _calendar(_stored(root, panel, TRADING_CALENDAR_DATASET))
    code = panel.securities[0]
    return tuple(
        LabelSample(
            ts_code=code,
            window=build_label_window(
                as_of=datetime.combine(
                    panel.sessions[offset], datetime.min.time(), tzinfo=SHANGHAI
                ).replace(hour=16),
                zone=SHANGHAI,
                horizon=parse_horizon(horizon),
                calendar=calendar,
            ),
        )
        for horizon, offset in zip(horizons, offsets, strict=True)
    )


def _inject_two_samples_on_one_prediction_day(root: Path) -> object:
    return overlapping_windows(_samples(root, horizons=("1d", "3d"), offsets=(0, 0)))


def _clear_two_samples_on_one_prediction_day(root: Path) -> str:
    (overlap,) = overlapping_windows(_samples(root, horizons=("3d", "3d"), offsets=(0, 1)))
    return f"the set is legitimate and annotated: {overlap.summary}"


# --- V2-P2-006: a row that disagrees with itself about its own session --------------------------

_CORRUPTED_ROW = 8
"""The `daily` grid is `[(day, code) for day in sessions for code in securities]` at eight
securities, so row 8 is `securities[0]` on `sessions[1]` -- the first row that has a previous
session to be compared against."""

_CORRUPTION = 5.0
"""Five percentage points added to one row's `pct_chg`, against a 1e-4 (0.01 point) bound.

Far past it on purpose: `MAX_PUBLISHED_RETURN_DISAGREEMENT` is one tick of a two-decimal
percentage, so a corruption of one tick would sit exactly on the boundary this vector is not
the place to pin -- `tests/unit/domain/test_daily_prices.py` owns that. What this proves is
that the third witness is consulted at all.
"""


def _pct_chg_panel(root: Path, *, corrupt: bool) -> tuple[GeneratedPanel, PanelStore]:
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    if corrupt:
        batch = panel.batch(DAILY_DATASET)
        columns = tuple(
            PanelColumn(
                column.name,
                column.kind,
                tuple(
                    float(value) + _CORRUPTION  # type: ignore[arg-type]
                    if index == _CORRUPTED_ROW
                    else value
                    for index, value in enumerate(column.values)
                ),
            )
            if column.name == "pct_chg"
            else column
            for column in batch.columns
        )
        panel = replace(
            panel, batches={**panel.batches, DAILY_DATASET: replace(batch, columns=columns)}
        )
    return panel, _stored(root, panel)


def _session_return(root: Path, *, corrupt: bool) -> SessionReturns:
    panel, store = _pct_chg_panel(root, corrupt=corrupt)
    calendar = _calendar(store)
    code, previous, day = panel.securities[0], panel.sessions[0], panel.sessions[1]
    bars = {
        session: load_daily_bars(
            store,
            day=session,
            calendar=calendar,
            as_of=AS_OF,
            max_staleness=None,
        )[code]
        for session in (previous, day)
    }
    return session_returns(
        bars[day],
        previous_close=bars[previous].close,
        previous_day=previous,
        factors=load_adjustment_histories(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)[
            code
        ],
    )


def _inject_disagreeing_pct_chg(root: Path) -> object:
    return _session_return(root, corrupt=True)


def _clear_disagreeing_pct_chg(root: Path) -> str:
    computed = _session_return(root, corrupt=False)
    return f"the session return is {computed.published!r} on both paths"


# --- V2-P2-007: a halt that was still running at the close --------------------------------------


def _realized_return_over_the_halt(root: Path, *, halted: bool) -> float:
    shapes = ("daily.close_moves_between_sessions",)
    panel = generate_panel(shapes=(*shapes, "suspension.timed_interruption") if halted else shapes)
    store = _stored(root, panel)
    calendar = _calendar(store)
    code = panel.securities[0]
    window = build_label_window(
        as_of=datetime(2026, 1, 4, 16, 0, tzinfo=SHANGHAI),
        zone=SHANGHAI,
        horizon=parse_horizon("2d"),
        calendar=calendar,
    )
    label = label_outcome(
        window,
        ts_code=code,
        bars={
            day: load_daily_bars(
                store,
                day=day,
                calendar=calendar,
                as_of=AS_OF,
                max_staleness=None,
            )[code]
            for day in window.sessions
        },
        factors=load_adjustment_histories(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)[
            code
        ],
        limits={
            day: load_price_limits(
                store,
                day=day,
                calendar=calendar,
                as_of=AS_OF,
                max_staleness=None,
            )[code]
            for day in window.sessions
        },
        halts=halt_corpus_for_years(
            load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None), years=(YEAR,)
        ),
        universe=load_stock_universe(store, years=(YEAR,), as_of=AS_OF, max_staleness=None),
    )
    return label.realized_return


def _inject_halt_into_the_close(root: Path) -> object:
    return _realized_return_over_the_halt(root, halted=True)


def _clear_halt_into_the_close(root: Path) -> str:
    return f"the window is labelled {_realized_return_over_the_halt(root, halted=False)!r}"


# --- the register -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class InjectionVector:
    """One injection, both of its halves, and what each half is required to say.

    `inject` and `clear` are fields rather than a dispatch keyed on `vector_id`, which is the
    whole design: a row cannot be added without them, and the two parametrized tests below run
    every row of the tuple, so an added row is an executed row.
    """

    vector_id: str
    issue: str
    injects: str
    """One line: what goes in. Prose, because no two of these are the same kind of thing."""
    refused_by: type[Exception]
    refusal: str
    """A regex the refusal must match, narrow enough to name which rule refused.

    `test_no_vectors_refusal_matches_another_vectors_refusal` is what makes "narrow enough"
    mechanical rather than a review note.
    """
    cleared: str
    """A regex the *un*-injected read's answer must match. Declared for the same reason the
    refusal is: "it did not raise" is satisfied by a read that answered nothing."""
    inject: Callable[[Path], object]
    clear: Callable[[Path], str]


INJECTION_VECTORS = (
    InjectionVector(
        vector_id="evidence-available-after-the-request",
        issue="V2-P2-001",
        injects=(
            "one EvidenceSnapshot whose available_time is half an hour after the request's "
            "as_of, driven through ResearchEngine.run_cycle -- the vector "
            "tests/integration/test_research_cycle.py held as a hand-written try/except/else "
            "until this table replaced it. The refusal is LookAheadViolationError raised "
            "inside a pydantic model_validator, so what reaches a caller is the "
            "ValidationError wrapping it -- which is the whole reason "
            "backtest/replay.py carries _is_look_ahead_violation instead of an isinstance "
            "check"
        ),
        refused_by=ValidationError,
        refusal=r"evidence is not visible at request as_of",
        cleared=r"^bullish on \['ev_[0-9a-f]{24}'\]$",
        inject=_inject_future_evidence,
        clear=_clear_future_evidence,
    ),
    InjectionVector(
        vector_id="filing-announced-after-the-read",
        issue="V2-P2-001",
        injects=(
            "one income row whose ann_date is three days after the panel's as_of, in the same "
            "partition year as the rows around it"
        ),
        refused_by=PanelStorageError,
        refusal=r"the income panel cannot be read at .*\['not_yet_knowable'\]",
        cleared=r"^the income panel answered for 8 securities$",
        inject=_inject_future_filing,
        clear=_clear_future_filing,
    ),
    InjectionVector(
        vector_id="two-versions-of-one-announcement",
        issue="V2-P2-002",
        injects=(
            "a second income row under the same (security, period, ann_date) key, disagreeing "
            "on the projection's first column and separated from the first only by update_flag "
            "-- the restatement form the announcement clock cannot order"
        ),
        refused_by=AmbiguousReportError,
        refusal=r"2 versions disagree about 'total_revenue' \(100\.0, 999\.0\)",
        cleared=r"^the one stored version answers 100\.0$",
        inject=_inject_duplicate_versions,
        clear=_clear_duplicate_versions,
    ),
    InjectionVector(
        vector_id="index-publication-after-the-read",
        issue="V2-P2-003",
        injects=(
            "a second monthly index_weight publication, dated after the read and carrying a "
            "real rebalance -- one constituent added and one removed"
        ),
        refused_by=PanelStorageError,
        refusal=r"000300\.SH's composition cannot be read at .*\['not_yet_knowable'\]",
        cleared=r"^000300\.SH answered for publications \(datetime\.date\(2026, 1, 16\),\)$",
        inject=_inject_future_publication,
        clear=_clear_future_publication,
    ),
    InjectionVector(
        vector_id="industry-assignment-after-the-read",
        issue="V2-P2-004",
        injects=(
            "an index_member_all assignment whose in_date is seventeen days after the read, "
            "closing an interval that had already taken effect"
        ),
        refused_by=PanelStorageError,
        refusal=r"the industry classification cannot be read at .*\['not_yet_knowable'\]",
        cleared=r"^the industry panel answered for 8 securities$",
        inject=_inject_future_reclassification,
        clear=_clear_future_reclassification,
    ),
    InjectionVector(
        vector_id="two-label-samples-on-one-prediction-day",
        issue="V2-P2-005",
        injects=(
            "one security's 1d and 3d label windows off the same prediction day -- two targets "
            "on one feature row, which is the one overlap shape that is not a horizon artefact"
        ),
        refused_by=LabelError,
        refusal=r"carries two samples for prediction day 2026-01-05 \(1d over .* and 3d over",
        cleared=r"^the set is legitimate and annotated: .* share 3 session\(s\) .* 75\.0%",
        inject=_inject_two_samples_on_one_prediction_day,
        clear=_clear_two_samples_on_one_prediction_day,
    ),
    InjectionVector(
        vector_id="row-disagreeing-with-its-own-pct-chg",
        issue="V2-P2-006",
        injects=(
            "five percentage points added to one stored daily row's pct_chg, so the row's own "
            "two statements of its session return come apart. The write accepts it -- "
            "_refuse_close_disagreement compares two pre_close values and pct_chg is the only "
            "witness that reads close -- and session_returns is where it is caught"
        ),
        refused_by=PriceDataError,
        refusal=r"pct_chg says .*% -- a gap of .*, past the 0\.0001",
        cleared=r"^the session return is 0\.050000000000000044 on both paths$",
        inject=_inject_disagreeing_pct_chg,
        clear=_clear_disagreeing_pct_chg,
    ),
    InjectionVector(
        vector_id="halt-still-running-at-the-close",
        issue="V2-P2-007",
        injects=(
            "a suspend_d row reading 13:00-15:00 on the window's exit session -- the security "
            "traded, so it has a bar and is TradingState.interrupted, and its close is the last "
            "print before the halt rather than an auction price"
        ),
        refused_by=LabelError,
        refusal=r"carries 1 refusal\(s\): halted_into_the_close on 2026-01-07",
        cleared=r"^the window is labelled 0\.10000000000000009$",
        inject=_inject_halt_into_the_close,
        clear=_clear_halt_into_the_close,
    ),
)
"""Eight vectors over seven issues, each a pair.

`V2-P2-001` carries two because its injection has two planes: the evidence store's per-row
`visible_at` check and the panel store's partition-level `not_yet_knowable`, which are separate
mechanisms and fail for separate reasons. The first is the row this table was asked to take
over from `test_research_cycle.py`.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class DisclosedExclusion:
    """One P2 issue with no row here, why, and the test that does cover it.

    `KNOWN_*_LIMITATIONS` is this repository's answer to "state it rather than assert something
    untrue", and this is the same answer at the register's level: an issue whose finding is
    that nothing refuses cannot be a row in a table whose harness asserts a refusal, and
    pretending otherwise would mean a second harness whose only job is to make the count right.
    """

    issue: str
    because: str
    covered_by: str
    """`path.py::test_name`, AST-verified below."""


DISCLOSED_EXCLUSIONS = (
    DisclosedExclusion(
        issue="V2-P2-008",
        because=(
            "the finding is that nothing refuses. A stock_basic partition fetched with "
            "list_status='L' is not malformed, it is smaller: it is self-consistent on every "
            "cross section, so require_datasets clears it identically to a complete registry "
            "-- same cleared datasets, scope, caveats, blocks and notices -- and the two only "
            "answer differently when a caller asks a day inside a terminated name's listing "
            "(227 securities on 2019-06-28 at live scale). An injection whose expected refusal "
            "does not exist has no place in a table whose harness asserts one; the defence is "
            "at fetch time, which no read-side check can audit, and it is stated in "
            "KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_"
            "downstream_check"
        ),
        covered_by=(
            "tests/integration/panel/test_panel_gate.py::"
            "test_a_registry_missing_its_delisted_half_clears_exactly_as_a_whole_one_does"
        ),
    ),
)


def _ids(vector: InjectionVector) -> str:
    return f"{vector.issue}:{vector.vector_id}"


# --- the two directions, over the whole table ---------------------------------------------------


@pytest.mark.parametrize("vector", INJECTION_VECTORS, ids=_ids)
def test_the_injected_half_is_refused_and_names_its_own_rule(
    vector: InjectionVector, tmp_path: Path
) -> None:
    """Direction one. `match=` is the row's own pattern, so the assertion is as narrow as the
    table says it is -- and `test_no_vectors_refusal_matches_another_vectors_refusal` is what
    stops a row from declaring a pattern that would have matched anything."""
    with pytest.raises(vector.refused_by, match=vector.refusal):
        vector.inject(tmp_path)


@pytest.mark.parametrize("vector", INJECTION_VECTORS, ids=_ids)
def test_the_same_read_without_the_injection_answers(
    vector: InjectionVector, tmp_path: Path
) -> None:
    """Direction two, and the half that makes direction one mean anything: a contract that
    refused everything would pass the first test for all eight rows.

    The answer is matched against the row's own `cleared` pattern rather than merely checked
    for not raising, because "it did not raise" is satisfied by a read that came back empty --
    which is exactly what a partition gate that had drifted to blocking everything would look
    like from the outside if the caller swallowed the difference."""
    answered = vector.clear(tmp_path)

    assert re.search(vector.cleared, answered), (
        f"{vector.vector_id}'s cleared half answered {answered!r}, which does not match "
        f"{vector.cleared!r}"
    )
    assert not re.search(vector.refusal, answered)


# --- the audits ---------------------------------------------------------------------------------


def test_no_vectors_refusal_matches_another_vectors_refusal(tmp_path: Path) -> None:
    """The drift audit that costs something, and the reason it is worth it.

    A register's characteristic rot is a row whose pattern is loose -- `.*`, or a bare word
    like `refused` -- because such a row passes forever and proves nothing about which rule
    fired. This runs every injection once, collects the real refusal text, and requires each
    pattern to match its own and **none of the others**. A row added with a lazy pattern is red
    on the first run.

    It also re-runs every injection, which is the second half of the audit: a row whose
    `inject` stopped raising is caught here as well as in the parametrized test, and this one
    names the whole matrix rather than one cell.
    """
    messages: dict[str, str] = {}
    for vector in INJECTION_VECTORS:
        with pytest.raises(vector.refused_by) as refused:
            vector.inject(tmp_path / vector.vector_id)
        messages[vector.vector_id] = str(refused.value)

    matches = {
        (vector.vector_id, other): bool(re.search(vector.refusal, messages[other]))
        for vector in INJECTION_VECTORS
        for other in messages
    }

    assert matches == {
        (vector.vector_id, other): vector.vector_id == other
        for vector in INJECTION_VECTORS
        for other in messages
    }


def test_every_p2_injection_issue_is_a_vector_or_a_disclosed_exclusion() -> None:
    """`001`..`008` are the issues whose gate is "all pass"; none may go missing quietly.

    Both directions, for `LABEL_REFUSAL_CODES`' reason: an issue with no row and no exclusion
    is an uncovered issue, and a row or an exclusion naming an issue outside the eight is a
    typo that would otherwise satisfy the first half.
    """
    covered = {vector.issue for vector in INJECTION_VECTORS}
    excluded = {exclusion.issue for exclusion in DISCLOSED_EXCLUSIONS}

    assert covered | excluded == set(P2_INJECTION_ISSUES)
    assert covered & excluded == set()
    assert len({vector.vector_id for vector in INJECTION_VECTORS}) == len(INJECTION_VECTORS)


def test_each_disclosed_exclusion_names_a_test_that_exists() -> None:
    """An exclusion is a promise that the issue is covered somewhere else, and a promise
    pointing at a deleted or renamed test is worse than no promise: it reads exactly like a
    kept one.

    Checked by parsing the named module rather than by importing it, which is the same
    structural check `tests/unit/test_offline_suite.py` makes of the `e2e` marker and
    `scripts/build_feature_coverage.py` makes of every ledger `file#symbol` reference.
    """
    for exclusion in DISCLOSED_EXCLUSIONS:
        path_part, _, function = exclusion.covered_by.partition("::")
        module = ROOT / path_part

        assert module.exists(), f"{exclusion.issue} points at a missing file: {path_part}"
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        declared = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert function in declared, (
            f"{exclusion.issue} is excused by {exclusion.covered_by}, which does not exist"
        )


def test_every_vector_is_driven_by_this_modules_own_parametrized_harness() -> None:
    """The row cannot be orphaned, and this is what keeps that true after a refactor.

    Today every row runs because both direction tests are parametrized over
    `INJECTION_VECTORS` itself. A later rewrite into a hand-written dispatch -- one test per
    issue, or a loop over a filtered subset -- would restore exactly the failure mode this
    table exists to close: a row added and nothing running it. So the parametrization is
    asserted structurally, off this module's own AST.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    parametrized = {
        node.name: {
            ast.unparse(decorator.args[1])
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and ast.unparse(decorator.func).endswith("parametrize")
            and len(decorator.args) == 2
        }
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    over_the_table = {
        name for name, sources in parametrized.items() if "INJECTION_VECTORS" in sources
    }

    assert over_the_table == {
        "test_the_injected_half_is_refused_and_names_its_own_rule",
        "test_the_same_read_without_the_injection_answers",
    }
    assert {
        name
        for name, node in (
            (node.name, node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )
        if any(argument.arg == "vector" for argument in node.args.args)
    } == over_the_table


def test_the_register_is_a_regression_gate_the_default_run_cannot_skip() -> None:
    """What "CI 回归门" is, spelled as the three facts that make it one.

    No new job and no new marker. `addopts` deselects exactly one marker, this module declares
    none, and the `python` job runs `uv run pytest` -- so the register runs on every push and
    pull request across the whole matrix, which is what "all of 001-008 pass" being a P3/P4
    precondition needs. A dedicated marker would make it *more* skippable, not less: a marker is
    a name a future `-m` can exclude, and the deselection this repository already has is the one
    thing standing between the default suite and the network.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        options = tomllib.load(handle)["tool"]["pytest"]["ini_options"]
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)

    assert [entry.split(":")[0].strip() for entry in options["markers"]] == ["e2e"]
    assert options["addopts"].endswith('-m "not e2e"')
    assert not Path(__file__).is_relative_to(ROOT / "tests" / "e2e")
    assert not any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        for node in tree.body
    ), "a module-level pytestmark here would be a marker a future -m could deselect"
    assert "uv run pytest --cov=openalpha_cn" in workflow


def test_the_replaced_vector_no_longer_lives_where_it_was() -> None:
    """Replacement, not coexistence -- which is what the roadmap asks for.

    `test_research_cycle.py`'s hand-written `try/except ValueError/else` is row one here now.
    Leaving it in place as well would mean two statements of one rule, which is the drift this
    whole file is about -- so its absence is asserted rather than assumed, and the module it
    left is required to still exist and still hold its other two tests.
    """
    module = ROOT / "tests" / "integration" / "test_research_cycle.py"
    source = module.read_text(encoding="utf-8")
    declared = {
        node.name
        for node in ast.parse(source, filename=str(module)).body
        if isinstance(node, ast.FunctionDef)
    }

    assert "test_cycle_rejects_evidence_that_was_not_visible_at_as_of" not in declared
    assert "not visible" not in source
    assert {
        "test_multi_agent_cycle_persists_evidence_linked_decision_idempotently",
        "test_cycle_abstains_explicitly_when_evidence_is_insufficient",
    } <= declared


def test_every_vector_says_what_it_injects_and_which_issue_asked_for_it() -> None:
    """A row with an empty `injects` is a row nobody can review, and a register nobody reviews
    is a list. Length rather than truthiness, because a one-word answer is the same failure."""
    assert all(len(vector.injects) >= 80 for vector in INJECTION_VECTORS)
    assert all(vector.issue in P2_INJECTION_ISSUES for vector in INJECTION_VECTORS)
    assert all(len(exclusion.because) >= 200 for exclusion in DISCLOSED_EXCLUSIONS)
    assert all(
        vector.vector_id == vector.vector_id.lower().replace(" ", "-")
        for vector in INJECTION_VECTORS
    )
