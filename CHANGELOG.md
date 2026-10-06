# Changelog

User-visible changes are recorded here. Entries under **Unreleased** are not a
published release or a claim that a hosted deployment has been updated.

## Unreleased

### Added

- A locked, production React/esbuild frontend build. Generated HTML loads local
  fingerprinted JS/CSS, with React license sidecars, a no-script fallback and a
  render error boundary. Tests build the actual assets and render all six screens.
- An installed `upkinsey` CLI and `python -m upstage_api_sim` server entrypoint,
  with `--static-dir`, `--data-dir` and equivalent environment defaults.
- Saved-run schema version 1 and provenance containing the actual model when
  known, selected-panel/prompt hashes, requested seed versus observed panel seeds,
  available source revisions and timing.
  Unknown values are not inferred; hashes do not reconstruct the original inputs.
- Exact-count persona sampling validation, SHA-256 cache manifests, recoverable
  invalid-cache quarantine and optional requested dataset revisions through
  `UPKINSEY_PERSONA_REVISION` / sampling `--revision`.
- SQLite knowledge-graph projection of saved runs, authenticated summary access
  and a source-checkout backfill tool. JSON remains canonical and projection
  failures do not discard a successful save. No additional database service is
  required; see [retention and limitations](docs/knowledge-graph.md).

- Open contribution workflow, issue/PR templates, maintainer review routing,
  governance, conduct and private security reporting guidance.
- Credential-free Python 3.10–3.14 and Node.js frontend CI, a Docker image build and no-key startup/authentication smoke
  check, explicit package build metadata, and an isolated wheel-install check.
  Actions are SHA-pinned; dependency
  updates are scheduled monthly without automatic merging or publication.
- Maintenance and research-validity guides, an evidence-based OSS comparison,
  and a scoped roadmap.

### Fixed

- Keep public health checks independent of research/graph archive size and do not
  expose saved-run counts; the UI reports API configuration, not unverified live connectivity.

- Python 3.10 compatibility for UTC timestamps in the run store; malformed saved
  records no longer block history browsing and can still be recovered from trash.
- Malformed, non-object, and non-finite model outputs are handled without taking
  down unrelated persona reactions; partial failures remain visible in reports. Failed persona attempts are counted
  in the logical request budget, and exported ratings use a 0–100 scale rather
  than implying calibrated purchase probabilities.
- Upstream failure handling and redaction of API keys in errors.
- Empty panels and missing research fields no longer acquire fabricated personas,
  scores, confidence, demographics, chat replies, timestamps, or comparison trends.
  Stance thresholds agree with the backend; distributions show the actual counts
  and percentages, and a sampling seed of zero is preserved.
- Browser API failures provide actionable feedback; request-abort listeners are
  cleaned up. Landing-page animation is identified as a sample, not a live run.
- The light theme now updates actual surfaces, backgrounds and text colors; a
  late dark-only visual-polish layer no longer overrides the selected palette.
- Persona cards and constellation selectors are native keyboard-accessible buttons
  with selection state and visible focus. Chat preserves Korean/other IME
  composition and Shift+Enter instead of sending unfinished text.
- GET and HEAD use the same authentication and access controls. Static serving
  blocks hidden files, directory listings, and paths resolving outside the app.
- Request framing, JSON finiteness, and follow-up container validation reject
  malformed inputs before paid calls. Idle socket reads have a timeout.
- All paid endpoints share admission limits; request identity no longer trusts
  arbitrary forwarding headers. Cross-site browser mutations are rejected and
  API/job error messages redact credentials.

### Changed / upgrade notes

- **Source web startup now needs a frontend build:** run `npm ci && npm run build`
  with Node.js 22+ before `upkinsey`. The wheel includes the server/CLI but not
  generated browser assets; supply `--static-dir` outside a source checkout.
  Node is not needed at runtime. Docker builds assets in a Node 22 stage and runs
  Python 3.11 without Node.
- Browser code now uses explicit ES modules instead of global script ordering.
  Runtime Babel, React/font CDN downloads and unused editor/legacy UI code were
  removed. An accessible theme control replaces the editor-only control path.
- Research input, interview-planning, Markdown-report and provenance concerns now
  have separate modules; existing public research imports and HTTP routes remain
  compatible. The old `scripts/run_upkinsey_server.py` launcher still works.
- Existing saved JSON runs are preserved; no destructive migration is required.
  Graph projection is automatic and best-effort, but graph retention is separate:
  deleting a saved run does not delete its graph evidence.

- Both READMEs now distinguish no-key contribution from live paid inference,
  disclose provider data flow and single-operator limitations, and document local
  bind addresses and persistent Docker storage. Example credentials are blank.
- `UPKINSEY_MAX_ACTIVE_JOBS` now covers all paid operations, including synchronous
  simulations, chat, analyst interviews, and document parsing. Concurrent callers
  may receive HTTP 429 where they previously bypassed this limit.
- Rate limits use the socket peer. Requests behind a reverse proxy share its
  budget; configure per-client limiting at a trusted proxy. Preserve public
  `Host`, `Origin`, and `Sec-Fetch-Site` headers for same-origin checks.
- Clients supplying a JSON content type must use `application/json`; ambiguous
  framing, non-finite numbers, and malformed nested containers return errors.
- The package/distribution name, Python import namespace, version `0.1.0`, and
  standard-library core are retained. The wheel includes the library and server/CLI, while web assets are a separate
  build artifact. No automatic publishing is added.
- Existing Render `autoDeploy: true` is unchanged. Before merging into a deployed
  branch, the owner must inspect the connected service and decide how to handle
  automatic deployment; a draft pull request is not a production rollout.

See [deployment notes](PUBLIC_DEPLOYMENT.md) before updating an existing instance.
