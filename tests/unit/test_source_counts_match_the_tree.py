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
)
"""Every counted sentence this file holds, **including a second wording of a number already here**.

One number per entry was the first shape of this table and the closure review measured what it
cost (its N-6): a loader added to the tree, with only the pinned sentences corrected, left
`panel_factors.py` saying "The other nine take a filtered one" and, four lines down, "the two
shared readers the other eight reach rows through" -- self-contradictory inside one paragraph,
with the suite green. Every wording is registered now, and
`test_no_unregistered_count_stands_in_a_pinned_paragraph` is what keeps the next one from
being written outside this table.
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


def _loose_numbers(spelled: dict[str, str]) -> list[str]:
    """Every spelled number inside a pinned paragraph that no claim and no excuse covers."""
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
            for word in _WORDS[1:]:
                for start, end in _spans(flat, f" {word} "):
                    position = start + 1
                    if any(begin <= position < finish for begin, finish in covered):
                        continue
                    loose.append(
                        f"{path.relative_to(ROOT)}: '{word}' in "
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
