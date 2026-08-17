"""Write-once document storage for sealed factor experiment artifacts (`V2-P3-015`).

`V2-P3-014` built the artifact, sealed it, and stored nothing -- deliberately, and it named the
obstacle rather than the type: *"`backtest/` may not import `storage/`, and `V2-P3-015` is where a
public face for a factor lives."* This module is the other side of that sentence.

## What it holds, and why the type is a string

`experiment_payload(record)` renders a `FactorExperimentRecord` as canonical JSON on
`stable_model_id`'s own canonicalisation, and `open_experiment(payload)` refuses a payload whose
content no longer hashes to the seal it carries. So the unit is a **document**, the integrity
check is the document's own, and a store that held the parsed record would be a second place that
knows how to build one.

It would also be illegal. `storage-no-upward-deps` forbids `openalpha_cn.storage` from importing
`openalpha_cn.backtest`, `runtime`, `product` or `agents`, with **full transitive reachability**
and no `ignore_imports` -- `tests/unit/test_import_layering.py` measures it on the live graph with
`grimp` as well. A signature of `(experiment_id, content_digest, built_at, payload)` is what makes
that contract survive contact with this feature instead of being widened for it.

## What "write-once" means here, and how much of it a byte store can enforce

All of it, which is the part worth stating. `backtest/factor_experiment.py`'s
`refuse_a_restated_experiment` is a comparison of **two digests**:

- an arriving record whose `experiment_id` matches a held one and whose `content_digest` differs
  is refused, naming both -- because the same four specs over the same builds at the same commit
  have one answer, so a second one is a build that did not reproduce rather than a second
  experiment;
- an arriving record identical to a held one is a **no-op and is accepted**, because an identity
  that moves for nothing makes a rebuild unwritable and its predecessor unreproducible.

Neither direction needs the artifact opened, and `put` enforces both on the two identifiers it is
handed. `FactorExperimentError` is deliberately *not* what it raises -- that class lives above
this layer -- so it raises `ExperimentStoreError`, and `factor_view` is where the two vocabularies
meet.

**The comparison is on `content_digest` and emphatically not on the payload bytes**, and that is
the measured half of this design rather than a shortcut. `FactorExperimentRecord.built_at` is a
field of the document and is outside every digest, exactly so that recomputing one experiment at
two wall clocks reproduces one identity. A store that compared bytes would therefore refuse the
second run of an unchanged experiment -- which is `FactorInputRef`'s defect, the one that had to
be given back: *"an identity that moves for nothing makes a rebuild unwritable and its predecessor
unreproducible."* So the digest is the key half and the bytes are the payload; the filename
carries both (`<experiment_id>.<content_digest>.json`), which is what lets a parse-free store name
**both** digests in its refusal.

**What it cannot do** is notice a payload whose seal was recomputed beside an edit: this store
hashes nothing and parses nothing. That is
`factor_view.KNOWN_FACTOR_RUN_LIMITATIONS.the_document_store_holds_bytes_and_re_derives_no_number`,
and it is `the_seal_detects_an_edit_and_does_not_authenticate_one` arriving one plane out. The
integrity property delivered is against a partial write and against a clobber, never against an
author.

## Why a directory of files rather than a `state.sqlite3` table

Three reasons, and the first is the one that decides it:

1. **The document is already content-addressed and already sealed.** A SQLite row would add a
   second identity (`sequence`) and a schema; the schema is what would then need a migration the
   day `factor-experiment-record/v2` exists, and a migration of opaque JSON payloads is the exact
   thing `storage/migrations.py`'s own docstring says it was built to make possible and would
   rather not have to do. A file named by the two content addresses needs neither.
2. **`ParquetEvidenceStore` is the precedent** for a store in this package that is a directory
   rather than a table, and it is the one whose contents are also opaque-to-the-store documents.
3. **An artifact is large and rare.** A three-tier report over a year is one document of tens of
   kilobytes written once per experiment, which is the opposite of the row-at-a-time access the
   `state.sqlite3` tables exist for.

Both filename components are `stable_model_id`'s output -- a four-character prefix and 24
lowercase hex characters -- and `_refuse_an_unusable_id` holds each to exactly that shape, so
nothing a caller passes can name a path outside the directory: a store keyed by an identifier that
reached the filesystem unchecked is a path traversal with a content address in front of it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

__all__ = [
    "CONTENT_DIGEST_PATTERN",
    "EXPERIMENT_DOCUMENT_SUFFIX",
    "EXPERIMENT_ID_PATTERN",
    "ExperimentStoreError",
    "FileExperimentStore",
]

EXPERIMENT_DOCUMENT_SUFFIX: Final[str] = ".json"

EXPERIMENT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^fxp_[0-9a-f]{24}$")
"""Exactly what `stable_model_id(prefix="fxp", ...)` produces, and nothing else.

