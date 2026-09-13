"""Content-addressed document storage for one shortlist run's whole answer (`V2-P4-071`/`062`).

`V2-P4-062`, as the third product acceptance measured it: `runtime/` held no shortlist or ranking
artifact, the only route was `POST /api/v1/shortlists/run`, there was no `GET`, and there was no
`openalpha shortlist get`. The answer already carried three content addresses -- `gate_manifest_id`,
`ranking_manifest_id`, `ranking_content_digest` -- *with nothing to address*. Two runs of one
command produced byte-identical addresses, so the identities were sound; a caller who wanted to
keep an answer had to redirect `--json` into a file and invent their own filing system.

## Why the key is the answer's own digest and not one of the three it already had

Each of the three was tried against the question "which one names *this answer*", and each fails a
case that is reachable on the fixtures this repository already has.

- **`ranking_manifest_id`** addresses the *question*: `as_of`, horizon, universe, scoring policy,
  code commit and config digest. Two runs of one question under two different gate bars share it
  and are two different verdicts.
- **`gate_manifest_id`** addresses the question *and* the bars, which is closer and still not it:
  the **evidence** a caller supplies is in neither address, so the same shortlist run with and
  without a supplied `SignalFrame` shares one `gate_manifest_id` and produces a different
  `admitted` list, a different `unresearched` and a different `researched_ratio`.
- **`ranking_content_digest`** addresses `(subject, rank, score, signal_id, run_manifest_id)` per
  **candidate** -- the researched names only. A first run with no evidence at all has zero
  candidates, so two entirely different shortlists over two different cross sections share that
  digest, which is exactly what `tests/integration/test_shortlist_workflow.py` measured on day one
  against day two before this store existed.

So the store is keyed by `shortlist_view`'s own `shortlist_id`: `stable_answer_digest` over the
whole rendered answer, less the one key derived from a wall clock. That makes this a **pure**
content-addressed store, and the write-once question a caller normally has to answer -- "may a
second answer arrive under this key?" -- does not arise: two answers that differ have two
addresses. What remains is the `unchanged` case, and it is kept for `FileExperimentStore`'s
reason: a second run reproducing an answer must not rewrite a document a reader already holds.

## Why a directory of files rather than a `state.sqlite3` table

`storage/factor_experiments.py`'s three reasons, and all three transfer unchanged: the document is
already content-addressed, so a row would add a second identity and a schema that would later need
a migration of opaque JSON payloads; `ParquetEvidenceStore` and `FileExperimentStore` are the two
precedents in this package for a store that is a directory of documents opaque to it; and a
shortlist answer is one document of a few kilobytes written once per run, which is the opposite of
the row-at-a-time access the `state.sqlite3` tables exist for.

The filename is the key plus the suffix and carries no second component, which is the one place
this differs from `FileExperimentStore`. That store names files `<experiment_id>.<content_digest>`
precisely so a parse-free refusal can name **both** digests when one identity holds two answers.
Here the identity *is* the digest, so a second component would be a copy of the first.

`SHORTLIST_ID_PATTERN` holds the key to exactly `stable_answer_digest`'s output and is matched with
`re.fullmatch`, never `re.match` with a trailing `$`: the token becomes a filename component, and
`domain/panel_batch.py` records that bug being measured one plane down, where `$` also matches
before a final newline and `"close\\n"` was accepted as a column name and written into Parquet.
Checking rather than sanitising is deliberate for `FileExperimentStore`'s reason -- sanitising
turns a wrong key into a plausible one, and a store keyed by an identifier that reached the
filesystem unchecked is a path traversal with a content address in front of it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Literal

__all__ = [
    "SHORTLIST_DOCUMENT_SUFFIX",
    "SHORTLIST_ID_PATTERN",
    "FileShortlistStore",
    "ShortlistStoreError",
]

SHORTLIST_DOCUMENT_SUFFIX: Final[str] = ".json"

SHORTLIST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"sla_[0-9a-f]{24}")
"""Exactly what `shortlist_view.stable_answer_digest` produces, and nothing else.

