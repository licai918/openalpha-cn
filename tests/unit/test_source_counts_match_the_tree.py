"""A count written into a `src/` sentence must be the count the tree gives, or say what else it is.

`tests/unit/test_source_cited_tests.py` is this file's sibling for citations: a sentence that
points at a test which does not exist is a sentence pointing at nothing, and a sentence that
counts something the tree can count is a sentence that can quietly stop being true. Both fail
the same way -- silently, because prose is not executed.

**Two hand-written counts went wrong inside one batch, and nothing saw either.** A diagram drew
"五级" over a four-level table (`V2-P5-071`, caught by a reader), and `panel_factors.py` said
"six of `panel_ingest`'s thirteen loaders" while the tree held fourteen and the other door held
eight rather than seven (caught by a reviewer). The second one contradicted four other places in
this repository that say fourteen, and a full suite was green over it: no test reads an English
sentence for the number in it.

**What this file holds.** `FACTS` derives each number from the syntax tree; `CLAIMS` names the
sentence each number is written into, as a template with the number left out. The test renders
every template against the tree's own answer and requires the result to appear verbatim in its
file. A sentence that drifts fails here, and so does one whose count moved -- with the two
repairs named in the message, because they are different: a moved count needs the sentence
rewritten, and a rewritten sentence needs its claim here rewritten.

**Numbers this file cannot see are numbers written another way.** A count spelled in a language
`_spell` does not cover, or a sentence that says "a handful", is outside it by construction. The
defence against that is not more regex: it is that a count of something countable is written
through this table, and a sentence that does not want to be counted says what it is counting
instead ("as of `V2-P4-069`", "in that sample"), which is what `panel/catalog.py`'s historical
notes and the ledger's `OA-FACTOR-002` do.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SRC: Final[Path] = ROOT / "src" / "openalpha_cn"
INGEST: Final[Path] = SRC / "panel_ingest.py"
STORE: Final[Path] = SRC / "panel" / "store.py"

_WORDS: Final[tuple[str, ...]] = (
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
)
"""How a count is spelled in a sentence: `no` for none, the word up to twenty, digits above it."""


def _spell(count: int) -> str:
    return _WORDS[count] if count < len(_WORDS) else str(count)


def _module_functions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _doors_called_directly(node: ast.AST) -> set[str]:
    """Which door this function opens itself: the same rule
    `tests/unit/panel/test_whole_partition_doors_never_hold_a_revision.py` matches on, plus the
    filtered one -- `read_if_ready` or an `.assessed(...)`/scope `.read(...)` is the whole
    partition, `read_visible_at` is the row filter."""
    doors: set[str] = set()
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        receiver = call.func.value
        if call.func.attr == "read_if_ready":
            doors.add("whole")
        elif call.func.attr == "read_visible_at":
            doors.add("filtered")
        elif call.func.attr == "read" and (
            isinstance(receiver, ast.Name)
            or (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "assessed"
            )
        ):
            doors.add("whole")
    return doors


def _doors_reached(
    name: str,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: frozenset[str] = frozenset(),
) -> set[str]:
    """The doors this function opens, following the module's own helpers.

    Transitive because a loader is allowed to share a reader: `load_industry_cross_section`
    reaches the filtered door two calls away, through `_read_visible_membership_rows`, and a
    rule that only looked one call deep is what left it out of the sentence this file now pins.
    """
    if name in seen:
        return set()
    node = functions[name]
    doors = _doors_called_directly(node)
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in functions
        ):
            doors |= _doors_reached(call.func.id, functions, seen | {name})
    return doors


def _loaders_by_door() -> dict[str, tuple[str, ...]]:
    functions = _module_functions(INGEST)
    loaders = sorted(name for name in functions if name.startswith("load_"))
    by_door: dict[str, list[str]] = {"whole": [], "filtered": [], "both": [], "none": []}
    for loader in loaders:
        doors = _doors_reached(loader, functions)
        key = "both" if len(doors) > 1 else (doors.pop() if doors else "none")
        by_door[key].append(loader)
    return {door: tuple(names) for door, names in by_door.items()}


def _reaching(helper: str) -> tuple[str, ...]:
    """The loaders that reach `helper`, however many calls away."""
    functions = _module_functions(INGEST)

    def reaches(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in seen or name not in functions:
            return False
        for call in ast.walk(functions[name]):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and (call.func.id == helper or reaches(call.func.id, seen | {name}))
            ):
                return True
        return False

    return tuple(sorted(name for name in functions if name.startswith("load_") and reaches(name)))


def _call_sites(attribute: str, *, outside: Path | None = None, on_a_scope: bool = True) -> int:
    """Calls of `attribute` in `src/`, with two ways to say which ones the sentence means.

    **A count by attribute name alone is not a count of a method's callers**, and this is what
    the closure review measured (its N-4): `read_visible_at` is the name of two methods -- the
    one-line `PanelStore.read_visible_at` and the `AssessedPanelRead.read_visible_at` it forwards
    to -- so counting the name merged them, counted the forwarding method's own body as a caller
    of itself, and counted the one reader that takes the scope directly and therefore never
    touches the one-line method at all. `outside` drops the file a method is defined in;
    `on_a_scope=False` drops the calls whose receiver is a scope rather than the store.
    """
    total = 0
    for path in sorted(SRC.rglob("*.py")):
        if outside is not None and path == outside:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attribute
            ):
                continue
            receiver = node.func.value
            scoped = (isinstance(receiver, ast.Name) and receiver.id in {"assessed", "scope"}) or (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "assessed"
            )
            if scoped and not on_a_scope:
                continue
            total += 1
    return total


def _imported_loaders(module: str) -> int:
    """How many of `panel_ingest`'s and the factor planes' loaders a module imports by name.

    A module's own `load_*` function is not one of them: `feature_matrix` defines
    `load_feature_cross_section` and calling it is not calling a loader, which is why this reads
    the imports rather than the calls.
    """
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    return len(
        {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name.startswith("load_")
        }
    )


def _name_call_sites(function: str) -> int:
    """Calls of a free function by name, anywhere in `src/`.

    `_call_sites` counts a method, which is called on a receiver and can share its name with a
    second method; a free function is called on nothing and cannot, so the two are different
    counts and asking for one with the other is how N-4 got its number wrong.
    """
    total = 0
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == function
            ):
                total += 1
    return total


def _direct_callers(helper: str) -> tuple[str, ...]:
    """The module-level functions of `panel_ingest` that call `helper` by name themselves."""
    functions = _module_functions(INGEST)
    return tuple(
        sorted(
            name
            for name, node in functions.items()
            if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == helper
                for call in ast.walk(node)
            )
        )
    )


def _shared_filtered_readers() -> tuple[str, ...]:
    """`panel_ingest`'s own readers that open the filtered door, which its loaders share."""
    functions = _module_functions(INGEST)
    return tuple(
        sorted(
            name
            for name, node in functions.items()
            if name.startswith("_read_visible") and "filtered" in _doors_called_directly(node)
        )
    )


