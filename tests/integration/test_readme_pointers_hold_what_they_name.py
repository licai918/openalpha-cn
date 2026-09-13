"""A README pointer that names what its target includes must name something the target holds.

`README.en.md:100` sent a reader to `docs/api/http.md` "for the named boundaries -- including
`V2-P4-026`", and http.md holds neither: a factor run's named boundaries are `openalpha factor
list --json`'s `run_limitations`, declared in `factor_view.py#KNOWN_FACTOR_RUN_LIMITATIONS`, and
the issue id is in the code and the changelog entry that closed it. The review of `D13`'s rebase
round found it by reading http.md for the id.

This reads `README.md` and `README.en.md` as `tests/prose_clauses.py` reads them. In each clause,
a markdown link to a file in this repository followed by "including" or 包括 and then a list of
backticked names is a claim that the linked file holds each of those names, and each must occur
in it verbatim.

**What it cannot see.** Only that shape: a pointer that says "see X for Y" without "including",
or names what X holds in plain words rather than in backticks, passes. A link to a heading
(`#...`) is read as its whole file, so a name held elsewhere in the file passes, and a name that
occurs in the target in another sense passes too -- occurrence is not relevance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from prose_clauses import clauses

ROOT = Path(__file__).resolve().parents[2]
READMES: Final[tuple[Path, ...]] = (ROOT / "README.md", ROOT / "README.en.md")

_LINK: Final[re.Pattern[str]] = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s#]+)(?:#[^)]*)?\)")
"""A markdown link, with its target's path apart from any `#anchor`."""

_INCLUDING: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z])including(?![A-Za-z])|包括")
"""The word that turns a pointer into a claim about what its target holds."""

_NAMES: Final[re.Pattern[str]] = re.compile(r"\s*(?:`[^`]+`(?:\s*(?:,|、|and|和|与|or|或)\s*)?)+")
"""The backticked names listed directly after that word, and nothing past the list."""


def _pointer_problems(document: str, label: str) -> list[str]:
    """One message per name a pointer in `document` says its target includes and it does not."""
    problems: list[str] = []
    for clause in clauses(document):
        links = list(_LINK.finditer(clause.text))
        for index, link in enumerate(links):
            stop = links[index + 1].start() if index + 1 < len(links) else len(clause.text)
            after = clause.text[link.end() : stop]
            keyword = _INCLUDING.search(after)
            if keyword is None:
                continue
            listed = _NAMES.match(after, keyword.end())
            if listed is None:
                continue
            target = ROOT / link["target"]
            held = target.read_text(encoding="utf-8") if target.is_file() else ""
            problems.extend(
                f"{label}:{clause.line} says {link['target']} includes `{name}`, and it does not"
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
