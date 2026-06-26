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
import os
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.models import (
    ALG_TRUSTMARK_P,
    Manifest,
    MerkleCheckpoint,
    SoftBinding,
    SoftBindingQueryResult,
    SupportedAlgorithms,
)
from rooted_provenance.resolver import InMemoryIndex, Resolver
from rooted_provenance.signing import generate_keypair, load_private_key, public_key_bytes
from rooted_provenance.watermark import FakeWatermarker

router = APIRouter()

# Scaffold singleton. Real wiring: Resolver(PostgresIndex(...), TrustMarkWatermarker()).
_resolver = Resolver(InMemoryIndex(), FakeWatermarker())

# The transparency log the recovery server serves. /ingest appends each manifest's canonical hash
# as a leaf; _leaf_index maps a manifest id to its 1-based leaf so an inclusion proof can be cut.
# In production this is a persistent tree with signed checkpoints under B2 Object Lock; the
# in-memory tree keeps the demo credential-free and swaps at the same seam as the resolver above.
_log = TransparencyLog()
_leaf_index: dict[str, int] = {}
_epoch = count(1)


def _load_signing_key() -> Ed25519PrivateKey:
    """The Ed25519 key that signs checkpoints. Loaded from ED25519_PRIVATE_KEY_PATH in production;
    an ephemeral key is generated otherwise, so a dev or CI run still produces verifiable heads."""
    path = os.environ.get("ED25519_PRIVATE_KEY_PATH")
    if path:
        return load_private_key(Path(path).read_bytes())
    priv, _pub = generate_keypair()
    return priv


_signing_key = _load_signing_key()


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
    _leaf_index[manifest_id] = _log.append(manifest.canonical_hash())
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


class CheckpointResponse(BaseModel):
    """A signed Merkle tree head plus the public key to verify it independently."""

    checkpoint: MerkleCheckpoint
    public_key_hex: str


class InclusionProofResponse(BaseModel):
    """An inclusion proof that a manifest's leaf is in the current tree head."""

    manifest_id: str
    leaf_index: int
    tree_size: int
    root_hash: str
    proof: dict[str, Any]
    verified: bool


@router.get("/transparency/checkpoint", response_model=CheckpointResponse)
async def transparency_checkpoint() -> CheckpointResponse:
    """The current signed tree head. The public key travels with it so a client can verify the
    head without trusting this endpoint."""
    cp = _log.checkpoint(next(_epoch), _signing_key, datetime.now(UTC).isoformat())
    public_key_hex = public_key_bytes(_signing_key.public_key()).hex()
    return CheckpointResponse(checkpoint=cp, public_key_hex=public_key_hex)


@router.get("/transparency/proof/{manifest_id:path}", response_model=InclusionProofResponse)
async def transparency_proof(manifest_id: str) -> InclusionProofResponse:
    """An inclusion proof for a manifest that was ingested into the transparency log."""
    index = _leaf_index.get(manifest_id)
    if index is None:
        raise HTTPException(status_code=404, detail="manifest not in transparency log")
    proof = _log.prove_inclusion(index)
    root = _log.root()
    return InclusionProofResponse(
        manifest_id=manifest_id,
        leaf_index=index,
        tree_size=_log.size,
        root_hash=root.hex(),
        proof=proof.serialize(),
        verified=_log.verify_inclusion(index, proof, root),
    )
