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

Dependabot checks Python metadata, GitHub Actions, and Docker monthly, with small PR limits and grouped minor/patch updates where appropriate. Major changes remain separate. Review release notes, Python support, licenses, and relevant tests. There is no auto-merge.

The optional `datasets` dependency is not exercised by offline tests; a dataset-loading change needs a deliberate sampling smoke test and a recorded manifest. HTML CDN dependencies are **not** covered by Dependabot: audit pinned React/Babel/font URLs and integrity hashes manually, and browser-test upgrades. Installing optional dependencies and running the full app require network access.

## Release checklist

There is no automatic package or container registry publishing workflow. This is separate from the existing Render auto-deployment setting described above. To prepare a release:

1. Choose the scope from merged changes; update [CHANGELOG.md](../CHANGELOG.md) with user-visible fixes and compatibility/deployment notes.
2. Run [the contributor checks](../CONTRIBUTING.md#offline-checks--api-키-없는-검증), inspect CI on the exact commit, and build/install the wheel in a clean environment. The wheel is only the Python library; the full app needs a checkout or container. The `Docker build` job installs optional dependencies, starts the image with disposable fixture credentials and no API key, and verifies health, GET/HEAD authentication, and browser assets. It does not exercise live provider access or dataset downloads.
3. For UI/API changes, exercise the relevant flow in a browser, including errors and auth boundaries. For prompt/model changes, follow [research validity](research-validity.md); record any live checks and costs separately from offline CI.
4. Review secrets/data exclusions and third-party notices. Back up persistent data and note saved-run format or configuration changes.
5. When actually releasing, update both `pyproject.toml` and `src/upstage_api_sim/__init__.py` to the same version. Keep `0.1.0` until a release decision is made. Pre-1.0 is not permission to omit migration notes.
6. Tag and publish release notes only after the owner's release decision. Do not claim a package/container is published unless its artifact was verified. A hosted deployment is a separate operation with rollback and persistence checks.

## Maintenance rhythm

When capacity permits, periodically review open regressions, dependency PRs, provider/model changes, deployment health, and the roadmap. This is not an automated schedule or service-level commitment. Record durable decisions in issues/PRs and these documents, not only in chat.
