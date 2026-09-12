"""User-facing markdown read back as clauses, for the tests that hold prose to the code.

Two guards read documents with this one reader:
`tests/integration/test_attribution_claims_match_known_limitations.py` (no attribution category
the code never produces may be presented as delivered) and
`tests/integration/test_usage_claims_match_the_shipped_paths.py` (no model usage recording may be
presented as something a shipped path does). Each guard decides what a *claim* is. This module
decides only what a *clause* is and how a marker word or a negated phrase is matched, so a change
to either is made in one place and moves both guards at once. Its mechanics are measured by the
attribution guard's reader tests -- block forms, every wrap width, clause ends, glued and
hyphenated English words, negation -- which drive it through that guard's classifier.

- *Blocks.* A paragraph, a list item and a blockquote are each one block, with their
  soft-wrapped continuation lines folded back in: indented or lazy (unindented)
  continuations, after any list marker (`-`, `*`, `+`, `1.`, `1)`), and lists nested under an
  item, which fold into that item. A table row, a heading and each line of a fenced code block
  are blocks of their own. A line break folds away to nothing when either side of it is
  non-ASCII -- a wrap between two Chinese characters, even inside 归因, is no word boundary --
  and to one space between two ASCII characters.
- *Clauses.* Each block is split after `。`, after a full-width semicolon, exclamation mark or
  question mark, after an ASCII `;`, and at an English sentence end: `.`, `!` or `?`, then a
  space, then anything but a lowercase letter -- so "e.g. factor" stays whole. A full-width or
  ASCII comma does not end a clause.
- *Markers.* `marker_pattern` matches a Chinese word as a substring and an English word in any
  case with an optional plural `s`, bounded by ASCII-letter lookarounds instead of `\\b`.
  Python's `\\b` treats a CJK character as a word character, so "因子与Agent归因" never matched
  under it. The lookarounds also let "per-agent" and "agent_id" name `agent`.
- *Negated phrases.* `holds_unnegated_phrase` finds a phrase only where no negation (非, 不是,
  "not", "never", "n't") ends directly before it: 并非结构性不产生 says the opposite of
  结构性不产生.

**What it cannot see.** A claim split across two blocks -- a blank line, a heading, a table row or
two lines of a code block between its halves -- or across two clauses is read as two halves, and
each guard judges each half on its own.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause of a document, with the 1-based line it starts on."""

    line: int
    text: str


NEGATION_BEFORE: Final[re.Pattern[str]] = re.compile(
    r"(?:非|不是)\s*$|(?<![A-Za-z])(?:not|never)\s+$|n't\s+$", re.IGNORECASE
)
"""A negation that ends directly before a phrase: 并非结构性不产生 says the opposite."""


def marker_pattern(marker: str) -> re.Pattern[str]:
    """`marker` as a pattern: a substring if Chinese, a bounded word with optional `s` if ASCII."""
    if marker.isascii():
        return re.compile(rf"(?<![A-Za-z]){re.escape(marker)}s?(?![A-Za-z])", re.IGNORECASE)
    return re.compile(re.escape(marker))


def holds_unnegated_phrase(text: str, phrases: Iterable[str]) -> bool:
    """Whether `text` holds one of `phrases`, in any case, with no negation directly before it."""
    lowered = text.lower()
    for phrase in phrases:
        found = lowered.find(phrase.lower())
        while found != -1:
            if NEGATION_BEFORE.search(lowered, 0, found) is None:
                return True
            found = lowered.find(phrase.lower(), found + 1)
    return False


_FENCE: Final[re.Pattern[str]] = re.compile(r"\s*(?:```|~~~)")
_HEADING: Final[re.Pattern[str]] = re.compile(r"[ \t]{0,3}#{1,6}(?:[ \t]|$)")
_LIST_ITEM: Final[re.Pattern[str]] = re.compile(
    r"(?P<indent>[ \t]*)(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+|$)"
)
_QUOTE: Final[re.Pattern[str]] = re.compile(r"[ \t]*> ?")

_CJK_CLAUSE_ENDS: Final[str] = (
    "。\N{FULLWIDTH SEMICOLON}\N{FULLWIDTH EXCLAMATION MARK}\N{FULLWIDTH QUESTION MARK}"
)
_CLAUSE_END: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[{_CJK_CLAUSE_ENDS};])\s*|(?<=[.!?])\s(?![a-z])"
)


def _opens_block(raw: str) -> bool:
    return bool(
        raw.strip().startswith("|")
        or _FENCE.match(raw)
        or _HEADING.match(raw)
        or _LIST_ITEM.match(raw)
    )


def _blocks(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Group `(line number, raw line)` pairs into blocks of `(line number, text)` pieces.

    A blank line closes a block. A table row, a heading and each line inside a fenced code block
    are blocks of their own. A list marker opens a new block unless it is indented deeper than
    the item the current block opened with, in which case it is a nested item and stays in that
    block; the marker itself is dropped. Any other line continues the current block, whatever
    its indentation. A blockquote's lines, and the lazy lines that continue it, are stripped of
    one `>` and grouped by this same function.
    """
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    quoted: list[tuple[int, str]] = []
    item_indent: int | None = None
    in_fence = False

    def close() -> None:
        nonlocal item_indent
        if current:
            blocks.append(current.copy())
            current.clear()
        item_indent = None

    def close_quote() -> None:
        if quoted:
            blocks.extend(_blocks(quoted.copy()))
            quoted.clear()

    for number, raw in lines:
        stripped = raw.strip()
        if in_fence:
            if _FENCE.match(raw):
                in_fence = False
            elif stripped:
                blocks.append([(number, stripped)])
            continue
        quote = _QUOTE.match(raw)
        if quote is not None:
            close()
            quoted.append((number, raw[quote.end() :]))
            continue
        if quoted and stripped and not _opens_block(raw):
            quoted.append((number, raw))
            continue
        close_quote()
        item = _LIST_ITEM.match(raw)
        if not stripped:
            close()
        elif _FENCE.match(raw):
            close()
            in_fence = True
        elif stripped.startswith("|") or _HEADING.match(raw):
            close()
            blocks.append([(number, stripped)])
        elif item is not None:
            indent = len(item["indent"].expandtabs(4))
            if item_indent is None or indent <= item_indent:
                close()
                item_indent = indent
            current.append((number, raw[item.end() :].strip()))
        else:
            current.append((number, stripped))
    close_quote()
    close()
    return blocks


def _fold(pieces: list[tuple[int, str]]) -> tuple[str, list[int], list[int]]:
    """One block's pieces as a single line, with each piece's start offset and line number."""
    text = ""
    offsets: list[int] = []
    numbers: list[int] = []
    for number, piece in pieces:
        words = " ".join(piece.split())
        if not words:
            continue
        if text and text[-1].isascii() and words[0].isascii():
            text += " "
        offsets.append(len(text))
        numbers.append(number)
        text += words
    return text, offsets, numbers


def clauses(document: str) -> list[Clause]:
    """`document` read as clauses: blocks folded back together, then split at clause ends."""
    found: list[Clause] = []
    for block in _blocks(list(enumerate(document.splitlines(), start=1))):
        text, offsets, numbers = _fold(block)
        ends = [(end.start(), end.end()) for end in _CLAUSE_END.finditer(text)]
        start = 0
        for stop, resume in [*ends, (len(text), len(text))]:
            clause = text[start:stop].strip()
            if clause:
                found.append(Clause(numbers[bisect_right(offsets, start) - 1], clause))
            start = resume
    return found
