"""Where `AlphaModel` may live, derived from the `lint-imports` contracts rather than argued.

`V2-P4-023` and `V2-P4-032` both settled a layer by elimination and wrote the elimination down;
this module runs it. Four issues on the `V2-P4-010`..`017` chain consume this contract from four
different packages, and the question "which package can all four reach" has an answer that
`pyproject.toml` already contains -- so it is computed here, off the parsed configuration, and a
contract relaxed later makes this go red instead of making `domain/alpha_model.py`'s docstring go
stale.

The answer is **not** a unique survivor, and saying so is the point: the contracts narrow
thirteen subpackages to two, `domain` and `tools`, and the choice between those two is a
judgement this file states rather than a gate it pretends to have.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import grimp
from importlinter import api as importlinter_api

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
PACKAGE_ROOT: Final[Path] = ROOT / "src" / "openalpha_cn"

CONSUMERS: Final[dict[str, str]] = {
    "openalpha_cn.storage": (
        "V2-P4-017 persists a PredictionBatch before the outcome is known, which means "
        "deserializing one, which means importing it"
    ),
    "openalpha_cn.backtest": (
        "V2-P4-013's walk-forward split is a study over labelled panel rows and belongs beside "
        "the other twelve"
    ),
}
"""The two consumers whose reach is *constrained by a contract*, with why each needs the import.

`runtime/` (V2-P4-010's manifest slot) and a future numeric-baseline package (V2-P4-015) are
consumers too and are not listed: neither is the source of any contract, so neither narrows the
answer. Listing them would make this computation look stronger than it is.
"""


def _subpackages() -> set[str]:
    """Every directory under `src/openalpha_cn/` that is a package."""
    return {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }


def _contracts() -> list[dict[str, object]]:
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contracts = config["contracts_options"]
    assert isinstance(contracts, list)
    return contracts


def _forbidden_for(consumer: str) -> set[str]:
    """Every module any `forbidden` contract keeps `consumer` (or a module of it) away from."""
    forbidden: set[str] = set()
    for contract in _contracts():
        sources = contract["source_modules"]
        assert isinstance(sources, list)
        if not any(source == consumer or source.startswith(f"{consumer}.") for source in sources):
            continue
        targets = contract["forbidden_modules"]
        assert isinstance(targets, list)
        forbidden.update(str(target) for target in targets)
    return forbidden


def test_the_contracts_narrow_thirteen_subpackages_to_domain_and_tools() -> None:
    """The elimination, run: which subpackage can both constrained consumers import?

    A package is out if either consumer is forbidden from reaching it, and a consumer is out as
    a home for itself when the *other* consumer may not reach it -- which is what rules out
    `backtest/` (`storage-no-upward-deps` forbids it) and `storage/`
    (`backtest-studies-touch-no-store` forbids it). `models/` is ruled out by
    `backtest-no-numeric-stack-or-panel-plane`, which names `openalpha_cn.models` outright: the
    row's "strictly separate from the LLM plane" turns out to be enforced already, from the
    other direction, and that is the finding this test exists to record.
    """
    candidates = _subpackages()
    assert len(candidates) == 13, sorted(candidates)

    survivors = {
        candidate
        for candidate in candidates
        if not any(
            f"openalpha_cn.{candidate}" in _forbidden_for(consumer) for consumer in CONSUMERS
        )
    }

    assert survivors == {"domain", "tools"}
    assert "openalpha_cn.models" in _forbidden_for("openalpha_cn.backtest")
    assert "openalpha_cn.backtest" in _forbidden_for("openalpha_cn.storage")
    assert "openalpha_cn.storage" in _forbidden_for("openalpha_cn.backtest")


def test_tools_is_the_survivor_this_repository_already_answered_about() -> None:
    """Why `domain` and not `tools`, stated as two measurements rather than a preference.

    `tools/` holds `ResearchTool` implementations and already imports `domain/`; `domain/` is
    where `V2-P0B-012` moved all five contracts `storage/` had to deserialize, and where
    `domain/labels.py` -- the type `TrainingExample` is expressed in -- already lives. A
    contract in `tools/` would additionally be unreachable from `domain/` forever, because
    `domain-purity` forbids every sibling subpackage, so no later domain contract could
    reference an `AlphaModel`.
    """
    graph = grimp.build_graph("openalpha_cn", cache_dir=None)

    assert graph.find_downstream_modules("openalpha_cn.domain", as_package=True) >= {
        "openalpha_cn.tools.base",
        "openalpha_cn.tools.evidence",
    }
    assert not graph.find_downstream_modules("openalpha_cn.tools", as_package=True) & {
        f"openalpha_cn.domain.{module.stem}" for module in (PACKAGE_ROOT / "domain").glob("*.py")
    }
    assert graph.direct_import_exists(
        importer="openalpha_cn.domain.alpha_model",
        imported="openalpha_cn.domain.labels",
    )


def test_the_contract_module_imports_no_sibling_subpackage_and_no_numeric_library() -> None:
    """`domain-purity` covers this by package; this reads the module's own import list.

    Independent of the contract's static `forbidden_modules` enumeration for the same reason
    `test_import_layering.py` re-derives siblings from the directory: the enumeration is a list
    somebody has to remember to extend, and this is not.
    """
    tree = ast.parse((PACKAGE_ROOT / "domain" / "alpha_model.py").read_text(encoding="utf-8"))
    imported = {
        node.module.split(".")[0] if node.module else ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not imported & {"numpy", "pandas", "scipy", "sklearn", "duckdb", "sqlite3"}
    subpackage_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("openalpha_cn.")
        and not node.module.startswith("openalpha_cn.domain.")
    }
    assert subpackage_imports == set()


def test_the_reference_model_joined_both_backtest_study_contracts_on_arrival() -> None:
    """The thirteenth `backtest/*.py`, in both per-module source lists.

    `tests/unit/test_import_layering.py::
    test_the_two_backtest_study_contracts_cover_every_module_in_the_package` is what makes this
    mandatory rather than polite; this asserts the specific outcome so a reader of *this* issue
    sees it without going to find that one.
    """
    for contract_id in (
        "backtest-studies-touch-no-store",
        "backtest-studies-reach-no-composition-root",
    ):
        contract = next(item for item in _contracts() if item.get("id") == contract_id)
        sources = contract["source_modules"]
        assert isinstance(sources, list)
        assert "openalpha_cn.backtest.alpha_model" in sources

    ranking = next(
        item for item in _contracts() if item.get("id") == "ranking-creates-no-portfolio-order"
    )
    ranking_sources = ranking["source_modules"]
    assert isinstance(ranking_sources, list)
    assert "openalpha_cn.backtest.alpha_model" not in ranking_sources, (
        "the ranking contract's two sources are the candidate list and its gate; a model that "
        "produces a number for every security in a cross section is neither, and adding it "
        "there would forbid domain/portfolio.py to a module that has no reason to want it"
    )
