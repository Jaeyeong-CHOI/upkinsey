import React from "react";
/* 입력(Brief) + 실행(Run) screens */

const { useState: useStateBR, useMemo: useMemoBR, useRef: useRefBR, useEffect: useEffectBR } = React;

/* ====================== BRIEF (1단계) ====================== */

function BriefScreen({ brief, setBrief, goNext, onParseDocument = null, parseStatus = null }) {
  const fileInputRef = useRefBR(null);
  const [uploadError, setUploadError] = useStateBR("");
  const [featuresText, setFeaturesText] = useStateBR(() => (brief.features || []).join("\n"));
  const [pricingText, setPricingText] = useStateBR(() => (brief.pricing || []).join("\n"));
  const localListEditRef = useRefBR({ features: null, pricing: null });
  const update = (k, v) => setBrief(prev => ({ ...prev, [k]: v }));
  const triggerUpload = () => fileInputRef.current?.click();
  const handlePdfUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !onParseDocument) return;
    setUploadError("");
    try {
      await onParseDocument(file);
    } catch (err) {
      setUploadError(String(err.message || err));
    }
  };
  const splitListInput = (raw, { keepNumericCommas = false } = {}) => {
    const text = String(raw || "")
      .replace(/\r\n?/g, "\n")
      .replace(/[•●◦]/g, "\n")
      .replace(/\n\s*[-*]\s+/g, "\n");
    const parts = [];
    let current = "";
    const push = () => {
      const item = current.replace(/^\s*[-*]\s+/, "").trim();
      if (item) parts.push(item);
      current = "";
    };
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      const prev = text[i - 1] || "";
      const next = text[i + 1] || "";
      const commaInsideNumber = keepNumericCommas && ch === "," && /\d/.test(prev) && /\d/.test(next);
      if (ch === "\n" || ch === ";" || ch === "；" || ch === "|" || (ch === "," && !commaInsideNumber)) {
        push();
      } else {
        current += ch;
      }
    }
    push();
    return parts;
  };

  const updateList = (k, v) => {
    if (k === "features") setFeaturesText(v);
    if (k === "pricing") setPricingText(v);
    const arr = splitListInput(v, { keepNumericCommas: k === "pricing" });
    localListEditRef.current[k] = arr.join("\n");
    setBrief(prev => ({ ...prev, [k]: arr }));
  };

  const featuresKey = (brief.features || []).join("\n");
  const pricingKey = (brief.pricing || []).join("\n");
  useEffectBR(() => {
    if (localListEditRef.current.features === featuresKey) {
      localListEditRef.current.features = null;
      return;
    }
    setFeaturesText(featuresKey);
  }, [featuresKey]);
  useEffectBR(() => {
    if (localListEditRef.current.pricing === pricingKey) {
      localListEditRef.current.pricing = null;
      return;
    }
    setPricingText(pricingKey);
  }, [pricingKey]);

  const fields = [
    { key: "productName", label: "제품 이름", filled: !!brief.productName },
    { key: "description", label: "한 줄 설명", filled: !!brief.description },
    { key: "features",    label: "핵심 기능", filled: (brief.features || []).length > 0 },
    { key: "pricing",     label: "가격 옵션", filled: (brief.pricing || []).length > 0 },
    { key: "target",      label: "타깃 고객", filled: !!brief.target },
    { key: "alternatives",label: "현재 대안", filled: !!brief.alternatives },
    { key: "hypothesis",  label: "확인하고 싶은 가설", filled: !!brief.hypothesis }
  ];
  const filledCount = fields.filter(f => f.filled).length;
  const completion = Math.round((filledCount / fields.length) * 100);
  const canRun = filledCount >= 3;

  // Brief quality preflight — mirrors the backend's brief_quality check
  const score = Math.round((filledCount / fields.length) * 100);
  const missing = fields.filter(f => !f.filled);

  return (
    <div className="page" data-screen-label="01 Brief">
      <div className="page-head">
        <div className="page-eyebrow">1단계 · 제품 정보 입력</div>
        <h1 className="page-title">시장에 보여줄<br /><em>제품을 한 줄로 소개해주세요</em></h1>
        <p className="page-sub">여기 적어주시는 내용을 다음 단계에서 합성 응답자가 처음 보게 됩니다. 광고 카피처럼 짧고 분명하게 적어주세요.</p>
      </div>

      <div className="brief-layout">
        <div className="card">
          <div className="card-head ai-card-head">
            <span className="card-tag">1단계</span>
            <div className="card-head-main">
              <div className="card-title">제품 정보</div>
              <div className="card-sub">최소 3개 항목만 채워도 시작할 수 있어요. 구체적으로 적으면 검증할 가설을 정리하기 쉬워요.</div>
            </div>
            <input ref={fileInputRef} type="file" aria-label="제품 소개 PDF 업로드" accept="application/pdf,.pdf" style={{ display: "none" }} onChange={handlePdfUpload} />
            <button className="ai-import-btn" onClick={triggerUpload} disabled={!onParseDocument || parseStatus?.state === "uploading"} title="PDF를 업로드하면 Upstage Document Parse API가 제품 정보를 자동으로 채웁니다.">
              <span className="ai-spark">✦</span>
              {parseStatus?.state === "uploading" ? "AI 분석 중" : "AI로 PDF 채우기"}
            </button>
          </div>
          {(parseStatus || uploadError) && (
            <div className={"ai-import-status " + (parseStatus?.state || "error")}>
              <div>
                <strong>{parseStatus?.state === "done" ? "PDF 분석 완료" : parseStatus?.state === "uploading" ? "Document Parse 실행 중" : "PDF 분석 오류"}</strong>
                <p>{uploadError || parseStatus?.message}</p>
              </div>
              {parseStatus?.state === "done" && (
                <div className="ai-import-meta">
                  <span>신뢰도 {parseStatus.confidence || 0}%</span>
                  <span>{parseStatus.textLength || 0}자 추출</span>
                </div>
              )}
            </div>
          )}
          {parseStatus?.state === "done" && (parseStatus.evidence || []).length > 0 && (
            <div className="ai-evidence-strip">
              {(parseStatus.evidence || []).slice(0, 3).map((item, i) => <span key={i}>{item}</span>)}
            </div>
          )}
          <div className="card-body">
            <div className="field">
              <label className="field-label" htmlFor="brief-product-name">
                제품 이름 <span className="req">*</span>
                <span className="field-help">고객에게 그대로 보여줘도 부끄럽지 않을 이름이면 좋아요.</span>
              </label>
              <input className="input" placeholder="예) AI 식단 코치 앱"
                     id="brief-product-name" value={brief.productName} onChange={e => update("productName", e.target.value)} />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="brief-description">
                한 줄 설명 <span className="req">*</span>
                <span className="field-help">‘누구의 어떤 문제를, 어떻게'가 한 문장에 들어가면 충분합니다.</span>
              </label>
              <textarea className="textarea" rows="3"
                        placeholder="예) 혼자 사시는 어르신의 외로움을, 강아지처럼 반응하는 가정용 로봇이 매일 교감으로 채워드립니다."
                        id="brief-description" value={brief.description} onChange={e => update("description", e.target.value)} />
            </div>

            <div className="grid-2">
              <div className="field">
                <label className="field-label" htmlFor="brief-features">
                  핵심 기능
                  <span className="field-help">3~5개. 한 줄에 하나씩 입력해주세요. 붙여넣기는 쉼표도 인식합니다.</span>
                </label>
                <textarea className="textarea compact-textarea list-textarea" rows="5" placeholder={"예) 음성 대화\n건강 알림\n가족 화상"}
                          id="brief-features" value={featuresText}
                          onChange={e => updateList("features", e.target.value)} />
              </div>

              <div className="field">
                <label className="field-label" htmlFor="brief-pricing">
                  가격 옵션
                  <span className="field-help">가격의 쉼표(30,000원)는 그대로 두고, 옵션은 엔터로 구분해주세요.</span>
                </label>
                <textarea className="textarea compact-textarea list-textarea" rows="3" placeholder={"예) 월 39,000원\n본체 290,000원"}
                          id="brief-pricing" value={pricingText}
                          onChange={e => updateList("pricing", e.target.value)} />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="brief-target">
                누구를 위한 제품인가요?
                <span className="field-help">나이대, 지역, 직업, 생활 맥락을 한 문장에 담아주세요.</span>
              </label>
              <input className="input" placeholder="예) 자녀와 떨어져 사는 60~75세 1인 가구"
                     id="brief-target" value={brief.target} onChange={e => update("target", e.target.value)} />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="brief-alternatives">
                지금 사용자는 이 문제를 어떻게 해결하고 있나요?
                <span className="field-help">대안을 적어주시면, 응답자가 ‘이 제품을 왜 굳이?'라고 비교할 수 있어요.</span>
              </label>
              <textarea className="textarea" rows="2"
                        placeholder="예) AI 스피커, TV, 자녀의 안부 전화, 노인복지관 프로그램"
                        id="brief-alternatives" value={brief.alternatives} onChange={e => update("alternatives", e.target.value)} />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="brief-hypothesis">
                이번 실행으로 확인하고 싶은 가설
                <span className="field-help">답을 얻고 싶은 한 가지 질문을 적어주세요. 비워둬도 괜찮습니다.</span>
              </label>
              <textarea className="textarea" rows="2"
                        placeholder="예) 월 구독 모델이 단발 결제보다 가입률이 높을 것이다"
                        id="brief-hypothesis" value={brief.hypothesis} onChange={e => update("hypothesis", e.target.value)} />
            </div>

            <div className="row between" style={{ marginTop: 24 }}>
              <div className="dim" style={{ fontSize: 12.5 }}>
                자동 저장됨 · {filledCount}/{fields.length}개 항목 작성
              </div>
              <button className="btn btn-primary" onClick={goNext} disabled={!canRun}
                      style={{ opacity: canRun ? 1 : 0.5, cursor: canRun ? "pointer" : "not-allowed" }}>
                {canRun ? "다음: 응답자에게 보여주기" : "최소 3개 항목을 채워주세요"}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
          </div>
        </div>

        {/* Blueprint side */}
        <div className="blueprint">
          <div className="bp-eyebrow">미리 보기</div>
          <div className="bp-title-help">응답자가 받게 될 제품 소개입니다.</div>

          <div className="bp-section">
            <div className="bp-key">제품 이름</div>
            <div className={"bp-val" + (!brief.productName ? " empty" : "")}>
              {brief.productName || "아직 비어있어요"}
            </div>
          </div>

          <div className="bp-section">
            <div className="bp-key">한 줄 설명</div>
            <div className={"bp-val" + (!brief.description ? " empty" : "")}
                 style={{ fontSize: 14, lineHeight: 1.6 }}>
              {brief.description || "여기에 한 줄 설명이 보여요"}
            </div>
          </div>

          <div className="bp-section">
            <div className="bp-key">핵심 기능</div>
            <div className="bp-chips">
              {(brief.features || []).length > 0
                ? brief.features.map((f, i) => <span key={i} className="chip">{f}</span>)
                : <span className="bp-val empty" style={{ fontSize: 14 }}>기능을 더해주세요</span>}
            </div>
          </div>

          <div className="bp-section">
            <div className="bp-key">가격</div>
            <div className="bp-chips">
              {(brief.pricing || []).length > 0
                ? brief.pricing.map((p, i) => <span key={i} className="chip neutral">{p}</span>)
                : <span className="bp-val empty" style={{ fontSize: 14 }}>가격을 적어주세요</span>}
            </div>
          </div>

          <div className="bp-section">
            <div className="bp-key">타깃 고객</div>
            <div className={"bp-val" + (!brief.target ? " empty" : "")}
                 style={{ fontSize: 14 }}>
              {brief.target || "누구를 위한 제품인지 적어주세요"}
            </div>
          </div>

          {/* Brief Quality preflight (matches backend brief_quality check) */}
          <div className="preflight">
            <div className="preflight-head">
              <div className="preflight-title">입력 품질 점검</div>
              <div className="preflight-score">{score}점 / 100점</div>
            </div>
            <div className="preflight-bar">
              <div className="preflight-bar-fill" style={{ width: score + "%" }}></div>
            </div>
            <ul className="preflight-items">
              {fields.map(f => (
                <li key={f.key} className={f.filled ? "ok" : "missing"}>
                  {f.label} {f.filled ? "확인" : "미작성"}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ====================== RUN (2단계) ====================== */

const TESTS = [
  { id: "concept",  num: "01", name: "컨셉 반응 보기",
    desc: "제품 자체가 마음에 드는지, 어떤 부분이 와닿는지 봐요." },
  { id: "pricing",  num: "02", name: "가격 반응 보기",
    desc: "가격이 부담스러운지, 어떤 가격대에서 ‘살까?'로 마음이 기우는지 봐요." },
  { id: "message",  num: "03", name: "카피 비교하기",
    desc: "헤드라인 후보 여러 개를 동시에 보여주고 어느 게 더 먹히는지 비교해요." },
  { id: "objection",num: "04", name: "거부 이유 찾기",
    desc: "‘이래서 안 사요'라는 이유를 카테고리별로 모아요." },
  { id: "segment",  num: "05", name: "핵심 고객 찾기",
    desc: "응답자 중 누가 가장 반응이 좋은지 봐요. 첫 타깃을 좁힐 때 유용해요." }
];

const TEST_DETAIL = {
  concept:   "‘이 제품, 와닿는다 vs 별로다'를 가르는 가장 큰 신호를 확인합니다. 처음 만든 컨셉이거나 메시지를 통째로 바꿨다면 이 모드부터 시작하세요.",
  pricing:   "‘너무 비싸다 vs 합리적이다'의 경계가 어디인지 봅니다. 가격대 후보가 2개 이상일 때 유용해요.",
  message:   "헤드라인 후보 여러 개를 동시에 보여주고, 가장 적은 거부를 만드는 카피가 어떤 건지 비교합니다.",
  objection: "왜 마음이 안 움직이는지를 카테고리(가격, 신뢰, 사용법 등)로 분해합니다. 다음 라운드에서 무엇을 손볼지 잡을 때 좋아요.",
  segment:   "응답자별 반응을 세그먼트로 쪼개서, 어떤 사람이 가장 먼저 사줄지 후보를 도출합니다."
};

function RunScreen({ brief, onRun, goBack, running = false, progress = null }) {
  const [test, setTest] = useStateBR("concept");
  const [sampleSize, setSampleSize] = useStateBR(8);
  const [seed, setSeed] = useStateBR(42);

  return (
    <div className="page" data-screen-label="02 Run">
      <div className="page-head">
        <div className="page-eyebrow">2단계 · 시뮬레이션 실행</div>
        <h1 className="page-title">합성 응답자가<br /><em>당신의 제품을 처음 듣습니다</em></h1>
        <p className="page-sub">한국형 합성 페르소나 패널을 바탕으로 모델이 제품 반응을 생성해요. 실제 고객의 응답은 아닙니다. 무엇을 검증할지 한 가지만 골라주세요.</p>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-tag">2단계</span>
          <div>
            <div className="card-title">무엇을 확인할까요?</div>
            <div className="card-sub">시뮬레이션 모드 하나를 고르면, 거기에 맞춰 응답자에게 질문하는 방식이 달라져요.</div>
          </div>
        </div>
        <div className="card-body">
          <div className="test-grid" role="group" aria-label="시뮬레이션 모드">
            {TESTS.map(t => (
              <button type="button" key={t.id} aria-pressed={test === t.id}
                   className={"test-card" + (test === t.id ? " active" : "")}
                   onClick={() => setTest(t.id)}>
                <span className="test-name">
                  <span className="test-num">{t.num}</span>
                  {t.name}
                </span>
                <span className="test-desc">{t.desc}</span>
              </button>
            ))}
          </div>

          <div className="test-detail-card">
            <div className="test-detail-eyebrow">선택한 모드 · {TESTS.find(t => t.id === test).name}</div>
            <div className="test-detail-body">{TEST_DETAIL[test]}</div>
          </div>

          <div className="run-config">
            <div className="config-block">
              <label className="config-label" htmlFor="run-sample-size">패널 규모 (합성 응답 수)</label>
              <div className="config-val">{sampleSize}<span className="unit">명</span></div>
              <div className="slider-row">
                <input id="run-sample-size" type="range" min="4" max="100" step="2" value={sampleSize}
                       className="slider"
                       style={{ "--pct": (((sampleSize - 4) / 96) * 100) + "%" }}
                       onChange={e => setSampleSize(Number(e.target.value))} />
                <span className="dim" style={{ fontSize: 12, minWidth: 50, textAlign: "right" }}>4~100명</span>
              </div>
              <div className="config-hint">기본은 8개 합성 응답입니다. 패널이 커지면 API 비용과 실행 시간이 늘어납니다.</div>
            </div>
            <div className="config-block">
              <label className="config-label" htmlFor="run-seed">샘플링 시드</label>
              <div className="config-val">{seed}</div>
              <div className="slider-row">
                <input className="input" style={{ padding: "8px 14px" }}
                       id="run-seed" value={seed} type="number"
                       onChange={e => setSeed(Number(e.target.value || 0))} />
                <button className="btn-ghost" style={{ padding: "8px 14px", fontSize: 12 }}
                        onClick={() => setSeed(Math.floor(Math.random() * 9999))}>
                  랜덤
                </button>
              </div>
              <div className="config-hint">같은 데이터·필터·시드로 패널 선택을 반복할 수 있어요. 모델의 응답까지 같아지는 것은 아닙니다.</div>
            </div>
          </div>

          <div className="dataset-card">
            <div className="dataset-mark">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L4 7v10l8 5 8-5V7z" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </div>
            <div>
              <div className="dataset-name">한국인 페르소나 데이터셋 활용</div>
              <div className="dataset-desc">
                nvidia/Nemotron-Personas-Korea에서 패널을 구성합니다. 선택된 패널의 인구 대표성은 보장하지 않습니다.
              </div>
            </div>
          </div>

          <div className="run-cta">
            <div className="cta-meta">
              <span><b>{sampleSize}명</b> 응답</span>
              <span>·</span>
              <span>실행 시간은 모델 응답과 재시도에 따라 달라집니다</span>
              <span>·</span>
              <span>실제 고객 검증 전 가설 탐색용</span>
            </div>
            <div className="row">
              <button className="btn" onClick={goBack}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M19 12H5M11 5l-7 7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                돌아가서 다시 적기
              </button>
              <button className="btn btn-primary" onClick={() => onRun({ test, sampleSize, seed })} disabled={running}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M8 5v14l11-7z" fill="currentColor" />
                </svg>
                {running ? (progress?.message || '시뮬레이션 실행 중…') : '시뮬레이션 실행'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}



export { BriefScreen, RunScreen };
