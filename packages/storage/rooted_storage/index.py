"""PostgresIndex: the recovery Index, backed by Postgres.

Implements the Index protocol (see rooted_provenance.resolver.Index) on Postgres:

- manifests as jsonb keyed by manifest id,
- watermark bindings as an exact-match primary-key lookup (a B-tree),
- PDQ perceptual hashes as native bit(256).

Nearest-fingerprint search is exact Hamming distance via the native Postgres bit_count of the XOR
(`#`) of two bit strings. This needs no extension, is exact (not approximate), and is correct for
the curated recovery index Rooted keeps small on purpose. pgvector's HNSW bit_hamming_ops (the `<~>`
operator) is an approximate-nearest-neighbour accelerator for very large indexes and can be layered
on later; the exact bit_count path here is the wired, tested baseline, so the claim matches code.

Connections come from a psycopg_pool.ConnectionPool with a liveness check on checkout, so a dropped
connection (a Postgres restart or idle timeout over a multi-day window) self-heals instead of
wedging recovery. register() runs the three index writes in one transaction, so a partial ingest
rolls back rather than orphaning an unrecoverable manifest. The API layer offloads these blocking
calls off the event loop. The remaining synchronous-vs-async driver swap is the only deferred item.
"""

from __future__ import annotations

import json

from psycopg_pool import ConnectionPool

from rooted_provenance.models import Manifest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifests (
    manifest_id text PRIMARY KEY,
    body jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS watermark_bindings (
    watermark_id text PRIMARY KEY,
    manifest_id text NOT NULL
);
CREATE TABLE IF NOT EXISTS perceptual_hashes (
    id bigserial PRIMARY KEY,
    manifest_id text NOT NULL,
    pdq bit(256) NOT NULL
);
CREATE INDEX IF NOT EXISTS perceptual_hashes_manifest_idx ON perceptual_hashes (manifest_id);
"""

_INSERT_MANIFEST = (
    "INSERT INTO manifests (manifest_id, body) VALUES (%s, %s) "
    "ON CONFLICT (manifest_id) DO UPDATE SET body = EXCLUDED.body"
)
# A watermark binding is immutable: DO NOTHING (not DO UPDATE) so a second ingest can never
# re-point an existing watermark id to a different manifest and poison recovery. The API layer
# rejects a re-point with a 409 before it reaches here; this is the defense-in-depth at the store.
_INSERT_BINDING = (
    "INSERT INTO watermark_bindings (watermark_id, manifest_id) VALUES (%s, %s) "
    "ON CONFLICT (watermark_id) DO NOTHING"
)
_INSERT_FINGERPRINT = "INSERT INTO perceptual_hashes (manifest_id, pdq) VALUES (%s, %s::bit(256))"


class PostgresIndex:
    """Postgres-backed Index. Construct with a libpq conninfo string or URL."""

    def __init__(self, conninfo: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self._pool = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            open=True,
            check=ConnectionPool.check_connection,
        )

    def create_schema(self) -> None:
        """Idempotently create the tables and indexes. Safe to call on every startup."""
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    def clear(self) -> None:
        """Truncate all index tables (used by tests for isolation)."""
        with self._pool.connection() as conn:
            conn.execute("TRUNCATE manifests, watermark_bindings, perceptual_hashes")

    def close(self) -> None:
        self._pool.close()

    def put_manifest(self, manifest: Manifest) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                _INSERT_MANIFEST, (manifest.manifest_id, json.dumps(manifest.model_dump()))
            )

    def get_manifest(self, manifest_id: str) -> Manifest | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT body FROM manifests WHERE manifest_id = %s", (manifest_id,))
            row = cur.fetchone()
        return Manifest.model_validate(row[0]) if row is not None else None

    def put_watermark_binding(self, watermark_id: str, manifest_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(_INSERT_BINDING, (watermark_id, manifest_id))

    def manifest_for_watermark(self, watermark_id: str) -> str | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT manifest_id FROM watermark_bindings WHERE watermark_id = %s",
                (watermark_id,),
            )
            row = cur.fetchone()
        return str(row[0]) if row is not None else None

    def put_fingerprint(self, pdq_bits: str, manifest_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(_INSERT_FINGERPRINT, (manifest_id, pdq_bits))

    def register(self, manifest: Manifest, watermark_id: str, pdq_bits: str) -> None:
        # All three writes in one transaction: a failure (e.g. a bad pdq cast) rolls the whole
        # ingest back, so a manifest is never stored without its recovery pointers.
        with self._pool.connection() as conn:
            conn.execute(
                _INSERT_MANIFEST, (manifest.manifest_id, json.dumps(manifest.model_dump()))
            )
            conn.execute(_INSERT_BINDING, (watermark_id, manifest.manifest_id))
            conn.execute(_INSERT_FINGERPRINT, (manifest.manifest_id, pdq_bits))

    def nearest_fingerprint(self, pdq_bits: str, threshold: int) -> tuple[str, int] | None:
        # bit_count(a # b) is the Hamming distance: count the set bits of the XOR. Exact, no index
        # needed for the small recovery set; the threshold bounds a false match.
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT manifest_id, bit_count(pdq # %s::bit(256))::int AS dist "
                "FROM perceptual_hashes ORDER BY dist ASC LIMIT 1",
                (pdq_bits,),
            )
            row = cur.fetchone()
        if row is None or row[1] > threshold:
            return None
        return (str(row[0]), int(row[1]))

    def fingerprints_for(self, manifest_id: str) -> list[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT pdq FROM perceptual_hashes WHERE manifest_id = %s", (manifest_id,))
            return [str(r[0]) for r in cur.fetchall()]
