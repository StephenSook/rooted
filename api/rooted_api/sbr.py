"""The vendor-neutral C2PA Soft Binding Resolution API (C2PA v2.4 route shapes).

The resolver uses the Postgres index when DATABASE_URL is set and the in-memory index otherwise, so
the recovery path runs end to end with or without credentials. The B2 store and the TrustMark
watermarker swap in at the same seams. The /ingest route is a convenience for the demo; the spec
query routes are byContent and byBinding.

Response models inherit from CamelModel, so the HTTP surface (and the generated OpenAPI) emit
camelCase aliases, while model_dump() stays snake_case for storage, canonical hashing, and signing.
"""

from __future__ import annotations

import hashlib
import io
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from rooted_provenance.merkle import TransparencyLog
from rooted_provenance.models import (
    ALG_TRUSTMARK_P,
    CamelModel,
    Manifest,
    MerkleCheckpoint,
    SoftBinding,
    SoftBindingQueryResult,
    SupportedAlgorithms,
)
from rooted_provenance.resolver import Index, InMemoryIndex, Resolver
from rooted_provenance.signing import generate_keypair, load_private_key, public_key_bytes
from rooted_provenance.watermark import FakeWatermarker

router = APIRouter()

# The recovery resolver. It uses PostgresIndex when DATABASE_URL is set, so the live recovery path
# runs on Postgres, and falls back to the in-memory index otherwise so the demo is credential-free.
# Built lazily on first use and overridable for tests (set_resolver). The watermarker stays the fake
# until TrustMark is wired; recovery uses the PDQ path either way.
_resolver: Resolver | None = None

# The transparency log the recovery server serves. /ingest appends each manifest's canonical hash
# as a leaf; _leaf_index maps a manifest id to its 1-based leaf so an inclusion proof can be cut.
# In production this is a persistent tree with signed checkpoints under B2 Object Lock; the
# in-memory tree keeps the demo credential-free and swaps at the same seam as the resolver above.
_log = TransparencyLog()
_leaf_index: dict[str, int] = {}


def _load_signing_key() -> tuple[Ed25519PrivateKey, str]:
    """The Ed25519 key that signs checkpoints, with its provenance.

    A configured key loads from ED25519_PRIVATE_KEY_PATH. When none is set we fail closed if a
    key is required (ROOTED_REQUIRE_SIGNING_KEY=1 or APP_ENV=production), so a production
    tamper-evidence anchor is never silently signed by a throwaway key. Otherwise an ephemeral key
    is generated, and key_source reports "ephemeral" so a dev/CI key is never a trust anchor.
    """
    path = os.environ.get("ED25519_PRIVATE_KEY_PATH")
    if path:
        return load_private_key(Path(path).read_bytes()), "configured"
    require = os.environ.get("ROOTED_REQUIRE_SIGNING_KEY") == "1" or (
        os.environ.get("APP_ENV") == "production"
    )
    if require:
        raise RuntimeError(
            "ED25519_PRIVATE_KEY_PATH is required when ROOTED_REQUIRE_SIGNING_KEY=1 "
            "or APP_ENV=production; refusing to sign checkpoints with an ephemeral key"
        )
    priv, _pub = generate_keypair()
    return priv, "ephemeral"


_signing_key, _key_source = _load_signing_key()


def _public_key_hex() -> str:
    return str(public_key_bytes(_signing_key.public_key()).hex())


def _signed_head() -> MerkleCheckpoint:
    """The signed tree head for the current log state. The epoch IS the tree size, so the same tree
    state always yields the same signed checkpoint (a GET is idempotent and reproducible)."""
    return _log.checkpoint(_log.size, _signing_key, datetime.now(UTC).isoformat())


