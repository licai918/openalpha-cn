"""User-facing attribution claims must not overstate what `OutcomeValidator` produces.

Two couplings, both anchored on `KNOWN_ATTRIBUTION_LIMITATIONS` in
`src/openalpha_cn/backtest/validation.py` rather than on a hand-written category list:

1. **The PRD.** `docs/specs/v2/openalpha-cn-v2-prd.md` must have exactly one `S65` row ("Rule,
   factor, model and Agent attribution reconciled to final result"), and its status may not be
   `IN` -- "v2 范围内，不打折" in the PRD's legend -- while any category is recorded as never
   produced, whatever the row's story text says. Every other user-story row is read
   generically: one whose story names attribution and an absent category may not be `IN`
   either. A story ID may appear on one row only, so a second row cannot shadow the first.

2. **User-facing prose.** `README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md` and
   `docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md` may not present an absent
   category as attribution the product delivers. `README.md` links `why-openalpha` from its
   header and from its body; `README.en.md` does not link it. `d4af27c` fixed this overclaim
   in both READMEs and in the marketing pack without a test; `9eb8368` fixed it in
   `why-openalpha` and added this module; `D7` added the marketing pack to `GUARDED_FILES`.
   There the guard flagged one clause, "不把市场上涨全部算成 Agent 功劳", which implied the
   rest of the move was the Agent's credit; it was reworded, not allowlisted.

   Some claims are invisible to this guard by design, because they carry neither 归因 nor
   "attribution". The marketing pack had two, found by reading and reworded in `D7`:
   "差异来自哪个规则或 Agent" and a list that "将结果拆成...规则、因子与标的暴露". Prose of that
   shape needs a manual review; this guard will never report it.

**How the guard in (2) reads a document.**

- *Blocks and clauses* come from `tests/prose_clauses.py`, the reader
  `tests/integration/test_usage_claims_match_the_shipped_paths.py` shares. Its docstring states
  how a block's soft-wrapped lines are folded back and where a clause ends. In short: a
  paragraph, a list item (with the items nested under it) and a blockquote are one block each;
  a table row, a heading and each line of a fenced code block are blocks of their own; a block
  is split into clauses after `。`, a full-width or ASCII semicolon, a full-width `!` or `?`, and
  at an English sentence end. The reader tests below -- block forms, wrap widths, sentence ends,
  the line a clause reports -- are that reader's measurements.
- *Claims.* A clause is a claim when it holds an attribution marker (归因, or "attribution" in
  any case, matched as a substring, so "attributions" counts), names at least one absent
  category, and holds no absence phrase. An absence phrase exempts only the clause it sits in,
  and exempts nothing when a negation (非, 不是, "not", "never", "n't") directly precedes it.
- *Category markers* are `CATEGORY_MARKERS`, each matched by `prose_clauses.marker_pattern`:
  Chinese words as substrings, English words in any case with an optional plural `s`, bounded
  by ASCII-letter lookarounds instead of `\\b`.
  Python's `\\b` treats a CJK character as a word character, so "因子与Agent归因" never
  matched under it. The lookarounds also let "per-agent" and "agent_id" name `agent`.
- *Threshold one.* One absent category is enough. Marketing's attribution claims mostly name a
  single category (the review of `9eb8368` measured this), and a threshold of two cannot see
  one.
- *`ALLOWLIST`* holds the guarded files' true non-claims. Each is pinned to one clause by that
  clause's whole text as the shared reader produces it (soft wraps folded, runs of whitespace
  collapsed), found by a short excerpt, limited to the categories it may exempt, and carries
  the reason it is true. A clause is exempt only while its text equals the pinned text, so any
  change to its words ends the exemption and the prose test fails on it until someone re-reads
  the clause, which `test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption`
  measures. The category limit covers the other way a verdict can move: a change in the guard's
  own reading of a clause whose words did not change.
  `test_every_allowlist_entry_exempts_exactly_one_flagged_clause` fails on an entry whose
  excerpt finds no clause or several, whose clause no longer reads as pinned, or which exempts
  a category its clause no longer presents, so stale entries cannot pile up. With the prose
  test, that makes the allowlist the census: every clause the guard flags in the guarded files
  is either fixed or listed there.

**What it cannot see.** `test_the_guards_stated_blind_spots_are_real` measures each of these.

- Within a single clause, one absence phrase exempts every category named in it:
  "因子归因已交付，模型结构性从不产生而非被收窄。" passes.
- A clause without 归因 or "attribution" is never a claim. The verb "attributes" is not a
  marker, and neither is any paraphrase.
- A claim split across two blocks -- a blank line, a heading, a table row or two lines of a code
  block between its halves -- or across two clauses is read as two halves, each innocent.
- A category is named only by the words in `CATEGORY_MARKERS`: 角色 (role) does not name
  `agent`.
- `ABSENCE_PHRASES` is the fixed list of wordings this repository uses; a caveat worded any
  other way is read as a claim.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, get_args

import pytest
from diagram_text import diagram_strings, diagram_units
from prose_clauses import clauses, holds_unnegated_phrase, marker_pattern

from openalpha_cn.backtest.validation import KNOWN_ATTRIBUTION_LIMITATIONS
from openalpha_cn.domain.validation import AttributionTerm

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

PRD: Final[Path] = ROOT / "docs" / "specs" / "v2" / "openalpha-cn-v2-prd.md"
README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)
"""User-facing prose checked by part (2): both READMEs, `why-openalpha` and the marketing pack."""

CATEGORY_VOCABULARY: Final[tuple[str, ...]] = get_args(
    AttributionTerm.model_fields["category"].annotation
)
"""`AttributionTerm.category`'s own `Literal` args, read from the type rather than retyped:
`('rule', 'factor', 'agent', 'model')` today."""


def _categories_named_by(codes: Iterable[str]) -> frozenset[str]:
    """Which of `CATEGORY_VOCABULARY` the limitation `codes` name, in the singular or plural.

    A code is an underscore-joined identifier, so it is read as its `_`-separated tokens rather
    than as a substring. Codes, not the `detail` prose: a `detail` narrates history and uses
    category words in sentences that are no claim about today -- the first limitation recounts a
    deleted split "across a rule, a factor and the agents", and a rule term is produced today
    (`TRANSACTION_COST_TERM` and `FORGONE_BENCHMARK_TERM` are both `category="rule"`).
    """
    named: set[str] = set()
    for code in codes:
        tokens = set(code.split("_"))
        named |= {c for c in CATEGORY_VOCABULARY if c in tokens or f"{c}s" in tokens}
    return frozenset(named)


ABSENT_CATEGORIES: Final[frozenset[str]] = _categories_named_by(
    limitation.code for limitation in KNOWN_ATTRIBUTION_LIMITATIONS
)
"""The categories `KNOWN_ATTRIBUTION_LIMITATIONS` records as never produced.

