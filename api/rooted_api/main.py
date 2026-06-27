"""FastAPI application entry point for the Rooted SBR API.

Exposes the C2PA v2.4 Soft Binding Resolution routes (mounted from rooted_api.sbr) plus a liveness
probe. The resolver (and its Postgres connection pool, when DATABASE_URL is set) is built and
validated at startup via the lifespan, so a misconfigured database fails the deploy loudly instead
of 500ing on the first user request.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from rooted_api.demo import router as demo_router
from rooted_api.demo import seed_demo
from rooted_api.sbr import get_log, get_resolver, get_storage
from rooted_api.sbr import router as sbr_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Build + validate the resolver/DB pool and rehydrate the transparency log now, not on first
    # request, so a misconfigured database fails the deploy loudly.
    get_resolver()
    get_log()
    # Seed the credential-free demo asset when asked (ROOTED_DEMO_SEED=1), so a live deploy with no
    # provider key still shows a real VERIFIED recovery. When B2 is configured, the seed also writes
    # the asset/manifest/signature to B2, so the live demo exercises B2 for real. Idempotent.
    if os.environ.get("ROOTED_DEMO_SEED") == "1":
        seed_demo(get_resolver(), get_log(), get_storage())
    try:
        yield
    finally:
        # Close the connection pools on shutdown (a no-op for the in-memory backends).
        get_resolver().close()
        get_log().close()


app = FastAPI(title="Rooted SBR API", version="0.1.0", lifespan=lifespan)
app.include_router(sbr_router)
app.include_router(demo_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns no provenance data, safe for an unauthenticated check."""
    return {"status": "ok", "service": "rooted-api"}