def _gated_doors() -> int:
    """The row-returning methods of `AssessedPanelRead`: the doors a verdict opens onto rows."""
    tree = ast.parse(STORE.read_text(encoding="utf-8"), filename=str(STORE))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AssessedPanelRead":
            return sum(
                1
                for member in node.body
                if isinstance(member, ast.FunctionDef) and member.name.startswith("read")
            )
    raise AssertionError("AssessedPanelRead is not defined in panel/store.py")


def _query_callers_in_src() -> int:
    """`PanelStore.query`'s callers: a `.query(...)` on a store rather than on an evidence store."""
    total = 0
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "query"
            ):
                continue
            receiver = node.func.value
            on_a_store = (isinstance(receiver, ast.Name) and receiver.id == "store") or (
                isinstance(receiver, ast.Attribute) and receiver.attr == "store"
            )
            if on_a_store:
                total += 1
    return total


def _row_filterable_codes() -> int:
    """How many readiness codes a row predicate may stand in for, read off the frozenset."""
    tree = ast.parse((SRC / "panel" / "catalog.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            named = any(
                isinstance(target, ast.Name) and target.id == "ROW_FILTERABLE_ISSUE_CODES"
                for target in targets
            )
            if named and node.value is not None:
                return sum(
                    1
                    for member in ast.walk(node.value)
                    if isinstance(member, ast.Constant) and isinstance(member.value, str)
                )
    raise AssertionError("ROW_FILTERABLE_ISSUE_CODES is not assigned in panel/catalog.py")


FACTS: Final[dict[str, Callable[[], int]]] = {
    "loaders": lambda: sum(len(names) for names in _loaders_by_door().values()),
    "row_filterable_codes": _row_filterable_codes,
    "gated_doors": _gated_doors,
    "query_callers": _query_callers_in_src,
    "stable_model_id_call_sites": lambda: _name_call_sites("stable_model_id"),
    "feature_matrix_loaders": lambda: _imported_loaders("feature_matrix.py"),
    "event_dated_callers": lambda: len(_direct_callers("_read_visible_event_dated_rows")),
    "whole_partition_loaders": lambda: len(_loaders_by_door()["whole"]),
    "filtered_loaders": lambda: len(_loaders_by_door()["filtered"]),
    "event_dated_loaders": lambda: len(_reaching("_read_visible_event_dated_rows")),
    "price_session_loaders": lambda: len(_direct_callers("_read_visible_price_session")),
    "shared_filtered_readers": lambda: len(_shared_filtered_readers()),
    "read_if_ready_call_sites": lambda: _call_sites("read_if_ready"),
    "one_line_door_callers": lambda: (
        _call_sites("read_if_ready", outside=STORE, on_a_scope=False)
        + _call_sites("read_visible_at", outside=STORE, on_a_scope=False)
    ),
}
"""Each number a sentence in `src/` states, derived from the tree rather than from a memory."""


@dataclass(frozen=True, slots=True)
class CountedClaim:
    """One sentence, with the numbers it states left as `{fact}` placeholders."""

    path: Path
    template: str


CLAIMS: Final[tuple[CountedClaim, ...]] = (
    CountedClaim(
        SRC / "panel_factors.py",
        "the whole-partition door is taken by {whole_partition_loaders} of `panel_ingest`'s "
        "{loaders} loaders today",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "The other {filtered_loaders} take a filtered one",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "`V2-P4-026` moved the {price_session_loaders} session-dated price loaders",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "the {whole_partition_loaders} loaders above and the {shared_filtered_readers} shared "
        "readers the other {filtered_loaders} reach rows through",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "for that {row_filterable_codes} code",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "for why exactly {row_filterable_codes} code is compensable",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "It is taken by {event_dated_loaders} loaders",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "it is one function rather than {event_dated_loaders} because",
    ),
    CountedClaim(
        SRC / "panel" / "store.py",
        "so their {one_line_door_callers} callers see no change at all",
    ),
    CountedClaim(
        SRC / "panel" / "store.py",
        "`read_if_ready` itself has {read_if_ready_call_sites} call site in `src/`",
    ),
    CountedClaim(
        SRC / "panel" / "store.py",
        "`read` and `read_visible_at` are the {gated_doors} doors",
    ),
    CountedClaim(
        SRC / "panel" / "store.py",
        "every supported read goes through one of the {gated_doors} gated doors",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "this is one of the {query_callers} callers in `src/` allowed to take it",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "The reconciliation the {event_dated_loaders} callers on that door rely on",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "Since `V2-P4-061` the {price_session_loaders} price loaders share this one door",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "so the {price_session_loaders} price loaders now take one door",
    ),
    CountedClaim(
        SRC / "feature_matrix.py",
        "`read_if_ready`, which has {read_if_ready_call_sites} call site of its own in `src/`",
    ),
    CountedClaim(
        SRC / "feature_matrix.py",
        "taken by {whole_partition_loaders} of `panel_ingest`'s {loaders} loaders; the other "
        "{filtered_loaders} take the row-filtered door instead",
    ),
    CountedClaim(
        SRC / "backtest" / "candidate_ranking.py",
        "that function has {stable_model_id_call_sites} call sites and no competitor",
    ),
    CountedClaim(
        SRC / "feature_matrix.py",
        "raises through the {feature_matrix_loaders} loaders this module calls",
    ),
    CountedClaim(
        SRC / "panel_factors.py",
        "the {shared_filtered_readers} filtered readers on the other",
    ),
    CountedClaim(
        SRC / "panel_ingest.py",
        "because the {event_dated_callers} callers' rules genuinely differ",
    ),
)
"""Every counted sentence this file holds, **including a second wording of a number already here**.

One number per entry was the first shape of this table and the closure review measured what it
cost (its N-6): a loader added to the tree, with only the pinned sentences corrected, left
`panel_factors.py` saying "The other nine take a filtered one" and, four lines down, "the two
shared readers the other eight reach rows through" -- self-contradictory inside one paragraph,
with the suite green.

**What "every wording" means, stated rather than assumed**, because this docstring said "every
wording is registered now" and the next review found three that were not. Registered is every
wording inside a paragraph that already holds a claim
(`test_no_unregistered_count_stands_in_a_pinned_paragraph`) and every wording anywhere in a
pinned file whose paragraph is about a door or about what goes through one
(`test_no_count_of_a_door_or_its_readers_stands_outside_the_table`). Outside both: a count in a
file that holds no claim at all, and a count written about something none of `COUNTED_NOUN`'s
nouns names. Neither is reachable from here, and the family sweep over the tracked tree is what
covers them.
"""

NUMBERS_MEANING_SOMETHING_ELSE: Final[tuple[tuple[Path, str, str], ...]] = (
    (
        SRC / "panel_ingest.py",
        "it is one function rather than",
        "the shared reader itself, not a count of anything the tree holds",
    ),
    (
        SRC / "panel" / "store.py",
        "are now one line each on top of it",
        "what each of the two methods is, not how many there are",
    ),
    (
        SRC / "panel" / "store.py",
        "still one assessment plus one read",
        "the cost of a call, which `test_readiness_assessment_cost.py` measures",
    ),
    (
        SRC / "panel" / "store.py",
        "Two gated doors stand in front of it",
        "the two methods this paragraph then names, each pinned by a claim of its own",
    ),
    (
        SRC / "panel" / "store.py",
        "takes one of them",
        "one of the two named above, not a count",
    ),
    (
        SRC / "panel" / "store.py",
        "The one exception is allowlisted",
        "the exception this sentence then names",
    ),
    (
        SRC / "panel" / "store.py",
        "and is a one-line forward to the scope",
        "the shape of the method, not a count",
    ),
    (
        SRC / "panel" / "store.py",
        "a module that is not this one calls",
        "this module, not a count",
    ),
    (
        SRC / "panel_ingest.py",
        "what two doors onto one question cost the last time there were two",
        "the two readers that existed before `V2-P4-076` merged them, a count of what was",
    ),
    (
        SRC / "panel" / "store.py",
        "assessing years one at a time",
        "how an assessment would be split, not how many there are",
    ),
    (
        SRC / "panel" / "store.py",
        "it is the one place the change is visible at all",
        "the reader this sentence names, not a count",
    ),
    (
        SRC / "panel" / "store.py",
        "the one reader that needs it",
        "the reader this sentence then names, not a count",
    ),
    (
        SRC / "panel" / "store.py",
        "152 rows, of which 92 were not knowable at 2024-07-01",
        "a measurement of one real partition, not a count of anything in the tree",
    ),
    (
        SRC / "panel_factors.py",
        "a different question from the one its own reader asks",
        "a turn of phrase about one question, not a count",
    ),
    (
        SRC / "panel_factors.py",
        "Roadmap section 11",
        "a section number",
    ),
    (
        SRC / "panel_ingest.py",
        "price loaders share this one door",
        "the door this sentence is about, not a count",
    ),
    (
        SRC / "panel_ingest.py",
        "price loaders now take one door",
        "the door this sentence is about, not a count",
    ),
    (
        SRC / "panel_ingest.py",
        "named the two callers a wider diff would have to re-argue",
        "the two modules the sentence then names, not a count of a door's callers",
    ),
    (
        SRC / "feature_matrix.py",
        "shared with fourteen callers",
        "the retracted wording, quoted so the correction beside it can be read",
    ),
    (
        SRC / "panel" / "store.py",
        "true of every one of those readers",
        "the readers the sentence then names, not a count of them",
    ),
    (
        SRC / "panel_ingest.py",
        "beside its one caller",
        "the caller this sentence then names, not a count",
    ),
    (
        SRC / "panel_ingest.py",
        "a function of two arguments the caller supplied",
        "a function's arity, not a count of callers",
    ),
    (
        SRC / "panel_ingest.py",
        "not one any caller has asked for",
        "no caller at all, written as English rather than as a count",
    ),
    (
        SRC / "feature_matrix.py",
        "the second half is false for the eight above",
        "a back-reference to the count in the sentence above, not a second count",
    ),
)
"""A number inside a pinned paragraph that counts nothing the tree can count, and why.

The escape hatch has to exist -- "one line each", "one of them" -- and it has to be written down
rather than inferred, because the check below cannot tell a count from a turn of phrase and
should not try.
"""


def _spelled() -> dict[str, str]:
    return {name: _spell(fact()) for name, fact in FACTS.items()}


def _flat(text: str) -> str:
    """One space for every run of whitespace: a sentence is read across the line it wraps on.

    Prose in this repository wraps at 100 columns, so a counted phrase is as likely to be broken
    by a newline as not, and a check that could only see unwrapped sentences would be a check
    the next reflow turns off.
    """
    return " ".join(text.split())


def _drifted(claims: tuple[CountedClaim, ...], spelled: dict[str, str]) -> list[str]:
    return [
        f"{claim.path.relative_to(ROOT)} no longer says: {rendered}"
        for claim in claims
        if _flat(rendered := claim.template.format(**spelled))
        not in _flat(claim.path.read_text(encoding="utf-8"))
    ]


def _paragraphs(path: Path) -> tuple[str, ...]:
    """The file's blank-line-separated blocks, which is the scope a sentence is read in."""
    return tuple(block for block in path.read_text(encoding="utf-8").split("\n\n") if block.strip())


def _pinned_paragraphs(
    spelled: dict[str, str],
) -> dict[Path, tuple[tuple[str, tuple[str, ...]], ...]]:
    """Every paragraph holding a rendered claim, with the claims it holds."""
    held: dict[Path, list[tuple[str, tuple[str, ...]]]] = {}
    rendered_by_path: dict[Path, list[str]] = {}
    for claim in CLAIMS:
        rendered_by_path.setdefault(claim.path, []).append(claim.template.format(**spelled))
    for path, rendered in rendered_by_path.items():
        for paragraph in _paragraphs(path):
            inside = tuple(one for one in rendered if _flat(one) in _flat(paragraph))
            if inside:
                held.setdefault(path, []).append((paragraph, inside))
    return {path: tuple(blocks) for path, blocks in held.items()}


def _spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = haystack.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = haystack.find(needle, start + 1)
    return spans


NUMBER_TOKEN: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w\-/`.,])(?:{'|'.join(_WORDS[1:])}|\d{{1,3}})(?![\w\-/`,])", re.IGNORECASE
)
"""A number as prose writes it: any case, any punctuation around it, spelled or in digits.

The first shape of this check looked for `f" {word} "` -- lower case, a space on each side -- and
the closure review measured seven escapes from that in one sitting (`Nine`, `nine.`, `nine,`,
`9`, `nine-loader`, `` `nine` ``, and 九). Capitalising a word at the start of a sentence, or
putting a full stop after it, is not "another way of writing a number"; it is the ordinary way.
Chinese numerals stay outside, and deliberately: the files this check reads are English
docstrings, and 一/二/三 in them would be quoting the documents, not counting the tree.

A comma is a boundary on neither side, because the numbers these files write with one are
thousands: `panel_factors.py` reports `providers/tushare.py`'s 3,867 lines beside a pinned count
of three, and reading the leading digit of that as a number is how the widened scope first went
red on a line count.
"""

