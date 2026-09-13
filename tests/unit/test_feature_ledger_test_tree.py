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

import ast
import csv
import importlib.util
import re
from collections import Counter
from functools import cache
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
    "tests/integration": 34,
    "tests/integration/panel": 34,
    "tests/integration/storage": 8,
    "tests/replay": 2,
    "tests/unit": 30,
    "tests/unit/agents": 2,
    "tests/unit/backtest": 15,
    "tests/unit/domain": 25,
    "tests/unit/evidence": 1,
    "tests/unit/models": 2,
    "tests/unit/panel": 3,
    "tests/unit/product": 1,
    "tests/unit/runtime": 9,
    "tests/unit/tools": 1,
    "web/e2e": 1,
    "web/src": 1,
}
LEDGER_TEST_FILE_TOTAL = 189

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


# --- what the notes and the anchors point at (D13, final review M10) ------------------------

_NOTE_NODE_ID: Final = re.compile(
    r"(?<![\w/.])((?:tests|web)/[\w./-]+\.py::[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)"
)
"""A pytest node id written into a notes cell: `tests/...py::name` or `...py::Class::name`."""

_NOTE_SYMBOL: Final = re.compile(r"(?<![\w/.])((?:[\w-]+/)*[\w-]+\.py)#([A-Za-z_]\w*)")
"""A `path.py#symbol` written into a notes cell -- the form `_load` checks in the evidence cells."""


def _markdown_headings(text: str) -> list[str]:
    return [line.lstrip("#").strip() for line in text.splitlines() if re.match(r"#{1,6}\s", line)]


def _folded(value: str) -> str:
    return value.casefold().replace("-", " ")


def _anchor_problem(path: Path, anchor: str) -> str | None:
    """Why `anchor` does not resolve in the non-Python file `path`, or `None` if it does."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        if any(_folded(anchor) in _folded(heading) for heading in _markdown_headings(text)):
            return None
        return "no heading contains it"
    return None if anchor in text else "it does not occur in the file"


def test_every_test_the_notes_name_by_node_id_is_a_real_test() -> None:
    """A notes cell that names `tests/...py::test_name` names a test that exists.

    `_validate_pytest_acceptance` AST-checks the `acceptance_test` of every `pytest` row, and
    the notes were read by nothing: `OA-MODEL-001` went on citing
    `test_no_shipped_path_constructs_the_usage_recording_provider_or_writes_usage_rows` after
    `2f489b0` renamed it to `test_no_shipped_path_calls_a_model_or_records_usage`. This runs
    the same AST check over every node id written into a notes cell.

    Blind spot, stated: a bare `test_*` name without its path is not checked. The notes also
    name test modules by their stem (`test_research_cycle`), so a bare name is not reliably a
    function, and a renamed test cited that way goes unnoticed here.
    """
    dangling: list[str] = []
    for row in _rows():
        for match in _NOTE_NODE_ID.finditer(row["notes"]):
            try:
                bfc._validate_pytest_acceptance(row["feature_id"], match.group(1))
            except ValueError as error:
                dangling.append(str(error))

    assert dangling == [], f"notes name tests that do not exist: {dangling}"


def test_every_anchor_the_ledger_names_resolves_in_its_file() -> None:
    """Every `file#anchor` in the evidence cells and `path.py#symbol` in the notes resolves.

    `_load` AST-checks `path.py#symbol` in the evidence cells and reads nothing after the `#`
    of any other file, so `OA-BOUND-003` cited `docs/data/providers.zh-CN.md#Redistribution`
    -- a word that file does not contain, under headings that are all Chinese -- and `--check`
    exited 0. The rules:

    * a Markdown anchor must be contained in one of the file's headings, compared case-folded
      with `-` read as a space (`#Non-goals` resolves to `## 13. v1 Non-Goals`);
    * an anchor into any other non-Python file must occur in it verbatim
      (`deploy/compose.yml#services`);
    * a `path.py#symbol` in the notes must be declared there by `_module_symbols`, the path
      read from the repository root and, failing that, from `src/openalpha_cn/` -- the two
      ways the notes spell a path.

    Blind spots, stated: the Markdown rule accepts any heading that merely contains the
    anchor, the verbatim rule accepts any occurrence rather than a definition, and a symbol
    written without a `path.py#` prefix is not read at all.
    """
    problems: list[str] = []
    for row in _rows():
        for field in ("local_source_evidence", "test_evidence"):
            for item in row[field].split(";"):
                raw_path, separator, anchor = item.partition("#")
                path = ROOT / raw_path.removeprefix("github:")
                if not separator or path.suffix == ".py":
                    continue  # no anchor, or one `_load` already checks by AST
                problem = _anchor_problem(path, anchor)
                if problem is not None:
                    problems.append(f"{row['feature_id']} {field}: {item} ({problem})")
        for match in _NOTE_SYMBOL.finditer(row["notes"]):
            relative, symbol = match.group(1), match.group(2)
            candidates = (ROOT / relative, ROOT / "src" / "openalpha_cn" / relative)
            found = next((candidate for candidate in candidates if candidate.is_file()), None)
            if found is None:
                problems.append(f"{row['feature_id']} notes: {match.group(0)} (no such file)")
            elif symbol not in bfc._module_symbols(found):
                problems.append(f"{row['feature_id']} notes: {match.group(0)} (not declared)")

    assert problems == [], f"the ledger points at anchors that are not there: {problems}"


_BARE_TEST_NAME: Final = re.compile(r"(?<![\w:/.])(test_\w+)")
"""A `test_*` name written into a notes cell without its path -- what the node-id check skips."""

HISTORICAL_TEST_NAMES: Final[dict[tuple[str, str], str]] = {
    ("OA-OPS-022", "test_migrate_run_a_second_time_reports_up_to_date"): (
        "the test its successor replaced, named as the one that asserted the Finding 1b lie"
    ),
    (
        "OA-OPS-031",
        "test_every_determinant_of_this_neutralisation_is_either_in_the_identity_or_exempted_by_name",
    ): "quoted as the stale reference that row's audit found in panel_neutralization.py",
    ("OA-FACTOR-003", "test_a_prose_only_edit_moves_the_identity_and_changes_no_number"): (
        "the test's former name, given beside the name it became"
    ),
    ("OA-FACTOR-008", "test_no_stored_statement_projection_carries_a_deducted_profit_column"): (
        "the assertion that went red when V2-P3-017 stored profit_dedt, beside its successor"
    ),
}
"""Bare `test_*` names the notes quote on purpose, as history, though nothing defines them now.

