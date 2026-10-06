import { PersonaPortrait } from "./persona-portrait.jsx";
import { scoreLabel, personaBio, shouldSubmitChatOnEnter } from "./research-ui.js";
import React from "react";
/* 응답자(Personas) — 별자리/카드 + 1대1 인터뷰 */

const { useState: useStateP, useEffect: useEffectP, useRef: useRefP, useMemo: useMemoP } = React;

function sourceText(value) {
  return typeof value === "string" ? value.trim() : (value == null ? "" : String(value).trim());
}

function sourceList(value) {
  if (Array.isArray(value)) return value.map(sourceText).filter(Boolean);
  const text = sourceText(value);
  return text ? [text] : [];
}

function sourceValue(context, key) {
  return sourceText(context?.[key]);
}

function PersonaSourceContext({ context, usedFields = [] }) {
  if (!context || typeof context !== "object") return null;
  const basics = [
    ["나이", sourceValue(context, "age")],
    ["지역", sourceValue(context, "province")],
    ["직업", sourceValue(context, "occupation")],
  ].filter(([, value]) => value);
  const narratives = [
    ["Persona summary", sourceValue(context, "persona")],
    ["가족/생활 맥락", sourceValue(context, "family_context")],
    ["직업/업무 맥락", sourceValue(context, "professional_context")],
    ["목표", sourceValue(context, "goals")],
  ].filter(([, value]) => value);
  const interests = sourceList(context.interests);
  const capabilities = sourceList(context.capabilities);
  const source = context.source && typeof context.source === "object" ? context.source : {};
  const sourceChips = [
    source.dataset_id ? `dataset: ${source.dataset_id}` : "",
    source.uuid ? `uuid: ${String(source.uuid).slice(0, 12)}${String(source.uuid).length > 12 ? "…" : ""}` : "",
    source.name_parse_confidence != null ? `name parse: ${source.name_parse_confidence}` : "",
  ].filter(Boolean);

  return (
    <details className="pd-source" open>
      <summary>
        <span>시뮬레이션에 사용한 원본 페르소나</span>
        <small>모델 prompt에 들어간 정보만</small>
      </summary>

      {basics.length > 0 && (
        <div className="pd-source-basics">
          {basics.map(([label, value]) => (
            <div className="pd-source-basic" key={label}><small>{label}</small><strong>{value}</strong></div>
          ))}
        </div>
      )}

      {narratives.map(([label, value]) => (
        <div className="pd-source-row" key={label}>
          <div className="pd-source-label">{label}</div>
          <p>{value}</p>
        </div>
      ))}

      {(interests.length > 0 || capabilities.length > 0 || usedFields.length > 0 || sourceChips.length > 0) && (
        <div className="pd-source-chipsets">
          {interests.length > 0 && <div><span>관심사</span>{interests.map(v => <em key={v}>{v}</em>)}</div>}
          {capabilities.length > 0 && <div><span>역량</span>{capabilities.map(v => <em key={v}>{v}</em>)}</div>}
          {usedFields.length > 0 && <div><span>반응 근거 필드</span>{usedFields.map(v => <em key={v}>{v}</em>)}</div>}
          {sourceChips.length > 0 && <div><span>출처 메타</span>{sourceChips.map(v => <em key={v}>{v}</em>)}</div>}
        </div>
      )}
    </details>
  );
}

/* ====== Constellation viz ====== */

