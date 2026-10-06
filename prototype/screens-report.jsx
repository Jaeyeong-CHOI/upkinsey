/* 리포트(Report) — 스토리 모드로 풀리는 인사이트 */
/* global React, RESONANCE_DATA */

const { useState: useStateR, useEffect: useEffectR, useRef: useRefR } = React;

const SECTIONS = [
  { id: "summary",   num: "1", label: "한 줄 결론" },
  { id: "verdict",   num: "2", label: "다음 액션" },
  { id: "evidence",  num: "3", label: "데이터는 충분한가요" },
  { id: "objections",num: "4", label: "시장이 멈추는 이유 5가지" },
  { id: "drivers",   num: "5", label: "그래도 살아남은 단어들" },
  { id: "next",      num: "6", label: "다음 4주의 실험" },
  { id: "interview", num: "7", label: "진짜 사람 인터뷰 계획" },
  { id: "survey",    num: "8", label: "설문 초안" }
];

function CountUp({ target, duration = 1400, suffix = "" }) {
  const [v, setV] = useStateR(0);
  const ref = useRefR(null);
  const [started, setStarted] = useStateR(false);

  useEffectR(() => {
    if (!ref.current) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => { if (e.isIntersecting) setStarted(true); });
    }, { threshold: 0.4 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  useEffectR(() => {
    if (!started) return;
    let raf;
    const startTs = performance.now();
    function tick(ts) {
      const t = Math.min(1, (ts - startTs) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setV(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [started, target, duration]);

  return <span ref={ref}>{v}{suffix}</span>;
}

function listR(value) {
  if (Array.isArray(value)) return value.filter(v => v !== null && v !== undefined && String(v).trim());
  if (value === null || value === undefined || value === "") return [];
  return [value];
}
function textR(value, fallback = "") {
  return String(value === null || value === undefined || value === "" ? fallback : value);
}
function objectionText(item) {
  if (typeof item === "string") return item;
  return textR(item?.objection || item?.category || item?.risk || item?.summary, "추가 검증이 필요한 장벽");
}
function experimentText(item) {
  if (typeof item === "string") return item;
  return textR(item?.experiment || item?.hypothesis || item?.test || item?.action || item?.summary, "다음 검증 실험");
}
function surveyQuestionText(item) {
  if (typeof item === "string") return item;
  return textR(item?.question || item?.prompt || item?.id, "검증 질문");
}
function ReportScreen({ goBack, goRestart, result = null, data = null }) {
  const [activeId, setActiveId] = useStateR("summary");
  const sectionRefs = useRefR({});
  const signals = data.signals;
  const report = result?.report || {};
  const evidence = report.evidence_quality || result?.evidence_quality || {};
  const budget = report.request_budget || result?.request_budget || {};
  const decisionBoard = report.decision_board || {};
  const founderMemo = report.founder_memo || result?.founder_memo || {};
  const validationPlan = report.validation_plan || {};
  const screener = report.recruiting_screener || {};
  const interviewGuide = report.interview_discussion_guide || {};
  const survey = report.validation_survey || {};
  const executiveSummary = report.executive_summary || founderMemo.headline || `${budget.actual_persona_count || signals.calls?.done || 0}명 synthetic panel 기준 결과입니다.`;
  const recommendation = decisionBoard.recommendation || decisionBoard.decision || founderMemo.recommendation || "다음 검증 필요";
  const nextStep = decisionBoard.next_step || founderMemo.decision_gate || validationPlan.next_step || "실제 사용자 검증으로 synthetic signal을 보정하세요.";
  const positiveDrivers = listR(report.positive_drivers).slice(0, 6);
  const topRisks = listR(report.top_risks).slice(0, 6);
  const objections = listR(report.objections).slice(0, 5);
  const experiments = listR(report.experiment_backlog || report.research_sprint?.experiments || validationPlan.experiments).slice(0, 4);
  const interviewQuestions = listR(interviewGuide.questions || interviewGuide.question_blocks?.flatMap(b => b.questions || []) || screener.screener_questions).slice(0, 6);
  const surveyQuestions = listR(survey.questions || survey.question_blocks?.flatMap(b => b.questions || [])).slice(0, 8);
  const warnings = listR(evidence.warnings).slice(0, 6);
  const strengths = listR(evidence.strengths).slice(0, 5);

  useEffectR(() => {
    const els = SECTIONS.map(s => sectionRefs.current[s.id]).filter(Boolean);
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          const id = e.target.dataset.sid;
          if (id) setActiveId(id);
        }
      });
    }, { threshold: 0.25, rootMargin: "-15% 0px -50% 0px" });
    els.forEach(el => io.observe(el));
    return () => io.disconnect();
  }, []);

  const scrollTo = (id) => {
    const el = sectionRefs.current[id];
    if (!el) return;
    window.scrollTo({ top: el.offsetTop - 80, behavior: "smooth" });
  };

  return (
    <div className="page" data-screen-label="06 Report">
      <div className="page-head">
        <div className="page-eyebrow">6단계 · 그래서 어떻게 할까요</div>
        <h1 className="page-title">시뮬레이션이 끝났어요<br /><em>합성 응답으로 리포트를 구성했어요</em></h1>
        <p className="page-sub">{executiveSummary}</p>
      </div>

      <div className="report-layout">
        <aside className="report-toc">
          <div className="toc-label">목차</div>
          {SECTIONS.map(s => (
            <a key={s.id} className={"toc-item" + (activeId === s.id ? " active" : "")} onClick={() => scrollTo(s.id)}>{s.num}. {s.label}</a>
          ))}
        </aside>

        <div className="report-main">
          <section className="story-section in" ref={el => sectionRefs.current.summary = el} data-sid="summary">
            <div className="story-num">1. 한 줄 결론</div>
            <h2 className="story-h">권장 액션은 <span style={{ color: "var(--accent-bright)" }}>{recommendation}</span>입니다.</h2>
            <p className="story-lead">{executiveSummary}</p>
            <div className="kpi-grid">
              <div className="kpi"><div className="l">채택 의향</div><div className="v">{signals.adoption.value == null ? "미제공" : <CountUp target={signals.adoption.value} suffix="점" />}</div></div>
              <div className="kpi"><div className="l">문제 적합도</div><div className="v">{signals.needFit.value == null ? "미제공" : <CountUp target={signals.needFit.value} suffix="점" />}</div></div>
              <div className="kpi"><div className="l">가격 부담</div><div className="v accent">{signals.priceRisk.value}</div></div>
              <div className="kpi"><div className="l">근거 점검 점수</div><div className="v warn">{evidence.score ?? signals.evidenceQuality.value}</div></div>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.verdict = el} data-sid="verdict">
            <div className="story-num">2. 다음 액션</div>
            <h2 className="story-h">Synthetic signal은 결정이 아니라 다음 검증의 우선순위입니다.</h2>
            <p className="story-lead">{textR(nextStep)}</p>
            <div className="decision-card">
              <div className="decision-eyebrow">권장 액션</div>
              <div className="decision-verdict">{recommendation}</div>
              <div className="decision-confidence">
                <span>근거 점검 점수</span>
                <div className="conf-bar"><div className="conf-bar-fill" style={{ width: `${Math.max(0, Math.min(100, Number(evidence.score ?? 0)))}%` }}></div></div>
                <span style={{ color: "var(--accent-bright)" }}>{evidence.score ?? "-"} / 100</span>
                <span className="dim">· {budget.actual_persona_count || signals.calls?.done || 0}명 응답 · 실패 {budget.failed_persona_calls || 0}건</span>
              </div>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.evidence = el} data-sid="evidence">
            <div className="story-num">3. 데이터는 충분한가요?</div>
            <h2 className="story-h">현재 근거 점검 등급은 {evidence.level || "directional"}입니다.</h2>
            <div className="story-block">
              <div className="row between" style={{ marginBottom: 14 }}>
                <span style={{ fontWeight: 500 }}>Evidence quality · {evidence.score ?? "-"}점</span>
                <span className="mono dim" style={{ fontSize: 12 }}>panel {budget.actual_persona_count || signals.calls?.done || 0}명</span>
              </div>
              <ul className="story-list" style={{ margin: 0 }}>
                {(warnings.length ? warnings : ["상위 가설은 실제 사용자 인터뷰/랜딩 테스트로 검증해야 합니다."]).map((w, i) => <li key={i}>{textR(w)}</li>)}
                {strengths.map((s, i) => <li key={`s${i}`}>강점: {textR(s)}</li>)}
              </ul>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.objections = el} data-sid="objections">
            <div className="story-num">4. 시장이 멈추는 이유 5가지</div>
            <h2 className="story-h">반복된 objection부터 줄이세요.</h2>
            <div className="story-block">
              <ul className="story-list" style={{ margin: 0 }}>
                {(objections.length ? objections.map(objectionText) : topRisks).map((risk, i) => <li key={i}>{textR(risk)}</li>)}
              </ul>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.drivers = el} data-sid="drivers">
            <div className="story-num">5. 그래도 살아남은 단어들</div>
            <h2 className="story-h">긍정 driver는 다음 메시지 실험의 재료입니다.</h2>
            <div className="kpi-grid">
              {(positiveDrivers.length ? positiveDrivers : ["명확한 문제 해결", "전환 비용 감소", "신뢰 가능한 근거"]).slice(0, 3).map((driver, i) => (
                <div className="kpi" key={i}><div className="l">키워드 {i + 1}</div><div className="v accent">{textR(driver)}</div></div>
              ))}
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.next = el} data-sid="next">
            <div className="story-num">6. 다음 4주의 실험</div>
            <h2 className="story-h">다음 실행은 아래 가설부터 좁히세요.</h2>
            <div className="story-block">
              <ul className="story-list" style={{ margin: 0 }}>
                {(experiments.length ? experiments.map(experimentText) : listR(validationPlan.recommended_next_actions || report.next_validation_questions)).slice(0, 5).map((exp, i) => <li key={i}>{textR(exp)}</li>)}
              </ul>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.interview = el} data-sid="interview">
            <div className="story-num">7. 진짜 사람 인터뷰 계획</div>
            <h2 className="story-h">Synthetic signal이 갈린 지점을 실제 사람에게 물어보세요.</h2>
            <div className="story-block">
              <ul className="story-list" style={{ margin: 0 }}>
                {(interviewQuestions.length ? interviewQuestions.map(surveyQuestionText) : listR(report.next_validation_questions)).slice(0, 6).map((q, i) => <li key={i}>{textR(q)}</li>)}
              </ul>
            </div>
          </section>

          <section className="story-section" ref={el => sectionRefs.current.survey = el} data-sid="survey">
            <div className="story-num">8. 설문 초안</div>
            <h2 className="story-h">빠른 정량 검증 문항입니다.</h2>
            <div className="story-block">
              <ul className="story-list" style={{ margin: 0 }}>
                {(surveyQuestions.length ? surveyQuestions.map(surveyQuestionText) : listR(report.next_validation_questions)).slice(0, 8).map((q, i) => <li key={i}>{textR(q)}</li>)}
              </ul>
            </div>
            <div className="callout" style={{ marginTop: 28 }}>
              <div className="callout-eyebrow">정리 한 줄</div>
              <div className="callout-text">{founderMemo.copy_paste_summary || nextStep}</div>
            </div>
          </section>

          <div className="row between" style={{ marginTop: 60, paddingTop: 32, borderTop: "1px solid var(--border)" }}>
            <button className="btn" onClick={goBack}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M11 5l-7 7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
              응답자 화면으로
            </button>
            <button className="btn btn-primary" onClick={goRestart}>
              다듬어서 다시 실행하기
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

window.ReportScreen = ReportScreen;
