"""AST-level symbol verification for feature-ledger evidence references.

`scripts/build_feature_coverage.py` checks that every `path#symbol` evidence
reference points at a file that exists. These tests cover the additional
requirement that, for Python targets, the referenced symbol must actually be
declared in that file (at module top level or within a class body). All
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
    "coverage_status",
    "acceptance_test",
    "local_source_evidence",
    "test_evidence",
]


def _write_csv(csv_path: Path, *, local_source_evidence: str, test_evidence: str) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(
            {
                "feature_id": "TST-001",
                "coverage_status": "NATIVE_COMPLETE",
                "acceptance_test": "Exercised by a synthetic fixture.",
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
