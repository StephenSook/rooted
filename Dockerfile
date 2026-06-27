# syntax=docker/dockerfile:1
# Production image for the Rooted SBR API (Render). The front end deploys separately to Vercel.
FROM python:3.12-slim

# uv, copied from its official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Copy the whole uv workspace: validating the locked lockfile needs every member's pyproject.toml.
COPY . /app

# Install ONLY the API package and its workspace deps (rooted-provenance, rooted-storage), not the
# worker, so the image stays lean and skips the alpha Genblaze SDK and torch. The recovery path runs
# in-memory (no Postgres/B2), so the demo deploy needs no credentials.
RUN uv sync --locked --package rooted-api --no-dev

EXPOSE 8000
# Render injects $PORT; default to 8000 locally. Shell form so the variable expands.
CMD .venv/bin/uvicorn rooted_api.main:app --host 0.0.0.0 --port ${PORT:-8000}
