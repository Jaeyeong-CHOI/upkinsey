"""Research artifact aggregation and model-call orchestration.

The simulation sends persona-level Upstage requests in parallel whenever possible
and only aggregates after all independent persona calls return.

Pure input normalization, Markdown export, and interview planning live in their
own modules. Their established names are reexported here so existing callers do
not need to change imports; model-call entry points remain patchable here.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

# Compatibility facade: keep established imports while pure modules stay acyclic.
from .research_inputs import (
    KOREA_PROVINCES as KOREA_PROVINCES,
    PRICE_RISK_SCORE as PRICE_RISK_SCORE,
    TARGET_SEGMENT_KEYWORDS as TARGET_SEGMENT_KEYWORDS,
    _as_int as _as_int,
    _bounded_list as _bounded_list,
    _contains_any as _contains_any,
    _dedupe_key as _dedupe_key,
    _dedupe_preserve_order as _dedupe_preserve_order,
    _extract_json as _extract_json,
    _first_text as _first_text,
    _infer_age_bounds_from_text as _infer_age_bounds_from_text,
    _listify as _listify,
    _normalize_persona_filters as _normalize_persona_filters,
    _persona_age as _persona_age,
    _persona_search_text as _persona_search_text,
    _price_risk_score as _price_risk_score,
    _segment_persona_label as _segment_persona_label,
    _shorten as _shorten,
    infer_persona_filters_from_brief as infer_persona_filters_from_brief,
    normalize_persona_for_prompt as normalize_persona_for_prompt,
    persona_context_for_result as persona_context_for_result,
    persona_filter_score as persona_filter_score,
    persona_meta as persona_meta,
    select_personas_for_brief as select_personas_for_brief,
    unique_top as unique_top,
    validate_brief as validate_brief,
)
from .report_markdown import (
    _markdown_bullets as _markdown_bullets,
    _markdown_escape as _markdown_escape,
    format_report_markdown as format_report_markdown,
)
from .interview_planning import (
    _analyst_information_coverage as _analyst_information_coverage,
    _asked_phases as _asked_phases,
    _clean_interview_followup as _clean_interview_followup,
    _normalize_chat_history as _normalize_chat_history,
    _normalized_contains as _normalized_contains,
    _normalized_similarity as _normalized_similarity,
    _persona_question_match_score as _persona_question_match_score,
    _probe as _probe,
    _question_signal_keywords as _question_signal_keywords,
    _select_probe_question as _select_probe_question,
    _short_quote as _short_quote,
    _synthesize_analyst_interviews as _synthesize_analyst_interviews,
    build_analyst_question_plan as build_analyst_question_plan,
    build_custom_analyst_followup as build_custom_analyst_followup,
    build_persona_chat_prompt as build_persona_chat_prompt,
    select_analyst_target_personas as select_analyst_target_personas,
)

from .upstage_client import UpstageClient
from .provenance import build_provenance

SAMPLE_PERSONAS = [
    {
        "name": "전기태",
        "age": 74,
        "province": "광주",
        "occupation": "하역 및 적재 관련 단순 종사원",
        "persona": "성실하고 사교적이지만 유료 구독과 스마트폰 사용에는 신중한 70대 가장",
    },
    {
        "name": "김다희",
        "age": 46,
        "province": "경기",
        "occupation": "무직",
        "persona": "가족의 평온함과 실용적인 정보 탐색을 중시하는 40대 가정 내 의사결정자",
    },
    {
        "name": "최은지",
        "age": 71,
        "province": "서울",
        "occupation": "회계 사무원",
        "persona": "수치와 실용성을 빠르게 따지는 외향적인 70대 사무직",
    },
    {
        "name": "설숙자",
        "age": 50,
        "province": "부산",
        "occupation": "서비스직",
        "persona": "가족 안정과 이웃 간의 정을 중시하며 앱 피로도를 경계하는 50대 여성",
    },
]

SYSTEM_PROMPT = """You are Upkinsey & Company, an AI market research simulator.
Generate concise Korean market research insights from synthetic persona reactions.
This is pre-research, not a statistically representative consumer survey.
Return valid JSON only. Do not include markdown fences."""

PERSONA_RESPONSE_SCHEMA = """
{
  "name": "...",
  "meta": "나이 · 지역 · 직업",
  "stance": "긍정형 | 조건부 긍정 | 관망형 | 회의형",
  "understanding_score": 0,
  "need_fit_score": 0,
  "adoption_likelihood": 0,
  "price_resistance": "Low | Low-Medium | Medium | High",
  "concern": "...",
  "positive_drivers": ["..."],
  "top_risks": ["..."],
  "next_validation_question": "...",
  "used_persona_fields": ["..."]
}
""".strip()


OBJECTION_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("가격 부담", ("가격", "비용", "결제", "구독", "유료", "월", "수수료", "부담")),
    ("신뢰 부족", ("신뢰", "믿", "정확", "검증", "근거", "품질", "보상 기준", "투명")),
    ("개인정보/데이터 우려", ("개인정보", "데이터", "정보 입력", "서류", "사진", "건강정보", "보안")),
    ("사용법 복잡성", ("복잡", "어렵", "앱", "설치", "사용법", "디지털", "알림", "피로", "번거")),
    ("기존 습관과 충돌", ("습관", "현재", "기존", "하던", "직접", "센터", "문의")),
    ("필요성 낮음", ("필요", "효용", "쓸 이유", "관심", "체감", "긴급")),
    ("가족/조직 내 의사결정 문제", ("가족", "보호자", "사장", "직원", "조직", "의사결정")),
    ("대체재가 이미 충분함", ("대체", "네이버", "카카오", "검색", "지인", "주민센터", "기존 서비스")),
]

OBJECTION_FIXES = {
    "가격 부담": "무료 체험, 저가 기본 플랜, 결제 전 가치 확인 구간을 명확히 제시",
    "신뢰 부족": "추천/판단 근거, 검수 방식, 실패 시 보상·책임 범위를 화면에 노출",
    "개인정보/데이터 우려": "수집 데이터 최소화, 보관 기간, 삭제 방법, 민감정보 비저장 옵션을 설명",
    "사용법 복잡성": "첫 사용 단계를 1-2개로 줄이고 카카오톡/문자 같은 익숙한 채널을 우선 제공",
    "기존 습관과 충돌": "현재 행동을 대체하기보다 체크리스트·알림처럼 보완하는 진입점을 설계",
    "필요성 낮음": "사용 전후 차이를 보여주는 구체적 상황·성과 지표로 메시지를 좁힘",
    "가족/조직 내 의사결정 문제": "공유 링크, 승인자용 요약, 가족/조직 초대 흐름을 제공",
    "대체재가 이미 충분함": "기존 대안 대비 더 빠르거나 안전한 전환 계기를 한 가지로 집중",
}


def classify_objection(text: str) -> str:
    """Classify a free-text risk into a stable objection category."""

    lowered = text.lower()
    best_category = "기타"
    best_score = 0
    for category, keywords in OBJECTION_KEYWORDS:
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score > best_score:
            best_category = category
            best_score = score
    return best_category


def unique_top_risks(items: list[str], limit: int = 4) -> list[str]:
    """Return top risks while avoiding multiple entries for the same objection category."""

    seen: set[str] = set()
    out: list[str] = []
    for raw_item in items:
        item = str(raw_item).strip()
        category = classify_objection(item)
        key = category if category != "기타" else _dedupe_key(item)
        if item and key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def mine_objections(reactions: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Aggregate persona-level risks into actionable objection cards."""

    grouped: dict[str, dict[str, Any]] = {}
    for reaction in reactions:
        persona_name = str(reaction.get("name") or "Persona")
        meta = str(reaction.get("meta") or "")
        affected_label = f"{persona_name} ({meta})" if meta else persona_name
        for risk in reaction.get("top_risks", []):
            objection = str(risk).strip()
            if not objection:
                continue
            category = classify_objection(objection)
            entry = grouped.setdefault(
                category,
                {
                    "category": category,
                    "examples": [],
                    "affected_personas": [],
                    "count": 0,
                    "suggested_fix": OBJECTION_FIXES.get(category, "해당 우려가 실제 구매/사용을 막는지 인터뷰에서 확인"),
                },
            )
            entry["count"] += 1
            if objection not in entry["examples"]:
                entry["examples"].append(objection)
            if affected_label not in entry["affected_personas"]:
                entry["affected_personas"].append(affected_label)

    ranked = sorted(grouped.values(), key=lambda item: (-item["count"], item["category"]))[:limit]
    return [
        {
            "category": item["category"],
            "objection": item["examples"][0],
            "examples": item["examples"][:3],
            "affected_personas": item["affected_personas"][:5],
            "count": item["count"],
            "suggested_fix": item["suggested_fix"],
        }
        for item in ranked
    ]


def _common_price_risk(reactions: list[dict[str, Any]]) -> str:
    if not reactions:
        return "Medium"
    ranked = sorted(
        PRICE_RISK_SCORE,
        key=lambda label: (
            -sum(1 for reaction in reactions if reaction.get("price_resistance") == label),
            PRICE_RISK_SCORE[label],
        ),
    )
    return ranked[0]


def _extract_price_amount_krw(value: Any) -> int | None:
    """Best-effort parser for Korean price labels used in founder briefs."""

    text = str(value or "").replace(",", "").strip().lower()
    if not text:
        return None
    if "무료" in text or "free" in text or re.search(r"(^|\D)0\s*원", text):
        return 0

    match = re.search(r"(\d+(?:\.\d+)?)\s*(만원|천원|원|만|천|k)?", text)
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2) or "원"
    if unit in {"만원", "만"}:
        amount *= 10_000
    elif unit in {"천원", "천", "k"}:
        amount *= 1_000
    return int(round(amount))


def _first_counter_item(items: list[str], fallback: str) -> str:
    counts: dict[str, int] = {}
    for item in items:
        text = str(item).strip()
        if text:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return fallback
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def assess_brief_quality(brief: dict[str, Any]) -> dict[str, Any]:
    """Score whether a product brief is specific enough for simulation.

    This deterministic preflight keeps Upkinsey from treating vague inputs as
    equally research-ready. It does not block simulation; it tells the founder
    which missing assumptions should be tightened before spending more API runs
    or recruiting real interviewees.
    """

    normalized = validate_brief(brief)
    score = 100
    missing_fields: list[str] = []
    issues: list[str] = []
    strengths: list[str] = []
    recommended_questions: list[str] = []

    product_text = " ".join(
        [
            normalized["product_name"],
            normalized["description"],
            " ".join(normalized["features"]),
            " ".join(normalized["pricing"]),
            normalized["target_market"],
            normalized["hypothesis"],
            normalized["current_alternatives"],
        ]
    )

    if len(normalized["description"]) < 30:
        score -= 18
        missing_fields.append("구체적인 제품 설명")
        issues.append("제품 설명이 짧아 persona가 실제 사용 상황을 떠올리기 어렵습니다.")
        recommended_questions.append("이 제품은 어떤 상황에서, 어떤 불편을, 어떤 방식으로 줄이나요?")
    else:
        strengths.append("제품 설명이 persona 판단에 필요한 기본 맥락을 제공합니다.")

    if len(normalized["features"]) < 2:
        score -= 12
        missing_fields.append("핵심 기능 2개 이상")
        issues.append("기능이 부족하면 반응이 브랜드 인상보다 일반 호감도에 치우칠 수 있습니다.")
        recommended_questions.append("사용자가 처음 1분 안에 경험할 핵심 기능은 무엇인가요?")
    else:
        strengths.append("핵심 기능이 분리되어 있어 driver/risk를 기능별로 해석할 수 있습니다.")

    if not normalized["pricing"]:
        score -= 15
        missing_fields.append("가격/과금 옵션")
        issues.append("가격 정보가 없어 adoption과 price resistance가 과도하게 낙관적일 수 있습니다.")
        recommended_questions.append("무료, 구독, 건별 결제 중 어떤 가격 조건을 먼저 검증해야 하나요?")
    else:
        strengths.append("가격 옵션이 있어 지불 저항을 함께 평가할 수 있습니다.")

    target_market = normalized["target_market"]
    if len(target_market) < 8:
        score -= 15
        missing_fields.append("타깃 고객")
        issues.append("타깃 고객이 불명확해 panel selection과 segment recommendation이 흐려집니다.")
        recommended_questions.append("가장 먼저 검증할 beachhead 고객은 누구인가요?")
    elif _contains_any(target_market, ("전국민", "모든 사람", "일반 사용자", "고객 전체")):
        score -= 8
        issues.append("타깃이 너무 넓어 초기 검증 세그먼트가 약해질 수 있습니다.")
        recommended_questions.append("초기에는 어떤 직업/가족상황/생활문제를 가진 집단부터 볼까요?")
    else:
        strengths.append("타깃 고객이 명시되어 persona panel 해석이 쉬워집니다.")

    hypothesis = normalized["hypothesis"]
    if len(hypothesis) < 15:
        score -= 12
        missing_fields.append("검증 가능한 가설")
        issues.append("가설이 짧거나 빠져 있어 결과를 다음 실험으로 연결하기 어렵습니다.")
        recommended_questions.append("어떤 조건이면 관심, 가입, 결제, 추천 의향이 높아진다고 보나요?")
    elif not _contains_any(hypothesis, ("관심", "의향", "결제", "전환", "신뢰", "수용", "높", "낮", "검증", "사용")):
        score -= 6
        issues.append("가설이 측정 가능한 행동/태도 변화로 표현되지 않았습니다.")
        recommended_questions.append("가설을 adoption, trust, price, usability 중 어떤 신호로 판정할까요?")
    else:
        strengths.append("검증 가설이 있어 결과를 후속 질문으로 바꾸기 좋습니다.")

    if not _contains_any(
        product_text,
        ("대체", "현재", "기존", "네이버", "카카오", "검색", "전화", "지인", "직접", "엑셀", "수기", "주민센터"),
    ):
        score -= 7
        missing_fields.append("현재 대체 행동/경쟁 대안")
        issues.append("현재 사용자가 어떻게 문제를 해결하는지 빠져 switching trigger를 해석하기 어렵습니다.")
        recommended_questions.append("사용자는 지금 이 문제를 어떤 도구나 습관으로 해결하고 있나요?")

    if not _contains_any(product_text, ("때", "상황", "순간", "매일", "반복", "업무", "가족", "출퇴근", "신청", "복약", "리뷰")):
        score -= 5
        issues.append("사용 맥락이 약해 persona 반응이 추상적일 수 있습니다.")
        recommended_questions.append("가장 자주 발생하는 사용 장면을 한 문장으로 쓰면 무엇인가요?")

    score = max(0, min(100, score))
    if score >= 80:
        verdict = "ready"
    elif score >= 60:
        verdict = "needs_tightening"
    else:
        verdict = "incomplete"

    return {
        "score": score,
        "verdict": verdict,
        "missing_fields": unique_top(missing_fields, limit=6),
        "issues": unique_top(issues, limit=6),
        "strengths": unique_top(strengths, limit=5),
        "recommended_questions": unique_top(recommended_questions, limit=5),
    }


