"""Every `KNOWN_*` registry, held to the suite by structure rather than by convention.

`V2-P2-007` invented a binding and proved it works: each `KNOWN_EXECUTION_LIMITATIONS` code
must be the suffix of a test function declared in the same module, checked off that module's
AST. Three separate attempts to break it -- renaming a registry entry, renaming its test,
adding a fourth entry with no test -- each go red. **It was installed on exactly one of the
registries.** All the others were held together by the convention that somebody would
write a test, and the P2 review measured what that is worth: renaming
`KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_downstream_check` --
the entry `V2-P2-008`'s whole finding rests on, and the one
`tests/integration/test_injection_register.py`'s disclosed exclusion points a reader at by
name -- left the entire suite green. Its name occurred twice in the repository, both times
inside a docstring, which is prose the runner never reads.

## Why the mechanism is not copied as it stands

`007`'s form is "one test per entry, named after the entry". At three entries that is exactly
right. Across the twenty-two code-carrying registries there are **211** entries -- 69 in
`KNOWN_PANEL_LIMITATIONS` alone -- and it stops being right somewhere well before that, for
three reasons and not one:

1. **The volume is not the real objection; the shape is.** Most of these entries are
   disclosures about what the *upstream* does not serve -- "`stock_basic` carries no
   announcement date", "`index_member_all` has no revision history". There is no branch to
   drive and no refusal to provoke, so a test named after such an entry would assert something
   about the sentence rather than about the code, and a test that asserts about a sentence is
   the drift this whole idea exists to stop wearing the badge of the fix.
2. **`KNOWN_PANEL_LIMITATIONS` is derived.** `panel_doctor._limitations()` folds seven dataset
   registries one-for-one and `KNOWN_STORAGE_LIMITATIONS` plane-wide, so 64 of its 65 codes are
   already somebody else's. Requiring a test named after each would mean writing every one of
   those tests twice, in a module that has nothing to say about them.
3. **A per-entry test is only as strong as its body.** `007`'s three have real bodies because
   somebody wrote them that way; the AST check cannot tell `def test_<code>(): pass` from a
   proof. The naming convention is what is enforced, not the exercising.

**Those two totals are the argument, so they are not left as prose.**
`test_the_registries_together_carry_the_entry_count_the_report_folds` holds a floor under both,
and a floor is the direction that can falsify the claim: entries only ever arrive, and "one test
per entry does not scale" stops being true if the registries shrink back towards `007`'s three.
The figures above are re-measured whenever a registry grows -- `V2-P3-017` added a twelfth
financial-statement limitation and moved them from 187 / 64, `V2-P3-016` added a whole
twentieth registry (`KNOWN_INDEX_PRICE_LIMITATIONS`, four entries) and moved them from 189 / 65,
`V2-P4-004` added a twenty-first (`KNOWN_CROSS_SECTION_LIMITATIONS`, seven entries) and moved
them from 197 / 69, and `V2-P4-005` added a twenty-second (`KNOWN_RANKING_LIMITATIONS`, seven
entries) and moved them from 204 / 69 -- and they had already drifted twice
before the floor existed: `128 entries -- 59 in KNOWN_PANEL_LIMITATIONS` was still written here
when the counts were 134 and 62, because the P2 merge that folded in an eleventh registry updated
the arithmetic below and left the prose alone. That is the drift this module exists to stop,
arriving in this module, which is why the number is now executable rather than quoted.

## The binding that is installed instead

**Every declared `code` must appear as a string literal in executable test code.** Executable
is the load-bearing word and it is checked structurally: the literal must sit somewhere other
than a docstring, in a file matching `test_*.py`. That is exactly the line the P2 finding fell
on -- the renamed code *was* in the repository twice, in prose -- and it is a line an AST can
draw and a reviewer cannot forget to.

It is weaker than `007`'s and it is weaker in a way worth naming: it does not require the
reference to be a proof of the limitation, only that the registry and the suite agree on what
the limitation is *called*. What it does buy is the whole registry rather than three entries of
it. Every rename, every deletion and every addition now fails something, in every one of the
registries, without anybody remembering to install anything -- including in a registry that
does not exist yet, because `test_the_registry_table_is_every_known_registry_in_the_source_tree`
reads the source tree rather than this list. `007`'s stronger binding stays where it is; this
is the floor under all of them, not a replacement for it.

In practice the reference each registry now carries is a set literal of all of its codes,
compared for **equality** -- the form `KNOWN_ADJUSTMENT_LIMITATIONS` has had since `V2-P1-005`.
Equality rather than membership because a membership assertion is additive: it can see a code
that was renamed and never a code that was removed.

One phrase in this module's own docstring is a sentinel that
`test_prose_does_not_satisfy_the_binding_and_this_is_how_that_is_known` looks for --
`only_named_in_prose_and_therefore_not_bound` -- and the audit is required *not* to count it.
It is the extractor's own test: if the docstring filter ever broke, every prose mention in the
repository would start satisfying the rule and this module would go green while proving
nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final, Protocol

from openalpha_cn.backtest.candidate_ranking import KNOWN_RANKING_LIMITATIONS
from openalpha_cn.backtest.cross_section import KNOWN_CROSS_SECTION_LIMITATIONS
from openalpha_cn.backtest.execution import KNOWN_EXECUTION_LIMITATIONS
from openalpha_cn.backtest.factor_experiment import KNOWN_EXPERIMENT_LIMITATIONS
from openalpha_cn.backtest.factor_ic import KNOWN_IC_LIMITATIONS
from openalpha_cn.backtest.factor_portfolio import KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS
from openalpha_cn.backtest.factor_redundancy import KNOWN_REDUNDANCY_LIMITATIONS
from openalpha_cn.backtest.factor_tradeability import KNOWN_TRADEABILITY_LIMITATIONS
from openalpha_cn.domain.adjustment import KNOWN_ADJUSTMENT_LIMITATIONS
from openalpha_cn.domain.daily_prices import KNOWN_PRICE_LIMITATIONS
from openalpha_cn.domain.factor import KNOWN_FACTOR_SEAL_LIMITATIONS
from openalpha_cn.domain.factor_neutralization import KNOWN_NEUTRALIZATION_LIMITATIONS
from openalpha_cn.domain.financial_statements import KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
from openalpha_cn.domain.index_membership import KNOWN_INDEX_MEMBERSHIP_LIMITATIONS
from openalpha_cn.domain.index_prices import KNOWN_INDEX_PRICE_LIMITATIONS
from openalpha_cn.domain.industry_classification import KNOWN_INDUSTRY_LIMITATIONS
from openalpha_cn.domain.labels import KNOWN_LABEL_LIMITATIONS
from openalpha_cn.domain.price_limits import KNOWN_SUSPENSION_LIMITATIONS
from openalpha_cn.domain.stock_universe import KNOWN_UNIVERSE_LIMITATIONS
from openalpha_cn.factor_view import KNOWN_FACTOR_RUN_LIMITATIONS
from openalpha_cn.panel.catalog import KNOWN_STORAGE_LIMITATIONS
from openalpha_cn.panel_doctor import KNOWN_PANEL_LIMITATIONS

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SOURCE_ROOT: Final[Path] = ROOT / "src" / "openalpha_cn"
TEST_ROOT: Final[Path] = ROOT / "tests"

PROSE_SENTINEL: Final[str] = "only_named_in_prose" + "_and_therefore_not_bound"
"""A string this module names in full **only** inside docstrings. See the module docstring.

