"""Prove, in a fresh interpreter, that `openalpha_cn.runtime.contracts` stays storage-free.

V2-P0B-001 moved `ResearchRunRequest`, `ResearchRunResult`, and `RunConflictError` out of
`runtime/engine.py` into `runtime/contracts.py` so contract-only consumers stop dragging in
`ResearchEngine`'s SQLite storage dependency. `runtime/__init__.py` resolves `ResearchEngine`
lazily through a module-level `__getattr__` (PEP 562) specifically so that importing any
lightweight submodule of the package -- `runtime.contracts` included -- does not force
`runtime/__init__.py` to eagerly import `runtime.engine` (see the docstring on
`runtime/__init__.py.__getattr__`).

A reviewer proved that guard has no automated backstop: `tests/unit/test_import_layering.py`
uses `grimp.build_graph` for every dependency-direction assertion in this codebase, but grimp
is a static AST-level tool. It records a top-level `from x import y` and a `__getattr__`-gated
`from x import y` inside a function body as the *same* edge -- it cannot see that one runs at
import time and the other only runs when the attribute is actually accessed. So a future
"simplification" that turns `runtime/__init__.py`'s lazy resolution back into an eager
top-level import would sail through every grimp-based test in this repository, `mypy`, and
`ruff`, while silently reintroducing the exact coupling this task removed.

This module closes that gap the only way it can be closed: by actually importing
`openalpha_cn.runtime.contracts` in a brand-new Python process and inspecting that process's
real `sys.modules`. This cannot be checked in-process -- by the time any test in this suite
runs, pytest has already imported most of `openalpha_cn` (including `runtime.engine` and the
storage layer it owns) as a side effect of collection, so an in-process `sys.modules` check
would trivially pass regardless of whether `runtime/__init__.py` is eager or lazy. Subprocess
isolation, launched with `sys.executable` so it runs under the same interpreter/venv as the
test suite itself, is therefore essential, not incidental.

This is a narrow, deliberate exception to the general prohibition (Task 8's brief) on runtime
`sys.modules` checks. That prohibition targets the dependency-*direction* assertions in
`test_import_layering.py`, where grimp's static analysis is the right tool and a runtime check
would be redundant. Here static analysis is provably blind to the property under test (eager
vs. lazy execution timing), so a runtime check is the only tool that can back this guard at
all.

## Why this only checks `openalpha_cn.storage`, not bare `sqlite3`

The most literal version of this guard would also assert `"sqlite3" not in sys.modules`.
That assertion was tried first and found to fail even against the current, correct, lazy
`runtime/__init__.py` -- `sqlite3` is *already* pulled into `sys.modules` by a fresh
`import openalpha_cn.runtime.contracts`, through a chain that has nothing to do with
`ResearchEngine`: `runtime.contracts` imports `openalpha_cn.agents.base`, which (because
Python always fully executes a package's `__init__.py` before importing any of its
submodules) runs `agents/__init__.py`, which imports `agents.model`, which imports
`openalpha_cn.models.base`, which likewise runs `models/__init__.py`, which imports
`models.openai_compatible`, which imports `models.governance` -- and `models/governance.py`
does `import sqlite3` directly at module scope, for `ModelRegistry`'s durable usage
accounting.

That specific edge, `openalpha_cn.models.governance -> sqlite3`, is not a bug this test
should chase: it is a pre-existing, already-tracked layering exemption (baseline entry
`V2-P0B-011` in `docs/architecture/import-layering-baseline.toml`, mirrored as an
`ignore_imports` line on the `models-no-infra-imports` contract in `pyproject.toml`), wholly
unrelated to `runtime/__init__.py`'s lazy `ResearchEngine` resolution and out of this task's
scope to fix. Asserting bare `sqlite3 not in sys.modules` here would make this test fail
permanently regardless of whether the guard under test is eager or lazy -- it would not
discriminate the regression it exists to catch. So, matching the precedent already set by
`test_import_layering.py`'s `ENGINE_OWNED_STORAGE_MODULES` (which scopes its transitive
check to `storage.recovery`/`storage.sqlite` specifically, for the same reason -- see that
module's comments), this test checks only for `openalpha_cn.storage` and its submodules:
exactly what an eager `from openalpha_cn.runtime.engine import ResearchEngine` at the top of
`runtime/__init__.py` would additionally pull in.
"""

from __future__ import annotations

import json
import subprocess
import sys

# `openalpha_cn.storage.recovery` and `openalpha_cn.storage.sqlite` are the two concrete
# modules `runtime.engine` imports for `ResearchEngine`'s persistence. Matching the whole
# `openalpha_cn.storage` prefix, not just those two, also catches an eager import dragging in
# the storage package's own `__init__.py` re-exports.
_PROBE_SCRIPT = """
import json
import sys

import openalpha_cn.runtime.contracts  # noqa: F401

leaked = sorted(
    module
    for module in sys.modules
    if module == "openalpha_cn.storage" or module.startswith("openalpha_cn.storage.")
)
print(json.dumps(leaked))
"""


def test_importing_runtime_contracts_in_a_fresh_process_does_not_load_the_storage_layer() -> None:
    """A fresh-process `import openalpha_cn.runtime.contracts` must not pull
    `openalpha_cn.storage` (or any of its submodules) into `sys.modules`.

    If this ever goes red, it means `runtime/__init__.py`'s `ResearchEngine` resolution has
    regressed from lazy (`__getattr__`, PEP 562) back to an eager top-level import: Python
    always fully executes a package's `__init__.py` before importing any of its submodules,
    so an eager `from openalpha_cn.runtime.engine import ResearchEngine` at the top of
    `runtime/__init__.py` re-establishes the exact SQLite coupling V2-P0B-001 removed, for
    every consumer that only ever wanted the lightweight contracts.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "probe subprocess failed to import openalpha_cn.runtime.contracts "
        f"(exit {result.returncode}):\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    leaked = json.loads(result.stdout)
    assert not leaked, (
        "importing openalpha_cn.runtime.contracts in a fresh interpreter pulled these "
        f"openalpha_cn.storage module(s) into sys.modules: {leaked}. This means "
        "runtime/__init__.py's ResearchEngine resolution is no longer lazy -- see the "
        "docstring on its module-level __getattr__."
    )
