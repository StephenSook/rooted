# Rooted

An open-source, vendor-neutral C2PA Soft Binding Resolution (SBR) server backed by Backblaze B2.
Rooted recovers stripped C2PA provenance manifests for AI-generated media. When an image is generated
and signed, then later shows up with its embedded manifest destroyed (after a screenshot or a
re-encode), Rooted recovers the full provenance by matching an invisible watermark or a
perceptual-hash fingerprint against manifests stored in B2, and returns the recovered, signed
manifest with a tamper-evident transparency-log proof.

## Live demo

- Web: https://rooted-web-phi.vercel.app
- API: https://rooted-api-ubvc.onrender.com

Open the site and click "recover the demo asset": a real AI-generated image (created with Genblaze on
GMI Cloud, model seedream-5.0-lite) is recovered to VERIFIED, the recovered manifest's system
provenance names that model, a separately C2PA-credentialed sample is read in the browser to show its
Content Credentials, and the transparency log renders as a 3D Merkle tree. The live demo runs
credential-free; with B2 credentials set it also stores each asset, manifest, and signature on
Backblaze B2. Provenance proves origin, not truth.

## Why it exists

Fewer than 1% of images published online carry C2PA metadata, and embedded manifests are routinely
stripped by social platforms and re-encodes. C2PA's answer is durable recovery: recover the stripped
manifest from a repository using a watermark or fingerprint. The only production manifest-recovery
service today is Adobe's, and it is Adobe-only. Rooted is the open, vendor-neutral version, on
commodity object storage you control.

## The loop

```mermaid
flowchart LR
    G["Generate<br/>(Genblaze: GMICloud + OpenAI fallback)"] --> W["Watermark<br/>(TrustMark variant P)"]
    W --> S["Store to Backblaze B2<br/>(content-addressable)"]
    S --> SG["Sign<br/>(Ed25519 / COSE + C2PA claim)"]
    SG --> IDX["Index for recovery<br/>(watermark id + PDQ bit(256))"]
    IDX --> L["Append to Merkle log<br/>(signed checkpoints, B2 Object Lock)"]
    STRIP["Asset circulates,<br/>manifest stripped"] -.screenshot / re-encode.-> R
    L --> R["SBR API recovery<br/>watermark, then PDQ fallback"]
    R --> RED["Redact (SB 942 split)<br/>system out, personal withheld"]
    RED --> V["VERIFIED + inclusion proof<br/>pinned to a signed checkpoint"]
```

Recovery tries the watermark (an exact pointer) first, then falls back to the PDQ perceptual-hash
(nearest within Hamming distance 31), with a cross-layer integrity check that rejects a watermark id
pointing at an unrelated asset. Rooted also exposes its own MCP server so an AI agent can verify
provenance, recover manifests, and audit the transparency log conversationally.

## Repo layout

```
/api          FastAPI SBR API (C2PA v2.4 routes), signing, SB 942 redaction, transparency routes
/worker       the generate -> watermark -> store -> sign -> index -> log ingest pipeline
/mcp          Rooted's own MCP server (FastMCP): verify_asset, recover_manifest, query_transparency_log
/packages
  /provenance trust core: models + canonical hashing, Ed25519/COSE, c2pa-python claim, PDQ, Merkle log
  /storage    Backblaze B2 (b2sdk), PostgresIndex (pgvector-free bit(256) Hamming), transparency store
/web          Next.js 15 front end: R3F galaxy, recovery reveal, C2PA display, 3D Merkle explorer
```

## Status (what is wired today, honestly)