Spliced from two halves rather than written out, because writing it out would put it in an
executable position in this very file and make the sentinel satisfy the rule it exists to prove
is unsatisfiable by prose. Neither half contains the whole, so the containment check below
holds for the same reason the equality one does.
"""


class _Limitation(Protocol):
    @property
    def code(self) -> str: ...


LIMITATION_REGISTRIES: Final[dict[str, Sequence[_Limitation]]] = {
    "KNOWN_EXECUTION_LIMITATIONS": KNOWN_EXECUTION_LIMITATIONS,
    "KNOWN_IC_LIMITATIONS": KNOWN_IC_LIMITATIONS,
    "KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS": KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS,
    "KNOWN_EXPERIMENT_LIMITATIONS": KNOWN_EXPERIMENT_LIMITATIONS,
    "KNOWN_FACTOR_RUN_LIMITATIONS": KNOWN_FACTOR_RUN_LIMITATIONS,
    "KNOWN_REDUNDANCY_LIMITATIONS": KNOWN_REDUNDANCY_LIMITATIONS,
    "KNOWN_TRADEABILITY_LIMITATIONS": KNOWN_TRADEABILITY_LIMITATIONS,
    "KNOWN_CROSS_SECTION_LIMITATIONS": KNOWN_CROSS_SECTION_LIMITATIONS,
    "KNOWN_RANKING_LIMITATIONS": KNOWN_RANKING_LIMITATIONS,
    "KNOWN_ADJUSTMENT_LIMITATIONS": KNOWN_ADJUSTMENT_LIMITATIONS,
    "KNOWN_PRICE_LIMITATIONS": KNOWN_PRICE_LIMITATIONS,
    "KNOWN_FINANCIAL_STATEMENT_LIMITATIONS": KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
    "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS": KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
    "KNOWN_INDEX_PRICE_LIMITATIONS": KNOWN_INDEX_PRICE_LIMITATIONS,
    "KNOWN_INDUSTRY_LIMITATIONS": KNOWN_INDUSTRY_LIMITATIONS,
    "KNOWN_LABEL_LIMITATIONS": KNOWN_LABEL_LIMITATIONS,
    "KNOWN_NEUTRALIZATION_LIMITATIONS": KNOWN_NEUTRALIZATION_LIMITATIONS,
    "KNOWN_FACTOR_SEAL_LIMITATIONS": KNOWN_FACTOR_SEAL_LIMITATIONS,
    "KNOWN_SUSPENSION_LIMITATIONS": KNOWN_SUSPENSION_LIMITATIONS,
    "KNOWN_UNIVERSE_LIMITATIONS": KNOWN_UNIVERSE_LIMITATIONS,
    "KNOWN_STORAGE_LIMITATIONS": KNOWN_STORAGE_LIMITATIONS,
    "KNOWN_PANEL_LIMITATIONS": KNOWN_PANEL_LIMITATIONS,
}
"""The twenty-one registries whose entries are identified by a `code`, keyed by their own names.

