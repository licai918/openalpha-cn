"""Validate the v1 feature ledger and generate reconciled release artifacts."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "features.csv"
SUMMARY_PATH = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "summary.json"
DOC_PATH = ROOT / "docs" / "release" / "openalpha-v1-feature-ledger.md"

TERMINAL_STATUSES = {
    "NATIVE_COMPLETE",
    "ADAPTER_COMPLETE",
    "ENHANCED_REPLACEMENT",
    "PLANNED_P0",
    "PLANNED_P1",
    "PLANNED_P2",
    "DEFERRED",
    "EXCLUDED",
}
TRUE_COMPLETE = {
    "NATIVE_COMPLETE",
    "ADAPTER_COMPLETE",
    "ENHANCED_REPLACEMENT",
}
# The pre-audit sentinel: a row that was looked at but whose destination could not be
# decided. It is deliberately absent from `TERMINAL_STATUSES`, so `_load` rejects it and
# `_summary` counts it (see the note there on why both counts used to be literals).
UNKNOWN_STATUS = "UNKNOWN"

ACCEPTANCE_KINDS = {"pytest", "ci-job", "not-applicable", "legacy-prose"}
NOT_APPLICABLE_STATUSES = {"EXCLUDED", "DEFERRED"}

_PYTEST_NODE_ID = re.compile(
    r"^(?P<path>[\w./-]+\.py)::(?:(?P<cls>[A-Za-z_]\w*)::)?(?P<func>[A-Za-z_]\w*)$"
)


def _paths(value: str) -> list[Path]:
    paths: list[Path] = []
    for item in value.split(";"):
        raw_path = item.split("#", maxsplit=1)[0]
        if raw_path.startswith("github:"):
            raw_path = raw_path.removeprefix("github:")
        paths.append(ROOT / raw_path)
    return paths


def _symbol_refs(value: str) -> list[tuple[Path, str]]:
    """Return the (file, symbol) pairs for each `path.py#symbol` reference in `value`.

    Non-Python targets and fragment-less references are omitted: they are covered
    by the file-existence check in `_load` alone, never by AST parsing.
    """
    refs: list[tuple[Path, str]] = []
    for item in value.split(";"):
        raw_path, separator, symbol = item.partition("#")
        if not separator:
            continue
        if raw_path.startswith("github:"):
            raw_path = raw_path.removeprefix("github:")
        path = ROOT / raw_path
        if path.suffix == ".py":
            refs.append((path, symbol))
    return refs


def _class_body_symbols(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for member in body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(member.name)
        elif isinstance(member, ast.Assign):
            names.update(target.id for target in member.targets if isinstance(target, ast.Name))
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            names.add(member.target.id)
    return names


def _module_symbols(path: Path) -> set[str]:
    """Names declared at module top level or within a class body of `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                names.update(_class_body_symbols(node.body))
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _pytest_module_functions(path: Path) -> set[str]:
    """Top-level test function names declared in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _pytest_class_methods(path: Path, class_name: str) -> set[str]:
    """Method names declared inside `class_name` in `path` (empty if the class is absent)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            }
    return set()


def _workflow_job_ids(path: Path) -> set[str]:
    """Job ids declared directly under the top-level `jobs:` key of a workflow file."""
    ids: set[str] = set()
    in_jobs = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not in_jobs:
            if line.rstrip() == "jobs:":
                in_jobs = True
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^ {2}([A-Za-z0-9_-]+):", line)
        if match:
            ids.add(match.group(1))
    return ids


def _validate_pytest_acceptance(feature_id: str, value: str) -> None:
    match = _PYTEST_NODE_ID.match(value)
    if not match:
        raise ValueError(f"{feature_id} has a malformed pytest node id: {value}")
    path = ROOT / match["path"]
    if not path.exists():
        raise ValueError(f"{feature_id} references a missing pytest file: {match['path']}")
    class_name = match["cls"]
    func_name = match["func"]
    known = (
        _pytest_class_methods(path, class_name) if class_name else _pytest_module_functions(path)
    )
    if func_name not in known:
        raise ValueError(f"{feature_id} references undefined symbols: [{value}]")


def _validate_ci_job_acceptance(feature_id: str, value: str) -> None:
    parts = value.split("::")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"{feature_id} has a malformed ci-job reference: {value}")
    workflow_part, job_id = parts
    workflow_path = ROOT / workflow_part
    if not workflow_path.exists():
        raise ValueError(f"{feature_id} references a missing workflow file: {workflow_part}")
    if job_id not in _workflow_job_ids(workflow_path):
        raise ValueError(f"{feature_id} references undefined symbols: [{value}]")


def _validate_acceptance(row: dict[str, str]) -> None:
    """Enforce the per-`acceptance_kind` rules for `row["acceptance_test"]`.

    - `pytest`: `acceptance_test` must be a pytest node id (`path.py::test_name` or
      `path.py::TestClass::test_name`) pointing at a real, AST-verified test.
    - `ci-job`: `acceptance_test` must be `<workflow-file>::<job-id>` where the job id
      is declared in that workflow's `jobs:` block.
    - `not-applicable`: only permitted when `coverage_status` is `EXCLUDED` or `DEFERRED`.
    - `legacy-prose`: unvalidated historical prose, counted by `_summary`.
    """
    feature_id = row["feature_id"]
    kind = row["acceptance_kind"]
    value = row["acceptance_test"]
    if not kind:
        raise ValueError(f"{feature_id} has no acceptance_kind")
    if kind not in ACCEPTANCE_KINDS:
        raise ValueError(f"{feature_id} has an unknown acceptance_kind: {kind}")
    if kind == "pytest":
        _validate_pytest_acceptance(feature_id, value)
    elif kind == "ci-job":
        _validate_ci_job_acceptance(feature_id, value)
    elif kind == "not-applicable" and row["coverage_status"] not in NOT_APPLICABLE_STATUSES:
        raise ValueError(
            f"{feature_id} uses acceptance_kind=not-applicable but coverage_status is "
            f"{row['coverage_status']!r}, expected one of {sorted(NOT_APPLICABLE_STATUSES)}"
        )
    # "legacy-prose" requires no further validation.


def _load() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row["feature_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("feature_id values must be unique")
    for row in rows:
        status = row["coverage_status"]
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"{row['feature_id']} has invalid status: {status}")
        if not row["acceptance_test"]:
            raise ValueError(f"{row['feature_id']} has no acceptance test")
        _validate_acceptance(row)
        # Existence and symbol checks run for **every** row, not only the
        # `TRUE_COMPLETE` ones (V2-P5-023). Until that row, both blocks sat behind
        # `if status in TRUE_COMPLETE`, so the five `EXCLUDED`/`DEFERRED` rows could
        # name files that had been moved or deleted and `--check` still exited 0.
        # Measured before the fix: `SECURITY.md` — named only by `OA-BOUND-004`, an
        # `EXCLUDED` row — could be deleted outright and `--check` still printed
        # `{"unknown": 0, ...}` and returned success. A terminal status records where a
        # feature *went*; it is not a reason to stop checking that its evidence is still
        # where the ledger says it is.
        missing = [
            path.relative_to(ROOT).as_posix()
            for field in ("local_source_evidence", "test_evidence")
            for path in _paths(row[field])
            if not path.exists()
        ]
        if missing:
            raise ValueError(f"{row['feature_id']} references missing evidence: {missing}")
        missing_symbols = [
            f"{path.relative_to(ROOT)}#{symbol}"
            for field in ("local_source_evidence", "test_evidence")
            for path, symbol in _symbol_refs(row[field])
            if symbol not in _module_symbols(path)
        ]
        if missing_symbols:
            raise ValueError(f"{row['feature_id']} references undefined symbols: {missing_symbols}")
    return rows


def _summary(rows: list[dict[str, str]]) -> dict[str, object]:
    by_status = Counter(row["coverage_status"] for row in rows)
    by_category = Counter(row["category"] for row in rows)
    completed = sum(by_status[status] for status in TRUE_COMPLETE)
    total = len(rows)
    legacy_acceptance_rows = sum(1 for row in rows if row["acceptance_kind"] == "legacy-prose")
    # `unreviewed` and `unknown` were literal `0`s from the ledger's first commit until
    # V2-P5-023 — two numbers the released artifact and `--check`'s stdout both printed as
    # findings, which ranged over nothing at all and could not have been anything but zero.
    # They are now derived from the rows, and from the same `TERMINAL_STATUSES` set the
    # validator in `_load` enforces, so loosening that set moves these counts instead of
    # leaving them frozen at a reassuring literal. Both are still 0 today — the difference
    # is that this is now a measurement rather than an assertion.
    unreviewed = sum(1 for row in rows if row["coverage_status"] not in TERMINAL_STATUSES)
    unknown = sum(1 for row in rows if row["coverage_status"] == UNKNOWN_STATUS)
    return {
        "schema_version": "openalpha-feature-coverage/v1",
        "generated_on": "2026-07-24",
        "totals": {
            "all_features": total,
            "true_completed_features": completed,
            "true_completion_rate_pct": round(completed / total * 100, 2),
            "unreviewed": unreviewed,
            "unknown": unknown,
            "legacy_acceptance_rows": legacy_acceptance_rows,
        },
        "status_distribution": dict(sorted(by_status.items())),
        "category_distribution": dict(sorted(by_category.items())),
        "reconciliation": {
            "status_sum_matches_total": sum(by_status.values()) == total,
            "category_sum_matches_total": sum(by_category.values()) == total,
            "unique_feature_ids": len({row["feature_id"] for row in rows}) == total,
            "all_terminal": all(row["coverage_status"] in TERMINAL_STATUSES for row in rows),
        },
    }


def _markdown(rows: list[dict[str, str]], summary: dict[str, object]) -> str:
    totals = summary["totals"]
    assert isinstance(totals, dict)
    lines = [
        "# OpenAlpha CN v1 100% 功能去向台账",
        "",
        "> 100% 表示每项能力都有唯一 ID、证据和终态去向; 真实完成率仅计 "
        "`NATIVE_COMPLETE`、`ADAPTER_COMPLETE`、`ENHANCED_REPLACEMENT`。",
        "",
        "## 对账结论",
        "",
        f"- 功能总数: {totals['all_features']}",
        f"- 当前真实完成: {totals['true_completed_features']} "
        f"({totals['true_completion_rate_pct']}%)",
        f"- `UNREVIEWED={totals['unreviewed']}`",
        f"- `UNKNOWN={totals['unknown']}`",
        f"- `legacy_acceptance_rows={totals['legacy_acceptance_rows']}` "
        "(历史散文验收、尚未绑定为可执行 pytest/CI 断言)",
        "",
        "## 状态分布",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    distribution = summary["status_distribution"]
    assert isinstance(distribution, dict)
    lines.extend(f"| `{status}` | {count} |" for status, count in distribution.items())
    lines.extend(
        [
            "",
            "## 功能明细",
            "",
            "| ID | 类别 | 功能 | 状态 | 源码证据 | 测试证据 | 验收类型 | 验收证据 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{row['feature_id']}` | {row['category']} | {row['feature_name']} | "
            f"`{row['coverage_status']}` | `{row['local_source_evidence']}` | "
            f"`{row['test_evidence']}` | `{row['acceptance_kind']}` | `{row['acceptance_test']}` |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- `EXCLUDED` 和 `DEFERRED` 也是已审计终态, 不计入真实完成率。",
            "- 源码 MIT 许可不覆盖第三方数据、品牌素材或链邻桌面安装程序。",
            "- 台账由 `scripts/build_feature_coverage.py` 生成并在 CI 中逐文件复核。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = _load()
    summary = _summary(rows)
    expected_summary = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_doc = _markdown(rows, summary)
    if args.check:
        if SUMMARY_PATH.read_text(encoding="utf-8") != expected_summary:
            raise SystemExit("summary.json is not synchronized")
        if DOC_PATH.read_text(encoding="utf-8") != expected_doc:
            raise SystemExit("feature ledger Markdown is not synchronized")
        print(json.dumps(summary["totals"], ensure_ascii=False, sort_keys=True))
        return 0
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(expected_summary, encoding="utf-8")
    DOC_PATH.write_text(expected_doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