function Constellation({ personas, selectedId, onSelect }) {
  const ref = useRefP(null);
  const [size, setSize] = useStateP({ w: 1000, h: 540 });

  useEffectP(() => {
    function update() {
      if (!ref.current) return;
      const r = ref.current.getBoundingClientRect();
      setSize({ w: r.width, h: r.height });
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const [tick, setTick] = useStateP(0);
  useEffectP(() => {
    let raf;
    function loop() {
      setTick(t => t + 1);
      raf = requestAnimationFrame(loop);
    }
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const draftPositions = useMemoP(() => {
    return personas.map((p, i) => {
      const drift = tick / 80;
      const dx = Math.sin(drift + i * 1.3) * 6;
      const dy = Math.cos(drift * 0.8 + i * 0.7) * 4;
      return {
        ...p,
        cx: (p.x / 100) * size.w + dx,
        cy: (p.y / 100) * size.h + dy
      };
    });
    // eslint-disable-next-line
  }, [personas, tick, size.w, size.h]);

  const lines = useMemoP(() => {
    const out = [];
    const sel = draftPositions.find(p => p.id === selectedId);
    if (sel) {
      draftPositions.forEach(p => {
        if (p.id !== sel.id) {
          out.push({ x1: sel.cx, y1: sel.cy, x2: p.cx, y2: p.cy, type: "sel" });
        }
      });
    } else {
      for (let i = 0; i < draftPositions.length; i++) {
        for (let j = i + 1; j < draftPositions.length; j++) {
          const a = draftPositions[i], b = draftPositions[j];
          if (a.stance === b.stance) {
            out.push({ x1: a.cx, y1: a.cy, x2: b.cx, y2: b.cy, type: a.stance });
          }
        }
      }
    }
    return out;
    // eslint-disable-next-line
  }, [draftPositions, selectedId]);

  return (
    <div className="constellation" ref={ref}>
      <div className="constellation-grid"></div>

      <div className="const-meta">
        위치는 화면 배치용 · 크기는 합성 채택 의향 점수
      </div>

      <svg>
        {lines.map((l, i) => (
          <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2}
                stroke={l.type === "sel" ? "var(--accent-bright)" : `var(--${l.type === "pos" ? "pos" : l.type === "neg" ? "neg" : "neu"})`}
                strokeWidth={l.type === "sel" ? 1 : 0.5}
                strokeDasharray={l.type === "sel" ? "0" : "3 4"}
                opacity={l.type === "sel" ? 0.35 : 0.15} />
        ))}
      </svg>

      {draftPositions.map(p => {
        // Adoption arc — replaces stance pill, shows quantitative intent
        const arcPct = p.adoption / 100;
        const r = p.size / 2 + 3;
        const C = 2 * Math.PI * r;
        const dash = arcPct * C;
        const arcColor = p.stance === "pos" ? "var(--pos)" : p.stance === "neg" ? "var(--neg)" : "var(--neu)";

        return (
          <button key={p.id} type="button"
               aria-pressed={selectedId === p.id}
               aria-label={`${p.name} 응답자 선택`}
               className={"persona-node" + (selectedId === p.id ? " selected" : "")}
               style={{ left: p.cx + "px", top: p.cy + "px" }}
               onClick={() => onSelect(p.id)}>
            <span className="node-portrait" style={{ "--size": p.size + "px" }}>
              <PersonaPortrait id={p.id} size={p.size} />
              <svg className="adoption-arc" viewBox={`0 0 ${p.size + 6} ${p.size + 6}`}>
                <circle cx={(p.size + 6) / 2} cy={(p.size + 6) / 2} r={r}
                        fill="none" stroke={arcColor} strokeWidth="2"
                        strokeDasharray={`${dash} ${C}`}
                        strokeLinecap="round"
                        transform={`rotate(-90 ${(p.size + 6) / 2} ${(p.size + 6) / 2})`} />
              </svg>
            </span>
            <span className="node-label">
              <span className="node-name">{p.name}</span>
              <span className="node-meta">{p.age == null ? "나이 미제공" : `${p.age}세`} · 채택 {scoreLabel(p.adoption)}</span>
            </span>
          </button>
        );
      })}

      <div className="const-legend">
        <span><span className="dist-dot pos"></span>채택 의향 높음</span>
        <span><span className="dist-dot neu"></span>고민 중</span>
        <span><span className="dist-dot neg"></span>채택 의향 낮음</span>
      </div>
    </div>
  );
}

/* ====== Cards mode ====== */

function PersonaCards({ personas, selectedId, onSelect }) {
  return (
    <div className="persona-cards">
      {personas.map(p => (
        <button key={p.id} type="button"
             aria-pressed={selectedId === p.id}
             aria-label={`${p.name} 응답자 선택`}
             className={"pcard" + (selectedId === p.id ? " selected" : "")}
             onClick={() => onSelect(p.id)}>
          <span className="pcard-head">
            <span className="pcard-identity">
              <PersonaPortrait id={p.id} size={44} />
              <span>
                <span className="pcard-name">{p.name}</span>
                <span className="pcard-bio">{personaBio(p)}</span>
              </span>
            </span>
          </span>
          <span className="pcard-quote">"{p.stanceLabel}"</span>
          <span className="pcard-meter">
            <span className="pcard-meter-label">채택 의향</span>
            <span className="pcard-meter-bar">
              <span className="pcard-meter-bar-fill" style={{ width: p.adoption + "%" }}></span>
            </span>
            <span className="pcard-meter-val">{scoreLabel(p.adoption)}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

/* ====== Persona Detail ====== */

function PersonaDetail({ persona }) {
  if (!persona) return null;
  const p = persona;
  const priceCls = p.price === "높음" ? "high" : p.price === "보통" ? "med" : "low";

  return (
    <div className="persona-detail">
      <div className="pd-head">
        <PersonaPortrait id={p.id} size={64} />
        <div>
          <div className="pd-name">{p.name}</div>
          <div className="pd-bio">{personaBio(p)}</div>
        </div>
      </div>

      <div className="pd-summary">
        "{p.stanceLabel}" — {p.buyCondition}
      </div>

      <div className="pd-stats">
        <div className="pd-stat"><div className="lbl">제품 이해도</div><div className="val">{scoreLabel(p.understanding)}</div></div>
        <div className="pd-stat"><div className="lbl">문제 적합도</div><div className="val">{scoreLabel(p.need)}</div></div>
        <div className="pd-stat"><div className="lbl">채택 의향</div><div className="val">{scoreLabel(p.adoption)}</div></div>
        <div className="pd-stat"><div className="lbl">가격 부담</div><div className={"val " + priceCls}>{p.price}</div></div>
      </div>

      <div className="pd-section">
        <div className="pd-shead">이 분이 한 줄로 말하자면</div>
        <div style={{ fontSize: 14.5, color: "var(--text)", lineHeight: 1.6, letterSpacing: "-0.005em" }}>{p.core}</div>
      </div>

      <div className="pd-cols">
        <div className="pd-section">
          <div className="pd-shead">마음이 기우는 이유</div>
          <ul className="pd-list">
            {p.drivers.length > 0
              ? p.drivers.map((d, i) => <li key={i}>{d}</li>)
              : <li style={{ color: "var(--text-4)" }}>아직 없음</li>}
          </ul>
        </div>
        <div className="pd-section">
          <div className="pd-shead">망설이는 이유</div>
          <ul className="pd-list">
            {p.risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      </div>

      <div className="pd-section">
        <div className="pd-shead">이 분에게 다음에 물어볼 것</div>
        <div className="pd-nextq">{p.nextQ}</div>
      </div>

      <PersonaSourceContext context={p.sourceContext} usedFields={p.usedPersonaFields || []} />
    </div>
  );
}

/* ====== Chat ====== */

function PersonaChat({ persona, onPersonaChat = null }) {
  const [stream, setStream] = useStateP([]);
  const [input, setInput] = useStateP("");
  const streamRef = useRefP(null);
  const [thinking, setThinking] = useStateP(false);

  useEffectP(() => {
    setStream([]);
    setInput("");
    setThinking(false);
    const script = [persona.core].filter(Boolean);
    const out = [];
    let i = 0;
    let timer;
    function next() {
      if (i >= script.length) return;
      out.push({ role: "bot", text: script[i] });
      setStream([...out]);
      i++;
      timer = setTimeout(next, 800);
    }
    timer = setTimeout(next, 300);
    return () => clearTimeout(timer);
  }, [persona.id]);

  useEffectP(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [stream, thinking]);

  const send = async () => {
    if (!input.trim() || thinking) return;
    const q = input.trim();
    const nextStream = [...stream, { role: "user", text: q }];
    setStream(nextStream);
    setInput("");
    setThinking(true);
    try {
      if (!onPersonaChat) throw new Error("후속 질문 API가 연결되지 않았어요.");
      const reply = await onPersonaChat(persona, q, stream.map(m => ({ role: m.role === 'bot' ? 'persona' : 'user', content: m.text })));
      if (typeof reply !== "string" || !reply.trim()) throw new Error("빈 응답을 받았어요. 다시 시도해주세요.");
      setStream(s => [...s, { role: "bot", text: reply }]);
    } catch (err) {
      setStream(s => [...s, { role: "bot", text: `API 요청이 실패했습니다: ${err.message || err}` }]);
    } finally {
      setThinking(false);
    }
  };

  const suggestionFor = (p) => {
    if (p.stance === "neg") return ["왜 마음이 안 움직이세요?", "어떤 사례가 있으면 다시 보시겠어요?", "가족이 사주면 어떨까요?"];
    if (p.stance === "pos") return ["어떤 점이 가장 마음에 드세요?", "월 결제 vs 단발 결제, 어느 쪽이?", "친구분께도 추천하시겠어요?"];
    return ["어떻게 설명하면 마음이 갈 것 같으세요?", "하루에 몇 번 정도 쓸 것 같으세요?", "어떤 가격이면 망설임이 없으세요?"];
  };

  return (
    <div className="chat">
      <div className="chat-head">
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <PersonaPortrait id={persona.id} size={40} />
          <div>
            <div className="chat-name">{persona.name}님과 대화</div>
            <div className="chat-sub">{personaBio(persona)}</div>
          </div>
        </div>
      </div>

      <div className="chat-stream" ref={streamRef}>
        {stream.length === 0 && (
          <div style={{ color: "var(--text-4)", fontSize: 14, textAlign: "center", padding: "40px 0" }}>
            이 분이 잠시 후 말을 걸어올 거예요.
          </div>
        )}
        {stream.map((m, i) => (
          <div key={i} className={"bubble " + m.role}>
            {m.text}
          </div>
        ))}
        {thinking && (
          <div className="bubble thinking">
            <span className="dot"></span><span className="dot"></span><span className="dot"></span>
            <span style={{ marginLeft: 8 }}>{persona.name}님이 생각하는 중…</span>
          </div>
        )}
      </div>

      <div className="chat-prompts">
        {suggestionFor(persona).map((s, i) => (
          <button key={i} className="prompt-chip" onClick={() => setInput(s)}>{s}</button>
        ))}
      </div>

      <div className="chat-input-row">
        <textarea className="chat-input"
                  aria-label={`${persona.name}님에게 후속 질문`}
                  placeholder="이 분께 직접 물어보세요. 예: 이 가격이면 왜 망설이세요?"
                  value={input}
                  rows={2}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if (shouldSubmitChatOnEnter(e)) { e.preventDefault(); send(); } }} />
        <button className="send-btn" onClick={send}>
          물어보기
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{ marginLeft: 6, verticalAlign: -2 }}>
            <path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      </div>
    </div>
  );
}

/* ====== Screen wrapper ====== */

function PersonasScreen({ mode, setMode, goNext, goBack, personas: livePersonas = null, onPersonaChat = null }) {
  const personas = Array.isArray(livePersonas) ? livePersonas : [];
  const [selectedId, setSelectedId] = useStateP(personas[0]?.id || "p4");
  const viewMode = mode === "cards" ? "cards" : "constellation";

  useEffectP(() => {
    if (!personas.find(p => p.id === selectedId)) setSelectedId(personas[0]?.id || "p4");
  }, [personas.length]);

  const selected = personas.find(p => p.id === selectedId) || personas[0];
  if (!selected) return <div className="page"><h1 className="page-title">표시할 합성 응답이 없어요</h1><p className="page-sub">이번 실행에는 응답자가 없습니다. 예시 응답으로 대체하지 않습니다.</p><button className="btn" onClick={goBack}>결과 화면으로</button></div>;

  return (
    <div className="page" data-screen-label="04 Personas">
      <div className="page-head">
        <div className="page-eyebrow">4단계 · 응답자 한 명씩 들여다보기</div>
        <h1 className="page-title">응답자가<br /><em>당신의 제품 앞에 앉아있습니다</em></h1>
        <p className="page-sub">합성 응답자를 클릭하면 모델이 생성한 점수와 반응을 확인하고 후속 질문을 할 수 있어요. 초상화는 인물 특성을 반영하지 않는 장식입니다. 앞 단계의 평균값이 여기서 개별 목소리로 분해됩니다.</p>
      </div>

      <div className="persona-controls">
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <span className="card-tag">4단계</span>
          <span style={{ fontSize: 14, fontWeight: 500 }}>한 명씩 들여다보기</span>
          <span className="dim" style={{ fontSize: 12 }}>현재 실행 · 합성 응답 {personas.length}개</span>
        </div>
        <div className="const-overlay-controls">
          {[
            { id: "constellation", label: "별자리" },
            { id: "cards", label: "카드" }
          ].map(m => (
            <button key={m.id} type="button"
                    aria-pressed={viewMode === m.id}
                    className={"seg" + (viewMode === m.id ? " active" : "")}
                    onClick={() => setMode(m.id)}>
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginBottom: 22 }}>
        {viewMode === "constellation" && <Constellation personas={personas} selectedId={selectedId} onSelect={setSelectedId} />}
        {viewMode === "cards" && <PersonaCards personas={personas} selectedId={selectedId} onSelect={setSelectedId} />}
      </div>

      <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 14, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>선택한 응답자</span>
        <span className="dim" style={{ fontSize: 12 }}>왼쪽: 점수와 반응 요약 · 오른쪽: 직접 대화하기</span>
      </div>

      <div className="workspace">
        <PersonaDetail persona={selected} />
        <PersonaChat key={selected.id} persona={selected} onPersonaChat={onPersonaChat} />
      </div>

      <div className="row between" style={{ marginTop: 36 }}>
        <button className="btn" onClick={goBack}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M11 5l-7 7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
          결과 화면으로
        </button>
        <button className="btn btn-primary" onClick={goNext}>
          5단계: 분석가에게 질문하기
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
      </div>
    </div>
  );
}


export { PersonasScreen };
