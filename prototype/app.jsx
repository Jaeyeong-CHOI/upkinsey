import React from "react";
import { EXAMPLE_BRIEF } from "./data.js";
import { BriefScreen, RunScreen } from "./screens-brief-run.jsx";
import { SignalsScreen } from "./screens-signals.jsx";
import { PersonasScreen } from "./screens-personas.jsx";
import { AnalystScreen } from "./screens-analyst.jsx";
import { ReportScreen } from "./screens-report.jsx";
import { apiPath, toBackendBrief, briefFingerprint, sleep, mapResultToResonance, readApiResponse } from "./research-ui.js";
/* 업킨지 앤 컴퍼니 — Drive zip UI wired to live backend */

const { useState, useEffect, useRef } = React;

const LAYERS = [
  { id: "brief",    num: "1", name: "입력",     sub: "제품 정보를 적어요" },
  { id: "run",      num: "2", name: "실행",     sub: "응답자에게 보여줘요" },
  { id: "signals",  num: "3", name: "결과",     sub: "시장 반응을 봐요" },
  { id: "personas", num: "4", name: "응답자",   sub: "한 명씩 들여다봐요" },
  { id: "analyst",  num: "5", name: "분석가",   sub: "질문을 다시 설계해요" },
  { id: "report",   num: "6", name: "리포트",   sub: "다음 액션을 정해요" }
];

const APPEARANCE_DEFAULTS = {
  "theme": "dark",
  "personaMode": "constellation"
};

const SESSION_KEY = "upkinsey.resonance.session.v2";
const SESSION_SCHEMA_VERSION = 3;
const RESULT_LAYERS = new Set(["signals", "personas", "analyst", "report"]);

function loadSession() {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    return {};
  }
}

function saveSessionPatch(patch) {
  try {
    const current = loadSession();
    window.localStorage.setItem(SESSION_KEY, JSON.stringify({ ...current, ...patch, schemaVersion: SESSION_SCHEMA_VERSION, savedAt: new Date().toISOString() }));
  } catch (err) {
    // No-account persistence is best-effort; never block the simulation UI.
  }
}

function clearSession() {
  try { window.localStorage.removeItem(SESSION_KEY); } catch (err) {}
}


function NoResultScreen({ goRun, message = "먼저 제품 정보를 입력하고 시뮬레이션을 실행해주세요." }) {
  return (
    <div className="page" data-screen-label="No Result">
      <div className="page-head">
        <div className="page-eyebrow">실행 결과 필요</div>
        <h1 className="page-title">아직 보여줄<br /><em>실제 시뮬레이션 결과가 없어요</em></h1>
        <p className="page-sub">{message} 데모 데이터와 실제 결과가 섞이지 않도록 이 화면은 실행 후에만 열립니다.</p>
      </div>
      <div className="action-card">
        <div className="action-eyebrow">다음 단계</div>
        <div className="action-headline">제품 brief를 확인한 뒤 Solar/Nemotron 패널을 실행하세요.</div>
        <div className="action-cta"><button className="btn btn-primary" onClick={goRun}>실행 단계로 이동</button></div>
      </div>
    </div>
  );
}

