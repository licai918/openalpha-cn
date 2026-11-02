"""A rehearsal of `scripts/research/p7_reading.py`'s default measure and preflight (`V2-P7-004`,
fix round 1, ruling 4): before the one real read, the real `_panel_measure` and `_panel_preflight`
-- the real `capped_panel_store`, calendar, facts day, screen, label reader, engine and document --
run on a panel generated at test time, capped at a fixture window. Nothing here reads a research
store or claims anything; the read-once path's git checks are in
`tests/unit/scripts/test_p7_reading.py`.

The panel is `strategy_fixtures.write_two_year_corpus` (2026-01-05..2027-01-22, every dataset the
label reader opens, a raw and a processed `reversal_1d/v1` build on every session from 2026-12-01)
with a name history added for both years, because the facts day reads `namechange` for every year
through the signal day's (`strategy_survivor_facts._years_through`). The window is January 2027:
the measure reads the year before it too (the calendar and `label_years`), which is why a
single-year panel cannot rehearse it. Two things are patched, both fixture facts: the generated
calendar is `SZSE`'s where the research store's is `SSE`, and the probe transform an eight-name
panel clears is not a shipped one.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE
from panel_fixtures import _batch as batch
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from panel_fixtures import _namechange_batch as namechange_batch
from strategy_fixtures import PROBE_TRANSFORM, TWO_YEAR_LAST, write_two_year_corpus

from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import split_panel_batch_by_year, write_name_history

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


p7_windows = _research_module("p7_windows")
p7_diagnostics = _research_module("p7_diagnostics")
p7_reading = _research_module("p7_reading")

WINDOW = p7_windows.DiagnosticWindow(
    name="fixture", first=date(2027, 1, 4), last=TWO_YEAR_LAST, ceiling_year=2027
)
"""2027's first session to the corpus's last: the year before it is 2026, which the corpus holds."""
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(
    seed=0, composite_factors=("reversal_1d/v1",), min_listing_sessions=60
)
"""The one factor the corpus builds; a listing minimum its 2026 calendar ages every name past."""
RENAMED: Final[NameRecord] = NameRecord(
    ts_code="600000.SH",
    name="Renamed Bank",
    effective_from=date(2027, 1, 11),
    announced_on=date(2027, 1, 5),
    change_reason="rename",
)
"""A 2027 announcement, so `namechange` holds a partition for the signal year as well."""
BINDING: Final[dict[str, str]] = {
    "reading": "rehearsal",
    "registration_sha256": "a" * 64,
    "registration_commit": "b" * 40,
    "claim_sha256": "c" * 64,
}
NAMED: Final[dict[str, Any]] = {
    "window": "validation",
    "reading": "rehearsal",
    "role": "primary",
    "issue": "V2-P7-024",
    "stop_rule": {
        "score": "composite",
        "horizon": 2,
        "minimum_periods": 1,
        "any_of": [{"metric": "ew_minus_worst_decile", "mean_at_or_below": -1.0}],
    },
}
"""What `reading_lines` reads off a registration (window, reading, role, issue, rule); the fixture
window is not a named one, so the banner borrows a named window's span."""


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    root = tmp_path_factory.mktemp("rehearsal")
    panel = write_two_year_corpus(root, processed={PROBE_TRANSFORM: TWO_YEAR_LAST})
    store = PanelStore(root / "panel")
    # The generator's own records, fetched at its `AS_OF`, then the 2027 one, fetched when it was
    # announced: a batch may not hold a row announced after its fetch.
    for _year, part in split_panel_batch_by_year(namechange_batch(panel.name_records)):
        write_name_history(store, part)
    announced = midnight_shanghai(RENAMED.announced_on)
    write_name_history(
        store,
        batch(
            NAMECHANGE_DATASET,
            subjects=[RENAMED.ts_code],
            columns=[
                PanelColumn(NAME_COLUMN, "string", (RENAMED.name,)),
                PanelColumn(NAME_EFFECTIVE_COLUMN, "string", (RENAMED.effective_from.isoformat(),)),
                PanelColumn(
                    NAME_ANNOUNCEMENT_COLUMN, "string", (RENAMED.announced_on.isoformat(),)
                ),
                PanelColumn(NAME_REASON_COLUMN, "string", (RENAMED.change_reason,)),
            ],
            event_time=[announced],
            available_time=[announced],
            fetched_at=announced,
        ),
    )
    return root, panel


