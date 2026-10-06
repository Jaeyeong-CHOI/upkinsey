# Maintaining Upkinsey

This is a lightweight operating guide for a small project, not a claim that repository settings, releases, or hosted services have already been configured. Start with [governance](../GOVERNANCE.md), [contributing](../CONTRIBUTING.md), and [the OSS comparison](oss-benchmark.md).

## Triage and review

- Review new reports when capacity permits. Reproduce bugs with invented inputs and identify the affected commit, user impact, and smallest fix. Route vulnerabilities privately.
- Prioritize unauthorized access/cost exposure, data loss, misleading research output, and broken onboarding before new integrations.
- Keep one coherent problem per PR. Require relevant regression coverage and exact verification results; do not treat passing tests as live-model validation.
- Optional labels such as `bug`, `enhancement`, `documentation`, `good first issue`, and `help wanted` can help triage. Add `good first issue` only when scope, likely files, and acceptance criteria are clear. Templates do not depend on labels being created.
- Do not auto-close reports merely because they are old, or auto-merge dependency updates without review.

## Repository settings (owner-operated)

These cannot be enabled by committing documentation. Inspect settings before changing them:

1. Keep GitHub private vulnerability reporting enabled and verify the report button as a non-maintainer. The upstream repository has it enabled; forks need their own configuration. Keep the fallback in `SECURITY.md` current if a dedicated private contact is established.
2. Enable Dependabot alerts/security updates and secret scanning/push protection where available. `dependabot.yml` schedules version-update PRs; it is not a vulnerability audit or a complete lockfile.
3. After CI has actually run, configure a `main` ruleset requiring the observed `Python 3.10` through `Python 3.14`, `Frontend regression tests`, `Docker build`, and `Build and install package` checks. Avoid naming checks that do not exist yet. Require review when there is another available maintainer; document an emergency path appropriate to a solo maintainer.
4. Keep workflow token defaults read-only. Our PR workflow does not use `pull_request_target`, production secrets, paid inference, or publishing credentials. Third-party Actions are pinned to full commit SHAs; verify tag/commit correspondence when updating.
5. Check deployment hooks **before merging**. The existing [`render.yaml`](../render.yaml) sets `autoDeploy: true`: a Render service connected to the merged branch may deploy automatically. The owner must inspect the actual service/branch and decide whether to keep or disable that behavior before a main-branch merge. This maintenance change does not alter the Render setting. The CI added here only tests/builds; it does not publish an image/package, run inference, or deploy a service.

## Dependency review

Dependabot checks Python metadata, npm lockfiles, GitHub Actions, and Docker monthly, with small PR limits and grouped minor/patch updates where appropriate. Major changes remain separate. Review release notes, Python support, licenses, and relevant tests. There is no auto-merge.

The frontend's exact dependencies and transitive integrity hashes live in `package.json` and `package-lock.json`. Use `npm ci`, run `npm audit --omit=dev` and `npm audit`, and browser-test updates after rebuilding. Keep the React license sidecars in the served output. The app no longer downloads runtime React, Babel or fonts from CDNs; do not reintroduce that deployment dependency.

The optional `datasets` dependency is not exercised by offline tests; a dataset-loading change needs a deliberate sampling smoke test and a recorded manifest. Installing build/optional dependencies needs network access. Built screens and offline tests do not require upstream services; live inference and dataset sampling do.

## Build and storage contracts

- `npm ci && npm run build` produces `frontend-dist/index.html`, `Resonance.html` and fingerprinted `assets/*` JS/CSS plus license sidecars. Source HTML is a template; there is no runtime Babel fallback. Build before source-checkout startup. A missing build causes an actionable startup error.
- Docker uses Node 22 only in its build stage and Python 3.11 in the runtime image. The runtime serves bundled React locally and needs no Node installation.
- `upkinsey`, `python -m upstage_api_sim`, and the old source launcher run the same packaged server. Set `--static-dir` / `UPKINSEY_STATIC_DIR` and `--data-dir` / `UPKINSEY_DATA_DIR` explicitly for installations outside a checkout. CLI options override environment defaults.
- JSON is canonical. Graph projection happens automatically after a successful save and is best-effort; it must not discard that save on failure. The authenticated graph-summary endpoint is operational information, not measured market evidence.
- Run deletion and graph retention are different operations. Deleting a JSON run does not erase graph observations. See [graph storage and backfill](knowledge-graph.md) before changing retention or rebuilding the projection.
- Persona cache reuse validates exact row counts, per-row sampling metadata and a SHA-256 sidecar. Invalid artifacts are moved to cache-local `.trash/` before resampling. Keep cache and manifest together in backups; use `UPKINSEY_PERSONA_REVISION` or the sampling CLI’s `--revision` to request a verified dataset commit when possible. A requested branch/ref is not automatically a resolved immutable revision. Cache publication is coordinated within one process, not across shared multi-worker writers.
- Preserve provenance without filling unknown model/revision values. Hashes are useful comparison identifiers but not a replacement for the original inputs or evidence of deterministic model output.

## Release checklist

There is no automatic package or container registry publishing workflow. This is separate from the existing Render auto-deployment setting described above. To prepare a release:

1. Choose the scope from merged changes; update [CHANGELOG.md](../CHANGELOG.md) with user-visible fixes and compatibility/deployment notes.
2. Run [the contributor checks](../CONTRIBUTING.md#offline-checks--api-키-없는-검증), inspect CI on the exact commit, and build/install the wheel in a clean environment. The wheel includes the server and CLI but not built web assets. Verify `upkinsey --version`, `python -m upstage_api_sim --help`, and startup with the built `--static-dir` in a clean environment. The `Docker build` job installs optional dependencies, starts the image with disposable fixture credentials and no API key, and verifies health, GET/HEAD authentication, and browser assets. It does not exercise live provider access or dataset downloads.
3. For UI/API changes, exercise the relevant flow in a browser, including errors and auth boundaries. For prompt/model changes, follow [research validity](research-validity.md); record any live checks and costs separately from offline CI.
4. Review secrets/data exclusions and third-party notices. Back up the entire configured data directory, including run JSON, persona cache, trash and SQLite graph. Note saved-run format or configuration changes. New schema-1 runs do not require conversion or deletion of older runs; graph backfill is a separate deliberate operation.
5. When actually releasing, update both `pyproject.toml` and `src/upstage_api_sim/__init__.py` to the same version. Keep `0.1.0` until a release decision is made. Pre-1.0 is not permission to omit migration notes.
6. Tag and publish release notes only after the owner's release decision. Do not claim a package/container is published unless its artifact was verified. A hosted deployment is a separate operation with rollback and persistence checks.

## Maintenance rhythm

When capacity permits, periodically review open regressions, dependency PRs, provider/model changes, deployment health, and the roadmap. This is not an automated schedule or service-level commitment. Record durable decisions in issues/PRs and these documents, not only in chat.

The public health endpoint does not read/count the saved-run archive or graph.
Authenticated history listing currently reads and sorts all run records by stored
timestamp before applying its output limit. For large archives, plan an indexed
store; do not add full-history scans to public readiness/liveness probes.