Today `an_agent_contribution_would_need_a_counterfactual_a_finished_run_cannot_supply`
contributes `agent`, `neither_a_factor_nor_a_model_term_is_ever_produced_here` contributes
`factor` and `model`, and the other two codes name no category. "rule" is not a member, matching
that rule is the one category produced.
"""


# --- Part 1: the PRD may not mark attribution `IN` that the code cannot produce --------------

STORY_ROW_ID: Final[re.Pattern[str]] = re.compile(r"^\|\s*(S\d+)\s*\|")


@dataclass(frozen=True, slots=True)
class StoryRow:
    """One `| S<n> | Story | 状态 | 说明 |` row of the PRD, with its 1-based line number."""

    line: int
    story_id: str
    story: str
    status: str
    note: str


def _prd_story_rows(text: str) -> list[StoryRow]:
    """Every user-story row in the PRD, in order, with duplicates kept.

    Matched on the ID column's shape (`S` + digits) rather than by locating one table, since the
    user stories span several tables of the same four-column shape. A list rather than a dict
    keyed by ID, because a dict silently kept only the last of two rows with the same ID.
    """
    rows: list[StoryRow] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not STORY_ROW_ID.match(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 4, (
            f"expected 4 columns (ID, Story, 状态, 说明) in PRD line {number} {line!r}, found "
            f"{len(cells)}: {cells}. This parser assumes that shape; if the table was reshaped, "
            "update the parser rather than let it misread cells."
        )
        story_id, story, status, note = cells
        rows.append(StoryRow(number, story_id, story, status, note))
    return rows


def _normalised_status(status: str) -> str:
    """A status cell without bold `*` or code backticks, so `**IN**` and `` `IN` `` read `IN`."""
    return status.replace("*", "").replace("`", "").strip()


def _prd_problems(text: str, absent: frozenset[str]) -> list[str]:
    """Every way the PRD's user-story rows overstate attribution, given the `absent` categories.

    `S65` is pinned by ID and its story text is not consulted: while anything is absent, it may
    not be `IN`. Every other row is `IN`-refused only when its story names attribution and an
    absent category, read with the same markers as the prose guard.
    """
    rows = _prd_story_rows(text)
    problems: list[str] = []
    repeated = sorted(
        story_id for story_id, count in Counter(row.story_id for row in rows).items() if count > 1
    )
    if repeated:
        problems.append(
            f"story IDs {repeated} appear on more than one PRD row; each ID must name one row, "
            "or a later row can shadow the one a reader checks"
        )
    s65 = [row for row in rows if row.story_id == "S65"]
    if len(s65) != 1:
        problems.append(
            f"expected exactly one S65 row in the PRD, found {len(s65)} "
            f"(lines {[row.line for row in s65]}); S65 is the attribution story this coupling "
            "pins, so if it was renumbered, move the pin with it"
        )
    for row in s65:
        if absent and _normalised_status(row.status) == "IN":
            problems.append(
                f"PRD line {row.line} marks S65 {row.status!r}, but KNOWN_ATTRIBUTION_LIMITATIONS "
                f"records {sorted(absent)} as never produced. IN promises delivery without "
                "discount; mark it IN-降级, as S54 is, until those limitations are closed."
            )
    for row in rows:
        if row.story_id == "S65" or not _names_attribution(row.story):
            continue
        named = _named_categories(row.story, absent)
        if named and _normalised_status(row.status) == "IN":
            problems.append(
                f"PRD line {row.line} marks {row.story_id} {row.status!r}, but its story names "
                f"{sorted(named)}, which KNOWN_ATTRIBUTION_LIMITATIONS records as never "
                f"produced: {row.story!r}"
            )
    return problems


def test_prd_story_status_is_not_bare_in_when_it_names_a_structurally_absent_category() -> None:
    """A PRD row may not claim `IN` ("v2 范围内，不打折") for attribution the code cannot produce.

    `S65` was marked bare `**IN**` although it names three categories that
    `KNOWN_ATTRIBUTION_LIMITATIONS` records as never produced. `S54` is the precedent for the
    corrected shape: `IN-降级`, plus a note on what is and is not delivered. The review of
    `9eb8368` found four edits that slipped past this check's first version -- the story reworded
    without "attribution", the story in Chinese, the status in backticks, a duplicate row --
    and `test_the_prd_coupling_refuses_every_way_s65_could_slip_back_to_in` holds each of them.
    """
    assert ABSENT_CATEGORIES, (
        "the derivation found no absent category. If factor, agent and model attribution are all "
        "produced now, S65 may be IN and this coupling should be retired, not left passing."
    )
    problems = _prd_problems(PRD.read_text(encoding="utf-8"), ABSENT_CATEGORIES)
    assert not problems, "\n".join(problems)


# --- Part 2: user-facing prose may not present an absent category as delivered --------------

CATEGORY_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "rule": ("rule", "规则"),
    "factor": ("factor", "因子"),
    "agent": ("agent", "智能体"),
    "model": ("model", "模型"),
}
"""Bilingual surface forms for each category in `CATEGORY_VOCABULARY`.

Bridging the code's English vocabulary to this repository's mixed zh-CN/en prose is a
hand-authored step: the *set* of categories to look for is derived from
`KNOWN_ATTRIBUTION_LIMITATIONS`, but no source lists "the Chinese word for each category", so
that mapping is written here once.
"""

ATTRIBUTION_MARKERS: Final[tuple[str, ...]] = ("归因", "attribution")
"""Matched as substrings, the English one in any case, so "attributions" counts."""

ABSENCE_PHRASES: Final[tuple[str, ...]] = (
    "结构性从不产生",
    "结构性不产生",
    "结构性从不产出",
    "结构性不产出",
    "structurally never produced",
    "structurally absent",
)
"""Phrasing this repository uses to state that a category is absent, not narrowed.

