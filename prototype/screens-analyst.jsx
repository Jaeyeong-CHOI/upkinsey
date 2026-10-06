import React from "react";
/* 분석가(Analyst) — persona 인터뷰 질문을 원질문에서 재설계 */

const { useState: useStateA } = React;

function aText(value) {
  return value == null ? "" : String(value).trim();
}

function aList(value) {
  if (Array.isArray(value)) return value.map(aText).filter(Boolean);
  const text = aText(value);
  return text ? [text] : [];
}

function AnalystTargetCard({ target, index }) {
  const context = target?.persona_context && typeof target.persona_context === "object" ? target.persona_context : {};
  const metaBits = [target?.meta, context.age ? `${context.age}세` : "", context.province, context.occupation].map(aText).filter(Boolean);
  const drivers = aList(target?.positive_drivers);
  const risks = aList(target?.top_risks);
  const interests = aList(context.interests);
  const capabilities = aList(context.capabilities);
  const usedFields = aList(target?.used_persona_fields);
  const source = context.source && typeof context.source === "object" ? context.source : {};
  const sourceChips = [
    source.dataset_id ? `dataset: ${source.dataset_id}` : "",
    source.uuid ? `uuid: ${String(source.uuid).slice(0, 12)}${String(source.uuid).length > 12 ? "…" : ""}` : "",
    source.name_parse_confidence != null ? `name parse: ${source.name_parse_confidence}` : "",
  ].filter(Boolean);
  const contextRows = [
    ["Persona summary", context.persona],
    ["가족/생활 맥락", context.family_context],
    ["직업/업무 맥락", context.professional_context],
    ["목표", context.goals],
  ].map(([label, value]) => [label, aText(value)]).filter(([, value]) => value);

  return (
    <div className="analyst-target-card">
      <div className="analyst-target-head">
        <div>
          <div className="pcard-name">{target?.name || `Persona ${index + 1}`}</div>
          <div className="pcard-bio">{metaBits.slice(0, 4).join(" · ") || "선택된 응답자"}</div>
        </div>
        <span className="chip">{target?.stance || "분석 대상"}</span>
      </div>
      <div className="analyst-target-reason">{target?.reason || target?.signal || "분석 질문에 맞춰 선택"}</div>
      <div className="analyst-target-kpis">
        <div><span>제품 이해도</span><strong>{target?.understanding_score ?? "-"}%</strong></div>
        <div><span>채택 의향</span><strong>{target?.adoption_likelihood ?? "-"}%</strong></div>
        <div><span>문제 적합도</span><strong>{target?.need_fit_score ?? "-"}%</strong></div>
        <div><span>가격 부담</span><strong>{target?.price_resistance || "-"}</strong></div>
      </div>
      {target?.concern && <p className="analyst-target-concern">“{target.concern}”</p>}
      {target?.next_validation_question && <div className="analyst-target-nextq"><b>다음 질문</b><span>{target.next_validation_question}</span></div>}
      {(drivers.length || risks.length || interests.length || capabilities.length) ? (
        <div className="analyst-target-chips">
          {drivers.map(v => <em key={`d-${v}`}>동기: {v}</em>)}
          {risks.map(v => <em key={`r-${v}`}>우려: {v}</em>)}
          {interests.map(v => <em key={`i-${v}`}>관심: {v}</em>)}
          {capabilities.map(v => <em key={`c-${v}`}>역량: {v}</em>)}
        </div>
      ) : null}
      {(contextRows.length || usedFields.length || sourceChips.length) ? (
        <details className="analyst-target-source" open>
          <summary><span>시뮬레이션에 사용한 원본 페르소나</span><small>4단계와 동일한 prompt context</small></summary>
          <div className="analyst-target-source-basics">
            {[["나이", context.age], ["지역", context.province], ["직업", context.occupation]].map(([label, value]) => aText(value) ? <div key={label}><small>{label}</small><strong>{value}</strong></div> : null)}
          </div>
          {contextRows.map(([label, value]) => <div className="analyst-target-source-row" key={label}><b>{label}</b><p>{value}</p></div>)}
          {(interests.length || capabilities.length || usedFields.length || sourceChips.length) ? (
            <div className="analyst-target-source-chips">
              {interests.length > 0 && <div><span>관심사</span>{interests.map(v => <em key={`si-${v}`}>{v}</em>)}</div>}
              {capabilities.length > 0 && <div><span>역량</span>{capabilities.map(v => <em key={`sc-${v}`}>{v}</em>)}</div>}
              {usedFields.length > 0 && <div><span>반응 근거 필드</span>{usedFields.map(v => <em key={`su-${v}`}>{v}</em>)}</div>}
              {sourceChips.length > 0 && <div><span>출처 메타</span>{sourceChips.map(v => <em key={`ss-${v}`}>{v}</em>)}</div>}
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}

function AnalystChatTranscript({ conversation, index }) {
  const messages = Array.isArray(conversation?.messages) ? conversation.messages : [];
  return (
    <div className="analyst-chat-card">
      <div className="analyst-chat-head">
        <div>
          <strong>{conversation?.persona_name || `Persona ${index + 1}`}</strong>
          {conversation?.question_plan?.objective && <p>{conversation.question_plan.objective}</p>}
        </div>
        <span className="chip">{conversation?.round_count || 1}회 대화</span>
      </div>
      <div className="analyst-chat-stream">
        {messages.map((m, j) => {
          const isAnalyst = m.role === "analyst";
          return (
            <div className={"analyst-chat-row " + (isAnalyst ? "analyst" : "persona")} key={j}>
              <div className="analyst-chat-speaker">{isAnalyst ? "인터뷰어" : (conversation?.persona_name || "응답자")}</div>
              <div className="analyst-chat-bubble">{m.content}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AnalystScreen({ result, analystResult, onAsk, goBack, goNext }) {
  const [question, setQuestion] = useStateA("가격보다 신뢰가 더 큰 장벽인지, 페르소나들에게 직접 물어보고 근거를 수합해줘.");
  const [loading, setLoading] = useStateA(false);
  const [error, setError] = useStateA("");
  const conversations = analystResult?.conversations || [];
  const targets = analystResult?.target_personas || [];
  const synthesis = analystResult?.synthesis || {};

  async function submit() {
    if (!question.trim() || loading || !result) return;
    setLoading(true);
    setError("");
    try {
      await onAsk(question.trim());
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page" data-screen-label="05 Analyst">
      <div className="page-head">
        <div className="page-eyebrow">5단계 · 분석가 질문 설계</div>
        <h1 className="page-title">원질문을 그대로 묻지 않고<br /><em>답을 얻는 질문으로 바꿉니다</em></h1>
        <p className="page-sub">분석 에이전트가 질문에 맞는 페르소나를 먼저 고르고, 각 페르소나에게 최적화된 질문과 후속 질문을 던진 뒤 의견을 수합합니다.</p>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-tag">5단계</span>
          <div>
            <div className="card-title">분석가에게 물어볼 원질문</div>
            <div className="card-sub">이 문장은 페르소나에게 그대로 전달되지 않습니다. 분석가가 목적을 해석하고 질문 계획으로 변환합니다.</div>
          </div>
        </div>
        <div className="card-body">
          <textarea className="textarea" rows="3" value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: 가격이 제일 큰 장벽인지, 아니면 신뢰/사용성이 더 큰 장벽인지 확인해줘." />
          {!result && <div className="callout" style={{ marginTop: 14 }}><div className="callout-eyebrow">먼저 실행 필요</div><div className="callout-text">Layer 2에서 시뮬레이션을 실행한 뒤 분석가 인터뷰를 보낼 수 있습니다.</div></div>}
          {error && <div className="callout" style={{ marginTop: 14 }}><div className="callout-eyebrow">Analyst API 오류</div><div className="callout-text">{error}</div></div>}
          <div className="row between" style={{ marginTop: 18 }}>
            <button className="btn" onClick={goBack}>응답자 화면으로</button>
            <button className="btn btn-primary" onClick={submit} disabled={!result || loading}>{loading ? "인터뷰 중…" : "분석가 인터뷰 실행"}</button>
          </div>
        </div>
      </div>

      {analystResult && (
        <div className="report-layout" style={{ marginTop: 28 }}>
          <aside className="report-toc">
            <div className="toc-label">수합 결과</div>
            <button className="toc-item active"><span className="toc-num">1</span>요약</button>
            <button className="toc-item"><span className="toc-num">2</span>타깃</button>
            <button className="toc-item"><span className="toc-num">3</span>대화 기록</button>
          </aside>
          <div className="report-main">
            <section className="story-section in">
              <div className="story-num">1. 수합된 의견</div>
              <h2 className="story-h">{synthesis.summary || "분석가 인터뷰 결과"}</h2>
              {Array.isArray(synthesis.opinion_groups) && synthesis.opinion_groups.length ? (
                <div className="kpi-grid">{synthesis.opinion_groups.slice(0, 4).map((g, i) => <div className="kpi" key={i}><div className="l">의견 그룹</div><div className="v accent" style={{ fontSize: 22 }}>{g.label || g.theme || `그룹 ${i+1}`}</div><p className="dim">{g.summary || g.description || ""}</p></div>)}</div>
              ) : null}
            </section>

            <section className="story-section in">
              <div className="story-num">2. 먼저 선택한 타깃 페르소나</div>
              <div className="analyst-target-grid">
                {targets.map((t, i) => <AnalystTargetCard target={t} index={i} key={i} />)}
              </div>
            </section>

            <section className="story-section in">
              <div className="story-num">3. 실제 대화 기록</div>
              <div className="analyst-chat-list">
                {conversations.map((c, i) => <AnalystChatTranscript conversation={c} index={i} key={i} />)}
              </div>
            </section>

            <div className="row between" style={{ marginTop: 32 }}>
              <button className="btn" onClick={goBack}>응답자 다시 보기</button>
              <button className="btn btn-primary" onClick={goNext}>6단계: 리포트 보기</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


export { AnalystScreen };
