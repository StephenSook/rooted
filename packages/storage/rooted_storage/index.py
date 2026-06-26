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

The Index protocol is synchronous (the resolver calls it synchronously), so this uses a synchronous
psycopg connection. A pooled async driver is the production swap when the resolver goes async.

Known production-hardening follow-ups, to land with the live Render Postgres deploy (the in-memory
demo path is unaffected): use psycopg_pool with reconnect (a dropped single connection otherwise
wedges recovery until restart); offload these blocking calls off the async event loop; make
Resolver.register atomic in one transaction (autocommit lets a partial ingest orphan a manifest);
and persist the transparency tree alongside the manifests (it is in-memory today, so proofs reset on
restart). Tracked in the PR description and project memory.
"""

from __future__ import annotations

import json

import psycopg

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


class PostgresIndex:
    """Postgres-backed Index. Construct with a libpq conninfo string or URL."""

    def __init__(self, conninfo: str) -> None:
        self._conn = psycopg.connect(conninfo, autocommit=True)

    def create_schema(self) -> None:
        """Idempotently create the tables and indexes. Safe to call on every startup."""
        with self._conn.cursor() as cur:
            cur.execute(_SCHEMA)

    def clear(self) -> None:
        """Truncate all index tables (used by tests for isolation)."""
        with self._conn.cursor() as cur:
            cur.execute("TRUNCATE manifests, watermark_bindings, perceptual_hashes")

    def close(self) -> None:
        self._conn.close()

    def put_manifest(self, manifest: Manifest) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manifests (manifest_id, body) VALUES (%s, %s) "
                "ON CONFLICT (manifest_id) DO UPDATE SET body = EXCLUDED.body",
                (manifest.manifest_id, json.dumps(manifest.model_dump())),
            )

    def get_manifest(self, manifest_id: str) -> Manifest | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT body FROM manifests WHERE manifest_id = %s", (manifest_id,))
            row = cur.fetchone()
        return Manifest.model_validate(row[0]) if row is not None else None

    def put_watermark_binding(self, watermark_id: str, manifest_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watermark_bindings (watermark_id, manifest_id) VALUES (%s, %s) "
                "ON CONFLICT (watermark_id) DO UPDATE SET manifest_id = EXCLUDED.manifest_id",
                (watermark_id, manifest_id),
            )

    def manifest_for_watermark(self, watermark_id: str) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT manifest_id FROM watermark_bindings WHERE watermark_id = %s",
                (watermark_id,),
            )
            row = cur.fetchone()
        return str(row[0]) if row is not None else None

    def put_fingerprint(self, pdq_bits: str, manifest_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO perceptual_hashes (manifest_id, pdq) VALUES (%s, %s::bit(256))",
                (manifest_id, pdq_bits),
            )

    def nearest_fingerprint(self, pdq_bits: str, threshold: int) -> tuple[str, int] | None:
        # bit_count(a # b) is the Hamming distance: count the set bits of the XOR. Exact, no index
        # needed for the small recovery set; the threshold bounds a false match.
        with self._conn.cursor() as cur:
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
        with self._conn.cursor() as cur:
            cur.execute("SELECT pdq FROM perceptual_hashes WHERE manifest_id = %s", (manifest_id,))
            return [str(r[0]) for r in cur.fetchall()]
