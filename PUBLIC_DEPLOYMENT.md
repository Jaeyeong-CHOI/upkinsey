# Public Deployment Guide

Upkinsey serves the production-built frontend and API from the same Python process. This is an early-stage, single-operator application, not a multi-tenant service. Never put `UPSTAGE_API_KEY` in browser code; keep it as a server-side environment variable only.

## Production safety defaults

A public Upkinsey deployment can spend real Upstage quota. For any internet-facing deployment, enable authentication and keep destructive APIs disabled unless you are operating a private/admin instance.

Recommended public settings:

```text
UPKINSEY_REQUIRE_BASIC_AUTH=1
UPKINSEY_BASIC_AUTH_USER=<operator username>
UPKINSEY_BASIC_AUTH_PASSWORD=<strong password>
UPKINSEY_ALLOW_DESTRUCTIVE_API=0
UPKINSEY_MAX_ACTIVE_JOBS=2
UPKINSEY_RATE_LIMIT_PER_MINUTE=30
UPKINSEY_MAX_PARALLEL_REQUESTS=2
UPKINSEY_MAX_DOCUMENT_BYTES=8000000
```

If `UPKINSEY_REQUIRE_BASIC_AUTH=1` but the username/password are missing, the API returns `auth_not_configured` instead of silently opening paid endpoints.

## Required environment variables

```text
UPSTAGE_API_KEY=<your server-side Upstage API key>
UPSTAGE_MODEL=solar-pro3
UPSTAGE_BASE_URL=https://api.upstage.ai/v1/solar/chat/completions
UPKINSEY_HOST=0.0.0.0
PORT=<platform assigned port>
```

## Recommended operational variables

```text
UPKINSEY_REQUIRE_BASIC_AUTH=1
UPKINSEY_BASIC_AUTH_USER=<operator username>
UPKINSEY_BASIC_AUTH_PASSWORD=<strong password>
UPKINSEY_ALLOW_DESTRUCTIVE_API=0
UPKINSEY_MAX_ACTIVE_JOBS=2
UPKINSEY_RATE_LIMIT_PER_MINUTE=30
UPKINSEY_MAX_PARALLEL_REQUESTS=2
UPKINSEY_MAX_BODY_BYTES=1000000
UPKINSEY_MAX_DOCUMENT_BYTES=8000000
UPKINSEY_JOB_TTL_SECONDS=3600
UPSTAGE_MAX_RETRIES=8
UPSTAGE_RETRY_BACKOFF_SECONDS=1.5
UPSTAGE_MAX_RETRY_DELAY_SECONDS=60
UPSTAGE_MIN_REQUEST_INTERVAL_SECONDS=1.1
```

## Optional Document Parse tuning

```text
UPSTAGE_DOCUMENT_PARSE_URL=https://api.upstage.ai/v1/document-ai/document-parse
UPSTAGE_DOCUMENT_PARSE_MODEL=document-parse
UPSTAGE_DOCUMENT_PARSE_OUTPUT_FORMATS=text,html
UPSTAGE_DOCUMENT_PARSE_FILE_FIELD=document
UPSTAGE_DOCUMENT_PARSE_TIMEOUT=120
```

## Build and run from a checkout

Node.js 22+ is needed only to build browser assets. Python 3.10+ runs the server.

```bash
npm ci
npm run build
python3 -m pip install -e '.[persona]'
upkinsey --host 127.0.0.1 --port 5173
```

`python3 -m upstage_api_sim` and the legacy `python3 scripts/run_upkinsey_server.py`
launcher call the same server. The Python wheel includes the API/CLI but does not
include frontend assets. Supply the directory produced by `npm run build` with
`--static-dir /path/to/frontend-dist` (or `UPKINSEY_STATIC_DIR`).
Use `--data-dir /path/to/persistent-data` (or `UPKINSEY_DATA_DIR`) to select storage.
Command-line options take precedence over environment variables. The server refuses
to start if either built HTML entry point is missing. Run builds before switching
traffic; do not serve `prototype/` directly.

Browser React/JS/CSS are bundled locally with a lockfile; runtime CDN scripts,
Babel transforms, and third-party font downloads are no longer needed. HTML
revalidates, fingerprinted assets are immutable, and the server sends a same-origin
script policy. The model API and an uncached dataset still require network access.

## Render deployment

1. Push this branch to GitHub.
2. In Render, choose `New > Blueprint` or `New > Web Service`.
3. Use Docker environment.
4. Set secrets/env vars:
   - `UPSTAGE_API_KEY`
   - `UPKINSEY_BASIC_AUTH_USER`
   - `UPKINSEY_BASIC_AUTH_PASSWORD`
5. Deploy and open the Render HTTPS URL.

`render.yaml` includes safe public defaults and marks secrets as manual/sync-disabled.

