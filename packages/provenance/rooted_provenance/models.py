"""Shared provenance contracts and canonicalization.

Every component (storage, fingerprint, signing, claim, Merkle log, the SBR API) builds against
these models. The canonical hash is the deterministic anchor: a manifest is serialized to canonical
JSON (sorted keys, no insignificant whitespace, UTF-8) and SHA-256'd. The same bytes are what we
sign and what we add to the Merkle log, so the three layers agree by construction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

# C2PA soft-binding algorithm identifiers. TrustMark variant P is the registered watermark we
# advertise. PDQ is an INTERNAL index only and is never listed in SupportedAlgorithms.
ALG_TRUSTMARK_P = "com.adobe.trustmark.P"
PDQ_HAMMING_THRESHOLD = 31


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, compact separators, UTF-8. The hashing input."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SoftBinding(BaseModel):
    """A recoverable pointer from an asset to its manifest (a watermark ID or a fingerprint)."""

    alg: str
    value: str
    scope: str = "all"


class Manifest(BaseModel):
    """The provenance record Rooted stores and recovers.

    system_provenance is disclosed on recovery (model, provider, timestamp). personal_provenance
    (prompt, user, IP) is withheld by the SB 942-style redaction layer. soft_bindings carry the
    watermark ID and the PDQ fingerprint used to recover this manifest when the asset is stripped.
    """

    manifest_id: str
    asset_sha256: str
    created_at: str
    system_provenance: dict[str, Any] = Field(default_factory=dict)
    personal_provenance: dict[str, Any] = Field(default_factory=dict)
    soft_bindings: list[SoftBinding] = Field(default_factory=list)

    def canonical_payload(self) -> dict[str, Any]:
        """The fields that are bound by the canonical hash and the signature.

        personal_provenance is EXCLUDED so a redacted manifest still verifies against the same
        hash (the redaction removes nothing that was hashed). soft_bindings are excluded too: they
        are recovery pointers maintained alongside the manifest, not part of its identity.
        """
        return {
            "manifest_id": self.manifest_id,
            "asset_sha256": self.asset_sha256,
            "created_at": self.created_at,
            "system_provenance": self.system_provenance,
        }

    def canonical_hash(self) -> str:
        return sha256_hex(canonical_json(self.canonical_payload()))

    def redacted(self) -> Manifest:
        """SB 942 split: emit system provenance, withhold personal provenance."""
        return self.model_copy(update={"personal_provenance": {}})


class Match(BaseModel):
    """One SBR query result: a manifest the queried asset binds to."""

    manifest_id: str
    similarity_score: int | None = None  # 0-100; None for an exact watermark hit
    endpoint: str | None = None


class SoftBindingQueryResult(BaseModel):
    matches: list[Match] = Field(default_factory=list)


class MerkleCheckpoint(BaseModel):
    """A signed tree head, written to B2 under Object Lock for tamper-evidence."""

    epoch: int
    tree_size: int
    root_hash: str
    signed_at: str
    signature_b64: str


class SupportedAlgorithms(BaseModel):
    """Advertised at /services/supportedAlgorithms. PDQ is deliberately absent (internal only)."""

    watermarks: list[str] = Field(default_factory=lambda: [ALG_TRUSTMARK_P])
    fingerprints: list[str] = Field(default_factory=list)
