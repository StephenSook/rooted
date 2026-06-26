"""Transparency log routes: a signed checkpoint and an inclusion proof for an ingested manifest.

The Merkle log on the API side is populated by /ingest (the convenience generation route), so the
recovery server can also serve the tamper-evidence surface the MCP product tools and the front end
read. Checkpoints are signed (Ed25519); the public key travels with the checkpoint so a client can
verify the tree head independently.
"""

from __future__ import annotations

import io

import httpx
import numpy as np
from httpx import ASGITransport
from PIL import Image

from rooted_api.main import app
from rooted_provenance.merkle import verify_checkpoint
from rooted_provenance.models import MerkleCheckpoint
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
    cp = MerkleCheckpoint.model_validate(body["checkpoint"])
    assert cp.tree_size >= 1
    pub = load_public_key(bytes.fromhex(body["public_key_hex"]))
    assert verify_checkpoint(cp, pub) is True


async def test_inclusion_proof_for_ingested_manifest() -> None:
    async with _client() as c:
        await _ingest(c, "urn:c2pa:tlog2", "RT02", 12)
        r = await c.get("/transparency/proof/urn:c2pa:tlog2")
    assert r.status_code == 200
    body = r.json()
    assert body["manifest_id"] == "urn:c2pa:tlog2"
    assert body["leaf_index"] >= 1
    assert body["tree_size"] >= body["leaf_index"]
    assert body["verified"] is True
    assert body["proof"]


async def test_inclusion_proof_unknown_manifest_404() -> None:
    async with _client() as c:
        r = await c.get("/transparency/proof/urn:c2pa:absent")
    assert r.status_code == 404