## Railway/Fly.io/other Docker platforms

The multi-stage Dockerfile uses Node 22 only during build and Python 3.11 for runtime.
The runtime runs as UID/GID 10001, not root. Build and run it, passing the required and recommended environment variables above.

```bash
docker build -t upkinsey .
docker run --rm -p 5173:5173 \
  -e UPSTAGE_API_KEY="$UPSTAGE_API_KEY" \
  -e UPKINSEY_REQUIRE_BASIC_AUTH=1 \
  -e UPKINSEY_BASIC_AUTH_USER="$UPKINSEY_BASIC_AUTH_USER" \
  -e UPKINSEY_BASIC_AUTH_PASSWORD="$UPKINSEY_BASIC_AUTH_PASSWORD" \
  --mount source=upkinsey-data,target=/app/data \
  upkinsey
```

Local URL:

```text
http://localhost:5173
```

## Persistence warning

By default, saved runs and persona cache are local files under:

```text
data/simulation_runs/
data/personas/
data/graph/upkinsey.sqlite3
data/.trash/
```

On Render/Railway-style ephemeral filesystems, these files can disappear on restart/redeploy. For durable production use, attach a persistent disk, mount `data/`, or replace the local JSON run store with an external database/object store.
The image uses `/app/data`; source checkouts default to `<repository>/data`.
Existing bind mounts must be writable by UID/GID 10001. Before upgrading from an
older root-running image, back up the volume and arrange directory ownership for
the service user; do not make the data directory world-writable. A fresh named
volume inherits the image directory's ownership.

JSON runs are canonical. SQLite is an automatically maintained, best-effort graph
projection: graph failures do not invalidate a saved research run. Its summary is
behind the same authentication as saved runs, not included in public health.
Graph records have separate retention from JSON-run deletion; see the
[graph and backfill guide](docs/knowledge-graph.md) before deleting or rebuilding.

Set `UPKINSEY_PERSONA_REVISION` to an available dataset commit SHA for a fixed source
revision. This is forwarded to the dataset loader and recorded, not independently
resolved or certified. Unpinned panels honestly report the revision as unknown.
Caches are checked for row count, sampling metadata, and (where present) content
hash. Invalid files are quarantined under the cache's `.trash/`, never substituted
with example personas. A fixed seed does not make live LLM outputs deterministic.

## Destructive API policy

`DELETE /api/runs` and `DELETE /api/runs/{id}` are disabled unless:

```text
UPKINSEY_ALLOW_DESTRUCTIVE_API=1
```

Keep this off for public demos. Use it only in a private/admin deployment.


## Reverse proxies and request identity

The built-in limiter uses the socket peer address; arbitrary `X-Forwarded-For`
and `CF-Connecting-IP` headers are not trusted. Behind a reverse proxy, requests
therefore share the proxy's limit. Configure per-client limits at a trusted TLS
proxy if needed, rather than exposing the Python process directly. Forward the
original `Host` and preserve browser `Origin` / `Sec-Fetch-Site` headers; do not
replace browser-supplied origin metadata with trusted values. Basic Auth must be
used over HTTPS outside localhost. The Python server is not a hardened internet
edge or a replacement for a proxy's connection/body timeouts and traffic limits.

Use separate instances and data directories for separate trust groups. Every
holder of the shared credentials can read the same runs. Browser localStorage
also retains brief and report content; reset the session when using a shared
browser. PDF extraction sends document contents to Upstage; evaluate that data
flow before uploading confidential material.

## Updating and rollback

Review [the changelog](CHANGELOG.md) and [maintenance checklist](docs/maintaining.md)
before changing the version. Back up `data/` outside the container, record the
currently deployed commit and environment configuration (without copying secrets
into Git), and verify the replacement locally before switching traffic. Keep the
previous image/checkout available for rollback. Do not overwrite the only copy of
saved research data while testing an upgrade.

The default CI uses fixtures only. Passing it does not certify live Upstage API
compatibility, Nemotron download access, or predictive validity of synthetic
results. The frontend build can be served without CDN access; live research still requires provider/dataset access.

All paid routes (simulation, persona chat, analyst interviews, and PDF parsing)
share `UPKINSEY_MAX_ACTIVE_JOBS`. This bounds active operations, not dollars or
model tokens. Each operation can make several provider calls and retries; configure
provider-side spend limits separately. Synchronous slots are released on failure.
Browser cross-origin mutations are rejected, including multipart PDF submissions;
ordinary CLI requests without an Origin header remain supported.


Updating GitHub alone does not update a local launch-agent/Cloudflare deployment.
Confirm the deployment's actual checkout and launch command separately. Do not
reset an existing checkout with uncommitted work or copy its `.env`/research data
into a public branch. Switch only after a clean build, isolated no-key smoke,
backup, and a recorded rollback target.
