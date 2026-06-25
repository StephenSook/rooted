"""SBR API: supportedAlgorithms hides PDQ, ingest then recover by content, redaction on read."""

from __future__ import annotations

import hashlib
import io

import httpx
import numpy as np
from httpx import ASGITransport
from PIL import Image

from rooted_api.main import app


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    img = Image.fromarray(arr).resize((64, 64)).resize((256, 256))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_supported_algorithms_hides_pdq() -> None:
    async with _client() as c:
        r = await c.get("/services/supportedAlgorithms")
    assert r.status_code == 200
    assert "pdq" not in str(r.json()).lower()


async def test_ingest_then_recover_by_content() -> None:
    data = _png(7)
    async with _client() as c:
        ing = await c.post(
            "/ingest",
            files={"file": ("a.png", data, "image/png")},
            data={"manifest_id": "urn:c2pa:demo", "watermark_id": "RT07", "model": "seedream"},
        )
        assert ing.status_code == 200
        rec = await c.post("/matches/byContent", files={"file": ("a.png", data, "image/png")})
    assert rec.status_code == 200
    assert rec.json()["matches"][0]["manifest_id"] == "urn:c2pa:demo"


async def test_get_manifest_is_redacted() -> None:
    data = _png(8)
    async with _client() as c:
        await c.post(
            "/ingest",
            files={"file": ("b.png", data, "image/png")},
            data={"manifest_id": "urn:c2pa:demo2", "watermark_id": "RT08", "model": "flux"},
        )
        r = await c.get("/manifests/urn:c2pa:demo2")
    assert r.status_code == 200
    body = r.json()
    assert body["system_provenance"]["model"] == "flux"
    assert body["personal_provenance"] == {}
    assert body["asset_sha256"] == hashlib.sha256(data).hexdigest()


async def test_unknown_manifest_404() -> None:
    async with _client() as c:
        r = await c.get("/manifests/urn:c2pa:nope")
    assert r.status_code == 404
