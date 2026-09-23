"""A structural claim in the feature ledger must say when it was true, or be read as a claim today.

`artifacts/openalpha-v1-feature-coverage/features.csv` is the largest piece of unguarded prose in
this repository -- 162 non-empty `notes` cells, 412,151 characters, the longest one 25,714 -- and
it is shipping material: an outside reader takes it as the statement of what this build does. Two
reviews running four rounds apart found the same failure in it twice, both times because the
sentence in `src/` had been corrected and the twin in this file had not.

**Why the counts guard is not pointed at this file.**
`tests/unit/test_source_counts_match_the_tree.py` holds a number against the tree, and most
numbers here are *readings* instead: "27 mutants killed", "measured
on the fixture panel at as_of 2026-01-12T04:00Z", "1,296 coverage lookups". They are supposed to
stay where they are while the tree moves, and a guard that reddened them would be turned off at
the first pipeline that ran it. The distinction that matters is not "a number" but **a
present-tense structural assertion about this repository** against **a reading with its own
time on it**.

**What this file requires.** A sentence that (1) asserts something universal or exclusive about
this repository's own structure -- what is outside a version, who is allowed to call something,
that a plane holds nothing of some kind, what the N things a module declares are, what is the
only something -- and (2) states a quantity or an enumeration while doing it, has to carry a time:
an issue id, a date, "as of", or a past-tense verb. A sentence with a time on it is a record; one
without is a claim about today, and today is what moves.

**Measured before it was written.** On the tree this file arrived in, the rule was red on exactly
the three assertions the final sweep had found by hand and on one it had not:

- `OA-DATA-003`: "graph resume is outside v1" -- refuted by `OA-MCR-001` in this same file, by
  `storage/recovery.py`'s own "Why the graph lives in its own table", and by three user documents.
- `OA-FACTOR-002`: "the src/ files allowed to call it (one: panel_factors.py)" -- four files and,
  since `V2-P4-074`, function by function.
- `OA-FACTOR-007`, twice: "the panel holds no index or market price series at all" and "the
  fifteen datasets providers/tushare.py declares are ..." -- `V2-P3-016` added `index_daily`, a
  sixteenth descriptor and an index price series, and `panel_factors.py` had already been
  corrected.
- `OA-PANEL-029`: "`_daily_close_timeline` is the only writer of that column" -- true today, and
  now carrying the issue that made it true.

**What it does not reach**, stated rather than discovered later: a structural claim written
without a quantity ("the files allowed to make the call", which names no count and stays true as
the allowlist moves), a claim about data rather than about the tree ("the only off-nominal
publication in the 633"), and any sentence in a column this file does not read. The family sweep
over the tracked tree is what covers those.

**And one boundary that only a mutation shows.** The anchor is read over the whole sentence, so
a sentence with one past-tense verb left in it is anchored even where a later clause has slipped
back into the present. Putting half of `OA-FACTOR-007`'s retracted wording back -- "the panel
holds no index or market price series at all" -- left the rest reading "declared then were" and
this rule stayed green; restoring the whole sentence turned it red. A rule at clause scope would
be sharper and would also fire on every ordinary sentence that mixes tenses, which is most of
them here.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
LEDGER: Final[Path] = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "features.csv"
COLUMNS: Final[tuple[str, ...]] = ("feature_name", "feature_description", "notes")

_NUMBER: Final[str] = (
    r"(?:no|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|\d{1,3})"
)

SHAPES: Final[dict[str, re.Pattern[str]]] = {
    "outside a version": re.compile(r"\b(?:is|are|stays?|remains?)\s+outside\s+v\d", re.IGNORECASE),
    "an allowlist": re.compile(r"\bfiles?\s+allowed\s+to\s+\w+", re.IGNORECASE),
    "nothing of a kind": re.compile(
        r"\b(?:holds?|has|have|carr(?:y|ies)|is|are)\s+no\b[^.]{0,80}?\bat\s+all\b", re.IGNORECASE
    ),
    "what a module declares": re.compile(
        rf"\bthe\s+{_NUMBER}\s+\w+\s+[\w./]+\.py\s+declares?\s+(?:are|is)\b", re.IGNORECASE
    ),
    "the only one": re.compile(
        r"\b(?:is|are)\s+the\s+only\s+\w+"
        r"|\bthe\s+only\s+\w+\s+(?:in\s+src/|that\s+(?:calls?|reads?|takes?|builds?))",
        re.IGNORECASE,
    ),
}
"""The shapes a universal or exclusive assertion about this repository is written in here."""

QUANTIFIED: Final[re.Pattern[str]] = re.compile(
    rf"\bonly\b|\bat\s+all\b|\boutside\s+v\d|\bevery\b|\(\s*{_NUMBER}\s*:|\b{_NUMBER}\s+\w+\s+"
    rf"[\w./]+\.py\b",
    re.IGNORECASE,
)
"""A quantity or an enumeration inside the claim.

