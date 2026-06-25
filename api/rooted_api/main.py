"""FastAPI application entry point for the Rooted SBR API.

The C2PA v2.4 Soft Binding Resolution routes (/matches/byBinding, /matches/byContent,
/manifests, /bindings, /services/supportedAlgorithms) are added in later phases. For now this
exposes a liveness probe so the deploy target and CI have a real, testable surface.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Rooted SBR API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns no provenance data, safe for an unauthenticated check."""
    return {"status": "ok", "service": "rooted-api"}
