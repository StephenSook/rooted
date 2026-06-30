"""The vendor-neutral C2PA Soft Binding Resolution API (C2PA v2.4 route shapes).

The resolver uses the Postgres index when DATABASE_URL is set and the in-memory index otherwise, so
the recovery path runs end to end with or without credentials. The B2 store and the TrustMark
watermarker swap in at the same seams. The /ingest route is a convenience for the demo; the spec
query routes are byContent and byBinding.

Response models inherit from CamelModel, so the HTTP surface (and the generated OpenAPI) emit
camelCase aliases, while model_dump() stays snake_case for storage, canonical hashing, and signing.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import ipaddress
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from rooted_provenance.audio import AudioDecodeError, audio_to_image
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
from rooted_provenance.signing import (
    generate_keypair,
    load_private_key,
    public_key_bytes,
    verify_manifest,
)
from rooted_provenance.video import VideoDecodeError, video_frames
from rooted_provenance.watermark import FakeWatermarker, Watermarker
from rooted_storage.storage import Storage

logger = logging.getLogger(__name__)

# Bound the decoder's pixel budget so a small, highly compressible upload cannot expand into
# hundreds of MB on decode. Pillow only raises DecompressionBombError above 2x this; the 1x-2x band
# merely warns and would otherwise proceed, so _decode_image promotes that warning to an error too.
Image.MAX_IMAGE_PIXELS = 64_000_000

router = APIRouter()

# The recovery resolver. It uses PostgresIndex when DATABASE_URL is set, so the live recovery path
# runs on Postgres, and falls back to the in-memory index otherwise so the demo is credential-free.
# Built lazily on first use and overridable for tests (set_resolver). The watermarker stays the fake
# until TrustMark is wired; recovery uses the PDQ path either way.
_resolver: Resolver | None = None

# The transparency log the recovery server serves. /ingest appends each manifest's canonical hash as
# a leaf; the log owns the manifest-id -> leaf-index map. With DATABASE_URL it persists the ordered
# leaves to Postgres and rebuilds the tree on startup, so proofs survive a restart; otherwise it is
# the credential-free in-memory log. Built lazily and overridable for tests (set_log).
_log: TransparencyLog | None = None


def _load_signing_key() -> tuple[Ed25519PrivateKey, str]:
    """The Ed25519 key that signs checkpoints, with its provenance.

    A configured key loads from ED25519_PRIVATE_KEY_HEX (the raw 32-byte key as hex, a single-line
    env-friendly value) or ED25519_PRIVATE_KEY_PATH (a raw-key file). Pinning a stable key across
    redeploys keeps the public key and every inclusion proof valid through the judging window. When
    none is set we fail closed if a key is required (ROOTED_REQUIRE_SIGNING_KEY=1 or
    APP_ENV=production), so a production tamper-evidence anchor is never silently signed by a
    throwaway key. Otherwise an ephemeral key is generated, and key_source reports "ephemeral" so a
    dev/CI key is never mistaken for a trust anchor.
    """
    hex_key = os.environ.get("ED25519_PRIVATE_KEY_HEX")
    if hex_key:
        return load_private_key(bytes.fromhex(hex_key.strip())), "configured"
    path = os.environ.get("ED25519_PRIVATE_KEY_PATH")
    if path:
        return load_private_key(Path(path).read_bytes()), "configured"
    require = os.environ.get("ROOTED_REQUIRE_SIGNING_KEY") == "1" or (
        os.environ.get("APP_ENV") == "production"
    )
    if require:
        raise RuntimeError(
            "ED25519_PRIVATE_KEY_HEX or ED25519_PRIVATE_KEY_PATH is required when "
            "ROOTED_REQUIRE_SIGNING_KEY=1 or APP_ENV=production; refusing to sign checkpoints "
            "with an ephemeral key"
        )
    priv, _pub = generate_keypair()
    return priv, "ephemeral"


_signing_key, _key_source = _load_signing_key()


def _public_key_hex() -> str:
    return str(public_key_bytes(_signing_key.public_key()).hex())


def _signed_head() -> MerkleCheckpoint:
    """The signed tree head for the current log state. The epoch IS the tree size, so the same tree
    state always yields the same signed checkpoint (a GET is idempotent and reproducible)."""
    log = get_log()
    return log.checkpoint(log.size, _signing_key, datetime.now(UTC).isoformat())


def current_checkpoint() -> MerkleCheckpoint:
    """The current signed tree head (public accessor for the checkpoint-lock surface)."""
    return _signed_head()


def signing_public_key() -> Ed25519PublicKey:
    """The Ed25519 public key that verifies checkpoint signatures."""
    return _signing_key.public_key()


def key_source() -> str:
    """Whether the signing key is a configured anchor or an ephemeral dev key."""
    return _key_source


def _psycopg_url(url: str) -> str:
    """Normalize a SQLAlchemy/async-style URL to the libpq form psycopg expects. DATABASE_URL is
    written for the app's async driver (postgresql+asyncpg://); the sync index needs postgresql://."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _make_watermarker() -> Watermarker:
    """The recovery watermarker. Defaults to the fake, so recovery runs on the PDQ path and the
    deploy stays lean. Set ROOTED_REAL_WATERMARK=1 (and install the `watermark` extra) to use the
    real TrustMark variant P; that import pulls torch, so it is opt-in. If the extra is missing we
    fall back to the fake rather than failing the resolver."""
    if os.environ.get("ROOTED_REAL_WATERMARK") == "1":
        try:
            from rooted_provenance.watermark import TrustMarkWatermarker

            return TrustMarkWatermarker()  # __init__ imports trustmark -> ImportError if absent
        except ImportError:
            # The operator explicitly opted into real watermarking; do not silently degrade to the
            # no-op fake. Warn loudly so the missing `watermark` extra is visible in the logs.
            logger.warning(
                "ROOTED_REAL_WATERMARK=1 but the trustmark extra is not importable; "
                "falling back to the no-op FakeWatermarker (install the `watermark` extra)"
            )
    return FakeWatermarker()


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
    return Resolver(index, _make_watermarker())


