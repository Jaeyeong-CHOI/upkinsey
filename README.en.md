<div align="center">

# Upkinsey

**A self-hostable synthetic research lab for pre-launch product decisions**<br>
Turn a product brief into persona reactions, market signals, objections, analyst interviews, validation plans, and founder-ready reports.

[한국어](README.md) · [English](README.en.md) · [Demo page](https://upstage.jaeyeong2026.com)

<img src="assets/readme-constellation.png" alt="Upkinsey constellation view with six synthetic personas" width="960" />

</div>

---

## Why Upkinsey?

Launching a product is expensive. Running lightweight market research before launch should not be.

Upkinsey lets you paste a product concept, run it against a Korean synthetic persona panel built around [**nvidia/Nemotron-Personas-Korea**](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea), and get structured outputs from [**Upstage Solar**](https://console.upstage.ai/docs/capabilities/generate/chat) that help answer:

- Who is likely to care?
- What objections appear repeatedly?
- Which segment should we test first?
- Is pricing the blocker, or is the value proposition unclear?
- What should we ask real users next?

> **Important:** Upkinsey is a pre-research tool. Synthetic persona signals are directional hypotheses, not statistical proof or a replacement for real customer research.

## What you get

- **Synthetic persona panel** combining [**Upstage Solar**](https://console.upstage.ai/docs/capabilities/generate/chat) reasoning with [**nvidia/Nemotron-Personas-Korea**](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea) persona data
- **Product brief preflight** to catch missing target, pricing, alternatives, and hypothesis details
- **Adoption / need-fit / price-risk signals** with heuristic evidence checks, not statistical confidence
- **Objection mining** and segment recommendations
- **Message angle tests** for landing-page and survey copy
- **Pricing sensitivity lab** for willingness-to-pay probes
- **Analyst interview mode** with multi-turn probes for recent behavior, current alternatives, barriers, proof needs, and next actions
- **Validation plan** with screener questions, interview guide, and survey draft
- **Founder memo** and portable Markdown report export
- **Saved run versions** with JSON storage, lookup/comparison APIs, and a current-run UI summary
- **Run provenance** with model identity when known, panel/prompt hashes, sampling seed, source revisions and timing
- **Local SQLite graph projection** of saved runs; JSON remains canonical and projection failures do not discard a saved result
- **Self-hosting safety controls** for paid API protection

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="assets/readme-hero.png" alt="Upkinsey landing page" /><br /><sub><b>Landing</b> — research workflow at a glance</sub></td>
    <td width="50%"><img src="assets/readme-app.png" alt="Upkinsey product brief workflow" /><br /><sub><b>Brief runner</b> — product context, pricing, and target setup</sub></td>
  </tr>
</table>

## Status and boundaries

Upkinsey is an early-stage, MIT-licensed beta and welcomes external contributions. Its current scope is a **single-operator, self-hosted research prototype**. It targets Python 3.10+; CI is configured for 3.10–3.14. APIs and saved-run formats are not yet stable contracts.

- Synthetic responses do not replace customer interviews or representative surveys. Scores and panel size are not measured conversion rates or statistical confidence. See [research validity](docs/research-validity.md).
- The app uses a standard-library Python HTTP server and local JSON files. Jobs and rate limits are process-local; there is no per-user data isolation.
- The browser UI is prebuilt with pinned React and esbuild. It makes no runtime CDN, Babel or external font requests. Node.js 22+ is needed for building and frontend tests, not serving. The Python wheel includes the server/CLI, but built web assets must be supplied separately.
- Live simulations need a paid Upstage API key and access to persona data. Tests do not. Demo-page availability is separate from repository maintenance.

## Quick start

### 1. Install, build and test without an API key

```bash
git clone https://github.com/Jaeyeong-CHOI/upkinsey.git
cd upkinsey
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci
npm run build
PYTHONPATH=src python -m unittest discover -s tests -p 'test*.py' -v
npm test
```

Use Python 3.10+ and Node.js 22+. The core Python runtime has no third-party Python dependencies. `unittest` is built in; tests do not need credentials, uploaded PDFs, or dataset downloads. Installation may need network access to package repositories. See [contributing](CONTRIBUTING.md) for Windows and frontend checks.

### 2. Explore the UI without a key

```bash
UPSTAGE_API_KEY= UPKINSEY_REQUIRE_BASIC_AUTH=0 upkinsey --host 127.0.0.1 --port 5173
```

Open <http://localhost:5173> to browse the landing and brief screens. This is a **local UI-only** command: it disables the API key and authentication. Inference and PDF analysis cannot run; sample results are not substituted for real runs. Stop with `Ctrl+C`. Enable authentication below before exposing the app beyond loopback.

### 3. Configure live simulations

```bash
python -m pip install -e '.[persona]'
cp .env.example .env
```

Edit `.env` with your own values; do not reuse example passwords:

```env
UPSTAGE_API_KEY=<your_upstage_api_key>
UPKINSEY_REQUIRE_BASIC_AUTH=1
UPKINSEY_BASIC_AUTH_USER=<your_operator_name>
UPKINSEY_BASIC_AUTH_PASSWORD=<a_long_unique_password>
```

Without an API key, live inference cannot run. New persona panels are sampled from Hugging Face and cached locally. Simulations send the product brief and persona context to Upstage; the PDF feature also sends documents to Upstage Document Parse. Start with invented or permitted data and a small panel.

### 4. Start the local app

```bash
upkinsey --host 127.0.0.1 --port 5173
```

Open <http://localhost:5173> and sign in with your configured Basic Auth credentials. The explicit host keeps the server bound to the local interface. After building, Node/npm are not required to serve the app. `python -m upstage_api_sim` runs the same server; `python scripts/run_upkinsey_server.py` remains a compatible source-checkout launcher.

The server detects `frontend-dist/` and `data/` in a source checkout. With an installed wheel alone, defaults are relative to the current working directory; build/copy web assets separately:

```bash
upkinsey --static-dir /absolute/path/to/frontend-dist \
  --data-dir /absolute/path/to/upkinsey-data --host 127.0.0.1 --port 5173
```

If built HTML is absent, startup exits with build instructions. `.env` is read from the checkout root, or the current working directory when running an installed wheel.

## Docker and deployment

After replacing the placeholder values in `.env`, run a local container with a named volume for data:

```bash
docker build -t upkinsey .
docker run --rm --env-file .env \
  -e UPKINSEY_HOST=0.0.0.0 \
  -p 127.0.0.1:5173:5173 \
  -v upkinsey-data:/app/data \
  upkinsey
```

Docker builds assets in a Node 22 stage and uses a Python 3.11 runtime image without Node.

An internet-facing deployment needs HTTPS, authentication, filesystem access controls, and backups. Basic Auth is not per-user isolation. See [the deployment guide](PUBLIC_DEPLOYMENT.md) for Render/Docker, proxies, persistence, and operational boundaries.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `UPKINSEY_STATIC_DIR` | `<root>/frontend-dist` | Built UI directory; overridden by `--static-dir` |
| `UPKINSEY_DATA_DIR` | `<root>/data` | Persistent runs, persona cache, graph and trash; overridden by `--data-dir` |
| `UPSTAGE_API_KEY` | — | Required server-side Upstage API key |
| `UPKINSEY_PERSONA_REVISION` | Unset | Optional Hugging Face commit/ref for new persona sampling; recorded as the requested reference |
| `UPSTAGE_MODEL` | `solar-pro3` | Solar chat model |
| `UPSTAGE_BASE_URL` | Upstage chat completions URL | Chat completion endpoint |
| `UPKINSEY_REQUIRE_BASIC_AUTH` | `1` in example | Require HTTP Basic Auth for API/UI |
| `UPKINSEY_BASIC_AUTH_USER` | — | Basic Auth username |
| `UPKINSEY_BASIC_AUTH_PASSWORD` | — | Basic Auth password |
| `UPKINSEY_ALLOW_DESTRUCTIVE_API` | `0` | Enable `DELETE /api/runs*` only when explicitly set |
| `UPKINSEY_MAX_PARALLEL_REQUESTS` | `2` | Persona API worker parallelism |
| `UPKINSEY_MAX_ACTIVE_JOBS` | `2` | Process-wide concurrent paid operations |
| `UPKINSEY_RATE_LIMIT_PER_MINUTE` | `30` | Per-peer mutating API rate limit (proxy clients share a budget) |
| `UPKINSEY_JOB_TTL_SECONDS` | `3600` | In-memory async job snapshot TTL |
| `UPSTAGE_MAX_RETRIES` | `8` | Retry budget for 429/5xx/transport failures |
| `UPSTAGE_MIN_REQUEST_INTERVAL_SECONDS` | `1.1` | Process-wide Upstage request spacing |

`<root>` is the source-checkout root, or the current directory for an installed wheel. Document Parse options are also available in `.env.example`. See [deployment notes](PUBLIC_DEPLOYMENT.md) for the complete setup.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Health and deployment safety status |
| `POST` | `/api/simulate/start` | Start async simulation job |
| `GET` | `/api/simulate/jobs/{job_id}` | Poll simulation progress/result |
| `POST` | `/api/persona-chat` | Ask a grounded follow-up to one persona |
| `POST` | `/api/analyst-question` | Run analyst follow-up synthesis |
| `POST` | `/api/document-brief` | Extract product brief from PDF |
| `GET` | `/api/runs` | List saved simulation versions |
| `GET` | `/api/runs/{version_id}` | Load saved simulation result |
| `GET` | `/api/runs/compare/{version_id}` | Compare with previous related run |
| `GET` | `/api/graph/summary` | Auth-protected local graph projection summary |

## Persona data

The source dataset has its own CC-BY-4.0 license; the MIT code license does not replace its attribution requirements. Install the `persona` extra before sampling.

Upkinsey is designed around [**nvidia/Nemotron-Personas-Korea**](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea).

Sample a compact local JSONL panel:

```bash
python scripts/sample_nemotron_personas.py \
  --seed 42 \
  --n 100 \
  --output data/personas/sample.jsonl
```

The sampled files are intentionally gitignored.

## Project structure

```text
prototype/                   # React ES modules, CSS, HTML build templates
scripts/build_frontend.mjs    # esbuild production build
frontend-dist/               # Generated local browser assets (gitignored)
package.json / package-lock.json
src/upstage_api_sim/server.py # Installed HTTP app and CLI entrypoint
src/upstage_api_sim/          # Simulation, input/interview/report modules,
                             # provenance, JSON runs and SQLite graph projection
scripts/run_upkinsey_server.py # Compatible source-checkout launcher
examples/                    # Example product briefs
docs/                        # Architecture, maintenance, research limitations
tests/                       # Offline Python and frontend regression tests
PUBLIC_DEPLOYMENT.md          # Self-hosting and deployment guide
```

## Safety and privacy

- API keys stay server-side in `.env` or deployment secrets.
- Saved runs are local JSON artifacts under the configured data directory (`data/simulation_runs/` by default) and are gitignored. SQLite graph observations are separate: deleting a run does not erase graph evidence. See [graph storage and retention](docs/knowledge-graph.md).
- PDF uploads are handled in-memory locally but sent to Upstage Document Parse; review provider data policies before uploading sensitive material.
- Public deployments should use Basic Auth, job limits, and rate limits because simulations spend paid Upstage quota.
- Synthetic results should be treated as hypothesis generation, not representative survey data.

## Run records and compatibility

New JSON runs record saved-run schema version 1 and provenance. Panel/prompt hashes identify inputs for comparison; they do not reconstruct the original inputs. Unknown dataset revisions and model identifiers remain unknown. A fixed sampling seed does not promise deterministic model responses. Existing JSON runs remain readable without a required data conversion or deletion.

Persona caches verify exact row counts, sampling metadata and a SHA-256 sidecar (`.jsonl.manifest.json`) before reuse. Invalid artifacts move to cache-local `.trash/` before resampling. Set `UPKINSEY_PERSONA_REVISION` or the sampling CLI’s `--revision` to a Hugging Face commit/ref for traceability. This records the requested reference, not a claimed resolution of a mutable branch to an immutable commit; use a verified commit when possible.

After saving a run, the app attempts a local SQLite graph projection automatically. It requires no extra service or Python package; a projection failure leaves the successful JSON save intact. See [Knowledge graph](docs/knowledge-graph.md) for historical backfill, backups and retention limits.

## Maintenance and contributing

Priorities are reproducible run metadata, evaluation, operational bounds, and accessibility. Discuss maintenance cost and verification before adding large features or integrations.

- [Contributor guide](CONTRIBUTING.md) — no-key development, tests, and review
- [Roadmap](docs/roadmap.md) · [Changelog](CHANGELOG.md)
- [Governance](GOVERNANCE.md) · [Code of conduct](CODE_OF_CONDUCT.md)
- [Security reporting](SECURITY.md) — do not post vulnerability details publicly
- [Maintainer guide](docs/maintaining.md) · [OSS comparison](docs/oss-benchmark.md)

Bug fixes, documentation, accessibility, and tests are welcome. Ask questions or propose improvements in [issues](https://github.com/Jaeyeong-CHOI/upkinsey/issues), in Korean or English. There is no guaranteed response or release schedule.

## Project team

Upkinsey was jointly developed as a collaborative project by:

- [Jaeyeong CHOI](https://github.com/Jaeyeong-CHOI)
- [@choihyun-1110](https://github.com/choihyun-1110)
- [@Mo-zZaAa](https://github.com/Mo-zZaAa)

## Attribution

- LLM / reasoning layer: [**Upstage Solar**](https://console.upstage.ai/docs/capabilities/generate/chat)
- Persona data support: [**nvidia/Nemotron-Personas-Korea**](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea), licensed under CC-BY-4.0
- Logo: original project artwork for Upkinsey

```text
Upstage Solar: https://console.upstage.ai/docs/capabilities/generate/chat
Nemotron-Personas-Korea: https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea
```

## License

MIT © Jaeyeong CHOI
