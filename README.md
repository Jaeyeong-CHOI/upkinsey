<div align="center">

# Upkinsey

**제품 출시 전에 시장 반응을 먼저 시뮬레이션하는 self-hostable synthetic research lab**

제품 brief 하나로 한국형 persona panel을 만들고, 반응·반대 이유·가격 저항·검증 질문·founder memo까지 한 번에 뽑아냅니다.

[한국어](README.md) · [English](README.en.md) · [Demo page](https://upstage.jaeyeong2026.com)

<img src="assets/readme-constellation.png" alt="Upkinsey constellation view with six synthetic personas" width="960" />

</div>

---

## What is Upkinsey?

Upkinsey는 **제품팀이 실제 고객 인터뷰를 시작하기 전에 가설을 빠르게 좁히는 도구**입니다.

제품 설명, 가격, 타깃, 현재 대체 행동을 입력하면 [nvidia/Nemotron-Personas-Korea](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea) 기반 persona panel을 구성하고, [Upstage Solar](https://console.upstage.ai/docs/capabilities/generate/chat)가 persona별 반응을 구조화합니다. 결과는 단순 점수가 아니라, 제품팀이 바로 써먹을 수 있는 objection, segment, pricing signal, validation plan, analyst interview transcript로 정리됩니다.

> Upkinsey is a **pre-research** tool. Synthetic persona signals are directional: they help prioritize hypotheses, but they do not replace real customer discovery or statistically valid surveys.

## Why teams use it

- 제품 아이디어가 너무 많을 때, **먼저 검증할 segment**를 고릅니다.
- “좋아 보인다”가 아니라 **왜 망설이는지**를 persona별로 분해합니다.
- 가격 문제인지, 신뢰 문제인지, 메시지 문제인지 **objection의 종류**를 나눕니다.
- 실제 인터뷰 전에 **좋은 follow-up 질문**을 자동으로 만듭니다.
- 매번 같은 실험을 반복하지 않도록 **simulation run을 저장하고 비교**합니다.

## Screenshots

<table>
  <tr>
    <td width="50%">
      <img src="assets/readme-hero.png" alt="Upkinsey landing page" />
      <br />
      <sub><b>Landing</b> — research workflow at a glance</sub>
    </td>
    <td width="50%">
      <img src="assets/readme-app.png" alt="Upkinsey product brief input workflow" />
      <br />
      <sub><b>Brief runner</b> — product context, pricing, and target setup</sub>
    </td>
  </tr>
</table>

## Core features

- **Synthetic persona panel** — Korean persona sampling designed around Nemotron-Personas-Korea
- **Brief preflight** — checks missing target, pricing, alternatives, and research assumptions
- **Persona reactions** — adoption, need-fit, understanding, price resistance, risks, positive drivers
- **Analyst interview mode** — multi-turn persona interviews that probe recent behavior, current alternatives, barriers, proof needs, and next action
- **Objection mining** — recurring reasons people hesitate, grouped into actionable themes
- **Segment recommendation** — beachhead segment suggestions with risk caveats
- **Pricing sensitivity lab** — willingness-to-pay probes and price-friction signals
- **Validation pack** — screener, field interview guide, survey draft, and experiment backlog
- **Founder memo** — concise decision memo for go / refine / pivot discussions
- **Run history** — save, reload, and compare simulation versions
- **Self-hosting guardrails** — Basic Auth, rate limits, active job limits, and destructive API opt-in

## 프로젝트 상태와 한계

MIT 라이선스의 초기 beta 프로젝트이며, 외부 기여를 환영합니다. 현재 범위는 **한 운영자가 사용하는 self-hosted 연구 프로토타입**입니다. Python 3.10 이상을 대상으로 하며 CI 설정은 3.10–3.14를 검사합니다. API·저장 형식은 아직 안정된 계약이 아닙니다.

- 합성 응답은 고객 인터뷰나 대표성 있는 설문을 대체하지 않습니다. 점수와 persona 수를 실제 전환율·통계적 신뢰도로 해석하지 마세요. [연구 타당성 안내](docs/research-validity.md)를 참고하세요.
- Python 표준 라이브러리 서버와 로컬 JSON 저장소를 사용합니다. 작업·요청 제한은 프로세스 단위이며 사용자별 데이터 격리는 없습니다.
- 브라우저 UI는 React/Babel CDN과 외부 폰트를 사용하므로 기본 설정은 완전한 오프라인 앱이 아닙니다. Python 라이브러리 wheel에는 UI와 서버 스크립트가 포함되지 않습니다.
- 라이브 시뮬레이션에는 유료 Upstage API와 persona 데이터 접근이 필요합니다. 테스트는 키 없이 실행할 수 있습니다. Demo page의 가용성은 저장소 유지관리와 별개입니다.

## Quick start

### 1. API 키 없이 설치·검증

```bash
git clone https://github.com/Jaeyeong-CHOI/upkinsey.git
cd upkinsey
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -p 'test*.py' -v
```

핵심 런타임에는 외부 Python 의존성이 없습니다. `unittest`는 Python 기본 기능이며, 테스트에 API 키·PDF 업로드·데이터셋 다운로드가 필요하지 않습니다. 설치 과정은 패키지 저장소 접근이 필요할 수 있습니다. Windows와 프런트엔드 검증 방법은 [기여 안내](CONTRIBUTING.md)에 있습니다.

### 2. 실제 시뮬레이션 설정

```bash
python -m pip install -e '.[persona]'
cp .env.example .env
```

`.env`에서 다음 값을 **직접 설정**하세요. 예시 비밀번호는 사용하지 마세요.

```env
UPSTAGE_API_KEY=<your_upstage_api_key>
UPKINSEY_REQUIRE_BASIC_AUTH=1
UPKINSEY_BASIC_AUTH_USER=<your_operator_name>
UPKINSEY_BASIC_AUTH_PASSWORD=<a_long_unique_password>
```

API 키가 없으면 라이브 추론은 실행되지 않습니다. 처음 사용하는 persona 패널은 Hugging Face에서 샘플링하여 로컬에 캐시합니다. 시뮬레이션은 제품 brief와 persona 맥락을 Upstage로 보내며, PDF 기능은 문서도 Upstage Document Parse로 전송합니다. 공유 가능한 가상 데이터와 작은 패널로 시작하세요.

### 3. 로컬 앱 실행

```bash
python scripts/run_upkinsey_server.py --host 127.0.0.1 --port 5173
```

<http://localhost:5173>에 접속하여 설정한 Basic Auth 계정으로 로그인하세요. `--host`를 명시하여 로컬 인터페이스에만 바인딩합니다. Node/npm은 앱 실행에 필요하지 않습니다.

## Docker / 배포

`.env`에 실제 값을 설정한 뒤 로컬 컨테이너를 실행할 수 있습니다. 데이터 보존을 위해 named volume을 사용합니다.

```bash
docker build -t upkinsey .
docker run --rm --env-file .env \
  -e UPKINSEY_HOST=0.0.0.0 \
  -p 127.0.0.1:5173:5173 \
  -v upkinsey-data:/app/data \
  upkinsey
```

외부 공개 시 HTTPS, 인증, 저장소 접근 통제와 백업이 필요합니다. Basic Auth는 사용자별 데이터 격리가 아닙니다. [배포 안내](PUBLIC_DEPLOYMENT.md)에서 Render/Docker, 프록시, 영속성, 운영 한계를 확인하세요.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `UPSTAGE_API_KEY` | — | Server-side Upstage API key |
| `UPSTAGE_MODEL` | `solar-pro3` | Solar chat model |
| `UPSTAGE_BASE_URL` | Upstage chat completions URL | Chat completion endpoint |
| `UPKINSEY_REQUIRE_BASIC_AUTH` | `1` in `.env.example` | Enable HTTP Basic Auth |
| `UPKINSEY_BASIC_AUTH_USER` | — | Basic Auth username |
| `UPKINSEY_BASIC_AUTH_PASSWORD` | — | Basic Auth password |
| `UPKINSEY_ALLOW_DESTRUCTIVE_API` | `0` | Allow `DELETE /api/runs*` |
| `UPKINSEY_MAX_PARALLEL_REQUESTS` | `2` | Persona API worker parallelism |
| `UPKINSEY_MAX_ACTIVE_JOBS` | `2` | Process-wide concurrent paid operations |
| `UPKINSEY_RATE_LIMIT_PER_MINUTE` | `30` | Per-peer mutating API rate limit (proxy clients share a budget) |
| `UPKINSEY_JOB_TTL_SECONDS` | `3600` | In-memory async job snapshot TTL |
| `UPSTAGE_MAX_RETRIES` | `8` | Retry budget for 429/5xx/transport failures |
| `UPSTAGE_MIN_REQUEST_INTERVAL_SECONDS` | `1.1` | Process-wide Upstage request spacing |

See [`PUBLIC_DEPLOYMENT.md`](PUBLIC_DEPLOYMENT.md) for Render, Docker, auth, persistence, and production checklist notes.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Health and deployment safety status |
| `POST` | `/api/simulate/start` | Start async simulation job |
| `GET` | `/api/simulate/jobs/{job_id}` | Poll simulation progress/result |
| `POST` | `/api/persona-chat` | Ask one persona a follow-up question |
| `POST` | `/api/analyst-question` | Run multi-turn analyst interviews |
| `POST` | `/api/document-brief` | Extract product brief from PDF |
| `GET` | `/api/runs` | List saved simulation versions |
| `GET` | `/api/runs/{version_id}` | Load saved simulation result |
| `GET` | `/api/runs/compare/{version_id}` | Compare with previous related run |

## Persona data

원본 데이터셋은 CC-BY-4.0 라이선스를 따릅니다. 코드의 MIT 라이선스와 별개이며 데이터의 출처 표시 요건을 확인하세요. 샘플링 전 `persona` extra를 설치해야 합니다.

Upkinsey is designed around [nvidia/Nemotron-Personas-Korea](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea). To sample a compact local JSONL panel:

```bash
python scripts/sample_nemotron_personas.py \
  --seed 42 \
  --n 100 \
  --output data/personas/sample.jsonl
```

Sampled persona files and simulation outputs are gitignored by default.

## Project structure

```text
prototype/                 # React/Babel browser prototype served by Python
scripts/run_upkinsey_server.py
                           # Static server + API endpoints
src/upstage_api_sim/       # Core simulation, Upstage client, run store
examples/                  # Example product briefs
docs/                      # Design, persona prompting, service docs
tests/                     # Unit tests
PUBLIC_DEPLOYMENT.md       # Self-hosting and production checklist
```

## Safety & privacy

- Keep API keys in `.env` or deployment secrets only. Never expose them to the browser.
- Saved runs live under `data/simulation_runs/` and are gitignored.
- PDF uploads are handled in-memory locally but sent to Upstage Document Parse; review provider data policies before uploading sensitive material.
- Public deployments should enable Basic Auth, job limits, and rate limits.
- Synthetic outputs are for hypothesis generation, not representative survey evidence.

## 유지관리와 기여

재현 가능한 실행 메타데이터, 결과 검증, 운영 제한, 접근성 개선을 우선합니다. 큰 기능·통합은 유지관리 비용과 검증 방법부터 논의합니다.

- [기여 안내](CONTRIBUTING.md) — 키 없는 개발·테스트, PR 검토 절차
- [로드맵](docs/roadmap.md) · [변경 기록](CHANGELOG.md)
- [거버넌스](GOVERNANCE.md) · [행동 강령](CODE_OF_CONDUCT.md)
- [보안 제보](SECURITY.md) — 취약점의 세부 내용은 공개 이슈에 올리지 마세요
- [유지관리 가이드](docs/maintaining.md) · [다른 오픈소스에서 배운 점](docs/oss-benchmark.md)

버그·문서·접근성·테스트 PR을 환영합니다. [이슈](https://github.com/Jaeyeong-CHOI/upkinsey/issues)에 한국어 또는 영어로 질문·제안할 수 있습니다. 응답 시점이나 출시 일정은 보장하지 않습니다.

## Team

- [Jaeyeong CHOI](https://github.com/Jaeyeong-CHOI)
- [@choihyun-1110](https://github.com/choihyun-1110)
- [@Mo-zZaAa](https://github.com/Mo-zZaAa)

## Attribution

- LLM / reasoning layer: [Upstage Solar](https://console.upstage.ai/docs/capabilities/generate/chat)
- Persona data support: [nvidia/Nemotron-Personas-Korea](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea), licensed under CC-BY-4.0
- Logo: original project artwork for Upkinsey

## License

MIT © Jaeyeong CHOI