`README.md`'s feature bullet and `why-openalpha`'s 验证改进 row say "结构性从不产生而非被收窄",
`README.md`'s validation-loop paragraph drops the 从, and `README.en.md` says "structurally never
produced, not merely narrowed". A fixed list measured against that usage, not a general
negation detector -- see the module docstring for both directions of error.
"""

CATEGORY_PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    category: tuple(marker_pattern(marker) for marker in markers)
    for category, markers in CATEGORY_MARKERS.items()
}


def _names_attribution(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in ATTRIBUTION_MARKERS)


def _named_categories(text: str, categories: Iterable[str]) -> frozenset[str]:
    return frozenset(
        category
        for category in categories
        if any(pattern.search(text) for pattern in CATEGORY_PATTERNS[category])
    )


def _states_absence(clause: str) -> bool:
    """Whether `clause` holds one of `ABSENCE_PHRASES` with no negation directly before it."""
    return holds_unnegated_phrase(clause, ABSENCE_PHRASES)


def _overclaimed_categories(clause: str, absent: frozenset[str]) -> frozenset[str]:
    """The absent categories one clause presents as delivered attribution, or empty.

    Empty when the clause holds no attribution marker, names none of `absent`, or states an
    absence with one of `ABSENCE_PHRASES`.
    """
    if not _names_attribution(clause):
        return frozenset()
    named = _named_categories(clause, absent)
    if not named or _states_absence(clause):
        return frozenset()
    return named


@dataclass(frozen=True, slots=True)
class FlaggedClause:
    """A clause the guard reads as a claim, with the absent categories it presents."""

    line: int
    text: str
    categories: frozenset[str]


def _flagged_clauses(document: str, absent: frozenset[str]) -> list[FlaggedClause]:
    """Every clause of `document` that presents one of `absent` as delivered attribution."""
    flagged: list[FlaggedClause] = []
    for clause in clauses(document):
        categories = _overclaimed_categories(clause.text, absent)
        if categories:
            flagged.append(FlaggedClause(clause.line, clause.text, categories))
    return flagged


@dataclass(frozen=True, slots=True, kw_only=True)
class AllowedClause:
    """A clause the guard flags that is true as written, pinned by its whole text, and why.

    `clause` is the clause exactly as `prose_clauses.clauses` reads it -- soft wraps folded,
    runs of whitespace collapsed -- so re-wrapping the paragraph keeps the pin and changing any
    word breaks it. `excerpt` only finds the clause, so a broken pin can say which one it was.
    """

    path: Path
    excerpt: str
    clause: str
    categories: frozenset[str]
    reason: str


ALLOWLIST: Final[tuple[AllowedClause, ...]] = (
    AllowedClause(
        path=README,
        excerpt="`factor run` 打三行档位和六格归因",
        clause="`factor run` 打三行档位和六格归因。",
        categories=frozenset({"factor"}),
        reason=(
            "`factor run` is a command's name, and its 六格归因 is the factor experiment's "
            "six-cell step attribution (`attribution_cells` in factor_view.py): what each of the "
            "raw->processed->neutralized steps does to a factor's statistic. That grid exists, "
            "and it is not return attribution by category."
        ),
    ),
    AllowedClause(
        path=README_EN,
        excerpt="`factor run` prints three tier rows and a six-cell attribution grid",
        clause="`factor run` prints three tier rows and a six-cell attribution grid.",
        categories=frozenset({"factor"}),
        reason="The same six-cell step grid as README.md's 六格归因, in English.",
    ),
    AllowedClause(
        path=README_EN,
        excerpt="OpenAlpha CN provides four-clock point-in-time evidence",
        clause=(
            "OpenAlpha CN provides four-clock point-in-time evidence, A-share event semantics, "
            "deterministic baseline agents, a secure OpenAI-compatible BYOK model boundary, typed "
            "signals and decisions, durable per-agent resume and research memory, A-share "
            "execution and portfolio constraints, same-path replay, reconciled attribution, "
            "durable batch research, a ChainLin contract Provider, screening, watchlists, "
            "reports, REST, Python SDK, CLI, and a responsive research workbench."
        ),
        categories=frozenset({"agent", "model"}),
        reason=(
            "One sentence listing eighteen features, which the block folding reads whole. "
            "'deterministic baseline agents', 'a secure OpenAI-compatible BYOK model boundary' "
            "and 'durable per-agent resume' are three of them and 'reconciled attribution' is "
            "another; the sentence attributes nothing to an agent or a model. Reconciled "
            "attribution is itself true: the rule terms plus unexplained_return must sum to "
            "net_active_return (ValidationResult.validate_window_and_attribution)."
        ),
    ),
)
"""The guarded files' true non-claims. An entry is for a clause that is true as written, never
for a claim waiting to be fixed.

