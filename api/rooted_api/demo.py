"""A credential-free demo asset.

Registers fixture assets into the index so the recovery loop closes to VERIFIED live. The images are
generated deterministically, so the /demo/sample bytes match the registered PDQ fingerprint exactly,
and the recovery (a genuine PDQ match plus a real transparency-log entry) is real; only the
"generation" is a local fixture, labeled as such in the manifest's system provenance.

When a Storage backend is configured (Backblaze B2 in production, via B2_KEY_ID/B2_APP_KEY/
B2_BUCKET_DEV), each asset's bytes, its canonical manifest, and its COSE signature are also written
content-addressably to B2, so the live demo exercises B2 for real. Without storage, it runs purely
in-memory. Gated on ROOTED_DEMO_SEED, and idempotent so a restart never duplicates it.
"""

from __future__ import annotations

import hashlib
import io
import os
from typing import Any

import numpy as np
from fastapi import APIRouter
from fastapi.responses import Response
from PIL import Image

from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.models import ALG_TRUSTMARK_P, Manifest, SoftBinding, canonical_json
from rooted_provenance.resolver import Resolver
from rooted_provenance.signing import generate_keypair, sign_manifest
from rooted_storage.storage import Storage, asset_key, manifest_key, signature_key

DEMO_MANIFEST_ID = "urn:c2pa:demo-0000-0000-0000-000000000001"
DEMO_WATERMARK_ID = "DEMO"
_CREATED_AT = "2026-06-27T00:00:00Z"

router = APIRouter()
_sample_png: bytes | None = None

_PRIMARY_SEED = 7
# Extra fixtures so the transparency log (and the Merkle explorer) has real structure, not one leaf.
_EXTRA_SEEDS = (11, 13, 17, 19, 23, 29)
DEMO_ENTRY_COUNT = 1 + len(_EXTRA_SEEDS)


def _demo_image(seed: int, size: int = 256) -> Image.Image:
    """A smooth gradient + soft blobs + mild texture: natural-frequency content that yields a
    stable, high-quality PDQ hash, so the served image self-matches the registered fingerprint."""
    rng = np.random.default_rng(seed)
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
    """The primary demo asset's exact PNG bytes (deterministic), cached, regenerable on restart."""
    global _sample_png
    if _sample_png is None:
        buf = io.BytesIO()
        _demo_image(_PRIMARY_SEED).save(buf, "PNG")
        _sample_png = buf.getvalue()
    return _sample_png


def _register(
    resolver: Resolver,
    log: TransparencyLog,
    manifest_id: str,
    watermark_id: str,
    png: bytes,
    storage: Storage | None,
    signing_key: Any,
) -> None:
    manifest = Manifest(
        manifest_id=manifest_id,
        asset_sha256=hashlib.sha256(png).hexdigest(),
        created_at=_CREATED_AT,
        system_provenance={"model": "rooted-demo-fixture", "note": "seeded demo asset"},
        soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=watermark_id)],
    )
    resolver.register(manifest, Image.open(io.BytesIO(png)), watermark_id)
    log.append(manifest.manifest_id, manifest.canonical_hash())
    if storage is not None:
        # Store the asset, its canonical manifest, and its COSE signature content-addressably, so
        # the live demo writes real objects to Backblaze B2 (the recovery repository).
        storage.put(asset_key(manifest.asset_sha256), png)
        storage.put(manifest_key(manifest_id), canonical_json(manifest.model_dump()))
        storage.put(signature_key(manifest_id), sign_manifest(manifest, signing_key))


def seed_demo(resolver: Resolver, log: TransparencyLog, storage: Storage | None = None) -> None:
    """Register the demo assets for recovery: the primary (served at /demo/sample, recovered by the
    UI) plus a few extra fixtures so the log has structure. When storage is set, also write each
    asset/manifest/signature to it (B2). Idempotent if already seeded."""
    if resolver.get_manifest(DEMO_MANIFEST_ID) is not None:
        return
    key, _pub = generate_keypair()
    _register(resolver, log, DEMO_MANIFEST_ID, DEMO_WATERMARK_ID, demo_sample_png(), storage, key)
    for i, seed in enumerate(_EXTRA_SEEDS):
        buf = io.BytesIO()
        _demo_image(seed).save(buf, "PNG")
        mid = f"urn:c2pa:demo-{i:04d}-0000-0000-0000-000000000002"
        _register(resolver, log, mid, f"DX{i:02d}", buf.getvalue(), storage, key)


# include_in_schema=False: these are demo aids, not part of the spec-defined SBR contract, so they
# stay out of the OpenAPI surface (and the schemathesis contract test); the UI fetches them.
@router.get("/demo/sample", include_in_schema=False)
async def demo_sample() -> Response:
    """Serve the demo asset bytes so the UI can recover it. No provenance data; safe unauthed."""
    return Response(content=demo_sample_png(), media_type="image/png")


@router.get("/demo/storage", include_in_schema=False)
async def demo_storage() -> dict[str, Any]:
    """Report where the primary demo asset is stored, and confirm the objects exist (a real read
    against B2 when configured). Drives the UI's "stored on Backblaze B2" panel."""
    from rooted_api.sbr import get_storage
    from rooted_storage.storage import B2Storage

    storage = get_storage()
    sha = hashlib.sha256(demo_sample_png()).hexdigest()
    keys = {
        "asset": asset_key(sha),
        "manifest": manifest_key(DEMO_MANIFEST_ID),
        "signature": signature_key(DEMO_MANIFEST_ID),
    }
    if storage is None:
        return {
            "backend": "none",
            "bucket": None,
            "keys": keys,
            "present": dict.fromkeys(keys, False),
        }
    backend = "backblaze-b2" if isinstance(storage, B2Storage) else "in-memory"
    bucket = os.environ.get("B2_BUCKET_DEV") if backend == "backblaze-b2" else None
    present = {name: storage.exists(k) for name, k in keys.items()}
    return {"backend": backend, "bucket": bucket, "keys": keys, "present": present}
