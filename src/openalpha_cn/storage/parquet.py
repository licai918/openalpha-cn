"""Immutable Parquet evidence partitions with DuckDB point-in-time queries."""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

import duckdb

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline, ensure_aware


class StorageIntegrityError(RuntimeError):
    """Raised when persisted provenance does not match reconstructed evidence."""


class ParquetEvidenceStore:
    """Store immutable evidence batches and query only point-in-time-visible rows."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, items: tuple[EvidenceSnapshot, ...]) -> Path:
        """Write one content-addressed Parquet partition for an evidence batch."""
        if not items:
            raise ValueError("cannot append an empty evidence batch")
        evidence_ids = [item.evidence_id for item in items]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence batch contains duplicate IDs")

        batch_hash = sha256("|".join(sorted(evidence_ids)).encode()).hexdigest()[:24]
        target = self.root / f"part-{batch_hash}.parquet"
        if target.exists():
            return target

        temporary = target.with_suffix(".parquet.tmp")
        rows = [self._serialize(item) for item in items]
        with duckdb.connect(":memory:") as connection:
            connection.execute(
                """
                CREATE TABLE evidence (
                    evidence_id VARCHAR,
                    subject VARCHAR,
                    kind VARCHAR,
                    event_time TIMESTAMPTZ,
                    available_time TIMESTAMPTZ,
                    ingested_time TIMESTAMPTZ,
                    revision_time TIMESTAMPTZ,
                    source_id VARCHAR,
                    source_uri VARCHAR,
                    source_license VARCHAR,
                    redistribution VARCHAR,
                    summary VARCHAR,
                    payload_json VARCHAR,
                    content_hash VARCHAR
                )
                """
            )
            connection.executemany(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            connection.execute(
                "COPY evidence TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(temporary)],
            )
        temporary.replace(target)
        return target

    def query(
        self,
        *,
        as_of: datetime,
        subject: str | None = None,
        kind: str | None = None,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Return evidence available by ``as_of``, ordered deterministically."""
        files = [str(path) for path in sorted(self.root.glob("*.parquet"))]
        if not files:
            return ()
        point_in_time = ensure_aware(as_of)
        with duckdb.connect(":memory:") as connection:
            rows = connection.execute(
                """
                SELECT
                    evidence_id,
                    subject,
                    kind,
                    event_time,
                    available_time,
                    ingested_time,
                    revision_time,
                    source_id,
                    source_uri,
                    source_license,
                    redistribution,
                    summary,
                    payload_json,
                    content_hash
                FROM read_parquet(?)
                WHERE available_time <= ?
                  AND (? IS NULL OR subject = ?)
                  AND (? IS NULL OR kind = ?)
                ORDER BY available_time, evidence_id
                """,
                [files, point_in_time, subject, subject, kind, kind],
            ).fetchall()
        return tuple(self._deserialize(cast(tuple[object, ...], row)) for row in rows)

    @staticmethod
    def _serialize(item: EvidenceSnapshot) -> tuple[object, ...]:
        dumped = item.model_dump(mode="json")
        payload_json = json.dumps(
            dumped["payload"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return (
            item.evidence_id,
            item.subject,
            item.kind,
            item.timeline.event_time,
            item.timeline.available_time,
            item.timeline.ingested_time,
            item.timeline.revision_time,
            item.source_id,
            item.source_uri,
            item.source_license,
            item.redistribution,
            item.summary,
            payload_json,
            item.content_hash,
        )

    @staticmethod
    def _deserialize(row: tuple[object, ...]) -> EvidenceSnapshot:
        stored_id = cast(str, row[0])
        stored_hash = cast(str, row[13])
        item = EvidenceSnapshot(
            subject=cast(str, row[1]),
            kind=cast(str, row[2]),
            timeline=Timeline(
                event_time=cast(datetime, row[3]),
                available_time=cast(datetime, row[4]),
                ingested_time=cast(datetime, row[5]),
                revision_time=cast(datetime, row[6]),
            ),
            source_id=cast(str, row[7]),
            source_uri=cast(str | None, row[8]),
            source_license=cast(str, row[9]),
            redistribution=cast(Literal["allowed", "restricted", "unknown"], row[10]),
            summary=cast(str, row[11]),
            payload=json.loads(cast(str, row[12])),
        )
        if item.evidence_id != stored_id or item.content_hash != stored_hash:
            raise StorageIntegrityError(f"evidence integrity check failed: {stored_id}")
        return item


def read_parquet_records(path: Path) -> list[dict[str, object]]:
    """Read every row of a Parquet file as plain dicts, via DuckDB.

    The concrete implementation behind `providers.file.ParquetReader` -- see that
    Protocol's docstring for why `providers/file.py` cannot import this module, or
    `duckdb`, at all (a prior attempt to have `providers/file.py` import this exact
    function was rejected by import-linter's `forbidden` contract for
    `providers-no-infra-imports`: `providers.file -> storage.parquet -> duckdb` is
    flagged as transitive `duckdb` reachability, identically to a direct
    `providers.file -> duckdb` import). Instead, `FileProvider`'s composition sites --
    `cli.py`'s `evidence_build` command and `sdk.py`'s `OpenAlphaSDK.build_file_evidence`,
    neither of which falls under that contract's `source_modules` -- import this function
    directly and pass it in as `parquet_reader`.

    `duckdb.Error` is translated to a plain `ValueError` here (matching the translation
    this logic used to perform inline inside `providers/file.py`, before V2-P0B-011),
    so callers can catch a generic, storage-agnostic failure without importing `duckdb`
    themselves.
    """
    try:
        with duckdb.connect(":memory:") as connection:
            cursor = connection.execute("SELECT * FROM read_parquet(?)", [str(path)])
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except duckdb.Error as error:
        raise ValueError(f"cannot read parquet file {path.name}: {error}") from error
