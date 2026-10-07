"""Prove `openalpha_cn.runtime`'s lazy `ResearchEngine` export (V2-P0B-001) actually works.

`runtime/__init__.py` resolves `ResearchEngine` through a module-level `__getattr__`
instead of an eager top-level import, specifically so that importing a lightweight
submodule such as `runtime.contracts` does not force-load `runtime.engine`'s SQLite
storage dependency (see the docstring on `runtime/__init__.py.__getattr__`). This test
covers the behavioral contract of that mechanism: the lazily resolved attribute is the
real class, and an unrelated attribute still raises `AttributeError` like any module.
"""

import pytest

import openalpha_cn.runtime as runtime_package
from openalpha_cn.runtime.engine import ResearchEngine as EngineModuleResearchEngine


def test_research_engine_is_lazily_resolvable_from_the_runtime_package() -> None:
    """`openalpha_cn.runtime.ResearchEngine` must be the exact class defined in
    `runtime.engine`, resolved on first access rather than at package-import time."""
    assert runtime_package.ResearchEngine is EngineModuleResearchEngine


def test_unknown_runtime_package_attribute_still_raises_attribute_error() -> None:
    """The lazy `__getattr__` must not swallow lookups for names that do not exist."""
    with pytest.raises(AttributeError, match="not_a_real_attribute"):
        _ = runtime_package.not_a_real_attribute  # type: ignore[attr-defined]
