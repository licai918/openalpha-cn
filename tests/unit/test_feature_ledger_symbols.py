"""AST-level symbol verification for feature-ledger evidence references.

`scripts/build_feature_coverage.py` checks that every `path#symbol` evidence
reference points at a file that exists. These tests cover the additional
requirement that, for Python targets, the referenced symbol must actually be
declared in that file (at module top level or within a class body), and that
every `acceptance_test` row is bound to an executable form (a pytest node id,
a CI job, or an explicit exemption) rather than free-form prose. All
fixtures are synthesized under `tmp_path` so these tests do not depend on the
current contents of the real `artifacts/openalpha-v1-feature-coverage/features.csv`.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_feature_coverage.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_feature_coverage_under_test", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bfc = _load_module()

FIELDNAMES = [
    "feature_id",
    "category",
    "coverage_status",
    "acceptance_test",
    "acceptance_kind",
    "local_source_evidence",
    "test_evidence",
]


def _write_csv(
    csv_path: Path,
    *,
    local_source_evidence: str,
    test_evidence: str,
    acceptance_test: str = "Exercised by a synthetic fixture.",
    acceptance_kind: str = "legacy-prose",
    coverage_status: str = "NATIVE_COMPLETE",
    category: str = "test-category",
) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(
            {
                "feature_id": "TST-001",
                "category": category,
                "coverage_status": coverage_status,
                "acceptance_test": acceptance_test,
                "acceptance_kind": acceptance_kind,
                "local_source_evidence": local_source_evidence,
                "test_evidence": test_evidence,
            }
        )


def _point_at_tmp_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, csv_path: Path) -> None:
    monkeypatch.setattr(bfc, "ROOT", tmp_path)
    monkeypatch.setattr(bfc, "CSV_PATH", csv_path)


def test_reference_to_an_existing_symbol_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(csv_path, local_source_evidence="pkg.py#real_function", test_evidence="pkg.py")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_reference_to_a_missing_symbol_raises_and_names_the_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path, local_source_evidence="pkg.py#DoesNotExistAnywhere", test_evidence="pkg.py"
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="DoesNotExistAnywhere") as excinfo:
        bfc._load()

    assert "TST-001" in str(excinfo.value)
    assert "pkg.py" in str(excinfo.value)


def test_reference_to_a_class_name_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("class RealClass:\n    pass\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(csv_path, local_source_evidence="pkg.py#RealClass", test_evidence="pkg.py")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_reference_to_a_method_inside_a_class_body_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text(
        "class RealClass:\n    def real_method(self):\n        return 1\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "features.csv"
    _write_csv(csv_path, local_source_evidence="pkg.py#real_method", test_evidence="pkg.py")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_reference_to_a_module_level_variable_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("REAL_VARIABLE = 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(csv_path, local_source_evidence="pkg.py#REAL_VARIABLE", test_evidence="pkg.py")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_non_python_fragment_reference_only_checks_file_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "spec.md"
    doc.write_text("# Spec\n\nnot python syntax at all }{][\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(csv_path, local_source_evidence="spec.md#Non-goals", test_evidence="spec.md")
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


# --- acceptance_kind: pytest ------------------------------------------------


def test_pytest_acceptance_kind_pointing_at_an_existing_function_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_thing.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_real():\n    assert True\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="tests/test_thing.py",
        acceptance_test="tests/test_thing.py::test_real",
        acceptance_kind="pytest",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_pytest_acceptance_kind_pointing_at_an_existing_class_method_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_thing.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class TestReal:\n    def test_method(self):\n        assert True\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="tests/test_thing.py",
        acceptance_test="tests/test_thing.py::TestReal::test_method",
        acceptance_kind="pytest",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_pytest_acceptance_kind_pointing_at_a_missing_test_raises_and_names_the_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_thing.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_real():\n    assert True\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="tests/test_thing.py",
        acceptance_test="tests/test_thing.py::test_does_not_exist",
        acceptance_kind="pytest",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001") as excinfo:
        bfc._load()

    assert "test_does_not_exist" in str(excinfo.value)


def test_pytest_acceptance_kind_with_a_malformed_node_id_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="tests/test_thing.py without a node id separator",
        acceptance_kind="pytest",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001"):
        bfc._load()


# --- acceptance_kind: ci-job -------------------------------------------------


def test_ci_job_acceptance_kind_pointing_at_an_existing_job_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "name: ci\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "  deploy:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="workflow.yml",
        acceptance_test="workflow.yml::build",
        acceptance_kind="ci-job",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_ci_job_acceptance_kind_pointing_at_a_missing_job_raises_and_names_the_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("name: ci\njobs:\n  build:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="workflow.yml",
        acceptance_test="workflow.yml::does-not-exist",
        acceptance_kind="ci-job",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001") as excinfo:
        bfc._load()

    assert "does-not-exist" in str(excinfo.value)


def test_ci_job_acceptance_kind_with_a_malformed_reference_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="workflow.yml (no job separator)",
        acceptance_kind="ci-job",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001"):
        bfc._load()


# --- acceptance_kind: not-applicable -----------------------------------------


def test_not_applicable_acceptance_kind_on_an_excluded_row_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "spec.md"
    doc.write_text("# Spec\n\nNon-goals\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="spec.md#Non-goals",
        test_evidence="spec.md",
        acceptance_test="Boundary declaration, no test applies.",
        acceptance_kind="not-applicable",
        coverage_status="EXCLUDED",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()

    assert rows[0]["feature_id"] == "TST-001"


def test_not_applicable_acceptance_kind_on_a_native_complete_row_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="Should not be allowed here.",
        acceptance_kind="not-applicable",
        coverage_status="NATIVE_COMPLETE",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001"):
        bfc._load()


# --- acceptance_kind: legacy-prose --------------------------------------------


def test_legacy_prose_acceptance_kind_is_not_validated_and_is_counted_in_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="Any free-form prose is fine here, never parsed.",
        acceptance_kind="legacy-prose",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    rows = bfc._load()
    summary = bfc._summary(rows)
    totals = summary["totals"]
    assert isinstance(totals, dict)

    assert totals["legacy_acceptance_rows"] == 1


# --- acceptance_kind: presence and enum validity -----------------------------


def test_missing_acceptance_kind_raises_and_names_the_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="Some prose.",
        acceptance_kind="",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001"):
        bfc._load()


def test_unknown_acceptance_kind_value_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pkg.py"
    source.write_text("def real_function():\n    return 1\n", encoding="utf-8")
    csv_path = tmp_path / "features.csv"
    _write_csv(
        csv_path,
        local_source_evidence="pkg.py#real_function",
        test_evidence="pkg.py",
        acceptance_test="Some prose.",
        acceptance_kind="totally-made-up",
    )
    _point_at_tmp_root(monkeypatch, tmp_path, csv_path)

    with pytest.raises(ValueError, match="TST-001"):
        bfc._load()