When this list was written the guard flagged exactly these three clauses in the three files it
read then: the two `factor run` sentences, where "factor" is a command's name and the
attribution is the factor experiment's six-cell step grid, and `README.en.md`'s four-clock
feature sentence. Adding the marketing pack in `D7` flagged one more clause, which was reworded
rather than listed.
"""


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def _attribution_violations(documents: dict[Path, str]) -> list[str]:
    """One message per flagged clause of `documents` with a category no pinned entry exempts."""
    violations: list[str] = []
    for path, document in documents.items():
        entries = [entry for entry in ALLOWLIST if entry.path == path]
        for flagged in _flagged_clauses(document, ABSENT_CATEGORIES):
            exempt = frozenset().union(
                *(entry.categories for entry in entries if entry.clause == flagged.text)
            )
            remaining = flagged.categories - exempt
            if remaining:
                violations.append(
                    f"{path.relative_to(ROOT)}:{flagged.line} presents {sorted(remaining)} as "
                    f"delivered attribution: {flagged.text!r}"
                )
    return violations


def test_user_facing_docs_do_not_present_an_absent_attribution_category_as_delivered() -> None:
    """The four `GUARDED_FILES` must not present an absent category as delivered attribution.

    Every clause the guard flags in them is a violation unless an `ALLOWLIST` entry for that
    file pins exactly its text, and then only for the categories the entry names.
    `why-openalpha`'s 验证改进 row was the violation this was first written against:
    "规则/因子/Agent 归因对账", factor and Agent presented as delivered with no caveat, until
    `9eb8368`.
    """
    assert ABSENT_CATEGORIES, (
        "the derivation found no absent category, so there is nothing to guard; retire this test "
        "rather than leave it passing"
    )
    violations = _attribution_violations(_guarded_documents())
    assert not violations, (
        "\n".join(violations) + "\nState the absence in the same clause with one of "
        "ABSENCE_PHRASES, or reword the claim. Pin an ALLOWLIST entry only for a clause that is "
        "true as written."
    )


def _allowlist_problems(
    documents: dict[Path, str], allowlist: Iterable[AllowedClause] = ALLOWLIST
) -> list[str]:
    """Why each `ALLOWLIST` entry no longer describes exactly one flagged clause of `documents`.

    Each entry must belong to a guarded file and exempt at least one category; its excerpt must
    find exactly one of its file's clauses, that clause must still read exactly as pinned, and
    the entry may exempt only categories that clause is flagged for.
    """
    problems: list[str] = []
    for entry in allowlist:
        label = f"ALLOWLIST entry {entry.excerpt!r} ({entry.path.relative_to(ROOT)})"
        if entry.path not in documents:
            problems.append(f"{label} is for a file the guard does not read")
            continue
        if not entry.categories:
            problems.append(f"{label} exempts no category")
            continue
        matches = [
            clause for clause in clauses(documents[entry.path]) if entry.excerpt in clause.text
        ]
        if len(matches) != 1:
            problems.append(
                f"{label} matches {len(matches)} clauses, at lines {[c.line for c in matches]}; "
                "it must match exactly one"
            )
            continue
        found = matches[0]
        if found.text != entry.clause:
            problems.append(
                f"{label}: the clause at line {found.line} now reads {found.text!r}, not the "
                f"pinned {entry.clause!r}; re-read it, then fix it, re-pin it or remove the entry"
            )
            continue
        flagged = _overclaimed_categories(found.text, ABSENT_CATEGORIES)
        if not entry.categories <= flagged:
            problems.append(
                f"{label} exempts {sorted(entry.categories - flagged)}, which its clause at line "
                f"{found.line} no longer presents (flagged: {sorted(flagged)}); remove or narrow "
                "the entry"
            )
    return problems


def test_every_allowlist_entry_exempts_exactly_one_flagged_clause() -> None:
    """An `ALLOWLIST` entry is a statement about one clause; this holds it to that clause.

    An entry whose clause was reworded, fixed, deleted or copied fails here instead of lingering
    as a silent exemption; a reworded clause fails the prose test as well, because its new text
    is pinned by no entry.
    """
    problems = _allowlist_problems(_guarded_documents())
    assert not problems, "\n".join(problems)


def test_the_allowlist_check_reports_each_way_an_entry_can_go_stale() -> None:
    """`_allowlist_problems` held to entries whose defect is known, one per way to go stale.

    Every real entry is current, so the test above cannot show that any of these checks
    reports anything; these entries can.
    """
    clause = "`factor run` 打三行档位和六格归因。"
    entry = AllowedClause(
        path=README,
        excerpt="`factor run`",
        clause=clause,
        categories=frozenset({"factor"}),
        reason="measured",
    )
    documents = {README: f"{clause}\n"}
    stale = {
        "a file the guard does not read": (replace(entry, path=PRD), documents),
        "an entry that exempts no category": (replace(entry, categories=frozenset()), documents),
        "an excerpt that finds no clause": (replace(entry, excerpt="`agent run`"), documents),
        "an excerpt that finds two clauses": (entry, {README: f"{clause}\n\n{clause}\n"}),
        "a clause that no longer reads as pinned": (
            entry,
            {README: "`factor run` 打三行档位和六格因子归因。\n"},
        ),
        "a category its clause does not present": (
            replace(entry, categories=frozenset({"agent", "factor"})),
            documents,
        ),
    }
    assert not _allowlist_problems(documents, [entry]), "a current entry was reported"
    unreported = [
        label for label, (bad, docs) in stale.items() if not _allowlist_problems(docs, [bad])
    ]
    assert not unreported, f"the allowlist check did not report: {unreported}"


def _claim_for(entry: AllowedClause) -> str:
    """A delivered-attribution claim naming only the categories `entry` exempts, in its language.

    Only those categories, because that is the claim the excerpt rule missed: a claim naming
    another category was already reported through the category limit. It holds no clause end,
    so written after the excerpt it lands inside the pinned clause.
    """
    names = sorted(entry.categories)
    if entry.clause.isascii():
        return ", and " + " and ".join(names) + " attribution is delivered"
    return "，并交付" + "与".join(CATEGORY_MARKERS[name][1] for name in names) + "归因"


def test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption() -> None:
    """The review of `D6`'s fix wrote a claim into a pinned clause and both tests stayed green.

    For each entry, `_claim_for(entry)` is written into the real document right after the
    entry's excerpt; the prose test and the allowlist test must then both fail. While the
    exemption was granted by excerpt, both passed: the review measured it on `README.md`'s
    `factor run` line and on `README.en.md`'s four-clock sentence.
    """
    documents = _guarded_documents()
    still_exempt: list[str] = []
    for entry in ALLOWLIST:
        edited = documents[entry.path].replace(entry.excerpt, entry.excerpt + _claim_for(entry), 1)
        assert edited != documents[entry.path], f"{entry.excerpt!r} is not in its document"
        changed = {**documents, entry.path: edited}
        if not _attribution_violations(changed) or not _allowlist_problems(changed):
            still_exempt.append(entry.excerpt)
    assert not still_exempt, (
        f"a claim written into these pinned clauses went unreported: {still_exempt}"
    )


# --- The reader's own tests -------------------------------------------------------------------

READER_TEST_ABSENT: Final[frozenset[str]] = frozenset({"agent", "factor", "model"})
"""The absent set the tests of the reader's mechanics run against.

