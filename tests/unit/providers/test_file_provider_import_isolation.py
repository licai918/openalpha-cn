"""Prove, in a fresh interpreter, that `openalpha_cn.providers.file` stays duckdb-free.

V2-P0B-011 first "fixed" the `providers.file -> duckdb` baseline entry by replacing a
top-level `import duckdb` with `importlib.import_module("duckdb")` inside `fetch()`'s call
path. A reviewer proved that was lint evasion, not decoupling: `duckdb` stayed out of
`sys.modules` after merely *importing* `providers.file` (satisfying import-linter's static
`forbidden` contract, and this test's letter), but `fetch()` on any `.parquet` file still
imported and used `duckdb` immediately -- the runtime coupling ADR-0001 exists to prevent
was still 100% intact, just invisible to static analysis.

This follow-up gives `FileProvider` an injected `parquet_reader` dependency (see
`providers/file.py#ParquetReader`) instead. The genuine test of that fix is not merely
"`duckdb` is absent from a fresh `import openalpha_cn.providers.file`" -- the dynamic-import
version already satisfied that trivially. It is that `duckdb` stays absent from `sys.modules`
in a fresh process, matching the subprocess-isolation pattern
`tests/unit/runtime/test_contracts_import_isolation.py` established, because an in-process
check would be meaningless: pytest has already imported most of `openalpha_cn` (and
transitively `duckdb`, via `storage.parquet`) as a side effect of collection.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PROBE_SCRIPT = """
import json
import sys

import openalpha_cn.providers.file  # noqa: F401

print(json.dumps("duckdb" in sys.modules))
"""


def test_importing_providers_file_in_a_fresh_process_does_not_load_duckdb() -> None:
    """A fresh-process `import openalpha_cn.providers.file` must not pull `duckdb` into
    `sys.modules`.

    Passing this alone is *not* sufficient proof of a real decoupling -- the reverted
    `importlib.import_module("duckdb")` approach also passed it. See
    `test_fetching_a_parquet_file_without_an_injected_reader_never_imports_duckdb` below
    for the check that actually distinguishes "duckdb is unreachable" from "duckdb is
    reached later, just not statically".
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "probe subprocess failed to import openalpha_cn.providers.file "
        f"(exit {result.returncode}):\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    leaked = json.loads(result.stdout)
    assert leaked is False, (
        "importing openalpha_cn.providers.file in a fresh interpreter pulled duckdb into "
        "sys.modules."
    )


_FETCH_PROBE_SCRIPT = """
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from openalpha_cn.providers.base import ProviderMetadata, ProviderRequest
from openalpha_cn.providers.file import FileProvider

metadata = ProviderMetadata(
    provider_id="user.file",
    display_name="User-owned file",
    source_license="user-supplied",
    redistribution="restricted",
    credential_env_vars=(),
    caching_policy="local-permitted",
    rate_limit="not-applicable",
    freshness="defined-by-input-file",
    failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    supported_datasets=("events",),
)
path = Path(sys.argv[1])
provider = FileProvider(path=path, metadata=metadata)
try:
    provider.fetch(ProviderRequest(dataset="events", as_of=datetime.now(UTC)))
except Exception as error:  # noqa: BLE001 -- the probe reports whatever surfaced, on purpose
    loaded = "duckdb" in sys.modules
    print(json.dumps({"duckdb_loaded": loaded, "error_type": type(error).__name__}))
else:
    print(json.dumps({"duckdb_loaded": "duckdb" in sys.modules, "error_type": None}))
"""


def test_fetching_a_parquet_file_without_an_injected_reader_never_imports_duckdb(
    tmp_path: Path,
) -> None:
    """Even the actual `.parquet` read path -- not just the module-level import -- must
    never reach `duckdb` when `FileProvider` was constructed without a `parquet_reader`.

    This is the check that would have failed the reverted `importlib.import_module`
    approach: there, `fetch()` on a `.parquet` file always imported and used `duckdb`,
    regardless of whether a reader was supplied, because none could be. Here, a
    `FileProvider` built the way the six contract-test call sites that don't care about
    Parquet build it (no `parquet_reader` kwarg at all) must fail with a structured,
    caught `ProviderFailure`-shaped error -- never a `duckdb` import, successful or not.
    """
    source = tmp_path / "events.parquet"
    source.write_bytes(b"not a real parquet file")

    result = subprocess.run(
        [sys.executable, "-c", _FETCH_PROBE_SCRIPT, str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"probe subprocess crashed (exit {result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["duckdb_loaded"] is False, (
        "fetch() on a .parquet file with no injected parquet_reader pulled duckdb into "
        "sys.modules; it should fail with ProviderFailure without ever reaching duckdb."
    )
    assert payload["error_type"] == "ProviderFailure", (
        "fetch() on a .parquet file with no injected parquet_reader must raise a "
        f"structured ProviderFailure, not {payload['error_type']!r} (e.g. an unwrapped "
        "ModuleNotFoundError -- the degraded failure mode this task also fixes)."
    )
