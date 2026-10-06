# Roadmap

우선순위는 **안전하게 실행 → 결과를 정직하게 해석 → 재현 가능하게 검증 → 기능 확장**입니다. 아래 항목은 방향과 완료 기준이며, 출시일이나 담당자 배정을 약속하지 않습니다.

Priorities are safe execution, honest interpretation, reproducible evaluation, then feature expansion. Items below are proposals, not scheduled commitments. Discuss scope in an issue before starting a large change.

## Next: reliable single-operator research

- **Reproducible runs:** persist model/provider, prompt/schema version, dataset revision, sampling manifest/IDs, seed, and relevant parameters with a run. Acceptance: another operator can reconstruct inputs and distinguish input reproducibility from nondeterministic model output.
- **Evaluation fixtures:** add a small, explicitly licensed or invented benchmark with repeated-run comparisons and known limitations. Acceptance: prompt/model changes report schema failures, variation, and qualitative differences without implying empirical customer validity.
- **Operational bounds:** review timeouts, cancellation, budget visibility, retention, and proxy-aware request limits. Acceptance: a stalled/failed upstream call cannot silently consume unbounded local resources or be mistaken for a successful study.
- **Accessible browser workflow:** keyboard/focus support and error/empty-state coverage. Acceptance: the brief → run → result → saved-history flow is usable at desktop and narrow widths, with honest partial-failure reporting.

## Later: only after the core is dependable

- A credential-free demo using labeled fixtures, with no paid API exposure.
- A reproducible frontend build to replace runtime Babel/CDN dependencies.
- A provider/persona interface with contract tests, without requiring another provider for baseline use.
- Durable shared storage and real user isolation before any multi-user hosting claim.
- Export integrations only when their maintenance cost, permissions, and data-sharing behavior are clear.

## Explicitly not promised

Representative market forecasts, statistical confidence from synthetic sample size, unattended production hosting, an SLA, automatic package/container publication, or parity with large research-agent frameworks. See [research validity](research-validity.md) and [the OSS comparison](oss-benchmark.md).