COUNTED_NOUN: Final[str] = r"(?:loaders?|callers?|call sites?|readers?|doors?)"
"""What the facts in this file count: a number in front of one of these counts the tree."""

DOOR_WORDS: Final[re.Pattern[str]] = re.compile(
    r"read_if_ready|read_visible_at|assessed|panel_ingest|door|gate|loaders?\b|_read_visible"
    r"|availability_rule",
    re.IGNORECASE,
)
"""The subject that makes a count this file's business rather than any count in a long module.

`loaders?`, `_read_visible` and `availability_rule` joined the doors when the scope below widened
from a sentence to a paragraph, and each earned its place on a real miss: `feature_matrix.py`
counts "the five loaders this module calls" while naming no door, and
`_refuse_a_slice_the_census_disagrees_with` counted `availability_rule`'s callers in a paragraph
that names neither. Adding `partition` or `store` as well was measured and rejected: it pulls in
`panel/store.py`'s concurrency notes ("two concurrent callers", "three reader processes"), which
count processes rather than anything in this file's tree.
"""

NUMBER_BEFORE_A_COUNTED_NOUN: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w-])(?:{'|'.join(_WORDS[1:])}|\d{{1,3}})[-\s]+(?:\w+[-\s]+){{0,2}}{COUNTED_NOUN}\b",
    re.IGNORECASE,
)
"""`five callers`, `three price loaders`, `two shared readers` -- a count with its noun attached."""