Without one, a structural sentence is a pointer rather than a count -- `OA-PANEL-026`'s "pins the
src/ files allowed to make the call" says who decides and not how many, and it stays true as the
allowlist moves. `OA-FACTOR-002`'s "(one: panel_factors.py)" is the same sentence with a count
bolted on, and that is the half that went stale.
"""

SUBJECT: Final[re.Pattern[str]] = re.compile(r"[\w/]+\.py\b|\bsrc/|\bv\d\b", re.IGNORECASE)
"""The claim has to be about this repository -- a module, a source path, or a version's scope.

A reading about the market ("the only off-nominal publication in the 633") is a measurement of
data, and its truth does not move when the tree does.
"""

ANCHOR: Final[re.Pattern[str]] = re.compile(
    r"V2-P\d+-\d+"
    r"|\b\d{4}-\d{2}-\d{2}"
    r"|\bas[ _]of\b"
    r"|\b(?:was|were|had|used to|until|before|since|no longer|retracted|stopped|previously)\b",
    re.IGNORECASE,
)
"""What turns a claim about today into a record of a day: an issue, a date, or a past tense.

The date has no closing boundary on purpose. This file writes its instants as
`2026-01-12T04:00Z`, and a `\\b` after the day would refuse to see the `T` -- which is how the
self-test below first went red on a reading that was anchored.
"""


def _sentences(text: str) -> list[str]:
    """A cell read by sentence. A `notes` cell is one CSV line and up to 25,714 characters, so
    reading it by line reads nothing; reading it by sentence is what found `OA-FACTOR-004`'s
    seventh copy of a retracted claim, and what this rule is applied at."""
    flat = " ".join(text.split())
    return [part for part in re.split(r"(?<=[.!?;])\s+", flat) if part.strip()]


def _rows() -> list[dict[str, str]]:
    csv.field_size_limit(10_000_000)
    with LEDGER.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _unanchored(rows: list[dict[str, str]]) -> list[str]:
    found: list[str] = []
    for row in rows:
        for column in COLUMNS:
            for sentence in _sentences(row.get(column) or ""):
                if not SUBJECT.search(sentence) or not QUANTIFIED.search(sentence):
                    continue
                if ANCHOR.search(sentence):
                    continue
                for shape, pattern in SHAPES.items():
                    if pattern.search(sentence):
                        found.append(f"{row['feature_id']}.{column} [{shape}]: {sentence[:200]}")
                        break
    return found


def test_every_structural_claim_in_the_ledger_carries_its_own_time() -> None:
    """The audit. A universal or exclusive claim about this tree must say when it was true."""
    unanchored = _unanchored(_rows())

    assert unanchored == [], (
        "\n".join(unanchored)
        + "\nEach of these asserts something universal or exclusive about this repository, with a "
        "count in it and no time on it. Either it is still true -- give it the issue or the date "
        "that made it so -- or it is a record, and the sentence belongs in the past tense."
    )


def test_the_audit_fires_on_each_shape_it_claims_to_read() -> None:
    """Not vacuous: each shape, written the way the ledger wrote it, has to be reported.

    The four sentences below are the ones this rule was built red against, with their anchors
    removed. A regex that stopped matching them would leave this file green over the same defect
    twice.
    """
    samples = {
        "outside a version": "Record persistence only; graph resume is outside v1.",
        "an allowlist": (
            "tests/unit/panel/test_visible_read_callers.py pins the src/ files allowed to call "
            "it (one: panel_factors.py) in the shape test_query_callers.py established."
        ),
        "nothing of a kind": (
            "The panel holds no index or market price series at all: the fifteen datasets "
            "providers/tushare.py declares are prices and valuations."
        ),
        "the only one": (
            "All three declare ClockStrategy.daily_close so providers/tushare.py's "
            "_daily_close_timeline is the only writer of that column."
        ),
    }
    missed = [
        shape
        for shape, sentence in samples.items()
        if not _unanchored([{"feature_id": "SAMPLE", "notes": sentence}])
    ]

    assert missed == [], f"these shapes would no longer be reported: {missed}"


def test_a_reading_with_its_own_time_on_it_is_left_alone() -> None:
    """The other half: the rule must not redden the readings this file is mostly made of.

    A guard that reddened "measured on the fixture panel at as_of 2026-01-12T04:00Z" would be
    turned off rather than obeyed, which is the argument this module's docstring opens with.
    """
    readings = (
        "Measured on the generated fixture panel at as_of 2026-01-12T04:00Z, daily_basic is the "
        "only dataset in src/ whose rows share one instant.",
        "V2-P4-026 moved it, so panel_ingest.py holds no whole-partition price read at all.",
        "Before this task api/app.py had no such route at all.",
    )
    reported = [
        reading for reading in readings if _unanchored([{"feature_id": "SAMPLE", "notes": reading}])
    ]

    assert reported == [], f"a dated reading was reported as a claim about today: {reported}"