| Area | State |
|---|---|
| Trust core, recovery, SBR API (camelCase per the C2PA SBR spec) | wired, tested, CI green |
| Transparency log (signed checkpoint + independently-verifiable, restart-durable proofs) | wired, tested |
| FastMCP product server (three curated tools) | wired, tested |
| PostgresIndex (pooled + self-healing, atomic ingest) selected by `DATABASE_URL` | wired, real-Postgres tested via pgserver |
| Real Genblaze generation (GMICloud primary, OpenAI fallback) | wired + demonstrated: the live demo recovers a real GMICloud generation (seedream-5.0-lite), and the full generate -> store-on-B2 -> recover loop was run end to end |
| TrustMark variant P watermark | wired + verified behind the `watermark` extra; opt-in in the API resolver (`ROOTED_REAL_WATERMARK`); recovery also works via the PDQ fallback |
| Front end (Next 15, R3F, typed openapi client) + Render/Vercel deploy | wired + live at the URLs above (the demo seed runs credential-free; with B2 env set it stores assets on Backblaze B2) |
| Audio/video modalities | not yet wired |

Numbers in any submission are taken from the actual test suite, never copied from draft prose.

## SBR API

Real C2PA v2.4 Soft Binding Resolution routes, contract-tested with schemathesis against
`/openapi.json`:

- `GET /services/supportedAlgorithms` (PDQ is an internal index, never advertised)
- `POST /matches/byContent`, `GET /matches/byBinding` -> `{matches: [{manifestId, similarityScore?}]}`
- `GET /manifests/{id}` (redacted: system provenance out, personal provenance withheld)
- `GET /transparency/checkpoint`, `GET /transparency/proof/{id}` (proof pinned to a signed checkpoint)
- `POST /ingest` (trusted generation-side; gated by `ROOTED_INGEST_KEY`, required in production)

## Quickstart

```bash
cp .env.example .env                       # fill real values (B2, provider keys, platform tokens)
uv sync --locked --all-packages --dev      # backend deps
uv run fastapi dev api/main.py             # the SBR API on :8000
uv run dramatiq worker.main                # the ingest pipeline worker
cd web && pnpm install && pnpm dev         # the front end
```

`DATABASE_URL` selects the Postgres index (live recovery on Postgres); unset, Rooted runs on an
in-memory index so the demo needs no database. `docker compose up` brings up Postgres (pgvector) and
Redis for local Postgres-backed runs.

## Testing

```bash
uv run ruff check . && uv run ruff format --check .   # CI gates on these
uv run pytest                                         # incl. real-Postgres tests via pgserver
uv run mypy .                                         # local (not a CI gate yet)
uvx schemathesis run http://localhost:8000/openapi.json --checks all   # SBR contract
uvx locust -f load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 15s  # load
cd web && pnpm test                                  # front-end component tests (Vitest + RTL), CI gate
```

Front-end component tests (Vitest + React Testing Library) cover the 2D recovery and storage UI
(the FAILED/VERIFIED reveal and the Backblaze B2 panel); the WebGL/R3F scene and the c2pa-web (WASM)
panel are covered by the live demo, not unit tests.

The load smoke hits the SBR read endpoints; a recent run held 0 failures at p95 ~6 ms (~60 req/s),
so the live demo will not fall over under concurrent judges.

## Honesty and limitations

- Provenance proves origin, not truth. A self-signed credential shows "Valid," not the green
  "Trusted" state, which needs a Conformance-Program CA. Rooted surfaces this distinction honestly in
  the UI rather than hiding it; a labeled conformance-test-mode trust path is future work, not
  claimed as wired.
- The C2PA Content Credentials panel reads a separately C2PA-credentialed sample to demonstrate the
  in-browser reading capability; the recovered stripped asset has no embedded manifest (that is the
  point of recovery), so its credentials come from the repository, not from the bytes.
- The Merkle checkpoint write to a B2 Object Lock (compliance-retention) bucket is supported (the
  storage layer takes `object_lock` + `retain_days`, covered by tests); the credential-free live
  deploy runs the in-memory log, so Object Lock is exercised in tests and on a one-shot verified run,
  not on the deployed instance.
- Hamming search is exact via native Postgres `bit_count`; pgvector HNSW `bit_hamming_ops` is an
  optional accelerator for very large indexes, not claimed as wired.
- Known follow-ups on the deployed Postgres path: a single ingest transaction spanning the index and
  transparency stores, a lock around lazy resolver init, and closing pools on shutdown.

## License

Apache-2.0. See [LICENSE](./LICENSE).
