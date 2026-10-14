"""Write-once document storage for the paper book's daily state snapshots (`V2-P7-017`).

`backtest/paper_state.py` defines the snapshot -- the book, each holding's entry adjustment
factor, the live orders with their attempt counts, the cool-downs and the session the book is
settled through -- renders it as canonical JSON, and on reading parses it back and re-derives its
content address. This module only keeps the bytes. It may not import that one
(`storage-no-upward-deps` forbids `openalpha_cn.storage` every module above it, on full transitive
reachability) and the backtest module may not import this one (`backtest-studies-touch-no-store`),
so the two meet structurally, as `FileExperimentStore` and `factor_view` do: a `Protocol` beside
the consumer, satisfied here with no import in either direction, and the composition root is the
one place that hands this store to anything.

## One document per session, written once

A file is named `<session date>.<snapshot_id>.json`. The date is the key a caller asks by -- the
next session's settlement reads the snapshot of the one before it -- and `snapshot_id` is the
snapshot's own content address, so a store that parses nothing can still tell "the same snapshot
again" from "a second answer for a settled session" by comparing two names, which is
`FileExperimentStore`'s `<experiment_id>.<content_digest>` arrangement for the same reason.

- The same `snapshot_id` again is `unchanged` and **writes nothing**: the file, its inode and its
  modification time stay as they were, so a second run of the same evening is a no-op on disk.
- A different `snapshot_id` for a session already held is **refused**, naming both: a session
  settles once, and a second answer for it is a run that did not reproduce, not an update.

## Atomic, and what that does and does not cover

The bytes go to `<document>.partial`, are flushed and `fsync`ed, and reach the document's name in
one `os.replace` -- a rename, atomic on one filesystem. A write that dies before the rename leaves
only the `.partial`, which no listing and no `get` reads, so a reader finds either the whole
snapshot or none. A later write of the same session truncates the leftover and replaces it.

What this store does **not** do is notice a document edited in place or damaged after the fact:
it hashes nothing and parses nothing. `backtest/paper_state.open_snapshot` is where that is
caught -- it re-derives the address from the content and refuses a mismatch by name.

Both filename components are checked rather than escaped: the date is a `date` rendered by
`isoformat`, and the id must be exactly `stable_model_id(prefix="pbs", ...)`'s shape, so nothing a
caller passes can name a path outside the directory.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Final, Literal

__all__ = [
    "SNAPSHOT_DOCUMENT_SUFFIX",
    "SNAPSHOT_ID_PATTERN",
    "FilePaperStateStore",
    "PaperStateStoreError",
]

SNAPSHOT_DOCUMENT_SUFFIX: Final[str] = ".json"

SNAPSHOT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"pbs_[0-9a-f]{24}")
"""Exactly what `stable_model_id(prefix="pbs", ...)` produces, and nothing else.

Unanchored and matched with `re.fullmatch`, never `re.match` with a trailing `$`, for the reason
`storage/factor_experiments.py`'s `EXPERIMENT_ID_PATTERN` records: `$` also matches before a
final newline, and this token becomes a filename component.
"""


class PaperStateStoreError(RuntimeError):
    """Raised for an unusable snapshot key, or for a second snapshot under a settled session.

    A `RuntimeError` for `ExperimentStoreError`'s reason: this layer may not import the backtest
    module's own error, and a `ValueError` would put a store refusal inside every
    `except ValueError` a caller wrote around a contract.
    """


class FilePaperStateStore:
    """A directory of paper-book snapshots, one per settled session.

    Satisfies `backtest.paper_state.PaperStateDocumentStore` structurally. Every method is total
    on a directory that does not exist yet: `sessions` answers `()` and `get` answers `None`,
    because a runtime directory with no paper book in it is the state of a fresh install. What a
    caller does with that answer -- refuse it, rather than start a book from zero -- is
    `backtest.paper_state`'s.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def put(
        self, *, session: date, snapshot_id: str, payload: str
    ) -> Literal["created", "unchanged"]:
        """Hold `payload` as the snapshot of `session`, writing nothing if it is already held.

        A different `snapshot_id` for a held session is refused, naming both, and the held file
        is left as it was.
        """
        _refuse_an_unusable_id(snapshot_id)
        held = self._held(session)
        if held is not None:
            held_id = held.name.split(".")[1]
            if held_id == snapshot_id:
                return "unchanged"
            raise PaperStateStoreError(
                f"{session.isoformat()} is already held as {held_id} and this snapshot is "
                f"{snapshot_id}; a session's snapshot is written once"
            )
        self.path.mkdir(parents=True, exist_ok=True)
        document = self._document(session, snapshot_id)
        temporary = document.with_name(f"{document.name}.partial")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, document)
        return "created"

    def get(self, session: date) -> tuple[str, str] | None:
        """`(snapshot_id, payload)` held for `session`, or `None` when nothing is."""
        held = self._held(session)
        if held is None:
            return None
        return held.name.split(".")[1], held.read_text(encoding="utf-8")

    def sessions(self) -> tuple[date, ...]:
        """Every session a snapshot is held for, ascending.

        Reads the directory rather than an index, and skips a name that is not a well-formed
        snapshot document -- including the `.partial` a crashed write leaves behind.
        """
        return tuple(sorted({_session_of(document) for document in self._documents()}))

    def _documents(self) -> tuple[Path, ...]:
        if not self.path.is_dir():
            return ()
        return tuple(entry for entry in sorted(self.path.iterdir()) if _is_a_document(entry))

    def _held(self, session: date) -> Path | None:
        """The one document held for `session`, or `None`.

        Refuses two, which `put` cannot produce: answering with whichever sorted first would make
        a reader's book depend on a filename.
        """
        found = [document for document in self._documents() if _session_of(document) == session]
        if len(found) > 1:
            ids = sorted(document.name.split(".")[1] for document in found)
            raise PaperStateStoreError(
                f"{session.isoformat()} is held under {len(found)} snapshots ({ids}); that "
                "contradiction predates any arrival and is reported against the directory"
            )
        return found[0] if found else None

    def _document(self, session: date, snapshot_id: str) -> Path:
        return self.path / f"{session.isoformat()}.{snapshot_id}{SNAPSHOT_DOCUMENT_SUFFIX}"


def _session_of(document: Path) -> date:
    return date.fromisoformat(document.name.split(".")[0])


def _is_a_document(entry: Path) -> bool:
    parts = entry.name.split(".")
    if not (
        entry.is_file()
        and len(parts) == 3
        and f".{parts[2]}" == SNAPSHOT_DOCUMENT_SUFFIX
        and SNAPSHOT_ID_PATTERN.fullmatch(parts[1]) is not None
    ):
        return False
    try:
        return date.fromisoformat(parts[0]).isoformat() == parts[0]
    except ValueError:
        return False


def _refuse_an_unusable_id(snapshot_id: str) -> None:
    """Refuse a key that is not `stable_model_id(prefix="pbs", ...)`'s own output."""
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise PaperStateStoreError(
            f"{snapshot_id!r} is not a snapshot_id; this store is keyed by the snapshot's own "
            "content address (`pbs_` and 24 lowercase hex characters), and a key that did not "
            "come from there is a filename rather than a content address"
        )
