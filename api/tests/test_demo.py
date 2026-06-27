"""Demo seed: register one real signed fixture asset so the recovery loop closes to VERIFIED
without any external credentials, and serve its exact bytes so the front end can recover it."""

from __future__ import annotations

import io

import httpx
from httpx import ASGITransport
from PIL import Image

from rooted_api import sbr
from rooted_api.demo import DEMO_MANIFEST_ID, demo_sample_png, seed_demo
from rooted_api.main import app
from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker


def _fresh() -> tuple[Resolver, TransparencyLog]:
    return Resolver(InMemoryIndex(), FakeWatermarker()), TransparencyLog()


def test_seed_registers_a_recoverable_asset() -> None:
    resolver, log = _fresh()
    seed_demo(resolver, log)
    img = Image.open(io.BytesIO(demo_sample_png()))
    result = resolver.resolve_by_content(img)
    assert [m.manifest_id for m in result.matches] == [DEMO_MANIFEST_ID]


def test_seed_is_idempotent() -> None:
    resolver, log = _fresh()
    seed_demo(resolver, log)
    seed_demo(
        resolver, log
    )  # a second call (e.g. a restart against a persistent backend) is a no-op
    result = resolver.resolve_by_content(Image.open(io.BytesIO(demo_sample_png())))
    assert len(result.matches) == 1


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
                files={"file": ("sample.png", sample.content, "image/png")},
            )
            assert rec.status_code == 200
            assert rec.json()["matches"][0]["manifestId"] == DEMO_MANIFEST_ID
    finally:
        sbr.set_resolver(None)
        sbr.set_log(None)
