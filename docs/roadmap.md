# Roadmap

우선순위는 **안전하게 실행 → 결과를 정직하게 해석 → 재현 가능하게 검증 → 기능 확장**입니다. 아래 항목은 방향과 완료 기준이며, 출시일이나 담당자 배정을 약속하지 않습니다.

Priorities are safe execution, honest interpretation, reproducible evaluation, then feature expansion. Items below are proposals, not scheduled commitments. Discuss scope in an issue before starting a large change.

## Implemented foundation

These are repository capabilities, not a claim of a published release or updated hosted demo.

- Contribution/security/governance guidance and no-key regression CI with a Python version matrix, frontend checks, wheel checks, Docker build and authentication smoke checks.
- A pinned React/esbuild production build with a lockfile, local fingerprinted assets, explicit modules and no runtime Babel/CDN/font dependency. Basic keyboard and error/empty-state fixes are included.
- An installable HTTP server: `upkinsey`, `python -m upstage_api_sim`, configurable static/data directories and a compatible old launcher. Built assets remain a separate artifact from the wheel.
- Research module boundaries for input handling, interview planning, report formatting and provenance, retaining existing API/import entry points.
- Saved-run schema version 1 and actual-model/panel/prompt/seed/timing provenance; missing dataset revisions stay unknown. Existing JSON runs are retained.
- Exact-size persona cache validation, SHA-256 manifests, invalid-cache quarantine and an optional requested dataset revision. Mutable refs are not presented as resolved commit hashes.
- A local SQLite graph projection and backfill tool, preserving JSON as canonical and isolating projection failures. It is not shared storage, user isolation or prompt retrieval.

## Next: reliable single-operator research

- **Complete reproducibility manifests:** build on the recorded model/panel/prompt/seed provenance with consistently resolved dataset revisions, original input retention/export, provider settings and comparison tooling. Acceptance: another operator can reconstruct permitted inputs; hashes alone and fixed seeds must not be presented as deterministic completions.
- **Evaluation fixtures:** add a small, explicitly licensed or invented benchmark with repeated-run comparisons and known limitations. Acceptance: prompt/model changes report schema failures, variation, and qualitative differences without implying empirical customer validity.
- **Operational bounds:** review timeouts, cancellation, budget visibility, retention, and proxy-aware request limits. Acceptance: a stalled/failed upstream call cannot silently consume unbounded local resources or be mistaken for a successful study.
- **Accessible browser workflow:** extend keyboard/focus and error/empty-state coverage beyond the implemented native persona selectors and IME-safe chat. Reduce constellation rendering work (currently a React state update per animation frame) and respect reduced-motion preferences. Acceptance: all six screens and a real saved-history browser flow is usable at desktop and narrow widths, with honest partial-failure reporting.

## Later: only after the core is dependable

- A credential-free demo using labeled fixtures, with no paid API exposure.
- A provider/persona interface with contract tests, without requiring another provider for baseline use.
- Durable shared storage and real user isolation before any multi-user hosting claim; the local SQLite projection does not provide either.
- Export integrations only when their maintenance cost, permissions, and data-sharing behavior are clear.

## Explicitly not promised

Representative market forecasts, statistical confidence from synthetic sample size, unattended production hosting, an SLA, automatic package/container publication, or parity with large research-agent frameworks. See [research validity](research-validity.md) and [the OSS comparison](oss-benchmark.md).