Fixed rather than read from `ABSENT_CATEGORIES`, so that those tests measure how the guard reads
a document and not what the derivation yields today; the derivation has its own test.
"""


def _flagged_categories(text: str) -> frozenset[str]:
    """Every category some clause of `text` presents as delivered, against `READER_TEST_ABSENT`."""
    return frozenset().union(
        *(flagged.categories for flagged in _flagged_clauses(text, READER_TEST_ABSENT))
    )


def test_the_overclaim_guard_tells_a_claim_from_a_caveated_mention() -> None:
    """Mutation check for `_overclaimed_categories` itself, independent of the guarded files.

    Minimal strings in the shapes the guard exists to tell apart, each classified as one clause.
    The two single-category claims are what threshold one is for: at the old threshold of two,
    both passed.
    """
    cases: tuple[tuple[str, frozenset[str]], ...] = (
        ("本项目对规则/因子/Agent 三类归因均已完整支持。", frozenset({"agent", "factor"})),
        ("每个 Agent 的贡献都有归因。", frozenset({"agent"})),
        ("Agent attribution tells you which agent earned the return.", frozenset({"agent"})),
        ("因子与 Agent 归因结构性从不产生而非被收窄，仅规则类目两项条款可查。", frozenset()),
        ("归因只产出规则类目下的两项条款。", frozenset()),
    )
    for clause, expected in cases:
        got = _overclaimed_categories(clause, READER_TEST_ABSENT)
        assert got == expected, (
            f"_overclaimed_categories({clause!r}) returned {sorted(got)}; expected "
            f"{sorted(expected)}"
        )


PRE_D4AF27C_README_44: Final[str] = (
    "- **结果可解释**：统一计入 A 股交易约束与成本，"
    "并提供规则、因子、智能体与模型归因，"
    "未被认领的部分记在显式残差里而不是摊进最后一项。\n"
)
"""`README.md` line 44 at `d4af27c^`, verbatim: a bullet naming factor, agent and model."""

PRE_D4AF27C_README_1183_1186: Final[str] = (
    "冻结语料回放再次调用同一 `run_cycle`，"
    "用于检查确定性和前视问题\N{FULLWIDTH SEMICOLON}"
    "多日组合 API 计算\n"
    "收益、基准、换手、容量和暴露\N{FULLWIDTH SEMICOLON}"
    "事件研究 API 给出 CAR、t 统计量和确定性 Bootstrap\n"
    "置信区间\N{FULLWIDTH SEMICOLON}"
    "结果验证 API 则先复核内容派生 ID，"
    "再把未来观察归因到规则、因子和 Agent。\n"
    "这些结果由研究者审阅后用于调整数据质量、路由、"
    "风险阈值和筛选条件，不会自动修改模型。\n"
)
"""`README.md` lines 1183-1186 at `d4af27c^`, verbatim: a plain, un-bulleted paragraph.