# Locks for the lazy singletons: routes run in a threadpool (run_in_threadpool), so two concurrent
# first requests could otherwise both build a resolver/log (one pool then leaks). The startup
# lifespan pre-builds both, so this is belt-and-suspenders for paths that skip the lifespan.
_resolver_lock = threading.Lock()
_log_lock = threading.Lock()


def get_resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = _make_resolver()
    return _resolver


def set_resolver(resolver: Resolver | None) -> None:
    """Override the resolver, or reset to None to rebuild on next use. Tests use this to point the
    live API at a Postgres-backed resolver."""
    global _resolver
    _resolver = resolver


# A separate resolver for AUDIO so an audio fingerprint can never cross-match an image one: each
# modality keeps its own index. The audio "image" is a spectrogram (see rooted_provenance.audio), so
# the same Resolver/PDQ/Hamming machinery recovers audio with no new matcher. Built lazily.
_audio_resolver: Resolver | None = None
_audio_resolver_lock = threading.Lock()


def get_audio_resolver() -> Resolver:
    global _audio_resolver
    if _audio_resolver is None:
        with _audio_resolver_lock:
            if _audio_resolver is None:
                _audio_resolver = Resolver(InMemoryIndex(), FakeWatermarker())
    return _audio_resolver


def set_audio_resolver(resolver: Resolver | None) -> None:
    """Override the audio resolver, or reset to None to rebuild on next use (tests)."""
    global _audio_resolver
    _audio_resolver = resolver


# ffmpeg decode is the one expensive, unauthenticated step in the audio path. Bound how many run at
# once so a burst of audio uploads cannot pin the whole threadpool (and starve every other blocking
# route) for the decode window; excess requests await a token rather than holding a worker.
_audio_decode_limiter = anyio.CapacityLimiter(
    int(os.environ.get("ROOTED_AUDIO_DECODE_CONCURRENCY", "3"))
)


