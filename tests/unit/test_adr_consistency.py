"""Prove ADR-0002 and ADR-0003 cannot silently drift from the mechanisms that enforce them.

ADR-0002 (`docs/architecture/ADR-0002-two-data-planes.md`) and ADR-0003
(`docs/architecture/ADR-0003-numerical-stack-boundary.md`) each write down an architecture
decision that Task 4's import-linter gate (`pyproject.toml`'s `[tool.importlinter]` section)
already enforces mechanically. Prose can rot: a human edits one side of a decision and forgets
the other. These tests turn "the ADR still matches the gate" into an executable assertion
instead of a hope.

- ADR-0003 declares the exact set of modules `openalpha_cn.domain` must not import, inside a
  machine-parseable block. That set is compared against the live `domain-purity` contract's
  `forbidden_modules` in `pyproject.toml`, read with `importlinter.api.read_configuration` --
  the same helper `tests/unit/test_import_layering.py` already uses to inspect contract
  configuration. Either side drifting fails the comparison.
- ADR-0002 names concrete storage symbols as `` `path.py#Symbol` `` references. Each one is
  resolved with the AST symbol lookup `scripts/build_feature_coverage.py` already built for
  feature-ledger evidence (`_module_symbols`), loaded the same way
  `tests/unit/test_feature_ledger_symbols.py` loads that module, instead of writing a second
  resolver.

Placed alongside `test_import_layering.py` would have conflated two different concerns (the
live import graph vs. documentation-to-config consistency); this is a separate module because
it parses Markdown prose rather than exercising import-linter's contract engine.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from importlinter import api as importlinter_api

ROOT = Path(__file__).resolve().parents[2]
ADR_0002_PATH = ROOT / "docs" / "architecture" / "ADR-0002-two-data-planes.md"
ADR_0003_PATH = ROOT / "docs" / "architecture" / "ADR-0003-numerical-stack-boundary.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
BFC_MODULE_PATH = ROOT / "scripts" / "build_feature_coverage.py"

REQUIRED_SECTIONS = ("## Context", "## Decision", "## Consequences")

_FORBIDDEN_BLOCK = re.compile(
    r"<!-- domain-purity-forbidden-modules:start -->\s*```text\n(?P<body>.*?)\n```\s*"
    r"<!-- domain-purity-forbidden-modules:end -->",
    re.DOTALL,
)
_SYMBOL_REF = re.compile(r"`(?P<path>[\w./-]+\.py)#(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)`")


def _load_build_feature_coverage() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_feature_coverage_under_test", BFC_MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bfc = _load_build_feature_coverage()


def _domain_purity_forbidden_modules() -> set[str]:
    """The live `forbidden_modules` set of the `domain-purity` contract in pyproject.toml."""
    config = importlinter_api.read_configuration(str(PYPROJECT_PATH))
    for contract in config["contracts_options"]:
        if contract.get("id") == "domain-purity":
            modules = contract["forbidden_modules"]
            assert isinstance(modules, list)
            return set(modules)
    raise AssertionError("domain-purity contract not found in pyproject.toml's [tool.importlinter]")


def _adr_0003_forbidden_modules() -> set[str]:
    """The `domain/`-forbidden-import list ADR-0003 declares, parsed from its marker block."""
    text = ADR_0003_PATH.read_text(encoding="utf-8")
    match = _FORBIDDEN_BLOCK.search(text)
    assert match, (
        "ADR-0003 must contain a domain-purity-forbidden-modules block delimited by "
        "<!-- domain-purity-forbidden-modules:start/end --> markers around a ```text fence"
    )
    return {line.strip() for line in match["body"].splitlines() if line.strip()}


def test_adr_0002_two_data_planes_document_exists_with_required_sections() -> None:
    assert ADR_0002_PATH.is_file(), f"missing {ADR_0002_PATH}"
    text = ADR_0002_PATH.read_text(encoding="utf-8")
    missing = [heading for heading in REQUIRED_SECTIONS if heading not in text]
    assert not missing, f"ADR-0002 is missing section(s): {missing}"


def test_adr_0003_numerical_stack_boundary_document_exists_with_required_sections() -> None:
    assert ADR_0003_PATH.is_file(), f"missing {ADR_0003_PATH}"
    text = ADR_0003_PATH.read_text(encoding="utf-8")
    missing = [heading for heading in REQUIRED_SECTIONS if heading not in text]
    assert not missing, f"ADR-0003 is missing section(s): {missing}"


def test_adr_0003_domain_forbidden_import_list_matches_import_linter_domain_purity_contract() -> (
    None
):
    """The real drift guard: ADR-0003's declared list and the live contract's
    `forbidden_modules` must be the exact same set. Editing either side without the other
    must fail this test."""
    adr_modules = _adr_0003_forbidden_modules()
    contract_modules = _domain_purity_forbidden_modules()
    assert adr_modules == contract_modules, (
        "ADR-0003 vs pyproject.toml domain-purity forbidden_modules drifted: "
        f"only in ADR-0003={sorted(adr_modules - contract_modules)}, "
        f"only in pyproject.toml={sorted(contract_modules - adr_modules)}"
    )


def test_adr_0002_referenced_storage_symbols_actually_exist() -> None:
    """Every `` `path.py#Symbol` `` reference in ADR-0002 must resolve via the same AST
    symbol lookup the feature ledger uses (`scripts/build_feature_coverage.py`)."""
    text = ADR_0002_PATH.read_text(encoding="utf-8")
    refs = _SYMBOL_REF.findall(text)
    assert refs, "expected ADR-0002 to name at least one concrete storage symbol"
    missing = []
    for raw_path, symbol in refs:
        path = ROOT / raw_path
        if not path.exists():
            missing.append(f"{raw_path}#{symbol} (file does not exist)")
            continue
        if symbol not in bfc._module_symbols(path):
            missing.append(f"{raw_path}#{symbol} (symbol not found)")
    assert not missing, f"ADR-0002 references undefined symbols: {missing}"