def _sentences(text: str) -> list[str]:
    """The file as sentences, so a count is read with the subject it was written about."""
    return [part for part in re.split(r"(?<=[.!?])\s+|\n\s*\n", _flat(text)) if part.strip()]


def _uncounted_door_counts(spelled: dict[str, str]) -> list[str]:
    """Counts of loaders, callers, call sites, readers or doors that no claim and no excuse holds.

    **File-wide, not paragraph-wide**, which is the hole the closure review drove a loader
    through (its P-7): with the count pinned in one paragraph, a second wording 2,183 lines away
    in the same file went stale and the suite stayed green. The scope that makes this tractable
    is the subject rather than the distance -- a count in a passage that mentions a door,
    `panel_ingest`, one of the read methods, a loader or an availability rule, in a file that
    already holds a claim -- because `panel_ingest.py` alone says "one partition" or "one caller"
    ninety times about everything else.

    **The passage is a paragraph rather than a sentence**, because a count and its subject are
    routinely two sentences apart. `_refuse_a_slice_the_census_disagrees_with` counted
    `availability_rule`'s callers at four when the tree held five, in a sentence naming no door
    at all, and sentence scope could not see it; paragraph scope over the wider subject above
    turns red on it, and on two more wordings, at a cost of four turns of phrase that had to be
    declared.
    """
    uncovered: list[str] = []
    for path in sorted({claim.path for claim in CLAIMS}):
        rendered = [
            _flat(claim.template.format(**spelled)) for claim in CLAIMS if claim.path == path
        ]
        excuses = [
            phrase for excused, phrase, _ in NUMBERS_MEANING_SOMETHING_ELSE if excused == path
        ]
        for paragraph in _paragraphs(path):
            passage = _flat(paragraph)
            if not DOOR_WORDS.search(passage):
                continue
            for match in NUMBER_BEFORE_A_COUNTED_NOUN.finditer(passage):
                covered = [
                    span
                    for fragment in rendered + excuses
                    for span in _spans(passage, fragment)
                    if span[0] <= match.start() < span[1]
                ]
                if not covered:
                    uncovered.append(
                        f"{path.relative_to(ROOT)}: '{match.group(0)}' in "
                        f"…{passage[max(0, match.start() - 80) : match.start() + 120]}…"
                    )
    return uncovered


