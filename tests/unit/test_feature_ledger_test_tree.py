"""Hold `features.csv` and the real test tree to an executable correspondence (V2-P5-023).

`artifacts/openalpha-v1-feature-coverage/features.csv` names test files by path, so a
test-tree reorganisation is a three-artifact change: the CSV, `summary.json` and
`docs/release/openalpha-v1-feature-ledger.md`. The last two are already held byte-for-byte
against the first by `build_feature_coverage.py --check`, so the weak edge was always
CSV → disk, and the point of this module is to make a move fail *here*, naming the row and
the path, instead of failing later in two CI jobs at once for a reason neither job states.

What was measured before this module existed:

- `--check` prints `{"unknown": 0, "unreviewed": 0}`. Both were **literal zeros** in
  `_summary`, ranging over nothing; they are computed from the rows as of V2-P5-023, and
  `test_summary_counts_*` below are the tests that make them stay computed.
- Evidence-path existence was checked only for rows in `TRUE_COMPLETE`, so the five
  `EXCLUDED`/`DEFERRED` rows were exempt. Three paths were reachable only through those
  rows (`SECURITY.md`, `docs/data/providers.zh-CN.md`,
  `docs/specs/openalpha-cn-v1-spec.md`); deleting `SECURITY.md` outright left `--check`
  exiting 0. `test_a_missing_evidence_path_is_caught_on_an_excluded_row` is that hole.

The counts below are an **equality**, not a floor and not a membership test — the
distinction `V2-P4-038` was written about. A floor ("at least N test files are named")
stays green when a row is deleted and an unrelated one added; an exact per-directory count
goes red and names the directory that changed. What this shape still does not catch is a
*swap inside one directory* (drop the row naming `tests/unit/a.py`, add one naming
`tests/unit/b.py`): both counts and the total are unchanged. That residue is covered for
`acceptance_kind="pytest"` rows by `_validate_pytest_acceptance`, which AST-checks that the
named test function exists, and is accepted for the rest.
"""

from __future__ import annotations

import csv
import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "build_feature_coverage.py"
CSV_PATH = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "features.csv"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_feature_coverage_tree", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bfc = _load_module()

# Directory → number of distinct test files the ledger names in it. Regenerate with
# `tests/unit/test_feature_ledger_test_tree.py`'s own helpers rather than by hand: run
# `_ledger_test_files()` and `Counter(Path(p).parent.as_posix() for p in ...)`. Every entry moving
# is a legitimate edit; the point is that it cannot happen *silently*.
LEDGER_TEST_FILE_COUNTS = {
    "tests": 2,
    "tests/contract/panel": 1,
    "tests/contract/providers": 14,
    "tests/e2e": 3,
    "tests/integration": 32,
    "tests/integration/panel": 34,
    "tests/integration/storage": 8,
    "tests/replay": 1,
    "tests/unit": 29,
    "tests/unit/agents": 2,
    "tests/unit/backtest": 14,
    "tests/unit/domain": 24,
    "tests/unit/evidence": 1,
    "tests/unit/models": 2,
    "tests/unit/panel": 3,
    "tests/unit/product": 1,
    "tests/unit/runtime": 8,
    "tests/unit/tools": 1,
    "web/e2e": 1,
    "web/src": 1,
}
LEDGER_TEST_FILE_TOTAL = 182

FIELDNAMES = [
    "feature_id",
    "category",
    "coverage_status",
    "acceptance_test",
    "acceptance_kind",
    "local_source_evidence",
    "test_evidence",
]