Held rather than assumed because this identifier becomes part of a filename.
`domain/_identity.py` takes the first 24 characters of a sha256 hex digest and prefixes them, so
the shape is closed and a token that does not match it did not come from the function this store's
whole key space is defined by. Checking rather than sanitising is deliberate: sanitising turns a
wrong key into a plausible one, and this store's only job is to keep two answers under one key
apart.
"""

CONTENT_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^fxc_[0-9a-f]{24}$")
"""`stable_model_id(prefix="fxc", ...)`'s output: the artifact's own content address."""


class ExperimentStoreError(RuntimeError):
    """Raised for an unusable experiment key, or for a second answer under a held one.

    A `RuntimeError` rather than a `ValueError` subclass of `backtest/`'s own
    `FactorExperimentError`: this layer may not import that one, and inheriting from `ValueError`
    would put a store refusal inside every `except ValueError` a caller wrote around a *contract*.
    `factor_view` is where this vocabulary is translated into the face's.
    """


class FileExperimentStore:
    """A directory of sealed experiment documents, keyed by `experiment_id`.

    Satisfies `factor_view.ExperimentDocumentStore` structurally -- there is no import in either
    direction, which is what keeps `openalpha_cn.storage` free of an edge into
    `openalpha_cn.backtest`.

    Every method is total on a directory that does not exist yet: `list_ids` answers `()` and
    `get` answers `None`, because a runtime directory with no experiments in it is the ordinary
    state of a fresh install rather than a fault.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def put(
        self, *, experiment_id: str, content_digest: str, built_at: datetime, payload: str
    ) -> Literal["created", "unchanged"]:
        """Hold `payload` under `experiment_id`, refusing a second answer under a held key.

        `built_at` is accepted and deliberately **not** stored beside the document: it is already
        a field of the record inside the payload, and a copy outside it would be a second source
        of truth for a value that is out of every digest. It is in the signature because a caller
        should not have to read this implementation to discover which of the record's fields this
        store looks at -- the answer is "none of them", and the parameter list is where that is
        visible.

        `unchanged` **keeps the held bytes** rather than overwriting them with the arriving ones.
        The two agree on `content_digest` and can still differ in `built_at`, which is outside
        every digest precisely so that a recomputation reproduces one identity; keeping the first
        means the wall clock on file is the one the artifact was first assembled at, and means a
        second run cannot quietly rewrite a document a reader already holds.
        """
        _refuse_an_unusable_id(experiment_id)
        _refuse_an_unusable_digest(content_digest)
        held = self._held(experiment_id)
        if held is not None:
            if held.name.split(".")[1] == content_digest:
                return "unchanged"
            raise ExperimentStoreError(
                f"{experiment_id} is already held at content {held.name.split('.')[1]} and this "
                f"record carries {content_digest}; the same four specs over the same builds at "
                "the same commit have one answer, so a second one is a build that did not "
                "reproduce and not a second experiment. Change a declared field -- which mints a "
                "new experiment_id -- or find out why the numbers moved"
            )
        self.path.mkdir(parents=True, exist_ok=True)
        document = self._document(experiment_id, content_digest)
        temporary = document.with_name(f"{document.name}.partial")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(document)
        return "created"

    def get(self, experiment_id: str) -> str | None:
        """The held payload for `experiment_id`, or `None` when nothing is held under it."""
        _refuse_an_unusable_id(experiment_id)
        held = self._held(experiment_id)
        return None if held is None else held.read_text(encoding="utf-8")

    def list_ids(self) -> tuple[str, ...]:
        """Every held `experiment_id`, ascending.

        Reads the directory rather than an index, and skips a name that is not a well-formed pair
        of content addresses -- including the `.partial` file a crashed write would leave behind.
        A store that returned one of those would hand a caller a key `get` then refuses.
        """
        return tuple(sorted({document.name.split(".")[0] for document in self._documents()}))

    def _documents(self) -> tuple[Path, ...]:
        if not self.path.is_dir():
            return ()
        return tuple(entry for entry in sorted(self.path.iterdir()) if _is_a_document(entry))

    def _held(self, experiment_id: str) -> Path | None:
        """The one document held under `experiment_id`, or `None`.

        Refuses two, which `put` cannot produce: a directory holding one `experiment_id` under two
        `content_digest`s is the contradiction `refuse_a_restated_experiment` reports against a
        *collection* rather than against an arrival, and answering with whichever sorted first
        would make a reader's answer depend on a filename.
        """
        found = [
            document
            for document in self._documents()
            if document.name.split(".")[0] == experiment_id
        ]
        if len(found) > 1:
            digests = sorted(document.name.split(".")[1] for document in found)
            raise ExperimentStoreError(
                f"{experiment_id} is held under {len(found)} content digests ({digests}); that "
                "contradiction predates any arrival and is reported against the directory rather "
                "than against a newcomer"
            )
        return found[0] if found else None

    def _document(self, experiment_id: str, content_digest: str) -> Path:
        return self.path / f"{experiment_id}.{content_digest}{EXPERIMENT_DOCUMENT_SUFFIX}"