function Nav({ current, setCurrent, apiState, onReset, theme, onToggleTheme }) {
  return (
    <nav className="nav" data-screen-label="Nav">
      <div className="shell nav-inner">
        <a className="brand" href="index.html" title="업킨지 앤 컴퍼니 홈으로">
          <div className="brand-mark">
            <svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="16" r="6" stroke="currentColor" strokeWidth="1.5" /><circle cx="16" cy="16" r="11" stroke="currentColor" strokeWidth="1.2" opacity="0.6" /><circle cx="16" cy="16" r="2.2" fill="currentColor" /></svg>
          </div>
          <span>업킨지 앤 컴퍼니</span>
          <span className="brand-sub">출시 전 시장 반응 시뮬레이션</span>
        </a>
        <div className="nav-layers">
          {LAYERS.map((L, i) => {
            const curIdx = LAYERS.findIndex(l => l.id === current);
            const done = i < curIdx;
            return <button key={L.id} className={"layer-pill" + (L.id === current ? " active" : "") + (done ? " done" : "")} onClick={() => setCurrent(L.id)} title={L.sub}><span className="num">{L.num}</span><span>{L.name}</span></button>;
          })}
        </div>
        <div className="nav-right">
          <div className="live-pill" role="status" aria-live="polite" title={apiState.detail || "실시간 API 상태"}>
            <div className="live-dot" data-status={apiState.status || "checking"} aria-hidden="true"></div><span>{apiState.label || "Live API"}</span><span className="ver">· 세션 자동 저장</span>
          </div>
          <button type="button" className="reset-pill" onClick={onToggleTheme} aria-label={theme === "dark" ? "밝은 테마로 전환" : "어두운 테마로 전환"}>{theme === "dark" ? "밝게" : "어둡게"}</button>
          <button className="reset-pill" onClick={onReset} title="현재 브라우저에 저장된 세션을 지우고 새로 시작">새 세션</button>
        </div>
      </div>
    </nav>
  );
}

