"""Smoke test for the liveness probe, exercised against the ASGI app in-process."""

from __future__ import annotations

import httpx
from httpx import ASGITransport

from rooted_api.main import app


async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "rooted-api"
