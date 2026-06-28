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
from functools import lru_cache
from pathlib import Path
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


# The C2PA conformance test trust list. anchors.pem + store.cfg are the C2PA project's PUBLIC test
# fixtures (the certs are marked FOR TESTING_ONLY): the test root CAs and the allowed signing EKUs.
# A manifest signed with the matching C2PA test certificate validates against these as the green
# "Trusted" state. A production deployment uses the C2PA production trust list
# (contentcredentials.org) instead; these demonstrate the trusted path honestly, not a production
# trust claim.
_CONFORMANCE_DIR = Path(__file__).resolve().parent / "conformance"


@lru_cache(maxsize=1)
def conformance_trust_anchors() -> str:
    """The C2PA conformance test trust anchors (root CA bundle), as PEM text."""
    return (_CONFORMANCE_DIR / "anchors.pem").read_text()


@lru_cache(maxsize=1)
def conformance_trust_config() -> str:
    """The C2PA conformance trust config: the allowed signing-certificate EKUs."""
    return (_CONFORMANCE_DIR / "store.cfg").read_text()


def read_claim(
    signed_bytes: bytes,
    fmt: str = "image/jpeg",
    trust_anchors: str | None = None,
    trust_config: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Read the embedded C2PA manifest back; return (manifest_json, validation_state).

    With trust_anchors supplied, validation runs against that trust list: a manifest whose signing
    cert chains to an anchor with an allowed EKU (per trust_config) validates as "Trusted" (the
    green state), not just "Valid". Without anchors the issuer is not checked, so a valid signature
    reads "Valid". Pass conformance_trust_anchors()/conformance_trust_config() to validate against
    the C2PA conformance test trust list.
    """
    if trust_anchors is None:
        reader = c2pa.Reader(fmt, io.BytesIO(signed_bytes))
        return json.loads(reader.json()), reader.get_validation_state()

    trust: dict[str, str] = {"trust_anchors": trust_anchors}
    if trust_config is not None:
        trust["trust_config"] = trust_config
    settings = c2pa.Settings.from_dict({"verify": {"verify_trust": True}, "trust": trust})
    with c2pa.Context(settings) as ctx:
        with c2pa.Reader(fmt, io.BytesIO(signed_bytes), context=ctx) as reader:
            return json.loads(reader.json()), reader.get_validation_state()
