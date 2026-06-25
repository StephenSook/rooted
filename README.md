# Rooted

An open-source, vendor-neutral C2PA Soft Binding Resolution (SBR) server backed by Backblaze B2.
Rooted recovers stripped C2PA provenance manifests for AI-generated media. When an image is
generated and signed, then later shows up with its embedded manifest destroyed (after a screenshot
or a re-encode), Rooted recovers the full provenance by matching an invisible watermark or a
perceptual-hash fingerprint against manifests stored in B2, and returns the recovered, signed
manifest with a tamper-evident transparency-log proof.

## Why it exists

Fewer than 1% of images published online carry C2PA metadata, and embedded manifests are routinely
stripped on social platforms and re-encodes. C2PA's answer is durable recovery: recover the stripped
manifest from a repository using a watermark or fingerprint. The only production manifest-recovery
service today is Adobe's, and it is Adobe-only. Rooted is the open, vendor-neutral version, on
commodity object storage you control.

## The pipeline

```
generate (Genblaze, multi-provider)
  -> store asset + SHA-256 manifest to Backblaze B2
  -> embed TrustMark variant P invisible watermark
  -> compute PDQ perceptual-hash (internal index)
  -> sign (Ed25519 / COSE) and map to a C2PA claim (c2pa-python)
  -> serve the vendor-neutral SBR API backed by B2
  -> apply the SB 942-style PII-redaction layer (system provenance out, personal provenance withheld)
  -> append to a Merkle transparency log with signed checkpoints under B2 Object Lock
```

Rooted also exposes its own MCP server so AI agents can verify provenance, recover manifests, and
query the transparency log conversationally.

## Architecture

```
/web          Next.js 15 App Router front end (React 19, TypeScript, Tailwind v4, shadcn/ui)
/api          FastAPI backend: the SBR API endpoints, signing, redaction
/worker       Dramatiq actors: the generate -> watermark -> fingerprint -> sign -> log pipeline
/packages
  /provenance c2pa-python, TrustMark, PDQ, Ed25519/COSE, Merkle-log logic (the trust core)
  /storage    Backblaze B2 access (b2sdk) and the Genblaze sink wiring
/mcp          Rooted's own MCP server (FastMCP, wraps the SBR API as curated tools)
```

## Quickstart

```bash
cp .env.example .env          # fill in real values (B2, provider keys, platform tokens)
docker compose up             # Postgres (pgvector) + Redis + API + worker
uv sync --locked --all-extras --dev
uv run fastapi dev api/...    # the SBR API
uv run dramatiq worker.main   # the pipeline worker
cd web && pnpm install && pnpm dev
```

## Testing

```bash
uv run ruff check . && uv run mypy .
uv run pytest
uvx schemathesis run http://localhost:8000/openapi.json --checks all   # SBR contract
```

## Honesty

Provenance proves origin, not truth. A self-signed credential shows "Valid," not the green "Trusted"
state, which requires a Conformance-Program CA. Rooted surfaces this distinction in the UI rather
than hiding it.

## License

Apache-2.0. See [LICENSE](./LICENSE).
