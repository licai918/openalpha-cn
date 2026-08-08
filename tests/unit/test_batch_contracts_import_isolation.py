"""Prove, in a fresh interpreter, that `openalpha_cn.batch_contracts` stays storage-free.

V2-P0B-012 moved `BatchResearchTask`/`BatchProgressEvent` (and the `BatchTaskItem`/
`BatchResultRef` types `BatchResearchTask` embeds) out of `runtime/batch.py` into a new
top-level module, `openalpha_cn.batch_contracts`, so `storage/batch.py` can depend on them
without importing anything under `openalpha_cn.runtime` (forbidden by the
`storage-no-upward-deps` import-linter contract -- see
`tests/unit/test_import_layering.py` and `tests/unit/test_storage_contract_relocation.py`).

`tests/unit/runtime/test_contracts_import_isolation.py` proved the analogous property for
`runtime/__init__.py`'s lazy `ResearchEngine` resolution, and its docstring explains why a
static, AST-level tool like `grimp` cannot see the difference between an eager top-level
import and one gated behind a runtime check: both look like the same edge to static
analysis. That reasoning applies here too, even though `batch_contracts.py` has no lazy
gate of its own to protect -- what matters is that nothing *else* in its own import chain
(`runtime.contracts`, and everything `runtime/__init__.py` eagerly imports as a side effect
of that) pulls `openalpha_cn.storage` in along the way. `test_storage_contract_relocation
.py::test_batch_contracts_module_does_not_depend_on_agents_product_backtest_or_storage`
already proves this statically; this module proves it dynamically, in a brand-new process,
as a second independent check the way `test_contracts_import_isolation.py` does for
`runtime.contracts` -- guarding against the specific failure mode this task's brief warns
about: a future edit that hides an edge from static analysis (e.g. a deferred
function-local import) rather than removing it. A hidden edge would still leave this
subprocess probe green only if the storage dependency were genuinely never imported; if
someone later added an eager `import openalpha_cn.storage...` anywhere in this chain, this
test goes red exactly like `test_contracts_import_isolation.py` would for `runtime.engine`.
"""

from __future__ import annotations

import json
import subprocess
import sys

_PROBE_SCRIPT = """
import json
import sys

import openalpha_cn.batch_contracts  # noqa: F401

leaked = sorted(
    module
    for module in sys.modules
    if module == "openalpha_cn.storage" or module.startswith("openalpha_cn.storage.")
)
print(json.dumps(leaked))
"""


def test_importing_batch_contracts_in_a_fresh_process_does_not_load_the_storage_layer() -> None:
    """A fresh-process `import openalpha_cn.batch_contracts` must not pull
    `openalpha_cn.storage` (or any of its submodules) into `sys.modules`.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "probe subprocess failed to import openalpha_cn.batch_contracts "
        f"(exit {result.returncode}):\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    leaked = json.loads(result.stdout)
    assert not leaked, (
        "importing openalpha_cn.batch_contracts in a fresh interpreter pulled these "
        f"openalpha_cn.storage module(s) into sys.modules: {leaked}."
    )