Pinned as whole (row, name) pairs, so each exemption covers that name in that row and nowhere
else, and `test_every_historical_test_name_is_still_quoted_and_still_undefined` fails an entry
that is no longer needed instead of leaving it to excuse a later one.
"""


@cache
def _defined_test_names() -> frozenset[str]:
    """Every function name defined anywhere under `tests/`, and every module stem there."""
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        names.add(path.stem)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )
    return frozenset(names)


def _undefined_bare_test_names() -> set[tuple[str, str]]:
    """Every (row, bare `test_*` name) in the notes that nothing under `tests/` defines."""
    defined = _defined_test_names()
    return {
        (row["feature_id"], name)
        for row in _rows()
        for name in _BARE_TEST_NAME.findall(row["notes"])
        if name not in defined
    }


def test_every_bare_test_name_in_the_notes_is_defined_under_tests() -> None:
    """A `test_*` the notes name without a path is still a function or a module under `tests/`.

    The node-id check above reads only `tests/...py::name`. D13's review counted seven bare
    names in the notes that no file defines: three were citations gone stale -- `OA-OPS-021` and
    `OA-FACTOR-021` named tests since renamed, `OA-BT-014` a name that never existed -- and four
    are history the notes quote on purpose, pinned in `HISTORICAL_TEST_NAMES`. A name passes
    when some file under `tests/` defines a function of that name or is a module of that stem;
    the notes also cite modules by stem (`test_research_cycle`).

    Blind spots, stated: a bare name defined anywhere under `tests/` passes wherever the row
    points; a name defined only under `web/` is not looked for; and a module stem passes a
    sentence that meant a function of the same name.
    """
    undefined = sorted(_undefined_bare_test_names() - HISTORICAL_TEST_NAMES.keys())

    assert undefined == [], f"notes name tests that nothing under tests/ defines: {undefined}"


def test_every_historical_test_name_is_still_quoted_and_still_undefined() -> None:
    """An exemption no longer needed -- unquoted, or defined again -- is taken out, not kept."""
    stale = sorted(HISTORICAL_TEST_NAMES.keys() - _undefined_bare_test_names())

    assert stale == [], f"HISTORICAL_TEST_NAMES exempts pairs that no longer need it: {stale}"


# --- the debt this module cannot check, held so it can only shrink (`V2-P5-038`) --------------
UNVALIDATED_ACCEPTANCE_ROWS: Final[int] = 26
"""How many rows carry `acceptance_kind="legacy-prose"`. It may be lowered. It may not be raised.

