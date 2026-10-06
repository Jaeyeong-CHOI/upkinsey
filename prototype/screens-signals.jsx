import { scoreLabel } from "./research-ui.js";
import React from "react";
/* 결과(Signals) — 한눈에 보는 시장 반응 */

const { useState: useStateS, useEffect: useEffectS, useRef: useRefS } = React;

function useCount(target, duration = 1400) {
  const [v, setV] = useStateS(0);
  useEffectS(() => {
    let raf;
    const init = v;
    const startTs = performance.now();
    function tick(ts) {
      const t = Math.min(1, (ts - startTs) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setV(Math.round(init + (target - init) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line
  }, [target]);
  return v;
}

function GaugeCard({ labelKo, labelEn, value, unit, sub, delta, prev, deltaUnit = "p" }) {
  const isNum = typeof value === "number";
  const animated = useCount(isNum ? value : 0);
  const display = isNum ? animated : value ?? "미제공";

  const trendCls = delta > 0 ? "" : delta < 0 ? " neg" : " neu";
  const trendIcon = delta > 0 ? "↑" : delta < 0 ? "↓" : "→";

  const [filled, setFilled] = useStateS(0);
  useEffectS(() => {
    setFilled(0);
    const t = setTimeout(() => setFilled(isNum ? value : 0), 120);
    return () => clearTimeout(t);
  }, [value, isNum]);

  return (
    <div className="gauge-card">
      <div className="gauge-label">
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent-bright)", boxShadow: "0 0 8px var(--accent-bright)" }}></span>
        {labelKo}
        <span className="gauge-label-en">{labelEn}</span>
      </div>
      <div className="gauge-bigval">
        {display}{isNum && unit && <span className="unit">{unit}</span>}
      </div>
      <div className="gauge-sub">{sub}</div>
      {isNum && <div className="gauge-ring">
        <div className="gauge-ring-fill"
             style={{ width: filled + "%", transition: "width 1.4s cubic-bezier(0.2, 0.8, 0.2, 1)" }}></div>
      </div>}

      {delta !== undefined && delta !== null && (
        <div className={"gauge-trend" + trendCls}>
          <span>{trendIcon}</span>
          <span>{delta > 0 ? "+" : ""}{delta}{isNum ? deltaUnit : ""}</span>
          <span style={{ opacity: 0.6, marginLeft: 4 }}>지난 실행 {prev}</span>
        </div>
      )}


    </div>
  );
}

function SignalsScreen({ goNext, goBack, data = null, versions = null, result = null }) {
  const D = data;
  const V = versions || [];
  const report = result?.report || {};
  const decision = report.decision_board?.recommendation || report.decision_board?.decision || D.decision?.current || "다음 검증 필요";
  const executiveSummary = report.executive_summary || `${D.calls?.done || 0}개 합성 응답 기준 채택 의향 ${scoreLabel(D.adoption.value)}, 문제 적합도 ${scoreLabel(D.needFit.value)}입니다.`;

  const [animKey, setAnimKey] = useStateS(0);
  useEffectS(() => { setAnimKey(k => k + 1); }, []);

  return (
    <div className="page" data-screen-label="03 Signals">
      <div className="page-head">
        <div className="page-eyebrow">3단계 · 결과 확인</div>
        <h1 className="page-title">시장은<br /><em>제품을 어떻게 받았나요?</em></h1>
        <p className="page-sub">{D.calls?.done || 0}개의 합성 응답을 정리했어요. 실제 고객 반응이나 구매 확률은 아닙니다. 권장 액션은 {decision}입니다.</p>
      </div>

      <div className="action-card">
        <div className="action-eyebrow">한 줄 결론</div>
        <div className="action-headline">
          긍정 {D.distribution.positive}%, 중립 {D.distribution.neutral}%, 회의적 {D.distribution.negative}%. <span className="hi">실제 인터뷰로 확인할 가설입니다.</span>
        </div>
        <div className="action-body">
          {executiveSummary}
        </div>
        <div className="action-cta">
          <button className="btn btn-primary" onClick={goNext}>
            응답자 한 명씩 들어가보기
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
          <button className="btn" onClick={goBack}>
            다시 실행하기
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 28 }}>
        <div className="card-head">
          <span className="card-tag">3단계</span>
          <div>
            <div className="card-title">핵심 지표 3가지</div>
            <div className="card-sub">{D.calls?.done || 0}개의 합성 응답을 100점 척도로 정리했어요. 모델이 생성한 점수이며, 실제 구매 확률이나 시장 통계가 아닙니다.</div>
          </div>
        </div>
        <div className="card-body">

          <div className="signals-grid">
            <GaugeCard
              labelKo="채택 의향" labelEn="Adoption"
              value={D.adoption.value} unit="점"
              sub="모델이 평가한 합성 채택 의향"
              delta={D.adoption.delta} prev={D.adoption.prev} />
            <GaugeCard
              labelKo="문제 적합도" labelEn="Need fit"
              value={D.needFit.value} unit="점"
              sub="모델이 평가한 문제 적합도"
              delta={D.needFit.delta} prev={D.needFit.prev} />
            <GaugeCard
              labelKo="가격 부담" labelEn="Price risk"
              value={D.priceRisk.value}
              sub="가격 때문에 멈칫하는 정도"
              delta={null} />
          </div>

          <div className="dist-block" key={animKey}>
            <div className="dist-head">
              <div>
                <div className="dist-title">{D.calls?.done || 0}개의 합성 응답은 어떻게 나뉘나요?</div>
                <div className="dim" style={{ fontSize: 12, marginTop: 4 }}>채택 점수 65 이상은 긍정, 40 미만은 회의적, 그 사이는 중립입니다.</div>
              </div>
              <div className="dist-meta">비율 % · 반올림으로 합계가 100%와 다를 수 있음</div>
            </div>
            <div className="dist-row">
              <div className="dist-label">
                <span className="dist-dot pos"></span>
                <div>
                  <div>마음에 들어 함</div>
                  <div className="dist-sub">{D.distributionCounts?.positive ?? 0}개 · 합성 점수 높음</div>
                </div>
              </div>
              <div className="dist-bar"><div className="dist-bar-fill pos" style={{ "--w": D.distribution.positive + "%" }}></div></div>
              <div className="dist-val">{D.distribution.positive}%</div>
            </div>
            <div className="dist-row">
              <div className="dist-label">
                <span className="dist-dot neu"></span>
                <div>
                  <div>지켜보는 중</div>
                  <div className="dist-sub">{D.distributionCounts?.neutral ?? 0}개 · 정보 더 필요</div>
                </div>
              </div>
              <div className="dist-bar"><div className="dist-bar-fill neu" style={{ "--w": D.distribution.neutral + "%" }}></div></div>
              <div className="dist-val">{D.distribution.neutral}%</div>
            </div>
            <div className="dist-row">
              <div className="dist-label">
                <span className="dist-dot neg"></span>
                <div>
                  <div>회의적</div>
                  <div className="dist-sub">{D.distributionCounts?.negative ?? 0}개 · 합성 점수 낮음</div>
                </div>
              </div>
              <div className="dist-bar"><div className="dist-bar-fill neg" style={{ "--w": D.distribution.negative + "%" }}></div></div>
              <div className="dist-val">{D.distribution.negative}%</div>
            </div>
          </div>

          <div style={{ height: 12 }}></div>

          <div className="row between" style={{ marginBottom: 20 }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.015em" }}>현재 실행 기록</div>
              <div className="dim" style={{ fontSize: 13, marginTop: 4 }}>현재 열려 있는 실행의 저장 정보입니다. 이전 실행 비교는 이 화면에 표시하지 않습니다.</div>
            </div>
          </div>

          <div className="timeline">
            {V.map(v => (
              <div key={v.id} className={"timeline-item" + (v.current ? " current" : "")}>
                <div className="version-card">
                  <div className="vc-head">
                    <div>
                      <div className="vc-name">{v.name}</div>
                      <div className="vc-time">{v.type} · {v.time}</div>
                    </div>
                    <div className="vc-id" title="실행 ID">#{v.shortId}</div>
                  </div>
                  <div className="vc-tags">
                    <span className={"vc-tag" + (v.current ? " accent" : "")}>채택 의향 {scoreLabel(v.adoption)}</span>
                    <span className={"vc-tag" + (v.current ? " accent" : "")}>문제 적합도 {scoreLabel(v.need)}</span>
                    <span className="vc-tag">가격 부담 {v.price}</span>
                    <span className="vc-tag">→ {v.decision}</span>
                  </div>
                  <div className="vc-meta">
                    <span>근거 {v.evidence}</span>
                    <span>·</span>
                    <span>{v.calls}</span>
                    <span>·</span>
                    <span>패널 {v.panel}</span>
                    <span>·</span>
                    <span>검증 경고 {v.warnings}개</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 32 }}>
            <div className="row between" style={{ marginBottom: 14 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.005em" }}>이번 실행의 해석 가드레일</div>
                <div className="dim" style={{ fontSize: 12, marginTop: 4 }}>현재 실행의 입력·패널 점검 결과입니다. 통계적 신뢰도나 예측 정확도가 아닙니다.</div>
              </div>
              <div className="mono" style={{ fontSize: 13, color: "var(--accent-bright)" }}>근거 점검 점수 {scoreLabel(D.evidenceQuality.value)}</div>
            </div>
            <div className="diff-grid">
              <div className="diff-cell">
                <div className="diff-label">합성 응답 수</div>
                <div className="diff-change">{D.calls?.done || 0} / {D.calls?.total || 0}</div>
                <div className="diff-delta same">{D.calls?.batches || "-"} batches</div>
              </div>
              <div className="diff-cell">
                <div className="diff-label">근거 점검 점수</div>
                <div className="diff-change">{scoreLabel(D.evidenceQuality.value)}</div>
                <div className="diff-delta same">경고 {D.warnings || 0}개</div>
              </div>
              <div className="diff-cell">
                <div className="diff-label">추천 액션</div>
                <div className="diff-change">{decision}</div>
                <div className="diff-delta same">field 검증 전제</div>
              </div>
              <div className="diff-cell">
                <div className="diff-label">부분 실패</div>
                <div className="diff-change">{result?.request_budget?.failed_persona_calls || 0}건</div>
                <div className="diff-delta same">성공 응답 기준 집계</div>
              </div>
            </div>
          </div>

          <div className="row between" style={{ marginTop: 36 }}>
            <button className="btn" onClick={goBack}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M11 5l-7 7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
              실행 단계로
            </button>
            <button className="btn btn-primary" onClick={goNext}>
              4단계: 응답자 한 명씩 들여다보기
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}


export { SignalsScreen };
