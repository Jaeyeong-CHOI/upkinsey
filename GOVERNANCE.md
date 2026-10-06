# Project governance

Upkinsey is a small, MIT-licensed, maintainer-led open-source project. Its focus is self-hostable, Korean-persona **hypothesis generation before real customer research**. It is not a representative survey service.

## Responsibility

The repository owner, [@Jaeyeong-CHOI](https://github.com/Jaeyeong-CHOI), is the initial maintenance and review contact, recorded in [CODEOWNERS](.github/CODEOWNERS). The team credited in the README reflects project authorship; it does not automatically grant review duties or imply a support commitment from every author.

Contributors can report problems, improve documentation, propose changes, and review PRs. Repository permissions are granted explicitly by the owner; contributing does not automatically grant publish, merge, or deployment access.

## Decisions

- Small, compatible fixes can go directly to a pull request.
- Significant API, data-format, dependency, architecture, or product-scope changes should start with an issue describing the problem, alternatives, and maintenance cost.
- Discussion should seek agreement. The owner makes the final merge/release decision when consensus is not possible and should record the rationale in the issue or PR.
- Security incidents and conduct reports follow [SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), rather than public design debate.

The [roadmap](docs/roadmap.md) is a prioritization guide, not a delivery promise. There is no guaranteed review or release schedule. No CLA, committee election process, sponsorship obligations, or additional license conditions are introduced here.

## Growing and handing over maintenance

Additional maintainers should have a history of useful contributions/reviews and agree on a bounded responsibility. Record appointments, scope, and changes to review routing in a public governance/CODEOWNERS PR, without exposing credentials. Use least-privilege repository access.

If the owner cannot continue, document the project's status and seek a willing successor before transferring control. If none is available, clearly mark maintenance as paused rather than promising ongoing support. MIT licensing permits forks; any repository transfer or ownership change is an explicit owner action, not an automated task.