Its third line claims attribution to rules, factors and Agents. The guard `9eb8368` added caught
it only because that one physical line happened to hold 归因 and both categories. The review of
`9eb8368` re-wrapped it and measured the misses: 19 of the 61 widths from 20 to 80, and a single
line break at 9 of the 16 places inside "再把未来观察归因到规则、因子和 Agent。".
"""

PRE_D4AF27C_README_EN_51_52: Final[str] = (
    "- evidence-linked decisions and reconciled rule/factor/agent/model attribution with an\n"
    "  explicit unexplained residual.\n"
)
"""`README.en.md` lines 51-52 at `d4af27c^`, verbatim: a soft-wrapped bullet."""

PRE_9EB8368_WHY_OPENALPHA_26: Final[str] = (
    "| 验证改进 | 决策、未来观察、基准、成本、规则/因子/Agent 归因对账 | "
    "知道结果来自哪里，而不是只看一条收益曲线 |\n"
)
"""`docs/why-openalpha-cn.zh-CN.md` line 26 at `9eb8368^`, verbatim: a table row."""

CLAIM_SENTENCE: Final[str] = (
    "结果验证 API 则先复核内容派生 ID，再把未来观察归因到规则、因子和 Agent。"
)
"""The claim sentence of `PRE_D4AF27C_README_1183_1186`, as the reader should report it."""


def test_the_four_overclaims_this_guard_was_written_for_are_flagged() -> None:
    """`d4af27c` fixed three of these without a test and `9eb8368` fixed the fourth.

    Each fixture is the text as it stood before its fix, so this is the guard's retroactive
    power, held: every one must still be flagged, for exactly the absent categories it names.
    """
    cases = (
        ("README.md:44 at d4af27c^", PRE_D4AF27C_README_44, {"agent", "factor", "model"}),
        ("README.md:1183-1186 at d4af27c^", PRE_D4AF27C_README_1183_1186, {"agent", "factor"}),
        (
            "README.en.md:51-52 at d4af27c^",
            PRE_D4AF27C_README_EN_51_52,
            {"agent", "factor", "model"},
        ),
        ("why-openalpha:26 at 9eb8368^", PRE_9EB8368_WHY_OPENALPHA_26, {"agent", "factor"}),
    )
    missed = [
        f"{label}: flagged {sorted(_flagged_categories(text))}, expected {sorted(expected)}"
        for label, text, expected in cases
        if _flagged_categories(text) != expected
    ]
    assert not missed, "the guard no longer flags a historical overclaim:\n" + "\n".join(missed)


def test_a_flagged_clause_reports_the_line_it_starts_on() -> None:
    """A violation names the clause's own first line, not its block's, so a reader can find it."""
    flagged = [
        (clause.line, clause.text)
        for clause in _flagged_clauses(PRE_D4AF27C_README_1183_1186, READER_TEST_ABSENT)
    ]
    assert flagged == [(3, CLAIM_SENTENCE)], (
        f"expected the claim sentence reported at line 3 of its fixture; got {flagged}"
    )


def _rewrap(text: str, width: int) -> str:
    """`text` greedily re-wrapped at `width` characters, never inside a Latin word or code span."""
    lines: list[str] = []
    current = ""
    for atom in re.findall(r"`[^`]*`|[A-Za-z0-9][A-Za-z0-9_./-]*| |.", text):
        if current and len(current) + len(atom) > width:
            lines.append(current.strip())
            current = atom.lstrip()
        else:
            current += atom
    lines.append(current.strip())
    return "\n".join(line for line in lines if line) + "\n"


def _is_latin(char: str) -> bool:
    return char.isascii() and char.isalnum()


def test_a_paragraph_claim_is_flagged_wherever_its_lines_are_wrapped() -> None:
    """The review's I-1 probe, as a test: where a soft wrap falls must not decide the verdict.

    The pre-`d4af27c` paragraph is unwrapped and then re-wrapped greedily at every width from 20
    to 80 characters, and separately broken once at every position inside its claim sentence --
    including inside 归因 -- except inside a Latin word, where a break would change the word.
    Every variant must be flagged for exactly factor and agent.
    """
    joined = "".join(line.strip() for line in PRE_D4AF27C_README_1183_1186.splitlines())
    expected = frozenset({"agent", "factor"})
    missed_widths = [
        width for width in range(20, 81) if _flagged_categories(_rewrap(joined, width)) != expected
    ]
    start = joined.index(CLAIM_SENTENCE)
    breaks = [
        position
        for position in range(start + 1, start + len(CLAIM_SENTENCE))
        if not (_is_latin(joined[position - 1]) and _is_latin(joined[position]))
    ]
    missed_breaks = [
        f"{joined[position - 3 : position]}|{joined[position : position + 3]}"
        for position in breaks
        if _flagged_categories(f"{joined[:position]}\n{joined[position:]}\n") != expected
    ]
    assert not missed_widths and not missed_breaks, (
        f"missed at {len(missed_widths)} of 61 wrap widths {missed_widths} and at "
        f"{len(missed_breaks)} of {len(breaks)} single line breaks {missed_breaks}"
    )


def test_an_absence_phrase_exempts_only_its_own_clause_and_only_unnegated() -> None:
    """The review of `9eb8368` showed the first four passing; each must be flagged.

    Each presents factor and Agent attribution as delivered beside an absence phrase that
    disclaims something else: in the next clause (the first, second and fourth), or negated in
    the same one (the third, 并非). The fifth and sixth negate an English and a second Chinese
    phrase; the seventh and eighth end the claim's clause with a full-width exclamation or
    question mark. The last two negate with "n't" and "never": the review of `D7` found that no
    case held either, so dropping them from `NEGATION_BEFORE` went unnoticed.
    """
    claims = (
        "归因覆盖规则、因子与 Agent 三类\N{FULLWIDTH SEMICOLON}模型类目结构性从不产生而非被收窄。",
        "提供因子与 Agent 归因\N{FULLWIDTH SEMICOLON}实时行情接口结构性不产出盘口数据。",
        "因子与 Agent 归因并非结构性不产生，而是已全部交付。",
        "Factor and agent attribution is delivered; tick data is structurally absent.",
        "Factor and agent attribution is not structurally absent.",
        "因子与 Agent 归因不是结构性不产出的。",
        "提供因子与 Agent 归因\N{FULLWIDTH EXCLAMATION MARK}模型结构性从不产生而非被收窄。",
        "提供因子与 Agent 归因\N{FULLWIDTH QUESTION MARK}模型结构性从不产生而非被收窄。",
        "Factor and agent attribution isn't structurally absent.",
        "Factor and agent attribution is never structurally absent.",
    )
    missed = [claim for claim in claims if _flagged_categories(claim) != {"agent", "factor"}]
    assert not missed, f"read as caveated, but each claims factor and agent: {missed}"


def test_plural_english_category_and_attribution_words_count() -> None:
    """`\\b{marker}\\b` never matched "factors" or "agents"; the review's I-3 string passed."""
    cases = (
        (
            "Reconciled attribution across rules, factors, agents and models.",
            {"agent", "factor", "model"},
        ),
        ("Factor and agent attributions are reconciled to the final result.", {"agent", "factor"}),
    )
    missed = [
        f"{text!r}: flagged {sorted(_flagged_categories(text))}, expected {sorted(expected)}"
        for text, expected in cases
        if _flagged_categories(text) != expected
    ]
    assert not missed, "\n".join(missed)


def test_latin_category_words_glued_to_chinese_count() -> None:
    """Python's `\\b` treats a CJK character as a word character, so these never matched."""
    cases = ("验证阶段提供因子与Agent归因。", "验证阶段提供factor与agent归因。")
    missed = [text for text in cases if _flagged_categories(text) != {"agent", "factor"}]
    assert not missed, f"a Latin category word glued to Chinese was not read: {missed}"


def test_a_hyphen_or_underscore_does_not_hide_a_category_word() -> None:
    """The lookarounds bound an English category word by ASCII letters only, so `-` and `_` do
    not hide it -- `\\b` missed "agent_id", because `_` is a word character to it."""
    cases = ("Per-agent attribution is delivered.", "Attribution is kept per agent_id.")
    missed = [text for text in cases if _flagged_categories(text) != {"agent"}]
    assert not missed, f"a category word beside '-' or '_' was not read: {missed}"


TWO_LINE_CLAIMS: Final[dict[str, str]] = {
    "plain paragraph": "验证层提供规则、因子与 Agent\n归因对账，结果可解释。\n",
    "English bullet wrapped after a category word": (
        "- Factor and agent\n  attribution is delivered.\n"
    ),
    "plain paragraph, indented continuation": "验证层提供规则、因子与 Agent\n  归因对账。\n",
    "'-' bullet": "- 验证层提供规则、因子与 Agent\n  归因对账，结果可解释。\n",
    "'*' bullet": "* 验证层提供规则、因子与 Agent\n  归因对账，结果可解释。\n",
    "'+' bullet": "+ 验证层提供规则、因子与 Agent\n  归因对账，结果可解释。\n",
    "'1.' bullet": "1. 验证层提供规则、因子与 Agent\n   归因对账，结果可解释。\n",
    "'1)' bullet": "1) 验证层提供规则、因子与 Agent\n   归因对账，结果可解释。\n",
    "lazy (unindented) bullet continuation": "- 验证层提供规则、因子与 Agent\n归因对账。\n",
    "blockquote": "> 验证层提供规则、因子与 Agent\n> 归因对账，结果可解释。\n",
    "blockquote, lazy continuation": "> 验证层提供规则、因子与 Agent\n归因对账。\n",
    "list nested under its item": "- 归因覆盖：\n  - 因子\n  - Agent\n",
    "list nested two levels deep": "1. 归因覆盖：\n   - 规则\n     - 因子\n     - Agent\n",
}
"""A claim split over two or more lines in each block form markdown allows."""


