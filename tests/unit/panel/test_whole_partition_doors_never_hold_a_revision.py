"""The whole-partition door cannot leak a revision, and this file is what keeps that true.

Visibility waits for two clocks: a row is readable at `as_of` once it had first become available
**and** the version stored for it had been published (`domain/time.py::is_visible_at`).
`read_visible_at` applies both in SQL, row by row. The other door -- `PanelStore.read_if_ready`
and `AssessedPanelRead.read`, the whole-partition read -- applies neither: `evaluate_readiness`
compares one number per partition against `as_of`, the newest `available_time` its coverage
record holds, and then hands back every row. Nothing on that path reads `revision_time`.

That is sound for exactly one kind of dataset: one whose rows never carry a revision instant
after their availability instant. For those, "newest availability at or before `as_of`" already
implies "every row's stored version published by then", and the door answers the same question
the row predicate does. Recording `max(revision_time)` in the catalog would make the door check
the second clock too, at the price of a catalog schema change and a migration; it is not needed
while the condition holds, so the condition is pinned here instead of assumed:

- every function in `panel_ingest` that takes the whole-partition door is enumerated from the
  source, and each is driven far enough to name the dataset it reads;
- each of those datasets is produced by a `providers/tushare.py` clock that never revises a row,
  measured by running the clock on a row that carries a later `f_ann_date`;
- the one clock that can revise (`ClockStrategy.announcement`) belongs to the four statement
  datasets and to no dataset read through the whole-partition door.

A new loader on the whole-partition door fails the first assertion until it is classified here;
a dataset moved onto a revising clock, or a revising dataset moved onto the whole-partition door,
fails the others.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import generate_panel

from openalpha_cn import panel_ingest
from openalpha_cn.domain.financial_statements import FINANCIAL_STATEMENT_DATASETS
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.providers.tushare import (
    _CLOCK_BUILDERS,
    TUSHARE_DATASETS,
    ClockStrategy,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
SRC: Final[Path] = ROOT / "src" / "openalpha_cn"
PANEL_INGEST: Final[Path] = SRC / "panel_ingest.py"

AS_OF: Final[datetime] = datetime(2026, 1, 9, 4, 0, tzinfo=UTC)


def _whole_partition_readers(tree: ast.AST) -> set[str]:
    """Every function that reads through a scope's `read`, which is the whole-partition door.

    `AssessedPanelRead.read` is the door; `read_visible_at` on the same scope is the filtered
    one. Matched on the receiver: a name bound from `<store>.assessed(...)` whose `.read(...)`
    is then called, so a file read or an unrelated `read` elsewhere is not counted. A direct
    `read_if_ready` call anywhere is counted too, though `panel_ingest` makes none today.
    """
    found: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        scopes = {
            target.id
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "assessed"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            receiver = node.func.value
            if node.func.attr == "read_if_ready" or (
                node.func.attr == "read"
                and isinstance(receiver, ast.Name)
                and receiver.id in scopes
            ):
                found.add(function.name)
    return found


WHOLE_PARTITION_LOADERS: Final[dict[str, Callable[[PanelStore], object]]] = {
    "load_trading_calendar": lambda store: panel_ingest.load_trading_calendar(
        store, exchange="SSE", years=(2026,), as_of=AS_OF
    ),
    "load_industry_trees": lambda store: panel_ingest.load_industry_trees(
        store, years=(2021,), as_of=AS_OF, max_staleness=None
    ),
    "load_industry_histories": lambda store: panel_ingest.load_industry_histories(
        store, years=(2026,), as_of=AS_OF, max_staleness=None
    ),
    "load_adjustment_histories": lambda store: panel_ingest.load_adjustment_histories(
        store, years=(2026,), as_of=AS_OF, max_staleness=None
    ),
    "load_index_membership": lambda store: panel_ingest.load_index_membership(
        store, index_code="000300.SH", years=(2026,), as_of=AS_OF, max_staleness=None
    ),
    "load_index_prices": lambda store: panel_ingest.load_index_prices(
        store, generate_panel().calendar(), years=(2026,), as_of=AS_OF, max_staleness=None
    ),
}
"""Each whole-partition loader, called just far enough to take its verdict.