def build_segment_recommendations(
    reactions: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    """Turn persona reactions into launch/validation segment recommendations.

    Upkinsey is most useful when it points founders to the next human learning
    loop. These cards keep aggregation deterministic while translating scores,
    stance, drivers, and objections into concrete segment-level actions.
    """

    groups = [
        {
            "segment": "우선 검증 타깃",
            "role": "beachhead",
            "reactions": [
                reaction
                for reaction in reactions
                if reaction.get("adoption_likelihood", 0) >= 70 and _price_risk_score(reaction.get("price_resistance")) <= 1
            ],
            "validation_action": "랜딩 페이지/인터뷰에서 핵심 가치 메시지와 결제 전환 조건을 바로 검증",
        },
        {
            "segment": "조건부 전환 타깃",
            "role": "conditional",
            "reactions": [
                reaction
                for reaction in reactions
                if reaction.get("adoption_likelihood", 0) >= 55
                and (
                    reaction.get("adoption_likelihood", 0) < 70
                    or _price_risk_score(reaction.get("price_resistance")) >= 2
                )
            ],
            "validation_action": "가격·신뢰·사용 난이도 중 어떤 조건을 해소하면 전환되는지 A/B 메시지로 확인",
        },
        {
            "segment": "거부 이유 학습 타깃",
            "role": "risk-learning",
            "reactions": [reaction for reaction in reactions if reaction.get("adoption_likelihood", 0) < 55],
            "validation_action": "구매 의향보다 비사용 이유와 대체재를 인터뷰해 포지셔닝 리스크를 수집",
        },
    ]

    recommendations: list[dict[str, Any]] = []
    for group in groups:
        group_reactions = group["reactions"]
        if not group_reactions:
            continue
        adoption = round(sum(r["adoption_likelihood"] for r in group_reactions) / len(group_reactions))
        need_fit = round(sum(r["need_fit_score"] for r in group_reactions) / len(group_reactions))
        drivers = [driver for reaction in group_reactions for driver in reaction.get("positive_drivers", [])]
        risks = [risk for reaction in group_reactions for risk in reaction.get("top_risks", [])]
        questions = [
            reaction["next_validation_question"]
            for reaction in group_reactions
            if reaction.get("next_validation_question")
        ]
        recommendations.append(
            {
                "segment": group["segment"],
                "role": group["role"],
                "persona_examples": [_segment_persona_label(reaction) for reaction in group_reactions[:5]],
                "persona_count": len(group_reactions),
                "avg_adoption": adoption,
                "avg_need_fit": need_fit,
                "price_risk_mode": _common_price_risk(group_reactions),
                "primary_driver": _first_counter_item(drivers, "실제 문제 해결 가능성"),
                "primary_objection": _first_counter_item(risks, "추가 리스크 확인 필요"),
                "validation_question": _first_counter_item(questions, "어떤 조건에서 실제 사용/결제로 이어지는가?"),
                "validation_action": group["validation_action"],
            }
        )

    return sorted(
        recommendations,
        key=lambda item: (item["role"] != "beachhead", -item["avg_adoption"], -item["persona_count"]),
    )[:limit]


def _persona_evidence_card(reaction: dict[str, Any], role: str) -> dict[str, Any]:
    persona = _segment_persona_label(reaction)
    adoption = _as_int(reaction.get("adoption_likelihood"), default=0)
    need_fit = _as_int(reaction.get("need_fit_score"), default=0)
    driver = _first_text(*_listify(reaction.get("positive_drivers")), "반응한 가치가 아직 불명확함")
    objection = _first_text(*_listify(reaction.get("top_risks")), reaction.get("concern"), "추가 우려 확인 필요")
    question = _first_text(reaction.get("next_validation_question"), "이 반응이 실제 다음 행동으로 이어지는 조건은 무엇인가?")

    if role == "supporter":
        signal = f"{persona}는 {driver} 때문에 {adoption}/100 채택 의향을 보였고, {objection}은 확인해야 합니다."
        probe = f"{driver}가 실제 가입/결제 행동까지 이어지는지 확인"
    elif role == "barrier":
        signal = f"{persona}는 {objection} 때문에 {adoption}/100 수준에 머물렀습니다."
        probe = f"{objection}이 비사용 이유인지, 어떤 증거가 있으면 완화되는지 확인"
    else:
        signal = f"{persona}의 다음 검증 질문: {question}"
        probe = question

    return {
        "persona": persona,
        "role": role,
        "stance": _first_text(reaction.get("stance"), "관망형"),
        "adoption_likelihood": adoption,
        "need_fit_score": need_fit,
        "price_resistance": _first_text(reaction.get("price_resistance"), "Medium"),
        "primary_driver": driver,
        "primary_objection": objection,
        "validation_question": question,
        "signal": signal,
        "interview_probe": probe,
    }


def build_persona_evidence_pack(reactions: list[dict[str, Any]], limit: int = 3) -> dict[str, Any]:
    """Select representative persona-level evidence behind the aggregate score.

    Aggregate adoption and risk scores are useful, but founders need to know
    which synthetic reactions they should turn into interview probes. This pack
    keeps that bridge deterministic and explicitly labels the cards as synthetic
    signals, not real customer quotes.
    """

    if not reactions:
        return {
            "summary": "No persona reactions available for evidence selection.",
            "supporter_cards": [],
            "barrier_cards": [],
            "validation_followups": [],
            "disclaimer": "Synthetic persona signal cards only; do not present them as real customer quotes.",
        }

    supporters = sorted(
        [reaction for reaction in reactions if _as_int(reaction.get("adoption_likelihood"), default=0) >= 65],
        key=lambda reaction: (
            -_as_int(reaction.get("adoption_likelihood"), default=0),
            _price_risk_score(reaction.get("price_resistance")),
            _segment_persona_label(reaction),
        ),
    )[:limit]
    barriers = sorted(
        [
            reaction
            for reaction in reactions
            if _as_int(reaction.get("adoption_likelihood"), default=0) < 55
            or _price_risk_score(reaction.get("price_resistance")) >= 2
        ],
        key=lambda reaction: (
            _as_int(reaction.get("adoption_likelihood"), default=0),
            -_price_risk_score(reaction.get("price_resistance")),
            _segment_persona_label(reaction),
        ),
    )[:limit]

    followups: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    ranked_for_questions = sorted(
        reactions,
        key=lambda reaction: (
            _as_int(reaction.get("adoption_likelihood"), default=0) >= 55,
            -_price_risk_score(reaction.get("price_resistance")),
            _segment_persona_label(reaction),
        ),
    )
    for reaction in ranked_for_questions:
        question = _first_text(reaction.get("next_validation_question"))
        key = _dedupe_key(question)
        if not key or key in seen_questions:
            continue
        seen_questions.add(key)
        followups.append(_persona_evidence_card(reaction, "followup"))
        if len(followups) >= limit:
            break

    avg_adoption = round(sum(_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in reactions) / len(reactions))
    return {
        "summary": (
            f"평균 채택 의향 {avg_adoption}/100 뒤의 대표 반응: "
            f"supporter {len(supporters)}명, barrier {len(barriers)}명, follow-up {len(followups)}개."
        ),
        "supporter_cards": [_persona_evidence_card(reaction, "supporter") for reaction in supporters],
        "barrier_cards": [_persona_evidence_card(reaction, "barrier") for reaction in barriers],
        "validation_followups": followups,
        "disclaimer": "Synthetic persona signal cards only; do not present them as real customer quotes.",
    }


def build_intent_cohort_contrast(reactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare high-, conditional-, and low-intent persona cohorts.

    Segment recommendations point to the next validation target. This contrast
    view explains *why* intent differs across cohorts, so founders can avoid
    averaging away the strongest and weakest market signals.
    """

    cohort_specs = [
        (
            "high_intent",
            "High-intent cohort",
            lambda reaction: _as_int(reaction.get("adoption_likelihood"), default=0) >= 70,
            "우선 고객 후보에게 핵심 가치 메시지와 실제 다음 행동을 바로 검증",
        ),
        (
            "conditional_intent",
            "Conditional-intent cohort",
            lambda reaction: 55 <= _as_int(reaction.get("adoption_likelihood"), default=0) < 70,
            "가격·신뢰·사용성 중 어떤 조건을 낮추면 전환되는지 비교",
        ),
        (
            "low_intent",
            "Low-intent cohort",
            lambda reaction: _as_int(reaction.get("adoption_likelihood"), default=0) < 55,
            "비사용 이유와 현재 대체 행동이 충분한 이유를 먼저 학습",
        ),
    ]

    cohorts: list[dict[str, Any]] = []
    for cohort_key, label, predicate, validation_focus in cohort_specs:
        cohort_reactions = [reaction for reaction in reactions if predicate(reaction)]
        if not cohort_reactions:
            continue
        count = len(cohort_reactions)
        drivers = [driver for reaction in cohort_reactions for driver in reaction.get("positive_drivers", [])]
        risks = [risk for reaction in cohort_reactions for risk in reaction.get("top_risks", [])]
        questions = [
            str(reaction.get("next_validation_question"))
            for reaction in cohort_reactions
            if str(reaction.get("next_validation_question") or "").strip()
        ]
        cohorts.append(
            {
                "cohort": cohort_key,
                "label": label,
                "persona_count": count,
                "avg_adoption": round(
                    sum(_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in cohort_reactions) / count
                ),
                "avg_need_fit": round(
                    sum(_as_int(reaction.get("need_fit_score"), default=0) for reaction in cohort_reactions) / count
                ),
                "price_risk_mode": _common_price_risk(cohort_reactions),
                "persona_examples": [_segment_persona_label(reaction) for reaction in cohort_reactions[:5]],
                "shared_drivers": unique_top(drivers, limit=4),
                "shared_objections": unique_top_risks(risks, limit=4),
                "validation_focus": validation_focus,
                "next_question": _first_counter_item(questions, "이 cohort가 다음 행동을 하거나 거부하는 결정 조건은 무엇인가?"),
            }
        )

    ranked = sorted(cohorts, key=lambda item: -item["avg_adoption"])
    strongest = ranked[0] if ranked else {}
    weakest = ranked[-1] if len(ranked) > 1 else {}
    adoption_gap = int(strongest.get("avg_adoption", 0)) - int(weakest.get("avg_adoption", strongest.get("avg_adoption", 0)))
    summary_parts: list[str] = []
    if strongest:
        summary_parts.append(
            f"Strongest signal: {strongest['label']} ({strongest['avg_adoption']}/100 avg adoption)"
        )
    if weakest:
        summary_parts.append(
            f"Weakest signal: {weakest['label']} ({weakest['avg_adoption']}/100 avg adoption)"
        )
    if adoption_gap >= 20:
        summary_parts.append("Average score hides a large intent gap; compare cohort drivers before changing the product.")
    elif strongest:
        summary_parts.append("Intent gap is modest; validate whether the same objection repeats across cohorts.")

    return {
        "cohorts": cohorts,
        "strongest_cohort": strongest.get("cohort"),
        "weakest_cohort": weakest.get("cohort") if weakest else None,
        "adoption_gap": adoption_gap,
        "summary": " ".join(summary_parts) if summary_parts else "No persona reactions available for cohort contrast.",
        "recommended_comparison": "고의향 cohort의 driver와 저의향 cohort의 objection을 같은 랜딩/인터뷰 stimulus에서 나란히 검증하세요.",
    }


def build_focus_group_simulation_plan(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    intent_cohort_contrast: dict[str, Any] | None = None,
    objections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a bounded synthetic focus-group plan from persona-level signals.

    Focus groups are useful when a product is adopted through family, team, or
    community discussion, but synthetic roleplay can easily become theater. This
    artifact keeps the group exercise small, cohort-balanced, and validation-led:
    it names who to include, what each persona should react to, and what the
    founder should record before deciding whether a real focus group is worth it.
    """

    normalized = validate_brief(brief)
    contrast = intent_cohort_contrast or build_intent_cohort_contrast(reactions)
    objection_cards = objections or mine_objections(reactions)
    cohorts = contrast.get("cohorts") if isinstance(contrast.get("cohorts"), list) else []

    def cohort_sort_key(reaction: dict[str, Any]) -> tuple[int, int]:
        adoption = _as_int(reaction.get("adoption_likelihood"), default=0)
        need_fit = _as_int(reaction.get("need_fit_score"), default=0)
        return (-adoption, -need_fit)

    high = sorted(
        [reaction for reaction in reactions if _as_int(reaction.get("adoption_likelihood"), default=0) >= 70],
        key=cohort_sort_key,
    )
    conditional = sorted(
        [
            reaction
            for reaction in reactions
            if 55 <= _as_int(reaction.get("adoption_likelihood"), default=0) < 70
        ],
        key=cohort_sort_key,
    )
    low = sorted(
        [reaction for reaction in reactions if _as_int(reaction.get("adoption_likelihood"), default=0) < 55],
        key=lambda reaction: (
            _as_int(reaction.get("adoption_likelihood"), default=0),
            -_price_risk_score(reaction.get("price_resistance")),
        ),
    )

    participant_mix: list[dict[str, Any]] = []
    mix_specs = [
        (
            "supporter",
            "High-intent supporter",
            high,
            "가치가 통하는 이유와 실제 다음 행동 조건을 말하게 합니다.",
        ),
        (
            "conditional",
            "Conditional adopter",
            conditional,
            "가격·신뢰·사용성 조건 중 무엇이 바뀌면 전환되는지 압박 질문을 맡깁니다.",
        ),
        (
            "skeptic",
            "Low-intent skeptic",
            low,
            "현재 대체 행동이 충분한 이유와 group discussion에서 남는 반대를 드러냅니다.",
        ),
    ]
    fallback_pool = sorted(reactions, key=cohort_sort_key)
    used_names: set[str] = set()
    for role, label, pool, sampling_rule in mix_specs:
        picks: list[dict[str, Any]] = []
        candidate_pool = pool or fallback_pool
        for reaction in candidate_pool:
            name = str(reaction.get("name") or _segment_persona_label(reaction))
            if name in used_names:
                continue
            picks.append(reaction)
            used_names.add(name)
            if len(picks) >= 2:
                break
        if picks:
            participant_mix.append(
                {
                    "role": role,
                    "label": label,
                    "persona_count": len(picks),
                    "personas": [_segment_persona_label(reaction) for reaction in picks],
                    "avg_adoption": round(
                        sum(_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in picks) / len(picks)
                    ),
                    "common_drivers": unique_top(
                        [driver for reaction in picks for driver in _listify(reaction.get("positive_drivers"))],
                        limit=3,
                    ),
                    "common_objections": unique_top_risks(
                        [risk for reaction in picks for risk in _listify(reaction.get("top_risks"))],
                        limit=3,
                    ),
                    "sampling_rule": sampling_rule,
                }
            )

    top_objection = objection_cards[0] if objection_cards else {}
    top_objection_text = str(top_objection.get("objection") or top_objection.get("category") or "전환 장벽")
    strongest = str(contrast.get("strongest_cohort") or "high_intent")
    weakest = str(contrast.get("weakest_cohort") or "low_intent")
    target = normalized.get("target_market") or "초기 타깃 고객"
    alternatives = normalized.get("current_alternatives") or "현재 쓰는 대체 행동"
    product = normalized.get("product_name") or "제품"

    return {
        "objective": f"{product}의 cohort 간 찬반 이유를 한 자리에 놓고, 실제 포커스그룹 전에 토론 쟁점을 좁힙니다.",
        "recommended_group_size": f"{max(3, min(6, sum(item['persona_count'] for item in participant_mix)))} synthetic personas, 20-25 minutes",
        "participant_mix": participant_mix,
        "discussion_protocol": [
            {
                "stage": "Concept read",
                "timebox_minutes": 3,
                "moderator_prompt": f"{target}에게 {product} 설명을 60초로 읽히고 각자 첫 반응을 한 문장으로 말하게 합니다.",
                "capture": "이해한 가치, 헷갈린 표현, 첫 adoption score",
            },
            {
                "stage": "Current alternative comparison",
                "timebox_minutes": 6,
                "moderator_prompt": f"현재 대체 행동({alternatives}) 대비 언제 바꿀 이유가 생기는지 묻습니다.",
                "capture": "switching trigger, stay reason, proof required",
            },
            {
                "stage": "Objection confrontation",
                "timebox_minutes": 7,
                "moderator_prompt": f"반복 objection인 '{top_objection_text}'를 공개하고 supporter/conditional/skeptic이 서로 반박하거나 조건을 붙이게 합니다.",
                "capture": "objection that survives group discussion, evidence that reduces it",
            },
            {
                "stage": "Decision moment",
                "timebox_minutes": 5,
                "moderator_prompt": "오늘 인터뷰 신청/무료체험/공유 중 하나를 고르게 하고 왜 지금은 하지 않는지도 기록합니다.",
                "capture": "observable next action, blocker, revised message",
            },
        ],
        "interaction_rules": [
            "각 persona 발화는 2문장 이하로 제한하고, 새 설정을 지어내지 않습니다.",
            "동의/반박/조건부 수용/질문 중 하나의 action label을 매 발화에 붙입니다.",
            "평균 점수를 재계산하지 말고, 토론 후에도 남는 objection과 필요한 증거만 기록합니다.",
            "실제 고객 발언처럼 인용하지 말고 synthetic pre-research signal로만 표시합니다.",
        ],
        "contrast_questions": [
            f"왜 {strongest} cohort는 관심을 보였고 {weakest} cohort는 머물렀는가?",
            f"{top_objection_text}를 줄이려면 제품 기능, 가격, 메시지 중 무엇을 먼저 바꿔야 하는가?",
            "다른 사람이 긍정적으로 말해도 각 persona의 다음 행동 의향이 실제로 바뀌는가?",
            "실제 포커스그룹에서 반드시 보여줘야 할 proof/evidence는 무엇인가?",
        ],
        "capture_template": {
            "columns": [
                "persona_role",
                "action_label",
                "quote_or_paraphrase",
                "driver_or_objection",
                "proof_required",
                "next_action_selected",
                "real_user_probe",
            ],
            "decision_use": "토론에서 살아남은 objection을 interview guide와 message A/B test의 첫 probe로 올립니다.",
        },
        "cohort_context": {
            "adoption_gap": contrast.get("adoption_gap", 0),
            "cohort_count": len(cohorts),
            "recommended_comparison": contrast.get("recommended_comparison", "cohort driver와 objection을 나란히 검증"),
        },
        "caution": "Synthetic focus group은 토론 쟁점 생성용입니다. 우선순위 결정 전에는 실제 사용자 4-6명으로 같은 protocol을 검증하세요.",
    }


def build_pricing_sensitivity(brief: dict[str, Any], reactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn persona price friction into a bounded pricing-lab readout.

    This is intentionally deterministic: it does not pretend to know true WTP,
    but it gives founders a price ladder to validate with real users before
    spending another Solar run or building a payment flow.
    """

    normalized = validate_brief(brief)
    pricing_options = normalized.get("pricing", [])
    total = max(1, len(reactions))
    base_adoption = round(sum(r.get("adoption_likelihood", 0) for r in reactions) / total)
    avg_price_friction = sum(_price_risk_score(r.get("price_resistance")) for r in reactions) / total
    high_friction = [reaction for reaction in reactions if _price_risk_score(reaction.get("price_resistance")) >= 2]
    low_friction = [reaction for reaction in reactions if _price_risk_score(reaction.get("price_resistance")) <= 1]

    parsed_prices = [_extract_price_amount_krw(option) for option in pricing_options]
    numeric_prices = sorted(price for price in parsed_prices if price is not None)
    min_price = numeric_prices[0] if numeric_prices else None
    max_price = numeric_prices[-1] if numeric_prices else None

    option_readouts: list[dict[str, Any]] = []
    for index, option in enumerate(pricing_options):
        amount = parsed_prices[index]
        if amount is None or min_price is None or max_price is None or min_price == max_price:
            relative_position = index / max(1, len(pricing_options) - 1)
        else:
            relative_position = (amount - min_price) / max(1, max_price - min_price)

        friction_penalty = round(avg_price_friction * 6 + relative_position * avg_price_friction * 10)
        estimated_adoption = max(5, min(95, base_adoption - friction_penalty))
        if estimated_adoption >= 65:
            test_role = "entry_offer"
            probe = "랜딩/인터뷰에서 이 가격을 기본 결제 조건으로 제시하고 실제 결제 의향을 확인"
        elif estimated_adoption >= 45:
            test_role = "threshold_probe"
            probe = "무료 체험·성과 보장·월/건별 결제 중 어떤 조건이 가격 저항을 낮추는지 A/B로 확인"
        else:
            test_role = "premium_stress_test"
            probe = "프리미엄 가격으로는 어떤 증거·ROI·대체재 대비 이점이 있어야 수용되는지 탐색"

        option_readouts.append(
            {
                "option": option,
                "parsed_price_krw": amount,
                "test_role": test_role,
                "estimated_adoption_after_friction": estimated_adoption,
                "friction_penalty": friction_penalty,
                "recommended_probe": probe,
            }
        )

    if option_readouts:
        recommended_option = sorted(
            option_readouts,
            key=lambda item: (-item["estimated_adoption_after_friction"], item.get("parsed_price_krw") is None, item.get("parsed_price_krw") or 0),
        )[0]
        recommended_price_probe = f"먼저 '{recommended_option['option']}' 조건으로 실제 결제/예약 의향을 검증하세요."
    else:
        recommended_price_probe = "가격 옵션이 없으므로 무료 체험, 월 구독, 건별 결제 중 2-3개 가격안을 먼저 정의하세요."

    top_price_objection = _first_counter_item(
        [risk for reaction in high_friction for risk in reaction.get("top_risks", [])],
        "가격 대비 효용 증거 부족",
    )

    return {
        "base_adoption": base_adoption,
        "overall_price_risk": _price_risk_from_reactions(reactions),
        "price_sensitive_persona_count": len(high_friction),
        "price_tolerant_persona_count": len(low_friction),
        "top_price_objection": top_price_objection,
        "options": option_readouts,
        "recommended_price_probe": recommended_price_probe,
        "validation_questions": unique_top(
            [
                "이 가격을 오늘 결제하지 않는다면 가장 큰 이유는 무엇인가요?",
                "무료 체험 후 유료 전환을 판단할 최소 성과/증거는 무엇인가요?",
                "현재 대체 행동에 쓰는 시간·돈과 비교해 어느 가격까지 납득되나요?",
                *[
                    reaction.get("next_validation_question", "")
                    for reaction in high_friction[:3]
                    if reaction.get("next_validation_question")
                ],
            ],
            limit=5,
        ),
        "sensitive_persona_examples": [_segment_persona_label(reaction) for reaction in high_friction[:5]],
        "tolerant_persona_examples": [_segment_persona_label(reaction) for reaction in low_friction[:5]],
        "disclaimer": "Synthetic pricing sensitivity only; validate willingness-to-pay with real purchase or reservation behavior.",
    }


def build_decision_board(
    reactions: list[dict[str, Any]],
    *,
    brief_quality: dict[str, Any] | None = None,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate simulation signals into a launch-research decision card.

    The simulator should not pretend to make a real go-to-market decision from
    synthetic personas alone. This board is intentionally framed as the *next
    research action*: continue to real interviews, refine the brief/message, try
    a narrower segment, or hold the idea in its current form.
    """

    total = max(1, len(reactions))
    adoption = round(sum(r.get("adoption_likelihood", 0) for r in reactions) / total)
    need_fit = round(sum(r.get("need_fit_score", 0) for r in reactions) / total)
    high_intent = sum(1 for r in reactions if r.get("adoption_likelihood", 0) >= 70)
    low_intent = sum(1 for r in reactions if r.get("adoption_likelihood", 0) < 40)
    high_price_risk = sum(1 for r in reactions if _price_risk_score(r.get("price_resistance")) >= 2)
    brief_score = int((brief_quality or {}).get("score", 100))
    segment_roles = {str(segment.get("role")) for segment in (segments or [])}
    top_objection = (objections or [{}])[0].get("category") if objections else None

    if brief_score < 60:
        decision = "Refine"
        confidence = "low"
        next_step = "제품 설명·타깃·가격·대체 행동을 먼저 보강한 뒤 같은 panel로 재시뮬레이션"
        rationale = "브리프가 부족해 persona 반응을 실제 시장 신호로 해석하기 어렵습니다."
    elif adoption >= 70 and need_fit >= 70 and high_intent >= max(1, total // 2) and high_price_risk <= total // 2:
        decision = "Go"
        confidence = "medium"
        next_step = "우선 검증 타깃 5-8명 실제 인터뷰와 랜딩페이지 메시지 테스트로 이동"
        rationale = "평균 채택/필요 적합도가 높고 강한 긍정 persona가 충분합니다."
    elif "beachhead" in segment_roles and adoption >= 50:
        decision = "Segment Pivot"
        confidence = "medium"
        next_step = "전체 평균보다 반응이 강한 beachhead segment만 따로 모집해 문제-해결 적합도를 검증"
        rationale = "전체 평균은 애매하지만 특정 세그먼트에서 다음 학습 루프를 진행할 신호가 있습니다."
    elif need_fit >= 60 and (adoption >= 45 or high_price_risk > total // 2):
        decision = "Refine"
        confidence = "medium"
        next_step = "가격·신뢰·사용 난이도 중 가장 큰 objection을 낮추는 메시지/패키징 버전으로 재실험"
        rationale = "문제 필요성은 보이지만 현재 제안은 전환 장벽이 큽니다."
    elif adoption < 45 and need_fit < 55 and low_intent >= max(1, total // 2):
        decision = "Hold"
        confidence = "medium"
        next_step = "현재 형태는 보류하고 문제 가설 또는 대체 행동 인터뷰부터 다시 확인"
        rationale = "필요 적합도와 채택 가능성이 모두 낮아 구현보다 문제 재정의가 먼저입니다."
    else:
        decision = "Refine"
        confidence = "low"
        next_step = "표본을 늘리거나 대비 panel로 다시 실행해 신호가 재현되는지 확인"
        rationale = "현재 synthetic 결과만으로는 명확한 다음 결정을 내리기 어렵습니다."

    criteria = [
        f"avg adoption {adoption}/100",
        f"avg need fit {need_fit}/100",
        f"high-intent personas {high_intent}/{len(reactions)}",
        f"high price/trust friction {high_price_risk}/{len(reactions)}",
        f"brief quality {brief_score}/100",
    ]
    if top_objection:
        criteria.append(f"top objection: {top_objection}")

    return {
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "next_step": next_step,
        "criteria": criteria,
        "decision_labels": ["Go", "Refine", "Segment Pivot", "Hold"],
        "disclaimer": "Synthetic pre-research signal only; validate with real users before product or investment decisions.",
    }


def build_switching_analysis(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    objections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the product against the user's current alternative.

    Founders often need to know not just whether personas like an idea, but
    what would make them stop doing the current workaround. This deterministic
    layer translates persona drivers/risks into switching triggers and concrete
    validation tests without adding another model call.
    """

    normalized = validate_brief(brief)
    current_alternatives = normalized.get("current_alternatives") or "현재 대체 행동 미정"
    product_name = normalized.get("product_name") or "제품"
    objections = objections or []

    drivers = unique_top(
        [driver for reaction in reactions for driver in reaction.get("positive_drivers", [])],
        limit=4,
    )
    risks = unique_top_risks(
        [risk for reaction in reactions for risk in reaction.get("top_risks", [])],
        limit=4,
    )
    high_intent = [reaction for reaction in reactions if reaction.get("adoption_likelihood", 0) >= 70]
    conditional = [reaction for reaction in reactions if 55 <= reaction.get("adoption_likelihood", 0) < 70]
    top_objection = objections[0] if objections else {}
    top_objection_label = str(top_objection.get("category") or "전환 장벽")
    top_fix = str(top_objection.get("suggested_fix") or "전환 장벽을 낮추는 증거를 먼저 보여주기")

    if normalized.get("current_alternatives"):
        why_current_persists = [
            f"사용자는 이미 '{current_alternatives}'로 문제를 해결하고 있어, 새 서비스는 기존 습관보다 분명한 이득을 보여줘야 합니다."
        ]
    else:
        why_current_persists = [
            "현재 대체 행동이 명시되지 않아 switching trigger 해석이 약합니다. 실제 인터뷰에서 먼저 대체 행동을 확인해야 합니다."
        ]
    if risks:
        why_current_persists.append(f"반복 우려: {risks[0]}")
    if top_objection_label != "전환 장벽":
        why_current_persists.append(f"가장 큰 objection category는 {top_objection_label}입니다.")

    switching_triggers: list[str] = []
    if drivers:
        switching_triggers.append(f"'{drivers[0]}' 효용이 현재 방식보다 빠르고 구체적으로 체감될 때")
    if high_intent:
        examples = ", ".join(_segment_persona_label(reaction) for reaction in high_intent[:2])
        switching_triggers.append(f"초기 반응이 강한 persona({examples})에게 먼저 문제-해결 적합도를 확인할 때")
    if conditional:
        switching_triggers.append("조건부 전환 persona에게 가격·신뢰·사용 난이도 중 하나를 낮춘 대안을 보여줄 때")
    switching_triggers.append(top_fix)

    validation_tests = unique_top(
        [
            f"인터뷰에서 '{current_alternatives}'와 {product_name}을 나란히 놓고 어떤 상황에서 바꿀지 묻기",
            "랜딩/데모 첫 화면에 현재 방식 대비 절약 시간·실패 감소·안심 근거 중 하나를 숫자나 예시로 제시하기",
            f"1순위 objection({top_objection_label})을 낮춘 메시지와 낮추지 않은 메시지를 A/B로 비교하기",
            "전환 의향보다 '지금 방식이 충분한 이유'를 먼저 묻고, 그 답을 다음 brief에 반영하기",
        ],
        limit=4,
    )

    return {
        "current_alternatives": current_alternatives,
        "why_current_alternative_persists": unique_top(why_current_persists, limit=4),
        "switching_triggers": unique_top(switching_triggers, limit=4),
        "must_prove": unique_top(
            [
                drivers[0] if drivers else "현재 방식보다 나은 구체적 효용",
                risks[0] if risks else "전환 장벽이 실제로 낮아지는 조건",
                top_objection_label,
            ],
            limit=3,
        ),
        "validation_tests": validation_tests,
    }


def _split_current_alternatives(value: Any, *, limit: int = 4) -> list[str]:
    text = _first_text(value)
    if not text:
        return []

    normalized = re.sub(r"\s+", " ", text).strip()
    parts = [part.strip(" .,/·") for part in re.split(r"\s*(?:/|,|·|또는|혹은|그리고|및|\bor\b)\s*", normalized) if part.strip()]
    if 1 < len(parts) <= limit:
        return unique_top(parts, limit=limit)
    return [normalized[:160]]


def build_competitive_benchmark(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    switching_analysis: dict[str, Any] | None = None,
    objections: list[dict[str, Any]] | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    """Frame current alternatives as a bounded competitive benchmark.

    Switching analysis says what must change. This matrix makes the comparison
    more operational for a founder: each current workaround gets a likely reason
    users stay, the Upkinsey advantage to test, the unresolved barrier, and one
    interview/landing probe. It is deterministic and local so it does not spend
    an extra Solar call.
    """

    normalized = validate_brief(brief)
    switching_analysis = switching_analysis or {}
    objections = objections or []
    current = _first_text(switching_analysis.get("current_alternatives"), normalized.get("current_alternatives"))
    alternatives = _split_current_alternatives(current, limit=limit) or ["현재 대체 행동 미정"]
    product_name = normalized.get("product_name") or "제품"

    drivers = unique_top(
        [driver for reaction in reactions for driver in reaction.get("positive_drivers", [])],
        limit=limit,
    )
    risks = unique_top_risks(
        [risk for reaction in reactions for risk in reaction.get("top_risks", [])],
        limit=limit,
    )
    top_objection = objections[0] if objections else {}
    objection_label = str(top_objection.get("category") or classify_objection(risks[0] if risks else "") or "전환 장벽")
    suggested_fix = str(top_objection.get("suggested_fix") or OBJECTION_FIXES.get(objection_label) or "현재 방식 대비 차이를 한 화면에서 증명")
    switching_triggers = _listify(switching_analysis.get("switching_triggers"))
    must_prove = _listify(switching_analysis.get("must_prove"))

    rows: list[dict[str, Any]] = []
    for index, alternative in enumerate(alternatives[: max(1, limit)]):
        driver = drivers[index % len(drivers)] if drivers else "핵심 효용"
        risk = risks[index % len(risks)] if risks else objection_label
        trigger = switching_triggers[index % len(switching_triggers)] if switching_triggers else f"{driver}가 현재 방식보다 분명할 때"
        proof = must_prove[index % len(must_prove)] if must_prove else driver
        rows.append(
            {
                "alternative": alternative,
                "why_users_stay": f"익숙하고 이미 작동하는 해결책이므로 '{risk}' 우려가 있으면 계속 머물 가능성이 큽니다.",
                "product_advantage_to_test": f"{product_name}이(가) {proof}을(를) {alternative}보다 빠르고 안전하게 제공하는지 검증",
                "unresolved_barrier": risk,
                "switching_trigger": trigger,
                "validation_probe": f"{alternative}와 {product_name}을(를) 나란히 보여주고, 어떤 증거가 있으면 바꿀지 묻기",
            }
        )

    return {
        "summary": f"{product_name}은(는) 현재 대안 {len(rows)}개와 비교해 전환 이유와 남은 장벽을 검증해야 합니다.",
        "current_alternatives": current or "현재 대체 행동 미정",
        "primary_barrier": objection_label,
        "suggested_positioning_fix": suggested_fix,
        "benchmarks": rows,
        "recommended_next_probe": rows[0]["validation_probe"] if rows else "현재 대체 행동을 먼저 인터뷰에서 확인",
    }


def build_assumption_stress_test(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    pricing_sensitivity: dict[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Convert synthetic signals into falsifiable product assumptions.

    Pre-market simulations are most useful when they identify what could be
    wrong. This stress test keeps the report from stopping at persona opinions:
    it names the riskiest assumptions, shows the synthetic signal behind each
    one, and proposes the smallest real-world test that could falsify it.
    """

    normalized = validate_brief(brief)
    segments = segments or []
    objections = objections or []
    switching_analysis = switching_analysis or {}
    pricing_sensitivity = pricing_sensitivity or {}
    total = max(1, len(reactions))
    adoption = round(sum(_as_int(r.get("adoption_likelihood"), default=0) for r in reactions) / total)
    need_fit = round(sum(_as_int(r.get("need_fit_score"), default=0) for r in reactions) / total)
    high_price_friction = sum(1 for r in reactions if _price_risk_score(r.get("price_resistance")) >= 2)
    low_intent = sum(1 for r in reactions if _as_int(r.get("adoption_likelihood"), default=0) < 55)

    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    objection_categories = {str(item.get("category")) for item in objections}
    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or "초기 타깃"
    current_alternative = str(
        switching_analysis.get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 대체 행동"
    )

    def severity_label(score: int) -> str:
        if score >= 3:
            return "High"
        if score == 2:
            return "Medium"
        return "Low"

    cards: list[dict[str, Any]] = []

    value_score = 3 if need_fit < 55 or adoption < 45 else 2 if need_fit - adoption >= 15 or low_intent >= total / 2 else 1
    cards.append(
        {
            "area": "value",
            "assumption": f"{target_market}이(가) {product_name}의 핵심 효용을 실제 문제 해결로 느낀다.",
            "risk_level": severity_label(value_score),
            "why_it_matters": "필요 적합도는 높아도 채택 의향이 따라오지 않으면 메시지보다 문제/해결 가설이 흔들릴 수 있습니다.",
            "synthetic_signal": f"avg adoption {adoption}/100, avg need fit {need_fit}/100, low/conditional intent {low_intent}/{len(reactions)}",
            "falsification_test": "타깃 5명에게 제품 설명 전 최근 문제 상황을 묻고, 설명 후 다음 행동(가입/데모/인터뷰)을 실제로 선택하게 하기",
            "pass_signal": "5명 중 3명 이상이 최근 문제를 구체적으로 말하고 다음 행동을 선택",
            "source_signal": f"adoption={adoption}; need_fit={need_fit}",
        }
    )

    price_score = 3 if high_price_friction / total >= 0.5 else 2 if pricing_sensitivity.get("overall_price_risk") in {"Medium", "High"} else 1
    top_price_objection = str(pricing_sensitivity.get("top_price_objection") or top_objection.get("objection") or "가격 대비 효용 증거")
    cards.append(
        {
            "area": "pricing",
            "assumption": "제시한 가격/과금 방식이 핵심 효용 대비 납득 가능하다.",
            "risk_level": severity_label(price_score),
            "why_it_matters": "합성 호감이 있어도 가격 저항이 높으면 실제 구매·예약 행동으로 이어지지 않습니다.",
            "synthetic_signal": f"high price friction {high_price_friction}/{len(reactions)}; top objection: {top_price_objection}",
            "falsification_test": "랜딩/인터뷰에서 무료 설명 뒤가 아니라 가격표를 먼저 보여주고 결제·예약 의향과 망설임 이유를 기록",
            "pass_signal": "응답자의 50% 이상이 특정 가격안에서 예약/대기자 등록 같은 비용 있는 다음 행동을 수락",
            "source_signal": f"price_risk={pricing_sensitivity.get('overall_price_risk', _price_risk_from_reactions(reactions))}",
        }
    )

    trust_score = 3 if {"신뢰 부족", "개인정보/데이터 우려"} & objection_categories else 2 if any(
        classify_objection(risk) in {"신뢰 부족", "개인정보/데이터 우려"}
        for reaction in reactions
        for risk in reaction.get("top_risks", [])
    ) else 1
    trust_fix = next(
        (str(item.get("suggested_fix")) for item in objections if item.get("category") in {"신뢰 부족", "개인정보/데이터 우려"}),
        "판단 근거, 데이터 처리 방식, 책임 범위를 한 화면에 제시",
    )
    cards.append(
        {
            "area": "trust",
            "assumption": "사용자가 결과 품질·데이터 처리·책임 범위를 충분히 신뢰한다.",
            "risk_level": severity_label(trust_score),
            "why_it_matters": "신뢰 장벽은 기능 선호보다 먼저 전환을 막는 경우가 많아, 데모 이전에 근거 제시 방식이 필요합니다.",
            "synthetic_signal": f"trust/privacy objection present: {trust_score >= 2}; suggested fix: {trust_fix}",
            "falsification_test": "근거/보안/책임 설명이 있는 버전과 없는 버전을 비교해 불안 점수와 선호 버전을 측정",
            "pass_signal": "근거 제시 버전 선호 60% 이상 또는 신뢰 불안 점수 20% 이상 감소",
            "source_signal": f"objection_categories={sorted(objection_categories)}",
        }
    )

    usability_score = 3 if "사용법 복잡성" in objection_categories else 2 if any(
        classify_objection(risk) == "사용법 복잡성" for reaction in reactions for risk in reaction.get("top_risks", [])
    ) else 1
    cards.append(
        {
            "area": "usability",
            "assumption": "첫 사용 흐름이 타깃 사용자에게 충분히 쉽고 익숙하다.",
            "risk_level": severity_label(usability_score),
            "why_it_matters": "초기 사용 난이도가 높으면 문제 인식이 있어도 체험 시작 전에 이탈합니다.",
            "synthetic_signal": f"usability objection present: {usability_score >= 2}",
            "falsification_test": "1분짜리 클릭 더미 또는 화면 3장을 보여주고 사용자가 다음 단계와 필요한 입력을 말로 설명하게 하기",
            "pass_signal": "5명 중 4명 이상이 첫 행동과 입력 정보를 도움 없이 설명",
            "source_signal": "objection=사용법 복잡성",
        }
    )

    switching_score = 3 if not normalized.get("current_alternatives") else 2 if "대체재가 이미 충분함" in objection_categories else 1
    must_prove = ", ".join(_listify(switching_analysis.get("must_prove"))[:3]) or "현재 방식보다 나은 구체적 효용"
    cards.append(
        {
            "area": "switching",
            "assumption": "현재 대체 행동보다 새 서비스로 바꿀 만큼 분명한 전환 계기가 있다.",
            "risk_level": severity_label(switching_score),
            "why_it_matters": "사용자는 문제를 느껴도 기존 습관이 충분하면 새 서비스를 시도하지 않습니다.",
            "synthetic_signal": f"current alternative: {current_alternative}; must prove: {must_prove}",
            "falsification_test": "제품 설명 전에 현재 해결법을 먼저 묻고, 새 방식과 나란히 비교해 바꿀 상황을 선택하게 하기",
            "pass_signal": "5명 중 3명 이상이 같은 전환 순간을 말하고 현재 방식 대비 새 방식을 선택",
            "source_signal": f"current_alternative={current_alternative}",
        }
    )

    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    area_order = {"value": 0, "pricing": 1, "trust": 2, "usability": 3, "switching": 4}
    ranked = sorted(cards, key=lambda card: (severity_order[card["risk_level"]], area_order[card["area"]]))
    limited = ranked[: max(1, min(limit, len(ranked)))]
    overall_risk = limited[0]["risk_level"] if limited else "Low"
    watchouts = [f"{card['area']}: {card['synthetic_signal']}" for card in limited if card["risk_level"] != "Low"]

    return {
        "overall_risk": overall_risk,
        "watchouts": unique_top(watchouts, limit=5),
        "assumptions": limited,
        "recommended_next_step": limited[0]["falsification_test"] if limited else "상위 가설을 실제 사용자 행동으로 검증",
        "disclaimer": "Synthetic stress test only; treat each item as a falsifiable assumption, not a market fact.",
    }

def build_validation_plan(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    decision_board: dict[str, Any] | None = None,
    brief_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a concrete real-user validation plan from synthetic signals.

    Upkinsey should help founders leave the simulation with the next research
    loop ready to run. This planner stays deterministic and bounded: it converts
    persona reactions, objection cards, and the decision board into a small
    interview/screener checklist rather than making another model call.
    """

    normalized = validate_brief(brief)
    segments = segments or []
    objections = objections or []
    decision = str((decision_board or {}).get("decision") or "Refine")
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    target_market = normalized.get("target_market") or "가장 문제를 자주 겪는 초기 사용자"
    product_name = normalized.get("product_name") or "제품"

    sample_size_by_decision = {
        "Go": "5-8명의 우선 검증 타깃 인터뷰 + 1개 랜딩/메시지 테스트",
        "Segment Pivot": "5-8명의 반응 강한 세그먼트 인터뷰",
        "Refine": "3-5명의 조건부 전환/거부 persona 인터뷰",
        "Hold": "3-5명의 문제 경험자 탐색 인터뷰",
    }
    objective_by_decision = {
        "Go": "긍정 신호가 실제 문제-해결/결제 의향으로 재현되는지 확인",
        "Segment Pivot": "전체 시장보다 강하게 반응한 beachhead 세그먼트의 공통 맥락 확인",
        "Refine": "가격·신뢰·사용성 objection 중 전환을 막는 1순위 장벽 확인",
        "Hold": "현재 제안보다 먼저 검증해야 할 문제 빈도와 대체 행동 확인",
    }

    segment_label = str(top_segment.get("segment") or target_market)
    persona_examples = _listify(top_segment.get("persona_examples"))[:3]
    recruiting_focus = f"{segment_label}: {target_market}"
    if persona_examples:
        recruiting_focus += f" (synthetic examples: {', '.join(persona_examples)})"

    validation_questions = [
        str(reaction.get("next_validation_question"))
        for reaction in reactions
        if str(reaction.get("next_validation_question") or "").strip()
    ]
    primary_objection = str(top_objection.get("objection") or top_objection.get("category") or "가장 큰 전환 장벽")
    objection_probe = str(top_objection.get("suggested_fix") or "이 우려가 실제 사용/구매를 막는지 확인")

    screener_questions = unique_top(
        [
            f"최근 1개월 안에 {product_name}이 해결하려는 문제를 직접 겪었나요?",
            f"현재는 이 문제를 어떻게 해결하나요? ({normalized.get('current_alternatives') or '대체 행동 확인'})",
            "이 문제 해결에 본인이 비용/시간 투입을 결정할 수 있나요?",
            "비슷한 앱, 서비스, 사람의 도움을 써본 경험이 있나요?",
        ],
        limit=4,
    )
    interview_questions = unique_top(
        [
            *(validation_questions[:3]),
            f"{primary_objection} 우려가 생기는 순간은 언제인가요?",
            f"다음 보완책이 있으면 사용/결제 의향이 달라지나요? — {objection_probe}",
            "현재 대체 행동을 그만두고 새 서비스를 시도할 만큼 강한 전환 계기는 무엇인가요?",
            "가격/무료체험/보상 조건 중 무엇이 가장 먼저 확인되어야 하나요?",
        ],
        limit=6,
    )

    success_criteria = [
        "인터뷰 대상의 60% 이상이 최근 문제 경험과 현재 대체 행동을 구체적으로 설명",
        "절반 이상이 핵심 가치 메시지를 듣고 다음 행동(가입/데모/대기자 등록)을 선택",
        f"1순위 objection({primary_objection})을 낮추는 조건이 반복적으로 확인",
    ]
    if (brief_quality or {}).get("score", 100) < 80:
        success_criteria.append("보강된 brief에서 누락 필드 없이 같은 질문으로 재시뮬레이션 가능")

    return {
        "objective": objective_by_decision.get(decision, objective_by_decision["Refine"]),
        "recommended_sample": sample_size_by_decision.get(decision, sample_size_by_decision["Refine"]),
        "recruiting_focus": recruiting_focus,
        "screener_questions": screener_questions,
        "interview_questions": interview_questions,
        "prototype_or_message_to_test": str(
            (top_segment.get("primary_driver") if top_segment else "핵심 가치")
            or "핵심 가치"
        ),
        "success_criteria": success_criteria,
        "next_artifact": "랜딩페이지 메시지, 1분 데모, 또는 인터뷰 스크립트 중 하나로 바로 실행",
    }


def build_recruiting_screener(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    validation_plan: dict[str, Any] | None = None,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    intent_cohort_contrast: dict[str, Any] | None = None,
    evidence_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn synthetic signals into a bounded real-user recruiting screener.

    The validation plan says what to learn. This pack makes the first fieldwork
    step operational: who qualifies, who should be screened out, which quota
    cells to fill, and how to route respondents. It is deterministic/local so a
    report can be used immediately without another Solar call.
    """

    normalized = validate_brief(brief)
    validation_plan = validation_plan or {}
    segments = segments or []
    objections = objections or []
    evidence_quality = evidence_quality or {}
    intent_cohort_contrast = intent_cohort_contrast or {}

    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or "초기 문제 경험자"
    current_alternative = normalized.get("current_alternatives") or "현재 대체 행동"
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    segment_label = str(top_segment.get("segment") or target_market)
    primary_driver = str(
        top_segment.get("primary_driver")
        or validation_plan.get("prototype_or_message_to_test")
        or "핵심 가치 제안"
    )
    primary_objection = str(top_objection.get("category") or top_objection.get("objection") or "전환 장벽")
    evidence_level = str(evidence_quality.get("level") or "exploratory")
    persona_examples = _listify(top_segment.get("persona_examples"))[:3]

    if evidence_level == "decision_support":
        recommended_completes = "8-12명: 주 타깃 6-8명 + 비교/반례 2-4명"
    elif evidence_level == "directional":
        recommended_completes = "6-8명: 주 타깃 4-6명 + 반례 2명"
    else:
        recommended_completes = "4-6명: 문제 경험자 탐색 3-4명 + 비경험자/저의향 반례 1-2명"

    must_have = unique_top(
        [
            f"{target_market}에 해당하거나 같은 구매/사용 맥락을 직접 설명할 수 있음",
            f"최근 1개월 안에 {product_name}이 해결하려는 문제를 겪었음",
            f"현재 해결법을 구체적으로 설명 가능: {current_alternative}",
            "서비스 선택/결제/도입에 본인이 영향을 줄 수 있음",
        ],
        limit=4,
    )
    disqualifiers = unique_top(
        [
            "문제를 직접 겪은 적이 없고 주변 사례만 말함",
            "현재 대체 행동·비용·불편을 구체적으로 설명하지 못함",
            "제품 카테고리 종사자/리서치 참여 목적만 강한 응답자",
            "가격·신뢰·사용 조건을 전혀 평가할 수 없는 응답자",
        ],
        limit=4,
    )

    base_screener_questions = _listify(validation_plan.get("screener_questions"))[:4]
    question_cards = [
        {
            "question": base_screener_questions[0]
            if base_screener_questions
            else f"최근 1개월 안에 {product_name} 관련 문제를 직접 겪었나요?",
            "accept_if": "최근 사례, 빈도, 손실 시간/비용 중 1개 이상을 구체적으로 설명",
            "reject_if": "막연한 관심만 있고 최근 경험이 없음",
            "source_signal": "problem_recency",
        },
        {
            "question": base_screener_questions[1]
            if len(base_screener_questions) > 1
            else f"현재는 이 문제를 어떻게 해결하나요? ({current_alternative})",
            "accept_if": "현재 대안, 불편, 계속 쓰는 이유를 함께 설명",
            "reject_if": "현재 해결법이 없거나 기억하지 못함",
            "source_signal": "switching_context",
        },
        {
            "question": f"{primary_driver} 메시지를 보면 어떤 다음 행동을 할 수 있나요?",
            "accept_if": "가입/문의/데모/동료 공유 등 관찰 가능한 행동을 선택",
            "reject_if": "좋아 보인다는 평가만 하고 행동 의향은 없음",
            "source_signal": "value_driver",
        },
        {
            "question": f"{primary_objection} 우려가 있으면 무엇을 확인해야 안심되나요?",
            "accept_if": "필요 증거, 조건, 보완책을 구체적으로 말함",
            "reject_if": "우려가 없다고만 답하거나 확인 조건을 말하지 못함",
            "source_signal": "top_objection",
        },
    ]

    quota_cells = [
        {
            "cell": "primary_target",
            "target": segment_label,
            "minimum": 3,
            "reason": f"가장 먼저 검증할 synthetic response segment. 예: {', '.join(persona_examples) if persona_examples else target_market}",
        },
        {
            "cell": "current_alternative_users",
            "target": str(current_alternative),
            "minimum": 2,
            "reason": "전환 장벽은 현재 대안을 실제로 쓰는 사람에게서 가장 잘 드러남",
        },
        {
            "cell": "objection_heavy_or_low_intent",
            "target": primary_objection,
            "minimum": 1,
            "reason": "긍정 평균 뒤의 반례를 찾아 메시지/제품 리스크를 줄임",
        },
    ]

    cohort_rows = intent_cohort_contrast.get("cohorts") if isinstance(intent_cohort_contrast.get("cohorts"), list) else []
    routing_rules = []
    for cohort in cohort_rows[:3]:
        routing_rules.append(
            {
                "route": str(cohort.get("label") or cohort.get("cohort") or "cohort"),
                "assign_when": str(cohort.get("validation_focus") or "응답자의 의향과 objection 패턴이 해당 cohort와 유사"),
                "probe": str(cohort.get("validation_focus") or "의향이 높거나 낮아진 구체적 조건을 확인"),
            }
        )
    if not routing_rules:
        routing_rules = [
            {
                "route": "high_intent",
                "assign_when": "핵심 가치에 즉시 다음 행동 의향을 보임",
                "probe": "어떤 증거를 보면 실제 가입/결제로 이어지는지 확인",
            },
            {
                "route": "low_or_conditional_intent",
                "assign_when": "관심은 있으나 가격·신뢰·사용 조건을 먼저 요구",
                "probe": "가장 먼저 낮춰야 할 전환 장벽과 현재 대안의 강점을 확인",
            },
        ]

    return {
        "objective": f"{product_name} 검증 인터뷰에 들어갈 실제 응답자를 선별",
        "target_profile": target_market,
        "recommended_completes": recommended_completes,
        "must_have_criteria": must_have,
        "disqualifiers": disqualifiers,
        "screener_questions": question_cards,
        "quota_cells": quota_cells,
        "routing_rules": routing_rules,
        "incentive_note": "짧은 20-30분 문제/대안 인터뷰 기준으로 보상 또는 커피 쿠폰을 명시하고, 제품 홍보가 아니라 검증 인터뷰임을 먼저 알림",
        "disclaimer": "Synthetic signals are only used to design recruitment; qualification must be based on real respondent experience.",
    }


def build_interview_discussion_guide(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    validation_plan: dict[str, Any] | None = None,
    recruiting_screener: dict[str, Any] | None = None,
    objections: list[dict[str, Any]] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    pricing_sensitivity: dict[str, Any] | None = None,
    message_angle_tests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a field-ready moderator guide from synthetic research signals.

    The validation plan and screener decide who to talk to and what to learn.
    This guide turns that into a bounded 30-minute interview flow: intro,
    concept read, warm-up, prioritized probes, note-taking rubric, and pass
    signals. It is local/deterministic so every saved simulation contains a
    reusable script without spending another model call.
    """

    normalized = validate_brief(brief)
    validation_plan = validation_plan or {}
    recruiting_screener = recruiting_screener or {}
    objections = objections or []
    switching_analysis = switching_analysis or {}
    pricing_sensitivity = pricing_sensitivity or {}
    message_angle_tests = message_angle_tests or []

    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or recruiting_screener.get("target_profile") or "초기 문제 경험자"
    description = normalized.get("description") or "제안된 제품 컨셉"
    current_alternatives = normalized.get("current_alternatives") or "현재 쓰는 대체 행동"
    features = _listify(normalized.get("features"))[:4]
    pricing = _listify(normalized.get("pricing"))[:3]
    primary_objection = objections[0] if objections else {}
    primary_objection_text = str(primary_objection.get("objection") or primary_objection.get("category") or "가장 큰 전환 장벽")
    primary_fix = str(primary_objection.get("suggested_fix") or "어떤 증거/조건이 있으면 우려가 낮아지는지 확인")
    validation_questions = _listify(validation_plan.get("interview_questions"))[:5]
    message_angle = message_angle_tests[0] if message_angle_tests else {}
    headline = str(message_angle.get("headline") or validation_plan.get("prototype_or_message_to_test") or "핵심 가치 메시지")

    top_reactions = sorted(
        reactions,
        key=lambda reaction: _as_int(reaction.get("adoption_likelihood"), default=50),
        reverse=True,
    )[:2]
    barrier_reactions = sorted(
        reactions,
        key=lambda reaction: _as_int(reaction.get("adoption_likelihood"), default=50),
    )[:2]
    synthetic_listen_fors = unique_top(
        [
            *[str(item) for reaction in top_reactions for item in _listify(reaction.get("positive_drivers"))],
            *[str(item) for reaction in barrier_reactions for item in _listify(reaction.get("top_risks"))],
            *[str(item.get("category") or item.get("objection")) for item in objections[:3]],
        ],
        limit=6,
    )

    concept_bits = [description]
    if features:
        concept_bits.append("주요 기능: " + ", ".join(features))
    if pricing:
        concept_bits.append("가격/과금 가정: " + ", ".join(str(item) for item in pricing))
    concept_read = f"{product_name}: " + " / ".join(bit for bit in concept_bits if bit)

    warmup_questions = unique_top(
        [
            f"최근에 {product_name}이 해결하려는 문제를 겪은 상황을 시간 순서대로 설명해 주세요.",
            f"현재는 이 문제를 어떻게 해결하나요? ({current_alternatives})",
            "그 해결법을 계속 쓰는 이유와 불편한 지점을 각각 하나씩 말해 주세요.",
            "이 문제를 해결하기 위해 최근 실제로 쓴 시간/돈/도움을 떠올려 주세요.",
        ],
        limit=4,
    )

    concept_reaction_tasks = [
        {
            "step": "unaided_reaction",
            "question": "컨셉 설명을 듣고, 무엇을 해주는 서비스라고 이해했는지 본인 말로 말해 주세요.",
            "what_to_listen_for": "이해도, 오해 지점, 핵심 가치가 자연스럽게 반복되는지",
            "source_signal": "understanding_score",
        },
        {
            "step": "problem_fit",
            "question": f"방금 말한 현재 해결법과 비교했을 때, {product_name}이 실제로 더 나은 순간은 언제인가요?",
            "what_to_listen_for": _listify(switching_analysis.get("switching_triggers"))[0]
            if _listify(switching_analysis.get("switching_triggers"))
            else "현재 대안을 멈출 만큼 강한 전환 계기",
            "source_signal": "switching_trigger",
        },
        {
            "step": "message_test",
            "question": f"'{headline}'라는 메시지를 보면 어떤 다음 행동을 할 수 있나요?",
            "what_to_listen_for": message_angle.get("pass_signal") or "가입/문의/공유/결제 등 관찰 가능한 행동",
            "source_signal": "message_angle",
        },
    ]

    objection_probes = [
        {
            "objection": str(item.get("category") or "우려"),
            "probe": f"{item.get('objection') or primary_objection_text} — 이 우려가 있으면 어떤 증거를 먼저 확인해야 하나요?",
            "possible_fix_to_test": str(item.get("suggested_fix") or primary_fix),
        }
        for item in objections[:4]
        if isinstance(item, dict)
    ] or [
        {
            "objection": "전환 장벽",
            "probe": f"{primary_objection_text}이 실제 사용/구매를 막는지 구체적으로 확인해 주세요.",
            "possible_fix_to_test": primary_fix,
        }
    ]

    price_probe = pricing_sensitivity.get("recommended_price_probe") or "제시 가격/무료체험 조건에서 실제 결제 전 확인할 기준을 묻기"
    if pricing:
        price_probe = f"{', '.join(str(item) for item in pricing)} 조건을 보여준 뒤: {price_probe}"

    closing_questions = unique_top(
        [
            "오늘 설명한 서비스가 출시되면 가장 먼저 취할 행동은 무엇인가요?",
            "이 서비스를 쓰지 않기로 한다면 가장 큰 이유는 무엇인가요?",
            "한 문장만 바꿔야 한다면 어떤 설명/기능/가격 조건을 바꾸면 좋을까요?",
            *(validation_questions[:2]),
        ],
        limit=5,
    )

    note_taking_rubric = [
        "Problem recency: 최근 실제 사례/빈도/손실이 구체적인가",
        "Current alternative: 대체 행동과 계속 쓰는 이유가 명확한가",
        "Value moment: 컨셉 없이도 반복되는 핵심 가치가 있는가",
        f"Top objection: {primary_objection_text}이 행동을 막는 실제 장벽인가",
        "Next action: 가입/문의/공유/결제 등 관찰 가능한 행동으로 이어지는가",
    ]
    if synthetic_listen_fors:
        note_taking_rubric.append("Synthetic watchlist: " + "; ".join(synthetic_listen_fors[:4]))

    success_signals = _listify(validation_plan.get("success_criteria"))[:4] or [
        "응답자의 절반 이상이 최근 문제 경험과 현재 대안을 구체적으로 설명",
        "핵심 메시지를 듣고 관찰 가능한 다음 행동을 선택",
        f"{primary_objection_text}을 낮추는 조건이 반복적으로 확인",
    ]

    return {
        "objective": validation_plan.get("objective") or f"{product_name}의 문제-해결 적합도와 다음 행동 의향 확인",
        "session_length": "25-30 minutes",
        "participant_profile": recruiting_screener.get("target_profile") or target_market,
        "moderator_intro": "제품을 판매하려는 자리가 아니라 문제 경험과 대체 행동을 배우는 인터뷰라고 먼저 설명하고, 정답은 없다고 안내한다.",
        "concept_read": concept_read,
        "warmup_questions": warmup_questions,
        "concept_reaction_tasks": concept_reaction_tasks,
        "objection_probes": objection_probes,
        "pricing_probe": price_probe,
        "closing_questions": closing_questions,
        "note_taking_rubric": note_taking_rubric,
        "success_signals": success_signals,
        "caution": "Synthetic persona signal에서 만든 가이드이므로 실제 인터뷰에서는 유도 질문을 피하고, 응답자의 최근 경험을 우선 기록한다.",
    }


def build_validation_survey(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    validation_plan: dict[str, Any] | None = None,
    recruiting_screener: dict[str, Any] | None = None,
    message_angle_tests: list[dict[str, Any]] | None = None,
    pricing_sensitivity: dict[str, Any] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    evidence_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a short quantitative survey instrument from synthetic signals.

    Interviews explain why a signal exists; founders often also need a fast
    Typeform/Google Forms survey to size the signal across a slightly larger
    audience. This pack turns the same deterministic report inputs into bounded
    survey blocks, randomized message arms, and pass/fail metrics without an
    extra model call.
    """

    normalized = validate_brief(brief)
    validation_plan = validation_plan or {}
    recruiting_screener = recruiting_screener or {}
    message_angle_tests = message_angle_tests or []
    pricing_sensitivity = pricing_sensitivity or {}
    switching_analysis = switching_analysis or {}
    evidence_quality = evidence_quality or {}

    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or recruiting_screener.get("target_profile") or "초기 문제 경험자"
    current_alternative = str(
        switching_analysis.get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 쓰는 대체 행동"
    )
    description = normalized.get("description") or product_name
    pricing_options = _listify(normalized.get("pricing"))[:4]
    evidence_level = str(evidence_quality.get("level") or "exploratory")
    if evidence_level == "decision_support":
        recommended_completes = "n=80-120 qualified respondents"
    elif evidence_level == "directional":
        recommended_completes = "n=50-80 qualified respondents"
    else:
        recommended_completes = "n=30-50 qualified respondents"

    top_drivers = unique_top(
        [driver for reaction in reactions for driver in _listify(reaction.get("positive_drivers"))],
        limit=4,
    )
    top_risks = unique_top(
        [risk for reaction in reactions for risk in _listify(reaction.get("top_risks"))],
        limit=5,
    )
    top_next_question = _first_text(
        *[
            str(reaction.get("next_validation_question"))
            for reaction in reactions
            if str(reaction.get("next_validation_question") or "").strip()
        ],
        "이 제품을 실제로 써보거나 결제하려면 무엇이 먼저 확인되어야 하나요?",
    )
    message_arms: list[dict[str, Any]] = []
    for index, angle in enumerate(message_angle_tests[:3], start=1):
        if not isinstance(angle, dict):
            continue
        headline = _first_text(angle.get("headline"), angle.get("core_message"))
        if not headline:
            continue
        message_arms.append(
            {
                "arm": f"message_{index}",
                "headline": headline,
                "subcopy": _first_text(angle.get("subcopy"), angle.get("evidence_to_show"), description),
                "source_signal": _first_text(angle.get("label"), "message_angle"),
            }
        )
    if not message_arms:
        message_arms = [
            {
                "arm": "control",
                "headline": f"{product_name}: {top_drivers[0] if top_drivers else '핵심 문제를 더 쉽게 해결'}",
                "subcopy": description,
                "source_signal": "product_brief",
            }
        ]

    price_options = pricing_sensitivity.get("options") if isinstance(pricing_sensitivity.get("options"), list) else []
    price_choices = [str(option.get("option")) for option in price_options[:5] if isinstance(option, dict) and option.get("option")]
    if not price_choices:
        price_choices = [str(item) for item in pricing_options]
    if not price_choices:
        price_choices = ["무료 체험 후 유료", "월 구독", "일회성 결제", "아직 모르겠음"]

    question_blocks = [
        {
            "block": "qualification",
            "purpose": "문제 경험자만 분석 cohort에 포함",
            "questions": [
                {
                    "id": "q_problem_recent",
                    "type": "single_choice",
                    "question": f"최근 1개월 안에 {product_name}이 해결하려는 문제를 겪었나요?",
                    "options": ["예, 여러 번", "예, 1번", "아니오", "잘 모르겠음"],
                    "qualify_if": ["예, 여러 번", "예, 1번"],
                },
                {
                    "id": "q_current_alternative",
                    "type": "short_text",
                    "question": f"현재는 이 문제를 어떻게 해결하나요? 예: {current_alternative}",
                    "qualify_if": "구체적인 도구/행동/사람/비용 중 하나 이상 언급",
                },
            ],
        },
        {
            "block": "concept_reaction",
            "purpose": "컨셉 이해도와 need fit 측정",
            "questions": [
                {
                    "id": "q_message_arm",
                    "type": "randomized_concept_card",
                    "question": "아래 메시지 중 무작위로 1개를 보여준 뒤 답변을 받습니다.",
                    "arms": message_arms,
                },
                {
                    "id": "q_understanding",
                    "type": "open_text",
                    "question": "방금 본 설명은 무엇을 해주는 서비스라고 이해했나요?",
                    "metric": "unaided understanding clarity",
                },
                {
                    "id": "q_need_fit",
                    "type": "likert_1_5",
                    "question": "이 서비스가 본인의 최근 문제를 해결하는 데 얼마나 맞다고 느끼나요?",
                    "anchors": ["전혀 맞지 않음", "매우 잘 맞음"],
                },
            ],
        },
        {
            "block": "adoption_and_objections",
            "purpose": "다음 행동 의향과 전환 장벽 분리",
            "questions": [
                {
                    "id": "q_next_action",
                    "type": "single_choice",
                    "question": "오늘 이 서비스가 실제로 있다면 가장 먼저 할 행동은 무엇인가요?",
                    "options": ["바로 가입/신청", "가격 확인", "데모/샘플 보기", "다른 사람에게 물어봄", "아무 행동 안 함"],
                    "primary_metric": "observable next-action intent",
                },
                {
                    "id": "q_top_objection",
                    "type": "rank_order",
                    "question": "사용/결제를 망설이게 하는 이유를 큰 순서대로 골라주세요.",
                    "options": unique_top([*top_risks, "가격 부담", "신뢰/정확도 우려", "지금 방식으로 충분함"], limit=7),
                },
                {
                    "id": "q_followup_condition",
                    "type": "open_text",
                    "question": top_next_question,
                    "metric": "conversion condition themes",
                },
            ],
        },
        {
            "block": "pricing",
            "purpose": "가격 옵션별 friction 확인",
            "questions": [
                {
                    "id": "q_price_choice",
                    "type": "single_choice",
                    "question": "아래 조건 중 실제로 가장 검토해볼 만한 가격/이용 방식은 무엇인가요?",
                    "options": price_choices,
                },
                {
                    "id": "q_price_probe",
                    "type": "open_text",
                    "question": pricing_sensitivity.get("recommended_price_probe")
                    or "결제 전 반드시 확인해야 할 기준은 무엇인가요?",
                    "metric": "willingness-to-pay evidence",
                },
            ],
        },
    ]

    primary_metrics = [
        "qualified respondent rate",
        "unaided understanding clarity",
        "need-fit mean (1-5)",
        "observable next-action intent share",
        "top objection frequency",
        "price option preference",
    ]
    pass_signals = [
        "qualified 응답자의 60% 이상이 최근 문제와 현재 대안을 구체적으로 설명",
        "need-fit 평균 3.8/5 이상 또는 상위 message arm이 control 대비 +15%p",
        "qualified 응답자의 30% 이상이 가입/가격확인/데모보기 중 하나를 선택",
    ]
    if validation_plan.get("success_criteria"):
        pass_signals = unique_top([*validation_plan.get("success_criteria", []), *pass_signals], limit=5)

    return {
        "objective": f"{product_name}의 문제 경험, 메시지 반응, 다음 행동 의향을 짧은 정량 설문으로 확인",
        "estimated_length": "5-7 minutes",
        "target_profile": target_market,
        "recommended_completes": recommended_completes,
        "intro_text": "제품 홍보가 아니라 문제 경험과 컨셉 반응을 배우기 위한 짧은 설문입니다. 정답은 없고, 최근 실제 경험 기준으로 답해 주세요.",
        "question_blocks": question_blocks,
        "randomization_plan": {
            "unit": "respondent",
            "arms": [arm["arm"] for arm in message_arms],
            "instruction": "컨셉 카드/message arm은 응답자별 1개만 무작위 노출하고 arm id를 저장합니다.",
        },
        "primary_metrics": primary_metrics,
        "analysis_plan": [
            "qualification 통과자만 primary analysis에 포함하고 탈락자는 problem awareness 참고로 분리",
            "message arm별 need-fit, next-action intent, top objection을 비교",
            "현재 대체 행동 유형별로 adoption intent와 가격 선택을 교차분석",
            "open text는 repeated objection/conversion condition theme으로 코딩",
        ],
        "pass_signals": pass_signals,
        "caution": "Synthetic signal에서 만든 설문 초안이므로 실제 배포 전 유도 문항과 개인정보 수집 항목을 점검해야 합니다.",
    }


def build_field_validation_tracker(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    validation_survey: dict[str, Any] | None = None,
    recruiting_screener: dict[str, Any] | None = None,
    decision_board: dict[str, Any] | None = None,
    evidence_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a sheet-ready tracker for comparing field evidence to synthetic signals.

    Upkinsey reports already propose interviews and surveys. This tracker closes
    the loop: it names the synthetic baseline, the exact columns to capture from
    real respondents, and the calibration rules that should change the next run
    or decision memo when reality disagrees with the synthetic panel.
    """

    normalized = validate_brief(brief)
    validation_survey = validation_survey or {}
    recruiting_screener = recruiting_screener or {}
    decision_board = decision_board or {}
    evidence_quality = evidence_quality or {}

    total = max(1, len(reactions))
    adoption = round(sum(_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in reactions) / total)
    need_fit = round(sum(_as_int(reaction.get("need_fit_score"), default=0) for reaction in reactions) / total)
    positive = sum(1 for reaction in reactions if _as_int(reaction.get("adoption_likelihood"), default=0) >= 65)
    next_action_share = round(positive / total * 100)
    top_objections = unique_top_risks(
        [risk for reaction in reactions for risk in _listify(reaction.get("top_risks"))],
        limit=5,
    )
    top_drivers = unique_top(
        [driver for reaction in reactions for driver in _listify(reaction.get("positive_drivers"))],
        limit=5,
    )
    survey_blocks = validation_survey.get("question_blocks") if isinstance(validation_survey.get("question_blocks"), list) else []
    survey_question_ids = unique_top(
        [
            str(question.get("id"))
            for block in survey_blocks
            if isinstance(block, dict)
            for question in (block.get("questions") if isinstance(block.get("questions"), list) else [])
            if isinstance(question, dict) and question.get("id")
        ],
        limit=12,
    )
    randomization_plan = validation_survey.get("randomization_plan") if isinstance(validation_survey.get("randomization_plan"), dict) else {}
    message_arms = _listify(randomization_plan.get("arms"))
    recommended_sample = _first_text(
        validation_survey.get("recommended_completes"),
        recruiting_screener.get("recommended_completes"),
        "n=30-50 qualified respondents",
    )
    decision = _first_text(decision_board.get("decision"), "Refine")
    evidence_level = _first_text(evidence_quality.get("level"), "exploratory")

    field_columns = [
        {
            "column": "respondent_id",
            "type": "text",
            "description": "익명 응답자 ID. 이름/연락처는 별도 동의 시스템에 분리 보관",
            "source": "field",
        },
        {
            "column": "qualified",
            "type": "boolean",
            "description": "최근 문제 경험과 현재 대체 행동을 구체적으로 설명했는지",
            "source": "screener",
        },
        {
            "column": "current_alternative",
            "type": "text",
            "description": "지금 쓰는 도구/습관/사람/비용",
            "source": "q_current_alternative",
        },
        {
            "column": "message_arm",
            "type": "category",
            "description": "노출한 메시지 arm id",
            "source": "q_message_arm",
        },
        {
            "column": "need_fit_1_5",
            "type": "number",
            "description": "정량 설문 또는 인터뷰 후 1-5점 need-fit 평가",
            "source": "q_need_fit",
        },
        {
            "column": "next_action",
            "type": "category",
            "description": "가입/가격확인/데모보기/공유/아무 행동 안 함 등 관찰 가능한 다음 행동",
            "source": "q_next_action",
        },
        {
            "column": "top_objection",
            "type": "category_or_text",
            "description": "가장 큰 전환 장벽",
            "source": "q_top_objection",
        },
        {
            "column": "price_choice",
            "type": "category",
            "description": "가장 검토해볼 만한 가격/이용 방식",
            "source": "q_price_choice",
        },
        {
            "column": "evidence_quote",
            "type": "text",
            "description": "판단 근거가 되는 실제 응답자의 짧은 발화/오픈텍스트",
            "source": "interview_or_open_text",
        },
        {
            "column": "researcher_decision",
            "type": "category",
            "description": "Go / Refine / Segment Pivot / Hold 중 field evidence 기준 결정",
            "source": "decision_memo",
        },
    ]

    comparison_metrics = [
        {
            "metric": "qualified respondent rate",
            "synthetic_baseline": f"target profile assumed: {normalized.get('target_market') or 'n/a'}",
            "field_measure": "qualified=true 비율",
            "alert_if": "50% 미만이면 타깃 정의나 모집 채널이 synthetic panel과 다름",
            "action_if_mismatch": "persona_filters/target_market을 실제 qualified 응답자 언어로 좁혀 재실행",
        },
        {
            "metric": "need-fit mean",
            "synthetic_baseline": f"{need_fit}/100 synthetic need fit",
            "field_measure": "q_need_fit 평균을 20배 해 100점 기준으로 비교",
            "alert_if": "field need-fit이 synthetic보다 15점 이상 낮음",
            "action_if_mismatch": "문제 빈도·사용 맥락을 brief에 보강하고 value assumption stress test부터 실행",
        },
        {
            "metric": "observable next-action intent",
            "synthetic_baseline": f"{next_action_share}% synthetic positive-intent personas",
            "field_measure": "가입/가격확인/데모보기 선택 비율",
            "alert_if": "field next-action이 synthetic baseline보다 20%p 이상 낮음",
            "action_if_mismatch": "호감 표현 대신 실제 행동 장벽을 objection/message brief에 반영",
        },
        {
            "metric": "top objection match",
            "synthetic_baseline": ", ".join(top_objections[:3]) or "n/a",
            "field_measure": "가장 많이 반복된 top_objection",
            "alert_if": "field 1순위 objection이 synthetic top 3 밖에서 반복됨",
            "action_if_mismatch": "새 objection을 FAQ/메시지/가격 실험 backlog의 P1로 승격",
        },
        {
            "metric": "message arm lift",
            "synthetic_baseline": ", ".join(str(arm) for arm in message_arms) or "control only",
            "field_measure": "message_arm별 need_fit_1_5와 next_action 비율",
            "alert_if": "arm 간 차이가 없거나 winner가 synthetic driver와 다름",
            "action_if_mismatch": "winning field language로 next-run brief variant를 업데이트",
        },
    ]

    calibration_rules = [
        "field next-action intent가 synthetic baseline보다 20%p 이상 낮으면 decision을 Go에서 Refine/Segment Pivot으로 낮춘다.",
        "qualified respondent rate가 낮으면 제품 자체보다 target/recruiting 정의 문제로 보고 panel filter부터 수정한다.",
        "field top objection이 synthetic top 3와 다르면 다음 Solar run에는 새 objection을 hypothesis/current_alternatives에 명시한다.",
        "field need-fit은 높고 next-action만 낮으면 가격·신뢰·사용성 중 barrier를 낮추는 message/pricing test로 이동한다.",
        "field quote는 synthetic persona quote처럼 쓰지 말고 실제 응답자 동의 범위 안에서 별도 근거로 보관한다.",
    ]

    return {
        "objective": f"{normalized.get('product_name') or '제품'} synthetic pre-research를 실제 인터뷰/설문 결과와 비교해 다음 decision을 보정",
        "recommended_field_sample": recommended_sample,
        "baseline_decision": decision,
        "evidence_level": evidence_level,
        "synthetic_baseline": {
            "persona_count": len(reactions),
            "adoption_score": adoption,
            "need_fit_score": need_fit,
            "positive_intent_share": next_action_share,
            "price_risk": _price_risk_from_reactions(reactions),
            "top_drivers": top_drivers,
            "top_objections": top_objections,
            "survey_question_ids": survey_question_ids,
        },
        "field_data_columns": field_columns,
        "comparison_metrics": comparison_metrics,
        "calibration_rules": calibration_rules,
        "decision_memo_template": [
            "1) qualified 응답자 수와 제외 사유",
            "2) synthetic baseline 대비 field metric 차이",
            "3) 반복된 실제 objection/driver quote",
            "4) Go / Refine / Segment Pivot / Hold 결정과 다음 Upkinsey rerun brief 변경점",
        ],
        "disclaimer": "Synthetic baseline is a comparison scaffold, not ground truth; field evidence should override synthetic signals.",
    }

def build_experiment_backlog(
    brief: dict[str, Any],
    *,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    decision_board: dict[str, Any] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    validation_plan: dict[str, Any] | None = None,
    evidence_quality: dict[str, Any] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Create a small prioritized experiment backlog from simulation signals.

    The validation plan says who to talk to and what to ask. This backlog turns
    the same signal into concrete founder actions with pass/fail thresholds, so
    a synthetic run ends with an executable learning sprint instead of a report.
    """

    normalized = validate_brief(brief)
    segments = segments or []
    objections = objections or []
    decision = str((decision_board or {}).get("decision") or "Refine")
    target_market = normalized.get("target_market") or "초기 문제 경험자"
    product_name = normalized.get("product_name") or "제품"
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    current_alternative = str(
        (switching_analysis or {}).get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 대체 행동"
    )
    primary_driver = str(
        top_segment.get("primary_driver")
        or (validation_plan or {}).get("prototype_or_message_to_test")
        or "핵심 가치 제안"
    )
    primary_objection = str(top_objection.get("category") or top_objection.get("objection") or "전환 장벽")
    objection_fix = str(top_objection.get("suggested_fix") or "전환 장벽을 낮추는 메시지")
    evidence_level = str((evidence_quality or {}).get("level") or "exploratory")
    recruiting_focus = str((validation_plan or {}).get("recruiting_focus") or f"{top_segment.get('segment') or target_market}: {target_market}")

    if decision == "Go":
        first_priority = "P0"
        first_metric = "랜딩 방문자 중 30% 이상이 데모/대기자 등록 클릭"
    elif decision == "Segment Pivot":
        first_priority = "P0"
        first_metric = "반응 강한 세그먼트 인터뷰 5명 중 3명 이상이 다음 행동 수락"
    elif decision == "Hold":
        first_priority = "P1"
        first_metric = "문제 경험자 5명 중 3명 이상이 현재 대안의 뚜렷한 불만을 설명"
    else:
        first_priority = "P1" if evidence_level == "exploratory" else "P0"
        first_metric = "메시지 노출자 중 25% 이상이 자세히 보기/인터뷰 신청"

    backlog = [
        {
            "priority": first_priority,
            "experiment": "핵심 가치 메시지 smoke test",
            "hypothesis": f"{target_market}은(는) '{primary_driver}' 메시지를 보면 {product_name}의 다음 행동을 선택할 것이다.",
            "audience": recruiting_focus,
            "setup": "랜딩/노션/카카오 설문 첫 화면에 현재 대안 대비 핵심 효용 1개와 CTA 1개만 제시",
            "metric": "CTA click/interview signup rate",
            "pass_threshold": first_metric,
            "source_signal": f"decision={decision}; evidence={evidence_level}; driver={primary_driver}",
        },
        {
            "priority": "P0" if decision == "Refine" else "P1",
            "experiment": "1순위 objection A/B test",
            "hypothesis": f"'{objection_fix}'을 명시하면 {primary_objection} 우려가 줄어든다.",
            "audience": target_market,
            "setup": "동일한 제품 설명에 objection 해소 문구가 있는 버전과 없는 버전을 1:1로 비교",
            "metric": "objection severity drop / preferred version share",
            "pass_threshold": "해소 문구 버전 선호 60% 이상 또는 objection 점수 20% 이상 감소",
            "source_signal": f"top_objection={primary_objection}",
        },
        {
            "priority": "P0" if decision == "Hold" else "P1",
            "experiment": "현재 대체 행동 인터뷰",
            "hypothesis": f"사용자가 '{current_alternative}'를 유지하는 이유를 알면 더 강한 전환 트리거를 찾을 수 있다.",
            "audience": target_market,
            "setup": "제품 설명 전에 최근 문제 상황, 현재 해결법, 불만, 비용/시간 손실을 먼저 질문",
            "metric": "recent problem frequency / current workaround pain",
            "pass_threshold": "5명 중 3명 이상이 최근 1개월 내 문제와 반복 불만을 구체적으로 설명",
            "source_signal": f"current_alternative={current_alternative}",
        },
    ]

    return backlog[: max(1, min(limit, len(backlog)))]


def build_next_run_brief_variants(
    brief: dict[str, Any],
    *,
    decision_board: dict[str, Any] | None = None,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    pricing_sensitivity: dict[str, Any] | None = None,
    message_angle_tests: list[dict[str, Any]] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Generate bounded follow-up brief variants for the next simulation run.

    Reports already explain what happened; this section turns the highest-signal
    objection, segment, and switching trigger into copy-pasteable rerun briefs.
    It stays deterministic and local so teams can iterate without spending an
    extra model call just to prepare the next Solar batch.
    """

    normalized = validate_brief(brief)
    segments = segments or []
    objections = objections or []
    switching_analysis = switching_analysis or {}
    pricing_sensitivity = pricing_sensitivity or {}
    message_angle_tests = message_angle_tests or []

    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or "초기 문제 경험자"
    current_alternative = str(
        switching_analysis.get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 대체 행동"
    )
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    top_angle = message_angle_tests[0] if message_angle_tests else {}
    segment_label = str(top_segment.get("segment") or target_market)
    primary_driver = str(top_segment.get("primary_driver") or top_angle.get("core_message") or "핵심 효용")
    objection_label = str(top_objection.get("category") or top_objection.get("objection") or "전환 장벽")
    objection_fix = str(top_objection.get("suggested_fix") or "전환 장벽을 낮추는 근거와 보완책")
    headline = str(top_angle.get("headline") or f"{product_name}이(가) {primary_driver}을(를) 더 쉽게 만듭니다")
    recommended_price_probe = str(pricing_sensitivity.get("recommended_price_probe") or "가격 조건별 실제 결제 의향을 확인")

    def base_brief(*, research_type: str, description_suffix: str, features: list[str], hypothesis: str, target: str | None = None) -> dict[str, Any]:
        variant_brief = {
            "product_name": product_name,
            "research_type": research_type,
            "description": _shorten(
                f"{normalized.get('description') or product_name}. {description_suffix}",
                limit=900,
            ),
            "features": unique_top([*normalized.get("features", []), *features], limit=6),
            "pricing": normalized.get("pricing", []),
            "target_market": target or target_market,
            "current_alternatives": current_alternative,
            "hypothesis": hypothesis,
            "sample_size": normalized.get("sample_size"),
            "seed": normalized.get("seed"),
        }
        if normalized.get("persona_filters"):
            variant_brief["persona_filters"] = normalized["persona_filters"]
        return variant_brief

    variants = [
        {
            "variant": "objection_reducer",
            "title": f"{objection_label} 해소안 재실험",
            "why": f"상위 objection인 '{objection_label}'이 실제 adoption을 막는지 분리해서 확인합니다.",
            "changes": unique_top([objection_fix, f"{objection_label} 우려를 직접 낮추는 FAQ/증거를 첫 화면에 배치"], limit=3),
            "brief": base_brief(
                research_type="Objection mining",
                description_suffix=f"이번 버전은 {objection_label} 우려를 낮추기 위해 {objection_fix}을(를) 명확히 제시합니다.",
                features=[objection_fix, f"{objection_label} FAQ"],
                hypothesis=f"{objection_fix}을(를) 먼저 보여주면 {target_market}의 {objection_label} 우려가 낮아지고 사용/결제 의향이 높아질 것이다.",
            ),
            "validation_focus": objection_label,
            "pass_signal": "동일 panel에서 objection 언급 빈도 감소 또는 adoption +10p 이상 상승",
        },
        {
            "variant": "beachhead_segment",
            "title": f"{segment_label} 집중 타깃 재실험",
            "why": "평균 점수보다 반응 강한 세그먼트를 좁혀 beachhead가 실제로 선명한지 확인합니다.",
            "changes": unique_top([f"타깃을 {segment_label}(으)로 좁힘", f"핵심 메시지를 '{primary_driver}' 중심으로 재작성"], limit=3),
            "brief": base_brief(
                research_type="Segment discovery",
                description_suffix=f"이번 버전은 {segment_label}에게 {primary_driver} 효용을 가장 먼저 보여주는 좁은 beachhead 가설입니다.",
                features=[primary_driver, "세그먼트별 온보딩/사용 사례"],
                target=f"{target_market} 중 {segment_label}",
                hypothesis=f"{segment_label}은(는) {primary_driver} 효용이 명확하면 전체 평균보다 높은 다음 행동 의향을 보일 것이다.",
            ),
            "validation_focus": "segment concentration",
            "pass_signal": "beachhead cohort adoption이 전체 평균보다 +15p 이상 높거나 high-intent persona가 절반 이상",
        },
        {
            "variant": "switching_trigger",
            "title": "현재 대안 대비 전환 트리거 재실험",
            "why": f"사용자가 이미 '{current_alternative}'로 해결하는 상황에서 바꿀 이유가 충분한지 검증합니다.",
            "changes": unique_top([headline, f"{current_alternative} 대비 절약 시간·안심 근거·실패 감소를 비교", recommended_price_probe], limit=4),
            "brief": base_brief(
                research_type="Message test",
                description_suffix=f"첫 화면에서 '{current_alternative}' 대비 어떤 순간에 더 빠르거나 안전한지 비교합니다. 핵심 카피: {headline}",
                features=["현재 대안 대비 비교", "전환 순간별 메시지", "가격/체험 조건 명시"],
                hypothesis=f"{current_alternative} 대비 전환 트리거를 명확히 보여주면 관망형 persona의 사용 의향이 상승할 것이다.",
            ),
            "validation_focus": "switching trigger",
            "pass_signal": "관망/조건부 cohort에서 switching trigger 언급과 다음 행동 선택이 반복 확인",
        },
    ]

    decision = str((decision_board or {}).get("decision") or "Refine")
    if decision == "Segment Pivot":
        order = {"beachhead_segment": 0, "objection_reducer": 1, "switching_trigger": 2}
    elif decision == "Go":
        order = {"switching_trigger": 0, "beachhead_segment": 1, "objection_reducer": 2}
    else:
        order = {"objection_reducer": 0, "switching_trigger": 1, "beachhead_segment": 2}

    ranked = sorted(variants, key=lambda item: order.get(str(item.get("variant")), 99))
    return ranked[: max(1, min(limit, len(ranked)))]


def build_research_sprint(
    brief: dict[str, Any],
    *,
    decision_board: dict[str, Any] | None = None,
    validation_plan: dict[str, Any] | None = None,
    experiment_backlog: list[dict[str, Any]] | None = None,
    assumption_stress_test: dict[str, Any] | None = None,
    switching_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Package synthetic results into a one-week founder research sprint.

    Upkinsey's report already names risks, questions, and experiments. This
    layer makes the immediate operating plan explicit so a founder can leave the
    simulation with a bounded next week rather than another unordered insight
    list. It stays deterministic and local; no extra Solar call is needed.
    """

    normalized = validate_brief(brief)
    decision = str((decision_board or {}).get("decision") or "Refine")
    confidence = str((decision_board or {}).get("confidence") or "low")
    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or "초기 문제 경험자"
    current_alternative = str(
        (switching_analysis or {}).get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 대체 행동"
    )
    recommended_sample = str((validation_plan or {}).get("recommended_sample") or "3-5명 실제 사용자 인터뷰")
    recruiting_focus = str((validation_plan or {}).get("recruiting_focus") or target_market)
    top_experiment = (experiment_backlog or [{}])[0]
    top_assumption = ((assumption_stress_test or {}).get("assumptions") or [{}])[0]
    riskiest_area = str(top_assumption.get("area") or "value")
    falsification_test = str(
        top_assumption.get("falsification_test")
        or (assumption_stress_test or {}).get("recommended_next_step")
        or "상위 가설을 실제 사용자 행동으로 검증"
    )
    smoke_test = str(top_experiment.get("experiment") or "핵심 가치 메시지 smoke test")
    pass_threshold = str(top_experiment.get("pass_threshold") or "다음 행동 선택률과 반복 objection을 기록")

    if decision == "Go":
        objective = "긍정 synthetic 신호가 실제 사용자 행동으로 재현되는지 확인"
        decision_gate = "실제 사용자 5명 중 3명 이상이 다음 행동을 선택하면 build/launch 준비로 이동"
    elif decision == "Segment Pivot":
        objective = "전체 평균보다 강한 beachhead segment가 실제로 존재하는지 확인"
        decision_gate = "반응 강한 세그먼트에서만 문제 빈도와 전환 계기가 반복되면 타깃을 좁혀 재실험"
    elif decision == "Hold":
        objective = "현재 제품안보다 먼저 문제 빈도와 대체 행동의 불만을 재확인"
        decision_gate = "최근 문제 경험과 현재 대안 불만이 반복되지 않으면 제품 구현을 보류"
    else:
        objective = "가장 큰 objection을 낮춘 메시지/패키징이 전환 의향을 바꾸는지 확인"
        decision_gate = "objection 해소 버전이 명확히 선호되면 같은 panel/brief로 재시뮬레이션"

    day_plan = [
        {
            "day": 1,
            "focus": "Sprint setup",
            "tasks": unique_top(
                [
                    f"{product_name} brief를 한 문장 value proposition과 현재 대안('{current_alternative}') 비교로 정리",
                    f"모집 대상 정의: {recruiting_focus}",
                    "인터뷰/랜딩에서 측정할 primary metric 1개와 stop condition 1개 확정",
                ],
                limit=3,
            ),
            "output": "validated research script + recruiting list",
        },
        {
            "day": 2,
            "focus": "Recruit and stimulus",
            "tasks": unique_top(
                [
                    f"{recommended_sample} 모집 또는 후보 리스트 작성",
                    f"{smoke_test}용 headline/CTA 또는 1분 데모 초안 제작",
                    f"가장 위험한 가정({riskiest_area})을 깨는 질문을 첫 3문항 안에 배치",
                ],
                limit=3,
            ),
            "output": "stimulus v1 + interview slots",
        },
        {
            "day": 3,
            "focus": "Run first learnings",
            "tasks": unique_top(
                [
                    "첫 2-3명 인터뷰 또는 소규모 랜딩 노출 실행",
                    falsification_test,
                    "말로만 긍정한 응답과 실제 다음 행동을 분리해 기록",
                ],
                limit=3,
            ),
            "output": "raw notes + action counts",
        },
        {
            "day": 4,
            "focus": "Adjust and complete",
            "tasks": unique_top(
                [
                    "반복 objection 1개를 낮춘 문구/가격/데모 버전으로 수정",
                    "남은 인터뷰/노출을 같은 기준으로 완료",
                    "현재 대안을 유지하는 이유를 제품 설명보다 먼저 기록",
                ],
                limit=3,
            ),
            "output": "completed evidence table",
        },
        {
            "day": 5,
            "focus": "Decision review",
            "tasks": unique_top(
                [
                    f"pass threshold 확인: {pass_threshold}",
                    "Go / Refine / Segment Pivot / Hold 중 하나로 판정",
                    "업데이트된 brief와 learned objections를 다음 Upkinsey run 입력으로 정리",
                ],
                limit=3,
            ),
            "output": "next-run brief + decision memo",
        },
    ]

    artifacts = unique_top(
        [
            "one-page product brief",
            "interview screener + script",
            "message/landing stimulus",
            "evidence table with quotes, actions, objections",
            "next Upkinsey simulation brief",
        ],
        limit=5,
    )
    stop_conditions = unique_top(
        [
            "응답자가 최근 문제 경험을 구체적으로 말하지 못함",
            "현재 대안 대비 전환 계기가 반복되지 않음",
            "가격/신뢰/사용성 objection이 해소 문구 후에도 동일하게 유지됨",
        ],
        limit=3,
    )

    return {
        "name": "5-day validation sprint",
        "decision_context": decision,
        "confidence": confidence,
        "objective": objective,
        "recommended_sample": recommended_sample,
        "recruiting_focus": recruiting_focus,
        "primary_experiment": smoke_test,
        "riskiest_assumption_area": riskiest_area,
        "day_plan": day_plan,
        "must_have_artifacts": artifacts,
        "stop_conditions": stop_conditions,
        "decision_gate": decision_gate,
        "next_review_checkpoint": "Day 5 decision memo + updated Upkinsey rerun brief",
    }


def build_message_angle_tests(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    switching_analysis: dict[str, Any] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Generate concrete landing-page message angles from simulation signals.

    Founders often need the next artifact to be copy, not another abstract
    insight. These cards translate repeated drivers, objections, and current
    alternatives into bounded A/B message tests that can be pasted into a
    landing page, survey, or interview stimulus.
    """

    normalized = validate_brief(brief)
    product_name = normalized.get("product_name") or "제품"
    target_market = normalized.get("target_market") or "초기 문제 경험자"
    segments = segments or []
    objections = objections or []

    drivers = [driver for reaction in reactions for driver in reaction.get("positive_drivers", [])]
    risks = [risk for reaction in reactions for risk in reaction.get("top_risks", [])]
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    primary_driver = str(top_segment.get("primary_driver") or _first_counter_item(drivers, "핵심 문제 해결"))
    primary_risk = str(top_objection.get("objection") or _first_counter_item(risks, "전환 장벽 확인 필요"))
    objection_category = str(top_objection.get("category") or classify_objection(primary_risk))
    objection_fix = str(top_objection.get("suggested_fix") or OBJECTION_FIXES.get(objection_category, "우려를 낮추는 근거를 먼저 제시"))
    current_alternative = str(
        (switching_analysis or {}).get("current_alternatives")
        or normalized.get("current_alternatives")
        or "현재 방식"
    )
    switching_triggers = (switching_analysis or {}).get("switching_triggers") if isinstance(switching_analysis, dict) else []
    primary_trigger = str(_first_counter_item(_listify(switching_triggers), primary_driver))
    audience = str(
        top_segment.get("segment")
        or target_market
    )
    persona_examples = _listify(top_segment.get("persona_examples"))[:3]
    audience_detail = f"{audience}: {', '.join(persona_examples)}" if persona_examples else audience

    cards = [
        {
            "angle": "core-value",
            "label": "핵심 가치 메시지",
            "audience": audience_detail,
            "headline": f"{product_name}로 {primary_driver}을(를) 먼저 해결하세요",
            "subcopy": f"{target_market}이(가) 지금 쓰는 방식보다 더 빠르게 판단할 수 있도록 핵심 결과를 먼저 보여줍니다.",
            "evidence_to_show": "실제 화면/데모에서 사용 전후 시간·비용·불편 감소를 한 가지 수치로 제시",
            "objection_to_watch": primary_risk,
            "test_setup": "랜딩 첫 화면 headline으로 노출하고 CTA click 또는 인터뷰 신청률을 비교",
            "pass_signal": "노출자 중 25% 이상이 자세히 보기/인터뷰 신청 또는 메시지 선호 60% 이상",
            "source_signal": f"driver={primary_driver}",
        },
        {
            "angle": "objection-reversal",
            "label": "장벽 해소 메시지",
            "audience": target_market,
            "headline": f"{objection_category} 걱정 없이 시작하도록 설계했습니다",
            "subcopy": objection_fix,
            "evidence_to_show": "가격표, 데이터 처리 방식, 추천 근거, 실패 시 보상/책임 범위를 한 화면에 제시",
            "objection_to_watch": primary_risk,
            "test_setup": "기본 설명 vs objection 해소 설명을 1:1로 보여주고 선호/불안 점수를 비교",
            "pass_signal": "해소 메시지 버전의 선호가 60% 이상이거나 불안 점수가 20% 이상 감소",
            "source_signal": f"objection={objection_category}",
        },
        {
            "angle": "switching-trigger",
            "label": "전환 계기 메시지",
            "audience": target_market,
            "headline": f"'{current_alternative}'에서 막히는 순간, {product_name}로 전환하세요",
            "subcopy": f"전환 계기는 '{primary_trigger}'입니다. 기존 습관을 완전히 바꾸기보다 가장 답답한 순간 하나를 줄이는 메시지로 검증합니다.",
            "evidence_to_show": "현재 방식과 새 방식의 단계 수, 걸리는 시간, 놓치는 정보 차이를 나란히 비교",
            "objection_to_watch": primary_risk,
            "test_setup": "제품 소개 전에 최근 현재 대체 행동 경험을 떠올리게 한 뒤 메시지 반응을 측정",
            "pass_signal": "5명 중 3명 이상이 최근 1개월 내 같은 전환 순간을 말하고 다음 행동을 수락",
            "source_signal": f"current_alternative={current_alternative}",
        },
    ]

    return cards[: max(1, min(limit, len(cards)))]


def build_research_type_lens(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]] | None = None,
    objections: list[dict[str, Any]] | None = None,
    pricing_sensitivity: dict[str, Any] | None = None,
    message_angle_tests: list[dict[str, Any]] | None = None,
    intent_cohort_contrast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate the selected research type into a focused readout.

    Upkinsey can generate many useful report sections, but the selected Layer 2
    research type should tell the founder what to look at first. This lens is a
    deterministic routing card for the UI/report: it names the primary metric,
    the most relevant artifact, and the next action for that specific study.
    """

    normalized = validate_brief(brief)
    research_type = normalized.get("research_type") or "Concept test"
    key = research_type.strip().lower().replace("_", "-")
    segments = segments or []
    objections = objections or []
    pricing_sensitivity = pricing_sensitivity or {}
    message_angle_tests = message_angle_tests or []
    intent_cohort_contrast = intent_cohort_contrast or {}

    count = max(1, len(reactions))
    adoption = round(sum(_as_int(r.get("adoption_likelihood"), default=0) for r in reactions) / count) if reactions else 0
    need_fit = round(sum(_as_int(r.get("need_fit_score"), default=0) for r in reactions) / count) if reactions else 0
    top_segment = segments[0] if segments else {}
    top_objection = objections[0] if objections else {}
    top_message = message_angle_tests[0] if message_angle_tests else {}
    price_probe = str(pricing_sensitivity.get("recommended_price_probe") or "가격 옵션별 지불 의향 질문을 먼저 실행")
    top_segment_name = str(top_segment.get("segment") or "우선 검증 타깃")
    top_objection_text = str(top_objection.get("objection") or "가장 반복되는 구매/사용 장벽")

    def base(label: str, primary_metric: str, primary_output: str, interpretation: str, next_action: str) -> dict[str, Any]:
        return {
            "research_type": research_type,
            "lens": label,
            "primary_metric": primary_metric,
            "primary_output": primary_output,
            "interpretation": interpretation,
            "recommended_next_action": next_action,
            "supporting_sections": [],
            "disclaimer": "Research-type lens is a deterministic readout of synthetic persona signals, not a statistical conclusion.",
        }

    if key in {"pricing test", "pricing-test", "price test", "price-test"}:
        card = base(
            "Pricing sensitivity lens",
            "price_resistance + friction-adjusted adoption",
            "Pricing sensitivity lab",
            f"평균 채택 {adoption}/100에서 가격 저항이 {pricing_sensitivity.get('overall_price_risk', 'Medium')}로 나타납니다.",
            price_probe,
        )
        card["supporting_sections"] = ["pricing_sensitivity", "objections", "validation_plan"]
        card["watch_metric"] = str(pricing_sensitivity.get("top_price_objection") or top_objection_text)
        return card

    if key in {"objection mining", "objection-mining", "objection"}:
        card = base(
            "Objection mining lens",
            "top objection frequency + severity",
            "Objection cards",
            f"가장 먼저 해소할 장벽은 '{top_objection_text}'입니다.",
            str(top_objection.get("suggested_fix") or "상위 objection을 반박 문구/FAQ/데모 근거로 바꿔 A/B로 검증"),
        )
        card["supporting_sections"] = ["objections", "message_angle_tests", "assumption_stress_test"]
        card["watch_metric"] = str(top_objection.get("category") or "기타")
        return card

    if key in {"segment discovery", "segment-discovery", "segment"}:
        gap = intent_cohort_contrast.get("adoption_gap", 0) if isinstance(intent_cohort_contrast, dict) else 0
        card = base(
            "Segment discovery lens",
            "cohort adoption gap + beachhead clarity",
            "Segment recommendations",
            f"우선 확인할 집단은 '{top_segment_name}'이며 high/low intent gap은 {gap}p입니다.",
            str(top_segment.get("validation_action") or "상위 intent cohort와 low-intent cohort를 각각 5명씩 인터뷰해 beachhead를 좁히기"),
        )
        card["supporting_sections"] = ["segment_recommendations", "intent_cohort_contrast", "panel_profile"]
        card["watch_metric"] = str(gap)
        return card

    if key in {"message test", "message-test", "message"}:
        headline = str(top_message.get("headline") or "가치 메시지와 objection 해소 메시지를 나란히 비교")
        card = base(
            "Message test lens",
            "message preference + objection reduction",
            "Message angle tests",
            f"첫 테스트 문안은 '{headline}'입니다.",
            str(top_message.get("test_setup") or "핵심 가치/장벽 해소/전환 계기 메시지를 1:1:1로 비교"),
        )
        card["supporting_sections"] = ["message_angle_tests", "objections", "switching_analysis"]
        card["watch_metric"] = str(top_message.get("pass_signal") or "메시지 선호 60% 이상")
        return card

    card = base(
        "Concept test lens",
        "adoption likelihood + need fit",
        "Decision board",
        f"평균 채택 {adoption}/100, need fit {need_fit}/100로 컨셉 이해/매력도를 먼저 판단합니다.",
        "채택/need fit이 높은 persona에게는 사용 맥락을, 낮은 persona에게는 이해 실패 지점을 확인",
    )
    card["supporting_sections"] = ["decision_board", "segment_recommendations", "validation_plan"]
    card["watch_metric"] = f"adoption={adoption}, need_fit={need_fit}"
    return card

def build_evidence_quality(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    brief_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grade how much trust a founder should place in one synthetic run.

    Synthetic persona scores are useful for finding questions, but small or
    poorly targeted panels can look falsely precise. This guardrail turns panel
    size, targeting, brief quality, and signal dispersion into a plain-language
    evidence card so the product team knows whether to act, rerun, or recruit.
    """

    normalized = validate_brief(brief)
    count = len(reactions)
    brief_score = int((brief_quality or assess_brief_quality(normalized)).get("score", 0))
    score = 100
    warnings: list[str] = []
    strengths: list[str] = []
    recommended_actions: list[str] = []

    if count < 8:
        score -= 30
        warnings.append("persona 수가 8명 미만이라 평균 점수는 방향성 참고용입니다.")
        recommended_actions.append("최소 20명 이상 panel로 같은 brief를 재실행하세요.")
    elif count < 20:
        score -= 20
        warnings.append("persona panel이 작아 세그먼트별 차이를 안정적으로 보기 어렵습니다.")
        recommended_actions.append("20-50명 panel로 확장해 objection과 segment가 반복되는지 확인하세요.")
    elif count < 50:
        score -= 10
        warnings.append("초기 탐색에는 충분하지만 우선순위 결정을 위해서는 더 큰 panel이 좋습니다.")
        recommended_actions.append("상위 후보는 50명 이상 또는 target-filtered panel로 재검증하세요.")
    else:
        strengths.append("panel size가 synthetic pre-research 비교에 충분한 편입니다.")

    if brief_score < 60:
        score -= 30
        warnings.append("brief quality가 낮아 persona 반응이 제품보다 누락 가정에 좌우될 수 있습니다.")
        recommended_actions.append("제품 설명, 타깃, 가격, 현재 대체 행동을 보강한 뒤 재시뮬레이션하세요.")
    elif brief_score < 80:
        score -= 15
        warnings.append("brief를 더 구체화하면 다음 검증 질문의 품질이 올라갑니다.")
        recommended_actions.append("brief preflight의 recommended questions를 먼저 채우세요.")
    else:
        strengths.append("brief가 충분히 구체적이라 persona 판단 근거를 해석하기 좋습니다.")

    if normalized.get("persona_filters"):
        strengths.append("product-specific persona filter가 적용되어 target segment 해석이 더 선명합니다.")
    elif normalized.get("target_market"):
        score -= 10
        warnings.append("타깃 고객은 적었지만 panel filter가 없어 일반 panel 평균에 섞일 수 있습니다.")
        recommended_actions.append("persona_filters로 occupation/age/keywords를 지정해 beachhead segment를 따로 보세요.")

    adoptions = [_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in reactions]
    if adoptions:
        spread = max(adoptions) - min(adoptions)
        if spread >= 45:
            score -= 10
            warnings.append("persona 간 adoption 차이가 매우 커서 평균보다 segment별 해석이 중요합니다.")
            recommended_actions.append("고/저 adoption persona를 분리해 메시지와 objection을 비교하세요.")
        elif spread >= 25:
            warnings.append("persona 간 반응 차이가 있어 segment recommendation을 함께 봐야 합니다.")
        else:
            strengths.append("persona 간 adoption 분산이 낮아 반복 실행에서 안정성을 확인하기 좋습니다.")

    stance_counts: dict[str, int] = {}
    for reaction in reactions:
        stance = str(reaction.get("stance") or "unknown")
        stance_counts[stance] = stance_counts.get(stance, 0) + 1
    if count >= 4 and stance_counts:
        top_stance, top_count = sorted(stance_counts.items(), key=lambda item: (-item[1], item[0]))[0]
        if top_count / count >= 0.8:
            score -= 8
            warnings.append(f"stance가 '{top_stance}'에 몰려 있어 prompt calibration 또는 대비 panel 확인이 필요합니다.")
            recommended_actions.append("동일 brief를 다른 seed/panel로 반복해 stance concentration이 재현되는지 보세요.")

    score = max(0, min(100, score))
    if score >= 80:
        level = "decision_support"
        confidence = "medium"
    elif score >= 60:
        level = "directional"
        confidence = "low-medium"
    else:
        level = "exploratory"
        confidence = "low"

    if not recommended_actions:
        recommended_actions.append("상위 가설은 실제 사용자 인터뷰/랜딩 테스트로 검증하세요.")

    return {
        "score": score,
        "level": level,
        "confidence": confidence,
        "persona_count": count,
        "brief_quality_score": brief_score,
        "adoption_range": [min(adoptions), max(adoptions)] if adoptions else [0, 0],
        "stance_counts": stance_counts,
        "warnings": unique_top(warnings, limit=6),
        "strengths": unique_top(strengths, limit=5),
        "recommended_actions": unique_top(recommended_actions, limit=5),
    }


def _uncertainty_band(values: list[int], *, min_value: int = 0, max_value: int = 100) -> dict[str, Any]:
    """Return a conservative synthetic uncertainty band for persona scores.

    This is not a statistical confidence interval. It is a local guardrail that
    combines observed dispersion with a small-panel penalty so founders do not
    treat a single synthetic mean as a hard launch decision.
    """

    if not values:
        return {"mean": 0, "margin": 0, "range": [0, 0], "observed_range": [0, 0]}

    count = len(values)
    mean = round(sum(values) / count)
    if count == 1:
        dispersion = 0.0
    else:
        raw_mean = sum(values) / count
        dispersion = (sum((value - raw_mean) ** 2 for value in values) / count) ** 0.5
    small_panel_penalty = 12 if count < 8 else 8 if count < 20 else 5 if count < 50 else 3
    margin = min(25, max(5, round((dispersion / (count ** 0.5)) + small_panel_penalty)))
    return {
        "mean": mean,
        "margin": margin,
        "range": [max(min_value, mean - margin), min(max_value, mean + margin)],
        "observed_range": [min(values), max(values)],
    }


def build_decision_sensitivity(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    decision_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check whether the synthetic decision survives conservative score bands.

    Upkinsey's decision board is intentionally a next-validation suggestion, not
    a launch verdict. This guardrail makes borderline runs explicit by showing
    whether adoption/need-fit bands cross the Go or Hold thresholds.
    """

    normalized = validate_brief(brief)
    decision = _first_text((decision_board or {}).get("decision"), "Refine")
    adoption = [_as_int(reaction.get("adoption_likelihood"), default=0) for reaction in reactions]
    need_fit = [_as_int(reaction.get("need_fit_score"), default=0) for reaction in reactions]
    adoption_band = _uncertainty_band(adoption)
    need_fit_band = _uncertainty_band(need_fit)
    persona_count = len(reactions)

    go_thresholds = {"adoption": 70, "need_fit": 70}
    hold_thresholds = {"adoption": 45, "need_fit": 50}
    clears_go_floor = adoption_band["range"][0] >= go_thresholds["adoption"] and need_fit_band["range"][0] >= go_thresholds["need_fit"]
    below_hold_ceiling = adoption_band["range"][1] < hold_thresholds["adoption"] or need_fit_band["range"][1] < hold_thresholds["need_fit"]
    crosses_go = adoption_band["range"][0] < go_thresholds["adoption"] <= adoption_band["range"][1] or need_fit_band["range"][0] < go_thresholds["need_fit"] <= need_fit_band["range"][1]
    crosses_hold = adoption_band["range"][0] < hold_thresholds["adoption"] <= adoption_band["range"][1] or need_fit_band["range"][0] < hold_thresholds["need_fit"] <= need_fit_band["range"][1]

    if clears_go_floor:
        risk_level = "low"
        boundary = "clears_go_thresholds"
        interpretation = "Conservative bands stay above the Go thresholds, but this still requires real-user validation before launch decisions."
        recommended_action = "Run the recommended field validation plan and compare real next-action intent against the synthetic baseline."
    elif below_hold_ceiling:
        risk_level = "low"
        boundary = "below_hold_thresholds"
        interpretation = "Even the optimistic band remains below at least one Hold threshold, so the current concept should not be treated as launch-ready."
        recommended_action = "Use the assumption stress test or next-run brief variants before spending on larger validation."
    elif crosses_go or crosses_hold:
        risk_level = "high"
        boundary = "threshold_crossing"
        interpretation = "The decision is sensitive to synthetic score uncertainty because a conservative band crosses a Go/Hold threshold."
        recommended_action = "Repeat with a larger target-filtered panel or move directly to 5-8 real interviews before changing roadmap priority."
    else:
        risk_level = "medium"
        boundary = "middle_zone"
        interpretation = "Scores sit between hard Go/Hold thresholds; treat the board as a learning-priority signal rather than a verdict."
        recommended_action = "Prioritize the top objection and beachhead segment in the next validation sprint."

    if normalized.get("persona_filters"):
        panel_note = "target-filtered panel"
    elif normalized.get("target_market"):
        panel_note = "target market stated, but no explicit persona filter"
    else:
        panel_note = "general panel"

    return {
        "decision": decision,
        "risk_level": risk_level,
        "decision_boundary": boundary,
        "persona_count": persona_count,
        "panel_note": panel_note,
        "adoption_band": adoption_band,
        "need_fit_band": need_fit_band,
        "go_thresholds": go_thresholds,
        "hold_thresholds": hold_thresholds,
        "summary": f"{decision} decision sensitivity is {risk_level}: adoption {adoption_band['range'][0]}-{adoption_band['range'][1]}/100, need-fit {need_fit_band['range'][0]}-{need_fit_band['range'][1]}/100 ({panel_note}).",
        "interpretation": interpretation,
        "recommended_action": recommended_action,
        "disclaimer": "Heuristic synthetic uncertainty band, not a statistical confidence interval or real-market forecast.",
    }


def _top_counter(values: list[str], *, limit: int = 5) -> list[dict[str, Any]]:
    counts = Counter(value for value in values if str(value).strip())
    total = sum(counts.values()) or 1
    return [
        {"value": value, "count": count, "share_percent": round(count / total * 100)}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _age_bucket(age: int | None) -> str:
    if age is None:
        return "unknown"
    if age < 30:
        return "20대 이하"
    if age < 40:
        return "30대"
    if age < 50:
        return "40대"
    if age < 60:
        return "50대"
    if age < 70:
        return "60대"
    return "70대 이상"


def build_persona_panel_profile(
    brief: dict[str, Any],
    personas: list[dict[str, Any]],
    *,
    source_persona_count: int | None = None,
) -> dict[str, Any]:
    """Describe selected persona-panel coverage before interpreting scores.

    Synthetic research is easy to over-trust when a target filter silently leaves
    a tiny or demographically narrow panel. This profile is deterministic and
    local: it makes panel composition, filter fit, and coverage warnings visible
    without adding another model call.
    """

    normalized = validate_brief(brief)
    filters = normalized.get("persona_filters") or {}
    filter_source = normalized.get("persona_filter_source") or ("user" if filters else None)
    count = len(personas)
    source_count = count if source_persona_count is None else max(0, int(source_persona_count))
    requested_sample_size = int(normalized.get("sample_size", 100))

    ages = [_persona_age(persona) for persona in personas]
    age_values = [age for age in ages if age is not None]
    provinces: list[str] = []
    occupations: list[str] = []
    for persona in personas:
        normalized_persona = normalize_persona_for_prompt(persona)
        province = _first_text(normalized_persona.get("province"))
        occupation = _first_text(normalized_persona.get("occupation"))
        if province:
            provinces.append(province)
        if occupation:
            occupations.append(occupation)

    filter_scores = [persona_filter_score(persona, filters) for persona in personas] if filters else []
    hard_gate_match_count = sum(1 for score in filter_scores if score >= 0)
    positive_signal_count = sum(1 for score in filter_scores if score > 0)
    text_filter_count = sum(len(filters.get(key, [])) for key in ("occupations", "provinces", "keywords")) if filters else 0

    warnings: list[str] = []
    recommendations: list[str] = []
    strengths: list[str] = []

    if count == 0:
        warnings.append("선택된 persona가 없어 panel coverage를 계산할 수 없습니다.")
        recommendations.append("Nemotron persona JSONL을 로드하거나 built-in sample panel을 사용하세요.")
    elif count < 8:
        warnings.append("selected panel이 8명 미만이라 demographic/segment coverage는 참고용입니다.")
        recommendations.append("최소 20명 이상으로 panel을 확장해 coverage와 objection 반복성을 확인하세요.")
    elif count >= 20:
        strengths.append("selected panel이 초기 segment 비교에 충분한 크기입니다.")

    if count < requested_sample_size:
        warnings.append(f"requested sample_size({requested_sample_size})보다 selected panel({count})이 작습니다.")
        recommendations.append("sample_size를 실제 selected panel 수에 맞추거나 source persona를 더 크게 샘플링하세요.")

    if filters:
        if count and hard_gate_match_count == 0:
            warnings.append("persona_filters가 적격 persona를 찾지 못해 fallback panel처럼 해석해야 합니다.")
            recommendations.append("occupation/keyword 조건을 완화하거나 source persona 표본을 늘린 뒤 재실행하세요.")
        elif text_filter_count and positive_signal_count == 0:
            warnings.append("선택된 panel이 target text signals(occupation/province/keyword)에 직접 매칭되지 않았습니다.")
            recommendations.append("target_market에 맞는 occupation/keyword를 더 구체화하거나 Nemotron 표본을 늘리세요.")
        else:
            strengths.append("persona_filters가 selected panel 구성에 반영되었습니다.")
    elif normalized.get("target_market"):
        warnings.append("target_market은 있지만 persona_filters가 없어 일반 panel 평균으로 섞일 수 있습니다.")
        recommendations.append("beachhead segment용 occupation/age/keyword filter를 추가하세요.")

    if count >= 4:
        if len(set(provinces)) <= 1 and provinces:
            warnings.append("selected panel의 지역이 한 곳에 몰려 있어 지역 차이를 보기 어렵습니다.")
        elif len(set(provinces)) >= 3:
            strengths.append("selected panel이 여러 지역을 포함합니다.")
        if len(set(_age_bucket(age) for age in ages)) <= 1 and age_values:
            warnings.append("selected panel의 연령대가 한 구간에 몰려 있습니다.")
        elif len(set(_age_bucket(age) for age in ages if age is not None)) >= 3:
            strengths.append("selected panel이 여러 연령대를 포함합니다.")

    if not recommendations:
        recommendations.append("panel coverage를 report score와 함께 보고, 상위 가설은 실제 사용자로 검증하세요.")

    if filters and count and hard_gate_match_count == 0:
        selection_mode = "filter_fallback_unmatched"
    elif filters:
        selection_mode = "target_filtered"
    else:
        selection_mode = "unfiltered"

    return {
        "selection_mode": selection_mode,
        "source_persona_count": source_count,
        "selected_persona_count": count,
        "requested_sample_size": requested_sample_size,
        "persona_filters": filters or None,
        "persona_filter_source": filter_source,
        "target_filter_match_count": hard_gate_match_count if filters else None,
        "target_filter_positive_signal_count": positive_signal_count if filters else None,
        "top_provinces": _top_counter(provinces, limit=5),
        "top_occupations": _top_counter(occupations, limit=5),
        "age_buckets": _top_counter([_age_bucket(age) for age in ages], limit=6),
        "age_range": [min(age_values), max(age_values)] if age_values else None,
        "warnings": unique_top(warnings, limit=6),
        "strengths": unique_top(strengths, limit=5),
        "recommendations": unique_top(recommendations, limit=5),
    }


def build_request_budget(
    brief: dict[str, Any],
    *,
    persona_count: int,
    max_parallel_requests: int | None = None,
) -> dict[str, Any]:
    """Describe the bounded Solar call budget for one simulation run.

    The service should make request volume visible before users scale panels.
    Aggregation is local/deterministic, so the only model calls in the main run
    are persona-level reactions. This card is intentionally simple and can be
    shown in the UI, Markdown export, and saved run artifacts.
    """

    normalized = validate_brief(brief)
    count = max(0, int(persona_count))
    requested_sample_size = normalized.get("sample_size", 100)
    workers = _as_int(max_parallel_requests, default=4, min_value=1, max_value=8)
    if count:
        workers = min(workers, count)
    planned_batches = ((count + workers - 1) // workers) if count and workers else 0

    warnings: list[str] = []
    recommendations: list[str] = []
    if count == 0:
        warnings.append("선택된 persona가 없어 Solar 요청을 보낼 수 없습니다.")
        recommendations.append("Nemotron persona JSONL 또는 built-in sample panel을 먼저 로드하세요.")
    if count < int(requested_sample_size):
        warnings.append(
            f"requested sample_size({requested_sample_size})보다 실제 로드/필터된 panel({count})이 작습니다."
        )
        recommendations.append("sample_size를 실제 panel 수에 맞추거나 persona source를 더 크게 샘플링하세요.")
    if count > 50:
        warnings.append("50명 초과 panel은 반복 실행 시 API 비용과 대기 시간이 빠르게 커집니다.")
        recommendations.append("먼저 20-50명 directional run으로 segment/objection을 확인한 뒤 상위 후보만 확장하세요.")
    if workers >= 8 and count >= 8:
        warnings.append("최대 병렬 요청 수가 8로 제한되어도 provider rate limit에 걸릴 수 있습니다.")
        recommendations.append("429가 발생하면 max_parallel_requests를 2-4로 낮추고 Retry-After를 따르세요.")
    if not recommendations:
        recommendations.append("현재 run은 bounded persona-level calls + local aggregation으로 안전하게 실행 가능합니다.")

    return {
        "mode": "bounded_parallel_persona_calls",
        "model": "not reported",
        "requested_sample_size": requested_sample_size,
        "actual_persona_count": count,
        "solar_persona_calls": count,
        "local_aggregation_calls": 0,
        "estimated_total_model_calls": count,
        "max_parallel_requests": workers,
        "planned_batches": planned_batches,
        "hard_persona_cap": 200,
        "warnings": unique_top(warnings, limit=5),
        "recommendations": unique_top(recommendations, limit=5),
    }


def build_founder_decision_memo(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    decision_board: dict[str, Any],
    evidence_quality: dict[str, Any],
    validation_plan: dict[str, Any],
    experiment_backlog: list[dict[str, Any]],
    decision_sensitivity: dict[str, Any],
    assumption_stress_test: dict[str, Any],
) -> dict[str, Any]:
    """Create a one-page, board-ready memo from the full synthetic report.

    Upkinsey now emits many downstream artifacts. This memo is intentionally
    compact: it tells a founder what to do next, what would change the decision,
    and where not to over-trust the synthetic panel.
    """

    normalized = validate_brief(brief)
    product_name = normalized["product_name"]
    persona_count = len(reactions)
    adoption = round(sum(r.get("adoption_likelihood", 0) for r in reactions) / max(1, persona_count))
    need_fit = round(sum(r.get("need_fit_score", 0) for r in reactions) / max(1, persona_count))
    positive_count = sum(1 for r in reactions if _as_int(r.get("adoption_likelihood"), default=0) >= 65)
    skeptical_count = sum(1 for r in reactions if _as_int(r.get("adoption_likelihood"), default=0) < 40)

    decision = _first_text(decision_board.get("decision"), "Refine")
    driver = _first_counter_item(
        [driver for reaction in reactions for driver in _listify(reaction.get("positive_drivers"))],
        "핵심 사용 상황에서 체감 가치가 있는지 확인",
    )
    risk = _first_counter_item(
        [risk for reaction in reactions for risk in _listify(reaction.get("top_risks"))],
        "가격·신뢰·전환 장벽을 실제 사용자에게 확인",
    )

    first_experiment = experiment_backlog[0] if experiment_backlog else {}
    first_assumption = {}
    assumptions = assumption_stress_test.get("assumptions") if isinstance(assumption_stress_test.get("assumptions"), list) else []
    if assumptions and isinstance(assumptions[0], dict):
        first_assumption = assumptions[0]

    decision_key = decision.lower()
    if decision_key.startswith("go"):
        recommendation = "실제 사용자 검증으로 전진하되, 결제/전환 gate를 작게 잡습니다."
    elif "segment" in decision_key or "pivot" in decision_key:
        recommendation = "전체 시장 판단을 미루고, 반응이 강한 beachhead segment로 brief와 panel을 좁힙니다."
    elif decision_key.startswith("hold"):
        recommendation = "확장 실험은 보류하고, 핵심 가정이 깨지는지 먼저 확인합니다."
    else:
        recommendation = "컨셉은 보존하되 메시지·가격·신뢰 장벽을 보강해 재검증합니다."

    success_criteria = validation_plan.get("success_criteria") if isinstance(validation_plan.get("success_criteria"), list) else []
    decision_gate = _first_text(
        decision_sensitivity.get("recommended_action"),
        decision_board.get("next_step"),
        success_criteria[0] if success_criteria else None,
        "다음 실제 사용자 검증에서 adoption/need-fit과 주요 objection이 같은 방향인지 확인",
    )
    evidence_warnings = evidence_quality.get("warnings") if isinstance(evidence_quality.get("warnings"), list) else []
    caveat = _first_text(
        *evidence_warnings,
        evidence_quality.get("confidence"),
        "합성 pre-research 신호이므로 실제 사용자 행동으로 보정해야 합니다.",
    )

    next_48h_actions = unique_top(
        [
            f"핵심 메시지를 '{driver}' 중심으로 한 문장 랜딩/컨셉 카드로 정리",
            _first_text(first_experiment.get("experiment"), "가장 큰 objection 1개를 해소하는 smoke test 설계"),
            _first_text(validation_plan.get("recruiting_focus"), "가장 잘 맞는 세그먼트 5-8명 인터뷰 모집 기준 확정"),
            f"Kill-risk 질문: {_first_text(first_assumption.get('falsification_test'), risk)}",
        ],
        limit=4,
    )

    return {
        "title": f"{product_name} founder decision memo",
        "headline": f"Synthetic panel {persona_count}명 기준 adoption {adoption}/100, need-fit {need_fit}/100 — decision은 {decision}입니다.",
        "decision": decision,
        "recommendation": recommendation,
        "why_it_may_work": driver,
        "primary_kill_risk": risk,
        "signal_snapshot": {
            "persona_count": persona_count,
            "adoption_score": adoption,
            "need_fit_score": need_fit,
            "positive_personas": positive_count,
            "skeptical_personas": skeptical_count,
            "evidence_level": evidence_quality.get("level"),
            "decision_boundary": decision_sensitivity.get("decision_boundary"),
        },
        "decision_gate": decision_gate,
        "next_48h_actions": next_48h_actions,
        "caveat": caveat,
        "copy_paste_summary": " ".join(
            [
                f"{product_name}: {decision}.",
                f"근거는 {adoption}/100 adoption / {need_fit}/100 need-fit, 핵심 driver는 {driver}.",
                f"가장 먼저 깨볼 리스크는 {risk}.",
                f"다음 gate: {decision_gate}",
            ]
        ),
    }


def build_persona_prompt(brief: dict[str, Any], persona: dict[str, Any]) -> str:
    prompt_persona = normalize_persona_for_prompt(persona)
    return f"""
다음 제품에 대해 한 명의 synthetic persona 관점에서 시장 반응을 시뮬레이션하라.

[PRODUCT BRIEF]
{json.dumps(brief, ensure_ascii=False, indent=2)}

[PERSONA]
{json.dumps(prompt_persona, ensure_ascii=False, indent=2)}

요구사항:
- 이 persona의 생활 맥락, 직업, 목표를 근거로 판단한다.
- 나이/성별/지역에 대한 고정관념만으로 판단하지 않는다.
- 제품 이해도, 필요 적합도, 채택 가능성, 가격 저항, 거부 이유를 구체적으로 쓴다.
- `understanding_score`, `need_fit_score`, `adoption_likelihood`는 반드시 0-100 정수 평정 점수로 쓴다. 실제 구매 확률이나 모집단 비율을 뜻하지 않는다.
- 점수는 변별력 있게 매긴다. 무난한 중간값으로 몰지 말고, persona-product fit이 낮으면 20-45도 적극 사용한다.
- stance calibration:
  - adoption_likelihood >= 70: "긍정형"
  - 55-69: "조건부 긍정"
  - 40-54: "관망형"
  - < 40: "회의형"
- price_resistance calibration:
  - 무료/저가라도 신뢰·효용이 부족하면 Medium 이상 가능
  - 월 구독/수수료가 persona 맥락상 부담이면 High 사용
- 출력은 JSON only.

JSON schema:
{PERSONA_RESPONSE_SCHEMA}
""".strip()


def simulate_persona_reaction(
    brief: dict[str, Any],
    persona: dict[str, Any],
    *,
    client: UpstageClient,
) -> dict[str, Any]:
    persona_context = persona_context_for_result(persona)
    text = client.complete_text(
        build_persona_prompt(brief, persona),
        system=SYSTEM_PROMPT,
        temperature=0.25,
        max_tokens=900,
        timeout=90,
    )
    data = _extract_json(text)
    # A malformed model response is a failed call, not a plausible default score.
    for field in ("understanding_score", "need_fit_score", "adoption_likelihood"):
        value = data.get(field)
        try:
            score = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"Model response {field} must be a number between 0 and 100") from None
        if isinstance(value, bool) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"Model response {field} must be a number between 0 and 100")
    return {
        "name": str(data.get("name") or persona.get("name") or "Persona"),
        "meta": str(data.get("meta") or persona_meta(persona)),
        "stance": str(data.get("stance") or "관망형"),
        "understanding_score": _as_int(data.get("understanding_score"), default=60),
        "need_fit_score": _as_int(data.get("need_fit_score"), default=60),
        "adoption_likelihood": _as_int(data.get("adoption_likelihood"), default=50),
        "price_resistance": str(data.get("price_resistance") or "Medium"),
        "concern": str(data.get("concern") or "추가 검증이 필요함"),
        "positive_drivers": _listify(data.get("positive_drivers"))[:5],
        "top_risks": _listify(data.get("top_risks"))[:5],
        "next_validation_question": str(data.get("next_validation_question") or "실제 사용자에게 어떤 조건에서 사용할지 확인해야 함"),
        "used_persona_fields": _listify(data.get("used_persona_fields"))[:8],
        "persona_context": persona_context,
    }


def _price_risk_from_reactions(reactions: list[dict[str, Any]]) -> str:
    values = [_price_risk_score(r.get("price_resistance")) for r in reactions]
    avg = sum(values) / max(1, len(values))
    if avg < 0.75:
        return "Low"
    if avg < 1.5:
        return "Low-Medium"
    if avg < 2.4:
        return "Medium"
    return "High"


def aggregate_market_research(
    brief: dict[str, Any],
    reactions: list[dict[str, Any]],
    *,
    max_parallel_requests: int | None = None,
    panel_personas: list[dict[str, Any]] | None = None,
    source_persona_count: int | None = None,
) -> dict[str, Any]:
    adoption = round(sum(r["adoption_likelihood"] for r in reactions) / max(1, len(reactions)))
    need_fit = round(sum(r["need_fit_score"] for r in reactions) / max(1, len(reactions)))
    positive = sum(1 for r in reactions if r["adoption_likelihood"] >= 65)
    negative = sum(1 for r in reactions if r["adoption_likelihood"] < 40)
    neutral = max(0, len(reactions) - positive - negative)
    total = max(1, len(reactions))
    distribution = {
        "positive": round(positive / total * 100),
        "neutral": round(neutral / total * 100),
        "negative": round(negative / total * 100),
    }

    drivers: list[str] = []
    risks: list[str] = []
    questions: list[str] = []
    for reaction in reactions:
        drivers.extend(reaction.get("positive_drivers", []))
        risks.extend(reaction.get("top_risks", []))
        if reaction.get("next_validation_question"):
            questions.append(reaction["next_validation_question"])

    product_name = brief.get("product_name", "제품")
    brief_quality = assess_brief_quality(brief)
    objections = mine_objections(reactions)
    segments = build_segment_recommendations(reactions)
    persona_evidence_pack = build_persona_evidence_pack(reactions)
    intent_cohort_contrast = build_intent_cohort_contrast(reactions)
    focus_group_simulation_plan = build_focus_group_simulation_plan(
        brief,
        reactions,
        intent_cohort_contrast=intent_cohort_contrast,
        objections=objections,
    )
    decision_board = build_decision_board(
        reactions,
        brief_quality=brief_quality,
        segments=segments,
        objections=objections,
    )
    decision_sensitivity = build_decision_sensitivity(
        brief,
        reactions,
        decision_board=decision_board,
    )
    switching_analysis = build_switching_analysis(
        brief,
        reactions,
        objections=objections,
    )
    competitive_benchmark = build_competitive_benchmark(
        brief,
        reactions,
        switching_analysis=switching_analysis,
        objections=objections,
    )
    pricing_sensitivity = build_pricing_sensitivity(brief, reactions)
    assumption_stress_test = build_assumption_stress_test(
        brief,
        reactions,
        segments=segments,
        objections=objections,
        switching_analysis=switching_analysis,
        pricing_sensitivity=pricing_sensitivity,
    )
    evidence_quality = build_evidence_quality(brief, reactions, brief_quality=brief_quality)
    validation_plan = build_validation_plan(
        brief,
        reactions,
        segments=segments,
        objections=objections,
        decision_board=decision_board,
        brief_quality=brief_quality,
    )
    recruiting_screener = build_recruiting_screener(
        brief,
        reactions,
        validation_plan=validation_plan,
        segments=segments,
        objections=objections,
        intent_cohort_contrast=intent_cohort_contrast,
        evidence_quality=evidence_quality,
    )
    message_angle_tests = build_message_angle_tests(
        brief,
        reactions,
        segments=segments,
        objections=objections,
        switching_analysis=switching_analysis,
    )
    interview_discussion_guide = build_interview_discussion_guide(
        brief,
        reactions,
        validation_plan=validation_plan,
        recruiting_screener=recruiting_screener,
        objections=objections,
        switching_analysis=switching_analysis,
        pricing_sensitivity=pricing_sensitivity,
        message_angle_tests=message_angle_tests,
    )
    validation_survey = build_validation_survey(
        brief,
        reactions,
        validation_plan=validation_plan,
        recruiting_screener=recruiting_screener,
        message_angle_tests=message_angle_tests,
        pricing_sensitivity=pricing_sensitivity,
        switching_analysis=switching_analysis,
        evidence_quality=evidence_quality,
    )
    field_validation_tracker = build_field_validation_tracker(
        brief,
        reactions,
        validation_survey=validation_survey,
        recruiting_screener=recruiting_screener,
        decision_board=decision_board,
        evidence_quality=evidence_quality,
    )
    research_type_lens = build_research_type_lens(
        brief,
        reactions,
        segments=segments,
        objections=objections,
        pricing_sensitivity=pricing_sensitivity,
        message_angle_tests=message_angle_tests,
        intent_cohort_contrast=intent_cohort_contrast,
    )
    request_budget = build_request_budget(
        brief,
        persona_count=len(reactions),
        max_parallel_requests=max_parallel_requests,
    )
    panel_profile = (
        build_persona_panel_profile(
            brief,
            panel_personas,
            source_persona_count=source_persona_count,
        )
        if panel_personas is not None
        else None
    )
    experiment_backlog = build_experiment_backlog(
        brief,
        segments=segments,
        objections=objections,
        decision_board=decision_board,
        switching_analysis=switching_analysis,
        validation_plan=validation_plan,
        evidence_quality=evidence_quality,
    )
    next_run_brief_variants = build_next_run_brief_variants(
        brief,
        decision_board=decision_board,
        segments=segments,
        objections=objections,
        switching_analysis=switching_analysis,
        pricing_sensitivity=pricing_sensitivity,
        message_angle_tests=message_angle_tests,
    )
    research_sprint = build_research_sprint(
        brief,
        decision_board=decision_board,
        validation_plan=validation_plan,
        experiment_backlog=experiment_backlog,
        assumption_stress_test=assumption_stress_test,
        switching_analysis=switching_analysis,
    )
    founder_memo = build_founder_decision_memo(
        brief,
        reactions,
        decision_board=decision_board,
        evidence_quality=evidence_quality,
        validation_plan=validation_plan,
        experiment_backlog=experiment_backlog,
        decision_sensitivity=decision_sensitivity,
        assumption_stress_test=assumption_stress_test,
    )
    result = {
        "brief_quality": brief_quality,
        "evidence_quality": evidence_quality,
        "request_budget": request_budget,
        "panel_profile": panel_profile,
        "founder_memo": founder_memo,
        "research_type_lens": research_type_lens,
        "persona_evidence_pack": persona_evidence_pack,
        "focus_group_simulation_plan": focus_group_simulation_plan,
        "interview_discussion_guide": interview_discussion_guide,
        "validation_survey": validation_survey,
        "field_validation_tracker": field_validation_tracker,
        "decision_sensitivity": decision_sensitivity,
        "competitive_benchmark": competitive_benchmark,
        "adoption_score": adoption,
        "need_fit_score": need_fit,
        "price_risk": _price_risk_from_reactions(reactions),
        "reaction_distribution": distribution,
        "personas": [
            {
                "name": r["name"],
                "meta": r["meta"],
                "stance": r["stance"],
                "concern": r["concern"],
            }
            for r in reactions
        ],
        "persona_reactions": reactions,
        "report": {
            "executive_summary": f"{product_name}은(는) 합성 페르소나 기준 평균 채택 가능성 {adoption}/100로 나타났으며, 실제 출시 전 가격 저항과 신뢰 형성 메시지를 우선 검증해야 합니다.",
            "positive_drivers": unique_top(drivers) or ["생활 문제를 직접 해결하는 실용성"],
            "top_risks": unique_top_risks(risks) or ["가격 저항과 신뢰 부족"],
            "objections": objections,
            "segment_recommendations": segments,
            "persona_evidence_pack": persona_evidence_pack,
            "intent_cohort_contrast": intent_cohort_contrast,
            "focus_group_simulation_plan": focus_group_simulation_plan,
            "decision_board": decision_board,
            "decision_sensitivity": decision_sensitivity,
            "switching_analysis": switching_analysis,
            "competitive_benchmark": competitive_benchmark,
            "pricing_sensitivity": pricing_sensitivity,
            "assumption_stress_test": assumption_stress_test,
            "validation_plan": validation_plan,
            "recruiting_screener": recruiting_screener,
            "interview_discussion_guide": interview_discussion_guide,
            "validation_survey": validation_survey,
            "field_validation_tracker": field_validation_tracker,
            "message_angle_tests": message_angle_tests,
            "research_type_lens": research_type_lens,
            "experiment_backlog": experiment_backlog,
            "next_run_brief_variants": next_run_brief_variants,
            "research_sprint": research_sprint,
            "founder_memo": founder_memo,
            "brief_quality": brief_quality,
            "evidence_quality": evidence_quality,
            "request_budget": request_budget,
            "panel_profile": panel_profile,
            "next_validation_questions": unique_top(questions) or ["어떤 조건에서 실제 결제로 이어지는지 검증해야 함"],
        },
        "request_plan": {
            "mode": "parallel_persona_calls",
            "persona_count": len(reactions),
            "estimated_total_model_calls": request_budget["estimated_total_model_calls"],
            "planned_batches": request_budget["planned_batches"],
            "max_parallel_requests": request_budget["max_parallel_requests"],
            "brief_quality_score": brief_quality["score"],
            "evidence_quality_score": evidence_quality["score"],
            "request_budget_warnings": request_budget["warnings"],
            "panel_coverage_warnings": panel_profile["warnings"] if panel_profile else [],
            "persona_selection": panel_profile["selection_mode"]
            if panel_profile
            else ("target_filtered" if brief.get("persona_filters") else "unfiltered"),
            "persona_filters": brief.get("persona_filters") or None,
            "persona_filter_source": panel_profile["persona_filter_source"] if panel_profile else brief.get("persona_filter_source") or None,
            "source_persona_count": panel_profile["source_persona_count"] if panel_profile else None,
            "selected_persona_count": panel_profile["selected_persona_count"] if panel_profile else len(reactions),
            "target_filter_match_count": panel_profile["target_filter_match_count"] if panel_profile else None,
            "aggregation": "local_deterministic",
        },
        "model_note": "Local aggregation; source model not reported",
    }
    result["report_markdown"] = format_report_markdown(brief, result)
    result["report"]["markdown"] = result["report_markdown"]
    return result


def simulate_market_research(
    brief: dict[str, Any],
    *,
    client: UpstageClient | None = None,
    personas: list[dict[str, Any]] | None = None,
    max_workers: int = 4,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run persona calls in parallel, then aggregate locally.

    Independent persona requests are parallelized to reduce latency. The final
    report aggregation is deterministic and local so it does not serialize an
    additional model call after the parallel batch.
    """

    started_at = datetime.now(timezone.utc).isoformat()
    started_clock = time.monotonic()
    normalized_brief = validate_brief(brief)
    if personas is not None and not personas:
        raise ValueError("personas must contain at least one persona")
    client = client or UpstageClient()
    inferred_filters = infer_persona_filters_from_brief(normalized_brief)
    if inferred_filters:
        normalized_brief = {
            **normalized_brief,
            "persona_filters": inferred_filters,
            "persona_filter_source": "inferred_target_market",
        }
    source_personas = personas or SAMPLE_PERSONAS
    selected_personas = select_personas_for_brief(source_personas, normalized_brief)
    model = getattr(getattr(client, "config", None), "model", None)
    # Validate and fingerprint inputs before spending quota, not after successful
    # model calls. External callers may provide non-JSON metadata on personas.
    provenance = build_provenance(
        brief=normalized_brief,
        source_count=len(source_personas),
        selected_personas=selected_personas,
        model=model if isinstance(model, str) else None,
        system_prompt=SYSTEM_PROMPT,
        persona_prompts=[build_persona_prompt(normalized_brief, persona) for persona in selected_personas],
        started_at=started_at,
        duration_seconds=0,
    )
    workers = max(1, min(max_workers, len(selected_personas), 8))
    if progress_callback:
        progress_callback(
            {
                "stage": "persona_calls",
                "message": "페르소나별 Solar 응답 생성 중",
                "completed": 0,
                "total": len(selected_personas),
            }
        )

    indexed_results: list[tuple[int, dict[str, Any]]] = []
    persona_failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(simulate_persona_reaction, normalized_brief, persona, client=client): index
            for index, persona in enumerate(selected_personas)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                indexed_results.append((index, future.result()))
            except Exception as exc:
                persona = selected_personas[index] if index < len(selected_personas) else {}
                persona_failures.append(
                    {
                        "index": index,
                        "name": _first_text(persona.get("name"), f"Persona {index + 1}"),
                        "error": _shorten(exc, limit=240),
                    }
                )
            if progress_callback:
                progress_callback(
                    {
                        "stage": "persona_calls",
                        "message": "페르소나별 Solar 응답 생성 중",
                        "completed": len(indexed_results) + len(persona_failures),
                        "total": len(selected_personas),
                    }
                )

    reactions = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]
    if not reactions and persona_failures:
        first_error = persona_failures[0].get("error") or "unknown upstream failure"
        raise RuntimeError(f"All persona calls failed: {first_error}")
    if progress_callback:
        progress_callback(
            {
                "stage": "aggregation",
                "message": f"{len(reactions)}명 응답을 집계하고 리포트를 구성 중",
                "completed": len(reactions),
                "total": len(selected_personas),
            }
        )
    result = aggregate_market_research(
        normalized_brief,
        reactions,
        max_parallel_requests=workers,
        panel_personas=selected_personas,
        source_persona_count=len(source_personas),
    )
    if persona_failures:
        result["partial_failures"] = persona_failures[:20]
        budget = build_request_budget(normalized_brief, persona_count=len(selected_personas), max_parallel_requests=workers)
        budget.update({
            "actual_persona_count": len(reactions),
            "attempted_persona_calls": len(selected_personas),
            "successful_persona_calls": len(reactions),
            "failed_persona_calls": len(persona_failures),
        })
        budget["warnings"] = unique_top([
            "Call counts are logical persona requests; provider retries may incur additional calls and cost.",
            *budget.get("warnings", []),
        ], limit=5)
        result["request_budget"] = budget
        result["request_plan"].update({
            "estimated_total_model_calls": budget["estimated_total_model_calls"],
            "planned_batches": budget["planned_batches"],
            "max_parallel_requests": budget["max_parallel_requests"],
            "attempted_persona_calls": len(selected_personas),
            "failed_persona_calls": len(persona_failures),
            "request_budget_warnings": budget["warnings"],
        })
        result["report"]["request_budget"] = budget
        warning = f"{len(persona_failures)} persona API calls failed; aggregate uses {len(reactions)} successful responses."
        evidence = result.setdefault("evidence_quality", {})
        # Put incomplete-panel evidence first so it survives warning limits.
        evidence["warnings"] = unique_top([warning] + _listify(evidence.get("warnings")), limit=8)
        result.setdefault("report", {}).setdefault("evidence_quality", evidence)["warnings"] = evidence["warnings"]
    provenance["duration_seconds"] = round(max(0, time.monotonic() - started_clock), 3)
    result["model_note"] = provenance["model"] or "not reported by client"
    result["provenance"] = provenance
    result["report"]["provenance"] = provenance
    result["request_budget"]["model"] = provenance["model"] or "not reported by client"
    result["report"]["request_budget"]["model"] = result["request_budget"]["model"]
    result["report_markdown"] = format_report_markdown(normalized_brief, result)
    result["report"]["markdown"] = result["report_markdown"]
    return result


def chat_with_persona(
    brief: dict[str, Any],
    persona_reaction: dict[str, Any],
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    client: UpstageClient | None = None,
) -> dict[str, Any]:
    """Ask a selected persona a follow-up interview question."""

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    if not isinstance(persona_reaction, dict):
        raise ValueError("persona must be an object")

    client = client or UpstageClient()
    text = client.complete_text(
        build_persona_chat_prompt(brief, persona_reaction, message.strip(), history),
        system=SYSTEM_PROMPT,
        temperature=0.35,
        max_tokens=600,
        timeout=90,
    )
    data = _extract_json(text)
    return {
        "persona_name": str(data.get("persona_name") or persona_reaction.get("name") or "Persona"),
        "reply": str(data.get("reply") or "조금 더 구체적으로 물어봐 주세요."),
        "signal": str(data.get("signal") or "other"),
        "new_information": _listify(data.get("new_information"))[:5],
        "suggested_followup": str(data.get("suggested_followup") or "어떤 조건이면 사용 의향이 생기나요?"),
    }


def analyst_question_personas(
    brief: dict[str, Any],
    persona_reactions: list[dict[str, Any]],
    question: str,
    *,
    client: UpstageClient | None = None,
    target_limit: int = 4,
    max_workers: int = 4,
    max_rounds: int = 5,
) -> dict[str, Any]:
    """Analyst layer: pick target personas, interview them, then synthesize.

    This sits between persona drill-down and the final report. It preserves the
    full analyst↔persona transcript so users can inspect exactly where the
    synthesis came from.
    """

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(persona_reactions, list) or not persona_reactions:
        raise ValueError("persona_reactions must be a non-empty list")

    normalized_brief = validate_brief(brief)
    safe_reactions = [reaction for reaction in persona_reactions if isinstance(reaction, dict)]
    targets = select_analyst_target_personas(safe_reactions, question.strip(), limit=target_limit)
    client = client or UpstageClient()
    workers = max(1, min(max_workers, len(targets), 6))
    max_rounds = max(1, min(max_rounds, 5))

    def ask(target: dict[str, Any]) -> dict[str, Any]:
        persona = safe_reactions[int(target["index"])]
        question_plan = build_analyst_question_plan(normalized_brief, persona, question.strip(), target)
        display_messages: list[dict[str, str]] = []
        chat_history: list[dict[str, str]] = []
        signals: list[str] = []
        followups: list[str] = []
        generated_questions: list[str] = []
        current_question = question_plan["primary_question"]
        result: dict[str, Any] = {}
        stop_reason = "max_rounds"
        coverage: dict[str, Any] = {"enough": False, "score": 0, "missing": []}

        for round_index in range(1, max_rounds + 1):
            generated_questions.append(current_question)
            result = chat_with_persona(
                normalized_brief,
                persona,
                current_question,
                history=chat_history,
                client=client,
            )
            reply = result.get("reply") or ""
            display_messages.extend(
                [
                    {"role": "analyst", "content": current_question, "round": round_index},
                    {"role": "persona", "content": reply, "round": round_index, "new_information": result.get("new_information") or []},
                ]
            )
            chat_history.extend(
                [
                    {"role": "user", "content": current_question},
                    {"role": "persona", "content": reply},
                ]
            )
            signals.append(str(result.get("signal") or "other"))
            if result.get("suggested_followup"):
                followups.append(str(result["suggested_followup"]))
            coverage = _analyst_information_coverage(display_messages)
            if coverage.get("enough"):
                stop_reason = "enough_information"
                break
            if coverage.get("repeated"):
                stop_reason = "repeated_answer"
                break
            if round_index < max_rounds:
                current_question = build_custom_analyst_followup(
                    normalized_brief,
                    persona,
                    question_plan,
                    display_messages,
                    result,
                    round_number=round_index + 1,
                )

        signal = _first_counter_item(signals, "other")
        persona_replies = [message["content"] for message in display_messages if message.get("role") == "persona"]
        new_information = unique_top(
            [info for message in display_messages for info in _listify(message.get("new_information"))],
            limit=8,
        )
        combined_reply = " / ".join(_short_quote(reply, limit=150) for reply in persona_replies[:3])
        return {
            "persona_index": target["index"],
            "persona_name": result.get("persona_name") or target.get("name") or persona.get("name") or "Persona",
            "meta": target.get("meta") or persona.get("meta") or "",
            "stance": target.get("stance") or persona.get("stance") or "",
            "target_reason": target.get("reason") or "분석 질문에 대한 대표 반응 확인",
            "signal": signal,
            "signals": signals,
            "question_plan": question_plan,
            "generated_questions": generated_questions,
            "reply": combined_reply or result.get("reply") or "",
            "final_reply": result.get("reply") or "",
            "new_information": new_information,
            "suggested_followup": _first_text(*followups[-2:]),
            "round_count": coverage.get("persona_turns") or max(1, len(persona_replies)),
            "max_rounds": max_rounds,
            "stop_reason": stop_reason,
            "information_coverage": coverage,
            "messages": display_messages,
        }

    indexed: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(ask, target): idx for idx, target in enumerate(targets)}
        for future in as_completed(futures):
            indexed.append((futures[future], future.result()))
    conversations = [item for _, item in sorted(indexed, key=lambda pair: pair[0])]

    return {
        "question": question.strip(),
        "target_personas": targets,
        "conversations": conversations,
        "synthesis": _synthesize_analyst_interviews(question.strip(), conversations),
        "max_rounds": max_rounds,
        "model_note": f"{getattr(getattr(client, 'config', None), 'model', None) or 'not reported by client'} persona interviews + local deterministic synthesis",
    }
