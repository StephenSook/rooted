"""Backblaze B2 Event-Notification ingest: the webhook (signature, test-event, ingest, guards,
idempotency) and the /demo/b2-events surface. Network-free: in-memory resolver/log/storage."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from collections.abc import Iterator
from typing import Any

import httpx
import numpy as np
import pytest
from httpx import ASGITransport
from PIL import Image

from rooted_api import b2_events, sbr
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker
from rooted_storage.storage import InMemoryStorage

_SECRET = "0123456789abcdef0123456789abcdef"


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    arr = (rng.random((64, 64, 3)) * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryStorage]:
    monkeypatch.setenv("B2_EVENT_SIGNING_SECRET", _SECRET)
    sbr.set_resolver(Resolver(InMemoryIndex(), FakeWatermarker()))
    sbr.set_log(TransparencyLog())
    st = InMemoryStorage()
    sbr.set_storage(st)
    b2_events._recent.clear()
    yield st
    sbr.set_resolver(None)
    sbr.set_log(None)
    sbr.set_storage(None)
    b2_events._recent.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _sig(raw: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def _post(
    c: httpx.AsyncClient, payload: dict[str, Any], secret: str = _SECRET
) -> httpx.Response:
    raw = json.dumps(payload).encode()
    return await c.post(
        "/webhooks/b2-event",
        content=raw,
        headers={
            "X-Bz-Event-Notification-Signature": _sig(raw, secret),
            "content-type": "application/json",
        },
    )


def _event(name: str, size: int = 4096, et: str = "b2:ObjectCreated:Upload") -> dict[str, Any]:
    return {
        "events": [
            {
                "eventType": et,
                "bucketName": "rooted-dev",
                "objectName": name,
                "objectSize": size,
                "objectVersionId": "4_zfakeversion",
                "eventTimestamp": 1684793309123,
            }
        ]
    }


async def test_webhook_refuses_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("B2_EVENT_SIGNING_SECRET", raising=False)
    async with _client() as c:
        r = await _post(c, _event("ingest/x.png"))
    assert r.status_code == 503
    assert r.json()["status"] == "not-configured"


async def test_webhook_rejects_bad_signature(storage: InMemoryStorage) -> None:
    async with _client() as c:
        raw = json.dumps(_event("ingest/x.png")).encode()
        r = await c.post(
            "/webhooks/b2-event",
            content=raw,
            headers={"X-Bz-Event-Notification-Signature": "deadbeef"},
        )
    assert r.status_code == 401


async def test_webhook_acks_test_event(storage: InMemoryStorage) -> None:
    async with _client() as c:
        r = await _post(c, {"events": [{"eventType": "b2:TestEvent"}]})
    assert r.status_code == 200
    assert r.json()["status"] == "test-ok"


async def test_webhook_ingests_and_recovers(storage: InMemoryStorage) -> None:
    png = _png(7)
    storage.put("ingest/photo.png", png)
    async with _client() as c:
        r = await _post(c, _event("ingest/photo.png", size=len(png)))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ingested"] == 1

        status = await c.get("/demo/b2-events")
        sj = status.json()
        assert sj["configured"] is True
        assert sj["count"] == 1
        assert sj["recent"][0]["objectKey"] == "ingest/photo.png"
        manifest_id = sj["recent"][0]["manifestId"]
        assert manifest_id.startswith("urn:c2pa:b2-")

        # The auto-ingested object is now recoverable by content (the orchestration loop closes).
        rec = await c.post("/matches/byContent", files={"file": ("photo.png", png, "image/png")})
        assert rec.status_code == 200
        assert rec.json()["matches"][0]["manifestId"] == manifest_id


async def test_webhook_skips_objects_outside_the_prefix(storage: InMemoryStorage) -> None:
    storage.put("other/photo.png", _png(8))
    async with _client() as c:
        r = await _post(c, _event("other/photo.png"))
    assert r.status_code == 200
    b = r.json()
    assert b["ingested"] == 0
    assert b["skipped"] == 1


async def test_webhook_skips_oversized(storage: InMemoryStorage) -> None:
    async with _client() as c:
        r = await _post(c, _event("ingest/huge.png", size=b2_events._MAX_OBJECT_BYTES + 1))
    assert r.status_code == 200
    assert r.json()["ingested"] == 0


async def test_webhook_is_idempotent(storage: InMemoryStorage) -> None:
    png = _png(9)
    storage.put("ingest/dup.png", png)
    async with _client() as c:
        first = await _post(c, _event("ingest/dup.png", size=len(png)))
        second = await _post(c, _event("ingest/dup.png", size=len(png)))
    assert first.json()["ingested"] == 1
    assert second.json()["ingested"] == 0  # already registered on redelivery