`V2-P5-038`. Every evidence path on every row is checked for existence by
`build_feature_coverage._load`, whatever the row's acceptance kind -- all 26 of these included,
`web/` paths and all (measured: pointing `OA-IFACE-006` at a nonexistent `App.test.NOPE.tsx`
makes `--check` raise). What only `pytest` rows have is a **named test function**, which
`_validate_pytest_acceptance` verifies by AST. For the rest, a swap inside one directory --
retire the row naming `tests/unit/a.py`, add one naming `tests/unit/b.py` -- leaves the counts
above unchanged and every path still on disk.

That row weighed two ways of closing it and found both dearer than the defect: migrating 85 rows
of prose is a content decision per row, and pinning a 185-item path set would go red on every
legitimate edit to the ledger -- the shape this repository has already paid for twice, in
`V2-P5-053` (a version literal that blocked its own security fix) and `V2-P5-060` (a hand-kept
list of spellings that went stale).

An exact count is the third way, and it is cheap. `summary.json` already records
`legacy_acceptance_rows` and `--check` pins it byte-for-byte, but a pin permits an increase as
easily as a decrease: update the artifact and it passes.

**Why `==` and not `<=`, which is what this was first written as.** A ceiling looks like the
gentler choice and is the worse one: pay ten rows down and the count is 75 while the ceiling is
still 85, so ten prose rows can be added back without a single test going red. A guard that
loosens itself every time somebody does the right thing is a guard nobody will trust in a year.
The equality costs one line -- lowering this number in the commit that earns it -- and buys a
figure that is still true when it is read.

**What this does not catch, stated rather than left to be found.** The count is a count: a commit
that migrates one row to `pytest` *and* adds another as prose nets zero and passes. And the
number here can be raised by anyone willing to write the diff. What the guard buys is that both
become visible, deliberate edits to a file under review, instead of a silent drift.

**The floor is not zero, and pretending otherwise would be the wrong guard.** Three rows --
`OA-IFACE-006`, `OA-IFACE-007`, `OA-OPS-002` -- name `web/src/App.test.tsx` and
`web/e2e/golden-flow.spec.ts`, which no `pytest` node id can address. A non-`pytest` kind
already exists -- `ci-job`, `<workflow>::<job-id>`, which `OA-OPS-005` uses -- but it names a
whole workflow job. So what these rows lack is either an acceptance finer-grained than a job or
the decision that a job is fine-grained enough; this number prejudges neither. (An earlier
version of this paragraph said they needed "a fourth acceptance kind"; `ci-job` is the fourth.)
"""


def test_the_unvalidated_acceptance_rows_are_the_number_recorded_above() -> None:
    """The ratchet, as an equality. Adding prose fails; paying prose down fails until recorded.

    The message names the count and the direction, not the eighty-odd rows that were already
    there -- `git diff` on the CSV names the one that changed, and a failure that prints the
    whole debt buries the one line the reader needs. It does **not** name the row that moved:
    this test cannot know which one is new, and an earlier note claiming it did was wrong.
    """
    prose = [row["feature_id"] for row in _rows() if row["acceptance_kind"] == "legacy-prose"]
    direction = "above" if len(prose) > UNVALIDATED_ACCEPTANCE_ROWS else "below"

    assert len(prose) == UNVALIDATED_ACCEPTANCE_ROWS, (
        f"{len(prose)} rows carry acceptance_kind=legacy-prose, {direction} the "
        f"{UNVALIDATED_ACCEPTANCE_ROWS} recorded here. Above: a new feature has to name a test "
        f"rather than describe one. Below: good -- lower the number in this commit. Either way "
        f"`git diff artifacts/openalpha-v1-feature-coverage/features.csv` names the row."
    )

    assert len(prose) == len(set(prose)), "the ledger has duplicate feature_id values"
