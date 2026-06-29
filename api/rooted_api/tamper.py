"""Tamper-diff forensics: when a manifest is altered, recover the AUTHENTIC one from the registry
and show WHICH signed field changed, not just a binary pass/fail.

The COSE signature (over the canonical payload: manifest id, asset sha256, created-at, system
provenance) proves tamper; the diff makes it forensic: the registry's authentic value next to the
submitted (altered) value, per signed field. Degrades honestly (falls back to the demo manifest if
the registry has no entry), never 500.
"""

from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from rooted_provenance.models import CamelModel, Manifest
from rooted_provenance.signing import verify_manifest

router = APIRouter()
logger = logging.getLogger(__name__)


class TamperDiffRequest(CamelModel):
    manifest: Manifest
    signature_b64: str


class FieldDiff(CamelModel):
    field: str
    authentic: str
    submitted: str
    changed: bool


class TamperDiffResponse(CamelModel):
    signature_valid: bool
    tampered: bool
    authentic_source: str  # "registry" | "demo"
    fields: list[FieldDiff]


def _signed_fields(m: Manifest) -> dict[str, str]:
    """The fields the signature covers (the canonical payload), flattened for a field-level diff."""
    fields: dict[str, str] = {
        "manifest_id": m.manifest_id,
        "asset_sha256": m.asset_sha256,
        "created_at": m.created_at,
    }
    for key, value in (m.system_provenance or {}).items():
        fields[f"system_provenance.{key}"] = str(value)
    return fields


@router.post("/demo/tamper-diff", response_model=TamperDiffResponse, include_in_schema=False)
async def tamper_diff(req: TamperDiffRequest) -> TamperDiffResponse:
    """Check the submitted manifest's signature, recover the authentic manifest from the registry,
    and return a field-level diff over the signed fields. tampered is true when the signature does
    not cover the submission or any signed field differs from the authentic record."""
    from rooted_api import sbr
    from rooted_api.demo import primary_manifest

    submitted = req.manifest
    try:
        cose = base64.b64decode(req.signature_b64, validate=True)
        signature_valid = await run_in_threadpool(
            verify_manifest, cose, submitted, sbr.signing_public_key()
        )
    except (binascii.Error, ValueError):
        signature_valid = False

    authentic = await run_in_threadpool(sbr.get_resolver().get_manifest, submitted.manifest_id)
    source = "registry"
    if authentic is None:
        authentic = primary_manifest()
        source = "demo"

    a = _signed_fields(authentic)
    s = _signed_fields(submitted)
    fields = [
        FieldDiff(
            field=key,
            authentic=a.get(key, "(absent)"),
            submitted=s.get(key, "(absent)"),
            changed=a.get(key) != s.get(key),
        )
        for key in sorted(set(a) | set(s))
    ]
    tampered = (not signature_valid) or any(f.changed for f in fields)
    return TamperDiffResponse(
        signature_valid=signature_valid,
        tampered=tampered,
        authentic_source=source,
        fields=fields,
    )
