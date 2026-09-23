"""Every content-address example in the HTTP reference must be one this build mints (V2-P4-067).

`V2-P4-067`(a) measured a caller copying `docs/api/http.md`'s
`"run_manifest_id": "rmf_…"` straight into a request and being refused with
`carries run_manifest_id 'rmf_…', which is not stable_model_id(prefix='run', ...)'s own
output` -- the documented prefix was three letters this repository has never minted. It was
repaired in passing by `V2-P4-032`/`V2-P4-049` while those rows were rewriting the shortlist
section, so at `be262ea` the doc reads `"run_…"` and the row's reproduction no longer fires.

Repaired in passing is not the same as held. Nothing pinned the doc to the minting function, so
the next hand-written example is free to invent another prefix, and the failure mode is silent:
a wrong prefix in prose costs a reader one refused request each, and costs CI nothing.

**How this is kept from being a copy of the same mistake.** The expected prefix is never written
down here. `test_the_run_manifest_id_example_carries_the_prefix_the_contract_mints` derives it by
building a real `RunManifest` and reading its address, and
`test_every_content_address_example_in_the_doc_is_a_prefix_this_build_mints` checks every
`<prefix>_…` example in the file against `live_prefixes()`, which
`tests/unit/domain/test_manifest_component_provenance.py` reads off the source tree by AST for
exactly this class of reason (`V2-P4-016` found the hand-written census stale in two directions
at once). Both are proved to have teeth by
`test_the_doc_audit_rejects_the_prefix_the_row_measured`, which puts `rmf_` back into a copy of
the file and requires the audit to catch it.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from openalpha_cn.domain.run import RunManifest

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
HTTP_DOC: Final[Path] = ROOT / "docs" / "api" / "http.md"
NOW: Final[datetime] = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _load_live_prefixes() -> Callable[[], list[str]]:
    """Borrow `live_prefixes` from the unit test that owns the census.

    Loaded by path rather than imported by name because `tests/` carries no `__init__.py`, so
    `tests.unit.domain...` is not a package and `pytest`'s `prepend` import mode only puts
    `tests/unit/domain` on `sys.path` when a module *from that directory* is collected -- which
    a targeted run of this file does not do. Re-deriving the census here instead would put a
    second copy of the very list `V2-P4-016` found stale into the tree.
    """
    path = ROOT / "tests" / "unit" / "domain" / "test_manifest_component_provenance.py"
    spec = importlib.util.spec_from_file_location("_manifest_component_provenance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.live_prefixes


live_prefixes = _load_live_prefixes()

# `abc_…`, `abc_...`, or `abc_0000…` -- the three ways this file writes an elided address.
_EXAMPLE = re.compile(r"\b([a-z][a-z0-9]{1,7})_(?:…|\.\.\.|[0-9a-f]{6,})")


def _minted_run_manifest_id() -> str:
    return RunManifest(
        run_id="doc-prefix-probe",
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest="b" * 64,
        random_seed=7,
        started_at=NOW,
        finished_at=NOW,
        status="succeeded",
    ).run_manifest_id


def _example_prefixes(text: str) -> set[str]:
    return {match.group(1) for match in _EXAMPLE.finditer(text)}


def test_the_run_manifest_id_example_carries_the_prefix_the_contract_mints() -> None:
    """The row's own reproduction: copy the documented address and it must be the right shape."""
    minted = _minted_run_manifest_id()
    prefix = minted.split("_", 1)[0]
    doc = HTTP_DOC.read_text(encoding="utf-8")

    documented = re.findall(r'"run_manifest_id":\s*\n?\s*"([a-z][a-z0-9]*)_', doc)

    assert documented, "the HTTP reference shows no `run_manifest_id` example to check"
    assert set(documented) == {prefix}, (
        f"the reference documents {sorted(set(documented))} where this build mints {prefix!r} "
        f"(e.g. {minted})"
    )
    assert "rmf" not in _example_prefixes(doc), "the prefix `V2-P4-067` measured is back"


def test_every_content_address_example_in_the_doc_is_a_prefix_this_build_mints() -> None:
    """No hand-written example may invent a prefix, whichever identifier it is about."""
    doc = HTTP_DOC.read_text(encoding="utf-8")
    known = set(live_prefixes())
    found = _example_prefixes(doc)

    assert found, "the regex matched no address example at all, so this test proves nothing"
    invented = sorted(found - known)
    assert not invented, (
        f"{invented} appear as content-address examples in docs/api/http.md and are minted "
        "nowhere in this repository"
    )


def test_the_doc_audit_rejects_the_prefix_the_row_measured() -> None:
    """The audit above is worthless unless it fails on the exact text the row reported.

    Run against a rewritten copy of the real file rather than a synthetic string, so it is the
    shipped document's own wording that is proved catchable.
    """
    doc = HTTP_DOC.read_text(encoding="utf-8")
    regressed = doc.replace('"run_manifest_id":\n"run_…"', '"run_manifest_id":\n"rmf_…"').replace(
        '"run_manifest_id":\n    "run_…"', '"run_manifest_id":\n    "rmf_…"'
    )
    if regressed == doc:  # the doc wraps the pair across a line; do it textually instead
        regressed = doc.replace('"run_…"', '"rmf_…"')
    assert regressed != doc, "could not synthesise the regression, so the audit is unproven"

    assert "rmf" in _example_prefixes(regressed)
    assert "rmf" not in set(live_prefixes())
    documented = re.findall(r'"run_manifest_id":\s*\n?\s*"([a-z][a-z0-9]*)_', regressed)
    assert documented == ["rmf"], (
        "the regression was written into the file but the `run_manifest_id` audit would not "
        "have seen it"
    )
