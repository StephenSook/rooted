"""Demo seed: register one real signed fixture asset so the recovery loop closes to VERIFIED
without any external credentials, and serve its exact bytes so the front end can recover it."""

from __future__ import annotations

import io

import httpx
from httpx import ASGITransport
from PIL import Image

from rooted_api import sbr
from rooted_api.demo import DEMO_ENTRY_COUNT, DEMO_MANIFEST_ID, demo_sample_bytes, seed_demo
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker


def _fresh() -> tuple[Resolver, TransparencyLog]:
    return Resolver(InMemoryIndex(), FakeWatermarker()), TransparencyLog()


def test_seed_registers_a_recoverable_asset() -> None:
    resolver, log = _fresh()
    seed_demo(resolver, log)
    img = Image.open(io.BytesIO(demo_sample_bytes()))
    result = resolver.resolve_by_content(img)
    assert [m.manifest_id for m in result.matches] == [DEMO_MANIFEST_ID]


def test_seed_is_idempotent() -> None:
    resolver, log = _fresh()
    seed_demo(resolver, log)
    seed_demo(
        resolver, log
    )  # a second call (e.g. a restart against a persistent backend) is a no-op
    result = resolver.resolve_by_content(Image.open(io.BytesIO(demo_sample_bytes())))
    assert len(result.matches) == 1
    assert log.size == DEMO_ENTRY_COUNT  # seeded once, not twice


def test_primary_asset_has_real_genblaze_provenance() -> None:
    # The primary demo asset is a real image generated via Genblaze on GMI Cloud (not a fixture),
    # so its system provenance names the real model and provider, honestly.
    resolver, log = _fresh()
    seed_demo(resolver, log)
    manifest = resolver.get_manifest(DEMO_MANIFEST_ID)
    assert manifest is not None
    assert manifest.system_provenance["model"] == "seedream-5.0-lite"
    assert manifest.system_provenance["provider"] == "gmicloud-image"


def test_seed_populates_the_log() -> None:
    resolver, log = _fresh()
    seed_demo(resolver, log)
    entries = log.entries()
    assert len(entries) == DEMO_ENTRY_COUNT
    assert entries[0] == (0, DEMO_MANIFEST_ID, entries[0][2])  # primary is the first leaf


async def test_transparency_log_route() -> None:
    resolver, log = _fresh()
    sbr.set_resolver(resolver)
    sbr.set_log(log)
    seed_demo(resolver, log)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/transparency/log")
            assert r.status_code == 200
            body = r.json()
            assert body["treeSize"] == DEMO_ENTRY_COUNT
            assert len(body["entries"]) == DEMO_ENTRY_COUNT
            assert body["entries"][0]["manifestId"] == DEMO_MANIFEST_ID
            assert len(body["rootHash"]) == 64  # sha256 hex
    finally:
        sbr.set_resolver(None)
        sbr.set_log(None)


async def test_demo_sample_route_then_recover() -> None:
    resolver, log = _fresh()
    sbr.set_resolver(resolver)
    sbr.set_log(log)
    seed_demo(resolver, log)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            sample = await c.get("/demo/sample")
            assert sample.status_code == 200
            assert sample.headers["content-type"].startswith("image/")

            rec = await c.post(
                "/matches/byContent",
                files={"file": ("sample.jpg", sample.content, "image/jpeg")},
            )
            assert rec.status_code == 200
            assert rec.json()["matches"][0]["manifestId"] == DEMO_MANIFEST_ID
    finally:
        sbr.set_resolver(None)
        sbr.set_log(None)