**None of `KNOWN_NEUTRALIZATION_LIMITATIONS`, `KNOWN_IC_LIMITATIONS`,
`KNOWN_REDUNDANCY_LIMITATIONS`, `KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS`,
`KNOWN_TRADEABILITY_LIMITATIONS`, `KNOWN_CROSS_SECTION_LIMITATIONS`,
`KNOWN_RANKING_LIMITATIONS`, `KNOWN_EXPERIMENT_LIMITATIONS` or
`KNOWN_FACTOR_RUN_LIMITATIONS` is folded into `KNOWN_PANEL_LIMITATIONS`**, unlike the eight
dataset registries below them. `panel_doctor` folds a registry when it bounds a *fetched* dataset,
and all nine of these bound derived planes -- no upstream, no `DATASET_CADENCE` entry and nothing
for a health report to be fresh against. Folding any of them would put entries about a
regression's, a correlation's, a simulated round trip's, a participation cap's, a shortlist's, a
candidate list's, a sealed experiment report's or a public face's own semantics into a report
about ingest coverage, and would move
`test_the_registries_together_carry_the_entry_count_the_report_folds`' arithmetic
for registries the report cannot say anything about.

`KNOWN_FACTOR_RUN_LIMITATIONS` (`V2-P3-015`) is the eighteenth and the first to live on a *face*
rather than on a contract. It is here for the same reason as the rest: its five entries are what
`openalpha factor run`, `POST /api/v1/factors/run` and `OpenAlphaSDK.run_factor_experiment` do not
answer, and a code named only in prose is a code the suite has no opinion about.

`KNOWN_CROSS_SECTION_LIMITATIONS` (`V2-P4-004`) is the twenty-first and moved the totals from
197 / 69. Its seven entries bound a *selection* rather than a measurement -- what a shortlist is
not, what the hard filter is worth (0.14% of an ordinary session, measured), and what the
winsorization's clip block does to a top-N cut on each of the three tiers.

`KNOWN_RANKING_LIMITATIONS` (`V2-P4-005`) is the twenty-second and moved the totals from 204 / 69.
Its seven entries bound a *join*: a candidate ranking holds the panel plane's shortlist beside the
evidence plane's conclusions, so its entries say what it inherits without repairing (every caveat
on the funnel's order), what it cannot yet carry (a model prediction), what its 因子暴露 column is
and is not (a characteristic, never a fitted loading), and why D16's `绝不直接创建组合订单` is four
lint-imports contracts rather than a sentence.

