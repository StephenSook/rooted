"""Transparency log routes: a signed checkpoint and a self-contained, independently-verifiable
inclusion proof for an ingested manifest.

The proof a client gets back must be verifiable WITHOUT trusting the server: it carries the
serialized pymerkle proof and the signed checkpoint (tree head) it is pinned to, plus the public
key, so the client resolves the proof to a root and checks that root is the signed head. These
tests verify client-side, not just via the server's own flag.
"""

from __future__ import annotations

import io

import httpx
import numpy as np
from httpx import ASGITransport
from PIL import Image
from pymerkle import MerkleProof

from rooted_api.main import app
from rooted_provenance.merkle import verify_checkpoint
from rooted_provenance.models import Manifest, MerkleCheckpoint
from rooted_provenance.signing import load_public_key


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    img = Image.fromarray(arr).resize((64, 64)).resize((256, 256))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _ingest(c: httpx.AsyncClient, manifest_id: str, watermark_id: str, seed: int) -> bytes:
    data = _png(seed)
    r = await c.post(
        "/ingest",
        files={"file": ("a.png", data, "image/png")},
        data={"manifest_id": manifest_id, "watermark_id": watermark_id, "model": "seedream"},
    )
    assert r.status_code == 200
    return data


async def test_checkpoint_is_signed_and_verifies() -> None:
    async with _client() as c:
        await _ingest(c, "urn:c2pa:tlog1", "RT01", 11)
        r = await c.get("/transparency/checkpoint")
    assert r.status_code == 200
    body = r.json()
    assert body["key_source"] == "ephemeral"
    cp = MerkleCheckpoint.model_validate(body["checkpoint"])
    assert cp.tree_size >= 1
    assert cp.epoch == cp.tree_size  # epoch is tied to tree state, not a request counter
    pub = load_public_key(bytes.fromhex(body["public_key_hex"]))
    assert verify_checkpoint(cp, pub) is True


async def test_checkpoint_get_is_idempotent() -> None:
    async with _client() as c:
        await _ingest(c, "urn:c2pa:tlog_idem", "RT09", 19)
        first = (await c.get("/transparency/checkpoint")).json()["checkpoint"]
        second = (await c.get("/transparency/checkpoint")).json()["checkpoint"]
    # No ingest between the two reads: the signed head must be identical (a GET must not mutate it).
    assert first["epoch"] == second["epoch"]
    assert first["tree_size"] == second["tree_size"]
    assert first["root_hash"] == second["root_hash"]
    assert first["signature_b64"] == second["signature_b64"]


async def test_inclusion_proof_is_independently_verifiable() -> None:
    async with _client() as c:
        await _ingest(c, "urn:c2pa:tlog2", "RT02", 12)
        r = await c.get("/transparency/proof/urn:c2pa:tlog2")
        manifest_body = (await c.get("/manifests/urn:c2pa:tlog2")).json()
    assert r.status_code == 200
    body = r.json()
    assert body["manifest_id"] == "urn:c2pa:tlog2"
    assert body["leaf_index"] >= 1
    assert body["server_verified"] is True

    # The proof is pinned to a signed checkpoint: its resolved root equals the signed root, and
    # that checkpoint verifies against the returned key. This is the independent (non-server) check.
    cp = MerkleCheckpoint.model_validate(body["checkpoint"])
    pub = load_public_key(bytes.fromhex(body["public_key_hex"]))
    assert verify_checkpoint(cp, pub) is True
    proof = MerkleProof.deserialize(body["proof"])
    assert proof.resolve() == bytes.fromhex(cp.root_hash)
    assert body["root_hash"] == cp.root_hash

    # The leaf is bound to the actual manifest: leaf_hash is the manifest's canonical hash, which
    # redaction leaves unchanged (personal provenance is excluded from canonical_payload).
    assert body["leaf_hash"] == Manifest.model_validate(manifest_body).canonical_hash()


async def test_inclusion_proof_does_not_resolve_to_a_wrong_root() -> None:
    async with _client() as c:
        await _ingest(c, "urn:c2pa:tlog3", "RT03", 13)
        body = (await c.get("/transparency/proof/urn:c2pa:tlog3")).json()
    proof = MerkleProof.deserialize(body["proof"])
    assert proof.resolve() != b"\x00" * 32


async def test_inclusion_proof_unknown_manifest_404() -> None:
    async with _client() as c:
        r = await c.get("/transparency/proof/urn:c2pa:absent")
    assert r.status_code == 404
