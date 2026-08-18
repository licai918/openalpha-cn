"""`RunMode` is the only declaration of the mode set, held so by a source-tree audit.

`V2-P4-003` exists because the set was written out three times -- `RunManifest.mode`,
`ResearchRunRequest.mode`, and a `StrEnum` of Typer choices in `cli.py` -- and editing two of
the three left the suite green. `V2-P4-001` had to add `paper` and `daily` to all three, so
the issue was unavoidable rather than adjacent, and it is closed here by construction: there
is one declaration and the other two name it.

That makes the obvious test ("assert the three lists agree") vacuous -- it would compare an
object with itself. What is *not* vacuous, and is what this module asserts instead, is that
nobody has reintroduced a second declaration: `test_no_other_module_declares_the_mode_set`
reads the source tree rather than a list, so a fourth copy fails without anybody remembering
to register it. That is the same shape as
`tests/unit/test_known_limitation_registries.py::test_the_registry_table_is_every_known_registry_in_the_source_tree`,
and for the same reason -- a hand-maintained correspondence is what went stale last time.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn import cli
from openalpha_cn.domain.json_value import canonical_json_bytes
from openalpha_cn.domain.run import RunManifest, RunManifestV1
from openalpha_cn.domain.run_mode import RUN_MODES, RunMode
from openalpha_cn.domain.run_request import ResearchRunRequest

SOURCE_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "openalpha_cn"
DECLARING_MODULE: Final[Path] = SOURCE_ROOT / "domain" / "run_mode.py"

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64


def _manifest(mode: RunMode | str) -> RunManifest:
    return RunManifest(
        run_id="run_mode_probe",
        mode=mode,  # type: ignore[arg-type]
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=NOW,
        status="running",
    )


def _request(mode: RunMode | str) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id="run_mode_probe",
        mode=mode,  # type: ignore[arg-type]
        subject="000001.SZ",
        as_of=NOW,
        evidence=(),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )


def test_every_declared_mode_reaches_both_contracts_and_the_cli() -> None:
    """The single-source property, exercised through all three former declaration sites.

    Iterating `RUN_MODES` rather than listing five strings is the point: a member added to the
    enum is exercised here without this test being edited, which is what "two of three" can no
    longer mean.
    """
    assert [mode.value for mode in RUN_MODES] == ["live", "replay", "backtest", "paper", "daily"]

    for mode in RUN_MODES:
        assert _manifest(mode).mode is mode
        assert _request(mode).mode is mode

    assert cli.RunMode is RunMode
    option_default = cli.research_run.__defaults__
    assert option_default is not None
    assert RunMode.live in option_default


@pytest.mark.parametrize("mode", ["paper", "daily"])
def test_the_two_new_modes_are_accepted_where_the_three_old_ones_were(mode: str) -> None:
    """`V2-P4-001`'s actual addition, from the string side a JSON caller uses."""
    assert _manifest(mode).mode == mode
    assert _request(mode).mode == mode


def test_an_undeclared_mode_is_still_refused() -> None:
    """Widening the set is not the same as opening it; `paper_trading` is not `paper`."""
    with pytest.raises(ValidationError, match="paper_trading"):
        _manifest("paper_trading")


def test_the_enum_serialises_to_the_bare_string_the_literal_did() -> None:
    """The reason replacing the `Literal` with a `StrEnum` moved no stored bytes.

    Two things read these dumps and would have been silently rewritten by a representation
    change: the `runs` table's payload, and `ResearchEngine._load_or_start_recovery`'s
    `request_digest`, which is `sha256` over the request's canonical JSON. The digest is
    asserted here as bytes rather than as "it still works", because a digest that changed
    would not fail -- it would make every stored recovery row look like a conflicting request.
    """
    assert _manifest(RunMode.live).model_dump(mode="json")["mode"] == "live"
    assert '"mode":"live"' in _manifest(RunMode.live).model_dump_json()
    assert b'"mode":"live"' in canonical_json_bytes(_request(RunMode.live).model_dump(mode="json"))


def _string_constants_outside_docstrings(path: Path) -> set[str]:
    """Every `str` literal in `path` that is not a module/class/function docstring.

    Prose has to be excluded or the audit becomes unfalsifiable: this repository documents
    heavily, and a docstring naming all three original modes is exactly the "the code says it
    twice, one of them in prose" mistake `test_known_limitation_registries.py` was built to
    stop counting.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


RUN_MANIFEST_V1_MODES: Final[frozenset[str]] = frozenset({"live", "replay", "backtest"})
"""The mode set `run-manifest/v1` accepted, which `RunManifestV1` is required to still spell.

The one exemption `test_no_other_module_declares_the_mode_set` grants, and it is granted as an
*equality* rather than as a filename on a skip list: `domain/run.py` may restate the three
modes v1 knew -- a frozen snapshot has to say what it froze, or it is describing the current
model instead -- and may not restate the current five. A contributor who copies today's set
back into `run.py` therefore fails the exemption they were reaching for.
"""


def test_no_other_module_declares_the_mode_set() -> None:
    """The audit that keeps `V2-P4-003` closed once `V2-P4-001` has closed it.

    "Declares the set" is read as "spells at least three of the five mode names as executable
    string literals", which is what a `Literal[...]`, a second `StrEnum`, or a tuple of
    choices all look like -- and is loose enough that an unrelated module mentioning one or
    two of these words in passing does not trip it. Prose is excluded, because a docstring
    listing the modes is not a declaration and counting it would make the audit unfalsifiable
    in a repository that documents this much.
    """
    names = {mode.value for mode in RunMode}
    declared = {
        path: sorted(names & _string_constants_outside_docstrings(path))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if len(names & _string_constants_outside_docstrings(path)) >= 3
    }

    assert set(declared) == {DECLARING_MODULE, SOURCE_ROOT / "domain" / "run.py"}
    assert set(declared[DECLARING_MODULE]) == names
    assert set(declared[SOURCE_ROOT / "domain" / "run.py"]) == RUN_MANIFEST_V1_MODES


def test_the_frozen_v1_snapshot_still_refuses_the_modes_v1_never_had() -> None:
    """The other half of the exemption above, measured on the class rather than its source.

    `RunManifestV1` exists so a stored v1 row can be validated before it is rewritten, and a
    snapshot that quietly accepted `paper` would let the migration re-version a row that was
    never legal at v1 -- repairing data while claiming to re-version it.
    """
    with pytest.raises(ValidationError, match="paper"):
        RunManifestV1(
            run_id="run_mode_probe",
            mode="paper",  # type: ignore[arg-type]
            as_of=NOW,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
            started_at=NOW,
            status="running",
        )
