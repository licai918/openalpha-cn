"""One shared `sqlite3.connect()` wrapper for every store touching `state.sqlite3` (task 21).

Why this exists: `PRAGMA foreign_keys` is per-*connection*, not per-database, and defaults to
off (SQLite's own default, kept for backward compatibility with schemas written before the
pragma existed). Ten stores in this package each open their own connection to the same
`state.sqlite3` file (`storage/sqlite.py`, `models.py`, `memory.py`, `portfolio.py`,
`product.py` (two classes), `recovery.py`, `batch.py`, `validation.py`, `jobs.py`). Nine of
them already existed when this module was added; exactly one (`SQLiteRunRepository`,
`storage/sqlite.py`) turned the pragma on in its own `_connect()`, and the other eight did
not -- not a deliberate choice, just eight copies of `sqlite3.connect(self.path, timeout=10)`
that never had the line added, each of them free to drift further from the others every time
one file's `_connect()` was touched and the rest were not. (`jobs.py`'s store was written
after this module existed and has called `open_state_connection()` since its first commit, so
it was never one of the eight.) `checkpoints.run_id -> runs.run_id` and `decisions.run_id ->
runs.run_id` are the two foreign keys this schema actually declares (`storage/sqlite.py`);
both are written only through `SQLiteRunRepository`'s own connection, which already enforced
them -- so this fix changes no runtime behavior for those two writes. What it buys is the
property "every connection this package opens against `state.sqlite3` enforces the
constraints the schema declares," instead of "enforcement happens to hold today because only
one store's table happens to have a foreign key and that one store happens to remember the
pragma" -- a property that silently stops being true the moment a future table adds a foreign
key of its own and its store's author copies one of the eight `_connect()` methods that never
had it.

One function, not a class: each caller already owns its own `sqlite3.Connection` lifecycle
(`contextlib.closing`, its own `timeout=10`, and -- for the eight that open a connection while
constructing -- its own WAL/journal-mode setup immediately after opening). This only needed to
stop being copy-pasted, not to grow a new abstraction layer around connection management.

"Each" and not "every": `SQLiteValidationStore` sets `journal_mode` nowhere in its file. It
opens no connection while constructing and none of its methods asks for WAL, so it inherits
whatever mode another store already put on the shared `state.sqlite3` -- WAL is a sticky
file-level property, not a per-connection one, which is why nothing has ever failed over it.
Measured 2026-09-10: `grep -c "journal_mode\|WAL" storage/validation.py` returns 0.
"""

import sqlite3
from pathlib import Path


def open_state_connection(path: Path, *, timeout: float = 10) -> sqlite3.Connection:
    """Open a `sqlite3.Connection` to `path` with foreign-key enforcement turned on.

    Every store's `_connect()` in this package calls this instead of `sqlite3.connect()`
    directly, so `PRAGMA foreign_keys = ON` is set exactly once, in one place, on every
    connection this package opens against `state.sqlite3` -- not ten separate copies that
    can independently drift (see the module docstring). `timeout` defaults to the same 10
    seconds every pre-existing `_connect()` already used, matching current behavior exactly
    for callers that do not override it.
    """
    connection = sqlite3.connect(path, timeout=timeout)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
