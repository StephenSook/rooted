"""C2PA claim: sign an asset, read the credential back, confirm the soft-binding + "Valid" state.

Uses the c2pa ES256 test cert fixtures kept in the gitignored research/ dir, so this test runs
locally where the fixtures exist and skips in CI where they do not (the rest of the suite still
exercises the trust core without any embedded keys).
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rooted_provenance.claim import (
    build_manifest_def,
    conformance_trust_anchors,
    conformance_trust_config,
    make_es256_signer,
    read_claim,
    sign_claim,
)
from rooted_provenance.models import Manifest

_FIXTURES = Path(__file__).resolve().parents[3] / "research" / "c2pa-test-certs"
_CERT = _FIXTURES / "es256_certs.pem"
_KEY = _FIXTURES / "es256_private.key"

# The committed credentialed sample (signed with the C2PA test cert). Public, no key, so the trust
# test below runs in CI.
_SAMPLE = Path(__file__).resolve().parents[3] / "web" / "public" / "credentialed-sample.jpg"

# The signing skip is per-test (not a module-level pytestmark): only the sign+read test needs the
# private key from the gitignored research/ dir, while the conformance-trust test below needs only
# the committed public sample + anchors, so it must still run in CI.
_needs_signing_key = pytest.mark.skipif(
    not (_CERT.exists() and _KEY.exists()),
    reason="c2pa ES256 test cert fixtures not present (research/c2pa-test-certs/)",
)


def _jpeg(seed: int) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG")
    return buf.getvalue()


@_needs_signing_key
def test_sign_and_read_c2pa_claim() -> None:
    signer = make_es256_signer(_CERT.read_text(), _KEY.read_bytes())
    manifest = Manifest(
        manifest_id="urn:c2pa:demo",
        asset_sha256="a" * 64,
        created_at="2026-06-25T00:00:00Z",
        system_provenance={"model": "seedream"},
    )
    signed = sign_claim(signer, _jpeg(1), build_manifest_def(manifest, watermark_id="RT42"))
    assert len(signed) > 0

    data, state = read_claim(signed)
    # A test/self-signed cert validates the SIGNATURE ("Valid"), not the issuer ("Trusted").
    assert state == "Valid"
    active = data["active_manifest"]
    labels = [a["label"] for a in data["manifests"][active]["assertions"]]
    assert "com.rooted.soft_binding" in labels


@pytest.mark.skipif(not _SAMPLE.exists(), reason="credentialed sample not present")
def test_conformance_trust_list_yields_trusted_state() -> None:
    """The committed credentialed sample is "Valid" with no trust list, and the green "Trusted"
    state when validated against the C2PA conformance test trust anchors. Runs in CI: it needs only
    the public sample + the public test anchors, no signing key."""
    signed = _SAMPLE.read_bytes()

    _without, valid_state = read_claim(signed)
    assert valid_state == "Valid"

    _with, trusted_state = read_claim(
        signed,
        trust_anchors=conformance_trust_anchors(),
        trust_config=conformance_trust_config(),
    )
    assert trusted_state == "Trusted"
