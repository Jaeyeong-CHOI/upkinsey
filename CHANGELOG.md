# Changelog

User-visible changes are recorded here. Entries under **Unreleased** are not a
published release or a claim that a hosted deployment has been updated.

## Unreleased

### Added

- Open contribution workflow, issue/PR templates, maintainer review routing,
  governance, conduct and private security reporting guidance.
- Credential-free Python 3.10–3.14 and Node.js frontend CI, a Docker image build and no-key startup/authentication smoke
  check, explicit package build metadata, and an isolated wheel-install check.
  Actions are SHA-pinned; dependency
  updates are scheduled monthly without automatic merging or publication.
- Maintenance and research-validity guides, an evidence-based OSS comparison,
  and a scoped roadmap.

### Fixed

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
- GET and HEAD use the same authentication and access controls. Static serving
  blocks hidden files, directory listings, and paths resolving outside the app.
- Request framing, JSON finiteness, and follow-up container validation reject
  malformed inputs before paid calls. Idle socket reads have a timeout.
- All paid endpoints share admission limits; request identity no longer trusts
  arbitrary forwarding headers. Cross-site browser mutations are rejected and
  API/job error messages redact credentials.

### Changed / upgrade notes

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
  standard-library core are retained. The wheel is a library artifact; run the
  full application from a checkout or container. No automatic publishing is added.
- Existing Render `autoDeploy: true` is unchanged. Before merging into a deployed
  branch, the owner must inspect the connected service and decide how to handle
  automatic deployment; a draft pull request is not a production rollout.

See [deployment notes](PUBLIC_DEPLOYMENT.md) before updating an existing instance.
