# Open-source maintenance benchmark

Observed: **2026-10-06**. Sources are upstream repository documentation and actual
workflow files, not popularity rankings. Links to `main` can change after this
review. These are selected practices, not endorsements or claims that the
projects' outputs predict real customer behavior.

Upkinsey should remain a small, self-hostable synthetic pre-research tool. The
goal is a contributor's repeatable path from installation to a verified fix, not
to copy the architecture or operational commitments of a much larger project.

## Microsoft TinyTroupe: simulation validity and bounded evaluation

Its persona interviews and product-feedback examples are the closest product
comparison. The README describes comparison against real survey data and tells
users to retest scenarios when changing models. The Responsible AI FAQ separates
simulation from demonstrated real-world human behavior. Its CI separates core
tests from slower full evaluations, but even the core workflow requires a model
API secret and anticipates long execution.

- **Adopt:** explicit research limitations; fixed scenarios when changing prompts
  or models; separate software regressions from human-outcome validation; bounded
  live evaluations outside the default contributor test path.
- **Do not copy:** secret-dependent PR tests, long paid evaluations on every
  change, a broad agent/world framework, Microsoft's security contacts or response
  SLA. Those do not fit this project's resources or ownership.
- Sources: [README](https://github.com/microsoft/TinyTroupe),
  [Responsible AI FAQ](https://github.com/microsoft/TinyTroupe/blob/main/RESPONSIBLE_AI_FAQ.md),
  [core CI](https://github.com/microsoft/TinyTroupe/blob/main/.github/workflows/core-tests.yml),
  [full CI](https://github.com/microsoft/TinyTroupe/blob/main/.github/workflows/all-tests.yml).

## Expected Parrot EDSL: reproducible experiments and contributor inputs

EDSL separates questions, scenarios, agents, models and structured results. Its
README emphasizes cached responses and reproducible analysis. Contributor guidance
asks for versions, environment details and a minimal reproducer. The test workflow
includes a Python matrix, tutorial checks and doctests. Its published release
workflow, however, relies on a particular self-hosted checkout and message keywords.

- **Adopt:** documented run provenance; schema-aware saved results; a minimal
  reproducer in bug reports; checks of installation and supported Python versions.
- **Do not copy:** mandatory remote accounts/caching for local onboarding,
  machine-specific release infrastructure, or message-keyword test bypasses.
- Sources: [README](https://github.com/expectedparrot/edsl),
  [contributing](https://github.com/expectedparrot/edsl/blob/main/docs/en/latest/contributing.mdx),
  [test CI](https://github.com/expectedparrot/edsl/blob/main/.github/workflows/test_suite.yml),
  [release workflow](https://github.com/expectedparrot/edsl/blob/main/.github/workflows/deploy_to_pypi.yml).

## GPT Researcher: offline CI and an explicit deployment boundary

Its test workflow explicitly blocks network access during the offline suite,
checks imports on supported interpreters and checks collection before executing
tests. Live-provider tests are outside that path. Its security policy documents a
private reporting route and its trusted-operator deployment assumptions.
Contribution guidance puts new retriever integrations behind a plugin contract.

- **Adopt:** tests that do not need paid keys; import/install checks; private
  vulnerability reporting that actually works; a clearly documented extension
  boundary before accepting many persona/provider integrations.
- **Do not copy:** the unauthenticated backend deployment model for a publicly
  reachable demo, large integration/deployment machinery, or permanent test
  isolation workarounds that hide shared-state bugs.
- Sources: [test CI](https://github.com/assafelovic/gpt-researcher/blob/main/.github/workflows/tests.yml),
  [security policy](https://github.com/assafelovic/gpt-researcher/blob/main/SECURITY.md),
  [contributing](https://github.com/assafelovic/gpt-researcher/blob/main/CONTRIBUTING.md).

## Stanford Generative Agents: inspectable replay, not a product template

This research artifact documents saving, restarting, replaying and demonstrating
simulations separately. That is a useful model for inspecting a stored Upkinsey
run without spending more API quota. Its README also describes source-file API
key configuration and manually coordinating two servers; these are not patterns
to reproduce for a maintained self-hosted application.

- **Adopt:** distinguish generated runs from saved replay and illustrative demos;
  make underlying responses inspectable.
- **Do not copy:** source-file secrets, a research-only setup experience, or the
  inference that believable simulated behavior proves market-prediction accuracy.
- Source: [README](https://github.com/joonspk-research/generative_agents).

## Proportional maintenance priorities

1. Working contributor instructions, issue/PR templates, ownership and security
   reporting; keep Korean and English entry points consistent.
2. Key-free regression checks, installation/build checks, a small supported-version
   matrix, and explicit opt-in for paid evaluation.
3. A changelog and executable release checklist before promising a release cadence
   or automated publishing.
4. Preserve real response provenance and failures; never fill missing results with
   demonstration data or undocumented invented metrics.
5. Evaluate model/prompt changes on stable fixtures. Record model, configuration,
   persona source and sampling choices; a seed alone does not make LLM output
   deterministic.
6. Keep empirical validation as a separate research task requiring real human data.
   See [Research validity](research-validity.md).

Future candidates, not claims of completed features: a read-only fixture demo,
cost/token/latency accounting, a small persona-provider interface, and evaluation
against consented real-customer findings. A database, new frontend framework,
multi-tenancy and a plugin ecosystem are not prerequisites for this foundation.