def _psycopg_url(url: str) -> str:
    """Normalize a SQLAlchemy/async-style URL to the libpq form psycopg expects. DATABASE_URL is
    written for the app's async driver (postgresql+asyncpg://); the sync index needs postgresql://."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _make_resolver() -> Resolver:
    """Build the resolver. DATABASE_URL selects the Postgres index (live recovery on Postgres);
    without it the in-memory index keeps the demo credential-free."""
    url = os.environ.get("DATABASE_URL")
    index: Index = InMemoryIndex()
    if url:
        from rooted_storage.index import PostgresIndex

        pg = PostgresIndex(_psycopg_url(url))
        pg.create_schema()
        index = pg
    return Resolver(index, FakeWatermarker())


def get_resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        _resolver = _make_resolver()
    return _resolver


def set_resolver(resolver: Resolver | None) -> None:
    """Override the resolver, or reset to None to rebuild on next use. Tests use this to point the
    live API at a Postgres-backed resolver."""
    global _resolver
    _resolver = resolver


_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # cap on an uploaded asset


def _decode_image(data: bytes) -> Image.Image:
    """Decode uploaded bytes to an RGB image, failing closed on untrusted input. An oversized upload
    is rejected (413) before decode; anything that is not a decodable image, including a
    decompression bomb whose header declares a huge size, becomes a 415 rather than a 500.
    DecompressionBombError is not an OSError, so it must be caught explicitly."""
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="uploaded asset too large")
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported image") from exc


async def _read_image(file: UploadFile) -> Image.Image:
    return _decode_image(await file.read())


@router.get("/services/supportedAlgorithms", response_model=SupportedAlgorithms)
async def supported_algorithms() -> SupportedAlgorithms:
    return SupportedAlgorithms()


@router.post(
    "/ingest",
    responses={
        400: {"description": "malformed request body"},
        409: {"description": "manifest id already exists"},
        413: {"description": "uploaded asset too large"},
        415: {"description": "invalid or unsupported image"},
    },
)
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
    image = _decode_image(data)
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


@router.post(
    "/matches/byContent",
    response_model=SoftBindingQueryResult,
    responses={
        400: {"description": "malformed request body"},
        413: {"description": "uploaded asset too large"},
        415: {"description": "invalid or unsupported image"},
    },
)
async def matches_by_content(file: UploadFile = File(...)) -> SoftBindingQueryResult:
    image = await _read_image(file)
    return get_resolver().resolve_by_content(image)


@router.get("/matches/byBinding", response_model=SoftBindingQueryResult)
async def matches_by_binding(alg: str, value: str) -> SoftBindingQueryResult:
    return get_resolver().resolve_by_binding(alg, value)


@router.get(
    "/manifests/{manifest_id:path}",
    response_model=Manifest,
    responses={404: {"description": "manifest not found"}},
)
async def get_manifest(manifest_id: str) -> Manifest:
    manifest = get_resolver().get_manifest(manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="manifest not found")
    return manifest.redacted()  # SB 942 split: withhold personal provenance on read


class CheckpointResponse(CamelModel):
    """A signed Merkle tree head plus the public key to verify it independently.

    key_source is "configured" for a real loaded key or "ephemeral" for a dev/CI key, so a client
    never mistakes a throwaway key for a trust anchor.
    """

    checkpoint: MerkleCheckpoint
    public_key_hex: str
    key_source: str


class InclusionProofResponse(CamelModel):
    """A self-contained, independently-verifiable inclusion proof.

    The client resolves the serialized proof to a root and confirms that root equals the embedded
    signed checkpoint's root_hash, then verifies the checkpoint signature against public_key_hex.
    server_verified is only the server's own check and is not a substitute for that client-side
    verification. leaf_hash is the manifest's canonical hash (the leaf), unchanged by redaction.
    """

    manifest_id: str
    leaf_index: int
    leaf_hash: str
    tree_size: int
    root_hash: str
    proof: dict[str, Any]
    checkpoint: MerkleCheckpoint
    public_key_hex: str
    key_source: str
    server_verified: bool


@router.get("/transparency/checkpoint", response_model=CheckpointResponse)
async def transparency_checkpoint() -> CheckpointResponse:
    """The current signed tree head. Idempotent: the epoch is the tree size, so re-reading an
    unchanged tree returns the same signed checkpoint. The public key travels with it."""
    return CheckpointResponse(
        checkpoint=_signed_head(), public_key_hex=_public_key_hex(), key_source=_key_source
    )


@router.get(
    "/transparency/proof/{manifest_id:path}",
    response_model=InclusionProofResponse,
    responses={404: {"description": "manifest not in transparency log"}},
)
async def transparency_proof(manifest_id: str) -> InclusionProofResponse:
    """An inclusion proof for a manifest, pinned to a signed checkpoint so the client can bind the
    leaf to a signed tree head without trusting this endpoint."""
    index = _leaf_index.get(manifest_id)
    manifest = get_resolver().get_manifest(manifest_id)
    if index is None or manifest is None:
        raise HTTPException(status_code=404, detail="manifest not in transparency log")
    # No await between these reads, so the proof, the root, and the checkpoint share one tree state.
    size = _log.size
    root = _log.root(size)
    proof = _log.prove_inclusion(index, size)
    return InclusionProofResponse(
        manifest_id=manifest_id,
        leaf_index=index,
        leaf_hash=manifest.canonical_hash(),
        tree_size=size,
        root_hash=root.hex(),
        proof=proof.serialize(),
        checkpoint=_signed_head(),
        public_key_hex=_public_key_hex(),
        key_source=_key_source,
        server_verified=_log.verify_inclusion(index, proof, root),
    )
