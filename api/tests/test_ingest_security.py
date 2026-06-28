"""Security review fixes for the /ingest write path and the image decoder.

- /ingest is gated by ROOTED_INGEST_KEY (it writes to the index and the transparency log).
- a watermark id, once bound to a manifest, is immutable (a second ingest cannot re-point it).
- the watermarker logs a visible warning when it silently degrades real TrustMark -> fake.
- the image decoder rejects a decompression bomb whose declared pixel size exceeds the cap.
"""

from __future__ import annotations

import io

import httpx
import PIL.Image
import pytest
from fastapi import HTTPException
from httpx import ASGITransport
from PIL import Image

from rooted_api import sbr
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker


def _png_bytes(size: int = 16) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (123, 50, 200)).save(buf, "PNG")
    return buf.getvalue()


def _wire() -> None:
    sbr.set_resolver(Resolver(InMemoryIndex(), FakeWatermarker()))
    sbr.set_log(TransparencyLog())


def _unwire() -> None:
    sbr.set_resolver(None)
    sbr.set_log(None)


async def _ingest(client: httpx.AsyncClient, manifest_id: str, watermark_id: str, key: str | None):
    headers = {"X-Ingest-Key": key} if key is not None else {}
    return await client.post(
        "/ingest",
        files={"file": ("a.png", _png_bytes(), "image/png")},
        data={"manifest_id": manifest_id, "watermark_id": watermark_id},
        headers=headers,
    )


async def test_ingest_requires_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTED_INGEST_KEY", "s3cret")
    _wire()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            no_key = await _ingest(c, "urn:c2pa:a", "WMA", key=None)
            assert no_key.status_code == 401
            wrong = await _ingest(c, "urn:c2pa:a", "WMA", key="nope")
            assert wrong.status_code == 401
            ok = await _ingest(c, "urn:c2pa:a", "WMA", key="s3cret")
            assert ok.status_code == 200
    finally:
        _unwire()


async def test_ingest_allowed_without_key_in_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOTED_INGEST_KEY", raising=False)
    monkeypatch.delenv("ROOTED_REQUIRE_INGEST_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    _wire()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            ok = await _ingest(c, "urn:c2pa:demo", "WMD", key=None)
            assert ok.status_code == 200
    finally:
        _unwire()


async def test_ingest_disabled_in_prod_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOTED_INGEST_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    _wire()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await _ingest(c, "urn:c2pa:p", "WMP", key=None)
            assert r.status_code == 503
    finally:
        _unwire()


async def test_ingest_rejects_duplicate_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOTED_INGEST_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    _wire()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            first = await _ingest(c, "urn:c2pa:victim", "SHARED", key=None)
            assert first.status_code == 200
            # a new manifest carrying the SAME watermark id must not re-point the binding
            second = await _ingest(c, "urn:c2pa:attacker", "SHARED", key=None)
            assert second.status_code == 409
    finally:
        _unwire()


def test_index_watermark_binding_is_immutable() -> None:
    idx = InMemoryIndex()
    idx.put_watermark_binding("W", "urn:c2pa:first")
    idx.put_watermark_binding("W", "urn:c2pa:second")  # must NOT overwrite
    assert idx.manifest_for_watermark("W") == "urn:c2pa:first"


def test_watermarker_warns_on_missing_extra(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ROOTED_REAL_WATERMARK", "1")
    # Force the missing-extra path deterministically (independent of whether trustmark happens to be
    # installed): the construction raises ImportError, so _make_watermarker must fall back to the
    # fake AND log a visible warning, not silently degrade.
    import rooted_provenance.watermark as wm_mod

    def _boom(self: object) -> None:
        raise ImportError("trustmark extra not installed")

    monkeypatch.setattr(wm_mod.TrustMarkWatermarker, "__init__", _boom)
    with caplog.at_level("WARNING"):
        wm = sbr._make_watermarker()
    assert isinstance(wm, FakeWatermarker)
    assert any(
        "trustmark" in r.message.lower() or "watermark" in r.message.lower() for r in caplog.records
    )


def test_decode_rejects_decompression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set the cap so a 50x50 image (2500 px) lands in the 1x-2x band (2000 < 2500 < 4000) where
    # Pillow only WARNS and would otherwise decode. The fix promotes that warning to an error, so
    # the decoder rejects (415) rather than materializing the pixels.
    monkeypatch.setattr(PIL.Image, "MAX_IMAGE_PIXELS", 2000)
    with pytest.raises(HTTPException) as exc:
        sbr._decode_image(_png_bytes(50))
    assert exc.value.status_code == 415