Unanchored and `fullmatch`ed; see this module's docstring for the measured reason that is not a
style preference. `sla` is "shortlist answer", and the 24-hex tail is this repository's one
identity shape -- `domain/_identity.py` takes the first 24 characters of a sha256 hex digest and
prefixes them, and every content address in the tree is that shape.
"""


class ShortlistStoreError(RuntimeError):
    """Raised for an unusable shortlist key, or for a document that will not round trip.

    A `RuntimeError` rather than a subclass of `shortlist_view`'s own `ShortlistViewError`, which
    is `ExperimentStoreError`'s arrangement and for its reason: this layer may not import that one
    (`storage-no-upward-deps` forbids `openalpha_cn.storage` every module above it, on full
    transitive reachability), and `shortlist_view` is where the two vocabularies meet.
    """


class FileShortlistStore:
    """A directory of shortlist answers, each keyed by its own content address.

    Satisfies `shortlist_view.ShortlistDocumentStore` structurally -- there is no import in either
    direction, which is what keeps `openalpha_cn.storage` free of an edge into
    `openalpha_cn.shortlist_view` and, through it, into `openalpha_cn.backtest`.

    Every method is total on a directory that does not exist yet: `list_ids` answers `()` and `get`
    answers `None`, because a runtime directory with no shortlist in it is the ordinary state of a
    fresh install rather than a fault.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def put(self, *, shortlist_id: str, payload: str) -> Literal["created", "unchanged"]:
        """Hold `payload` under `shortlist_id`, keeping the held bytes if something is there.

        `unchanged` **keeps what is held** rather than overwriting it, which is
        `FileExperimentStore.put`'s rule and its reason arriving here through a different door.
        There the two payloads could differ in `built_at`; here they can differ in
        `measurement.ranking_age_days`, which is `built_at - as_of` and is the one rendered value
        this store's key deliberately does not address -- see
        `shortlist_view.SHORTLIST_ANSWER_UNADDRESSED_KEYS`. So a re-run of yesterday's shortlist
        today produces a document that is byte-different and answer-identical, and keeping the
        first means a reader who already fetched it is not handed a second copy with a different
        number in it.

        There is no "refused" outcome and there is nothing missing: the key is the digest of the
        answer, so an arriving document that disagreed with a held one would have a different key.
        That is the whole of what content addressing buys, and it is why this signature carries no
        `content_digest` beside the id the way the experiment store's does.
        """
        _refuse_an_unusable_id(shortlist_id)
        document = self._document(shortlist_id)
        if document.is_file():
            return "unchanged"
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = document.with_name(f"{document.name}.partial")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(document)
        return "created"

    def get(self, shortlist_id: str) -> str | None:
        """The held payload for `shortlist_id`, or `None` when nothing is held under it."""
        _refuse_an_unusable_id(shortlist_id)
        document = self._document(shortlist_id)
        return document.read_text(encoding="utf-8") if document.is_file() else None

    def list_ids(self) -> tuple[str, ...]:
        """Every held `shortlist_id`, ascending.

        Reads the directory rather than an index, and skips a name that is not a well-formed
        content address -- including the `.partial` file a crashed write would leave behind. A
        store that returned one of those would hand a caller a key `get` then refuses.
        """
        if not self.path.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name[: -len(SHORTLIST_DOCUMENT_SUFFIX)]
                for entry in self.path.iterdir()
                if _is_a_document(entry)
            )
        )

    def _document(self, shortlist_id: str) -> Path:
        return self.path / f"{shortlist_id}{SHORTLIST_DOCUMENT_SUFFIX}"


def _is_a_document(entry: Path) -> bool:
    parts = entry.name.split(".")
    return (
        entry.is_file()
        and len(parts) == 2
        and f".{parts[1]}" == SHORTLIST_DOCUMENT_SUFFIX
        and SHORTLIST_ID_PATTERN.fullmatch(parts[0]) is not None
    )


def _refuse_an_unusable_id(shortlist_id: str) -> None:
    """Refuse a key that is not a content address of this store's own shape.

    The path safety, and it is a *shape* check rather than an escaping one for the reason
    `SHORTLIST_ID_PATTERN` states: `sla_` plus 24 lowercase hex characters contains no separator,
    no `.` and nothing a filesystem treats specially, so a key that matches cannot name anything
    outside this directory. Sanitising instead would turn `../../etc/passwd` into a plausible key
    and store a document under it.
    """
    if not SHORTLIST_ID_PATTERN.fullmatch(shortlist_id):
        raise ShortlistStoreError(
            f"{shortlist_id!r} is not a shortlist_id; this store is keyed by the answer's own "
            "content address (`sla_` and 24 lowercase hex characters), and a key that did not "
            "come from there is a filename rather than a content address"
        )