Imported and named rather than reached through `importlib`: a test may import every one of
these directly, so a name-to-module indirection would buy nothing and would hide from the
import graph the very dependency this module is asserting about.
"""

CODELESS_REGISTRIES: Final[tuple[str, ...]] = ("KNOWN_CALENDAR_LOOKAHEAD",)
"""The one registry with no `code`, bound by its dates instead.

`KNOWN_CALENDAR_LOOKAHEAD`'s three entries are three reproductions of **one** defect rather
than three defects -- `panel_doctor._limitations()` says so by folding all three into the
single `the_published_schedule_can_be_amended_after_it_becomes_answerable` entry -- so its
natural key is `calendar_date`, and
`tests/unit/domain/test_trading_calendar.py::test_the_known_lookahead_registry_carries_its_measured_widths`
already pins all three as an exact dict. Listed here rather than left out so that "it has no
code" is a recorded judgement instead of an omission, and so
`test_the_registry_table_is_every_known_registry_in_the_source_tree` still accounts for it.
"""


def declared_codes() -> dict[str, tuple[str, ...]]:
    """Every registry's codes, keyed by the registry's own name."""
    return {
        name: tuple(item.code for item in registry)
        for name, registry in LIMITATION_REGISTRIES.items()
    }


def _docstring_constants(tree: ast.AST) -> set[int]:
    """The `id()` of every string constant that is a docstring or an attribute docstring.

    A docstring is a bare string expression **statement**, whether it opens a module, a class
    or a function, or trails a dataclass field -- which is the form every registry entry's own
    prose takes. Identified by position in a body rather than by content, so a sentence that
    happens to equal a code is still prose.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for statement in body:
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                found.add(id(statement.value))
    return found


def _test_modules() -> Iterator[Path]:
    return (path for path in sorted(TEST_ROOT.rglob("test_*.py")))


def executable_string_literals() -> set[str]:
    """Every string literal the test suite *evaluates*, docstrings excluded.

    Restricted to `test_*.py`: a mention inside `tests/panel_fixtures.py`'s `measurement=`
    prose is an evaluated literal too, and it is still prose -- it describes a fixture rather
    than asserting anything about the registry.
    """
    literals: set[str] = set()
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_constants(tree)
        literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        )
    return literals


def docstring_string_literals() -> set[str]:
    """The complement of the above: every literal the suite only *documents*."""
    prose: set[str] = set()
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_constants(tree)
        prose.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) in docstrings
        )
    return prose


