"""A README pointer that names what its target includes must name something the target holds.

`README.en.md:100` sent a reader to `docs/api/http.md` "for the named boundaries -- including
`V2-P4-026`", and http.md holds neither: a factor run's named boundaries are `openalpha factor
list --json`'s `run_limitations`, declared in `factor_view.py#KNOWN_FACTOR_RUN_LIMITATIONS`, and
the issue id is in the code and the changelog entry that closed it. The review of `D13`'s rebase
round found it by reading http.md for the id.

This reads `README.md` and `README.en.md` as `tests/prose_clauses.py` reads them. In each clause,
a markdown link to a path in this repository followed by "including", in any case, or 包括, and
then a list of backticked names, is a claim that the linked path holds each of those names, and
each must occur in it verbatim. A link is inline (`[text](path)`) or reference-style
(`[text][label]`, `[label][]` or `[label]`, with `[label]: path` defined in the same README and
labels matched regardless of case). A file holds its text; a directory holds the path of every
entry beneath it and the text of every file there. "including" directly after a negation
(`prose_clauses.NEGATION_BEFORE`: "not", "never", "n't", 非, 不是) is no claim, and neither is
不包括. The review of `D13` (its m-11) found the check reporting true pointers to an external
page, to a directory and after 不包括, and passing false ones after a capitalised "Including" and
behind a reference-style link.

**What it cannot see.** Only that shape: a pointer that says "see X for Y" without "including",
or names what X holds in plain words rather than in backticks, passes, and so does one worded
"includes" or 包含. A link to a page outside this repository -- a target with a URL scheme such
as `https:` -- is not read at all: this check cannot fetch it. A negated pointer is not checked
the other way, so 不包括 before a name the target does hold passes. A link to a heading (`#...`)
is read as its whole file, so a name held elsewhere in the file passes, and a name that occurs in
the target in another sense passes too -- occurrence is not relevance; a reference to a heading
of the same README is not read at all. `test_the_pointer_checks_stated_blind_spots_are_real`
measures the external link, the negated pointer and the two wordings.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

import pytest
from prose_clauses import NEGATION_BEFORE, clauses

ROOT = Path(__file__).resolve().parents[2]
READMES: Final[tuple[Path, ...]] = (ROOT / "README.md", ROOT / "README.en.md")

_INLINE_LINK: Final[re.Pattern[str]] = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s#]+)(?:#[^)]*)?\)")
"""An inline markdown link, with its target's path apart from any `#anchor`."""

_REFERENCE_LINK: Final[re.Pattern[str]] = re.compile(
    r"\[(?P<text>[^\]]*)\](?:\[(?P<label>[^\]]*)\])?"
)
"""A reference-style link: `[text][label]`, `[label][]`, or `[label]` alone."""

_DEFINITION: Final[re.Pattern[str]] = re.compile(
    r"^[ ]{0,3}\[(?P<label>[^\]]+)\]:[ \t]*<?(?P<target>[^\s>]+)>?", re.MULTILINE
)
"""A link reference definition, `[label]: target`, its target in angle brackets or not."""

_EXTERNAL: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
"""A target with a URL scheme -- `https:`, `mailto:` -- which this check cannot read."""

_INCLUDING: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])including(?![A-Za-z])|(?<!不)包括", re.IGNORECASE
)
"""The word that turns a pointer into a claim about what its target holds, in any case. 不包括
is its negation, and so is "including" directly after one `NEGATION_BEFORE` reads."""

_NAMES: Final[re.Pattern[str]] = re.compile(r"\s*(?:`[^`]+`(?:\s*(?:,|、|and|和|与|or|或)\s*)?)+")
"""The backticked names listed directly after that word, and nothing past the list."""


def _label(text: str) -> str:
    """A reference label as markdown matches it: case-folded, runs of whitespace collapsed."""
    return " ".join(text.split()).casefold()


