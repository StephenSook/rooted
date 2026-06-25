"""The vendor-neutral C2PA Soft Binding Resolution API (C2PA v2.4 route shapes).

This scaffold wires the resolver to an in-memory index and the fake watermarker so the recovery
path runs end to end without credentials. The real deployment swaps in the Postgres index, the B2
store, and the TrustMark watermarker via the get_resolver dependency. The /ingest route is a
convenience for the demo; the spec query routes are byContent and byBinding.

Note: response field names are snake_case here; camelCase spec aliasing lands with the schemathesis
contract pass.
"""

from __future__ import annotations

import hashlib
import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from rooted_provenance.models import (
    ALG_TRUSTMARK_P,
    Manifest,
    SoftBinding,
    SoftBindingQueryResult,
    SupportedAlgorithms,
)
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.watermark import FakeWatermarker

router = APIRouter()

# Scaffold singleton. Real wiring: Resolver(PostgresIndex(...), TrustMarkWatermarker()).
_resolver = Resolver(InMemoryIndex(), FakeWatermarker())


def get_resolver() -> Resolver:
    return _resolver


async def _read_image(file: UploadFile) -> Image.Image:
    data = await file.read()
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported image") from exc


@router.get("/services/supportedAlgorithms", response_model=SupportedAlgorithms)
async def supported_algorithms() -> SupportedAlgorithms:
    return SupportedAlgorithms()


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    manifest_id: str = Form(...),
    watermark_id: str = Form(...),
    model: str = Form("unknown"),
) -> dict[str, str]:
    """Trusted generation-side ingest (authenticated in prod). Public surface is /matches/*.

    The asset hash is computed from the uploaded bytes, never taken from the client, and an existing
    manifest id is not overwritten.
    """
    resolver = get_resolver()
    if resolver.get_manifest(manifest_id) is not None:
        raise HTTPException(status_code=409, detail="manifest id already exists")
    data = await file.read()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported image") from exc
    manifest = Manifest(
        manifest_id=manifest_id,
        asset_sha256=hashlib.sha256(data).hexdigest(),
        created_at="2026-06-25T00:00:00Z",
        system_provenance={"model": model},
        soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=watermark_id)],
    )
    resolver.register(manifest, image, watermark_id)
    return {"manifestId": manifest_id}


@router.post("/matches/byContent", response_model=SoftBindingQueryResult)
async def matches_by_content(file: UploadFile = File(...)) -> SoftBindingQueryResult:
    image = await _read_image(file)
    return get_resolver().resolve_by_content(image)


@router.get("/matches/byBinding", response_model=SoftBindingQueryResult)
async def matches_by_binding(alg: str, value: str) -> SoftBindingQueryResult:
    return get_resolver().resolve_by_binding(alg, value)


@router.get("/manifests/{manifest_id:path}", response_model=Manifest)
async def get_manifest(manifest_id: str) -> Manifest:
    manifest = get_resolver().get_manifest(manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="manifest not found")
    return manifest.redacted()  # SB 942 split: withhold personal provenance on read
