/* Shared browser/Node helpers: no network requests or DOM work at import time. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.UpkinseyUI = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
"use strict";

const RESEARCH_TYPE_BY_MODE = {
  concept: "Concept test",
  pricing: "Pricing test",
  message: "Message test",
  objection: "Objection mining",
  segment: "Segment discovery"
};

function asList(value) { return Array.isArray(value) ? value : (value ? [value] : []); }
function clamp(n, lo = 0, hi = 100) { return Math.max(lo, Math.min(hi, Number(n) || 0)); }
function shortId(value) { return String(value || "live").replace(/[^a-zA-Z0-9]/g, "").slice(-6) || "live"; }
function apiPath(path) {
  return new URL(path, window.location.origin).toString();
}
function priceKo(value) {
  const v = String(value || "").toLowerCase();
  if (v.includes("high") || v.includes("높")) return "높음";
  if (v.includes("low") || v.includes("낮")) return "낮음";
  if (v.includes("medium") || v.includes("보통")) return "보통";
  return "미제공";
}
function parseMeta(meta = "") {
  const bits = String(meta).split(/[·,/]/).map(s => s.trim()).filter(Boolean);
  const age = parseAge(bits[0]);
  // Legacy metadata has no schema. Only interpret its complete canonical form;
  // a lone occupation or region must not be silently reclassified.
  return { age, region: age !== null && bits.length >= 3 ? bits[1] : "", role: age !== null && bits.length >= 3 ? bits.slice(2).join(" · ") : "" };
}
function parseAge(value) {
  const match = String(value ?? "").trim().match(/^(\d{1,3})\s*세?$/);
  return match ? Number(match[1]) : null;
}
function score(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  return Number.isFinite(Number(value)) ? clamp(value) : null;
}
function scoreLabel(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${value}점` : "미제공";
}
function personaBio(persona) {
  return [persona.age !== null && persona.age !== undefined ? `${persona.age}세` : "", persona.region, persona.role].filter(Boolean).join(" · ") || persona.__raw?.meta || "인구통계 정보 미제공";
}
function toBackendBrief(brief, config = {}) {
  return {
    product_name: brief.productName || "제품",
    description: brief.description || "",
    features: asList(brief.features),
    pricing: asList(brief.pricing),
    target_market: brief.target || "",
    current_alternatives: brief.alternatives || "",
    hypothesis: brief.hypothesis || "",
    research_type: RESEARCH_TYPE_BY_MODE[config.test] || config.research_type || "Concept test",
    sample_size: Number(config.sampleSize || 8),
    seed: Number(config.seed ?? 42)
  };
}
function fromBackendBrief(brief) {
  return {
    productName: brief.product_name || brief.productName || "제품",
    description: brief.description || "",
    features: asList(brief.features),
    pricing: asList(brief.pricing),
    target: brief.target_market || brief.target || "",
    alternatives: brief.current_alternatives || brief.alternatives || "",
    hypothesis: brief.hypothesis || ""
  };
}
function canonicalBriefForHash(brief) {
  return {
    productName: String(brief?.productName || ""),
    description: String(brief?.description || ""),
    features: asList(brief?.features).map(String),
    pricing: asList(brief?.pricing).map(String),
    target: String(brief?.target || ""),
    alternatives: String(brief?.alternatives || ""),
    hypothesis: String(brief?.hypothesis || ""),
  };
}
function briefFingerprint(brief) {
  const text = JSON.stringify(canonicalBriefForHash(brief));
  let hash = 5381;
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash) ^ text.charCodeAt(i);
  return (hash >>> 0).toString(36);
}
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException("aborted", "AbortError")); return; }
    const onAbort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(new DOMException("aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
async function readApiResponse(response) {
  const data = await response.json().catch(() => null);
  if (!response.ok || data?.error) {
    const messages = {
      authentication_required: "로그인이 필요해요. 페이지를 새로고침하고 인증해주세요.",
      auth_not_configured: "서버 인증 설정이 완료되지 않았어요. 운영자에게 문의해주세요.",
      rate_limited: "요청이 너무 많아요. 잠시 후 다시 시도해주세요.",
      too_many_active_jobs: "다른 시뮬레이션이 실행 중이에요. 완료 후 다시 시도해주세요.",
      job_not_found: "실행 정보를 찾을 수 없어요. 서버가 재시작되었거나 기록이 만료되었을 수 있어요.",
    };
    const detail = typeof data?.message === "string" ? data.message : typeof data?.error === "string" ? (messages[data.error] || data.error) : "";
    const defaults = { 401: "인증이 필요해요.", 413: "업로드한 파일이나 입력이 너무 커요.", 429: messages.rate_limited, 502: "모델 서비스에 연결하지 못했어요. 잠시 후 다시 시도해주세요.", 503: "서버를 사용할 준비가 되지 않았어요. 잠시 후 다시 시도해주세요." };
    const retry = Number(data?.retry_after_seconds);
    throw new Error(`${detail || defaults[response.status] || "요청을 처리하지 못했어요."} (HTTP ${response.status})${retry > 0 ? ` · ${retry}초 후 재시도` : ""}`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("서버 응답 형식이 올바르지 않아요. 페이지를 새로고침한 뒤 다시 시도해주세요.");
  return data;
}
function mapPersona(raw = {}, idx = 0) {
  const meta = parseMeta(raw.meta, idx);
  const context = raw.persona_context || raw.source_context || {};
  const adoption = score(raw.adoption_likelihood ?? raw.adoption);
  const need = score(raw.need_fit_score ?? raw.need);
  const understanding = score(raw.understanding_score ?? raw.understanding);
  // Match aggregate_market_research: positive >=65, negative <40.
  const stance = adoption === null ? "unknown" : adoption >= 65 ? "pos" : adoption >= 40 ? "neu" : "neg";
  return {
    id: `p${idx + 1}`,
    name: raw.name || `Persona ${idx + 1}`,
    age: parseAge(context.age ?? raw.age) ?? meta.age,
    region: context.province || raw.region || raw.province || meta.region,
    role: context.occupation || raw.role || raw.occupation || meta.role,
    stance,
    stanceLabel: raw.stance || (stance === "unknown" ? "반응 점수 미제공" : stance === "pos" ? "긍정적으로 검토" : stance === "neu" ? "정보가 더 필요" : "회의적"),
    buyCondition: raw.buy_condition || raw.buyCondition || raw.next_validation_question || "추가 근거 확인 후 판단",
    adoption, need, understanding,
    price: priceKo(raw.price_resistance || raw.price),
    core: raw.concern || raw.core || raw.reply || "아직 핵심 반응이 없습니다.",
    drivers: asList(raw.positive_drivers || raw.drivers),
    risks: asList(raw.top_risks || raw.risks),
    nextQ: raw.next_validation_question || raw.nextQ || "어떤 근거가 있으면 다음 행동으로 넘어갈 수 있나요?",
    sourceContext: Object.keys(context).length ? context : null,
    usedPersonaFields: asList(raw.used_persona_fields),
    x: 12 + ((idx * 37) % 76),
    y: 18 + ((idx * 29) % 64),
    size: Math.max(42, Math.min(72, 42 + adoption * 0.42)),
    __raw: raw
  };
}
function mapResultToResonance(result, brief, versions = []) {
  const dist = result?.reaction_distribution || {};
  const personas = asList(result?.persona_reactions ?? result?.personas).filter(p => p && typeof p === "object" && !Array.isArray(p)).map(mapPersona);
  const adoption = score(result?.adoption_score);
  const need = score(result?.need_fit_score);
  const evidence = result?.evidence_quality || result?.report?.evidence_quality || {};
  const requestBudget = result?.request_budget || result?.report?.request_budget || {};
  const version = result?.version || {};
  const currentVersion = {
    id: version.version_id || "live",
    shortId: shortId(version.version_id),
    name: brief.productName || version.product_name || "제품",
    type: version.research_type || result?.research_type || "Concept test",
    time: version.created_at ? new Date(version.created_at).toLocaleString("ko-KR") : "저장 시각 미제공",
    adoption,
    need,
    price: priceKo(result?.price_risk),
    decision: result?.report?.decision_board?.recommendation || version.decision || "다음 검증 필요",
    evidence: `${evidence.score ?? version.evidence_quality_score ?? "-"}점`,
    calls: `${requestBudget.actual_persona_count ?? personas.length}명 / ${requestBudget.requested_sample_size ?? personas.length}명`,
    panel: result?.panel_profile?.selection_mode || "필터 없음",
    warnings: (evidence.warnings || []).length || version.evidence_warning_count || 0,
    current: true
  };
  const mappedVersions = versions.length ? versions : [currentVersion];
  return {
    brief,
    signals: {
      adoption: { value: adoption, delta: null, prev: null },
      needFit: { value: need, delta: null, prev: null },
      priceRisk: { value: priceKo(result?.price_risk), changed: false, prev: null },
      evidenceQuality: { value: evidence.score ?? "미제공", delta: null },
      distribution: { positive: Number(dist.positive ?? 0), neutral: Number(dist.neutral ?? 0), negative: Number(dist.negative ?? 0) },
      distributionCounts: { positive: personas.filter(p => p.stance === "pos").length, neutral: personas.filter(p => p.stance === "neu").length, negative: personas.filter(p => p.stance === "neg").length },
      calls: { done: personas.length, total: requestBudget.requested_sample_size ?? personas.length, batches: requestBudget.planned_batches ?? "-" },
      warnings: (evidence.warnings || []).length,
      decision: { current: currentVersion.decision, prev: currentVersion.decision }
    },
    versions: mappedVersions,
    personas,
    result
  };
}


return { asList, clamp, shortId, apiPath, priceKo, parseMeta, toBackendBrief, fromBackendBrief, canonicalBriefForHash, briefFingerprint, sleep, mapPersona, mapResultToResonance, readApiResponse, scoreLabel, personaBio };
});