def _links(text: str, definitions: dict[str, str]) -> list[tuple[int, int, str]]:
    """Each link in `text` this check can read, in order: its span and its target's path.

    Inline links, and reference-style links whose label `definitions` defines, each with its
    target's `#anchor` dropped. A target with a URL scheme (`_EXTERNAL`) is left out, and so is
    a reference to a heading of the same file, whose path is empty once the anchor is dropped.
    """
    links = [(link.start(), link.end(), link["target"]) for link in _INLINE_LINK.finditer(text)]
    for link in _REFERENCE_LINK.finditer(text):
        if any(start <= link.start() < end for start, end, _ in links):
            continue
        target = definitions.get(_label(link["label"] or link["text"]))
        if target is None:
            continue
        path = target.partition("#")[0]
        if path:
            links.append((link.start(), link.end(), path))
    return sorted(link for link in links if not _EXTERNAL.match(link[2]))


def _held(target: Path) -> str:
    """What a pointer's target holds: a file's text; for a directory, the path of every entry
    beneath it and the text of every file there; for a target that is neither, nothing."""
    if target.is_file():
        return target.read_text(encoding="utf-8")
    if target.is_dir():
        entries = sorted(target.rglob("*"))
        paths = [entry.relative_to(target).as_posix() for entry in entries]
        texts = [
            entry.read_text(encoding="utf-8", errors="replace")
            for entry in entries
            if entry.is_file()
        ]
        return "\n".join(paths + texts)
    return ""


def _pointer_problems(document: str, label: str) -> list[str]:
    """One message per name a pointer in `document` says its target includes and it does not."""
    definitions = {
        _label(match["label"]): match["target"] for match in _DEFINITION.finditer(document)
    }
    problems: list[str] = []
    for clause in clauses(document):
        links = _links(clause.text, definitions)
        for index, (_, end, target) in enumerate(links):
            stop = links[index + 1][0] if index + 1 < len(links) else len(clause.text)
            for keyword in _INCLUDING.finditer(clause.text, end, stop):
                if NEGATION_BEFORE.search(clause.text, end, keyword.start()) is not None:
                    continue
                listed = _NAMES.match(clause.text, keyword.end(), stop)
                if listed is None:
                    continue
                held = _held(ROOT / target)
                problems.extend(
                    f"{label}:{clause.line} says {target} includes `{name}`, and it does not"
                    for name in re.findall(r"`([^`]+)`", listed.group())
                    if name not in held
                )
    return problems


def test_a_readme_pointer_names_only_what_its_target_holds() -> None:
    """Every "see X ... including `Y`" pointer in the two READMEs names a `Y` that X holds."""
    problems = [
        problem
        for path in READMES
        for problem in _pointer_problems(path.read_text(encoding="utf-8"), path.name)
    ]
    assert not problems, "\n".join(problems)


def test_the_pointer_check_reports_a_name_its_target_does_not_hold() -> None:
    """The READMEs may hold no failing pointer, so the check above could read nothing and pass.

    Against two planted pointers at `docs/api/http.md`, the one naming an id http.md holds
    (`V2-P4-043`) passes, and the one naming an id it does not (`V2-P4-026`) is reported.
    """
    planted = (
        "See [the HTTP contract](docs/api/http.md) for the request limit, "
        "including `V2-P4-043`.\n\n"
        "See [the HTTP contract](docs/api/http.md) for the named boundaries, "
        "including `V2-P4-026`.\n"
    )
    assert _pointer_problems(planted, "planted") == [
        "planted:3 says docs/api/http.md includes `V2-P4-026`, and it does not"
    ]


