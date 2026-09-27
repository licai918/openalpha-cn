"""A generated panel a strategy backtest can run on, shared by the view and the CLI tests
(`V2-P6-007`).

At the `tests/` root for `panel_fixtures.py`'s reason: two subtrees use it --
`tests/unit/test_strategy_view.py` and `tests/unit/test_cli_strategy_backtest.py` -- and each
building its own copy would let the two faces be tested against two different panels.

The panel is `panel_fixtures.generate_panel`'s ten-session, eight-security corpus with closes that
move between sessions, plus two things that generator has no synthetic form for: an `index_daily`
partition (000300.SH, which the level read requires, and 000905.SH, the protocol's benchmark) and
raw `reversal_1d/v1` cross sections built through the real engine at each session's 16:30
publication instant. `write_tiered_corpus` adds the processed and neutralized tiers under probe
specs an eight-name panel clears. Everything is written at test time; nothing is checked in.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

from panel_fixtures import (
    DAILY_BASIC_DATASET,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformRegistry,
    FactorTransformSpec,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATA_COLUMNS, INDEX_DAILY_DATASET
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_requirement,
    session_publication_instant,
    write_index_prices,
)
from openalpha_cn.panel_neutralization import (
    apply_factor_neutralization,
    load_industry_market_cap_cross_section,
    write_neutralized_factor_panels,
)

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
BUILT_AT: Final[datetime] = datetime(2026, 1, 17, 1, 0, tzinfo=UTC)
READ_AT: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""12:00 Shanghai on the Saturday after the panel's last session: every close is knowable."""

INDEX_LEVELS: Final[dict[str, tuple[float, ...]]] = {
    "000300.SH": (4000.0, 4040.0, 4020.0, 4060.0, 4100.0, 4080.0, 4120.0, 4160.0, 4140.0, 4180.0),
    "000905.SH": (6000.0, 6030.0, 6090.0, 6060.0, 6120.0, 6150.0, 6180.0, 6120.0, 6180.0, 6240.0),
}


def build_instant(session: date, *, late: bool = False) -> datetime:
    """The session's publication instant (16:30 Shanghai), or half an hour after it."""
    instant = session_publication_instant(session)
    return instant + timedelta(minutes=30) if late else instant


def stored_value(subjects: Sequence[str], subject: str, session_index: int) -> float:
    """`reversal_1d/v1`'s stored value: a rotation of the securities, so holdings change."""
    position = (subjects.index(subject) + session_index) % len(subjects)
    return (position + 1) / 100.0


def _index_batch(sessions: Sequence[date]) -> ColumnarPanelBatch:
    subjects: list[str] = []
    days: list[date] = []
    levels: list[float] = []
    previous_levels: list[float] = []
    for code, path in INDEX_LEVELS.items():
        previous = path[0] / 1.001
        for day, level in zip(sessions, path, strict=True):
            subjects.append(code)
            days.append(day)
            levels.append(level)
            previous_levels.append(previous)
            previous = level
    columns = (
        PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        PanelColumn("open", "float", tuple(levels)),
        PanelColumn("high", "float", tuple(levels)),
        PanelColumn("low", "float", tuple(levels)),
        PanelColumn("close", "float", tuple(levels)),
        PanelColumn("pre_close", "float", tuple(previous_levels)),
        PanelColumn(
            "pct_chg",
            "float",
            tuple(
                (level / previous - 1.0) * 100.0
                for level, previous in zip(levels, previous_levels, strict=True)
            ),
        ),
        PanelColumn("vol", "float", tuple(300000.0 for _ in days)),
        PanelColumn("amount", "float", tuple(900000.0 for _ in days)),
    )
    assert tuple(column.name for column in columns) == INDEX_DAILY_DATA_COLUMNS
    instants = tuple(session_publication_instant(day) for day in days)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=INDEX_DAILY_DATASET,
        kind="index_daily",
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
            revision_time=instants,
        ),
        columns=columns,
    )


def _build(
    store: PanelStore, panel: GeneratedPanel, session: date, *, late: bool, reversed_: bool = False
) -> FactorPanel:
    """One raw cross section through the real engine, the evaluator seam supplying values.

    `reversed_` files the securities in the opposite order, so a second build on one session
    carries values a reader could tell apart from the first.
    """
    instant = build_instant(session, late=late)
    index = panel.sessions.index(session)
    subjects = tuple(reversed(panel.securities)) if reversed_ else tuple(panel.securities)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=subjects,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(),
                years=(session.year,),
                as_of=instant,
                max_staleness=timedelta(days=30),
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: lambda context: stored_value(subjects, context.subject, index)
        },
    )


def write_strategy_corpus(
    root: Path, *, late: bool = False, rebuilt_late: bool = False
) -> GeneratedPanel:
    """Write the panel, the index levels and a raw cross section on every session but the first.

    `late=True` stamps every build half an hour after its session's publication instant, which
    is the look-ahead shape the backtest must refuse. `rebuilt_late=True` keeps the on-time
    builds and adds a second, reversed build half an hour later on every session -- the shape a
    re-run produces, which a reader must not trade on in place of the one it had at the signal.
    """
    store = PanelStore(root / "panel")
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    write_generated_panel(store, panel)
    write_index_prices(store, [_index_batch(panel.sessions)])
    builds = [_build(store, panel, session, late=late) for session in panel.sessions[1:]]
    if rebuilt_late:
        builds.extend(
            _build(store, panel, session, late=True, reversed_=True)
            for session in panel.sessions[1:]
        )
    write_factor_panels(store, builds)
    return panel