def _rows() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _referenced_paths(value: str) -> list[str]:
    """Every `path` in a `a.py#sym;b.py` evidence cell, fragments and prefixes stripped."""
    out: list[str] = []
    for item in value.split(";"):
        raw = item.split("#", maxsplit=1)[0].removeprefix("github:")
        if raw:
            out.append(raw)
    return out


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    return (
        path.startswith("tests/")
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _ledger_test_files() -> set[str]:
    """Distinct test-file paths the ledger names, across evidence cells and node ids."""
    named: set[str] = set()
    for row in _rows():
        for field in ("local_source_evidence", "test_evidence"):
            named.update(_referenced_paths(row[field]))
        if row["acceptance_kind"] == "pytest":
            named.add(row["acceptance_test"].split("::")[0])
    return {path for path in named if _is_test_path(path)}


# --- the real ledger against the real tree ------------------------------------------


def test_the_ledger_names_exactly_this_many_test_files_per_directory() -> None:
    counts = Counter(Path(path).parent.as_posix() for path in _ledger_test_files())

    assert dict(sorted(counts.items())) == dict(sorted(LEDGER_TEST_FILE_COUNTS.items()))


def test_the_ledger_names_exactly_this_many_distinct_test_files() -> None:
    assert len(_ledger_test_files()) == LEDGER_TEST_FILE_TOTAL


def test_every_test_file_the_ledger_names_is_on_disk() -> None:
    missing = sorted(path for path in _ledger_test_files() if not (ROOT / path).exists())

    assert missing == [], f"features.csv names test files that no longer exist: {missing}"


def test_every_path_the_ledger_names_is_on_disk_whatever_the_row_status() -> None:
    """The whole CSV, not just its test files and not just its `TRUE_COMPLETE` rows."""
    dangling = sorted(
        f"{row['feature_id']} -> {path}"
        for row in _rows()
        for field in ("local_source_evidence", "test_evidence")
        for path in _referenced_paths(row[field])
        if not (ROOT / path).exists()
    )

    assert dangling == [], f"features.csv references paths that do not exist: {dangling}"


# --- the guard that used to be skipped on non-TRUE_COMPLETE rows ----------------------


def _write_single_row_csv(csv_path: Path, **overrides: str) -> None:
    row = {
        "feature_id": "TST-023",
        "category": "test-category",
        "coverage_status": "EXCLUDED",
        "acceptance_test": "Boundary declaration, no test applies.",
        "acceptance_kind": "not-applicable",
        "local_source_evidence": "spec.md#Non-goals",
        "test_evidence": "tests/unit/test_moved.py",
    }
    row.update(overrides)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)


def _point_at_tmp_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, csv_path: Path) -> None:
    monkeypatch.setattr(bfc, "ROOT", tmp_path)
    monkeypatch.setattr(bfc, "CSV_PATH", csv_path)