def _module_level_known_names(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.startswith("KNOWN_"):
                yield target.id


def test_the_registry_table_is_every_known_registry_in_the_source_tree() -> None:
    """The direction a hand-written list cannot cover: a *twenty-second* registry.

    `V2-P2-007`'s binding is installed per module, so a new `KNOWN_*` tuple arrives with no
    binding and nothing notices -- which is how eleven of them got here. The table above is
    therefore checked against the source tree rather than trusted, by AST rather than by
    import, so a registry that is declared and never referenced still counts.
    """
    found = {
        name
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for name in _module_level_known_names(path)
    }

    assert found == set(LIMITATION_REGISTRIES) | set(CODELESS_REGISTRIES)
    assert len(LIMITATION_REGISTRIES) == 22
    assert set(LIMITATION_REGISTRIES) & set(CODELESS_REGISTRIES) == set()


def test_every_declared_limitation_code_is_named_in_executable_test_code() -> None:
    """The binding itself, over all twenty-one code-carrying registries and all their entries.

    A code that appears nowhere but in prose is a code the suite has no opinion about: rename
    it and everything stays green while every citation of it silently stops resolving. That is
    not a hypothesis -- it is what the P2 review measured on
    `a_listed_only_registry_is_invisible_to_every_downstream_check`, whose only two occurrences
    were both docstrings.

    The failure names the registry and the code rather than a count, because "127 of 128" tells
    a reader nothing about which sentence stopped being checked.
    """
    literals = executable_string_literals()

    unbound = {
        registry: tuple(code for code in codes if code not in literals)
        for registry, codes in declared_codes().items()
    }

    assert unbound == {registry: () for registry in unbound}, (
        "a declared limitation whose code the test suite never evaluates is prose; give it a "
        "set-literal assertion in its own module's tests"
    )


def test_prose_does_not_satisfy_the_binding_and_this_is_how_that_is_known() -> None:
    """The extractor's own test, without which the audit above could pass vacuously.

    If `_docstring_constants` ever stopped recognising docstrings, every prose mention in the
    suite would start counting as a binding and
    `test_every_declared_limitation_code_is_named_in_executable_test_code` would go green while
    checking nothing. The sentinel is a string this module names in its docstrings and nowhere
    else, so it has to land on the prose side of the split and never on the executable one.

    Containment rather than equality on the prose side, because the sentinel is a phrase inside
    a docstring and not a docstring of its own; equality *and* containment on the executable
    side, which is the direction that matters -- the audit above tests membership, so an
    exact-match escape would be enough to fool it, and containment rules out the rest.
    """
    assert any(PROSE_SENTINEL in text for text in docstring_string_literals())
    executable = executable_string_literals()
    assert PROSE_SENTINEL not in executable
    assert not any(PROSE_SENTINEL in text for text in executable)


DERIVED_REGISTRY: Final[str] = "KNOWN_PANEL_LIMITATIONS"
"""The one registry whose codes are deliberately **not** unique.

`panel_doctor._limitations()` folds seven registries into one tuple and four of them record
`silent_truncation_at_the_response_cap`, because the same defect really does recur at four
different caps. Its identity is therefore `(code, datasets)`, which
`tests/unit/test_panel_doctor_rules.py::test_a_limitation_is_identified_by_its_code_and_the_datasets_it_speaks_for`
asserts. Uniqueness below is a claim about the sixteen registries a code is written into by hand.
"""


def test_every_registry_entry_is_uniquely_identified_within_its_own_registry() -> None:
    """A duplicated code makes a registry's set literal smaller than the registry itself, so
    one of the two entries could be deleted without changing what the literal asserts."""
    counts = {
        registry: codes
        for registry, codes in declared_codes().items()
        if registry != DERIVED_REGISTRY
    }

    assert {registry: len(set(codes)) for registry, codes in counts.items()} == {
        registry: len(codes) for registry, codes in counts.items()
    }
    assert DERIVED_REGISTRY in declared_codes()


def test_the_registries_together_carry_the_entry_count_the_report_folds() -> None:
    """A total, so this module fails on a registry that empties itself out, and the floor under
    the volume argument in this module's docstring.

    Every assertion above is per-entry and would be satisfied by a registry of length zero.
    `KNOWN_PANEL_LIMITATIONS` is a fold of two kinds of source, and they are counted apart
    because they enter `_limitations()` differently: **eight** dataset registries fold in
    one-for-one against the datasets they bound (`V2-P3-016`'s `KNOWN_INDEX_PRICE_LIMITATIONS`
    is the eighth), `KNOWN_STORAGE_LIMITATIONS` folds in with an
    empty `datasets` because a storage boundary holds for every dataset at once, and one
    calendar code is the report's own -- `KNOWN_CALENDAR_LOOKAHEAD` is three reproductions of a
    single defect and is folded to one entry rather than three."""
    codes = declared_codes()
    folded = sum(
        len(codes[registry])
        for registry in (
            "KNOWN_UNIVERSE_LIMITATIONS",
            "KNOWN_ADJUSTMENT_LIMITATIONS",
            "KNOWN_PRICE_LIMITATIONS",
            "KNOWN_SUSPENSION_LIMITATIONS",
            "KNOWN_INDEX_MEMBERSHIP_LIMITATIONS",
            "KNOWN_INDEX_PRICE_LIMITATIONS",
            "KNOWN_INDUSTRY_LIMITATIONS",
            "KNOWN_FINANCIAL_STATEMENT_LIMITATIONS",
        )
    )
    plane_wide = len(codes["KNOWN_STORAGE_LIMITATIONS"])

    assert len(codes["KNOWN_PANEL_LIMITATIONS"]) == folded + plane_wide + 1
    assert all(codes[registry] for registry in codes)
    assert sum(len(entries) for entries in codes.values()) >= 211
    assert len(codes["KNOWN_PANEL_LIMITATIONS"]) >= 69