# A separate resolver for VIDEO (same reasoning as audio): a video registers one PDQ per sampled
# frame in its own index, so video frames never cross-match an image or audio asset. Built lazily.
_video_resolver: Resolver | None = None
_video_resolver_lock = threading.Lock()
_video_decode_limiter = anyio.CapacityLimiter(
    int(os.environ.get("ROOTED_VIDEO_DECODE_CONCURRENCY", "2"))
)


def get_video_resolver() -> Resolver:
    global _video_resolver
    if _video_resolver is None:
        with _video_resolver_lock:
            if _video_resolver is None:
                _video_resolver = Resolver(InMemoryIndex(), FakeWatermarker())
    return _video_resolver


def set_video_resolver(resolver: Resolver | None) -> None:
    """Override the video resolver, or reset to None to rebuild on next use (tests)."""
    global _video_resolver
    _video_resolver = resolver


def _lookup_manifest(manifest_id: str) -> Manifest | None:
    """Find a manifest across all modality resolvers (image, audio, video). They keep separate
    indexes so fingerprints never cross-match, but a manifest recovered from any of them must still
    be fetchable by id (for GET /manifests and the inclusion proof)."""
    for resolver in (get_resolver(), get_audio_resolver(), get_video_resolver()):
        manifest = resolver.get_manifest(manifest_id)
        if manifest is not None:
            return manifest
    return None


def _make_log() -> TransparencyLog:
    """Build the transparency log. DATABASE_URL persists the ordered leaves to Postgres (so proofs
    survive a restart and a second instance); without it the leaves live in memory."""
    url = os.environ.get("DATABASE_URL")
    if url:
        from rooted_storage.transparency import PostgresTransparencyStore

        return TransparencyLog(PostgresTransparencyStore(_psycopg_url(url)))
    return TransparencyLog()


def get_log() -> TransparencyLog:
    global _log
    if _log is None:
        with _log_lock:
            if _log is None:
                _log = _make_log()
    return _log


def set_log(log: TransparencyLog | None) -> None:
    """Override the transparency log, or reset to None to rebuild on next use (tests)."""
    global _log
    _log = log


# The B2 object store. When the B2 credentials are present (B2_KEY_ID, B2_APP_KEY, B2_BUCKET_DEV),
# Rooted stores assets, manifests, and signatures content-addressably on Backblaze B2; without them
# the demo runs in-memory and never touches B2. Built lazily, overridable for tests (set_storage).
_storage: Storage | None = None
_storage_built = False
_storage_lock = threading.Lock()


def _make_storage() -> Storage | None:
    key_id = os.environ.get("B2_KEY_ID")
    app_key = os.environ.get("B2_APP_KEY")
    bucket = os.environ.get("B2_BUCKET_DEV")
    if not (key_id and app_key and bucket):
        return None
    from rooted_storage.storage import B2Storage

    return B2Storage(key_id, app_key, bucket)