def _loose_numbers(spelled: dict[str, str]) -> list[str]:
    """Numbers inside a pinned paragraph that no claim and no excuse covers.

    Two shapes are read as counts, in any spelling `NUMBER_TOKEN` accepts: a number standing in
    front of one of `COUNTED_NOUN`'s nouns, and a number equal to one of the facts this file
    derives. The first is a count of something with its noun attached; the second is a number
    that says what a pinned sentence says, four lines from it, without saying what it counts.

    **What that leaves outside**, stated rather than discovered later. A bare number whose noun
    is elided *and* which is not one of the facts -- "they are nine" beside a paragraph whose
    fact is eight. And a bare `one` or `two`, because a fact valued one or two makes that rule
    useless: `one` and `two` are ordinary English in every other sentence of these files ("one
    security", "two twins"), so a claim about one or two things has to name the noun, which
    `test_no_count_of_a_door_or_its_readers_stands_outside_the_table` then reads file-wide.
    """
    facts = {
        form
        for name, fact in FACTS.items()
        if (value := fact()) >= 3
        for form in (spelled[name].lower(), str(value))
    }
    loose: list[str] = []
    for path, blocks in _pinned_paragraphs(spelled).items():
        excuses = tuple(
            phrase for excused, phrase, _ in NUMBERS_MEANING_SOMETHING_ELSE if excused == path
        )
        for paragraph, claims in blocks:
            flat = _flat(paragraph)
            covered = [
                span
                for fragment in [*(_flat(claim) for claim in claims), *excuses]
                for span in _spans(flat, fragment)
            ]
            counted = {match.start() for match in NUMBER_BEFORE_A_COUNTED_NOUN.finditer(flat)}
            for match in NUMBER_TOKEN.finditer(flat):
                start, end = match.span()
                if start not in counted and match.group(0).lower() not in facts:
                    continue
                if any(begin <= start < finish for begin, finish in covered):
                    continue
                loose.append(
                    f"{path.relative_to(ROOT)}: '{match.group(0)}' in "
                    f"…{flat[max(0, start - 60) : end + 60]}…"
                )
    return loose