The arguments are the least each loader accepts; none of them is read past `assessed`, because
the spy below stops the call there and records which dataset the requirement named.
"""


class _Stop(Exception):
    """Raised by the spy once the requirement has been seen, so no partition is ever needed."""


def _datasets_through_the_whole_partition_door(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> dict[str, str]:
    seen: dict[str, str] = {}
    current: list[str] = []

    def spy(self: PanelStore, requirement: ReadinessRequirement) -> Any:
        seen[current[-1]] = requirement.dataset
        raise _Stop

    monkeypatch.setattr(PanelStore, "assessed", spy)
    store = PanelStore(root / "panel")
    for name, call in WHOLE_PARTITION_LOADERS.items():
        current.append(name)
        with pytest.raises(_Stop):
            call(store)
    return seen


def _revises(clock: ClockStrategy, date_field: str) -> bool:
    """Whether `clock` stamps a later `revision_time` on a row that carries a later `f_ann_date`.

    The row names every date column any clock here reads, all on one day, plus an `f_ann_date`
    six months later -- the one column that moves a revision clock today. A clock that returns
    a revision later than the availability for it is a clock that can revise.
    """
    row = {
        date_field: "20240105",
        "ann_date": "20240105",
        "f_ann_date": "20240705",
    }
    timeline = _CLOCK_BUILDERS[clock](row, date_field, datetime(2026, 1, 9, tzinfo=UTC))
    return timeline.revision_time > timeline.available_time


def test_every_whole_partition_loader_is_classified_here() -> None:
    """The enumeration is read off the source, so a loader added on the whole-partition door is
    unclassified until it is named in `WHOLE_PARTITION_LOADERS`, where its dataset is checked.

    `panel_ingest` is the only module outside the store that takes the door today, and that is
    asserted too: a whole-partition read added anywhere else in `src/` would otherwise sit
    outside the enumeration this file checks."""
    tree = ast.parse(PANEL_INGEST.read_text(encoding="utf-8"), filename=str(PANEL_INGEST))
    elsewhere = {
        path.relative_to(SRC).as_posix(): sorted(readers)
        for path in sorted(SRC.rglob("*.py"))
        if path not in {PANEL_INGEST, SRC / "panel" / "store.py"}
        and (readers := _whole_partition_readers(ast.parse(path.read_text(encoding="utf-8"))))
    }

    assert _whole_partition_readers(tree) == set(WHOLE_PARTITION_LOADERS)
    assert elsewhere == {}


def test_no_dataset_read_through_the_whole_partition_door_can_carry_a_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each whole-partition dataset's producing clock, run on a row with a later `f_ann_date`,
    stamps no revision -- so `max_available_time <= as_of` is the whole visibility question for
    it, and the door that answers only that question answers it correctly."""
    datasets = _datasets_through_the_whole_partition_door(monkeypatch, tmp_path)
    descriptors = {descriptor.dataset: descriptor for descriptor in TUSHARE_DATASETS}

    assert set(datasets) == set(WHOLE_PARTITION_LOADERS)
    revising = {
        loader: dataset
        for loader, dataset in datasets.items()
        if _revises(descriptors[dataset].clock, descriptors[dataset].date_field)
    }
    assert revising == {}, (
        f"{revising} read a dataset whose clock can stamp revision_time after available_time "
        "through a door that judges only the newest available_time; move it to read_visible_at "
        "or record the revision clock in the catalog first"
    )


def test_the_only_revising_clock_belongs_to_the_statements_and_they_take_the_filtered_door(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive half, so the check above cannot pass by measuring nothing: the announcement
    clock does revise, it is the only clock that does, it is the statements' clock, and no
    statement dataset is read through the whole-partition door."""
    revising_clocks = {
        descriptor.clock
        for descriptor in TUSHARE_DATASETS
        if _revises(descriptor.clock, descriptor.date_field)
    }
    announced = {
        descriptor.dataset
        for descriptor in TUSHARE_DATASETS
        if descriptor.clock == ClockStrategy.announcement
    }

    assert revising_clocks == {ClockStrategy.announcement}
    assert announced == set(FINANCIAL_STATEMENT_DATASETS)
    whole = _datasets_through_the_whole_partition_door(monkeypatch, tmp_path)
    assert not announced & set(whole.values())