def test_every_block_form_is_read_with_its_continuation_lines_folded_back() -> None:
    """The review's D6-M4 document cases, plus the forms around them, each flagged whole."""
    missed = [
        form
        for form, text in TWO_LINE_CLAIMS.items()
        if _flagged_categories(text) != {"agent", "factor"}
    ]
    assert not missed, f"a claim split over lines was not read whole in: {missed}"


def test_a_negation_negates_only_the_phrase_it_directly_precedes() -> None:
    """A negation elsewhere in the clause, or before another copy of the phrase, negates nothing.

    Each clause states a true absence. In the first two a negation stands earlier in the clause
    but not directly before the absence phrase; in the third the phrase appears twice, negated
    once and stated once. Each must read as caveated, so none may be flagged.
    """
    caveated = (
        "因子与 Agent 归因不是被收窄，而是结构性不产生。",
        "Factor and agent attribution is not narrowed: it is structurally absent.",
        "有人说因子与 Agent 归因并非结构性不产生，但它们确实结构性不产生。",
    )
    wrongly = {
        text: sorted(_flagged_categories(text)) for text in caveated if _flagged_categories(text)
    }
    assert not wrongly, f"a caveated clause was read as a claim: {wrongly}"


_CAVEAT_LINE: Final[str] = "因子与 Agent 归因结构性不产生，"
_CLAIM_LINE: Final[str] = "验证层提供因子与 Agent 归因。"

BLOCK_BOUNDARIES: Final[dict[str, str]] = {
    "a '-' item after a paragraph line": f"{_CAVEAT_LINE}\n- {_CLAIM_LINE}\n",
    "a '*' item after a paragraph line": f"{_CAVEAT_LINE}\n* {_CLAIM_LINE}\n",
    "a '+' item after a paragraph line": f"{_CAVEAT_LINE}\n+ {_CLAIM_LINE}\n",
    "a '1.' item after a paragraph line": f"{_CAVEAT_LINE}\n1. {_CLAIM_LINE}\n",
    "a '1)' item after a paragraph line": f"{_CAVEAT_LINE}\n1) {_CLAIM_LINE}\n",
    "a '####' heading above a paragraph": f"#### {_CAVEAT_LINE}\n{_CLAIM_LINE}\n",
    "a '######' heading above a paragraph": f"###### {_CAVEAT_LINE}\n{_CLAIM_LINE}\n",
    "two lines of a '~~~' fence": f"~~~\n{_CAVEAT_LINE}\n{_CLAIM_LINE}\n~~~\n",
    "a list item after a quote": f"> {_CAVEAT_LINE}\n- {_CLAIM_LINE}\n",
}
"""A claim on the second line, and on the first a caveat that would exempt it if the two lines
were folded into one clause. The first line ends in a comma, so only the block boundary keeps
them apart."""


def test_a_list_item_heading_or_fence_line_never_folds_into_the_line_above() -> None:
    """Each marker, heading level and fence form must open a block of its own.

    `TWO_LINE_CLAIMS` cannot show this for `+`, `1)`, `####` or `~~~`: its lines fold into a
    plain paragraph and are still flagged when the marker is not recognised. Here a missed
    boundary folds the claim into the caveat above it, and the claim goes unflagged.
    """
    missed = [
        form
        for form, text in BLOCK_BOUNDARIES.items()
        if _flagged_categories(text) != {"agent", "factor"}
    ]
    assert not missed, f"a block boundary was folded away in: {missed}"