function App() {
  const restoredSession = React.useMemo(loadSession, []);
  const activeRunRef = useRef(null);
  const activeAbortRef = useRef(null);
  const analystProgressTimerRef = useRef(null);
  const [appearance, setAppearance] = useState(APPEARANCE_DEFAULTS);
  const [current, setCurrent] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const start = params.get("start");
    if (start && LAYERS.find(l => l.id === start)) return start;
    if (restoredSession.current && LAYERS.find(l => l.id === restoredSession.current)) return restoredSession.current;
    return "brief";
  });
  const [brief, setBrief] = useState(() => restoredSession.brief || { ...EXAMPLE_BRIEF });
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(null);
  const [analystRunning, setAnalystRunning] = useState(false);
  const [analystProgress, setAnalystProgress] = useState(null);
  const [result, setResult] = useState(() => restoredSession.result || null);
  const [analystResult, setAnalystResult] = useState(() => restoredSession.analystResult || null);
  const [parseStatus, setParseStatus] = useState(null);
  const [apiState, setApiState] = useState({ status: "checking", label: "API 확인 중", detail: "Solar backend 상태 확인 중" });
  const [error, setError] = useState("");

  const currentBriefHash = briefFingerprint(brief);
  const resultStale = Boolean(result && result.__briefHash !== currentBriefHash);
  const liveResult = result && !resultStale ? result : null;
  const liveData = liveResult ? mapResultToResonance(liveResult, brief) : null;
  const personas = liveResult ? liveData.personas : [];
  const goTo = (id) => {
    if (RESULT_LAYERS.has(id) && !liveResult) {
      setCurrent("run");
      setError(resultStale ? "제품 정보가 바뀌어 이전 결과를 숨겼어요. 다시 실행해주세요." : "먼저 시뮬레이션을 실행해주세요.");
      return;
    }
    setCurrent(id);
  };
  const cancelActiveRun = () => {
    activeRunRef.current = null;
    if (activeAbortRef.current) activeAbortRef.current.abort();
    activeAbortRef.current = null;
  };
  const updateBrief = (next) => {
    cancelActiveRun();
    setRunning(false);
    setProgress(null);
    setBrief(prev => (typeof next === "function" ? next(prev) : next));
    setResult(null);
    setAnalystResult(null);
    setParseStatus(null);
    setError("");
  };
  const resetSession = () => {
    cancelActiveRun();
    if (analystProgressTimerRef.current) clearInterval(analystProgressTimerRef.current);
    analystProgressTimerRef.current = null;
    clearSession();
    setCurrent("brief");
    setBrief({ ...EXAMPLE_BRIEF });
    setResult(null);
    setAnalystResult(null);
    setParseStatus(null);
    setRunning(false);
    setProgress(null);
    setAnalystRunning(false);
    setAnalystProgress(null);
    setError("");
  };

  useEffect(() => { document.documentElement.dataset.theme = appearance.theme; }, [appearance.theme]);
  useEffect(() => { window.scrollTo({ top: 0, behavior: "smooth" }); }, [current]);
  useEffect(() => { checkApiHealth(); return () => { cancelActiveRun(); if (analystProgressTimerRef.current) clearInterval(analystProgressTimerRef.current); }; }, []);
  useEffect(() => {
    if (RESULT_LAYERS.has(current) && !liveResult) setCurrent("run");
  }, [current, liveResult]);
  useEffect(() => {
    saveSessionPatch({ current, brief, result: liveResult, analystResult, briefHash: currentBriefHash });
  }, [current, brief, liveResult, analystResult, currentBriefHash]);

  async function checkApiHealth() {
    try {
      const response = await fetch(apiPath("/api/health"));
      const health = await readApiResponse(response);
      if (!health.ok) throw new Error("API key missing");
      setApiState({ status: "ready", label: "API Configured", detail: `${health.model || "configured model"} · server key configured` });
    } catch (err) {
      setApiState({ status: "error", label: "API Not Ready", detail: String(err.message || err) });
    }
  }

  async function pollJob(jobId, runToken, signal) {
    const started = Date.now();
    while (true) {
      if (activeRunRef.current !== runToken) throw new DOMException("stale run ignored", "AbortError");
      if (Date.now() - started > 20 * 60 * 1000) throw new Error("시뮬레이션 시간이 너무 오래 걸려 중단했어요. 패널 크기를 줄이거나 잠시 후 다시 시도해주세요.");
      const response = await fetch(apiPath(`/api/simulate/jobs/${encodeURIComponent(jobId)}`), { signal });
      const job = await readApiResponse(response);
      if (activeRunRef.current === runToken) setProgress(job);
      if (job.status === "done") return job.result;
      if (job.status === "error") throw new Error(job.message || job.error || "simulation failed");
      await sleep(850, signal);
    }
  }

  async function parseDocumentBrief(file) {
    if (!file) return null;
    setParseStatus(null);
    if (!/\.pdf$/i.test(file.name || "") && file.type !== "application/pdf") {
      throw new Error("PDF 파일만 업로드할 수 있어요.");
    }
    setParseStatus({ state: "uploading", message: "PDF를 Document Parse API로 읽는 중…", sourceFileName: file.name || "document.pdf" });
    setError("");
    setResult(null);
    setAnalystResult(null);
    try {
      const form = new FormData();
      form.append("file", file, file.name || "document.pdf");
      const response = await fetch(apiPath("/api/document-brief"), { method: "POST", body: form });
      const data = await readApiResponse(response);
      const extracted = data.brief || {};
      setBrief(prev => ({
        ...prev,
        productName: extracted.productName || "",
        description: extracted.description || "",
        features: Array.isArray(extracted.features) ? extracted.features : [],
        pricing: Array.isArray(extracted.pricing) ? extracted.pricing : [],
        target: extracted.target || "",
        alternatives: extracted.alternatives || "",
        hypothesis: extracted.hypothesis || "",
      }));
      setParseStatus({
        state: "done",
        message: "AI가 PDF에서 제품 정보를 채웠어요.",
        sourceFileName: file.name || "document.pdf",
        confidence: extracted.confidence || 0,
        evidence: extracted.evidence || [],
        textLength: data.document_parse?.text_length || 0,
      });
      setApiState({ status: "ready", label: "Live API Ready", detail: "Document Parse 완료" });
      return data;
    } catch (err) {
      setParseStatus({ state: "error", message: String(err.message || err) });
      throw err;
    }
  }

  async function runSimulation(config = {}) {
    cancelActiveRun();
    const runToken = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const controller = new AbortController();
    activeRunRef.current = runToken;
    activeAbortRef.current = controller;
    setRunning(true);
    setError("");
    setAnalystResult(null);
    setResult(null);
    setApiState({ status: "busy", label: "API 실행 중", detail: "Solar Pro 3 persona 응답 생성 중" });
    try {
      const backendBrief = toBackendBrief(brief, config);
      const runBriefHash = briefFingerprint(brief);
      const response = await fetch(apiPath("/api/simulate/start"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(backendBrief), signal: controller.signal });
      const job = await readApiResponse(response);
      const payload = await pollJob(job.job_id, runToken, controller.signal);
      if (activeRunRef.current !== runToken) return;
      const ownedPayload = { ...payload, __briefHash: runBriefHash };
      setResult(ownedPayload);
      setApiState({ status: "ready", label: "방금 저장됨", detail: ownedPayload?.version?.version_id || "simulation complete" });
      setCurrent("signals");
    } catch (err) {
      if (err?.name === "AbortError" || activeRunRef.current !== runToken) return;
      setError(String(err.message || err));
      setApiState({ status: "error", label: "API 실패", detail: String(err.message || err) });
    } finally {
      if (activeRunRef.current === runToken) {
        activeRunRef.current = null;
        activeAbortRef.current = null;
        setRunning(false);
        setProgress(null);
      }
    }
  }

  async function askAnalyst(question) {
    if (!liveResult) return null;
    if (analystProgressTimerRef.current) clearInterval(analystProgressTimerRef.current);
    const messages = [
      "질문 의도를 분석하고 적합한 응답자를 고르는 중",
      "선택한 페르소나별 인터뷰 질문을 다시 쓰는 중",
      "응답자에게 후속 질문을 던지고 대화 맥락을 모으는 중",
      "의견 그룹과 공통 근거를 수합하는 중",
    ];
    let tick = 0;
    setAnalystRunning(true);
    setAnalystProgress({ percent: 10, message: messages[0] });
    analystProgressTimerRef.current = setInterval(() => {
      tick += 1;
      setAnalystProgress(prev => {
        const nextPercent = Math.min(92, Number(prev?.percent || 10) + (tick < 8 ? 6 : 3));
        const msg = messages[Math.min(messages.length - 1, Math.floor(nextPercent / 28))];
        return { percent: nextPercent, message: msg };
      });
    }, 900);
    try {
      const response = await fetch(apiPath("/api/analyst-question"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ brief: toBackendBrief(brief, {}), question, persona_reactions: liveResult.persona_reactions || liveResult.personas || [], target_limit: 4, max_rounds: 5 })
      });
      const data = await readApiResponse(response);
      setAnalystProgress({ percent: 100, message: "분석가 인터뷰 결과를 정리했어요" });
      setAnalystResult(data);
      return data;
    } finally {
      if (analystProgressTimerRef.current) clearInterval(analystProgressTimerRef.current);
      analystProgressTimerRef.current = null;
      setTimeout(() => {
        setAnalystRunning(false);
        setAnalystProgress(null);
      }, 350);
    }
  }

  async function personaChat(persona, message, history) {
    const response = await fetch(apiPath("/api/persona-chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief: toBackendBrief(brief, {}), persona: persona.__raw || persona, message, history: history.slice(-8) })
    });
    const data = await readApiResponse(response);
    return data.reply;
  }

  return (
    <>
      <Nav current={current} setCurrent={goTo} apiState={apiState} onReset={resetSession} theme={appearance.theme} onToggleTheme={() => setAppearance(prev => ({ ...prev, theme: prev.theme === "dark" ? "light" : "dark" }))} />
      <main className="shell">
        {error && <div className="callout" style={{ marginTop: 24 }}><div className="callout-eyebrow">API 오류</div><div className="callout-text">{error}</div></div>}
        {resultStale && <div className="callout" style={{ marginTop: 24 }}><div className="callout-eyebrow">결과 숨김</div><div className="callout-text">제품 정보가 바뀌어 이전 시뮬레이션 결과를 표시하지 않습니다. 새로 실행해주세요.</div></div>}
        {current === "brief"    && <BriefScreen brief={brief} setBrief={updateBrief} goNext={() => goTo("run")} onParseDocument={parseDocumentBrief} parseStatus={parseStatus} />}
        {current === "run"      && <RunScreen brief={brief} onRun={runSimulation} goBack={() => goTo("brief")} running={running} progress={progress} />}
        {current === "signals"  && (liveResult ? <SignalsScreen data={liveData.signals} versions={liveData.versions} result={liveResult} goNext={() => goTo("personas")} goBack={() => goTo("run")} /> : <NoResultScreen goRun={() => goTo("run")} />)}
        {current === "personas" && (liveResult ? <PersonasScreen personas={personas} mode={appearance.personaMode} setMode={(m)=>setAppearance(prev => ({ ...prev, personaMode: m }))} goNext={() => goTo("analyst")} goBack={() => goTo("signals")} onPersonaChat={personaChat} /> : <NoResultScreen goRun={() => goTo("run")} />)}
        {current === "analyst"  && (liveResult ? <AnalystScreen result={liveResult} analystResult={analystResult} onAsk={askAnalyst} goBack={() => goTo("personas")} goNext={() => goTo("report")} /> : <NoResultScreen goRun={() => goTo("run")} />)}
        {current === "report"   && (liveResult ? <ReportScreen result={liveResult} data={liveData} goBack={() => goTo("analyst")} goRestart={resetSession} /> : <NoResultScreen goRun={() => goTo("run")} />)}
      </main>
      <footer className="shell" style={{ padding: "24px 0", color: "var(--text-3)", fontSize: 12 }}>합성 응답은 실제 소비자 조사나 구매 확률이 아닙니다. · <a href="https://github.com/Jaeyeong-CHOI/upkinsey" target="_blank" rel="noopener noreferrer">GitHub · 기여하기</a></footer>
      {running && <SimulationOverlay progress={progress} title="시장에 제품을 던지고 있어요" defaultMessage="합성 응답자가 제품을 처음 듣고 있어요…" />}
      {analystRunning && <SimulationOverlay progress={analystProgress} title="분석가가 인터뷰를 진행하고 있어요" defaultMessage="응답자를 고르고 질문을 다시 설계하는 중…" />}

    </>
  );
}