def test_every_counted_sentence_states_the_count_the_tree_gives() -> None:
    """The audit. A sentence that counts something must state the tree's own answer."""
    drifted = _drifted(CLAIMS, _spelled())

    assert drifted == [], (
        "\n".join(drifted) + "\nEither the count moved and the sentence has to be rewritten, or "
        "the sentence was rewritten and its entry in CLAIMS has to follow it."
    )


def test_no_unregistered_count_stands_in_a_pinned_paragraph() -> None:
    """A second wording of a number, beside a pinned one, must be pinned too or declared.

    The coverage half of this file, and the one the closure review's own mutation walked through:
    correcting only the pinned sentence leaves the paragraph contradicting itself. Every spelled
    number inside a paragraph that holds a claim has to be either inside one of that paragraph's
    claims or in `NUMBERS_MEANING_SOMETHING_ELSE`, which says what it counts instead.
    """
    loose = _loose_numbers(_spelled())

    assert loose == [], (
        "\n".join(loose) + "\nA number beside a pinned one is either a count -- register the "
        "wording in CLAIMS -- or a turn of phrase, which NUMBERS_MEANING_SOMETHING_ELSE says so."
    )


def test_no_count_of_a_door_or_its_readers_stands_outside_the_table() -> None:
    """The same rule at file scope, for the nouns this file's facts are about.

    One paragraph is too small a scope: the review's own mutation put the stale wording 2,183
    lines below the pinned one, in the same file and the same docstring family, and the suite
    stayed green. Anywhere in a file that holds a claim, a number in front of `loaders`,
    `callers`, `call sites`, `readers` or `doors`, in a paragraph that is about a door or about
    what goes through one, has to be a registered claim or a declared turn of phrase.
    """
    uncovered = _uncounted_door_counts(_spelled())

    assert uncovered == [], (
        "\n".join(uncovered) + "\nEach of these counts something the tree can count: register "
        "the wording in CLAIMS, or say in NUMBERS_MEANING_SOMETHING_ELSE what it counts instead."
    )


def test_a_stale_count_in_a_source_sentence_is_reported() -> None:
    """Each claim, rendered one higher than the tree, must be reported -- otherwise a green run
    here would mean only that the templates match some sentence, not the right one."""
    one_higher = {name: _spell(fact() + 1) for name, fact in FACTS.items()}
    unreported = [
        claim.template for claim in CLAIMS if not _drifted((claim,), {**_spelled(), **one_higher})
    ]

    assert unreported == [], f"a stale count would not be reported: {unreported}"


def test_every_loader_takes_exactly_one_door() -> None:
    """The fact the counted sentences rest on: the two doors partition `panel_ingest`'s loaders.

    A loader on both doors, or on neither, makes "six take this one and eight take that one" a
    sentence about nothing -- and it is the shape a refactor produces first, by leaving a second
    read behind.
    """
    by_door = _loaders_by_door()

    assert by_door["both"] == (), f"loaders on two doors: {by_door['both']}"
    assert by_door["none"] == (), f"loaders reaching no door: {by_door['none']}"
