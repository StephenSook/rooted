"""The MCP product tools wrap the SBR API: verify, recover, and audit the transparency log.

The tools talk to the SBR API over httpx; these tests point that client at the in-process FastAPI
app (ASGITransport), so the full path runs with no network and no credentials. The shared in-process
state (resolver index + transparency log) is populated by ingesting through the same app.
"""

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest
from fastmcp import Client
from httpx import ASGITransport
from PIL import Image

from rooted_api.main import app
from rooted_mcp.server import SbrClient, mcp, set_client


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    img = Image.fromarray(arr).resize((64, 64)).resize((256, 256))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _asgi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def sbr() -> AsyncIterator[SbrClient]:
    async with _asgi_client() as client:
        instance = SbrClient(client)
        set_client(instance)
        yield instance


async def _ingest(manifest_id: str, watermark_id: str, seed: int) -> bytes:
    data = _png(seed)
    async with _asgi_client() as c:
        r = await c.post(
            "/ingest",
            files={"file": ("a.png", data, "image/png")},
            data={"manifest_id": manifest_id, "watermark_id": watermark_id, "model": "flux"},
        )
        assert r.status_code == 200
    return data


async def test_lists_three_curated_tools(sbr: SbrClient) -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == {
        "verify_asset",
        "recover_manifest",
        "query_transparency_log",
    }


async def test_verify_asset_recovers_after_ingest(sbr: SbrClient) -> None:
    data = await _ingest("urn:c2pa:mcp1", "RT21", 21)
    b64 = base64.b64encode(data).decode()
    async with Client(mcp) as client:
        result = await client.call_tool("verify_asset", {"image_base64": b64})
    assert result.data["recovered"] is True
    assert result.data["manifest_id"] == "urn:c2pa:mcp1"


async def test_verify_asset_reports_no_match(sbr: SbrClient) -> None:
    b64 = base64.b64encode(_png(999)).decode()
    async with Client(mcp) as client:
        result = await client.call_tool("verify_asset", {"image_base64": b64})
    assert result.data["recovered"] is False


async def test_recover_manifest_by_content_is_redacted(sbr: SbrClient) -> None:
    data = await _ingest("urn:c2pa:mcp2", "RT22", 22)
    b64 = base64.b64encode(data).decode()
    async with Client(mcp) as client:
        result = await client.call_tool("recover_manifest", {"image_base64": b64})
    manifest = result.data["manifest"]
    assert manifest["manifest_id"] == "urn:c2pa:mcp2"
    assert manifest["personal_provenance"] == {}


async def test_recover_manifest_by_binding(sbr: SbrClient) -> None:
    await _ingest("urn:c2pa:mcp3", "RT23", 23)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "recover_manifest", {"alg": "com.adobe.trustmark.P", "value": "RT23"}
        )
    assert result.data["recovered"] is True
    assert result.data["manifest"]["manifest_id"] == "urn:c2pa:mcp3"


async def test_query_transparency_log_returns_proof(sbr: SbrClient) -> None:
    await _ingest("urn:c2pa:mcp4", "RT24", 24)
    async with Client(mcp) as client:
        result = await client.call_tool("query_transparency_log", {"manifest_id": "urn:c2pa:mcp4"})
    assert result.data["included"] is True
    assert result.data["inclusion_proof"]["server_verified"] is True
    assert result.data["checkpoint"]["checkpoint"]["tree_size"] >= 1
