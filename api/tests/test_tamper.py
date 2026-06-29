"""Tamper-diff forensics: /demo/tamper-diff checks the signature, recovers the authentic manifest,
and returns a field-level diff. An untampered manifest is clean; an altered signed field is flagged
with the authentic value next to the submitted one."""

from __future__ import annotations

from typing import Any, cast

import httpx
from httpx import ASGITransport

from rooted_api.main import app


async def _signed(c: httpx.AsyncClient) -> dict[str, Any]:
    r = await c.get("/demo/signed-manifest")
    assert r.status_code == 200, r.text
    return cast("dict[str, Any]", r.json())


async def test_tamper_diff_clean_for_authentic_manifest() -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        signed = await _signed(c)
        r = await c.post(
            "/demo/tamper-diff",
            json={"manifest": signed["manifest"], "signatureB64": signed["signatureB64"]},
        )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["signatureValid"] is True
    assert b["tampered"] is False
    assert all(not f["changed"] for f in b["fields"])


async def test_tamper_diff_flags_the_changed_field() -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        signed = await _signed(c)
        manifest = dict(signed["manifest"])
        manifest["systemProvenance"] = {
            **manifest["systemProvenance"],
            "model": "evil-swapped-model",
        }
        r = await c.post(
            "/demo/tamper-diff",
            json={"manifest": manifest, "signatureB64": signed["signatureB64"]},
        )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["signatureValid"] is False  # the signature no longer covers the altered manifest
    assert b["tampered"] is True
    model = next(f for f in b["fields"] if f["field"] == "system_provenance.model")
    assert model["changed"] is True
    assert model["submitted"] == "evil-swapped-model"
    assert model["authentic"] != "evil-swapped-model"
    # untouched signed fields are not flagged
    assert any(f["field"] == "asset_sha256" and not f["changed"] for f in b["fields"])
