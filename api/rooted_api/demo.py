"""A credential-free demo asset.

Registers one real fixture asset into the index so the recovery loop closes to VERIFIED live without
a provider key or B2 bucket. The image is generated deterministically, so the bytes served at
/demo/sample match the registered PDQ fingerprint exactly, and the recovery (a genuine PDQ match
plus a real transparency-log entry) is real; only the "generation" is a local fixture, labeled as
such in the manifest's system provenance. Gated on ROOTED_DEMO_SEED so it never runs against a real
deploy by accident, and idempotent so a restart against a persistent backend never duplicates it.
"""

from __future__ import annotations

import hashlib
import io

import numpy as np
from fastapi import APIRouter
from fastapi.responses import Response
from PIL import Image

from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.models import ALG_TRUSTMARK_P, Manifest, SoftBinding
from rooted_provenance.resolver import Resolver

DEMO_MANIFEST_ID = "urn:c2pa:demo-0000-0000-0000-000000000001"
DEMO_WATERMARK_ID = "DEMO"
_CREATED_AT = "2026-06-27T00:00:00Z"

router = APIRouter()
_sample_png: bytes | None = None


def _demo_image(size: int = 256) -> Image.Image:
    """A smooth gradient + soft blobs + mild texture: natural-frequency content that yields a
    stable, high-quality PDQ hash, so the served image self-matches the registered fingerprint."""
    rng = np.random.default_rng(7)
    ramp = np.linspace(0, 255, size)
    img = np.stack(
        [np.tile(ramp, (size, 1)), np.tile(ramp[:, None], (1, size)), np.full((size, size), 128.0)],
        axis=-1,
    )
    yy, xx = np.mgrid[0:size, 0:size]
    for _ in range(6):
        cy, cx = rng.integers(0, size, 2)
        r = rng.integers(20, 70)
        blob = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r))
        img += blob[..., None] * rng.integers(-80, 80, 3)
    img += rng.normal(0, 6, (size, size, 3))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def demo_sample_png() -> bytes:
    """The demo asset's exact PNG bytes (deterministic), cached and regenerable after a restart."""
    global _sample_png
    if _sample_png is None:
        buf = io.BytesIO()
        _demo_image().save(buf, "PNG")
        _sample_png = buf.getvalue()
    return _sample_png


def seed_demo(resolver: Resolver, log: TransparencyLog) -> None:
    """Register the demo asset for recovery. Idempotent: a no-op if it is already present."""
    if resolver.get_manifest(DEMO_MANIFEST_ID) is not None:
        return
    png = demo_sample_png()
    image = Image.open(io.BytesIO(png))
    manifest = Manifest(
        manifest_id=DEMO_MANIFEST_ID,
        asset_sha256=hashlib.sha256(png).hexdigest(),
        created_at=_CREATED_AT,
        system_provenance={"model": "rooted-demo-fixture", "note": "seeded demo asset"},
        soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=DEMO_WATERMARK_ID)],
    )
    resolver.register(manifest, image, DEMO_WATERMARK_ID)
    log.append(manifest.manifest_id, manifest.canonical_hash())


# include_in_schema=False: this is a demo aid, not part of the spec-defined SBR contract, so it
# stays out of the OpenAPI surface (and the schemathesis contract test). The UI fetches it directly.
@router.get("/demo/sample", include_in_schema=False)
async def demo_sample() -> Response:
    """Serve the demo asset bytes so the UI can recover it. No provenance data; safe unauthed."""
    return Response(content=demo_sample_png(), media_type="image/png")
