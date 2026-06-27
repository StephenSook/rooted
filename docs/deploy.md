# Deploy

Rooted deploys as two services: the FastAPI SBR API on Render and the Next.js front end on Vercel.
The demo runs fully in-memory (no Postgres, Redis, B2, or provider keys), so a live, judge-facing
site needs no credentials: the seeded demo asset makes the site show a real VERIFIED recovery.

## API (Render)

Render builds the repo's `Dockerfile` (the API image: `uv sync --locked --package rooted-api`,
running `uvicorn rooted_api.main:app`). Config is declared in `render.yaml`:

- Runtime: Docker. Plan: `starter` (paid, always-on, so judges never hit a cold start).
- Health check: `/health`.
- Env: `ROOTED_DEMO_SEED=1` (seeds the demo asset so recovery returns VERIFIED live).

Deploy via the Render Blueprint (`render.yaml`) or a Docker web service pointed at the repo. After
the first deploy, note the service URL (e.g. `https://rooted-api.onrender.com`).

Optional production hardening (not needed for the demo): set `DATABASE_URL` for Postgres-backed
recovery + a persistent transparency log, `ED25519_PRIVATE_KEY_PATH` + `ROOTED_REQUIRE_SIGNING_KEY=1`
for a real (non-ephemeral) checkpoint key, and the B2 / provider keys for real ingest.

## Front end (Vercel)

- Root Directory: `web` (monorepo subdirectory). Vercel auto-detects Next.js; package manager pnpm.
- Env: `API_PROXY_TARGET` = the Render API URL. `next.config.ts` rewrites `/api/*` to it, so the
  browser talks to the API same-origin (no CORS). Leave `NEXT_PUBLIC_API_BASE_URL` unset.

## Keepalive

`.github/workflows/keepalive.yml` pings the API `/health` (and the site) every 10 minutes and fails
loudly if either is down. After deploying, set the repo variables:

- `ROOTED_API_URL` = the Render API URL.
- `ROOTED_SITE_URL` = the Vercel site URL (optional).

Until those are set the job no-ops. Render `starter` does not spin down, so this is insurance plus a
visible signal if the live site breaks during the judging window.

## Verify after deploy

1. `curl https://<api>/health` returns `{"status":"ok"}`.
2. `curl https://<api>/transparency/log` returns the seeded leaves (treeSize 7) and a root hash.
3. Open the site, click "recover the demo asset" -> it shows VERIFIED with the signed manifest, and
   the Merkle explorer renders the transparency tree.
