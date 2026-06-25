# CLAUDE.md

This file orients Claude Code to the Rooted repo. It is always loaded, so it holds standing
facts only: what the project is, how the repo is laid out, the commands to build and test each
service, the MCP servers in use, and the conventions to follow. Repeatable procedures live in
`.claude/skills/`, not here, to keep per-turn context small.

> TODO markers below flag values that must be filled in once the repo is scaffolded and the
> services are live (real paths, deploy URLs, project IDs). Update them as they become real so
> this file never describes something that does not exist.

---

## What Rooted is

Rooted is an open-source, vendor-neutral C2PA Soft Binding Resolution (SBR) server backed by
Backblaze B2. It recovers stripped C2PA provenance manifests for AI-generated media. The core
flow: an image is generated and signed, then later shows up stripped of its embedded manifest
(for example after a screenshot or re-encode), and Rooted recovers the full provenance by
matching an invisible watermark or a perceptual-hash fingerprint against manifests stored in B2,
then returns the recovered, signed manifest with a tamper-evident transparency-log proof.

The pipeline, end to end:
generate (Genblaze, multi-provider) -> store asset + SHA-256 manifest to B2 -> embed TrustMark
variant P watermark -> compute PDQ perceptual hash (internal index) -> sign manifest
(Ed25519/COSE) and map to a C2PA claim (c2pa-python) -> serve the vendor-neutral SBR API backed
by B2 -> apply the SB 942-style PII-redaction layer (output system provenance, withhold personal
provenance) -> append to a Merkle transparency log with signed checkpoints written to B2 under
Object Lock.

Rooted also exposes its own MCP server as a product feature, so AI agents can verify provenance,
recover manifests, and query the transparency log conversationally.

---

## Repo layout

Monorepo: Turborepo for the JS side, uv workspaces for the Python side.

```
/web          Next.js 15+ App Router front end (React 19, TypeScript, Tailwind v4, shadcn/ui)
/api          FastAPI backend: the SBR API endpoints, signing, redaction
/worker       Dramatiq actors: the generate -> watermark -> fingerprint -> sign -> log pipeline
/packages
  /provenance c2pa-python, TrustMark, PDQ, Merkle-log logic (the trust core)
  /storage    Backblaze B2 access (b2sdk) and the Genblaze sink wiring
/mcp          Rooted's own MCP server (FastMCP, wraps the SBR API as curated tools)
```

TODO: confirm this layout once scaffolded; adjust the paths above to match what actually exists.

---

## Commands

Python services use `uv`. The JS front end uses TODO:PACKAGE_MANAGER (pnpm or npm: pick one at
scaffold time and lock it in here).

Backend / worker / packages (run from repo root or the relevant service dir):
- Install: `uv sync --locked --all-extras --dev`
- Run the API locally: `uv run fastapi dev api/...` TODO: confirm entry path
- Run the worker: `uv run dramatiq worker.main` TODO: confirm module path
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`  (check-only in CI: `uv run ruff format --check .`)
- Type-check: `uv run mypy .`  (and/or `uv run ty check` for fast local feedback)
- Test: `uv run pytest`
- Contract-test the SBR API against its OpenAPI spec:
  `uvx schemathesis run http://localhost:8000/openapi.json --checks all`

Front end (run from `/web`):
- Install: TODO `pnpm install` or `npm install`
- Dev: TODO `pnpm dev`
- Build: TODO `pnpm build`
- Lint: TODO `pnpm lint`
- Component tests: TODO `pnpm test` (Vitest + React Testing Library)
- Regenerate typed API client from the backend OpenAPI schema:
  TODO `pnpm generate:api` (openapi-typescript + openapi-fetch against /openapi.json)

Local full stack:
- `docker compose up` brings up Postgres (pgvector/pgvector:pg16), Redis, the API, and a worker.
  TODO: confirm the compose service names once the file exists.

Always run lint, type-check, and tests before considering a change done. Never commit a change
that leaves `uv.lock` stale (CI runs `uv sync --locked` and will fail).

---

## MCP servers in use

Use these rather than guessing or reaching for the browser. Build-time connectors:

- context7 (remote) - live, version-specific docs. ALWAYS use Context7 for library or API
  documentation before generating code against any dependency (Next.js, FastAPI, react-three-fiber,
  c2pa-python, FastMCP, b2sdk). This is the highest-leverage rule in this file.
- github (remote) - issues, PRs, repo operations.
- postgres - Crystal DBA `postgres-mcp` (Postgres MCP Pro) in `--access-mode=restricted` against
  the database. Schema inspection, safe queries, and pgvector index tuning for the PDQ Hamming
  search. Do NOT install `@modelcontextprotocol/server-postgres` (deprecated, unpatched SQL
  injection in its read-only path).
- vercel (remote) - front-end deploys and logs.
- render (remote) - backend deploys, logs, metrics, env vars, read-only DB queries.
- sentry (remote) - production error triage.
- playwright (local) - drives the front end for E2E tests and for scripting the demo run.
- sequential-thinking (local) - multi-service planning.
- memory (local) - cross-session context.
- Optional: chrome-devtools (local, WebGL perf profiling), figma (remote, if designing in Figma).

