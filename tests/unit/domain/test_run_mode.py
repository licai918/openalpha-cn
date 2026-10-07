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


def _folded_literal(node: ast.AST) -> str | None:
    """`node` as the string it spells if it is one written out in full, else `None`.

    Adjacent literals -- `"future" "_data"` -- never reach here: the parser folds them into a
    single `ast.Constant` before the tree exists, which is why the audit already caught that
    spelling. `"look_ahead" + "_violation"` is a different node entirely, an `ast.BinOp` whose
    two halves are each a non-name, and `V2-P4-107` measured what that was worth: it passed.

    Folding it is not a claim to have closed the class. It closes the one spelling that was
    arbitrarily different from a spelling already caught -- the two mean the same thing to the
    reader and to the interpreter, and catching one while missing the other was an accident of
    where CPython does its constant folding rather than a line anybody drew.
    `test_a_flag_set_assembled_at_run_time_is_invisible_to_this_audit` states what is left.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded_literal(node.left), _folded_literal(node.right)
        return None if left is None or right is None else left + right
    return None


def _string_constants_outside_docstrings(path: Path) -> set[str]:
    """Every `str` literal in `path` that is not a docstring, with `+`-joined literals folded.

    Prose is excluded so that a module documenting the vocabulary cannot be read as declaring
    it -- the "the code says it twice, one of them in prose" mistake
    `test_known_limitation_registries.py` was built to stop counting. `V2-P4-107` measured what
    the filter is worth on today's tree and the answer is nothing: the comparison is exact
    equality between a whole `ast.Constant` and a mode name, so a docstring *mentioning* a mode
    never matches, and counting docstrings moves this audit's answer on no module of `src/`.
    It is kept for the case that would match -- a docstring that is exactly a mode name -- and
    `test_a_docstring_that_is_exactly_a_mode_name_is_not_a_declaration` is that case.
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
        spelled
        for node in ast.walk(tree)
        if id(node) not in docstrings and (spelled := _folded_literal(node)) is not None
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


def test_a_plus_joined_mode_name_is_folded_into_the_name_it_spells(tmp_path: Path) -> None:
    """`V2-P4-107` over this audit's copy of the helper, which shares the whole defect.

    `test_risk_flag.py` is where the finding was filed and this is the identical extractor, so
    the identical escape applied: adjacent literals are folded by the parser and were caught,
    `"back" + "test"` was not. Both are caught now, and both halves are asserted here rather
    than in one file only -- a shared defect fixed in one of two copies is a defect that comes
    back through the copy nobody tested.
    """
    names = {mode.value for mode in RunMode}
    spelled = sorted(names)[:2]
    assert len(spelled) == 2, "this audit needs two mode names to have anything to fold"

    written_out = tmp_path / "written_out.py"
    written_out.write_text(f"_legacy = frozenset({{{spelled[0]!r}, {spelled[1]!r}}})\n", "utf-8")
    assert names & _string_constants_outside_docstrings(written_out) == set(spelled)

    joined = tmp_path / "joined.py"
    joined.write_text(
        f"_legacy = frozenset({{{spelled[0][:2]!r} {spelled[0][2:]!r}, "
        f"{spelled[1][:2]!r} + {spelled[1][2:]!r}}})\n",
        "utf-8",
    )
    assert names & _string_constants_outside_docstrings(joined) == set(spelled), (
        "a `+`-joined mode name is not folded, so a declaration one token away from the "
        "spelling this audit catches walks past it -- the V2-P4-107 escape, in this file"
    )


def test_a_docstring_that_is_exactly_a_mode_name_is_not_a_declaration(tmp_path: Path) -> None:
    """The docstring filter, exercised -- because `src/` does not exercise it.

    Measured by `V2-P4-107`: counting docstrings changes this audit's answer on no module of
    the tree, because the comparison is exact equality against a whole constant. This is the
    case where the filter decides something, written here because the repository has none.
    """
    names = {mode.value for mode in RunMode}
    spelled = sorted(names)[:2]
    module = tmp_path / "prose.py"
    module.write_text(f'"""{spelled[0]}"""\n_legacy = frozenset({{{spelled[1]!r}}})\n', "utf-8")
    assert names & _string_constants_outside_docstrings(module) == {spelled[1]}
