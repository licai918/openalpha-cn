"""A directory of model exchanges, one write-once document per request content address
(`V2-P7-012`).

The contract lives in `models/recording.py` (`ModelExchange`, `model-exchange/v1`) and says
what a stored exchange is; this is the store, and it is responsible for the three things the
contract cannot do for itself: turning the address into a filename, writing it atomically,
and refusing to hand back a document whose content no longer matches the addresses it claims.
That last check is the one that makes "content-addressed" a property rather than a naming
convention: `get` re-derives the request digest from `{"url", "request", "attempt"}` and the
response
digest from the decoded `response`, and refuses a document edited outside this store with
`ModelExchangeIntegrityError` naming which address moved.

## Why a directory rather than a `state.sqlite3` table

`storage/factor_experiments.py`'s three reasons transfer unchanged, and they transfer because
the document is already content-addressed: a row would add a second identity and a schema, and
the schema is what would need a migration of opaque JSON the day `model-exchange/v2` exists.
Documents are large-ish, written once per distinct request, and read by a replay that already
knows the address -- the opposite of the row-at-a-time access the SQLite tables exist for.

## Write-once, and what a byte store can actually enforce

A second `put` under a held `request_digest` with the same `response_digest` returns
`"unchanged"` and keeps the held bytes (so the recorded wall clock stays the one the exchange
was first recorded at). A second `put` with a *different* `response_digest` raises
`ModelExchangeConflictError`: one request has one recorded answer, and silently replacing it
would make a replay depend on when it ran -- the drift review focus 3 names.

The write itself is a temporary file plus `os.replace` (`Path.replace`), the same hand-off
`FilePredictionStore` uses: a reader never sees a half-written document, and a crash between
the two steps leaves only a `.partial` file that this store's name check skips. The write-once
guarantee is sequential, not a lock: two concurrent first-writers of the same address are not
serialised here -- the second `os.replace` would win -- and that is stated rather than implied,
because no caller in this batch writes from two processes at once.
"""

import re
from pathlib import Path
from typing import Final, Literal

from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.models.recording import (
    MODEL_EXCHANGE_VERSIONS,
    ModelExchange,
    exchange_request_digest,
    exchange_response_digest,
)

DOCUMENT_SUFFIX: Final[str] = ".json"
"""The one suffix this store writes; a `.partial` file left by a crash does not end in it."""

_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


class ModelExchangeIntegrityError(ValueError):
    """Raised when a held document no longer matches the addresses it claims."""


class ModelExchangeConflictError(ValueError):
    """Raised when one request address arrives with a second, different response."""


class FileModelExchangeStore:
    """Hold `ModelExchange` documents under `<root>/<request_digest>.json`, write-once."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, request_digest: str) -> ModelExchange | None:
        """The exchange held under `request_digest`, or `None` when nothing is held.

        Reads through `read_versioned`, so a document whose `schema_version` this build does
        not know is refused by name (`UnknownSchemaVersionError`) rather than misread; then
        re-derives both content addresses and refuses a document that has been edited.
        """
        document = self._document(request_digest)
        if not document.is_file():
            return None
        exchange = read_versioned(MODEL_EXCHANGE_VERSIONS, document.read_text(encoding="utf-8"))
        self._recheck(exchange, requested=request_digest)
        return exchange

    def put(self, exchange: ModelExchange) -> Literal["created", "unchanged"]:
        """Hold `exchange`, or report that the identical answer is already held.

        `unchanged` keeps the held bytes rather than overwriting them with the arriving ones:
        the two agree on the response address and can still differ in `recorded_at`, which is
        deliberately outside every digest, and keeping the first means the wall clock on file
        is the one the exchange was recorded at.
        """
        self._recheck(exchange, requested=exchange.request_digest)
        document = self._document(exchange.request_digest)
        if document.is_file():
            held = self.get(exchange.request_digest)
            if held is None:  # pragma: no cover - `is_file` and `get` disagree only on a race
                raise ModelExchangeIntegrityError(
                    f"{exchange.request_digest} is filed here and could not be read back"
                )
            if held.response_digest != exchange.response_digest:
                raise ModelExchangeConflictError(
                    f"{exchange.request_digest} already holds a response with digest "
                    f"{held.response_digest}, and this exchange carries "
                    f"{exchange.response_digest}. One request has one recorded answer; a "
                    "second, different response under the same request_digest is a replay that "
                    "would depend on when it ran, not a second record"
                )
            return "unchanged"
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = document.with_name(f"{document.name}.partial")
        temporary.write_text(exchange.model_dump_json(), encoding="utf-8")
        temporary.replace(document)
        return "created"

    def _document(self, request_digest: str) -> Path:
        """Refuse an unusable address before it is turned into a path."""
        if _DIGEST_PATTERN.fullmatch(request_digest) is None:
            raise ModelExchangeIntegrityError(
                f"{request_digest!r} is not a sha256 request digest; this store's document "
                "names are content addresses, so an unusable one is refused rather than given "
                "a filename"
            )
        return self.root / f"{request_digest}{DOCUMENT_SUFFIX}"

    @staticmethod
    def _recheck(exchange: ModelExchange, *, requested: str) -> None:
        """Re-derive both digests from the content and name the one that does not match."""
        if exchange.request_digest != requested:
            raise ModelExchangeIntegrityError(
                f"document {requested}: it carries request_digest "
                f"{exchange.request_digest!r}, which is not the address it is filed under"
            )
        request = exchange.request
        if not isinstance(request, dict):
            raise ModelExchangeIntegrityError(
                f"document {requested}: request_digest cannot be re-derived from a request "
                "that is not a JSON object"
            )
        recomputed_request = exchange_request_digest(
            url=exchange.url, payload=request, attempt=exchange.attempt
        )
        if recomputed_request != exchange.request_digest:
            raise ModelExchangeIntegrityError(
                f"document {requested}: request_digest {exchange.request_digest} does not "
                f"address its url and request content ({recomputed_request}); the document was "
                "edited outside this store"
            )
        recomputed_response = exchange_response_digest(response=exchange.response)
        if recomputed_response != exchange.response_digest:
            raise ModelExchangeIntegrityError(
                f"document {requested}: response_digest {exchange.response_digest} does not "
                f"address its response content ({recomputed_response}); the document was "
                "edited outside this store"
            )
