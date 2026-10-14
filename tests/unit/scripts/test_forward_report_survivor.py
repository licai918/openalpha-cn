"""`scripts/forward_report.py` for a survivor registration (`V2-P7-018`, Part E task E4).

A registration whose configuration names `kind: "survivor_book/v1"` is dispatched right after
the report's admission (`_survivor_of`, through `daily_selection.survivor_registration`) to the
survivor forward report (`_survivor_forward_report`): the pre-registered protocol read from the
registration's settings, the records the prediction store witnesses, the late-record check run
over every one of them, the prediction layer at each pre-registered horizon, the paper book's
record and the relative return, rendered as one `survivor_forward_report/v1` document
(`strategy_survivor_forward.survivor_forward_document`) and written to
`RT/reports/forward-survivor-<date>.json`. A P6 registration (no `kind`) runs the P6 report
exactly as before -- `test_forward_report.py` runs unmodified and is the measurement of that --
and loads none of the survivor modules (a fresh interpreter, below).

## The worlds

- **Three evenings.** `test_daily_selection_survivor_day.py`'s scripted market (120 main-board
  securities, every weekday of 2026), seeded once through 3 April by the real `panel build`, and
  three survivor evenings -- 6, 7 and 8 April, the first three after the epoch -- run through
  `daily_selection.main` once per module, under a registration whose settings carry the test's
  protocol. Every test copies the runtime directory. The records are the daily command's own,
  with the provenance it wrote, so the late-record check here is the real one: it scores each
  day again from the store. Nothing has matured on 9 April.
- **Matured records.** The same market built once through 2 June by one `panel build`, and
  twenty-five records -- ordinals 1 to 25, 6 April to 8 May -- filed straight into the prediction
  store with `survivor_declaration` and hand-chosen scores, as the daily command files them
  (on time, at 18:30), instead of running twenty-five days. Their h=20 labels are the shared
  reader's, on the market's own bars. They have no provenance, so the late-record check is
  replaced by `ScriptedCheck`, which answers from a table and writes down how it was built; the
  check itself is `test_strategy_survivor_check.py`'s, and the three-evening world runs it.

The protocol values are test values (a window of two periods, so a test can appear on two
months of records); they are not the Part E design's recommendations, which are decided at the
`V2-P7-006` checkpoint. Nothing reaches the network.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
from research_repo import commit_file, git, head
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import cli, strategy_survivor_check
from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.domain.alpha_model import AlphaModelArtifact, Prediction, PredictionBatch
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.model_view import LabelReach, OutcomeLabels
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_index_prices,
    load_trading_calendar,
    session_publication_instant,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import RegisteredConfiguration
from openalpha_cn.strategy_survivor_check import SurvivorRecordVerdict
from openalpha_cn.strategy_survivor_forward import (
    DESCRIPTIVE_WORDING,
    NO_VERDICT_WORDING,
    PREDICTION_LAYER_WORDING,
    SURVIVOR_FORWARD_REPORT_KIND,
    WitnessedRecord,
    record_outcome,
)
from openalpha_cn.strategy_survivor_market import PROTOCOL_BENCHMARK, SessionOrdinals
from openalpha_cn.strategy_survivor_morning import (
    MORNING_FILE_SUFFIX,
    MORNING_SCHEMA,
    MORNING_WORDING,
)
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    SURVIVOR_PROVENANCE_DIRECTORY,
    survivor_declaration,
    survivor_feature_ids,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
daily = tds.daily
forward_report = tds._script("forward_report", ROOT / "scripts")
SHANGHAI = daily.SHANGHAI

PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 2,
    "bootstrap_samples": 1000,
    "random_seed": 11,
    "q": 0.1,
}
"""Test values only (module docstring)."""


def settings_with(**changes: Any) -> dict[str, Any]:
    """The registration's settings: the P6 measurement settings and the test's protocol."""
    return {
        **tds.grid.protocol_settings(),
        "survivor_forward_protocol": {**PROTOCOL, **changes},
    }


CONFIG: Final[dict[str, Any]] = survivor_day.CONFIG
"""`test_daily_selection_survivor_day`'s survivor configuration: epoch 3 April."""
BOOK = survivor_day.BOOK
EPOCH: Final[date] = survivor_day.EPOCH
EVENINGS: Final[tuple[date, ...]] = (survivor_day.DAY, survivor_day.NEXT, survivor_day.THIRD)
"""6, 7 and 8 April: ordinals 1, 2 and 3."""
REPORT_AS_OF: Final[datetime] = datetime(2026, 4, 9, 10, 0, tzinfo=SHANGHAI)
"""After 8 April's registration cutoff (09:15 on the 9th): the three evenings' sessions closed."""
FORBIDDEN: Final = re.compile(
    r"\b(significant|confirmed|alpha|effective|outperform)", flags=re.IGNORECASE
)
"""The words a report never prints before its pre-registered window (design section 2)."""


def report_name(as_of: datetime) -> str:
    return f"forward-survivor-{as_of.astimezone(SHANGHAI).date().isoformat()}.json"


# --- repositories and worlds -------------------------------------------------------------------


def _repository(
    root: Path, config: Mapping[str, Any], *, settings: Mapping[str, Any] | None
) -> tuple[Path, Path]:
    """`test_daily_selection._repository`, with the registration's settings named (`None`: the
    P6 measurement settings alone, which carry no forward protocol)."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--template=")
    for name in tds.BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(tds.LOCK if name == "uv.lock" else "RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    (repo / ".python-version").write_text(f"{tds.RUNNING_PYTHON}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    git(repo, "add", ".python-version", ".gitignore", ".gitattributes")
    git(repo, "commit", "-q", "-m", "initial", at=tds.COMMITTED - timedelta(days=1))
    registration = repo / "docs" / "research" / "p6-registration.json"
    tds.registry.register(
        config, tds.CRITERIA, registration, code_commit=head(repo), settings=settings
    )
    commit_file(repo, registration, "register", at=tds.COMMITTED)
    return repo, registration


def _daily(world: tds.World, day: date) -> tuple[int, str, str]:
    """`day` through `daily_selection.main` as the scheduler runs it, at its 18:30."""
    at = survivor_day.evening_of(day)
    world.market.today = day
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", at.isoformat(), "--json"]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = daily.main(arguments, clock=lambda: at + timedelta(minutes=5))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def evenings(tmp_path_factory: pytest.TempPathFactory, seeded: Path) -> tds.World:  # noqa: F811
    """The three evenings, run once (module docstring)."""
    root = tmp_path_factory.mktemp("survivor-evenings")
    repo, registration = _repository(root, CONFIG, settings=settings_with())
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    runtime = root / "runtime"
    shutil.copytree(seeded, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, repo, market)
        for day in EVENINGS:
            code, _out, err = _daily(world, day)
            assert code == 0, err
    return world


@pytest.fixture
def world(evenings: tds.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tds.World:
    """A copy of the three evenings' runtime directory, under their registration."""
    runtime = tmp_path / "runtime"
    shutil.copytree(evenings.runtime, runtime)
    tds._bind(monkeypatch, evenings.repo, evenings.market)
    return dataclasses.replace(evenings, runtime=runtime)


def _report(
    world: tds.World,
    capsys: pytest.CaptureFixture[str],
    *,
    as_of: datetime = REPORT_AS_OF,
    json_output: bool = False,
) -> tuple[int, str, str]:
    """The weekly report through `forward_report.main`, at `as_of`."""
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", as_of.isoformat()]
    if json_output:
        arguments.append("--json")
    code = forward_report.main(arguments)
    out, err = capsys.readouterr()
    return code, out, err


def _document(world: tds.World, as_of: datetime = REPORT_AS_OF) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (world.runtime / "reports" / report_name(as_of)).read_text(encoding="utf-8")
    )
    return body


def _journal(world: tds.World, day: date) -> tuple[Path, dict[str, Any]]:
    config_id = json.loads(world.registration.read_text(encoding="utf-8"))["config_id"]
    path = world.runtime / "daily_survivor" / config_id[:16] / f"{day.isoformat()}.json"
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return path, body


