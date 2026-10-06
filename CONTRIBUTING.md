# Contributing to Upkinsey

한국어와 영어 이슈·PR 모두 환영합니다. 작은 버그 수정, 재현 사례, 문서·접근성 개선부터 시작할 수 있습니다. 유료 API 키나 데이터셋 다운로드 없이 핵심 테스트에 참여할 수 있습니다.

Issues and pull requests in Korean or English are welcome. Useful first contributions include reproducible bugs, documentation, accessibility, and focused fixes. You do not need a paid API key or downloaded dataset to run the core tests.

## Set up / 개발 환경

Use Python 3.10 or newer. CI targets Python 3.10–3.14 on Linux. Node.js 22 is used only for the zero-dependency frontend regression tests; serving the app does not require Node.

```bash
git clone https://github.com/Jaeyeong-CHOI/upkinsey.git
cd upkinsey
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The project uses Python's `unittest`; `pytest` remains an optional convenience extra (`python -m pip install -e '.[test]'`).

## Offline checks / API 키 없는 검증

From the repository root:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test*.py' -v
python -m compileall -q src scripts tests
node --test tests/frontend*.cjs
```

With the editable package installed, Windows users can omit `PYTHONPATH=src` and run `python -m unittest discover -s tests -p 'test*.py' -v`. Python tests use fake clients and temporary data; local HTTP tests may open loopback ports. Do not introduce real API calls or dataset downloads into the default suite. CI deliberately supplies no API key and disables Hugging Face online access. Dependency/tool installation itself requires network access.

Packaging changes also need a source-distribution and clean-wheel check:

```bash
python -m pip install build
python -m build
python -m venv .venv-wheel
.venv-wheel/bin/python -m pip install --no-deps dist/*.whl
.venv-wheel/bin/python -I -c "import upstage_api_sim; import upstage_api_sim.personas.nemotron; print(upstage_api_sim.__version__)"
```

The wheel contains the Python library, not the browser app or server scripts. Use a repository checkout or Docker for the complete application. Do not commit local virtual environments or build artifacts.

Container or deployment dependency changes should also pass `docker build --tag upkinsey:ci .`, as run by CI. This downloads build dependencies but does not start the app, spend inference quota, or publish the image. A successful build does not replace a separate runtime check.

## Optional live checks / 실제 모델 검증 (선택)

Follow the [README](README.en.md#quick-start) for `.[persona]`, `.env`, and local server setup. Live simulations and PDF parsing send data to external providers and can cost money. Run them only deliberately, with invented or permitted data and a small panel. Never require contributors to supply secrets to CI or execute live calls automatically on a pull request.

For prompt/model changes, record the model, commit, panel IDs or sampling manifest, seed, sample size, parameters, repeated-run variation, and limitations. A passing unit test or a plausible answer is not evidence of real-world research validity. See [research validity](docs/research-validity.md).

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
