"""Rooted's MCP product server.

Three curated tools, not the raw REST surface, so an AI agent can check provenance, recover a
stripped manifest, and audit the transparency log conversationally:

- verify_asset(image_base64): did this (possibly screenshotted) image keep recoverable provenance?
- recover_manifest(image_base64 | alg+value): return the signed, redacted manifest.
- query_transparency_log(manifest_id): the signed checkpoint and the inclusion proof.

Each tool is a thin client to the deployed SBR API (ROOTED_API_BASE_URL), so the MCP surface and
the front end consume the exact same vendor-neutral API. The HTTP client is injectable so tests can
point it at an in-process app with no network and no credentials.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx
from fastmcp import FastMCP

mcp: FastMCP = FastMCP("Rooted")

_DEFAULT_BASE_URL = "http://localhost:8000"


class SbrClient:
    """A typed wrapper over the SBR HTTP API. One method per route the tools need."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._c = client

    async def matches_by_content(self, image: bytes) -> dict[str, Any]:
        r = await self._c.post(
            "/matches/byContent", files={"file": ("query.png", image, "image/png")}
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def matches_by_binding(self, alg: str, value: str) -> dict[str, Any]:
        r = await self._c.get("/matches/byBinding", params={"alg": alg, "value": value})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def manifest(self, manifest_id: str) -> dict[str, Any] | None:
        r = await self._c.get(f"/manifests/{manifest_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def checkpoint(self) -> dict[str, Any]:
        r = await self._c.get("/transparency/checkpoint")
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def proof(self, manifest_id: str) -> dict[str, Any] | None:
        r = await self._c.get(f"/transparency/proof/{manifest_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data


_client: SbrClient | None = None


def set_client(client: SbrClient) -> None:
    """Inject the SBR client (tests point this at an in-process app)."""
    global _client
    _client = client


def _client_or_default() -> SbrClient:
    global _client
    if _client is None:
        base_url = os.environ.get("ROOTED_API_BASE_URL", _DEFAULT_BASE_URL)
        _client = SbrClient(httpx.AsyncClient(base_url=base_url, timeout=30.0))
    return _client


def _first_match(result: dict[str, Any]) -> dict[str, Any] | None:
    matches = result.get("matches") or []
    return matches[0] if matches else None


@mcp.tool
async def verify_asset(image_base64: str) -> dict[str, Any]:
    """Verify a possibly-stripped image. Recover its manifest through the SBR server and report
    whether provenance was found, by which recovery method, and the disclosed system provenance."""
    client = _client_or_default()
    match = _first_match(await client.matches_by_content(base64.b64decode(image_base64)))
    if match is None:
        return {"recovered": False, "reason": "no soft-binding match"}
    manifest = await client.manifest(match["manifest_id"])
    score = match.get("similarity_score")
    return {
        "recovered": True,
        "manifest_id": match["manifest_id"],
        "recovery_method": "fingerprint" if score is not None else "watermark",
        "similarity_score": score,
        "system_provenance": (manifest or {}).get("system_provenance", {}),
    }


@mcp.tool
async def recover_manifest(
    image_base64: str | None = None,
    alg: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    """Recover the signed provenance manifest for an asset, by content (image_base64) or by soft
    binding (alg + value). Personal provenance is withheld by the server's redaction layer."""
    client = _client_or_default()
    if image_base64 is not None:
        result = await client.matches_by_content(base64.b64decode(image_base64))
    elif alg is not None and value is not None:
        result = await client.matches_by_binding(alg, value)
    else:
        return {"error": "provide image_base64, or both alg and value"}
    match = _first_match(result)
    if match is None:
        return {"recovered": False}
    manifest = await client.manifest(match["manifest_id"])
    return {"recovered": manifest is not None, "manifest": manifest}


@mcp.tool
async def query_transparency_log(manifest_id: str) -> dict[str, Any]:
    """Audit the Merkle transparency log for a manifest: return the signed checkpoint (tree head)
    and the inclusion proof that the manifest's leaf is committed under that head."""
    client = _client_or_default()
    proof = await client.proof(manifest_id)
    if proof is None:
        return {"included": False, "manifest_id": manifest_id}
    return {
        "included": True,
        "manifest_id": manifest_id,
        "inclusion_proof": proof,
        "checkpoint": await client.checkpoint(),
    }


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
