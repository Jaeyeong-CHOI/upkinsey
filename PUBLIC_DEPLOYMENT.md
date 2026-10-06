# Public Deployment Guide

Upkinsey serves the static prototype and API from the same Python process. This is an early-stage, single-operator application, not a multi-tenant service. Never put `UPSTAGE_API_KEY` in browser code; keep it as a server-side environment variable only.

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

Build and run the Dockerfile, passing the required and recommended environment variables above.

```bash
docker build -t upkinsey .
docker run --rm -p 5173:5173 \
  -e UPSTAGE_API_KEY="$UPSTAGE_API_KEY" \
  -e UPKINSEY_REQUIRE_BASIC_AUTH=1 \
  -e UPKINSEY_BASIC_AUTH_USER="$UPKINSEY_BASIC_AUTH_USER" \
  -e UPKINSEY_BASIC_AUTH_PASSWORD="$UPKINSEY_BASIC_AUTH_PASSWORD" \
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
```

On Render/Railway-style ephemeral filesystems, these files can disappear on restart/redeploy. For durable production use, attach a persistent disk, mount `data/`, or replace the local JSON run store with an external database/object store.

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
results. The frontend currently relies on version-pinned CDN scripts and fonts;
it is not a fully offline web bundle.

All paid routes (simulation, persona chat, analyst interviews, and PDF parsing)
share `UPKINSEY_MAX_ACTIVE_JOBS`. This bounds active operations, not dollars or
model tokens. Each operation can make several provider calls and retries; configure
provider-side spend limits separately. Synchronous slots are released on failure.
Browser cross-origin mutations are rejected, including multipart PDF submissions;
ordinary CLI requests without an Origin header remain supported.