Backblaze B2 has no official MCP server. Access B2 in code via `b2sdk` (or boto3 against the
S3-compatible endpoint), and use Backblaze's official Claude Agent Skill for B2 management tasks.

---

## CLIs available

Through the shell: `uv`, `ruff`, `pytest`, `schemathesis`, `vercel`, `render`, `modal`, `b2`,
`gh`, `git`, and TODO:PACKAGE_MANAGER. Prefer these for deploys, logs, and secrets over manual steps.

---

## Deployment

- Front end: Vercel. TODO: project URL once live.
- API + Postgres + Redis: Render (paid always-on instance for the demo, to avoid cold-start
  failures on stage). TODO: service URL once live.
- GPU / slow inference (TrustMark encode, the slow pipeline tail): Modal serverless functions,
  called from the API. TODO: confirm which steps run on Modal vs in-container once decided.
- The front end is typed from the backend's `/openapi.json`; CORS is enabled on the API for the
  Vercel origin (or `/api/*` is proxied via `next.config.js` rewrites).

Secrets live in GitHub Actions secrets, Vercel env, Render env, and Modal secrets. Keep a single
source of truth (a `.env.example` plus a secrets manager) and sync outward. Never hardcode secrets
in `.mcp.json` (use `${ENV_VAR}` interpolation). Required external credentials: B2 application key
(B2_KEY_ID / B2_APP_KEY), the generation-provider keys (for example GMI Cloud, OpenAI), and the
platform tokens (Vercel, Render). These are expected, not extra dependencies.

---

## Conventions and build rules

- Style: plain, direct prose in docs and comments. No marketing language. No em-dashes anywhere
  (this is a deliberate house rule; use commas, parentheses, or separate sentences instead).
- Explain non-obvious steps as if for the first time: concrete, broken down, no assumed knowledge.
- TypeScript: end-to-end type safety against the FastAPI backend via generated clients
  (openapi-typescript + openapi-fetch + TanStack Query). Regenerate types whenever the API changes;
  never hand-write API response types that the schema can produce.
- Python: FastAPI + Pydantic v2, async-first. SQLModel for models, Alembic for migrations,
  asyncpg as the driver. Validate everything at the boundary.
- Perceptual-hash search: store PDQ hashes as `bit(256)` in Postgres, index with an HNSW
  `bit_hamming_ops` index (pgvector 0.7+), query by Hamming distance. Watermark-ID lookups are
  exact-match (a plain B-tree index), not nearest-neighbor.
- C2PA signing requires an X.509 certificate chain, not a bare key. For the demo a self-signed
  test cert is fine (validation will flag an untrusted root, which is expected and should be shown
  honestly, not hidden).
- TrustMark: standardize on variant P everywhere. Pre-bake the model weights into the container
  (they download on first use otherwise, which fails offline). Never double-watermark an image.
- The product UI stays generic and universal: no hardcoded persona names or company names in the
  build. Named examples belong only in live pitch narration, never in the shipped interface.
- Honesty rule, surfaced in the UI: provenance proves origin, not truth. A self-signed credential
  shows "Valid," not the green "Trusted" state, which requires a Conformance-Program CA.

---

## Testing and quality

- Python: pytest + pytest-asyncio + httpx for the API.
- Contract: schemathesis against the SBR OpenAPI spec, to prove the API conforms to the C2PA SBR
  contract. This is high-value because the SBR API is spec-defined; run it in CI.
- Front end: Vitest + React Testing Library for components; Playwright for E2E and visual
  regression (screenshot diffs on a fixed viewport for the WebGL/3D surfaces).
- Demo-safety: a k6 or locust smoke test against the SBR endpoints and the perceptual-hash search
  before deploy, so the live demo will not fall over.

---

## How to work in this repo

- Use plan mode for any change that touches more than one service; let an Explore subagent map the
  relevant code first so the main context stays small.
- Use subagents to build independent pieces in parallel (for example the react-three-fiber scene
  and the FastAPI SBR endpoints), each spec'd fully since they start cold, and each including the
  build-and-verify step.
- Keep repeatable procedures (deploy runbook, release checklist, "run schemathesis against the SBR
  spec," the demo-run script) in `.claude/skills/`, not in this file.
- When you need a fact about a dependency you are not certain of, fetch it via Context7 or read the
  actual repo. Do not generate code against a remembered API surface for a fast-moving dependency
  (react-three-fiber, FastMCP, c2pa-python, Genblaze).

---

## Genblaze integration note

Genblaze emits its own SHA-256-bound JSON provenance manifest, NOT a C2PA manifest (its own docs
say to pair it with your own signer or C2PA when adversarial verification matters). So treat the
Genblaze manifest as complementary input and keep c2pa-python as the authoritative C2PA layer.
Install-name gotcha: pip names use hyphens, imports use underscores (`pip install genblaze-core`
-> `import genblaze_core`). Genblaze is a young SDK; pin versions and expect API churn.