def test_the_guards_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states, measured, so the docstring cannot drift from it.

    A change that starts flagging one of the `unflagged` rows has closed a stated blind spot:
    delete the row here and the sentence in the docstring together. The last assertion is the
    other direction -- a caveat worded outside `ABSENCE_PHRASES` is read as a claim.
    """
    unflagged = {
        "one absence phrase exempts every category in its clause": (
            "因子归因已交付，模型结构性从不产生而非被收窄。"
        ),
        "the English verb is not a marker": (
            "The validation API attributes each outcome to the factor and agent behind it."
        ),
        "a paraphrase without 归因 is not a claim": (
            "结果验证把未来观察拆分为因子和 Agent 层面的贡献。"
        ),
        "a claim split across a blank line": "验证层覆盖因子与 Agent\n\n归因对账，结果可解释。\n",
        "a claim split across a quoted blank line": "> 验证层覆盖因子与 Agent\n>\n> 归因对账。\n",
        "a claim split across a heading and its paragraph": "## 归因覆盖\n因子与 Agent 均已交付\n",
        "a claim split across two table rows": "| 归因覆盖 |\n| 因子与 Agent |\n",
        "a claim split across two lines of a code block": "```\n归因覆盖\n因子与 Agent\n```\n",
        "a claim split across two clauses": "归因覆盖三类。因子、Agent 与规则均已交付。",
        "a synonym outside CATEGORY_MARKERS": "差异才能归因到角色本身。",
    }
    wrongly_flagged = {
        label: sorted(_flagged_categories(text))
        for label, text in unflagged.items()
        if _flagged_categories(text)
    }
    assert not wrongly_flagged, (
        f"a stated blind spot is now flagged -- update the docstring with it: {wrongly_flagged}"
    )
    differently_worded_caveat = "因子与 Agent 归因从不产生。"
    assert _flagged_categories(differently_worded_caveat) == {"agent", "factor"}, (
        f"{differently_worded_caveat!r} is a caveat outside ABSENCE_PHRASES; the docstring says "
        "it is read as a claim"
    )


def test_an_english_sentence_end_splits_a_clause_but_an_abbreviation_does_not() -> None:
    """`.` then a space ends a clause, except before a lowercase word, as in "e.g. factor".

    The first claim's absence phrase sits in the next English sentence and must not reach back;
    the second would be cut in half, and missed, if "e.g." ended a clause.
    """
    claims = (
        "Factor and agent attribution is delivered. Model attribution is structurally absent.",
        "Attribution, e.g. factor and agent terms, is delivered to every run.",
    )
    missed = [claim for claim in claims if _flagged_categories(claim) != {"agent", "factor"}]
    assert not missed, f"English clause splitting misread: {missed}"


def test_the_absent_categories_are_the_ones_the_limitation_codes_name() -> None:
    """The derivation is keyed on English words inside identifiers, so it is held explicitly.

    `_categories_named_by` reads each code's `_`-separated tokens, singular or plural. A
    limitation added, removed or renamed so that the derived set moves fails here with the set it
    now derives -- re-read the guarded documents' attribution wording against it before changing
    the expectation.
    """
    assert set(CATEGORY_MARKERS) == set(CATEGORY_VOCABULARY), (
        f"CATEGORY_MARKERS covers {sorted(CATEGORY_MARKERS)} but AttributionTerm.category admits "
        f"{sorted(CATEGORY_VOCABULARY)}; give every category its surface forms"
    )
    plural = "neither_factors_nor_models_are_ever_produced_here"
    assert _categories_named_by([plural]) == {"factor", "model"}, (
        f"a limitation code naming its categories in the plural ({plural}) derived "
        f"{sorted(_categories_named_by([plural]))}"
    )
    assert sorted(ABSENT_CATEGORIES) == ["agent", "factor", "model"], (
        f"KNOWN_ATTRIBUTION_LIMITATIONS now derives {sorted(ABSENT_CATEGORIES)} as never produced "
        "(it derived agent, factor and model when this was written)"
    )


PRD_EXCERPT: Final[str] = (
    "| ID | Story | 状态 | 说明 |\n"
    "|---|---|---|---|\n"
    "| S63 | Multiple-testing controls for broad factor and model searches | **IN** | 不可省 |\n"
    "| S65 | Rule, factor, model and Agent attribution reconciled to final result "
    "| **IN-降级** | 规则类目两项条款 IN |\n"
    "| S79 | Portfolio and attribution dashboards | **IN** | 页 4 |\n"
)
"""Three real PRD rows in the PRD's own shape: `S65` as corrected, and two rows that share its
vocabulary without its defect (`S63` names categories but not attribution, `S79` the reverse)."""

S65_EXCERPT_ROW: Final[str] = (
    "| S65 | Rule, factor, model and Agent attribution reconciled to final result "
    "| **IN-降级** | 规则类目两项条款 IN |"
)


def test_the_prd_coupling_refuses_every_way_s65_could_slip_back_to_in() -> None:
    """Each edit the review of `9eb8368` found passing, and the ones next to them, must fail."""
    bare_s65 = S65_EXCERPT_ROW.replace("**IN-降级**", "**IN**")
    mutations = {
        "S65 marked **IN**": PRD_EXCERPT.replace(S65_EXCERPT_ROW, bare_s65),
        "S65 marked plain IN": PRD_EXCERPT.replace("**IN-降级**", "IN"),
        "S65 marked `IN`": PRD_EXCERPT.replace("**IN-降级**", "`IN`"),
        "S65 marked **`IN`**": PRD_EXCERPT.replace("**IN-降级**", "**`IN`**"),
        "S65's story without 'attribution', **IN**": PRD_EXCERPT.replace(
            S65_EXCERPT_ROW, bare_s65.replace("attribution reconciled", "contributions reconciled")
        ),
        "S65's story in Chinese, **IN**": PRD_EXCERPT.replace(
            S65_EXCERPT_ROW,
            bare_s65.replace(
                "Rule, factor, model and Agent attribution reconciled to final result",
                "规则、因子、模型与 Agent 归因对账到最终结果",
            ),
        ),
        "a bare-IN S65 duplicate before the real one": PRD_EXCERPT.replace(
            S65_EXCERPT_ROW, f"{bare_s65}\n{S65_EXCERPT_ROW}"
        ),
        "a bare-IN S65 duplicate after the real one": PRD_EXCERPT.replace(
            S65_EXCERPT_ROW, f"{S65_EXCERPT_ROW}\n{bare_s65}"
        ),
        "S63 duplicated": PRD_EXCERPT.replace(
            "| S79 |", "| S63 | Multiple-testing controls | **IN** | |\n| S79 |"
        ),
        "S65 deleted": PRD_EXCERPT.replace(f"{S65_EXCERPT_ROW}\n", ""),
        "S65 renumbered S650": PRD_EXCERPT.replace("| S65 |", "| S650 |"),
        "S63 names attribution, **IN** (generic reach)": PRD_EXCERPT.replace(
            "and model searches", "and model attribution searches"
        ),
        "a Chinese story naming 因子 and 归因, **IN** (generic reach)": (
            PRD_EXCERPT + "| S98 | 因子与模型归因报告 | **IN** | |\n"
        ),
    }
    unmutated = _prd_problems(PRD_EXCERPT, READER_TEST_ABSENT)
    assert not unmutated, f"the unmutated excerpt should pass; got {unmutated}"
    accepted = [
        label for label, text in mutations.items() if not _prd_problems(text, READER_TEST_ABSENT)
    ]
    assert not accepted, f"the PRD coupling accepted: {accepted}"


# --- The diagram reader -----------------------------------------------------------------------

EXAMPLE_GENERATOR: Final[str] = "scripts/generate_example_diagrams.py"
"""A generator path no file has, so the name in a report can only have come from `filename=`."""


def test_the_diagram_reader_names_the_generator_it_parsed() -> None:
    """A SyntaxError or warning from `tests/diagram_text.py` must say which generator raised it.

    Its callers read two generators one after the other, and `ast.parse` names a source it was
    given no `filename=` for `<unknown>`, which hides which of the two a report came from. The
    final review of `d4ef5e4` found both readers doing this; `1178151` fixed the same kind of
    call at three loops elsewhere. Each reader is held to both reports: a SyntaxError, and an
    invalid-escape warning (DeprecationWarning on 3.11, SyntaxWarning from 3.12).
    """
    for reader in (diagram_strings, diagram_units):
        with pytest.raises(SyntaxError) as raised:
            reader("svg.text(1, 2, 'unclosed'\n", filename=EXAMPLE_GENERATOR)
        assert raised.value.filename == EXAMPLE_GENERATOR, (
            f"{reader.__name__}'s SyntaxError names {raised.value.filename!r}"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reader('svg.text(1, 2, "\\q")\n', filename=EXAMPLE_GENERATOR)
        named = [str(warning.filename) for warning in caught]
        assert named and all(name == EXAMPLE_GENERATOR for name in named), (
            f"{reader.__name__}'s warnings name {named}"
        )