D13_POINTERS: Final[dict[str, tuple[str, list[str]]]] = {
    "an external link": (
        "See [the Tushare docs](https://tushare.pro/document/2) including `daily`.\n",
        [],
    ),
    "a directory link naming an entry": (
        "See [the API docs](docs/api/) including `schemas/run-manifest-v3.json`.\n",
        [],
    ),
    "a directory link naming what a file beneath it holds": (
        "See [the API docs](docs/api/) including `V2-P4-043`.\n",
        [],
    ),
    "a directory link naming what it does not hold": (
        "See [the API docs](docs/api/) including `V2-P4-026`.\n",
        ["V2-P4-026"],
    ),
    "不包括": ("见 [HTTP 合同](docs/api/http.md)，不包括 `V2-P4-026`。\n", []),
    "not including": (
        "See [the HTTP contract](docs/api/http.md), not including `V2-P4-026`.\n",
        [],
    ),
    "a claim after a negated one": (
        "See [the HTTP contract](docs/api/http.md), not including `V2-P4-026` but including "
        "`NO-SUCH-NAME`.\n",
        ["NO-SUCH-NAME"],
    ),
    "a capitalised Including": (
        "- [HTTP contract](docs/api/http.md): Including `V2-P4-026`\n",
        ["V2-P4-026"],
    ),
    "a full reference link": (
        "See [the HTTP contract][http] for the named boundaries, including `V2-P4-026`.\n\n"
        "[http]: docs/api/http.md\n",
        ["V2-P4-026"],
    ),
    "a collapsed reference link, defined in another case": (
        "See [HTTP Contract][] for the named boundaries, including `V2-P4-026`.\n\n"
        "[http contract]: docs/api/http.md\n",
        ["V2-P4-026"],
    ),
    "a shortcut reference link": (
        "See [HTTP contract] for the named boundaries, including `V2-P4-026`.\n\n"
        "[HTTP contract]: docs/api/http.md\n",
        ["V2-P4-026"],
    ),
    "a definition in angle brackets": (
        "See [the HTTP contract][http] for the request limit, including `V2-P4-043`.\n\n"
        "[http]: <docs/api/http.md>\n",
        [],
    ),
    "a definition with an anchor": (
        "See [the HTTP contract][size] for the request limit, including `V2-P4-043`.\n\n"
        "[size]: docs/api/http.md#request-size\n",
        [],
    ),
}
"""A planted pointer, and the names the check must report in it. The review of `D13` (its m-11)
found it reporting the true pointers of the first three shapes it names -- an external link, a
directory link and 不包括 -- and passing the false ones of the other two, a capitalised
"Including" and a reference-style link; the rest are their siblings. `docs/api/` holds the entry
`schemas/run-manifest-v3.json`, a path no file there writes out (`contracts.md` names the file
alone), and http.md holds `V2-P4-043` and neither `V2-P4-026` nor `NO-SUCH-NAME`."""


def test_the_pointer_check_reads_the_shapes_the_review_of_d13_named() -> None:
    """Each planted pointer of `D13_POINTERS` reports exactly the names it lists."""
    misread = {
        label: reported
        for label, (text, expected) in D13_POINTERS.items()
        if (
            reported := [
                name
                for problem in _pointer_problems(text, "planted")
                for name in re.findall(r"includes `([^`]+)`", problem)
            ]
        )
        != expected
    }
    assert not misread, f"reported otherwise than D13_POINTERS lists: {misread}"


def test_a_reference_to_a_heading_of_the_same_readme_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reference whose definition is only an anchor (`[top]: #section`) names a heading of the
    README itself, which the check does not read. Read as a path, what is left after the anchor
    is the repository root, so the root is a small directory here and names nothing."""
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    (tmp_path / "notes.md").write_text("nothing is named here\n", encoding="utf-8")
    planted = "See [the section][top] for more, including `NO-SUCH-NAME`.\n\n[top]: #section\n"
    assert _pointer_problems(planted, "planted") == []


POINTER_LIMITS: Final[dict[str, str]] = {
    "an external link is not read": (
        "See [the Tushare docs](https://tushare.pro/document/2) including `NO-SUCH-NAME`.\n"
    ),
    "a negated pointer is not checked the other way": (
        "见 [HTTP 合同](docs/api/http.md)，不包括 `V2-P4-043`。\n"
    ),
    "a pointer worded with includes is not read": (
        "[The HTTP contract](docs/api/http.md) includes `NO-SUCH-NAME`.\n"
    ),
    "a pointer worded with 包含 is not read": (
        "[HTTP 合同](docs/api/http.md)包含 `NO-SUCH-NAME`。\n"
    ),
}
"""False pointers the module docstring says pass, each planted alone."""


def test_the_pointer_checks_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states, measured: these false pointers are reported as
    nothing. A change that reports one of them has closed a stated blind spot: delete the entry
    and the sentence in the docstring together."""
    reported = {
        label: problems
        for label, text in POINTER_LIMITS.items()
        if (problems := _pointer_problems(text, "planted"))
    }
    assert not reported, f"a stated blind spot is now reported: {reported}"