def body(panel: Any) -> dict[str, Any]:
    """A registration body for the fixture window, with what `register_reading` writes (the
    window is not a named one, so it is written here by hand)."""
    book = BOOK.model_dump(mode="json")
    book.pop("config_id")
    return {
        "request": {
            "first": WINDOW.first.isoformat(),
            "last": WINDOW.last.isoformat(),
            "stride": 1,
            "horizons": [2],
            "scores": ["composite"],
            "tails": [1, 2, 3],
            "loser_decile_count": BOOK.loser_decile_count,
        },
        "config": {
            "book": book,
            "transform": PROBE_TRANSFORM.qualified_key,
            "exchange": EXCHANGE,
            "min_coverage": 0.5,
            "max_carry_sessions": 250,
            "as_of": panel.as_of.isoformat(),
        },
        "code_commit": "0" * 40,
    }


class _Transforms:
    """`FACTOR_TRANSFORMS` with the fixture's probe transform declared beside the shipped ones."""

    def __init__(self, shipped: Any) -> None:
        self.shipped = shipped

    def get(self, key: str) -> Any:
        return PROBE_TRANSFORM if key == PROBE_TRANSFORM.qualified_key else self.shipped.get(key)


@pytest.fixture
def fixture_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(p7_reading, "FACTOR_TRANSFORMS", _Transforms(p7_reading.FACTOR_TRANSFORMS))
    monkeypatch.setattr(p7_reading, "EXCHANGE", EXCHANGE)


def test_the_default_preflight_finds_every_partition_the_read_opens(
    corpus: tuple[Path, Any], fixture_facts: None
) -> None:
    root, panel = corpus
    registered = body(panel)

    assert p7_reading._panel_preflight(root, WINDOW)(registered) == ()
    # The same catalogue, a window one year later: the year before it (2027) is held, its own
    # (2028) is not, and each dataset says so.
    later = p7_windows.DiagnosticWindow(
        name="later", first=date(2028, 1, 3), last=date(2028, 1, 21), ceiling_year=2028
    )
    problems = p7_reading.preflight_problems(
        p7_windows.WindowCappedStore(root / "panel", window=later),
        window=later,
        datasets=p7_reading._preflight_datasets(BOOK),
    )
    assert problems and all("[2028]" in problem for problem in problems)


def test_the_default_measure_reads_a_generated_panel_and_logs_what_it_read(
    corpus: tuple[Path, Any], fixture_facts: None
) -> None:
    root, panel = corpus
    registered = body(panel)

    document = p7_reading._panel_measure(root)(registered, WINDOW, BINDING)

    assert document["kind"] == p7_diagnostics.READ_ONCE_KIND
    assert document["binding"] == BINDING
    read = document["provenance"]["partitions_read"]
    assert read
    plain = PanelStore(root / "panel")
    for item in read:
        assert item["content_hash"] is not None
        assert item["content_hash"] == plain.partition_content_hash(item["dataset"], item["year"])
    datasets = {item["dataset"] for item in read}
    assert {"trade_cal", "daily", "adj_factor", "namechange"} <= datasets
    assert {item["year"] for item in read} <= {2026, 2027}
    block: Mapping[str, Any] = document["summary"]["composite"]["h2"]
    assert block["read"] > 0, block["gaps"]
    assert block["ew_minus_worst_decile"]["n"] > 0
    assert document["plan"]["sessions"] and document["plan"]["first"] == "2027-01-04"
    rule = p7_reading.StopRule(
        score="composite",
        horizon=2,
        minimum_periods=1,
        any_of=(p7_reading.StopClause(metric="ew_minus_worst_decile", mean_at_or_below=-1.0),),
    )
    evaluated = p7_reading.evaluate_stop_rule(rule, document)
    assert evaluated["clauses"][0]["n"] == block["ew_minus_worst_decile"]["n"]
    text = "\n".join(p7_reading.reading_lines({**document, "stop_rule": evaluated}, NAMED))
    assert "in-sample" not in text and "in-sample" not in json.dumps(document)
    assert f"  rank_ic power {p7_diagnostics.READ_ONCE_POWER_WORDING}: sd " in text