def _is_a_document(entry: Path) -> bool:
    parts = entry.name.split(".")
    return (
        entry.is_file()
        and len(parts) == 3
        and f".{parts[2]}" == EXPERIMENT_DOCUMENT_SUFFIX
        and EXPERIMENT_ID_PATTERN.match(parts[0]) is not None
        and CONTENT_DIGEST_PATTERN.match(parts[1]) is not None
    )


def _refuse_an_unusable_id(experiment_id: str) -> None:
    """Refuse a key that is not `stable_model_id`'s own output.

    Half of the path safety, and it is a *shape* check rather than an escaping one for the reason
    `EXPERIMENT_ID_PATTERN` states: `fxp_` plus 24 lowercase hex characters contains no separator,
    no `.` and nothing a filesystem treats specially, so a key that matches cannot name anything
    outside this directory. Sanitising instead would turn `../../etc/passwd` into a plausible key
    and store a document under it.
    """
    if not EXPERIMENT_ID_PATTERN.match(experiment_id):
        raise ExperimentStoreError(
            f"{experiment_id!r} is not an experiment_id; this store is keyed by "
            "stable_model_id's own output (`fxp_` and 24 lowercase hex characters), and a key "
            "that did not come from there is a filename rather than a content address"
        )


def _refuse_an_unusable_digest(content_digest: str) -> None:
    """Refuse a content address that is not `stable_model_id`'s own output.

    The other half, and it is not decoration: the digest is the second filename component, so it
    reaches the filesystem exactly as the key does -- and it is also what a refusal compares, so a
    store that accepted an empty one could never tell a re-derivation that reproduced from one
    that did not.
    """
    if not CONTENT_DIGEST_PATTERN.match(content_digest):
        raise ExperimentStoreError(
            f"{content_digest!r} is not a content_digest; an artifact's content address is "
            "`fxc_` and 24 lowercase hex characters, and it is what tells a re-derivation that "
            "reproduced from one that did not"
        )
