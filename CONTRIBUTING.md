# Contributing to Upkinsey

한국어와 영어 이슈·PR 모두 환영합니다. 작은 버그 수정, 재현 사례, 문서·접근성 개선부터 시작할 수 있습니다. 유료 API 키나 데이터셋 다운로드 없이 핵심 테스트에 참여할 수 있습니다.

Issues and pull requests in Korean or English are welcome. Useful first contributions include reproducible bugs, documentation, accessibility, and focused fixes. You do not need a paid API key or downloaded dataset to run the core tests.

## Set up / 개발 환경

Use Python 3.10 or newer. CI targets Python 3.10–3.14 on Linux. Use Node.js 22+ for the frontend build and tests (CI uses Node 22). Once built, serving the app requires only Python; the production container has no Node runtime.

```bash
git clone https://github.com/Jaeyeong-CHOI/upkinsey.git
cd upkinsey
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci
npm run build
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The project uses Python's `unittest`; `pytest` remains an optional convenience extra (`python -m pip install -e '.[test]'`).

## Offline checks / API 키 없는 검증

From the repository root:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test*.py' -v
python -m compileall -q src scripts tests
npm test
npm run build
```

With the editable package installed, Windows users can omit `PYTHONPATH=src` and run `python -m unittest discover -s tests -p 'test*.py' -v`. Python tests use fake clients and temporary data; local HTTP tests may open loopback ports. Do not introduce real API calls or dataset downloads into the default suite. CI deliberately supplies no API key and disables Hugging Face online access. Dependency/tool installation itself requires network access.

`npm test` uses Node's built-in runner, builds into a temporary directory, checks local fingerprinted assets, and renders all six screens through their module imports. Install the locked dependencies with `npm ci` first. The helper tests alone do not need a model, browser, or server. `npm run build` generates `frontend-dist/`; it is not committed. HTML under `prototype/` is a build template and is not a standalone runnable app.

For a local no-key browser check (POSIX shell):

```bash
UPSTAGE_API_KEY= UPKINSEY_REQUIRE_BASIC_AUTH=0 upkinsey --host 127.0.0.1 --port 5173
```

Open `http://localhost:5173`. This loopback-only command cannot run inference. For PowerShell, this process-local Python command also prevents an existing `.env` key from being loaded:

```powershell
python -c "import os; os.environ['UPSTAGE_API_KEY']=''; os.environ['UPKINSEY_REQUIRE_BASIC_AUTH']='0'; from upstage_api_sim.server import main; main()" --host 127.0.0.1 --port 5173
```

Do not reuse the auth-disabled setup for a public deployment.

Dependency audits query the npm registry and are separate from offline tests:

```bash
npm audit --omit=dev
npm audit
```

Packaging changes also need a source-distribution and clean-wheel check:

```bash
python -m pip install build
python -m build
python -m venv .venv-wheel
.venv-wheel/bin/python -m pip install --no-deps dist/*.whl
.venv-wheel/bin/python -I -c "import upstage_api_sim; import upstage_api_sim.server; print(upstage_api_sim.__version__)"
.venv-wheel/bin/upkinsey --version
.venv-wheel/bin/python -m upstage_api_sim --help
```

The wheel includes the Python library, HTTP server and `upkinsey` CLI, but not generated browser assets or source-checkout helper scripts. Supply built assets with `--static-dir /absolute/path/to/frontend-dist` and persistent storage with `--data-dir /absolute/path/to/data` when running outside a checkout. `python -m upstage_api_sim` is equivalent; `scripts/run_upkinsey_server.py` remains a compatibility launcher. Do not commit local virtual environments, `node_modules/`, built web assets or package artifacts.

Container or deployment dependency changes should also pass `docker build --tag upkinsey:ci .`, as run by CI. This downloads build dependencies but does not start the app, spend inference quota, or publish the image. CI also starts the image with disposable Basic Auth credentials and no API key, then checks health, authentication and built assets. This does not validate live inference or a deployed service.

## Optional live checks / 실제 모델 검증 (선택)

Follow the [README](README.en.md#quick-start) for `.[persona]`, `.env`, and local server setup. Live simulations and PDF parsing send data to external providers and can cost money. Run them only deliberately, with invented or permitted data and a small panel. Never require contributors to supply secrets to CI or execute live calls automatically on a pull request.

For prompt/model changes, retain the generated provenance (model when known, seed, panel/prompt hashes, dataset source/revision when known, timing) and separately record the commit, original panel or sampling manifest, parameters, repeated-run variation and limitations. Hashes alone cannot reconstruct the inputs. A passing unit test or a plausible answer is not evidence of real-world research validity. See [research validity](docs/research-validity.md).

## Code boundaries and compatibility

- `prototype/` contains ordinary React ES modules. Import dependencies explicitly; do not add global script ordering, runtime transpilers or third-party CDN scripts. `prototype/main.jsx` owns the React root; `scripts/build_frontend.mjs` owns the production asset contract.
- Keep frontend dependencies pinned and update `package-lock.json` with the manifest. Review `npm audit` and browser behavior when updating. React license notices are emitted next to the generated JS.
- `upstage_api_sim.server` owns HTTP wiring; research input normalization, interview planning, Markdown formatting and provenance are separate modules. Existing public `market_research` imports and API routes must remain compatible unless intentionally versioned.
- JSON run files are canonical. Preserve legacy runs; new saved runs carry schema version 1. The SQLite graph is a best-effort projection, not a replacement. Test graph changes with temporary databases, including corrupt/incompatible stores and idempotent backfill. See [graph storage](docs/knowledge-graph.md).

## A focused pull request / PR 작성

1. Search existing issues. Open an issue before a broad feature, new service/dependency, or architecture change; a small fix does not need advance permission.
2. Fork the repository if you do not have write access, then create a focused branch from `main`.
3. Keep the current standard-library core and existing API compatible unless a change is explained. Add a regression test for a behavior fix. Avoid unrelated formatting rewrites.
4. Run relevant checks above. UI changes also need a browser check at desktop and narrow widths, keyboard navigation, and the affected success/error/empty states. Include a screenshot when useful.
5. Document changed behavior. Configuration belongs in `.env.example` and `PUBLIC_DEPLOYMENT.md`; user-facing onboarding belongs in both READMEs. Note saved-run compatibility and migration steps.
6. Open a PR using the template. State what was verified and what was not. Keep API keys, credentials, private product briefs, PDFs, sampled datasets, and simulation runs out of patches and logs.

Generated code is welcome when the contributor understands it, can explain its behavior, and verifies it. Contributors retain their copyright; contributions are made under the repository's [MIT license](LICENSE). Third-party code and data need compatible permissions and attribution.

## Review and help / 검토와 문의

The repository owner routes review through [CODEOWNERS](.github/CODEOWNERS). Maintainers may request a smaller change or decline features outside [the roadmap](docs/roadmap.md). No response-time or release-date guarantee is implied. A PR is not a deployment approval.

Ask usage questions in a regular [issue](https://github.com/Jaeyeong-CHOI/upkinsey/issues). Follow the [code of conduct](CODE_OF_CONDUCT.md); report vulnerabilities through [SECURITY.md](SECURITY.md), not a public reproduction. See [governance](GOVERNANCE.md) for decisions and [the maintainer guide](docs/maintaining.md) for release operations.