def get_storage() -> Storage | None:
    global _storage, _storage_built
    if not _storage_built:
        with _storage_lock:
            if not _storage_built:
                _storage = _make_storage()
                _storage_built = True
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Override the object store (tests). Marks it built so get_storage returns it as-is."""
    global _storage, _storage_built
    _storage = storage
    _storage_built = True


# A SEPARATE Object-Lock-enabled bucket for the signed Merkle checkpoints. Kept distinct from the
# iteration bucket (B2_BUCKET_DEV) because a fileLock-enabled bucket's compliance-retained writes
# are immutable: you iterate against the dev bucket and write the audit anchor to the locked one.
# When B2_BUCKET_LOCKED is unset, the checkpoint demo falls back to the in-memory model, labeled.
_locked_storage: Storage | None = None
_locked_storage_built = False


def _make_locked_storage() -> Storage | None:
    key_id = os.environ.get("B2_KEY_ID")
    app_key = os.environ.get("B2_APP_KEY")
    bucket = os.environ.get("B2_BUCKET_LOCKED")
    if not (key_id and app_key and bucket):
        return None
    from rooted_storage.storage import B2Storage

    return B2Storage(key_id, app_key, bucket)


def get_locked_storage() -> Storage | None:
    """The Object-Lock checkpoint bucket, or None when B2_BUCKET_LOCKED is not configured."""
    global _locked_storage, _locked_storage_built
    if not _locked_storage_built:
        with _storage_lock:
            if not _locked_storage_built:
                _locked_storage = _make_locked_storage()
                _locked_storage_built = True
    return _locked_storage


def set_locked_storage(storage: Storage | None) -> None:
    """Override the locked checkpoint store (tests)."""
    global _locked_storage, _locked_storage_built
    _locked_storage = storage
    _locked_storage_built = True


_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # cap on an uploaded asset

# Bound concurrent image decodes like audio/video are bounded: _decode_image can materialize up to
# ~192 MB of RGB (Image.MAX_IMAGE_PIXELS), so an unbounded burst on the public byContent/ingest
# routes could OOM the lean instance. Excess requests await a token rather than pinning a worker.
_image_decode_limiter = anyio.CapacityLimiter(
    int(os.environ.get("ROOTED_IMAGE_DECODE_CONCURRENCY", "4"))
)


def _decode_image(data: bytes) -> Image.Image:
    """Decode uploaded bytes to an RGB image, failing closed on untrusted input. An oversized upload
    is rejected (413) before decode; anything that is not a decodable image, including a
    decompression bomb whose header declares a huge size, becomes a 415 rather than a 500.

    The bomb check reads the declared pixel size from the header (Image.open is lazy) and rejects
    before materializing pixels with .convert(). This is an explicit, thread-safe check: it does not
    touch the process-global warnings filters, so it is safe under the multi-thread decode pool
    (mutating warnings filters there could let a concurrent decode slip a bomb through).
    DecompressionBombError is not an OSError, so it is caught explicitly."""
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="uploaded asset too large")
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported image") from exc
    max_pixels = Image.MAX_IMAGE_PIXELS
    if max_pixels is not None and width * height > max_pixels:
        raise HTTPException(status_code=415, detail="invalid or unsupported image")
    try:
        return img.convert("RGB")
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported image") from exc


async def _read_image(file: UploadFile) -> Image.Image:
    # Offload the CPU/memory-bound decode off the event loop so a large (or bomb) image cannot stall
    # request handling for every other client, and bound concurrency (like the audio/video decoders)
    # so a burst of large decodes cannot pin the threadpool or OOM the instance.
    data = await file.read()
    return await anyio.to_thread.run_sync(_decode_image, data, limiter=_image_decode_limiter)


def _check_ingest_auth(provided: str | None) -> None:
    """Gate the /ingest write path. ROOTED_INGEST_KEY, when set, must match the X-Ingest-Key header
    (constant-time). When it is unset, ingest is allowed in the credential-free demo but fails
    closed in production (APP_ENV=production or ROOTED_REQUIRE_INGEST_KEY=1), mirroring the
    signing-key fail-closed pattern, so a public deploy never exposes an unauthenticated writer."""
    expected = os.environ.get("ROOTED_INGEST_KEY")
    if expected:
        # Compare as bytes: hmac.compare_digest raises on a non-ASCII str, which would turn a
        # crafted header into a 500 instead of a clean 401. Encoding sidesteps that (still fails
        # closed regardless), keeping the comparison constant-time.
        ok = provided is not None and hmac.compare_digest(
            provided.encode("utf-8"), expected.encode("utf-8")
        )
        if not ok:
            raise HTTPException(status_code=401, detail="invalid or missing ingest credential")
        return
    require = (
        os.environ.get("ROOTED_REQUIRE_INGEST_KEY") == "1"
        or os.environ.get("APP_ENV") == "production"
    )
    if require:
        raise HTTPException(
            status_code=503, detail="ingest is disabled: ROOTED_INGEST_KEY is not configured"
        )


# manifest_id and watermark_id flow into content-addressable storage keys (manifests/<id>.json,
# signatures/<id>.cose) and the soft binding; constrain them to a safe charset at the boundary so a
# value with '/' or '..' can never shape a key (the key builders only sanitize ':').
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9:_.\-]{1,256}$")


def _validate_id(value: str, field: str) -> None:
    if not _SAFE_ID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"invalid {field}")


@router.get("/services/supportedAlgorithms", response_model=SupportedAlgorithms)
async def supported_algorithms() -> SupportedAlgorithms:
    # Advertise the configured federation peers so the SBR network is discoverable from the spec
    # service-description route.
    return SupportedAlgorithms(peers=_peer_urls())


@router.post(
    "/ingest",
    responses={
        400: {"description": "malformed request body"},
        401: {"description": "invalid or missing ingest credential"},
        409: {"description": "manifest id or watermark id already exists"},
        413: {"description": "uploaded asset too large"},
        415: {"description": "invalid or unsupported image"},
        503: {"description": "ingest disabled (no ingest key configured in production)"},
    },
)
async def ingest(
    file: UploadFile = File(...),
    manifest_id: str = Form(...),
    watermark_id: str = Form(...),
    model: str = Form("unknown"),
    x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key"),
) -> dict[str, str]:
    """Trusted generation-side ingest, gated by ROOTED_INGEST_KEY (the X-Ingest-Key header; required
    in production). The public query surface is /matches/*.

    The asset hash is computed from the uploaded bytes, never taken from the client. An existing
    manifest id is not overwritten (409), and a watermark id already bound to a manifest cannot be
    re-pointed (409), so a second ingest cannot poison recovery for a victim's watermark.
    """
    _check_ingest_auth(x_ingest_key)
    _validate_id(manifest_id, "manifest_id")
    _validate_id(watermark_id, "watermark_id")
    resolver = get_resolver()
    # The index calls are synchronous (psycopg); offload them so a DB round-trip never blocks the
    # event loop and serializes concurrent requests.
    if await run_in_threadpool(resolver.get_manifest, manifest_id) is not None:
        raise HTTPException(status_code=409, detail="manifest id already exists")
    existing = await run_in_threadpool(resolver.resolve_by_binding, ALG_TRUSTMARK_P, watermark_id)
    if existing.matches:
        raise HTTPException(status_code=409, detail="watermark id already bound to a manifest")
    data = await file.read()
    image = await anyio.to_thread.run_sync(_decode_image, data, limiter=_image_decode_limiter)
    manifest = Manifest(
        manifest_id=manifest_id,
        asset_sha256=hashlib.sha256(data).hexdigest(),
        created_at="2026-06-25T00:00:00Z",
        system_provenance={"model": model},
        soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=watermark_id)],
    )
    await run_in_threadpool(resolver.register, manifest, image, watermark_id)
    try:
        await run_in_threadpool(get_log().append, manifest_id, manifest.canonical_hash())
    except Exception as exc:  # noqa: BLE001 - surface ANY append failure clearly, not an opaque 500
        # The manifest is registered (recoverable) but not in the transparency log, so it has no
        # inclusion proof yet. Tell the caller exactly that. (A single cross-store transaction is
        # the documented production follow-up.)
        logger.error("manifest %s registered but transparency append failed: %s", manifest_id, exc)
        raise HTTPException(
            status_code=500, detail="manifest registered but transparency log append failed"
        ) from exc
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
    # resolve_by_content does blocking DB work and CPU-bound PDQ; offload it off the event loop.
    return await run_in_threadpool(get_resolver().resolve_by_content, image)


@router.post(
    "/matches/byAudioContent",
    response_model=SoftBindingQueryResult,
    include_in_schema=False,
    responses={
        413: {"description": "uploaded asset too large"},
        415: {"description": "invalid or unsupported audio"},
    },
)
async def matches_by_audio_content(file: UploadFile = File(...)) -> SoftBindingQueryResult:
    """Recover an audio asset's manifest by its perceptual audio fingerprint, the audio analog of
    /matches/byContent. The asset is decoded and reduced to a spectrogram image, then matched in the
    audio index. It is outside the image-oriented SBR spec contract (so it is unlisted), and
    the audio fingerprint stays internal like PDQ."""
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="uploaded asset too large")
    try:
        # Bound concurrent ffmpeg decodes (the limiter), separately from the rest of the threadpool.
        image = await anyio.to_thread.run_sync(audio_to_image, data, limiter=_audio_decode_limiter)
    except AudioDecodeError as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported audio") from exc
    return await run_in_threadpool(get_audio_resolver().resolve_by_content, image)


# Larger than images/audio (25 MB) but still bounded: a re-encoded demo clip is a few MB, and the
# per-frame decode is independently size-bounded (see rooted_provenance.video), so this caps the
# body buffer without needing a multi-GB allowance.
_MAX_VIDEO_UPLOAD_BYTES = 32 * 1024 * 1024


def _resolve_video_frames(frames: list[Image.Image]) -> SoftBindingQueryResult:
    """Match each sampled frame against the video index; the first frame that resolves wins. Any one
    frame match recovers the video, which is what survives a re-encode that perturbs some frames."""
    resolver = get_video_resolver()
    for frame in frames:
        result = resolver.resolve_by_content(frame)
        if result.matches:
            return result
    return SoftBindingQueryResult()


@router.post(
    "/matches/byVideoContent",
    response_model=SoftBindingQueryResult,
    include_in_schema=False,
    responses={
        413: {"description": "uploaded asset too large"},
        415: {"description": "invalid or unsupported video"},
    },
)
async def matches_by_video_content(file: UploadFile = File(...)) -> SoftBindingQueryResult:
    """Recover a video's manifest by per-keyframe PDQ. The video is decoded to sampled frames; the
    first frame whose fingerprint matches the video index recovers the manifest. Outside the
    image-oriented SBR spec contract (so it is unlisted); the per-frame fingerprint is internal."""
    data = await file.read()
    if len(data) > _MAX_VIDEO_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="uploaded asset too large")
    try:
        frames = await anyio.to_thread.run_sync(video_frames, data, limiter=_video_decode_limiter)
    except VideoDecodeError as exc:
        raise HTTPException(status_code=415, detail="invalid or unsupported video") from exc
    return await run_in_threadpool(_resolve_video_frames, frames)


@router.get("/matches/byBinding", response_model=SoftBindingQueryResult)
async def matches_by_binding(alg: str, value: str) -> SoftBindingQueryResult:
    return await run_in_threadpool(get_resolver().resolve_by_binding, alg, value)


# --- Federation: forward a soft-binding query to peer resolvers on a local miss ----------------
_MAX_PEERS = 5


def _peer_is_safe(url: str) -> bool:
    """A configured peer is usable only if it is a well-formed http(s) URL, and in production only
    if it is https and not a private/loopback/link-local host. Peers come from ROOTED_SBR_PEERS
    (operator-set), never from a request, so this guards an operator misconfiguration that could
    point a forward at an internal service, not an attacker-supplied URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if os.environ.get("APP_ENV") != "production":
        return True
    if parsed.scheme != "https":
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return parsed.hostname.lower() != "localhost"


def _peer_urls() -> list[str]:
    """The configured, validated federation peers (at most _MAX_PEERS), read fresh so a changed
    ROOTED_SBR_PEERS is picked up without a restart (and tests can set it per-case)."""
    raw = os.environ.get("ROOTED_SBR_PEERS", "")
    peers = [u.strip() for u in raw.split(",") if u.strip() and _peer_is_safe(u.strip())]
    return peers[:_MAX_PEERS]


def _peer_client() -> httpx.AsyncClient:
    """The HTTP client used to reach federation peers. A seam so tests can inject a MockTransport
    without patching the global httpx (which the app's own clients also use)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))


async def _forward_to_peers(alg: str, value: str) -> SoftBindingQueryResult:
    """Forward a soft-binding query to each configured peer SBR node until one recovers the
    manifest, labeling it with the recovering peer. One bad peer never breaks the query: each error
    is caught and the next peer tried. No peer ever sees the asset, only the {alg, value}."""
    peers = _peer_urls()
    if not peers:
        return SoftBindingQueryResult(matches=[])
    async with _peer_client() as client:
        for base in peers:
            try:
                resp = await client.get(
                    f"{base.rstrip('/')}/matches/byBinding", params={"alg": alg, "value": value}
                )
                resp.raise_for_status()
                result = SoftBindingQueryResult.model_validate(resp.json())
            except (httpx.HTTPError, ValueError) as exc:  # network error or a malformed peer body
                logger.warning("federation: peer %s failed: %s", base, exc)
                continue
            if result.matches:
                for match in result.matches:
                    match.endpoint = base  # label which peer recovered it
                return result
    return SoftBindingQueryResult(matches=[])


@router.get(
    "/matches/byBinding/federated",
    response_model=SoftBindingQueryResult,
    include_in_schema=False,
)
async def matches_by_binding_federated(alg: str, value: str) -> SoftBindingQueryResult:
    """Federated SBR: resolve a soft binding locally; on a local miss, forward the {alg, value}
    query to the configured peer resolvers and return the first peer's recovered manifest, labeled
    with its endpoint. Recovery becomes an open, vendor-neutral network, not one repository."""
    local = await run_in_threadpool(get_resolver().resolve_by_binding, alg, value)
    if local.matches:
        return local
    return await _forward_to_peers(alg, value)


class FederationStatus(CamelModel):
    """The federation surface: whether peer-forwarding is enabled and which peers are configured. A
    live cross-node recovery needs a second resolver node with a complementary index; the forwarding
    mechanism itself is wired and tested here."""

    enabled: bool
    peers: list[str]
    note: str


@router.get("/demo/federation", response_model=FederationStatus, include_in_schema=False)
async def demo_federation() -> FederationStatus:
    peers = _peer_urls()
    note = (
        "On a local miss the resolver forwards the soft-binding query to these peer SBR nodes and "
        "returns the first peer's recovered manifest, labeled with its endpoint. A live cross-node "
        "hit needs a second node with a complementary index."
        if peers
        else "No peers configured. Set ROOTED_SBR_PEERS to other SBR resolver URLs to federate "
        "recovery across an open, vendor-neutral network."
    )
    return FederationStatus(enabled=bool(peers), peers=peers, note=note)


@router.get(
    "/manifests/{manifest_id:path}",
    response_model=Manifest,
    responses={404: {"description": "manifest not found"}},
)
async def get_manifest(manifest_id: str) -> Manifest:
    manifest = await run_in_threadpool(_lookup_manifest, manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="manifest not found")
    return manifest.redacted()  # SB 942 split: withhold personal provenance on read


class VerifyRequest(CamelModel):
    """A manifest plus a COSE_Sign1 signature (base64) to check against the checkpoint key."""

    manifest: Manifest
    signature_b64: str


class VerifyResponse(CamelModel):
    """Whether the signature covers this exact manifest, with the key it was checked against."""

    signature_valid: bool
    public_key_hex: str
    key_source: str


@router.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    """Verify a manifest against a COSE signature using the server's checkpoint public key. Changing
    ANY signed field (manifest id, asset hash, created-at, system provenance) makes this False, so a
    client can prove tamper-evidence live. Adversarial input (bad base64, malformed COSE, a tampered
    manifest) collapses to signatureValid=false, never a 500."""
    try:
        cose = base64.b64decode(req.signature_b64, validate=True)
    except (binascii.Error, ValueError):
        valid = False
    else:
        valid = await run_in_threadpool(
            verify_manifest, cose, req.manifest, _signing_key.public_key()
        )
    return VerifyResponse(
        signature_valid=valid, public_key_hex=_public_key_hex(), key_source=_key_source
    )


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


class ConsistencyProofResponse(CamelModel):
    """A self-contained, independently-verifiable Merkle consistency proof: the current log is an
    append-only extension of the tree at prior_size (no earlier leaf altered or removed). The client
    resolves the serialized proof from prior_root_hash to root_hash and confirms it, then binds
    root_hash to the embedded signed checkpoint. server_verified is only this server's own check.
    This is the same append-only guarantee Certificate Transparency publishes."""

    prior_size: int
    prior_root_hash: str
    tree_size: int
    root_hash: str
    proof: dict[str, Any]
    checkpoint: MerkleCheckpoint
    public_key_hex: str
    key_source: str
    server_verified: bool


class LogEntry(CamelModel):
    """One append-ordered transparency-log leaf."""

    leaf_index: int
    manifest_id: str
    leaf_hash: str


class LogResponse(CamelModel):
    """The ordered transparency-log leaves with the current tree head, for auditing and display."""

    entries: list[LogEntry]
    tree_size: int
    root_hash: str


@router.get("/transparency/log", response_model=LogResponse)
async def transparency_log() -> LogResponse:
    """The append-ordered log leaves plus the current root. A transparency log is meant to be
    auditable, so the entries are public; this also feeds the Merkle explorer."""
    # One snapshot: entries, size, and root read in a single synchronous pass in the threadpool,
    # so the leaf list, the tree size, and the root cannot disagree under a concurrent append.
    rows, size, root = await run_in_threadpool(get_log().snapshot)
    return LogResponse(
        entries=[LogEntry(leaf_index=i, manifest_id=m, leaf_hash=h) for i, m, h in rows],
        tree_size=size,
        root_hash=root.hex(),
    )


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
    log = get_log()
    index = log.index_for(manifest_id)
    manifest = await run_in_threadpool(_lookup_manifest, manifest_id)
    if index is None or manifest is None:
        raise HTTPException(status_code=404, detail="manifest not in transparency log")
    # signed_proof computes the proof, the root, and the signed checkpoint under one lock, so the
    # proof's root and the checkpoint's root describe the same tree state (no divergence under a
    # concurrent append) and the CPU-bound proof runs off the event loop.
    size, root, proof, checkpoint, verified = await run_in_threadpool(
        log.signed_proof, index, _signing_key, datetime.now(UTC).isoformat()
    )
    return InclusionProofResponse(
        manifest_id=manifest_id,
        # index is the pymerkle 1-based position (used for prove/verify); the response field is
        # 0-based to agree with GET /transparency/log, so a client can cross-reference the two.
        leaf_index=index - 1,
        leaf_hash=manifest.canonical_hash(),
        tree_size=size,
        root_hash=root.hex(),
        proof=proof.serialize(),
        checkpoint=checkpoint,
        public_key_hex=_public_key_hex(),
        key_source=_key_source,
        server_verified=verified,
    )


@router.get(
    "/transparency/consistency/{prior_size}",
    response_model=ConsistencyProofResponse,
    responses={404: {"description": "no such prior tree size (must be 1..current tree size)"}},
)
async def transparency_consistency(prior_size: int) -> ConsistencyProofResponse:
    """A Certificate-Transparency-style consistency proof that the log only appended (never altered
    or removed a leaf) between an earlier published tree size and the current head, pinned to the
    signed current checkpoint. This is the append-only guarantee that makes the ledger auditable."""
    # signed_consistency computes the two roots, the proof, and the signed checkpoint under one lock
    # (a consistent snapshot) off the event loop. A prior_size outside 1..current is a state that
    # never existed in the log, so 404 (not 400): that historical tree size is simply not there.
    try:
        psize, proot, size, root, proof, checkpoint, verified = await run_in_threadpool(
            get_log().signed_consistency, prior_size, _signing_key, datetime.now(UTC).isoformat()
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ConsistencyProofResponse(
        prior_size=psize,
        prior_root_hash=proot.hex(),
        tree_size=size,
        root_hash=root.hex(),
        proof=proof.serialize(),
        checkpoint=checkpoint,
        public_key_hex=_public_key_hex(),
        key_source=_key_source,
        server_verified=verified,
    )