def test_an_excluded_row_naming_an_existing_test_file_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "spec.md").write_text("# Spec\n\nNon-goals\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "unit" / "test_moved.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_thing():\n    assert True\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_single_row_csv(csv_path)
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-023"


def test_a_missing_evidence_path_is_caught_on_an_excluded_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The V2-P5-023 regression: `EXCLUDED` used to exempt a row from every path check."""
    (tmp_path / "spec.md").write_text("# Spec\n\nNon-goals\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_single_row_csv(csv_path)  # `tests/unit/test_moved.py` is never created
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-023") as excinfo:
        bfc._load()

    assert "tests/unit/test_moved.py" in str(excinfo.value)


def test_a_missing_evidence_path_is_caught_on_a_deferred_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "spec.md").write_text("# Spec\n\nNon-goals\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_single_row_csv(csv_path, coverage_status="DEFERRED")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-023") as excinfo:
        bfc._load()

    assert "tests/unit/test_moved.py" in str(excinfo.value)


def test_an_undefined_symbol_is_caught_on_an_excluded_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pkg.py").write_text("def real_function():\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "unit" / "test_moved.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_thing():\n    assert True\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_single_row_csv(csv_path, local_source_evidence="pkg.py#NotDeclaredAnywhere")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-023") as excinfo:
        bfc._load()

    assert "NotDeclaredAnywhere" in str(excinfo.value)


# --- the two totals that used to be literals ------------------------------------------


def _summary_row(status: str) -> dict[str, str]:
    return {
        "feature_id": f"TST-{status}",
        "category": "test-category",
        "coverage_status": status,
        "acceptance_test": "prose",
        "acceptance_kind": "legacy-prose",
        "local_source_evidence": "pkg.py",
        "test_evidence": "pkg.py",
    }


def test_summary_counts_unreviewed_rows_instead_of_reporting_zero() -> None:
    """`_summary` is called on validated rows in production, so drive it directly."""
    rows = [_summary_row("NATIVE_COMPLETE"), _summary_row("NOT_A_TERMINAL_STATUS")]

    totals = bfc._summary(rows)["totals"]

    assert isinstance(totals, dict)
    assert totals["unreviewed"] == 1


def test_summary_counts_unknown_rows_instead_of_reporting_zero() -> None:
    rows = [_summary_row("NATIVE_COMPLETE"), _summary_row(bfc.UNKNOWN_STATUS)]

    totals = bfc._summary(rows)["totals"]

    assert isinstance(totals, dict)
    assert totals["unknown"] == 1
    assert totals["unreviewed"] == 1, "UNKNOWN is not terminal, so it is also unreviewed"


def test_the_real_ledger_has_no_unreviewed_or_unknown_rows() -> None:
    """Now a measurement of the shipped CSV rather than a pair of hardcoded zeros."""
    totals = bfc._summary(_rows())["totals"]

    assert isinstance(totals, dict)
    assert totals["unreviewed"] == 0
    assert totals["unknown"] == 0


# --- the debt this module cannot check, held so it can only shrink (`V2-P5-038`) --------------

UNVALIDATED_ACCEPTANCE_CEILING: Final[int] = 85
"""How many rows may carry `acceptance_kind="legacy-prose"`. It may fall. It may not rise.

`V2-P5-038`. The rows this module counts per directory are checked for *existence* --
`build_feature_coverage._load` verifies every evidence path on every row, whatever its status --
but only `acceptance_kind="pytest"` rows are tied to a **named test function**, which
`_validate_pytest_acceptance` then verifies by AST. For the rest, a swap inside one directory
(retire the row naming `tests/unit/a.py`, add one naming `tests/unit/b.py`) leaves the counts
above unchanged and every path still on disk.

That row weighed two ways of closing it and found both more expensive than the defect: migrating
85 rows of prose is a content decision per row, and pinning a 185-item path set would go red on
every legitimate edit to the ledger -- the shape this repository has already paid for twice, in
`V2-P5-053` (a version literal that blocked its own security fix) and `V2-P5-060` (a hand-kept
list of spellings that went stale).

A ceiling is the third way, and it is cheap. `summary.json` already records
`legacy_acceptance_rows` and `--check` pins it byte-for-byte, but a pin permits an increase as
easily as a decrease: update the artifact and it passes. This does not. New work must name a
test; the debt can only be paid down, and every payment edits this number downward in the same
commit that earns it.

**The floor is not zero, and pretending otherwise would be the wrong guard.** Three rows --
`OA-IFACE-006`, `OA-IFACE-007`, `OA-OPS-002` -- name `web/src/App.test.tsx` and
`web/e2e/golden-flow.spec.ts`, which no `pytest` node id can address. They need a fourth
acceptance kind or they stay prose; either is a decision this ceiling does not prejudge.
"""


def test_the_unvalidated_acceptance_rows_never_grow() -> None:
    """The ratchet. A row added as prose fails here; a row migrated away lowers the ceiling.

    The message names the count and the ceiling and not the eighty-odd rows that were already
    there -- `git diff` on the CSV names the one that changed, and a failure that prints the
    whole debt buries the one line the reader needs.
    """
    prose = [row["feature_id"] for row in _rows() if row["acceptance_kind"] == "legacy-prose"]

    excess = len(prose) - UNVALIDATED_ACCEPTANCE_CEILING
    assert len(prose) <= UNVALIDATED_ACCEPTANCE_CEILING, (
        f"{len(prose)} rows carry acceptance_kind=legacy-prose, {excess} "
        f"above the ceiling of {UNVALIDATED_ACCEPTANCE_CEILING}. A new feature has to name a "
        f"test rather than describe one; an existing row may only move the other way. "
        f"`git diff artifacts/openalpha-v1-feature-coverage/features.csv` names the row."
    )

    assert len(prose) == len(set(prose)), "the ledger has duplicate feature_id values"
