"""Regenerate web/public/credentialed-sample.jpg: a real C2PA-credentialed JPEG, signed with the
ES256 test certificate, for the front-end Content Credentials display.

The signed image is committed; the signing key is NOT (it lives in the gitignored
research/c2pa-test-certs/). Run from the repo root:
`uv run python scripts/make_credentialed_sample.py`.
The test cert yields a "Valid" signature, not the green "Trusted" state (which needs a
Conformance-Program CA); the UI states that honestly.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from rooted_api.demo import _demo_image
from rooted_provenance.claim import build_manifest_def, make_es256_signer, read_claim, sign_claim
from rooted_provenance.models import Manifest

CERTS = Path("research/c2pa-test-certs")
OUT = Path("web/public/credentialed-sample.jpg")


def main() -> None:
    buf = io.BytesIO()
    _demo_image(7).convert("RGB").save(buf, "JPEG", quality=90)
    jpeg = buf.getvalue()

    manifest = Manifest(
        manifest_id="urn:c2pa:demo-credentialed-0000-0000-000000000003",
        asset_sha256=hashlib.sha256(jpeg).hexdigest(),
        created_at="2026-06-27T00:00:00Z",
        system_provenance={"model": "rooted-demo-fixture", "note": "C2PA-credentialed demo asset"},
    )
    signer = make_es256_signer(
        (CERTS / "es256_certs.pem").read_text(),
        (CERTS / "es256_private.key").read_bytes(),
    )
    signed = sign_claim(
        signer, jpeg, build_manifest_def(manifest, "DEMO", "image/jpeg"), "image/jpeg"
    )

    _manifest_json, validation = read_claim(signed, fmt="image/jpeg")
    print(f"validation_state: {validation}; signed bytes: {len(signed)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(signed)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