function SimulationOverlay({ progress, title = "시장에 제품을 던지고 있어요", defaultMessage = "합성 응답자가 제품을 처음 듣고 있어요…" }) {
  const canvasRef = useRef(null);
  const percent = Math.max(0, Math.min(100, Number(progress?.percent ?? 12)));
  const thought = progress?.message || defaultMessage;
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = 280, H = 280;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let raf, t = 0;
    function tick() {
      t += 0.016;
      ctx.clearRect(0,0,W,H);
      ctx.strokeStyle = "rgba(132,120,232,.18)"; ctx.lineWidth = 1;
      for (let i=0;i<10;i++) { const a=(i/10)*Math.PI*2+t; const x=W/2+Math.cos(a)*90; const y=H/2+Math.sin(a)*70; ctx.beginPath(); ctx.arc(x,y,5+(i%3),0,Math.PI*2); ctx.stroke(); }
      ctx.fillStyle = "rgba(132,120,232,.9)"; ctx.beginPath(); ctx.arc(W/2,H/2,8,0,Math.PI*2); ctx.fill();
      raf=requestAnimationFrame(tick);
    }
    tick();
    return () => cancelAnimationFrame(raf);
  }, []);
  return <div className="sim-overlay"><div className="sim-stage"><canvas ref={canvasRef}></canvas><div className="sim-headline">{title}</div><div className="sim-thought">{thought}</div><div className="sim-progress"><div className="sim-progress-bar" style={{ width: percent + "%" }}></div></div></div></div>;
}

export default App;
