# Security policy

보안 취약점의 재현 방법, 비밀키, 비공개 데이터를 공개 이슈에 올리지 마세요. 아래 비공개 제보 경로를 먼저 확인해 주세요.

## Reporting a vulnerability

1. Use [GitHub private vulnerability reporting](https://github.com/Jaeyeong-CHOI/upkinsey/security/advisories/new), enabled for this repository. You can also find **Report a vulnerability** on the [Security tab](https://github.com/Jaeyeong-CHOI/upkinsey/security).
2. If private reporting is unavailable, check [the repository owner's public profile](https://github.com/Jaeyeong-CHOI) for a private contact method. If none is listed, open an issue titled **Private security contact requested**, mentioning `@Jaeyeong-CHOI`, with no vulnerability details. Wait for a private route before sharing a reproduction.

Forks must configure their own reporting route. Do not place exploit details, API keys, uploaded documents, private briefs, or saved runs in a public issue, pull request, or log.

In a private report, include the affected commit/version, deployment setup, minimal reproduction using invented data, observed impact, and any suggested fix. Only test systems you own or have permission to assess. If a credential was exposed, revoke/rotate it at its provider; removing it from a later commit is not enough.

## Support scope

Upkinsey is an early-stage, self-hosted research prototype. Security fixes target the current `main` branch; there are no promised backports, long-term-support branches, response-time guarantees, or bug-bounty payments. Maintainers will assess reports and coordinate a fix and disclosure as capacity permits. Older checkouts should be updated after reviewing changes and backing up data.

## Deployment boundaries

- The Python `http.server` application is a single-operator prototype, not a hardened multi-tenant service. HTTP Basic Auth does not provide per-user isolation, encryption, or authorization roles.
- Keep paid API routes behind authentication and HTTPS, using an appropriate reverse proxy or hosting platform. Leave destructive APIs disabled unless explicitly needed.
- API keys belong only in server-side environment variables or secrets. `.env` and generated data must not be publicly served or committed.
- Product briefs/persona context are sent to Upstage for inference; PDF extraction also sends the uploaded file to Upstage Document Parse. Local in-memory upload handling does not mean the document stays on the machine. Check provider policies before using sensitive information.
- Runs and sampled personas are stored as local files; jobs and rate limits are process-local. Restrict filesystem access, plan backups/retention, and do not assume worker-to-worker coordination.
- The browser prototype loads third-party scripts/fonts; default self-hosting is not air-gapped. Follow the [deployment guide](PUBLIC_DEPLOYMENT.md) and keep dependencies under review.

See [the maintainer guide](docs/maintaining.md) for repository security settings and release checks.