# --- the processed and neutralized tiers ---------------------------------------------------------

PROBE_TRANSFORM: Final[FactorTransformSpec] = FactorTransformSpec(
    key="probe_zscore",
    version=1,
    winsorization=WinsorizationPolicy(method="none"),
    standardization="zscore",
    missing_values=MissingValuePolicy(
        not_in_universe="exclude",
        insufficient_history="exclude",
        ambiguous_filing="exclude",
        input_missing="exclude",
        undefined_value="exclude",
    ),
    min_cross_section=1,
)
"""A transform whose floor an eight-name panel clears; the shipped one needs fifty names.

`tests/integration/panel/test_factor_neutralizations.py::_transform_spec`'s probe, restated with
the same settings because that helper lives in a test module no other file may import."""

PROBE_NEUTRALIZATION: Final[FactorNeutralizationSpec] = FactorNeutralizationSpec(
    key="probe_neutral",
    version=1,
    industry_level="L1",
    market_cap_measure="total_mv",
    market_cap_scale="log",
    participation="measured_only",
    min_industry_members=2,
    min_cross_section=2,
)
"""A neutralisation whose floors an eight-name panel clears (`industry_and_size/v1` needs 100)."""

PROBE_TRANSFORMS: Final[FactorTransformRegistry] = FactorTransformRegistry((PROBE_TRANSFORM,))
PROBE_NEUTRALIZATIONS: Final[FactorNeutralizationRegistry] = FactorNeutralizationRegistry(
    (PROBE_NEUTRALIZATION,)
)

CAP_BASE: Final[float] = 2_000_000.0
CAP_STEP: Final[float] = 750_000.0


def _with_market_caps(panel: GeneratedPanel) -> GeneratedPanel:
    """The generated panel with a `total_mv` that varies, so the size regressor is not flat.

    The generator writes `1.0` on every row, a design with no dispersion that the neutralisation
    refuses as degenerate; `test_factor_neutralizations._with_market_caps`' substitution.
    """
    batch = panel.batch(DAILY_BASIC_DATASET)
    order = tuple(panel.securities)
    caps = tuple(CAP_BASE + CAP_STEP * order.index(str(subject)) for subject in batch.subjects)
    columns = tuple(
        PanelColumn(column.name, column.kind, caps) if column.name == "total_mv" else column
        for column in batch.columns
    )
    replaced = dataclasses.replace(batch, columns=columns)
    return dataclasses.replace(panel, batches={**panel.batches, DAILY_BASIC_DATASET: replaced})


@dataclass(frozen=True, slots=True, kw_only=True)
class TieredCorpus:
    """What `write_tiered_corpus` stored, so a read can be held against the writer's rows."""

    panel: GeneratedPanel
    processed: Mapping[date, Mapping[str, float]]
    """Each build session's stored processed value per security, admitted rows only."""
    neutralized: Mapping[date, Mapping[str, float]]
    """Each build session's stored residual per security, admitted rows only."""


def write_tiered_corpus(root: Path) -> TieredCorpus:
    """The strategy panel with all three tiers of `reversal_1d/v1` written through the real writers.

    Every session but the first gets a raw build at its 16:30 instant, the probe transform of it
    and the probe neutralisation of that, each written by its own plane's writer
    (`write_factor_panels`, `write_processed_factor_panels`, `write_neutralized_factor_panels`).
    """
    store = PanelStore(root / "panel")
    panel = _with_market_caps(generate_panel(shapes=("daily.close_moves_between_sessions",)))
    write_generated_panel(store, panel)
    write_index_prices(store, [_index_batch(panel.sessions)])
    raw, processed, neutralized = [], [], []
    for session in panel.sessions[1:]:
        source = _build(store, panel, session, late=False)
        transformed = apply_factor_transform(
            source, PROBE_TRANSFORM, code_commit=COMMIT, built_at=source.built_at
        )
        section = load_industry_market_cap_cross_section(
            store,
            PROBE_NEUTRALIZATION,
            subjects=panel.securities,
            day=session,
            as_of=build_instant(session),
            calendar=panel.calendar(),
            membership_years=(session.year,),
            max_staleness=None,
        )
        raw.append(source)
        processed.append(transformed)
        neutralized.append(
            apply_factor_neutralization(
                transformed,
                PROBE_NEUTRALIZATION,
                section,
                code_commit=COMMIT,
                built_at=source.built_at,
            )
        )
    write_factor_panels(store, raw)
    write_processed_factor_panels(store, processed)
    write_neutralized_factor_panels(store, neutralized)
    zone = session_publication_instant(panel.sessions[0]).tzinfo
    return TieredCorpus(
        panel=panel,
        processed={
            build.observations[0].as_of.astimezone(zone).date(): {
                row.subject: row.value
                for row in build.observations
                if row.coverage in TIER_ADMITTED_CODES["processed"] and row.value is not None
            }
            for build in processed
        },
        neutralized={
            build.observations[0].as_of.astimezone(zone).date(): {
                row.subject: row.value
                for row in build.observations
                if row.coverage in TIER_ADMITTED_CODES["neutralized"] and row.value is not None
            }
            for build in neutralized
        },
    )
