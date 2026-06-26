"""C2PA claim wrapping: turn a Rooted manifest into a real, signed Content Credential.

We sign through c2pa-python's from_callback path so the signing key stays in our control and no
timestamp authority is required. ES256 is the most broadly supported C2PA algorithm (c2pa-rs
validates the signing cert against its profile at sign time, so a malformed self-signed cert is
rejected; a conformant test or self-signed chain validates as "Valid"). "Valid" means the signature
checks out, NOT the green "Trusted" state, which requires a Conformance-Program CA. We surface that
distinction honestly rather than hide it.
"""

from __future__ import annotations

import io
import json
from typing import Any

import c2pa
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from .models import ALG_TRUSTMARK_P, Manifest

SOFT_BINDING_LABEL = "com.rooted.soft_binding"


def make_es256_signer(cert_chain_pem: str, private_key_pem: bytes) -> c2pa.Signer:
    """Build a c2pa Signer that signs with the given ES256 (P-256) key, no timestamp authority."""
    key = load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("ES256 signer requires an EC P-256 private key")

    def sign_cb(data: bytes) -> bytes:
        der = key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")  # C2PA wants raw r||s, not DER

    return c2pa.Signer.from_callback(
        sign_cb, c2pa.C2paSigningAlg.ES256, certs=cert_chain_pem, tsa_url=None
    )


def build_manifest_def(
    manifest: Manifest, watermark_id: str, fmt: str = "image/jpeg"
) -> dict[str, Any]:
    """The C2PA manifest definition, carrying the soft-binding pointer and the system provenance."""
    return {
        "claim_generator": "rooted/0.1.0",
        "claim_generator_info": [{"name": "rooted", "version": "0.1.0"}],
        "format": fmt,
        "title": manifest.manifest_id,
        "assertions": [
            {"label": "c2pa.actions", "data": {"actions": [{"action": "c2pa.created"}]}},
            {
                "label": SOFT_BINDING_LABEL,
                "data": {"alg": ALG_TRUSTMARK_P, "value": watermark_id, "scope": "all"},
            },
            {
                "label": "com.rooted.provenance",
                "data": {
                    "manifest_id": manifest.manifest_id,
                    "asset_sha256": manifest.asset_sha256,
                    "system_provenance": manifest.system_provenance,
                },
            },
        ],
    }


def sign_claim(
    signer: c2pa.Signer, image_bytes: bytes, manifest_def: dict[str, Any], fmt: str = "image/jpeg"
) -> bytes:
    """Embed and sign the C2PA manifest into the asset; return the signed asset bytes."""
    builder = c2pa.Builder(manifest_def)
    dest = io.BytesIO()
    builder.sign(signer, fmt, io.BytesIO(image_bytes), dest)
    return dest.getvalue()


def read_claim(signed_bytes: bytes, fmt: str = "image/jpeg") -> tuple[dict[str, Any], str | None]:
    """Read the embedded C2PA manifest back; return (manifest_json, validation_state)."""
    reader = c2pa.Reader(fmt, io.BytesIO(signed_bytes))
    return json.loads(reader.json()), reader.get_validation_state()