def _sections(document: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every section of the document that is an object, nested ones included."""
    for name, section in document.items():
        if isinstance(section, Mapping):
            yield name, section
            if name == "prediction_layer":
                for inner, layer in section.items():
                    if isinstance(layer, Mapping):
                        yield f"{name}.{inner}", layer


def _assert_the_wording_is_in_every_section(document: Mapping[str, Any]) -> None:
    sections = dict(_sections(document))
    assert {
        "protocol",
        "records",
        "prediction_layer",
        "paper_ledger",
        "relative",
        "cannot_be_evaluated_yet",
    } <= set(sections)
    for name, section in sections.items():
        assert section.get("wording") == SURVIVOR_WORDING, name
    for name, section in sections.items():
        if name.startswith("prediction_layer.h"):
            assert section["forecast_wording"] == PREDICTION_LAYER_WORDING, name
    assert document["wording"] == [SURVIVOR_WORDING, PREDICTION_LAYER_WORDING, NO_VERDICT_WORDING]
    assert document["integrity"] == daily.INTEGRITY


def _no_verdict_word(*texts: str) -> None:
    for text in texts:
        assert FORBIDDEN.search(text) is None, FORBIDDEN.search(text)


def _codes(document: Mapping[str, Any]) -> list[str]:
    return [item["code"] for item in document["cannot_be_evaluated_yet"]["items"]]


ALWAYS: Final[tuple[str, ...]] = (
    "g0_needs_research_and_validation_conclusions",
    "spent_window_2024_01_02_to_2026_09_24",
    "paper_equity_is_not_evidence",
    "t8_expected_numbers_are_optimistic",
    "ic_2021",
    "h5_ic_dispersion_unmeasured",
    "unlabelled_names_left_out",
    "no_null_signal_control",
)
"""What the report states it cannot evaluate whatever its records say (design section 6)."""


# --- the three evenings -----------------------------------------------------------------------


def test_three_evenings_report_counts_their_records_and_tests_nothing(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The survivor registration is dispatched, its three records witnessed and each checked
    for real -- scored again from the store, all three verified -- and nothing has matured on 9
    April: counts only, every record pending in the sample, "not tested: 0 of 2", no p-value
    and no verdict word anywhere. The paper book's record of the three evenings stands beside
    the prediction layer, and the file is `forward-survivor-<date>.json`, never P6's name."""
    code, out, err = _report(world, capsys)

    assert code == 0, err
    assert err == ""
    reports = sorted(path.name for path in (world.runtime / "reports").glob("forward*.json"))
    assert reports == [report_name(REPORT_AS_OF)]
    document = _document(world)
    assert document["kind"] == SURVIVOR_FORWARD_REPORT_KIND == "survivor_forward_report/v1"
    assert document["as_of"] == REPORT_AS_OF.isoformat()
    admitted = json.loads(world.registration.read_text(encoding="utf-8"))
    assert document["config_id"] == admitted["config_id"]
    assert document["family"] == SURVIVOR_FAMILY
    protocol = document["protocol"]
    assert {key: protocol[key] for key in PROTOCOL} == PROTOCOL
    assert (protocol["periods_matured"], protocol["window_satisfied"]) == (0, False)

    records = document["records"]
    assert records["witnessed"] == 3
    assert (records["first_session"], records["newest_session"]) == ("2026-04-06", "2026-04-08")
    assert records["through"] == "2026-04-08"
    assert records["missing_sessions"] == []
    assert records["checked"] == 3
    assert records["by_verdict"] == {"verified": 3, "unverifiable": 0, "refused": 0, "unchecked": 0}
    assert (records["unverifiable"], records["refused"]) == ([], [])

    for name, horizon, positions in (("h20", 20, 2), ("h5", 5, 8)):
        layer = document["prediction_layer"][name]
        assert (layer["horizon"], layer["witnessed"], layer["mature"], layer["immature"]) == (
            horizon,
            3,
            0,
            3,
        )
        assert layer["label_as_of"] == REPORT_AS_OF.isoformat()
        assert layer["included"] == 0
        assert layer["descriptive"]["wording"] == DESCRIPTIVE_WORDING
        sample = layer["non_overlapping"]
        assert sample["sampled"] == ["2026-04-06"]
        assert (sample["pending"], sample["gaps"], sample["values"]) == (["2026-04-06"], [], [])
        assert (sample["matured"], sample["window_positions"], sample["window_satisfied"]) == (
            0,
            positions,
            False,
        )
        assert sample["test"] is None
        assert sample["status"] == (f"not tested: 0 of {positions} pre-registered periods matured")
    family = document["prediction_layer"]["secondary_family"]
    assert family["headline"]["tests"] == []
    assert family["headline"]["status"].startswith("not applied")

    ledger = document["paper_ledger"]
    assert "refused" not in ledger
    assert ledger["coverage"]["evenings"] == 3
    assert ledger["coverage"]["last_settled"] == "2026-04-08"
    assert document["relative"]["windows_given"] == 0
    assert set(ALWAYS) | {"first_outcome_not_matured", "window_not_satisfied"} == set(
        _codes(document)
    )
    _assert_the_wording_is_in_every_section(document)
    text = (world.runtime / "reports" / report_name(REPORT_AS_OF)).read_text(encoding="utf-8")
    assert "p_value" not in text
    _no_verdict_word(text, out)

    lines = out.splitlines()
    assert lines[-1] == f"written to {world.runtime / 'reports' / report_name(REPORT_AS_OF)}"
    assert "    not tested: 0 of 2 pre-registered periods matured" in lines
    assert not any("one-sided p" in line for line in lines)
    order = [
        next(index for index, line in enumerate(lines) if line.startswith(head))
        for head in ("records:", "prediction layer", "paper ledger", "relative", "wording:")
    ]
    assert order == sorted(order)
    assert lines[-3] == daily.INTEGRITY
    assert lines[-2] == "this run: 3 records checked, 0 verdicts served from the verdict cache"


def test_a_rerun_with_the_same_as_of_writes_the_same_bytes_and_json_prints_the_document(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second run serves the three verdicts the first one kept (`this_run`, printed, never
    written) and writes the same bytes; `--json` prints the document."""
    code, _out, err = _report(world, capsys)
    assert code == 0, err
    path = world.runtime / "reports" / report_name(REPORT_AS_OF)
    first = path.read_bytes()

    code, out, err = _report(world, capsys, json_output=True)

    assert code == 0, err
    assert path.read_bytes() == first
    printed = json.loads(out)
    assert printed.pop("this_run") == {"checked": 3, "served_from_cache": 3}
    assert printed == json.loads(first)


def test_a_morning_file_beside_the_journals_is_not_read_and_the_report_is_the_same(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """`daily_selection.py --morning-recheck` files `<session>.morning.json` in the evening
    journals' own directory (`V2-P7-008`, D6 review Minor 6). The report reads the journals by
    their ISO names alone, so a morning file beside each of the three changes nothing: the report
    is produced, with the same bytes."""
    code, _out, err = _report(world, capsys)
    assert code == 0, err
    path = world.runtime / "reports" / report_name(REPORT_AS_OF)
    first = path.read_bytes()
    path.unlink()
    for day in EVENINGS:
        journal, body = _journal(world, day)
        morning = journal.with_name(f"{day.isoformat()}{MORNING_FILE_SUFFIX}")
        document = {
            "schema": MORNING_SCHEMA,
            "session": day.isoformat(),
            "as_of": (survivor_day.evening_of(day) + timedelta(hours=14)).isoformat(),
            "held": body["held"],
            "evening_snapshot_id": body["result"]["paper"]["snapshot_id"],
            "registration": body["registration"],
            "evening_read": {"summary": {}, "aged_zero": [], "verdicts": {}},
            "requests": {"namechange": 1},
            "differences": [],
            "tolerated": [],
            "notices": [],
            "holdings_compared": [],
            "wording": MORNING_WORDING,
        }
        morning.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    assert path.read_bytes() == first
    assert _document(world)["records"]["witnessed"] == 3


def test_a_record_with_no_provenance_is_refused_listed_and_the_report_still_produced(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """7 April's provenance gone: its record is refused (`no_provenance`), listed with its
    reason, left out of every reading and counted as excluded; the report is still written."""
    _path, journal = _journal(world, EVENINGS[1])
    record_id = journal["prediction"]["record_id"]
    provenance = world.runtime / SURVIVOR_PROVENANCE_DIRECTORY
    (provenance / f"{journal['prediction']['provenance']}.json").unlink()

    code, out, err = _report(world, capsys)

    assert code == 0, err
    records = _document(world)["records"]
    assert records["by_verdict"] == {"verified": 2, "unverifiable": 0, "refused": 1, "unchecked": 0}
    assert records["by_scope"]["no_provenance"] == 1
    [refused] = records["refused"]
    assert (refused["record_id"], refused["session"], refused["scope"]) == (
        record_id,
        "2026-04-07",
        "no_provenance",
    )
    assert "no provenance" in refused["reason"]
    layer = _document(world)["prediction_layer"]["h20"]
    assert layer["excluded"] == {"refused": 1, "unchecked": 0}
    assert any(line.startswith(f"  REFUSED {record_id}") for line in out.splitlines())


def test_a_record_bound_to_another_registration_is_never_witnessed_or_scored_again(
    world: tds.World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A survivor record of another registration, filed on time for 7 April, is not this
    registration's: it is not witnessed, so it is neither scored again nor refused, and it
    appears nowhere in the report; the check scores the three bound records."""
    store = FilePredictionStore(world.runtime / "predictions", clock=lambda: REPORT_AS_OF)
    _path, journal = _journal(world, EVENINGS[1])
    held = store.get(journal["prediction"]["record_id"])
    assert held is not None
    admitted = daily.admit_registration(world.registration, world.repo)
    other = dataclasses.replace(
        admitted.declared, config_id="rcf_99999999999999999999999a", registration_sha256="cd" * 32
    )
    foreign = _file(
        world,
        held.batch.model_copy(
            update={
                "artifact": held.batch.artifact.model_copy(
                    update={"declaration": survivor_declaration(BOOK, other)}
                )
            }
        ),
    )
    scored: list[str] = []
    recompute = strategy_survivor_check.recompute_survivor_batch

    def spied(store: PanelStore, record: PredictionRecord, **keywords: Any) -> PredictionBatch:
        scored.append(record.record_id)
        return recompute(store, record, **keywords)

    monkeypatch.setattr(strategy_survivor_check, "recompute_survivor_batch", spied)

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    assert foreign not in scored
    assert len(scored) == 3
    text = (world.runtime / "reports" / report_name(REPORT_AS_OF)).read_text(encoding="utf-8")
    assert foreign not in text
    document = _document(world)
    assert document["records"]["witnessed"] == 3
    assert document["records"]["by_scope"]["not_bound"] == 0


def test_a_paper_ledger_refusal_keeps_the_prediction_layer(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """7 April's journal names a snapshot the store does not hold for it: the paper record is
    refused by name, in its own two sections, and the report -- its prediction layer whole --
    is still produced, exit 0."""
    path, journal = _journal(world, EVENINGS[1])
    held = journal["result"]["paper"]["snapshot_id"]
    journal["result"]["paper"]["snapshot_id"] = held[:-1] + ("0" if held[-1] != "0" else "1")
    path.write_text(json.dumps(journal), encoding="utf-8")

    code, out, err = _report(world, capsys)

    assert code == 0, err
    document = _document(world)
    for section in ("paper_ledger", "relative"):
        assert document[section]["wording"] == SURVIVOR_WORDING
        assert "2026-04-07" in document[section]["refused"]
    assert document["records"]["by_verdict"]["verified"] == 3
    assert document["prediction_layer"]["h20"]["witnessed"] == 3
    assert any(line.startswith("paper ledger") and "refused" in line for line in out.splitlines())


def test_two_bound_records_for_one_session_refuse_the_report(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second bound record for 7 April, filed before its cutoff: a session has one record, and
    the report does not choose between them -- exit 1, the session named, nothing written."""
    store = FilePredictionStore(world.runtime / "predictions", clock=lambda: REPORT_AS_OF)
    _path, journal = _journal(world, EVENINGS[1])
    held = store.get(journal["prediction"]["record_id"])
    assert held is not None
    _file(
        world,
        held.batch.model_copy(
            update={"predicted_at": held.batch.predicted_at + timedelta(minutes=9)}
        ),
    )

    code, out, err = _report(world, capsys)

    assert code == 1
    assert out == ""
    assert "2026-04-07 carries 2 records" in err
    assert not (world.runtime / "reports" / report_name(REPORT_AS_OF)).exists()


def test_a_report_in_a_year_whose_calendar_is_not_built_is_refused_by_name(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A January report before the new year's calendar is stored: the book counts its sessions
    on the calendar through the report's date, so the report is refused, naming the year and
    what builds it -- exit 1, nothing written."""
    january = datetime(2027, 1, 6, 10, 0, tzinfo=SHANGHAI)

    code, out, err = _report(world, capsys, as_of=january)

    assert code == 1
    assert out == ""
    assert "calendar of 2027 is not built" in err
    assert "the daily command's panel update builds it" in err
    assert not (world.runtime / "reports" / report_name(january)).exists()


@pytest.mark.parametrize("fault", ["record gone", "journal names another"])
def test_a_journal_naming_a_record_the_store_does_not_witness_refuses_the_report(
    world: tds.World, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    """The store is the witness: 7 April's journal says its evening filed a record, and the
    store holds none for that session -- the record's document gone -- or holds another. The
    journal and the store disagree, neither is preferred, and the report is refused naming the
    session: exit 1, nothing written."""
    path, journal = _journal(world, EVENINGS[1])
    record_id = journal["prediction"]["record_id"]
    if fault == "record gone":
        (world.runtime / "predictions" / f"{record_id}.json").unlink()
    else:
        journal["prediction"]["record_id"] = "prd_" + "0" * (len(record_id) - 4)
        path.write_text(json.dumps(journal), encoding="utf-8")

    code, out, err = _report(world, capsys)

    assert code == 1
    assert out == ""
    assert "2026-04-07" in err
    assert "the store is the witness" in err
    assert not (world.runtime / "reports" / report_name(REPORT_AS_OF)).exists()


# --- a week on: the relative return reads the primary layer -----------------------------------

WEEK_BUILD: Final[datetime] = datetime(2026, 4, 15, 9, 0, tzinfo=SHANGHAI)
"""A panel build after the three evenings: 14 April has published, 15 April has not."""
WEEK_AS_OF: Final[datetime] = datetime(2026, 4, 15, 10, 0, tzinfo=SHANGHAI)
"""After 14 April's cutoff: 6 April's h=5 window, 7 April's close to 14 April's, has matured, and
no h=20 one has."""


@pytest.fixture(scope="module")
def week(tmp_path_factory: pytest.TempPathFactory, evenings: tds.World) -> tds.World:
    """The three evenings' runtime directory with its panel built on through 14 April by one
    real `panel build` -- no evening ran after 8 April, so the paper book and the records stop
    there -- on a market of its own (the scripted market is the same on every instance)."""
    runtime = tmp_path_factory.mktemp("survivor-week") / "runtime"
    shutil.copytree(evenings.runtime, runtime)
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    market.today = WEEK_BUILD.date()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", "2026"]
        arguments += ["--as-of", WEEK_BUILD.isoformat(), "--exchange", "SSE", "--json"]
        for target in survivor_day.SEED_TARGETS:
            arguments += ["--dataset", target]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()
    return dataclasses.replace(evenings, runtime=runtime, market=market)


def test_the_relative_return_reads_the_primary_layers_sampled_periods_never_a_secondarys(
    week: tds.World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The three evenings reported on 15 April, the panel built on through the 14th: the h=5
    outcome of 6 April has matured -- its window runs from 7 April's close to 14 April's -- and
    no h=20 one has. The relative return's windows are the primary layer's sampled periods (none
    yet), never the h=5 layer's (one): the paper record, the real book of the three evenings, is
    given none."""
    runtime = tmp_path / "runtime"
    shutil.copytree(week.runtime, runtime)
    tds._bind(monkeypatch, week.repo, week.market)
    world = dataclasses.replace(week, runtime=runtime)

    code, _out, err = _report(world, capsys, as_of=WEEK_AS_OF)

    assert code == 0, err
    document = _document(world, WEEK_AS_OF)
    layers = document["prediction_layer"]
    assert _period_sessions(layers["h5"]["non_overlapping"]) == ["2026-04-06"]
    assert layers["h20"]["non_overlapping"]["values"] == []
    assert "refused" not in document["paper_ledger"]
    assert document["records"]["by_verdict"]["refused"] == 0
    assert document["paper_ledger"]["coverage"]["evenings"] == 3
    assert "refused" not in document["relative"]
    assert document["relative"]["windows_given"] == 0


def _file(world: tds.World, batch: PredictionBatch) -> str:
    """`batch` filed on time: the store's custody clock a minute after its `predicted_at`."""
    store = PanelStore(world.runtime / "panel")
    day = batch.as_of.astimezone(SHANGHAI).date()
    calendar = daily._outcome_calendar(store, "SSE", day, batch.predicted_at)
    filed = FilePredictionStore(
        world.runtime / "predictions", clock=lambda: batch.predicted_at + timedelta(minutes=1)
    ).put(batch=batch, calendar=calendar, zone=SHANGHAI)
    return filed.record.record_id


# --- the protocol ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        (None, "carry no survivor_forward_protocol block"),
        (settings_with(window_periods=0), "window_periods"),
        (
            {**tds.grid.protocol_settings(), "survivor_forward_protocol": {**PROTOCOL, "k": 3}},
            "k: Extra inputs are not permitted",
        ),
    ],
    ids=["missing", "invalid", "extra-key"],
)
def test_a_registration_without_a_valid_forward_protocol_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
    settings: Mapping[str, Any] | None,
    named: str,
) -> None:
    """The protocol is the registration's, frozen at `V2-P7-006`: a report without one -- or
    with one that does not read -- is refused by name, exit 1, before anything is read or
    written; not with P6's "names no start date"."""
    repo, registration = _repository(tmp_path, CONFIG, settings=settings)
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    runtime = tmp_path / "runtime"
    shutil.copytree(seeded, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)

    code, out, err = _report(world, capsys)

    assert code == 1
    assert out == ""
    assert named in err
    assert "start date" not in err
    assert not (runtime / "reports").exists()


# --- matured records ---------------------------------------------------------------------------

MATURED_BUILD: Final[datetime] = datetime(2026, 6, 3, 9, 0, tzinfo=SHANGHAI)
"""The matured market's build: 2 June has published, 3 June has not."""
MATURED_AS_OF: Final[datetime] = datetime(2026, 6, 3, 10, 0, tzinfo=SHANGHAI)
"""After 2 June's cutoff (09:15 on the 3rd): the sample runs through 2 June, ordinal 42."""
RECORDED: Final[tuple[int, ...]] = tuple(range(1, 26))
"""The ordinals filed: 6 April .. 8 May."""


@pytest.fixture(scope="module")
def matured_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The scripted market through 2 June, built once through the real `panel build`."""
    runtime = tmp_path_factory.mktemp("survivor-matured") / "runtime"
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    market.today = MATURED_BUILD.date()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", "2026"]
        arguments += ["--as-of", MATURED_BUILD.isoformat(), "--exchange", "SSE", "--json"]
        for target in survivor_day.SEED_TARGETS:
            arguments += ["--dataset", target]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()
    return runtime


def calendar_of(runtime: Path) -> TradingCalendar:
    return load_trading_calendar(
        PanelStore(runtime / "panel"), exchange="SSE", years=(2026,), as_of=MATURED_AS_OF
    )


def score_of(index: int, ordinal: int) -> float:
    """A record's score for the `index`-th security: a permutation of 0..119 that moves with the
    session, so no two names of one record tie and the deciles change from day to day."""
    return ((index * 37 + ordinal * 11) % 120) / 120


def batch_of(day: date, ordinal: int, registered: RegisteredConfiguration) -> PredictionBatch:
    """A survivor record of `day`, shaped as `survivor_batch` files one, scored by `score_of`."""
    published = session_publication_instant(day)
    features = survivor_feature_ids(BOOK)
    scores = {code: score_of(index, ordinal) for index, code in enumerate(survivor_day.SECURITIES)}
    return PredictionBatch(
        as_of=published,
        predicted_at=survivor_day.evening_of(day),
        artifact=AlphaModelArtifact(
            declaration=survivor_declaration(BOOK, registered),
            feature_ids=features,
            training_cutoff=published,
            training_example_count=len(features) * len(scores),
            parameters=tuple((feature, 1.0 / len(features)) for feature in features),
        ),
        predictions=tuple(
            Prediction(ts_code=code, score=score) for code, score in sorted(scores.items())
        ),
    )


@dataclasses.dataclass
class Matured:
    world: tds.World
    registered: RegisteredConfiguration
    ordinals: SessionOrdinals
    records: dict[int, str]
    """Record id by ordinal."""


def _matured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    panel: Path,
    *,
    settings: Mapping[str, Any] | None = None,
    recorded: Sequence[int] = RECORDED,
) -> Matured:
    repo, registration = _repository(
        tmp_path, CONFIG, settings=settings_with() if settings is None else settings
    )
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, repo, market)
    runtime = tmp_path / "runtime"
    shutil.copytree(panel, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    registered = daily.admit_registration(registration, repo).declared
    ordinals = SessionOrdinals(calendar_of(runtime), EPOCH)
    records = {
        ordinal: _file(world, batch_of(ordinals.day_of(ordinal), ordinal, registered))
        for ordinal in recorded
    }
    return Matured(world=world, registered=registered, ordinals=ordinals, records=records)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> ScriptedCheck:
    check = ScriptedCheck()
    monkeypatch.setattr(strategy_survivor_check, "SurvivorRecordCheck", check)
    return check


class ScriptedCheck:
    """Stands in for `SurvivorRecordCheck` (module docstring): built as the report builds the
    check -- every keyword written down -- and asked of each record, it answers verified unless
    `table` names the record's session."""

    def __init__(self) -> None:
        self.table: dict[date, tuple[str, str | None, tuple[str, ...]]] = {}
        self.built: list[dict[str, Any]] = []
        self.asked: list[str] = []

    def __call__(self, store: PanelStore, **keywords: Any) -> ScriptedCheck:
        self.built.append(keywords)
        return self._Bound(self)

    @dataclasses.dataclass
    class _Bound:
        check: ScriptedCheck

        def __call__(self, record: PredictionRecord) -> SurvivorRecordVerdict:
            self.check.asked.append(record.record_id)
            session = record.batch.as_of.astimezone(SHANGHAI).date()
            verdict, scope, changes = self.check.table.get(session, ("verified", None, ()))
            return SurvivorRecordVerdict(
                record_id=record.record_id,
                session=session,
                verdict=verdict,  # type: ignore[arg-type]
                scope=scope,  # type: ignore[arg-type]
                changes=changes,
                reason=None if verdict == "verified" else f"scripted: {verdict} by the table",
            )


def _period_sessions(sample: Mapping[str, Any]) -> list[str]:
    return [period["session"] for period in sample["values"]]


def _one_sided(values: Sequence[float]) -> float:
    """The protocol's one-sided p of a two-value sample, by hand: of the four sign patterns the
    observed one and its negation reach `|a + b|` always and the other two only when `a` and `b`
    differ in sign, so the two-sided p is 1/2 or 1; the one-sided p is half of it for a positive
    mean and one less half of it otherwise."""
    a, b = values
    two_sided = 1.0 if (a > 0) != (b > 0) else 0.5
    return two_sided / 2 if a + b > 0 else 1 - two_sided / 2


def test_matured_records_put_the_labels_the_sample_its_gaps_and_the_test_on_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    matured_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """Twenty-five records on 3 June: at h=20 the records of ordinals 1 to 21 have matured
    (their exits, ordinal 22 to 42, published by the 2nd), the sample is ordinals 1, 21 and 41
    -- 41 a gap, no record, after the window -- and with a window of two periods the test is made
    once, over its two positions 1 and 21, each number of a period the shared reader's at the
    report's `as_of`. At h=5 the window is the eight positions over the same 40 sessions, three
    of them without a record: tested on five. The secondary
    family is corrected once the window is satisfied (Benjamini-Yekutieli, arbitrary
    dependence); the check was built as the registration says and asked of every record, mature
    or not; the paper book holds nothing, so its record is refused by name and the prediction
    layer stands."""
    matured = _matured(tmp_path, monkeypatch, matured_panel)
    world, ordinals = matured.world, matured.ordinals
    day = ordinals.day_of

    code, out, err = _report(world, capsys, as_of=MATURED_AS_OF)

    assert code == 0, err
    [built] = scripted.built
    assert built["book"] == BOOK
    assert (built["exchange"], built["min_coverage"], built["max_carry_sessions"]) == (
        "SSE",
        0.5,
        250,
    )
    assert built["registered"] == matured.registered
    assert isinstance(built["verdicts"], strategy_survivor_check.FileSurvivorVerdicts)
    assert sorted(scripted.asked) == sorted(matured.records.values())
    document = _document(world, MATURED_AS_OF)
    records = document["records"]
    assert records["witnessed"] == 25
    assert records["through"] == day(42).isoformat() == "2026-06-02"
    assert records["missing_sessions"] == [day(o).isoformat() for o in range(26, 43)]

    layer = document["prediction_layer"]["h20"]
    assert (layer["witnessed"], layer["mature"], layer["immature"], layer["included"]) == (
        25,
        21,
        4,
        21,
    )
    sample = layer["non_overlapping"]
    assert sample["sampled"] == [day(1).isoformat(), day(21).isoformat(), day(41).isoformat()]
    assert sample["gaps"] == [{"session": day(41).isoformat(), "reason": "no_record"}]
    assert _period_sessions(sample) == [day(1).isoformat(), day(21).isoformat()]
    assert (sample["matured"], sample["window_satisfied"]) == (2, True)
    assert sample["status"] == (
        "tested on 2 of 2 positions (0 no record, 0 refused, 0 unverifiable excluded in the "
        "sensitivity)"
    )
    expected = _independent_outcome(world, matured, ordinal=1, horizon=20)
    first = sample["values"][0]
    assert first["record_id"] == matured.records[1]
    for quantity in ("rank_ic", "investable_ew", "survivors_minus_ew", "csi500"):
        assert first[quantity] == getattr(expected, quantity), quantity
    tests = {test["metric"]: test for test in sample["test"]}
    assert [(test["metric"], test["role"]) for test in sample["test"]] == [
        ("rank_ic", "primary"),
        ("survivors_minus_ew", "secondary"),
        ("ew_minus_worst_decile", "secondary"),
    ]
    for metric, test in tests.items():
        values = [period[metric] for period in sample["values"]]
        assert test["values"] == 2
        assert test["exact"] is True
        assert (
            test["two_sided_p_value"]
            == sign_flip_test(tuple(values), bootstrap_samples=1000, random_seed=11).p_value
        )
        assert test["one_sided_p_value"] == pytest.approx(_one_sided(values))

    five = document["prediction_layer"]["h5"]["non_overlapping"]
    assert five["sampled"] == [day(o).isoformat() for o in range(1, 42, 5)]
    assert _period_sessions(five) == [day(o).isoformat() for o in (1, 6, 11, 16, 21)]
    assert [(test["metric"], test["values"]) for test in five["test"]] == [("rank_ic", 5)]
    assert five["window_sessions"] == [day(o).isoformat() for o in range(1, 37, 5)]
    assert five["status"] == (
        "tested on 5 of 8 positions (3 no record, 0 refused, 0 unverifiable excluded in the "
        "sensitivity)"
    )

    family = document["prediction_layer"]["secondary_family"]
    assert family["family"] == ["h20.survivors_minus_ew", "h20.ew_minus_worst_decile", "h5.rank_ic"]
    assert (family["dependence"], family["q"], family["family_size"]) == ("arbitrary", 0.1, 3)
    p_values = {
        "h20.survivors_minus_ew": tests["survivors_minus_ew"]["one_sided_p_value"],
        "h20.ew_minus_worst_decile": tests["ew_minus_worst_decile"]["one_sided_p_value"],
        "h5.rank_ic": five["test"][0]["one_sided_p_value"],
    }
    corrected = {item["hypothesis_id"]: item for item in family["headline"]["tests"]}
    assert set(corrected) == set(p_values)
    for name, q_value in _by_by_hand(p_values, q=0.1).items():
        assert corrected[name]["one_sided_p_value"] == p_values[name]
        assert corrected[name]["q_value"] == pytest.approx(q_value)
        assert corrected[name]["below_q"] is (q_value <= 0.1)
    assert document["protocol"]["periods_matured"] == 2
    assert document["protocol"]["window_satisfied"] is True
    assert set(_codes(document)) == set(ALWAYS)

    for section in ("paper_ledger", "relative"):
        assert "no snapshot" in document[section]["refused"]
    _assert_the_wording_is_in_every_section(document)
    text = (world.runtime / "reports" / report_name(MATURED_AS_OF)).read_text(encoding="utf-8")
    _no_verdict_word(text, out)
    assert any(line.startswith("    rank_ic (primary): 2 periods") for line in out.splitlines())


def _independent_outcome(world: tds.World, matured: Matured, *, ordinal: int, horizon: int) -> Any:
    """The record of `ordinal` labelled by a reader built here, not by the report."""
    store = PanelStore(world.runtime / "panel")
    labels = OutcomeLabels(store, LabelReach(as_of=MATURED_AS_OF, years=(2026,), exchange="SSE"))
    levels = load_index_prices(
        store, labels.calendar, years=(2026,), as_of=MATURED_AS_OF, max_staleness=None
    )[PROTOCOL_BENCHMARK]
    prediction_store = FilePredictionStore(
        world.runtime / "predictions", clock=lambda: MATURED_AS_OF
    )
    record = prediction_store.get(matured.records[ordinal])
    assert record is not None
    witnessed = WitnessedRecord(
        session=matured.ordinals.day_of(ordinal),
        ordinal=ordinal,
        record_id=record.record_id,
        record=record,
    )
    return record_outcome(
        labels, witnessed, horizon=horizon, book=BOOK, csi500=levels, as_of=MATURED_AS_OF
    )


def _by_by_hand(p_values: Mapping[str, float], *, q: float) -> dict[str, float]:
    """Benjamini-Yekutieli's adjusted p-values written out: rank the m p-values ascending, take
    `H_m * m * p_(j) / j` and its running minimum from the largest rank down, capped at one."""
    m = len(p_values)
    harmonic = sum(1 / index for index in range(1, m + 1))
    ranked = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = float("inf")
    for rank in range(m, 0, -1):
        name, p_value = ranked[rank - 1]
        running = min(running, harmonic * m * p_value / rank)
        adjusted[name] = min(1.0, running)
    assert 0 < q < 1
    return adjusted


def test_a_refused_record_in_the_sample_at_k_compromises_the_test_and_an_unverifiable_one_is_listed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    matured_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """Ordinal 6 refused and ordinal 11 UNVERIFIABLE. At h=5 the refused record is a gap among
    the window's eight positions (1, 6, .., 36), the sample the test is made over: the test is
    printed "compromised (1 refused)". The UNVERIFIABLE record stays in the headline and is
    listed with its changes; the sensitivity turns its session into a gap and tests the same
    window less it. The refused record is in no reading: the h=20 headline includes 20 of its 21
    mature records, the sensitivity 19."""
    day = SessionOrdinals(calendar_of(matured_panel), EPOCH).day_of
    scripted.table = {
        day(6): ("refused", "recompute_differs", ()),
        day(11): ("unverifiable", "recompute_differs", ("daily 2026: census moved",)),
    }
    matured = _matured(tmp_path, monkeypatch, matured_panel)

    code, out, err = _report(matured.world, capsys, as_of=MATURED_AS_OF)

    assert code == 0, err
    document = _document(matured.world, MATURED_AS_OF)
    records = document["records"]
    assert records["by_verdict"] == {
        "verified": 23,
        "unverifiable": 1,
        "refused": 1,
        "unchecked": 0,
    }
    [refused] = records["refused"]
    assert (refused["record_id"], refused["scope"]) == (matured.records[6], "recompute_differs")
    [unverifiable] = records["unverifiable"]
    assert (unverifiable["record_id"], unverifiable["changes"]) == (
        matured.records[11],
        ["daily 2026: census moved"],
    )
    assert records["shares"]["overall"] == {
        "checked": 25,
        "unverifiable": 1,
        "refused": 1,
        "unverifiable_share": 1 / 25,
        "refused_share": 1 / 25,
    }

    five = document["prediction_layer"]["h5"]
    headline = five["non_overlapping"]
    assert {"session": day(6).isoformat(), "reason": "refused"} in headline["gaps"]
    assert _period_sessions(headline)[:2] == [day(1).isoformat(), day(11).isoformat()]
    assert headline["refused_in_sample"] == 1
    assert headline["status"] == (
        "compromised (1 refused): tested on 4 of 8 positions (3 no record, 1 refused, 1 "
        "unverifiable excluded in the sensitivity)"
    )
    sensitivity = five["excluding_unverifiable"]["non_overlapping"]
    assert {"session": day(11).isoformat(), "reason": "unverifiable"} in sensitivity["gaps"]
    assert _period_sessions(sensitivity)[:2] == [day(1).isoformat(), day(16).isoformat()]
    assert sensitivity["status"] == (
        "compromised (1 refused): tested on 3 of 8 positions (3 no record, 1 refused, 1 "
        "unverifiable excluded in the sensitivity)"
    )

    twenty = document["prediction_layer"]["h20"]
    assert twenty["excluded"] == {"refused": 1, "unchecked": 0}
    assert (twenty["included"], twenty["excluding_unverifiable"]["included"]) == (20, 19)
    assert twenty["descriptive"]["means"]["rank_ic"]["n"] <= 20
    assert twenty["non_overlapping"]["refused_in_sample"] == 0
    assert twenty["non_overlapping"]["status"] == (
        "tested on 2 of 2 positions (0 no record, 0 refused, 0 unverifiable excluded in the "
        "sensitivity)"
    )
    lines = out.splitlines()
    refused_line = lines.index(next(line for line in lines if line.startswith("  REFUSED")))
    unverifiable_line = lines.index(
        next(line for line in lines if line.startswith("  UNVERIFIABLE"))
    )
    layer_line = lines.index(next(line for line in lines if line.startswith("prediction layer")))
    assert refused_line < layer_line and unverifiable_line < layer_line
    assert "daily 2026: census moved" in lines[unverifiable_line]
    _no_verdict_word(out)


def test_no_secondary_test_is_shown_before_the_primary_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    matured_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """A window of three periods: h=20 has matured two of its positions (1 and 21; 41, with no
    record, exits at ordinal 62), so the one analysis is not made. h=5's window is the twelve
    positions over the same 60 sessions, eight of them matured: no test, no p-value, no
    correction anywhere."""
    matured = _matured(
        tmp_path, monkeypatch, matured_panel, settings=settings_with(window_periods=3)
    )

    code, out, err = _report(matured.world, capsys, as_of=MATURED_AS_OF)

    assert code == 0, err
    document = _document(matured.world, MATURED_AS_OF)
    twenty = document["prediction_layer"]["h20"]["non_overlapping"]
    assert (twenty["matured"], twenty["window_satisfied"], twenty["test"]) == (2, False, None)
    assert twenty["status"] == "not tested: 2 of 3 pre-registered periods matured"
    five = document["prediction_layer"]["h5"]["non_overlapping"]
    assert (five["matured"], five["window_positions"], five["window_satisfied"]) == (8, 12, False)
    assert (five["test"], five["status"]) == (
        None,
        "not tested: 8 of 12 pre-registered periods matured",
    )
    family = document["prediction_layer"]["secondary_family"]
    assert family["headline"]["tests"] == []
    assert "window_not_satisfied" in _codes(document)
    text = (matured.world.runtime / "reports" / report_name(MATURED_AS_OF)).read_text(
        encoding="utf-8"
    )
    assert "p_value" not in text
    assert not any("one-sided p" in line for line in out.splitlines())
    _no_verdict_word(text, out)


# --- a P6 report loads no survivor module ------------------------------------------------------

_IN_A_FRESH_INTERPRETER: Final[str] = r"""
import contextlib, io, json, sys
from pathlib import Path


def watched():
    return sorted(
        name
        for name in sys.modules
        if name == "openalpha_cn.backtest.paper"
        or name.startswith("openalpha_cn.strategy_survivor")
    )


import pytest
import test_forward_report as p6

form, root = sys.argv[1], Path(sys.argv[2]).resolve()
before = watched()
patch = pytest.MonkeyPatch()
out, err = io.StringIO(), io.StringIO()
try:
    if form == "p6":
        repo = root / "repo"
        repo.mkdir(parents=True)
        p6._git(repo, "init", "-q", "--template=")
        for relative in p6.BOUND:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            text = p6.tds.LOCK if relative == "uv.lock" else "# fixture\n"
            path.write_text(text, encoding="utf-8")
        (repo / ".python-version").write_text(f"{p6.RUNNING_PYTHON}\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
        p6._git(repo, "add", ".python-version", ".gitignore", *p6.BOUND)
        p6._git(repo, "commit", "-q", "-m", "seed", at=p6.CLOCK0)
        package = repo / "src" / "openalpha_cn" / "__init__.py"
        research = repo / "scripts" / "research"
        patch.setattr(p6.registry, "_imported_package", lambda: package)
        patch.setattr(
            p6.registry,
            "_imported_scripts",
            lambda: (research / "grid.py", research / "registry.py"),
        )
        patch.setattr(p6.daily, "installed_distributions", lambda: dict(p6.tds.LOCKED))
        work = root / "work"
        work.mkdir()
        runtime, admitted, _sessions, _days = p6.build_static_fixture(work, repo)
        registration, as_of = admitted.path, p6.READ_AT
    else:
        import test_daily_selection_dispatch as dispatch
        from daily_market import Market

        repo, registration = p6.tds._repository(root, dispatch.SURVIVOR_CONFIG)
        p6.tds._bind(patch, repo, Market(open_days=p6.tds.OPEN_2026))
        runtime, as_of = root / "runtime", p6.READ_AT
    arguments = ["--runtime-dir", str(runtime), "--registration", str(registration)]
    arguments += ["--repo", str(repo), "--as-of", as_of.isoformat(), "--json"]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = p6.forward_report.main(arguments)
finally:
    patch.undo()
print(json.dumps({"exit": code, "before": before, "after": watched(), "stderr": err.getvalue()}))
"""


def _in_a_fresh_interpreter(form: str, root: Path) -> dict[str, Any]:
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            str(ROOT / "tests" / "unit" / "scripts"),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    root.mkdir(parents=True)
    completed = subprocess.run(
        [sys.executable, "-c", _IN_A_FRESH_INTERPRETER, form, str(root)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return report


def test_a_p6_report_loads_neither_the_paper_book_nor_any_survivor_module(tmp_path: Path) -> None:
    """A P6 report, end to end, in a process that had loaded none of them: it still has none.
    `backtest/paper.py` installs a process-wide audit hook on import; a P6 report keeps no paper
    book and must not install it."""
    report = _in_a_fresh_interpreter("p6", tmp_path / "p6")

    assert report["exit"] == 0, report["stderr"]
    assert report["before"] == []
    assert report["after"] == []


def test_a_survivor_dispatch_loads_them_which_is_what_the_p6_probe_would_see(
    tmp_path: Path,
) -> None:
    """The control: the same probe sees the survivor modules and the paper module load once a
    registration names a `kind` -- here up to the dispatch and its refusal of a registration
    with no forward protocol -- so the P6 answer is a measurement, not a probe that cannot see."""
    report = _in_a_fresh_interpreter("survivor", tmp_path / "survivor")

    assert report["exit"] == 1, report["stderr"]
    assert "survivor_forward_protocol" in report["stderr"]
    assert report["before"] == []
    assert "openalpha_cn.backtest.paper" in report["after"]
    assert "openalpha_cn.strategy_survivor" in report["after"]


# --- E-FIX-B: the reads, the report's instant, the paper book's bound --------------------------
#
# Appended to E4's tests (the review of E4, `V2-P7-018`). Two more worlds, and what the worlds
# above leave out:
#
# - **Four short evenings.** The three evenings' market and seed, and four survivor evenings -- 6
#   to 9 April -- under a protocol whose primary horizon is one session and whose secondary is
#   two (test values, as above): a record matures inside the four evenings, so the paper book's
#   relative return has windows to compute on the snapshots the evenings wrote -- the 6 April
#   record's window runs from the 7 April close to the 8 April close, the 7 April record's from
#   the 8th to the 9th.
# - **New Year.** The same market from 14 December 2026, under a configuration whose epoch is that
#   day, built through 31 December with 2027's calendar beside it (as the daily command builds it
#   for a window that crosses New Year), and one record, on the year's last session.
# - **What the matured world does not exercise** (E4 review, minor 8). Its twenty-five records are
#   hand-built `PredictionBatch`es filed through `FilePredictionStore.put` -- shaped as
#   `survivor_batch` shapes a record and filed on time as the daily command files one, but made by
#   neither `survivor_batch` nor `daily_selection.register_prediction` -- and its late-record check
#   is `ScriptedCheck`. The records the real check scores again from the store are the evenings'
#   own, in the three- and four-evening worlds.
#
# The order the report reads in is the evening's write order reversed. An evening files its
# record, then its journal's `prediction` stage, then its ledger rows and its snapshot, then its
# journal's `paper` and `result`; the report lists the snapshots first (which fixes the paper
# book's head), then reads the journals, then scans the prediction store. The two probes below
# write an evening's later steps into the runtime directory while the report runs.


def _snapshot_path(world: tds.World, day: date) -> Path:
    [path] = sorted((world.runtime / "paper" / "snapshots").glob(f"{day.isoformat()}.pbs_*.json"))
    return path


def _ledger_ids(world: tds.World, day: date) -> list[str]:
    """The paper ledger's rows of `day`, by order id."""
    from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

    ledger = SQLitePortfolioLedger(world.runtime / "paper" / "ledger.sqlite3")
    return [row.order.order_id for row in ledger.list() if row.after.as_of == day]


def _ledger_sql(world: tds.World, statement: str, *rows: Sequence[Any]) -> None:
    """`statement` run on the paper ledger's file, once per row of parameters (once with none)."""
    import sqlite3
    from contextlib import closing

    path = world.runtime / "paper" / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executemany(statement, rows or [()])


def _evening_back_to(world: tds.World, day: date, *, filed: bool) -> Any:
    """`day`'s evening taken back on disk to just after its record was filed and its journal's
    `prediction` stage written (`filed`), or to before either, and what writes the rest of it.

    Taken back: the ledger rows and the snapshot of `day` gone, the journal without `paper` and
    `result` (and, not `filed`, without `prediction`, the record gone from the store). The
    returned call writes them back as the evening writes them: the record and the prediction
    stage, the ledger rows and the snapshot, then the journal's last two stages."""
    paper = world.runtime / "paper"
    whole = world.runtime.parent / f"paper-of-{day.isoformat()}"
    shutil.copytree(paper, whole)
    journal_path, journal = _journal(world, day)
    complete = journal_path.read_bytes()
    record = world.runtime / "predictions" / f"{journal['prediction']['record_id']}.json"
    record_bytes = record.read_bytes()
    rows = [(order_id,) for order_id in _ledger_ids(world, day)]
    if rows:
        _ledger_sql(world, "DELETE FROM portfolio_transitions WHERE order_id = ?", *rows)
    _snapshot_path(world, day).unlink()
    dropped = {"paper", "result"} if filed else {"prediction", "paper", "result"}
    kept = {name: value for name, value in journal.items() if name not in dropped}
    journal_path.write_text(json.dumps(kept, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not filed:
        record.unlink()

    def the_rest() -> None:
        record.write_bytes(record_bytes)
        shutil.rmtree(paper)
        shutil.copytree(whole, paper)
        journal_path.write_bytes(complete)

    return the_rest


def _during_the_check(monkeypatch: pytest.MonkeyPatch, then: Any) -> list[str]:
    """`then` called once, right after the late-record check's first verdict: the real check,
    wrapped. Returns the record ids it was asked of."""
    real = strategy_survivor_check.SurvivorRecordCheck
    asked: list[str] = []

    def built(store: PanelStore, **keywords: Any) -> Any:
        check = real(store, **keywords)

        def verdict_of(record: PredictionRecord) -> SurvivorRecordVerdict:
            verdict = check(record)
            asked.append(record.record_id)
            if len(asked) == 1:
                then()
            return verdict

        return verdict_of

    monkeypatch.setattr(strategy_survivor_check, "SurvivorRecordCheck", built)
    return asked


def _after_the_store_scan(monkeypatch: pytest.MonkeyPatch, then: Any) -> None:
    """`then` called right after the report has scanned the prediction store for its witnessed
    records (`strategy_survivor_forward.witnessed_survivor_records`, wrapped)."""
    from openalpha_cn import strategy_survivor_forward

    real = strategy_survivor_forward.witnessed_survivor_records

    def scanned(*arguments: Any, **keywords: Any) -> Any:
        witnessed = real(*arguments, **keywords)
        then()
        return witnessed

    monkeypatch.setattr(strategy_survivor_forward, "witnessed_survivor_records", scanned)


def test_an_evening_that_commits_its_paper_book_during_the_check_is_left_out_of_the_record_whole(
    world: tds.World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4 review, important 1 (a). When the report starts, 8 April's evening has filed its record
    and its journal's prediction stage; it commits its ledger rows, its snapshot and its journal's
    last two stages while the report checks the records. The snapshots were listed first, so the
    paper book's head is 7 April and 8 April is left out of the paper record whole -- no refused
    day, no ledger row of a session the history does not hold -- while the prediction layer
    witnesses its record. Read the other way round (the journals first, the snapshots after the
    check) the head would be 8 April with a journal that has no result: a day refused that was
    not."""
    asked = _during_the_check(monkeypatch, _evening_back_to(world, EVENINGS[2], filed=True))

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    assert len(asked) == 3
    assert _snapshot_path(world, EVENINGS[2]).is_file()  # the evening committed, during the report
    document = _document(world)
    assert document["records"]["witnessed"] == 3
    ledger = document["paper_ledger"]
    assert "refused" not in ledger, ledger.get("refused")
    coverage = ledger["coverage"]
    assert (coverage["evenings"], coverage["last_settled"]) == (2, "2026-04-07")
    assert coverage["refused_days"] == []
    assert coverage["settled_sessions_without_document"] == []
    assert ledger["reconciliation"]["rows_of_unsettled_sessions"] == []


def test_a_record_filed_between_the_reads_cannot_make_a_journal_contradict_the_store(
    world: tds.World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4 review, important 1 (b). When the report starts, 8 April's evening has filed nothing;
    right after the report scans the prediction store, it files its record, its journal's
    prediction stage and all the rest. The journals were read before the store: none names 8
    April's record, which the store did not hold, and the report is produced -- 8 April missing,
    the paper book's head 7 April. Read the other way round (the store first, then the journals)
    the journal would name a record the scan never saw, and the report would be refused."""
    _after_the_store_scan(monkeypatch, _evening_back_to(world, EVENINGS[2], filed=False))

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    document = _document(world)
    records = document["records"]
    assert (records["witnessed"], records["newest_session"]) == (2, "2026-04-07")
    assert records["missing_sessions"] == ["2026-04-08"]
    coverage = document["paper_ledger"]["coverage"]
    assert (coverage["evenings"], coverage["last_settled"]) == (2, "2026-04-07")
    assert coverage["refused_days"] == []


@pytest.mark.parametrize("instant", ["17:00", "18:30"], ids=["before-the-evening", "at-its-pin"])
def test_a_report_as_of_an_instant_reads_what_existed_at_that_instant(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck, instant: str
) -> None:
    """E4 review, minor 1 (and M2). Run after the three evenings, a report as of 17:00 on 8 April
    -- after its 16:30 publication, before its evening -- or as of 18:30, that evening's pinned
    instant, before its record was filed (at 18:35): the record, registered after the report's
    instant, is not witnessed; the journal is read as it stood then (pinned after 17:00; at
    18:30, without its prediction stage and the two after it, which were written after the
    record); and the paper book's head is 7 April although 8 April's snapshot is in the store --
    two records, through 7 April, two evenings, as a run at that instant would have said."""
    as_of = datetime.fromisoformat(f"{EVENINGS[2].isoformat()}T{instant}:00+08:00")

    code, _out, err = _report(world, capsys, as_of=as_of)

    assert code == 0, err
    document = _document(world, as_of)
    records = document["records"]
    assert (records["witnessed"], records["newest_session"], records["through"]) == (
        2,
        "2026-04-07",
        "2026-04-07",
    )
    assert records["checked"] == len(scripted.asked) == 2
    ledger = document["paper_ledger"]
    assert "refused" not in ledger, ledger.get("refused")
    coverage = ledger["coverage"]
    assert (coverage["evenings"], coverage["last_settled"], coverage["refused_days"]) == (
        2,
        "2026-04-07",
        [],
    )


def test_a_journal_pinned_after_the_reports_instant_is_not_read_at_all(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """E4 review, minor 1. A journal whose evening pinned its instant after the report's was not
    there at that instant, whatever it says now: as of 17:00 on 8 April, 8 April's journal -- here
    naming a record the store no longer holds, which a journal that is read would refuse the
    report for -- is not read, and the report is the two evenings' own. (It is this rule alone
    that keeps a later settle-only day, which files a result and no record, out of the paper
    book's head.)"""
    _path, journal = _journal(world, EVENINGS[2])
    (world.runtime / "predictions" / f"{journal['prediction']['record_id']}.json").unlink()
    as_of = datetime(2026, 4, 8, 17, 0, tzinfo=SHANGHAI)

    code, _out, err = _report(world, capsys, as_of=as_of)

    assert code == 0, err
    document = _document(world, as_of)
    assert (document["records"]["witnessed"], document["records"]["through"]) == (
        2,
        "2026-04-07",
    )
    coverage = document["paper_ledger"]["coverage"]
    assert (coverage["documents"], coverage["evenings"], coverage["last_settled"]) == (
        2,
        2,
        "2026-04-07",
    )


def test_a_report_as_of_an_instant_before_the_panels_newest_publication_is_refused(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """What `--as-of` cannot do, pinned (E4 review, minor 1). As of 17:00 on 7 April, run after
    8 April's evening stored 8 April's adjustment factors (knowable at its 16:30): the panel's
    readers judge a partition whole, so the labels cannot be read at that instant and the report
    is refused by name -- exit 1, nothing written -- rather than produced from a panel it could
    not have read then."""
    as_of = datetime(2026, 4, 7, 17, 0, tzinfo=SHANGHAI)

    code, out, err = _report(world, capsys, as_of=as_of)

    assert code == 1
    assert out == ""
    assert "the survivor forward report is refused" in err
    assert "not_yet_knowable" in err
    assert not (world.runtime / "reports" / report_name(as_of)).exists()


def test_files_beside_the_journals_that_are_no_sessions_journal_are_not_read(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """E4 review, minor 7 (M8). The morning re-check's advice, `<session>.morning.json`, and the
    `<session>.json.partial` a crashed journal write leaves sit beside the journals: neither is a
    session's journal, neither is read, and the report is the three evenings' own."""
    path, _body = _journal(world, EVENINGS[1])
    morning = {"schema": "openalpha-daily-survivor-morning/v1", "session": "2026-04-07"}
    path.with_name("2026-04-07.morning.json").write_text(json.dumps(morning), encoding="utf-8")
    path.with_name("2026-04-08.json.partial").write_text("{", encoding="utf-8")

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    document = _document(world)
    assert document["records"]["witnessed"] == 3
    coverage = document["paper_ledger"]["coverage"]
    assert (coverage["documents"], coverage["evenings"], coverage["refused_days"]) == (3, 3, [])


def test_a_journal_written_under_another_registration_refuses_the_report(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """E4 review, minor 7 (M9). 7 April's journal says another registration of the configuration
    wrote it: the journals are what the store is held against, so the report is refused, naming
    the file -- exit 1, nothing written."""
    path, body = _journal(world, EVENINGS[1])
    body["registration"]["sha256"] = "ef" * 32
    path.write_text(json.dumps(body), encoding="utf-8")

    code, out, err = _report(world, capsys)

    assert code == 1
    assert out == ""
    assert "2026-04-07.json was written under another registration" in err
    assert not (world.runtime / "reports" / report_name(REPORT_AS_OF)).exists()


def _damage(world: tds.World, fault: str) -> None:
    """The paper book's store damaged in one way (`PAPER_FAULTS`)."""
    if fault.startswith("snapshot"):
        path = _snapshot_path(world, EVENINGS[1])
        if fault == "snapshot-not-a-snapshot":
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_bytes(b"\xff\xfe not text")
        return
    if fault == "ledger-table-damaged":
        _ledger_sql(world, "ALTER TABLE portfolio_transitions RENAME COLUMN payload TO body")
        return
    [order_id, *_] = _ledger_ids(world, EVENINGS[1])
    from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

    held = SQLitePortfolioLedger(world.runtime / "paper" / "ledger.sqlite3").get(order_id)
    assert held is not None
    payload = json.loads(held.model_dump_json(exclude_computed_fields=True))
    if fault == "ledger-unknown-schema":
        text = json.dumps({**payload, "schema_version": "portfolio-transition/v99"})
    elif fault == "ledger-invalid-row":
        text = json.dumps({key: value for key, value in payload.items() if key != "order"})
    else:
        text = "not a transition"
    _ledger_sql(
        world, "UPDATE portfolio_transitions SET payload = ? WHERE order_id = ?", (text, order_id)
    )


PAPER_FAULTS: Final[dict[str, str]] = {
    "snapshot-not-a-snapshot": "cannot be read",
    "snapshot-not-utf8": "is not UTF-8 text",
    "ledger-unknown-schema": "portfolio-transition/v99",
    "ledger-invalid-row": "order",
    "ledger-not-json": "",
    "ledger-table-damaged": "payload",
}
"""How the paper book's store is damaged, and a word of the refusal each gives (E4 review, minor
3 and M12): a snapshot that does not parse or is not UTF-8 text, a ledger row of an unknown
schema version, one its model refuses, one that is not JSON, and a ledger table that cannot be
read."""


@pytest.mark.parametrize("fault", sorted(PAPER_FAULTS))
def test_a_paper_book_store_that_does_not_read_back_is_the_paper_sections_refusal(
    world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck, fault: str
) -> None:
    """E4 review, minor 3 (M12). Whatever of the paper book's store fails to read back -- a
    snapshot, a ledger row, the ledger's table -- the paper record is refused in its own two
    sections and the report, its prediction layer whole, is still produced: exit 0, never a
    traceback."""
    _damage(world, fault)

    code, _out, err = _report(world, capsys)

    assert code == 0, err
    document = _document(world)
    for section in ("paper_ledger", "relative"):
        assert document[section]["wording"] == SURVIVOR_WORDING
        assert PAPER_FAULTS[fault] in document[section]["refused"], document[section]["refused"]
    assert document["records"]["witnessed"] == 3
    assert document["prediction_layer"]["h20"]["witnessed"] == 3


@pytest.mark.parametrize("fault", ["IndexPriceError", "PanelBatchError"])
def test_benchmark_levels_that_do_not_read_refuse_the_report_by_name(
    world: tds.World,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    scripted: ScriptedCheck,
    fault: str,
) -> None:
    """E4 review, minor 3. The CSI 500's levels refused by their reader -- a stored level row that
    does not read (`IndexPriceError`), a read the panel's batch rules refuse (`PanelBatchError`)
    -- refuse the report by name: exit 1, nothing written, never a traceback."""
    from openalpha_cn.domain.index_prices import IndexPriceError
    from openalpha_cn.domain.panel_batch import PanelBatchError

    error = {"IndexPriceError": IndexPriceError, "PanelBatchError": PanelBatchError}[fault]

    def refused(*_arguments: Any, **_keywords: Any) -> Any:
        raise error("level row 3 does not read")

    monkeypatch.setattr(forward_report, "load_index_prices", refused)

    code, out, err = _report(world, capsys)

    assert code == 1
    assert out == ""
    assert f"the {PROTOCOL_BENCHMARK} levels of [2026] cannot be read" in err
    assert "level row 3 does not read" in err
    assert not (world.runtime / "reports" / report_name(REPORT_AS_OF)).exists()


def test_labels_that_cannot_be_read_refuse_the_report_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    matured_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """E4 review, important 2 (M13). The adjustment factors the shared label reader reads are not
    stored: the reader's `ModelPanelUnreadableError` refuses the report by name, exit 1."""
    from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET

    matured = _matured(tmp_path, monkeypatch, matured_panel)
    PanelStore(matured.world.runtime / "panel").remove_partition(ADJ_FACTOR_DATASET, 2026)

    code, out, err = _report(matured.world, capsys, as_of=MATURED_AS_OF)

    assert code == 1
    assert out == ""
    assert "the survivor forward report is refused" in err
    assert "adjustment factors" in err
    assert not (matured.world.runtime / "reports" / report_name(MATURED_AS_OF)).exists()


def _file_at(world: tds.World, batch: PredictionBatch, *, at: datetime) -> str:
    """`batch` filed with the store's custody clock at `at`."""
    store = PanelStore(world.runtime / "panel")
    day = batch.as_of.astimezone(SHANGHAI).date()
    calendar = daily._outcome_calendar(store, "SSE", day, batch.predicted_at)
    filed = FilePredictionStore(world.runtime / "predictions", clock=lambda: at).put(
        batch=batch, calendar=calendar, zone=SHANGHAI
    )
    return filed.record.record_id


def test_a_record_registered_at_0914_is_on_time_and_one_registered_at_0915_is_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    matured_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """E4 review, minor 7 (M11). The registration cutoff is 09:15 of the next session, an instant
    and not a date: ordinal 26's record registered at 09:14 of ordinal 27's day is witnessed;
    ordinal 27's registered at 09:15 of ordinal 28's day is a late record, not a registration --
    never witnessed, never checked, its session missing."""
    matured = _matured(tmp_path, monkeypatch, matured_panel)
    world, day = matured.world, matured.ordinals.day_of

    def morning(ordinal: int, minute: int) -> datetime:
        return datetime.fromisoformat(f"{day(ordinal).isoformat()}T09:{minute:02d}:00+08:00")

    on_time = _file_at(world, batch_of(day(26), 26, matured.registered), at=morning(27, 14))
    late = _file_at(world, batch_of(day(27), 27, matured.registered), at=morning(28, 15))

    code, _out, err = _report(world, capsys, as_of=MATURED_AS_OF)

    assert code == 0, err
    records = _document(world, MATURED_AS_OF)["records"]
    assert (records["witnessed"], records["newest_session"]) == (26, day(26).isoformat())
    assert day(26).isoformat() not in records["missing_sessions"]
    assert day(27).isoformat() in records["missing_sessions"]
    assert on_time in scripted.asked
    assert late not in scripted.asked


NEW_YEAR_EPOCH: Final[date] = date(2026, 12, 14)
NEW_YEAR_DAYS: Final[tuple[date, ...]] = tds.weekdays(
    NEW_YEAR_EPOCH, date(2027, 3, 31), closed=(date(2027, 1, 1),)
)
"""The New Year market's sessions: from the epoch, 14 December 2026; 1 January 2027 a holiday."""
NEW_YEAR_BUILD: Final[datetime] = datetime(2026, 12, 31, 18, 0, tzinfo=SHANGHAI)
"""The build: 31 December has published, and 2027's calendar is fetched beside it -- before the
record (18:30), which is placed on it."""
NEW_YEAR_AS_OF: Final[datetime] = datetime(2026, 12, 31, 19, 30, tzinfo=SHANGHAI)
"""The report: after the year's last session's record was filed (18:31), still in 2026."""


@pytest.fixture(scope="module")
def new_year_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The New Year market through 31 December 2026, and 2027's calendar, by `panel build`."""
    runtime = tmp_path_factory.mktemp("survivor-new-year") / "runtime"
    market = survivor_day.SurvivorMarket(open_days=NEW_YEAR_DAYS)
    market.today = NEW_YEAR_BUILD.date()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        for year, targets in ((2026, survivor_day.SEED_TARGETS), (2027, ("trade_cal",))):
            arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
            arguments += ["--as-of", NEW_YEAR_BUILD.isoformat(), "--exchange", "SSE", "--json"]
            for target in targets:
                arguments += ["--dataset", target]
            built = daily.invoke(arguments)
            assert built.exit_code == 0, built.reason()
    return runtime


def test_a_record_on_the_years_last_session_is_witnessed_on_next_years_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    new_year_panel: Path,
    scripted: ScriptedCheck,
) -> None:
    """E4 review, minor 2 (M4). A record on 31 December, reported on the evening it was filed:
    its registration cutoff is 09:15 of 4 January 2027, a session of next year's calendar, which
    the panel holds -- the report reads it beside the report's own year, and the record is
    witnessed and checked. Without it the cutoff could not be placed and the report would be
    refused."""
    config = survivor_day.survivor_config(epoch=NEW_YEAR_EPOCH)
    repo, registration = _repository(tmp_path, config, settings=settings_with())
    market = survivor_day.SurvivorMarket(open_days=NEW_YEAR_DAYS)
    tds._bind(monkeypatch, repo, market)
    runtime = tmp_path / "runtime"
    shutil.copytree(new_year_panel, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    registered = daily.admit_registration(registration, repo).declared
    last = date(2026, 12, 31)
    record_id = _file(world, batch_of(last, NEW_YEAR_DAYS.index(last), registered))

    code, _out, err = _report(world, capsys, as_of=NEW_YEAR_AS_OF)

    assert code == 0, err
    records = _document(world, NEW_YEAR_AS_OF)["records"]
    assert (records["witnessed"], records["newest_session"], records["through"]) == (
        1,
        "2026-12-31",
        "2026-12-31",
    )
    assert scripted.asked == [record_id]


SHORT: Final[dict[str, Any]] = {"primary_horizon": 1, "secondary_horizons": [2]}
"""The four short evenings' protocol changes: test values (the block above)."""
SHORT_EVENINGS: Final[tuple[date, ...]] = (*EVENINGS, survivor_day.FOURTH)
"""6, 7, 8 and 9 April: ordinals 1 to 4."""
SHORT_AS_OF: Final[datetime] = datetime(2026, 4, 10, 10, 0, tzinfo=SHANGHAI)
"""After 9 April's registration cutoff: the four evenings' sessions closed, two records mature."""


@pytest.fixture(scope="module")
def short_evenings(tmp_path_factory: pytest.TempPathFactory, seeded: Path) -> tds.World:  # noqa: F811
    """The four short evenings, run once (the block above). The first name the book holds after
    7 April is delisted on 9 April, with no bar that day: the 9 April evening writes it off, so
    that session has a ledger row (the book trades nothing on 8 April)."""
    root = tmp_path_factory.mktemp("survivor-short-evenings")
    repo, registration = _repository(root, CONFIG, settings=settings_with(**SHORT))
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    runtime = root / "runtime"
    shutil.copytree(seeded, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, repo, market)
        for day in SHORT_EVENINGS:
            if day == survivor_day.FOURTH:
                market.delisted = ((_held_after(world, EVENINGS[1])[0], day),)
            code, _out, err = _daily(world, day)
            assert code == 0, err
    return world


def _held_after(world: tds.World, day: date) -> list[str]:
    """The names the paper book held after `day`, from its snapshot."""
    from openalpha_cn.backtest.paper_state import load_snapshot
    from openalpha_cn.storage.paper_state import FilePaperStateStore

    store = FilePaperStateStore(world.runtime / "paper" / "snapshots")
    return sorted(position.subject for position in load_snapshot(store, day).book.positions)


@pytest.fixture
def short_world(
    short_evenings: tds.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tds.World:
    """A copy of the four short evenings' runtime directory, under their registration."""
    runtime = tmp_path / "runtime"
    shutil.copytree(short_evenings.runtime, runtime)
    tds._bind(monkeypatch, short_evenings.repo, short_evenings.market)
    return dataclasses.replace(short_evenings, runtime=runtime)


def _six(value: Any) -> str:
    """`value` to six places, half away from zero: the record's own rounding, written out."""
    from fractions import Fraction

    exact = Fraction(value)
    scaled = abs(exact) * 10**6
    whole = scaled.numerator // scaled.denominator
    if scaled - whole >= Fraction(1, 2):
        whole += 1
    sign = "-" if exact < 0 and whole else ""
    return f"{sign}{whole // 10**6}.{whole % 10**6:06d}"


def _equity(world: tds.World, day: date) -> Any:
    """The paper book's equity on `day`, from its snapshot, read here and not by the report."""
    from fractions import Fraction

    from openalpha_cn.backtest.paper_state import load_snapshot
    from openalpha_cn.storage.paper_state import FilePaperStateStore

    store = FilePaperStateStore(world.runtime / "paper" / "snapshots")
    return Fraction(load_snapshot(store, day).book.equity)


def _short_outcome(world: tds.World, record_id: str, *, as_of: datetime) -> Any:
    """`record_id`'s outcome at the primary horizon, labelled by a reader built here."""
    store = PanelStore(world.runtime / "panel")
    labels = OutcomeLabels(store, LabelReach(as_of=as_of, years=(2026,), exchange="SSE"))
    levels = load_index_prices(
        store, labels.calendar, years=(2026,), as_of=as_of, max_staleness=None
    )[PROTOCOL_BENCHMARK]
    record = FilePredictionStore(world.runtime / "predictions", clock=lambda: as_of).get(record_id)
    assert record is not None
    session = record.batch.as_of.astimezone(SHANGHAI).date()
    ordinals = SessionOrdinals(calendar_of(world.runtime), EPOCH)
    witnessed = WitnessedRecord(
        session=session, ordinal=ordinals.index_of(session), record_id=record_id, record=record
    )
    return record_outcome(labels, witnessed, horizon=1, book=BOOK, csi500=levels, as_of=as_of)


def test_the_relative_return_is_the_paper_books_equity_over_the_primary_samples_windows(
    short_world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """E4 review, important 2: the relative return wired end to end. Four evenings, a primary
    horizon of one session: the windows are the primary sample's matured periods -- one session
    each, entry and exit the record's D+1 and D+2 closes -- and each window's book return is the
    paper book's equity at the exit over its equity at the entry, from the evenings' own
    snapshots; its investable equal weight and CSI 500 are the record's own at that horizon; and
    windows that meet are compounded into one chain, whose book return telescopes to the last
    exit's equity over the first entry's."""
    from decimal import Decimal

    code, _out, err = _report(short_world, capsys, as_of=SHORT_AS_OF)

    assert code == 0, err
    document = _document(short_world, SHORT_AS_OF)
    assert document["paper_ledger"]["coverage"]["evenings"] == 4
    assert document["paper_ledger"]["write_offs"]["count"] == 1
    relative = document["relative"]
    assert "refused" not in relative, relative.get("refused")
    windows = relative["windows"]
    assert windows and relative["windows_computed"] == len(windows) == relative["windows_given"]
    ordinals = SessionOrdinals(calendar_of(short_world.runtime), EPOCH)
    for window in windows:
        entry, exit_day = date.fromisoformat(window["entry"]), date.fromisoformat(window["exit"])
        assert ordinals.index_of(exit_day) - ordinals.index_of(entry) == 1, window
        expected = _equity(short_world, exit_day) / _equity(short_world, entry) - 1
        assert window["book_return"] == _six(expected), window
        outcome = _short_outcome(short_world, window["record_id"], as_of=SHORT_AS_OF)
        assert (outcome.entry_day, outcome.exit_day) == (entry, exit_day)
        assert window["investable_ew"] == _six(Decimal(repr(outcome.investable_ew)))
        assert window["csi500"] == _six(Decimal(repr(outcome.csi500)))
    [chain] = relative["chains"]
    first, last = date.fromisoformat(chain["entry"]), date.fromisoformat(chain["exit"])
    assert chain["windows"] == len(windows)
    assert chain["book"] == _six(_equity(short_world, last) / _equity(short_world, first) - 1)


def test_a_report_as_of_an_instant_bounds_the_paper_book_and_its_windows_to_that_instant(
    short_world: tds.World, capsys: pytest.CaptureFixture[str], scripted: ScriptedCheck
) -> None:
    """E4 review, important 2 (M1, M2, M3) and minor 1. Run after the four evenings, a report as
    of 17:00 on 9 April -- after its 16:30 publication, before its evening. The 7 April record's
    window (the 8 April close to the 9 April close) has matured, and the 9 April snapshot it
    exits on is in the store; but the paper book's head is 8 April (9 April's journal was pinned
    after the report's instant), so the snapshots, the ledger rows and the journals read are 8
    April's and before: that window has no snapshot to exit on, 9 April's write-off is no ledger
    row of the record, and no journal of 9 April is held against a history that ends the day
    before."""
    as_of = datetime(2026, 4, 9, 17, 0, tzinfo=SHANGHAI)
    seventh = _journal(short_world, EVENINGS[1])[1]["prediction"]["record_id"]
    assert _snapshot_path(short_world, survivor_day.FOURTH).is_file()
    assert _ledger_ids(short_world, survivor_day.FOURTH)

    code, _out, err = _report(short_world, capsys, as_of=as_of)

    assert code == 0, err
    document = _document(short_world, as_of)
    assert (document["records"]["witnessed"], document["records"]["through"]) == (3, "2026-04-08")
    ledger = document["paper_ledger"]
    assert "refused" not in ledger, ledger.get("refused")
    coverage = ledger["coverage"]
    assert (coverage["evenings"], coverage["last_settled"]) == (3, "2026-04-08")
    assert ledger["reconciliation"]["rows_of_unsettled_sessions"] == []
    windows = {window["record_id"]: window for window in document["relative"]["windows"]}
    assert (windows[seventh]["entry"], windows[seventh]["exit"]) == ("2026-04-08", "2026-04-09")
    assert windows[seventh]["status"] == "unavailable"
    assert windows[seventh]["book_return"] is None
    assert "no snapshot for 2026-04-09" in windows[seventh]["reason"]
