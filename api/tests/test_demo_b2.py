"""The demo seed writes each asset/manifest/signature to the object store (B2 in prod) when one is
configured, and /demo/storage reports it. Uses the in-memory storage fake; the real B2 path is
exercised by the live deploy and a local smoke."""

from __future__ import annotations

import hashlib

import httpx
from httpx import ASGITransport

from rooted_api import sbr
from rooted_api.demo import DEMO_MANIFEST_ID, demo_sample_bytes, seed_demo
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker
from rooted_storage.storage import (
    InMemoryStorage,
    asset_key,
    manifest_key,
    signature_key,
)


def _fresh() -> tuple[Resolver, TransparencyLog, InMemoryStorage]:
    return Resolver(InMemoryIndex(), FakeWatermarker()), TransparencyLog(), InMemoryStorage()


def test_seed_writes_asset_manifest_signature_to_storage() -> None:
    resolver, log, storage = _fresh()
    seed_demo(resolver, log, storage)
    sha = hashlib.sha256(demo_sample_bytes()).hexdigest()
    assert storage.exists(asset_key(sha))
    assert storage.exists(manifest_key(DEMO_MANIFEST_ID))
    assert storage.exists(signature_key(DEMO_MANIFEST_ID))


def test_seed_without_storage_stays_in_memory() -> None:
    resolver, log, _ = _fresh()
    seed_demo(resolver, log)  # no storage arg
    assert resolver.get_manifest(DEMO_MANIFEST_ID) is not None


async def test_demo_storage_route_reports_present_objects() -> None:
    resolver, log, storage = _fresh()
    sbr.set_resolver(resolver)
    sbr.set_log(log)
    sbr.set_storage(storage)
    seed_demo(resolver, log, storage)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/demo/storage")
            assert r.status_code == 200
            body = r.json()
            assert body["backend"] == "in-memory"
            assert body["present"]["asset"]
            assert body["present"]["manifest"]
            assert body["present"]["signature"]
    finally:
        sbr.set_resolver(None)
        sbr.set_log(None)
        sbr.set_storage(None)
