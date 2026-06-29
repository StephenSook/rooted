"""Backblaze B2 Event-Notification ingest (the B2 data-orchestration axis).

A Backblaze B2 bucket rule POSTs a signed notification to this webhook when an object is created
under a watched prefix. Rooted verifies the HMAC-SHA256 signature, fetches the new object from B2,
fingerprints it, and registers it for provenance recovery + the transparency log. The result: drop
an asset into B2 and it auto-becomes recoverable. Backblaze B2 is an active part of the pipeline
(event-driven orchestration), not just passive storage.

Activation (one-time): set B2_EVENT_SIGNING_SECRET on the API, then add a B2 Event Notification rule
on the bucket targeting POST <api>/webhooks/b2-event with the same hmacSha256SigningSecret and an
objectNamePrefix (default "ingest/"). Until then the webhook refuses unsigned calls and
/demo/b2-events reports not-configured (honest dual-mode).

Security: the signature is verified (constant-time) BEFORE any work; an unconfigured secret refuses
rather than ingesting unsigned; only ObjectCreated events under the watched prefix are processed;
the fetched object is size-capped and decoded through the shared decompression-bomb guard; ingest is
idempotent (B2 may redeliver) via a content-addressed manifest id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from rooted_provenance.models import ALG_TRUSTMARK_P, CamelModel, Manifest, SoftBinding

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_OBJECT_BYTES = 25 * 1024 * 1024  # cap a fetched object; skip anything larger
_RECENT_MAX = 20
_recent: list[dict[str, Any]] = []
_recent_lock = threading.Lock()


def _watch_prefix() -> str:
    return os.environ.get("B2_EVENT_PREFIX", "ingest/")


def _signing_secret() -> str | None:
    return os.environ.get("B2_EVENT_SIGNING_SECRET") or None


def _verify_signature(raw: bytes, header: str | None, secret: str) -> bool:
    """Constant-time check of the lowercase hex HMAC-SHA256 of the raw body (B2's scheme)."""
    if not header:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


def _record(entry: dict[str, Any]) -> None:
    with _recent_lock:
        _recent.insert(0, entry)
        del _recent[_RECENT_MAX:]


class B2IngestRecord(CamelModel):
    object_key: str
    manifest_id: str
    bucket: str
    size_bytes: int
    ingested_at: str


class B2EventStatus(CamelModel):
    configured: bool
    watch_prefix: str
    count: int
    recent: list[B2IngestRecord]


async def _ingest_events(events: list[Any]) -> tuple[int, int]:
    """Fetch + register each ObjectCreated event under the watched prefix. Returns (ingested,
    skipped). Every per-event failure is caught: one bad object must not fail the whole webhook."""
    from rooted_api import sbr

    storage = sbr.get_storage()
    resolver = sbr.get_resolver()
    prefix = _watch_prefix()
    ingested = 0
    skipped = 0
    for ev in events:
        name = str(ev.get("objectName", "")) if isinstance(ev, dict) else ""
        et = str(ev.get("eventType", "")) if isinstance(ev, dict) else ""
        if not et.startswith("b2:ObjectCreated") or not name.startswith(prefix) or storage is None:
            skipped += 1
            continue
        size = ev.get("objectSize") or 0
        if isinstance(size, int) and size > _MAX_OBJECT_BYTES:
            logger.warning("b2-event: skipping oversized object %s (%d bytes)", name, size)
            skipped += 1
            continue
        try:
            data = await run_in_threadpool(storage.get, name)
        except Exception as exc:  # noqa: BLE001 - a fetch failure must not 500 the webhook
            logger.warning("b2-event: fetch failed for %s: %s", name, exc)
            skipped += 1
            continue
        if len(data) > _MAX_OBJECT_BYTES:
            skipped += 1
            continue
        try:
            image = await anyio.to_thread.run_sync(
                sbr._decode_image, data, limiter=sbr._image_decode_limiter
            )
        except Exception as exc:  # noqa: BLE001 - not a decodable image; skip, never 500
            logger.info("b2-event: %s is not a decodable image (%s); skipping", name, exc)
            skipped += 1
            continue
        sha = hashlib.sha256(data).hexdigest()
        manifest_id = f"urn:c2pa:b2-{sha[:32]}"
        if await run_in_threadpool(resolver.get_manifest, manifest_id) is not None:
            skipped += 1  # idempotent: already registered + recoverable on a redelivery
            continue
        bucket = str(ev.get("bucketName", ""))
        manifest = Manifest(
            manifest_id=manifest_id,
            asset_sha256=sha,
            created_at=datetime.now(UTC).isoformat(),
            system_provenance={"source": "b2-event-ingest", "bucket": bucket, "objectKey": name},
            soft_bindings=[SoftBinding(alg=ALG_TRUSTMARK_P, value=f"B2{sha[:10]}")],
        )
        await run_in_threadpool(resolver.register, manifest, image, f"B2{sha[:10]}")
        try:
            await run_in_threadpool(sbr.get_log().append, manifest_id, manifest.canonical_hash())
        except Exception as exc:  # noqa: BLE001 - registered + recoverable; just no proof yet
            logger.error("b2-event: %s registered but log append failed: %s", manifest_id, exc)
        _record(
            {
                "objectKey": name,
                "manifestId": manifest_id,
                "bucket": bucket,
                "sizeBytes": len(data),
                "ingestedAt": manifest.created_at,
            }
        )
        ingested += 1
    return ingested, skipped


@router.post("/webhooks/b2-event", include_in_schema=False)
async def b2_event(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Bz-Event-Notification-Signature"),
) -> JSONResponse:
    """Receive a Backblaze B2 Event Notification, verify its HMAC signature, and auto-ingest the new
    object(s) for provenance recovery. Refuses unsigned calls; acks B2's connectivity TestEvent."""
    secret = _signing_secret()
    raw = await request.body()
    if secret is None:
        # Not configured: we cannot verify the caller, so we never ingest unsigned. Honest 503.
        return JSONResponse({"status": "not-configured"}, status_code=503)
    if not _verify_signature(raw, x_signature, secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="malformed payload") from exc

    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        if isinstance(payload, dict) and payload.get("eventType") == "b2:TestEvent":
            return JSONResponse({"status": "test-ok"})
        events = []
    if any(isinstance(e, dict) and e.get("eventType") == "b2:TestEvent" for e in events):
        return JSONResponse({"status": "test-ok"})

    ingested, skipped = await _ingest_events(events)
    return JSONResponse({"status": "ok", "ingested": ingested, "skipped": skipped})


@router.get("/demo/b2-events", response_model=B2EventStatus, include_in_schema=False)
async def b2_events_status() -> B2EventStatus:
    """The B2 event-driven ingest surface: whether the webhook is configured, the watched prefix,
    and the most recently auto-ingested objects (each recoverable via /matches/byContent)."""
    with _recent_lock:
        recent = [B2IngestRecord(**r) for r in _recent]
    return B2EventStatus(
        configured=_signing_secret() is not None,
        watch_prefix=_watch_prefix(),
        count=len(recent),
        recent=recent,
    )
