"""Market research simulation prompt + response helpers.

The simulation sends persona-level Upstage requests in parallel whenever possible
and only aggregates after all independent persona calls return.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Callable

from .upstage_client import UpstageClient

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


def _extract_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object")
    return data


def _as_int(value: Any, *, default: int = 0, min_value: int = 0, max_value: int = 100) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(value)] if str(value).strip() else []


def _bounded_list(value: Any, *, limit: int = 12, item_limit: int = 80) -> list[str]:
    return [str(item).strip()[:item_limit] for item in _listify(value) if str(item).strip()][:limit]


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _shorten(value: Any, *, limit: int = 600) -> str:
    text = _first_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def persona_meta(persona: dict[str, Any]) -> str:
    """Return a compact display label for built-in or Nemotron personas."""

    demographics = persona.get("demographics") if isinstance(persona.get("demographics"), dict) else {}
    age = _first_text(persona.get("age"), demographics.get("age"))
    province = _first_text(persona.get("province"), demographics.get("province"))
    occupation = _first_text(persona.get("occupation"), demographics.get("occupation"))
    parts = []
    if age:
        parts.append(f"{age}세" if age.isdigit() else age)
    if province:
        parts.append(province)
    if occupation:
        parts.append(occupation)
    return " · ".join(parts) or "합성 페르소나"


def normalize_persona_for_prompt(persona: dict[str, Any]) -> dict[str, Any]:
    """Flatten compact Nemotron rows into the prompt shape used by the simulator.

    The Nemotron sampler stores demographics and rich life-domain prose in nested
    sections. This keeps the prompt small and consistent while preserving enough
    context for product fit judgments.
    """

    demographics = persona.get("demographics") if isinstance(persona.get("demographics"), dict) else {}
    life_domains = persona.get("life_domains") if isinstance(persona.get("life_domains"), dict) else {}
    capabilities = persona.get("capabilities") if isinstance(persona.get("capabilities"), dict) else {}
    interests = persona.get("interests") if isinstance(persona.get("interests"), dict) else {}

    return {
        "name": _first_text(persona.get("name")) or "Persona",
        "meta": persona_meta(persona),
        "age": persona.get("age", demographics.get("age")),
        "province": _first_text(persona.get("province"), demographics.get("province")),
        "occupation": _first_text(persona.get("occupation"), demographics.get("occupation")),
        "persona": _shorten(persona.get("persona"), limit=900),
        "family_context": _shorten(life_domains.get("family"), limit=500),
        "professional_context": _shorten(life_domains.get("professional"), limit=500),
        "interests": _listify(interests.get("hobbies_list"))[:8],
        "capabilities": _listify(capabilities.get("skills_list"))[:8],
        "goals": _shorten(persona.get("goals"), limit=500),
        "source": {
            "dataset_id": persona.get("dataset_id"),
            "uuid": persona.get("uuid"),
            "name_parse_confidence": (persona.get("name_parse") or {}).get("confidence")
            if isinstance(persona.get("name_parse"), dict)
            else None,
        },
    }


def persona_context_for_result(persona: dict[str, Any]) -> dict[str, Any]:
    """Return the exact compact persona context that was shown to the model.

    This is intentionally not the full raw Nemotron row. It mirrors
    `normalize_persona_for_prompt()` so the UI can explain why a respondent
    reacted a certain way without exposing unused source fields.
    """

    normalized = normalize_persona_for_prompt(persona)
    return {
        key: value
        for key, value in normalized.items()
        if value not in (None, "", [], {})
    }


def _persona_age(persona: dict[str, Any]) -> int | None:
    demographics = persona.get("demographics") if isinstance(persona.get("demographics"), dict) else {}
    value = persona.get("age", demographics.get("age"))
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _persona_search_text(persona: dict[str, Any]) -> str:
    """Return compact text used for deterministic target-panel ranking."""

    normalized = normalize_persona_for_prompt(persona)
    parts: list[str] = []
    for key in ("name", "meta", "province", "occupation", "persona", "family_context", "professional_context", "goals"):
        parts.append(_first_text(normalized.get(key)))
    parts.extend(_listify(normalized.get("interests")))
    parts.extend(_listify(normalized.get("capabilities")))

    demographics = persona.get("demographics") if isinstance(persona.get("demographics"), dict) else {}
    life_domains = persona.get("life_domains") if isinstance(persona.get("life_domains"), dict) else {}
    for value in [*demographics.values(), *life_domains.values()]:
        parts.append(_shorten(value, limit=500))
    return " ".join(part for part in parts if part).lower()


def _normalize_persona_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    filters = {
        "occupations": _bounded_list(value.get("occupations"), limit=20),
        "provinces": _bounded_list(value.get("provinces"), limit=20),
        "keywords": _bounded_list(value.get("keywords"), limit=30),
        "exclude_keywords": _bounded_list(value.get("exclude_keywords"), limit=20),
    }
    if value.get("age_min") is not None:
        filters["age_min"] = _as_int(value.get("age_min"), default=0, min_value=0, max_value=120)
    if value.get("age_max") is not None:
        filters["age_max"] = _as_int(value.get("age_max"), default=120, min_value=0, max_value=120)
    if value.get("panel_limit") is not None:
        filters["panel_limit"] = _as_int(value.get("panel_limit"), default=50, min_value=1, max_value=200)
    return {key: val for key, val in filters.items() if val not in ([], None, "")}



TARGET_SEGMENT_KEYWORDS: list[tuple[tuple[str, ...], dict[str, list[str]]]] = [
    (("카페", "음식점", "식당", "소상공인", "자영업", "사장"), {
        "occupations": ["카페", "음식점", "식당", "자영업", "소상공인", "사장"],
        "keywords": ["매장", "고객", "리뷰", "운영", "동네"],
    }),
    (("리뷰", "답글", "평점"), {
        "keywords": ["리뷰", "답글", "평점", "고객 응대", "반복 불만"],
    }),
    (("병원", "진료", "환자", "만성질환", "보호자"), {
        "occupations": ["보호자", "간병", "의료"],
        "keywords": ["병원", "진료", "대기", "보호자", "만성질환", "건강"],
    }),
    (("복약", "약", "시니어", "고령", "노인", "어르신"), {
        "keywords": ["복약", "건강", "가족", "보호자", "알림"],
    }),
    (("학생", "대학생", "청년"), {
        "occupations": ["학생", "대학생"],
        "keywords": ["학교", "학업", "청년"],
    }),
    (("직장인", "회사원", "오피스", "출퇴근"), {
        "occupations": ["직장인", "회사원", "사무"],
        "keywords": ["출퇴근", "회사", "업무"],
    }),
    (("수리", "기사", "견적", "동네"), {
        "occupations": ["자영업", "기술", "수리"],
        "keywords": ["수리", "견적", "동네", "지인", "신뢰"],
    }),
    (("정책", "복지", "주민센터", "지원금"), {
        "keywords": ["정책", "복지", "주민센터", "지원금", "지역"],
    }),
    (("식단", "냉장고", "요리", "건강관리"), {
        "keywords": ["식단", "요리", "냉장고", "건강", "가족"],
    }),
]

KOREA_PROVINCES = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충청북도", "충남", "충청남도", "전북", "전라북도",
    "전남", "전라남도", "경북", "경상북도", "경남", "경상남도", "제주",
]


def _dedupe_preserve_order(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text[:80])
        if len(out) >= limit:
            break
    return out


def _infer_age_bounds_from_text(text: str) -> dict[str, int]:
    bounds: dict[str, int] = {}
    ranges = re.findall(r"(\d{2})\s*[-~–]\s*(\d{2})대", text)
    if ranges:
        lows = [int(start) for start, _ in ranges]
        highs = [int(end) + 9 for _, end in ranges]
        bounds["age_min"] = max(0, min(lows))
        bounds["age_max"] = min(120, max(highs))
        return bounds

    decades = [int(value) for value in re.findall(r"(\d{2})대", text)]
    if decades:
        bounds["age_min"] = max(0, min(decades))
        bounds["age_max"] = min(120, max(decades) + 9)
    if any(keyword in text for keyword in ("시니어", "고령", "노인", "어르신", "은퇴")):
        bounds["age_min"] = max(int(bounds.get("age_min", 0)), 60)
    return bounds


def infer_persona_filters_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Infer lightweight target-panel filters from a natural-language brief.

    The prototype UI asks for target_market/current alternatives but not raw
    persona_filters. This helper keeps runs target-aware without another model
    call: it only extracts conservative occupation, province, keyword, and age
    hints from the brief. Explicit persona_filters always take precedence.
    """

    normalized = validate_brief(brief)
    if normalized.get("persona_filters"):
        return {}

    text = " ".join(
        str(normalized.get(key) or "")
        for key in ("product_name", "description", "target_market", "hypothesis", "current_alternatives")
    )
    text += " " + " ".join(_listify(normalized.get("features")))
    lowered = text.lower()

    occupations: list[str] = []
    keywords: list[str] = []
    for needles, payload in TARGET_SEGMENT_KEYWORDS:
        if any(needle.lower() in lowered for needle in needles):
            occupations.extend(payload.get("occupations", []))
            keywords.extend(payload.get("keywords", []))

    provinces = [province for province in KOREA_PROVINCES if province.lower() in lowered]
    inferred: dict[str, Any] = {
        "occupations": _dedupe_preserve_order(occupations, limit=12),
        "provinces": _dedupe_preserve_order(provinces, limit=8),
        "keywords": _dedupe_preserve_order(keywords, limit=18),
    }
    inferred.update(_infer_age_bounds_from_text(text))
    inferred = _normalize_persona_filters(inferred)
    if not any(inferred.get(key) for key in ("occupations", "provinces", "keywords")) and not (
        "age_min" in inferred or "age_max" in inferred
    ):
        return {}
    return inferred

def persona_filter_score(persona: dict[str, Any], filters: dict[str, Any]) -> int:
    """Score how well a persona matches product-specific target filters.

    Age bounds and exclude keywords are hard gates. Other fields are positive
    ranking signals so small panels do not become empty accidentally.
    """

    if not filters:
        return 0

    age = _persona_age(persona)
    if age is not None:
        if "age_min" in filters and age < int(filters["age_min"]):
            return -1
        if "age_max" in filters and age > int(filters["age_max"]):
            return -1

    text = _persona_search_text(persona)
    if any(keyword.lower() in text for keyword in filters.get("exclude_keywords", [])):
        return -1

    score = 0
    demographics = persona.get("demographics") if isinstance(persona.get("demographics"), dict) else {}
    occupation = _first_text(persona.get("occupation"), demographics.get("occupation")).lower()
    province = _first_text(persona.get("province"), demographics.get("province")).lower()
    for keyword in filters.get("occupations", []):
        needle = keyword.lower()
        if needle and (needle in occupation or needle in text):
            score += 4
    for keyword in filters.get("provinces", []):
        needle = keyword.lower()
        if needle and (needle in province or needle in text):
            score += 2
    for keyword in filters.get("keywords", []):
        needle = keyword.lower()
        if needle and needle in text:
            score += 1
    return score


def select_personas_for_brief(personas: list[dict[str, Any]], brief: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank a panel using product-specific persona filters without shrinking sample_size.

    Target filters should prioritize the best-fit personas, not silently reduce a
    requested 30-person run to the 3 personas that match inferred age/keyword
    hints. Only an explicit `panel_limit` is allowed to trim the panel below the
    requested `sample_size`.
    """

    filters = brief.get("persona_filters") if isinstance(brief.get("persona_filters"), dict) else {}
    if not filters:
        return personas

    requested = _as_int(brief.get("sample_size"), default=len(personas), min_value=1, max_value=500)
    explicit_panel_limit = filters.get("panel_limit") is not None
    target_size = int(filters.get("panel_limit") if explicit_panel_limit else requested)
    target_size = max(1, min(target_size, len(personas)))

    scored: list[tuple[int, int, dict[str, Any]]] = []
    fallback: list[tuple[int, dict[str, Any]]] = []
    for index, persona in enumerate(personas):
        score = persona_filter_score(persona, filters)
        if score >= 0:
            scored.append((score, index, persona))
        elif not explicit_panel_limit:
            # Age bounds are useful ranking hints for inferred target panels, but
            # they must not cut a user-requested sample short. Keep explicit
            # exclude_keywords as hard exclusions even when filling the panel.
            text = _persona_search_text(persona)
            if not any(keyword.lower() in text for keyword in filters.get("exclude_keywords", [])):
                fallback.append((index, persona))
    if not scored:
        return [persona for _, persona in fallback[:target_size]] or personas[:target_size]

    ranked = [persona for score, _, persona in sorted(scored, key=lambda item: (-item[0], item[1]))]
    if len(ranked) < target_size and not explicit_panel_limit:
        ranked.extend(persona for _, persona in fallback if persona not in ranked)
    return ranked[:target_size]


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

PRICE_RISK_SCORE = {"Low": 0, "Low-Medium": 1, "Medium": 2, "High": 3}


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


def _dedupe_key(text: str) -> str:
    lowered = text.lower()
    replacements = {
        "구독료": "가격",
        "요금": "가격",
        "비용": "가격",
        "유료": "가격",
        "결제": "가격",
        "신뢰도": "신뢰",
        "믿기": "신뢰",
        "정확도": "신뢰",
        "개인 정보": "개인정보",
        "데이터": "개인정보",
        "설치": "사용법",
        "복잡함": "복잡",
        "어려움": "어렵",
    }
    for old, new in replacements.items():
        lowered = lowered.replace(old, new)
    return re.sub(r"[^0-9a-z가-힣]+", "", lowered)


def unique_top(items: list[str], limit: int = 4) -> list[str]:
    """Return stable top items while collapsing near-duplicate phrasing."""

    seen: set[str] = set()
    out: list[str] = []
    for raw_item in items:
        item = str(raw_item).strip()
        key = _dedupe_key(item)
        if item and key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


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


def _price_risk_score(label: Any) -> int:
    return PRICE_RISK_SCORE.get(str(label), 2)


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


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


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


def _segment_persona_label(reaction: dict[str, Any]) -> str:
    name = _first_text(reaction.get("name"), "Persona")
    meta = _first_text(reaction.get("meta"))
    return f"{name} ({meta})" if meta else name


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
        "model": "Upstage Solar Pro 3",
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


def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Normalize and bound user-provided brief before it reaches the API."""

    if not isinstance(brief, dict):
        raise ValueError("brief must be an object")

    def text_field(name: str, default: str = "") -> str:
        value = brief.get(name, default)
        if value is None:
            return default
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        return str(value).strip()[:2000]

    sample_size = _as_int(brief.get("sample_size", 100), default=100, min_value=1, max_value=500)
    seed = _as_int(brief.get("seed", 42), default=42, min_value=0, max_value=2_147_483_647)
    filter_source = text_field("persona_filter_source")
    if filter_source not in {"user", "inferred_target_market"}:
        filter_source = "user" if isinstance(brief.get("persona_filters"), dict) and brief.get("persona_filters") else ""

    return {
        "product_name": text_field("product_name", "제품") or "제품",
        "description": text_field("description"),
        "features": _listify(brief.get("features"))[:12],
        "pricing": _listify(brief.get("pricing"))[:8],
        "target_market": text_field("target_market"),
        "hypothesis": text_field("hypothesis"),
        "current_alternatives": text_field("current_alternatives"),
        "research_type": text_field("research_type", "Concept test") or "Concept test",
        "sample_size": sample_size,
        "seed": seed,
        "persona_filters": _normalize_persona_filters(brief.get("persona_filters")),
        "persona_filter_source": filter_source,
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
- `understanding_score`, `need_fit_score`, `adoption_likelihood`는 반드시 0-100 정수 percentage로 쓴다.
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


def _markdown_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _markdown_bullets(items: list[Any], *, empty: str = "n/a", limit: int = 6) -> list[str]:
    values = [str(item).strip() for item in items if str(item).strip()][:limit]
    if not values:
        values = [empty]
    return [f"- {_markdown_escape(item)}" for item in values]


def format_report_markdown(brief: dict[str, Any], aggregate: dict[str, Any]) -> str:
    """Render a simulation result as a portable Markdown insight report.

    The JSON response remains the canonical machine-readable artifact, but a
    founder needs a paste-ready report for docs, Notion, GitHub issues, and
    follow-up interview scripts. This formatter is deterministic and local so it
    does not add another API call or leak secrets.
    """

    normalized = validate_brief(brief)
    report = aggregate.get("report", {}) if isinstance(aggregate.get("report"), dict) else {}
    brief_quality = aggregate.get("brief_quality") or report.get("brief_quality") or {}
    evidence_quality = aggregate.get("evidence_quality") or report.get("evidence_quality") or {}
    request_budget = aggregate.get("request_budget") or report.get("request_budget") or {}
    panel_profile = aggregate.get("panel_profile") or report.get("panel_profile") or {}
    founder_memo = aggregate.get("founder_memo") or report.get("founder_memo") or {}
    research_type_lens = aggregate.get("research_type_lens") or report.get("research_type_lens") or {}
    persona_evidence_pack = aggregate.get("persona_evidence_pack") or report.get("persona_evidence_pack") or {}
    decision_board = report.get("decision_board") if isinstance(report.get("decision_board"), dict) else {}
    switching_analysis = report.get("switching_analysis") if isinstance(report.get("switching_analysis"), dict) else {}
    competitive_benchmark = report.get("competitive_benchmark") if isinstance(report.get("competitive_benchmark"), dict) else {}
    pricing_sensitivity = report.get("pricing_sensitivity") if isinstance(report.get("pricing_sensitivity"), dict) else {}
    assumption_stress_test = report.get("assumption_stress_test") if isinstance(report.get("assumption_stress_test"), dict) else {}
    validation_plan = report.get("validation_plan") if isinstance(report.get("validation_plan"), dict) else {}
    recruiting_screener = report.get("recruiting_screener") if isinstance(report.get("recruiting_screener"), dict) else {}
    interview_discussion_guide = report.get("interview_discussion_guide") if isinstance(report.get("interview_discussion_guide"), dict) else {}
    validation_survey = report.get("validation_survey") if isinstance(report.get("validation_survey"), dict) else {}
    field_validation_tracker = report.get("field_validation_tracker") if isinstance(report.get("field_validation_tracker"), dict) else {}
    message_angle_tests = report.get("message_angle_tests") if isinstance(report.get("message_angle_tests"), list) else []
    experiment_backlog = report.get("experiment_backlog") if isinstance(report.get("experiment_backlog"), list) else []
    next_run_brief_variants = report.get("next_run_brief_variants") if isinstance(report.get("next_run_brief_variants"), list) else []
    research_sprint = report.get("research_sprint") if isinstance(report.get("research_sprint"), dict) else {}
    intent_cohort_contrast = report.get("intent_cohort_contrast") if isinstance(report.get("intent_cohort_contrast"), dict) else {}
    focus_group_simulation_plan = report.get("focus_group_simulation_plan") if isinstance(report.get("focus_group_simulation_plan"), dict) else {}
    decision_sensitivity = report.get("decision_sensitivity") if isinstance(report.get("decision_sensitivity"), dict) else {}
    objections = report.get("objections") if isinstance(report.get("objections"), list) else []
    segments = report.get("segment_recommendations") if isinstance(report.get("segment_recommendations"), list) else []
    reactions = aggregate.get("persona_reactions") if isinstance(aggregate.get("persona_reactions"), list) else []

    lines = [
        f"# Upkinsey Market Insight Report — {_markdown_escape(normalized['product_name'])}",
        "",
        "## Executive summary",
        "",
        "Scores are model-generated 0–100 ratings, not purchase probabilities or statistical confidence.",
        "",
        _markdown_escape(report.get("executive_summary") or "Synthetic market pre-research result."),
        "",
        "## Product brief",
        "",
        f"- Research type: {_markdown_escape(normalized['research_type'])}",
        f"- Target market: {_markdown_escape(normalized['target_market'] or 'n/a')}",
        f"- Current alternatives: {_markdown_escape(normalized['current_alternatives'] or 'n/a')}",
        f"- Hypothesis: {_markdown_escape(normalized['hypothesis'] or 'n/a')}",
        "",
        "## Signal board",
        "",
        f"- Adoption likelihood: {aggregate.get('adoption_score', '-')}/100",
        f"- Need fit: {aggregate.get('need_fit_score', '-')}/100",
        f"- Price risk: {_markdown_escape(aggregate.get('price_risk', '-'))}",
        f"- Brief quality: {brief_quality.get('score', '-')} / 100 ({_markdown_escape(brief_quality.get('verdict', '-'))})",
        f"- Evidence quality: {evidence_quality.get('score', '-')} / 100 ({_markdown_escape(evidence_quality.get('level', '-'))}, {_markdown_escape(evidence_quality.get('confidence', '-'))} confidence)",
        f"- Solar request budget: {request_budget.get('estimated_total_model_calls', '-')} model calls across {request_budget.get('planned_batches', '-')} batch(es)",
        *(
            [
                f"- Persona panel: {_markdown_escape(panel_profile.get('selected_persona_count', '-'))} selected ({_markdown_escape(panel_profile.get('selection_mode', '-'))})"
            ]
            if panel_profile
            else []
        ),
        "",
    ]

    if founder_memo:
        snapshot = founder_memo.get("signal_snapshot") if isinstance(founder_memo.get("signal_snapshot"), dict) else {}
        lines += [
            "## Founder decision memo",
            "",
            f"- Headline: {_markdown_escape(founder_memo.get('headline', ''))}",
            f"- Recommendation: {_markdown_escape(founder_memo.get('recommendation', ''))}",
            f"- Why it may work: {_markdown_escape(founder_memo.get('why_it_may_work', ''))}",
            f"- Primary kill-risk: {_markdown_escape(founder_memo.get('primary_kill_risk', ''))}",
            f"- Decision gate: {_markdown_escape(founder_memo.get('decision_gate', ''))}",
            f"- Positive / skeptical personas: {_markdown_escape(snapshot.get('positive_personas', '-'))} / {_markdown_escape(snapshot.get('skeptical_personas', '-'))}",
            "- Next 48h actions:",
            *_markdown_bullets(founder_memo.get("next_48h_actions", []), empty="n/a", limit=4),
            f"- Caveat: {_markdown_escape(founder_memo.get('caveat', ''))}",
            "",
            f"> {_markdown_escape(founder_memo.get('copy_paste_summary', ''))}",
            "",
        ]

    if research_type_lens:
        lines += [
            "## Research type lens",
            "",
            f"- Lens: {_markdown_escape(research_type_lens.get('lens', '-'))}",
            f"- Primary metric: {_markdown_escape(research_type_lens.get('primary_metric', '-'))}",
            f"- Primary output: {_markdown_escape(research_type_lens.get('primary_output', '-'))}",
            f"- Interpretation: {_markdown_escape(research_type_lens.get('interpretation', '-'))}",
            f"- Recommended next action: {_markdown_escape(research_type_lens.get('recommended_next_action', '-'))}",
            f"- Watch metric: {_markdown_escape(research_type_lens.get('watch_metric', '-'))}",
            "",
        ]

    if panel_profile:
        lines += [
            "## Persona panel coverage",
            "",
            f"- Selection mode: {_markdown_escape(panel_profile.get('selection_mode', 'unfiltered'))}",
            f"- Source personas: {_markdown_escape(panel_profile.get('source_persona_count', '-'))}",
            f"- Selected personas: {_markdown_escape(panel_profile.get('selected_persona_count', '-'))}",
            f"- Age range: {_markdown_escape(panel_profile.get('age_range', 'n/a'))}",
        ]
        if panel_profile.get("top_provinces"):
            province_summary = ", ".join(
                f"{item.get('value')} {item.get('count')}명" for item in panel_profile.get("top_provinces", [])[:5]
            )
            lines.append(f"- Top provinces: {_markdown_escape(province_summary)}")
        if panel_profile.get("top_occupations"):
            occupation_summary = ", ".join(
                f"{item.get('value')} {item.get('count')}명" for item in panel_profile.get("top_occupations", [])[:5]
            )
            lines.append(f"- Top occupations: {_markdown_escape(occupation_summary)}")
        if panel_profile.get("age_buckets"):
            age_summary = ", ".join(
                f"{item.get('value')} {item.get('count')}명" for item in panel_profile.get("age_buckets", [])[:6]
            )
            lines.append(f"- Age buckets: {_markdown_escape(age_summary)}")
        if panel_profile.get("warnings"):
            lines += ["Warnings:", *_markdown_bullets(panel_profile.get("warnings", []), empty="n/a"), ""]
        elif panel_profile.get("recommendations"):
            lines += ["Recommendations:", *_markdown_bullets(panel_profile.get("recommendations", []), empty="n/a"), ""]
        else:
            lines.append("")

    if request_budget:
        lines += [
            "## Run budget & bounds",
            "",
            f"- Mode: {_markdown_escape(request_budget.get('mode', 'bounded_parallel_persona_calls'))}",
            f"- Requested sample size: {_markdown_escape(request_budget.get('requested_sample_size', '-'))}",
            f"- Actual personas: {_markdown_escape(request_budget.get('actual_persona_count', '-'))}",
            f"- Solar persona calls: {_markdown_escape(request_budget.get('solar_persona_calls', '-'))}",
            f"- Local aggregation calls: {_markdown_escape(request_budget.get('local_aggregation_calls', 0))}",
            f"- Max parallel requests: {_markdown_escape(request_budget.get('max_parallel_requests', '-'))}",
            f"- Planned batches: {_markdown_escape(request_budget.get('planned_batches', '-'))}",
        ]
        if request_budget.get("warnings"):
            lines += ["Warnings:", *_markdown_bullets(request_budget.get("warnings", []), empty="n/a"), ""]
        elif request_budget.get("recommendations"):
            lines += ["Recommendations:", *_markdown_bullets(request_budget.get("recommendations", []), empty="n/a"), ""]
        else:
            lines.append("")

    if evidence_quality.get("warnings") or evidence_quality.get("recommended_actions"):
        lines += ["## Evidence quality guardrail", ""]
        lines += [
            f"- Persona count: {evidence_quality.get('persona_count', '-')}",
            f"- Adoption range: {_markdown_escape(evidence_quality.get('adoption_range', '-'))}",
        ]
        if evidence_quality.get("warnings"):
            lines += ["Warnings:", *_markdown_bullets(evidence_quality.get("warnings", []), empty="n/a"), ""]
        if evidence_quality.get("recommended_actions"):
            lines += ["Recommended next actions:", *_markdown_bullets(evidence_quality.get("recommended_actions", []), empty="n/a"), ""]

    if brief_quality.get("missing_fields") or brief_quality.get("recommended_questions"):
        lines += ["## Brief preflight", ""]
        if brief_quality.get("missing_fields"):
            lines += ["Missing/tighten:", * _markdown_bullets(brief_quality.get("missing_fields", [])), ""]
        if brief_quality.get("recommended_questions"):
            lines += ["Recommended questions:", * _markdown_bullets(brief_quality.get("recommended_questions", [])), ""]

    lines += [
        "## What may work",
        "",
        *_markdown_bullets(report.get("positive_drivers", []), empty="생활 문제를 직접 해결하는 실용성"),
        "",
        "## What may block adoption",
        "",
        *_markdown_bullets(report.get("top_risks", []), empty="가격 저항과 신뢰 부족"),
        "",
    ]

    if persona_evidence_pack:
        lines += [
            "## Persona evidence pack",
            "",
            _markdown_escape(persona_evidence_pack.get("summary", "")),
            "",
        ]
        supporter_cards = persona_evidence_pack.get("supporter_cards") if isinstance(persona_evidence_pack.get("supporter_cards"), list) else []
        barrier_cards = persona_evidence_pack.get("barrier_cards") if isinstance(persona_evidence_pack.get("barrier_cards"), list) else []
        followup_cards = persona_evidence_pack.get("validation_followups") if isinstance(persona_evidence_pack.get("validation_followups"), list) else []
        cards = [("Supporter", card) for card in supporter_cards[:3]] + [("Barrier", card) for card in barrier_cards[:3]]
        if cards:
            lines += ["| Type | Persona | Signal | Interview probe |", "|---|---|---|---|"]
            for label, card in cards:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(label),
                            _markdown_escape(card.get("persona", "Persona")),
                            _markdown_escape(card.get("signal", "")),
                            _markdown_escape(card.get("interview_probe", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if followup_cards:
            lines += ["Validation follow-ups:"]
            lines += _markdown_bullets([card.get("validation_question", "") for card in followup_cards], empty="n/a", limit=5)
            lines.append("")
        if persona_evidence_pack.get("disclaimer"):
            lines += [f"_Note: {_markdown_escape(persona_evidence_pack.get('disclaimer', ''))}_", ""]

    if objections:
        lines += ["## Objection cards", "", "| Category | Example objection | Suggested fix | Affected |", "|---|---|---|---|"]
        for objection in objections[:6]:
            affected = ", ".join(str(item) for item in _listify(objection.get("affected_personas"))[:3])
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(objection.get("category", "기타")),
                        _markdown_escape(objection.get("objection", "")),
                        _markdown_escape(objection.get("suggested_fix", "")),
                        _markdown_escape(affected or "n/a"),
                    ]
                )
                + " |"
            )
        lines.append("")

    if switching_analysis:
        lines += [
            "## Current alternative & switching triggers",
            "",
            f"- Current alternative: {_markdown_escape(switching_analysis.get('current_alternatives', 'n/a'))}",
            "- Why the current alternative persists:",
            *_markdown_bullets(switching_analysis.get("why_current_alternative_persists", []), empty="n/a", limit=5),
            "- Switching triggers:",
            *_markdown_bullets(switching_analysis.get("switching_triggers", []), empty="n/a", limit=5),
            "- Validation tests:",
            *_markdown_bullets(switching_analysis.get("validation_tests", []), empty="n/a", limit=5),
            "",
        ]

    if competitive_benchmark:
        lines += [
            "## Competitive benchmark matrix",
            "",
            _markdown_escape(competitive_benchmark.get("summary", "")),
            "",
            f"- Primary barrier: {_markdown_escape(competitive_benchmark.get('primary_barrier', 'n/a'))}",
            f"- Positioning fix: {_markdown_escape(competitive_benchmark.get('suggested_positioning_fix', 'n/a'))}",
            f"- Next probe: {_markdown_escape(competitive_benchmark.get('recommended_next_probe', 'n/a'))}",
            "",
        ]
        benchmarks = competitive_benchmark.get("benchmarks") if isinstance(competitive_benchmark.get("benchmarks"), list) else []
        if benchmarks:
            lines += ["| Current alternative | Why users stay | Advantage to test | Barrier | Probe |", "|---|---|---|---|---|"]
            for row in benchmarks[:5]:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(row.get("alternative", "")),
                            _markdown_escape(row.get("why_users_stay", "")),
                            _markdown_escape(row.get("product_advantage_to_test", "")),
                            _markdown_escape(row.get("unresolved_barrier", "")),
                            _markdown_escape(row.get("validation_probe", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")

    if pricing_sensitivity:
        lines += [
            "## Pricing sensitivity lab",
            "",
            f"- Overall price risk: {_markdown_escape(pricing_sensitivity.get('overall_price_risk', 'n/a'))}",
            f"- Price-sensitive personas: {_markdown_escape(pricing_sensitivity.get('price_sensitive_persona_count', 0))}",
            f"- Top price objection: {_markdown_escape(pricing_sensitivity.get('top_price_objection', 'n/a'))}",
            f"- Recommended probe: {_markdown_escape(pricing_sensitivity.get('recommended_price_probe', 'n/a'))}",
            "",
        ]
        options = pricing_sensitivity.get("options") if isinstance(pricing_sensitivity.get("options"), list) else []
        if options:
            lines += ["| Price option | Role | Friction-adjusted adoption | Probe |", "|---|---|---:|---|"]
            for option in options[:6]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(option.get("option", "")),
                            _markdown_escape(option.get("test_role", "")),
                            _markdown_escape(f"{option.get('estimated_adoption_after_friction', '-')}/100"),
                            _markdown_escape(option.get("recommended_probe", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if pricing_sensitivity.get("validation_questions"):
            lines += [
                "Pricing validation questions:",
                *_markdown_bullets(pricing_sensitivity.get("validation_questions", []), empty="n/a", limit=5),
                "",
            ]

    if assumption_stress_test:
        lines += [
            "## Assumption stress test",
            "",
            f"- Overall assumption risk: {_markdown_escape(assumption_stress_test.get('overall_risk', 'n/a'))}",
            f"- Recommended next step: {_markdown_escape(assumption_stress_test.get('recommended_next_step', 'n/a'))}",
            "",
        ]
        assumptions = assumption_stress_test.get("assumptions") if isinstance(assumption_stress_test.get("assumptions"), list) else []
        if assumptions:
            lines += ["| Risk | Assumption | Synthetic signal | Falsification test | Pass signal |", "|---|---|---|---|---|"]
            for card in assumptions[:6]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(card.get("risk_level", "")),
                            _markdown_escape(card.get("assumption", "")),
                            _markdown_escape(card.get("synthetic_signal", "")),
                            _markdown_escape(card.get("falsification_test", "")),
                            _markdown_escape(card.get("pass_signal", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if assumption_stress_test.get("watchouts"):
            lines += ["Watchouts:", *_markdown_bullets(assumption_stress_test.get("watchouts", []), empty="n/a", limit=5), ""]

    if segments:
        lines += ["## Segment recommendations", "", "| Segment | Personas | Avg adoption | Primary objection | Next validation action |", "|---|---:|---:|---|---|"]
        for segment in segments[:5]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(segment.get("segment", "검증 타깃")),
                        _markdown_escape(segment.get("persona_count", 0)),
                        _markdown_escape(f"{segment.get('avg_adoption', '-')}/100"),
                        _markdown_escape(segment.get("primary_objection", "")),
                        _markdown_escape(segment.get("validation_action", "")),
                    ]
                )
                + " |"
            )
        lines.append("")

    if intent_cohort_contrast:
        lines += [
            "## Intent cohort contrast",
            "",
            _markdown_escape(intent_cohort_contrast.get("summary", "")),
            "",
            f"- Adoption gap: {_markdown_escape(intent_cohort_contrast.get('adoption_gap', 0))} points",
            f"- Recommended comparison: {_markdown_escape(intent_cohort_contrast.get('recommended_comparison', ''))}",
            "",
        ]
        cohorts = intent_cohort_contrast.get("cohorts") if isinstance(intent_cohort_contrast.get("cohorts"), list) else []
        if cohorts:
            lines += [
                "| Cohort | Personas | Avg adoption | Drivers | Objections | Validation focus |",
                "|---|---:|---:|---|---|---|",
            ]
            for cohort in cohorts[:5]:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(cohort.get("label", cohort.get("cohort", "cohort"))),
                            _markdown_escape(cohort.get("persona_count", 0)),
                            _markdown_escape(f"{cohort.get('avg_adoption', '-')}/100"),
                            _markdown_escape(", ".join(_listify(cohort.get("shared_drivers"))[:3]) or "n/a"),
                            _markdown_escape(", ".join(_listify(cohort.get("shared_objections"))[:3]) or "n/a"),
                            _markdown_escape(cohort.get("validation_focus", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")

    if focus_group_simulation_plan:
        lines += [
            "## Focus group simulation plan",
            "",
            f"- Objective: {_markdown_escape(focus_group_simulation_plan.get('objective', ''))}",
            f"- Recommended group size: {_markdown_escape(focus_group_simulation_plan.get('recommended_group_size', ''))}",
            "",
        ]
        participant_mix = focus_group_simulation_plan.get("participant_mix") if isinstance(focus_group_simulation_plan.get("participant_mix"), list) else []
        if participant_mix:
            lines += ["| Role | Personas | Avg adoption | Objections |", "|---|---|---:|---|"]
            for participant in participant_mix[:5]:
                if not isinstance(participant, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(participant.get("label", participant.get("role", ""))),
                            _markdown_escape(", ".join(_listify(participant.get("personas"))) or "n/a"),
                            _markdown_escape(f"{participant.get('avg_adoption', '-')}/100"),
                            _markdown_escape(", ".join(_listify(participant.get("common_objections"))[:3]) or "n/a"),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        protocol = focus_group_simulation_plan.get("discussion_protocol") if isinstance(focus_group_simulation_plan.get("discussion_protocol"), list) else []
        if protocol:
            lines += ["Protocol:", "", "| Stage | Timebox | Moderator prompt | Capture |", "|---|---:|---|---|"]
            for stage in protocol[:6]:
                if not isinstance(stage, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(stage.get("stage", "")),
                            _markdown_escape(f"{stage.get('timebox_minutes', '-')} min"),
                            _markdown_escape(stage.get("moderator_prompt", "")),
                            _markdown_escape(stage.get("capture", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if focus_group_simulation_plan.get("contrast_questions"):
            lines += [
                "Contrast questions:",
                *_markdown_bullets(focus_group_simulation_plan.get("contrast_questions", []), empty="n/a", limit=5),
                "",
            ]
        if focus_group_simulation_plan.get("interaction_rules"):
            lines += [
                "Interaction rules:",
                *_markdown_bullets(focus_group_simulation_plan.get("interaction_rules", []), empty="n/a", limit=5),
                "",
            ]
        if focus_group_simulation_plan.get("caution"):
            lines += [f"_Caution: {_markdown_escape(focus_group_simulation_plan.get('caution', ''))}_", ""]

    if decision_board:
        lines += [
            "## Decision board",
            "",
            f"- Decision: **{_markdown_escape(decision_board.get('decision', 'Refine'))}** ({_markdown_escape(decision_board.get('confidence', 'low'))} confidence)",
            f"- Rationale: {_markdown_escape(decision_board.get('rationale', ''))}",
            f"- Next step: {_markdown_escape(decision_board.get('next_step', ''))}",
            "- Criteria:",
            *_markdown_bullets(decision_board.get("criteria", []), empty="n/a", limit=10),
            "",
        ]

    if decision_sensitivity:
        adoption_band = decision_sensitivity.get("adoption_band") if isinstance(decision_sensitivity.get("adoption_band"), dict) else {}
        need_fit_band = decision_sensitivity.get("need_fit_band") if isinstance(decision_sensitivity.get("need_fit_band"), dict) else {}
        lines += [
            "## Decision sensitivity guardrail",
            "",
            f"- Risk level: {_markdown_escape(decision_sensitivity.get('risk_level', 'n/a'))}",
            f"- Decision boundary: {_markdown_escape(decision_sensitivity.get('decision_boundary', 'n/a'))}",
            f"- Adoption band: {_markdown_escape(adoption_band.get('range', 'n/a'))} (mean {adoption_band.get('mean', '-')}, ±{adoption_band.get('margin', '-')})",
            f"- Need-fit band: {_markdown_escape(need_fit_band.get('range', 'n/a'))} (mean {need_fit_band.get('mean', '-')}, ±{need_fit_band.get('margin', '-')})",
            f"- Interpretation: {_markdown_escape(decision_sensitivity.get('interpretation', ''))}",
            f"- Recommended action: {_markdown_escape(decision_sensitivity.get('recommended_action', ''))}",
            f"_Note: {_markdown_escape(decision_sensitivity.get('disclaimer', ''))}_",
            "",
        ]

    if validation_plan:
        lines += [
            "## Real-user validation plan",
            "",
            f"- Objective: {_markdown_escape(validation_plan.get('objective', ''))}",
            f"- Recommended sample: {_markdown_escape(validation_plan.get('recommended_sample', ''))}",
            f"- Recruiting focus: {_markdown_escape(validation_plan.get('recruiting_focus', ''))}",
            "- Interview questions:",
            *_markdown_bullets(validation_plan.get("interview_questions", []), empty="n/a", limit=8),
            "- Success criteria:",
            *_markdown_bullets(validation_plan.get("success_criteria", []), empty="n/a", limit=6),
            "",
        ]

    if recruiting_screener:
        lines += [
            "## Recruiting screener pack",
            "",
            f"- Objective: {_markdown_escape(recruiting_screener.get('objective', ''))}",
            f"- Target profile: {_markdown_escape(recruiting_screener.get('target_profile', ''))}",
            f"- Recommended completes: {_markdown_escape(recruiting_screener.get('recommended_completes', ''))}",
            "- Must-have criteria:",
            *_markdown_bullets(recruiting_screener.get("must_have_criteria", []), empty="n/a", limit=5),
            "- Disqualifiers:",
            *_markdown_bullets(recruiting_screener.get("disqualifiers", []), empty="n/a", limit=5),
            "",
        ]
        screener_questions = (
            recruiting_screener.get("screener_questions") if isinstance(recruiting_screener.get("screener_questions"), list) else []
        )
        if screener_questions:
            lines += ["| Question | Accept if | Reject if |", "|---|---|---|"]
            for card in screener_questions[:6]:
                if not isinstance(card, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(card.get("question", "")),
                            _markdown_escape(card.get("accept_if", "")),
                            _markdown_escape(card.get("reject_if", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        quota_cells = recruiting_screener.get("quota_cells") if isinstance(recruiting_screener.get("quota_cells"), list) else []
        if quota_cells:
            lines += ["Quota cells:", "", "| Cell | Target | Minimum | Reason |", "|---|---|---:|---|"]
            for cell in quota_cells[:5]:
                if not isinstance(cell, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(cell.get("cell", "")),
                            _markdown_escape(cell.get("target", "")),
                            _markdown_escape(cell.get("minimum", "")),
                            _markdown_escape(cell.get("reason", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if recruiting_screener.get("incentive_note"):
            lines += [f"- Incentive note: {_markdown_escape(recruiting_screener.get('incentive_note', ''))}", ""]

    if interview_discussion_guide:
        lines += [
            "## Interview discussion guide",
            "",
            f"- Objective: {_markdown_escape(interview_discussion_guide.get('objective', ''))}",
            f"- Session length: {_markdown_escape(interview_discussion_guide.get('session_length', '25-30 minutes'))}",
            f"- Participant profile: {_markdown_escape(interview_discussion_guide.get('participant_profile', ''))}",
            f"- Moderator intro: {_markdown_escape(interview_discussion_guide.get('moderator_intro', ''))}",
            f"- Concept read: {_markdown_escape(interview_discussion_guide.get('concept_read', ''))}",
            "",
        ]
        if interview_discussion_guide.get("warmup_questions"):
            lines += ["Warm-up questions:", *_markdown_bullets(interview_discussion_guide.get("warmup_questions", []), empty="n/a", limit=5), ""]
        tasks = interview_discussion_guide.get("concept_reaction_tasks") if isinstance(interview_discussion_guide.get("concept_reaction_tasks"), list) else []
        if tasks:
            lines += ["| Step | Question | Listen for |", "|---|---|---|"]
            for task in tasks[:6]:
                if not isinstance(task, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(task.get("step", "")),
                            _markdown_escape(task.get("question", "")),
                            _markdown_escape(task.get("what_to_listen_for", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        probes = interview_discussion_guide.get("objection_probes") if isinstance(interview_discussion_guide.get("objection_probes"), list) else []
        if probes:
            lines += ["Objection probes:"]
            lines += _markdown_bullets(
                [f"{probe.get('objection', '우려')}: {probe.get('probe', '')}" for probe in probes if isinstance(probe, dict)],
                empty="n/a",
                limit=5,
            )
            lines.append("")
        if interview_discussion_guide.get("pricing_probe"):
            lines += [f"- Pricing probe: {_markdown_escape(interview_discussion_guide.get('pricing_probe', ''))}", ""]
        if interview_discussion_guide.get("note_taking_rubric"):
            lines += ["Note-taking rubric:", *_markdown_bullets(interview_discussion_guide.get("note_taking_rubric", []), empty="n/a", limit=7), ""]
        if interview_discussion_guide.get("success_signals"):
            lines += ["Success signals:", *_markdown_bullets(interview_discussion_guide.get("success_signals", []), empty="n/a", limit=5), ""]
        if interview_discussion_guide.get("caution"):
            lines += [f"_Caution: {_markdown_escape(interview_discussion_guide.get('caution', ''))}_", ""]

    if validation_survey:
        lines += [
            "## Validation survey instrument",
            "",
            f"- Objective: {_markdown_escape(validation_survey.get('objective', ''))}",
            f"- Estimated length: {_markdown_escape(validation_survey.get('estimated_length', '5-7 minutes'))}",
            f"- Target profile: {_markdown_escape(validation_survey.get('target_profile', ''))}",
            f"- Recommended completes: {_markdown_escape(validation_survey.get('recommended_completes', ''))}",
            "- Primary metrics:",
            *_markdown_bullets(validation_survey.get("primary_metrics", []), empty="n/a", limit=8),
            "",
        ]
        randomization = validation_survey.get("randomization_plan") if isinstance(validation_survey.get("randomization_plan"), dict) else {}
        if randomization:
            lines += [
                f"- Randomization: {_markdown_escape(randomization.get('instruction', ''))}",
                f"- Arms: {_markdown_escape(', '.join(_listify(randomization.get('arms'))) or 'n/a')}",
                "",
            ]
        blocks = validation_survey.get("question_blocks") if isinstance(validation_survey.get("question_blocks"), list) else []
        if blocks:
            lines += ["Survey blocks:", "", "| Block | Purpose | Example question |", "|---|---|---|"]
            for block in blocks[:6]:
                if not isinstance(block, dict):
                    continue
                questions = block.get("questions") if isinstance(block.get("questions"), list) else []
                first_question = questions[0] if questions and isinstance(questions[0], dict) else {}
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(block.get("block", "")),
                            _markdown_escape(block.get("purpose", "")),
                            _markdown_escape(first_question.get("question", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if validation_survey.get("pass_signals"):
            lines += ["Pass signals:", *_markdown_bullets(validation_survey.get("pass_signals", []), empty="n/a", limit=6), ""]
        if validation_survey.get("caution"):
            lines += [f"_Caution: {_markdown_escape(validation_survey.get('caution', ''))}_", ""]

    if field_validation_tracker:
        lines += [
            "## Field validation calibration tracker",
            "",
            f"- Objective: {_markdown_escape(field_validation_tracker.get('objective', ''))}",
            f"- Recommended field sample: {_markdown_escape(field_validation_tracker.get('recommended_field_sample', ''))}",
            f"- Baseline decision: {_markdown_escape(field_validation_tracker.get('baseline_decision', ''))}",
            f"- Evidence level: {_markdown_escape(field_validation_tracker.get('evidence_level', ''))}",
            "",
        ]
        baseline = field_validation_tracker.get("synthetic_baseline") if isinstance(field_validation_tracker.get("synthetic_baseline"), dict) else {}
        if baseline:
            lines += [
                "Synthetic baseline:",
                f"- Personas: {_markdown_escape(baseline.get('persona_count', '-'))}",
                f"- Adoption / need-fit: {_markdown_escape(baseline.get('adoption_score', '-'))}/100 / {_markdown_escape(baseline.get('need_fit_score', '-'))}/100",
                f"- Positive-intent share: {_markdown_escape(baseline.get('positive_intent_share', '-'))}%",
                f"- Top objections: {_markdown_escape(', '.join(_listify(baseline.get('top_objections'))[:5]) or 'n/a')}",
                "",
            ]
        columns = field_validation_tracker.get("field_data_columns") if isinstance(field_validation_tracker.get("field_data_columns"), list) else []
        if columns:
            lines += ["Field data columns:", "", "| Column | Type | Description |", "|---|---|---|"]
            for column in columns[:12]:
                if not isinstance(column, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(column.get("column", "")),
                            _markdown_escape(column.get("type", "")),
                            _markdown_escape(column.get("description", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        metrics = field_validation_tracker.get("comparison_metrics") if isinstance(field_validation_tracker.get("comparison_metrics"), list) else []
        if metrics:
            lines += ["Comparison metrics:", "", "| Metric | Synthetic baseline | Field measure | Alert if |", "|---|---|---|---|"]
            for metric in metrics[:8]:
                if not isinstance(metric, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_escape(metric.get("metric", "")),
                            _markdown_escape(metric.get("synthetic_baseline", "")),
                            _markdown_escape(metric.get("field_measure", "")),
                            _markdown_escape(metric.get("alert_if", "")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        if field_validation_tracker.get("calibration_rules"):
            lines += ["Calibration rules:", *_markdown_bullets(field_validation_tracker.get("calibration_rules", []), empty="n/a", limit=6), ""]
        if field_validation_tracker.get("disclaimer"):
            lines += [f"_Note: {_markdown_escape(field_validation_tracker.get('disclaimer', ''))}_", ""]

    if message_angle_tests:
        lines += ["## Message angle tests", "", "| Angle | Audience | Headline | Evidence to show | Pass signal |", "|---|---|---|---|---|"]
        for angle in message_angle_tests[:5]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(angle.get("label", "Message test")),
                        _markdown_escape(angle.get("audience", "")),
                        _markdown_escape(angle.get("headline", "")),
                        _markdown_escape(angle.get("evidence_to_show", "")),
                        _markdown_escape(angle.get("pass_signal", "")),
                    ]
                )
                + " |"
            )
        lines.append("")

    if experiment_backlog:
        lines += [
            "## Experiment backlog",
            "",
            "| Priority | Experiment | Hypothesis | Pass threshold |",
            "|---|---|---|---|",
        ]
        for experiment in experiment_backlog[:5]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(experiment.get("priority", "P1")),
                        _markdown_escape(experiment.get("experiment", "Learning test")),
                        _markdown_escape(experiment.get("hypothesis", "")),
                        _markdown_escape(experiment.get("pass_threshold", "")),
                    ]
                )
                + " |"
            )
        lines.append("")

    if next_run_brief_variants:
        lines += [
            "## Next-run brief variants",
            "",
            "Use one of these bounded variants for the next Upkinsey simulation instead of rewriting the brief from scratch.",
            "",
        ]
        for variant in next_run_brief_variants[:5]:
            variant_brief = variant.get("brief") if isinstance(variant.get("brief"), dict) else {}
            lines += [
                f"### {_markdown_escape(variant.get('title', variant.get('variant', 'Next-run variant')))}",
                f"- Why: {_markdown_escape(variant.get('why', ''))}",
                f"- Validation focus: {_markdown_escape(variant.get('validation_focus', ''))}",
                f"- Pass signal: {_markdown_escape(variant.get('pass_signal', ''))}",
            ]
            if variant.get("changes"):
                lines += ["- Changes:", *_markdown_bullets(variant.get("changes", []), empty="n/a", limit=5)]
            lines += [
                "- Suggested brief:",
                f"  - Research type: {_markdown_escape(variant_brief.get('research_type', ''))}",
                f"  - Target: {_markdown_escape(variant_brief.get('target_market', ''))}",
                f"  - Hypothesis: {_markdown_escape(variant_brief.get('hypothesis', ''))}",
                f"  - Features: {_markdown_escape(', '.join(_listify(variant_brief.get('features'))[:6]) or 'n/a')}",
                "",
            ]

    if research_sprint:
        lines += [
            "## Research sprint plan",
            "",
            f"- Sprint: {_markdown_escape(research_sprint.get('name', '5-day validation sprint'))}",
            f"- Objective: {_markdown_escape(research_sprint.get('objective', ''))}",
            f"- Recruiting focus: {_markdown_escape(research_sprint.get('recruiting_focus', ''))}",
            f"- Primary experiment: {_markdown_escape(research_sprint.get('primary_experiment', ''))}",
            f"- Decision gate: {_markdown_escape(research_sprint.get('decision_gate', ''))}",
            "",
            "| Day | Focus | Output | Tasks |",
            "|---:|---|---|---|",
        ]
        for day in research_sprint.get("day_plan", [])[:7]:
            task_text = "; ".join(str(task) for task in _listify(day.get("tasks"))[:4])
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(day.get("day", "")),
                        _markdown_escape(day.get("focus", "")),
                        _markdown_escape(day.get("output", "")),
                        _markdown_escape(task_text),
                    ]
                )
                + " |"
            )
        lines.append("")
        if research_sprint.get("stop_conditions"):
            lines += ["Stop conditions:", *_markdown_bullets(research_sprint.get("stop_conditions", []), empty="n/a", limit=5), ""]

    if reactions:
        lines += ["## Persona reaction table", "", "| Persona | Stance | Adoption | Need fit | Main concern |", "|---|---|---:|---:|---|"]
        for reaction in reactions[:12]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(_segment_persona_label(reaction)),
                        _markdown_escape(reaction.get("stance", "")),
                        _markdown_escape(f"{reaction.get('adoption_likelihood', '-')}/100"),
                        _markdown_escape(f"{reaction.get('need_fit_score', '-')}/100"),
                        _markdown_escape(reaction.get("concern", "")),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines += [
        "---",
        "Synthetic pre-research signal only; validate with real users before product or investment decisions.",
    ]
    return "\n".join(lines).rstrip() + "\n"


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
        "model_note": "Upstage Solar Pro 3",
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
        result["report_markdown"] = format_report_markdown(normalized_brief, result)
        result["report"]["markdown"] = result["report_markdown"]
    return result


def _normalize_chat_history(value: Any, *, limit: int = 10) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    history: list[dict[str, str]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "persona"} and content:
            history.append({"role": role, "content": content[:800]})
    return history


def build_persona_chat_prompt(
    brief: dict[str, Any],
    persona_reaction: dict[str, Any],
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Build a grounded follow-up interview prompt for one persona."""

    normalized_brief = validate_brief(brief)
    safe_persona = {
        "name": _first_text(persona_reaction.get("name"), "Persona"),
        "meta": _shorten(persona_reaction.get("meta"), limit=300),
        "stance": _shorten(persona_reaction.get("stance"), limit=80),
        "understanding_score": _as_int(persona_reaction.get("understanding_score"), default=60),
        "need_fit_score": _as_int(persona_reaction.get("need_fit_score"), default=60),
        "adoption_likelihood": _as_int(persona_reaction.get("adoption_likelihood"), default=50),
        "price_resistance": _shorten(persona_reaction.get("price_resistance"), limit=80),
        "concern": _shorten(persona_reaction.get("concern"), limit=800),
        "positive_drivers": _listify(persona_reaction.get("positive_drivers"))[:5],
        "top_risks": _listify(persona_reaction.get("top_risks"))[:5],
        "next_validation_question": _shorten(persona_reaction.get("next_validation_question"), limit=500),
        "used_persona_fields": _listify(persona_reaction.get("used_persona_fields"))[:8],
    }
    return f"""
너는 시장조사 시뮬레이션에서 아래 persona 본인처럼 답한다.
과장된 롤플레이가 아니라, persona 결과와 제품 맥락에 근거해 짧고 현실적으로 답한다.

[PRODUCT BRIEF]
{json.dumps(normalized_brief, ensure_ascii=False, indent=2)}

[PERSONA RESULT]
{json.dumps(safe_persona, ensure_ascii=False, indent=2)}

[RECENT CHAT]
{json.dumps(_normalize_chat_history(history), ensure_ascii=False, indent=2)}

[USER QUESTION]
{user_message[:1200]}

응답 규칙:
- 반드시 1인칭으로 답한다.
- persona 결과와 모순되지 않게 답한다.
- 실제 인터뷰 참여자처럼 자연스럽게 답한다. 보고서 문체나 컨설턴트 문체를 쓰지 않는다.
- 제품팀이 배울 수 있는 구체적 이유/조건을 포함하되, 근거 없는 수치/ROI/성과율은 만들지 않는다.
- 최근 대화에서 이미 말한 내용을 반복하지 말고, 새 조건/증거/상황만 추가한다.
- 질문이 이전 답변을 요약하거나 인용하더라도 그 문장을 따라 쓰지 않는다.
- "판단합니다", "제시된다면", "납득 가능합니다"보다 "저라면", "그 정도면", "아직은" 같은 구어체를 쓴다.
- 질문 하나에만 답한다. 인터뷰어가 묻지 않은 항목까지 보고서처럼 정리하지 않는다.
- 가능하면 최근 경험/현재 방식/망설이는 순간/확인하고 싶은 증거 중 하나를 구체적으로 말한다.
- 1~3문장 이내 한국어로 답한다.
- JSON only.

JSON schema:
{{
  "persona_name": "...",
  "reply": "...",
  "signal": "price | trust | usability | need | message | other",
  "new_information": ["이번 답변에서 새로 나온 사실/조건"],
  "suggested_followup": "..."
}}
""".strip()


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


def _analyst_information_coverage(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Heuristic stop check for iterative analyst interviews.

    The analyst should keep asking until the transcript has practical learning,
    not just a generic one-shot opinion. This local check avoids another model
    call: it looks for reason, condition, evidence, and concrete next-action
    language across persona replies.
    """

    replies = " ".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "persona"
    )
    lowered = replies.lower()
    checks = {
        "situation": _contains_any(lowered, ("최근", "지난", "때", "순간", "상황", "업무", "생활", "프로젝트", "수업", "강의", "현장")),
        "current_alternative": _contains_any(lowered, ("지금", "현재", "기존", "대신", "직접", "수작업", "엑셀", "검색", "주변", "혼자", "따로")),
        "reason": _contains_any(lowered, ("왜", "이유", "때문", "부담", "우려", "불안", "필요", "문제", "걸려", "망설")),
        "condition": _contains_any(lowered, ("조건", "하면", "된다면", "있다면", "먼저", "경우", "전제", "필요", "정도면")),
        "evidence": _contains_any(lowered, ("근거", "샘플", "체험", "무료", "데모", "후기", "검증", "보여", "공개", "확인", "예시", "시연")),
        "price_condition": _contains_any(lowered, ("가격", "비용", "결제", "무료", "환불", "구독", "요금", "만원", "원")),
        "action": _contains_any(lowered, ("사용", "결제", "가입", "신청", "전환", "써볼", "구매", "시도", "볼 것", "해볼")),
    }
    score = sum(1 for ok in checks.values() if ok)
    persona_turns = sum(1 for message in messages if message.get("role") == "persona")
    persona_replies = [
        str(message.get("content") or "").strip()
        for message in messages
        if message.get("role") == "persona" and str(message.get("content") or "").strip()
    ]
    repeated = False
    if len(persona_replies) >= 2:
        prev, last = persona_replies[-2], persona_replies[-1]
        repeated = _normalized_similarity(prev, last) >= 0.78 or _normalized_contains(prev, last)
    enough = persona_turns >= 3 and score >= 5 and len(replies.strip()) >= 180
    missing = [key for key, ok in checks.items() if not ok]
    return {"checks": checks, "score": score, "enough": enough, "repeated": repeated, "missing": missing, "persona_turns": persona_turns}


def _normalized_similarity(left: str, right: str) -> float:
    def clean(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]+", "", value or "").lower()

    a = clean(left)
    b = clean(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _normalized_contains(left: str, right: str) -> bool:
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    a = clean(left)
    b = clean(right)
    if min(len(a), len(b)) < 40:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return short in long


def _short_quote(text: Any, *, limit: int = 90) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[: limit - 1].rstrip() + "…" if len(value) > limit else value


def _probe(phase: str, question: str, goal: str) -> dict[str, str]:
    return {"phase": phase, "question": question, "goal": goal}


def _clean_interview_followup(text: Any) -> str:
    """Return a display-safe interviewer question, or empty if it sounds like an internal prompt."""

    question = re.sub(r"\s+", " ", str(text or "")).strip()
    if not question:
        return ""
    forbidden = (
        "반복하지",
        "새로운 조건",
        "1~3문장",
        "분석 목표",
        "persona",
        "페르소나",
        "정확한 조건",
        "ROI",
        "성과율",
        "보고서",
        "JSON",
    )
    if any(term.lower() in question.lower() for term in forbidden):
        return ""
    if len(question) > 180:
        return ""
    if not question.endswith("?"):
        question += "?"
    return question


def _asked_phases(messages: list[dict[str, str]], question_plan: dict[str, Any]) -> set[str]:
    asked_questions = [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "analyst"
    ]
    phases: set[str] = set()
    for probe in question_plan.get("probe_sequence") or []:
        if not isinstance(probe, dict):
            continue
        question = str(probe.get("question") or "")
        if any(_normalized_similarity(question, asked) >= 0.72 for asked in asked_questions):
            phases.add(str(probe.get("phase") or ""))
    return {phase for phase in phases if phase}


def _select_probe_question(
    question_plan: dict[str, Any],
    messages: list[dict[str, str]],
    missing: list[str],
    *,
    round_number: int,
) -> str:
    phase_by_missing = {
        "situation": "past_behavior",
        "current_alternative": "current_alternative",
        "reason": "barrier",
        "condition": "switching_condition",
        "evidence": "proof",
        "price_condition": "price_condition",
        "action": "next_action",
    }
    probes = [probe for probe in (question_plan.get("probe_sequence") or []) if isinstance(probe, dict)]
    asked = _asked_phases(messages, question_plan)
    preferred_phases = [phase_by_missing[key] for key in missing if key in phase_by_missing]
    preferred_phases += ["past_behavior", "current_alternative", "barrier", "proof", "price_condition", "next_action", "wrap"]
    for phase in preferred_phases:
        if phase in asked:
            continue
        for probe in probes:
            if probe.get("phase") == phase:
                question = _clean_interview_followup(probe.get("question"))
                if question:
                    return question

    # Deterministic fallback for longer interviews: pick the next unasked natural probe.
    for probe in probes[round_number - 1 :] + probes:
        phase = str(probe.get("phase") or "")
        if phase in asked:
            continue
        question = _clean_interview_followup(probe.get("question"))
        if question:
            return question
    return "마지막으로, 이 제품에서 꼭 바뀌었으면 하는 걸 하나만 말해 주세요?"


def build_analyst_question_plan(
    brief: dict[str, Any],
    persona_reaction: dict[str, Any],
    research_question: str,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate the user's research question into persona-facing probes.

    The user question is the analyst's objective, not a script to paste into the
    persona chat. This planner turns it into a primary interview question and a
    small follow-up bank tailored to the persona's current stance/risks.
    """

    normalized = validate_brief(brief)
    question_lower = research_question.lower()
    matched_signals = [
        signal
        for signal, keywords in _question_signal_keywords(research_question).items()
        if any(keyword.lower() in question_lower for keyword in keywords)
    ]
    if not matched_signals:
        matched_signals = ["need"]

    persona_name = _first_text(persona_reaction.get("name"), (target or {}).get("name"), "이 persona")
    stance = _first_text(persona_reaction.get("stance"), "관망형")
    concern = _short_quote(_first_text(persona_reaction.get("concern"), *(_listify(persona_reaction.get("top_risks")) or [""])), limit=120)
    driver = _short_quote(_first_text(*(_listify(persona_reaction.get("positive_drivers")) or [""])), limit=90)

    objective_by_signal = {
        "price": "지불 의향과 가격 저항이 생기는 정확한 조건을 분리",
        "trust": "신뢰를 만들거나 깨는 근거와 proof requirement를 파악",
        "usability": "첫 사용/전환 과정에서 막히는 사용성 장벽을 확인",
        "need": "문제 강도와 현재 대체 행동 대비 실제 필요성을 확인",
        "message": "어떤 설명/표현이 설득 또는 거부감을 만드는지 확인",
        "other": "의사결정에 필요한 구체적 이유와 다음 행동 조건을 확인",
    }
    objective = " / ".join(objective_by_signal.get(signal, objective_by_signal["other"]) for signal in matched_signals[:3])

    if "price" in matched_signals and "trust" in matched_signals:
        primary = (
            "처음 봤을 때 가격이 더 걸리나요, 아니면 믿어도 되는지에 대한 불안이 더 큰가요? "
            "왜 그렇게 느끼는지도 편하게 말해 주세요."
        )
    elif "price" in matched_signals:
        primary = (
            "가격이나 결제 조건을 봤을 때 제일 걸리는 부분이 뭐예요? "
            "결제 전에 뭘 확인하면 마음이 좀 놓일까요?"
        )
    elif "trust" in matched_signals:
        primary = (
            "이걸 믿고 써보려면 먼저 뭐가 보여야 할까요? "
            "반대로 아직 찝찝한 지점도 같이 말해 주세요."
        )
    elif "usability" in matched_signals:
        primary = (
            "처음 써본다고 생각하면 어디서 귀찮거나 어렵게 느껴질 것 같아요? "
            "반대로 어떤 흐름이면 한번 해볼 만하다고 느낄까요?"
        )
    elif "message" in matched_signals:
        primary = (
            "설명을 들었을 때 어떤 부분은 믿음이 가고, 어떤 부분은 과장처럼 느껴져요? "
            "어떻게 말하면 더 자연스러울지도 알려주세요."
        )
    else:
        primary = (
            "이걸 실제로 써볼지 말지 정할 때 제일 먼저 보는 기준이 뭐예요? "
            "지금 하던 방식에서 바꾸려면 어떤 조건이 필요할까요?"
        )

    if persona_name:
        primary = f"{persona_name}님, {primary}"

    probes = [
        _probe("opening", primary, "첫 반응과 가장 큰 장벽 확인"),
        _probe("past_behavior", "비슷한 일이 최근에 있었나요? 그때는 어떻게 해결했어요?", "과거 행동과 실제 맥락 확인"),
        _probe("current_alternative", "지금은 이 문제를 보통 어떻게 해결하고 계세요?", "현재 대체 행동 확인"),
        _probe("barrier", "그중에서 제일 걸리는 걸 하나만 고르면 뭐예요?", "핵심 장벽 좁히기"),
        _probe("proof", "그 불안을 줄이려면 화면이나 설명에서 뭘 먼저 보여주면 좋을까요?", "필요한 신뢰 증거 확인"),
        _probe("switching_condition", "그 정도가 확인되면 지금 하던 방식에서 바꿔볼 마음이 생길까요?", "전환 조건 확인"),
        _probe("next_action", "그게 확인되면 다음에는 뭘 해볼 것 같아요? 가격을 더 보거나, 데모를 보거나, 바로 써보거나요.", "다음 행동 확인"),
        _probe("wrap", "마지막으로, 이 제품에서 꼭 바뀌었으면 하는 걸 하나만 말해 주세요.", "제품 개선 우선순위 확인"),
    ]
    if "message" in matched_signals:
        probes.insert(4, _probe("message_reaction", "어떤 표현은 믿음이 가고, 어떤 표현은 좀 과장처럼 들리나요?", "메시지 반응 확인"))
    if "price" in matched_signals:
        probes.insert(5, _probe("price_condition", "가격이 괜찮다고 느끼려면 어떤 결제 방식이나 체험 조건이 필요할까요?", "지불 장벽 완화 조건 확인"))
    if "trust" in matched_signals:
        probes.insert(5, _probe("proof", "믿어도 되겠다고 느끼려면 후기, 샘플, 검증 자료 중 뭐가 제일 먼저 보여야 할까요?", "신뢰 형성 증거 확인"))

    probe_sequence: list[dict[str, str]] = []
    seen_phases: set[str] = set()
    for probe in probes:
        phase = probe["phase"]
        if phase == "proof" and phase in seen_phases:
            continue
        if phase not in seen_phases:
            probe_sequence.append(probe)
            seen_phases.add(phase)

    return {
        "research_question": research_question.strip(),
        "objective": objective,
        "signals": matched_signals,
        "primary_question": primary[:1200],
        "probe_sequence": probe_sequence[:8],
        "followup_questions": [probe["question"] for probe in probe_sequence[1:6]],
        "persona_context": {
            "name": persona_name,
            "stance": stance,
            "concern": concern,
            "driver": driver,
            "target_reason": (target or {}).get("reason") or "분석 질문에 대한 대표 반응 확인",
        },
    }


def build_custom_analyst_followup(
    brief: dict[str, Any],
    persona_reaction: dict[str, Any],
    question_plan: dict[str, Any],
    messages: list[dict[str, str]],
    last_result: dict[str, Any],
    *,
    round_number: int,
) -> str:
    """Create the next tailored analyst question for a persona transcript."""

    coverage = _analyst_information_coverage(messages)
    missing = coverage.get("missing") or []
    persona_name = _first_text(persona_reaction.get("name"), last_result.get("persona_name"), "이 persona")
    suggested = _short_quote(last_result.get("suggested_followup"), limit=120)
    focus = _select_probe_question(question_plan, messages, list(missing), round_number=round_number)
    suggested_clean = _clean_interview_followup(suggested)
    if suggested_clean and not any(_normalized_similarity(suggested_clean, str(message.get("content") or "")) >= 0.72 for message in messages if message.get("role") == "analyst"):
        # Let the persona's own suggested angle win only when it is short and interview-like.
        if round_number >= 4:
            focus = suggested_clean

    return f"{persona_name}님, {focus}"[:360]


def _question_signal_keywords(question: str) -> dict[str, tuple[str, ...]]:
    return {
        "price": ("가격", "비용", "결제", "구독", "요금", "무료", "비싸", "수수료"),
        "trust": ("신뢰", "믿", "정확", "근거", "검증", "보안", "개인정보", "데이터"),
        "usability": ("사용", "설치", "복잡", "쉽", "온보딩", "귀찮", "불편"),
        "need": ("필요", "문제", "니즈", "쓸", "왜", "대체", "현재", "습관"),
        "message": ("문구", "메시지", "카피", "설명", "랜딩", "광고", "표현", "포지셔닝"),
    }


def _persona_question_match_score(reaction: dict[str, Any], question: str) -> int:
    text = " ".join(
        [
            _first_text(reaction.get("stance")),
            _first_text(reaction.get("concern")),
            " ".join(_listify(reaction.get("positive_drivers"))),
            " ".join(_listify(reaction.get("top_risks"))),
            _first_text(reaction.get("next_validation_question")),
            _first_text(reaction.get("meta")),
        ]
    ).lower()
    question_lower = question.lower()
    score = 0
    for signal, keywords in _question_signal_keywords(question).items():
        if any(keyword in question_lower for keyword in keywords):
            score += 4 * sum(1 for keyword in keywords if keyword in text)
            if signal == "price" and _price_risk_score(reaction.get("price_resistance")) >= 2:
                score += 5
            if signal in {"trust", "usability", "need", "message"} and any(keyword in text for keyword in keywords):
                score += 3
    return score


def select_analyst_target_personas(
    persona_reactions: list[dict[str, Any]],
    question: str,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Choose a small interview panel before the analyst asks a question.

    The analyst layer should not blindly ask every synthetic respondent. It first
    assembles a compact target panel that covers likely supporters, conditional
    adopters, and blockers, while boosting personas whose risks/drivers match the
    question topic.
    """

    if not persona_reactions:
        return []

    limit = max(1, min(limit, len(persona_reactions), 8))
    selected: list[dict[str, Any]] = []
    selected_indices: set[int] = set()

    def add(index: int, reason: str) -> None:
        if index in selected_indices or len(selected) >= limit:
            return
        reaction = persona_reactions[index]
        selected_indices.add(index)
        selected.append(
            {
                "index": index,
                "name": _first_text(reaction.get("name"), f"Persona {index + 1}"),
                "meta": _first_text(reaction.get("meta")),
                "stance": _first_text(reaction.get("stance"), "분석됨"),
                "understanding_score": _as_int(reaction.get("understanding_score"), default=50),
                "adoption_likelihood": _as_int(reaction.get("adoption_likelihood"), default=50),
                "need_fit_score": _as_int(reaction.get("need_fit_score"), default=50),
                "price_resistance": _first_text(reaction.get("price_resistance"), "Medium"),
                "concern": _first_text(reaction.get("concern")),
                "positive_drivers": _listify(reaction.get("positive_drivers"))[:5],
                "top_risks": _listify(reaction.get("top_risks"))[:5],
                "next_validation_question": _first_text(reaction.get("next_validation_question")),
                "used_persona_fields": _listify(reaction.get("used_persona_fields"))[:8],
                "persona_context": reaction.get("persona_context") if isinstance(reaction.get("persona_context"), dict) else None,
                "reason": reason,
            }
        )

    ranked_by_question = sorted(
        range(len(persona_reactions)),
        key=lambda idx: (
            -_persona_question_match_score(persona_reactions[idx], question),
            -_price_risk_score(persona_reactions[idx].get("price_resistance")),
            _as_int(persona_reactions[idx].get("adoption_likelihood"), default=50),
            _segment_persona_label(persona_reactions[idx]),
        ),
    )
    if ranked_by_question and _persona_question_match_score(persona_reactions[ranked_by_question[0]], question) > 0:
        add(ranked_by_question[0], "질문 주제와 가장 직접적으로 연결된 우려/동기가 있는 persona")

    supporters = sorted(
        range(len(persona_reactions)),
        key=lambda idx: (-_as_int(persona_reactions[idx].get("adoption_likelihood"), default=0), _segment_persona_label(persona_reactions[idx])),
    )
    blockers = sorted(
        range(len(persona_reactions)),
        key=lambda idx: (_as_int(persona_reactions[idx].get("adoption_likelihood"), default=100), -_price_risk_score(persona_reactions[idx].get("price_resistance")), _segment_persona_label(persona_reactions[idx])),
    )
    conditionals = sorted(
        [
            idx
            for idx, reaction in enumerate(persona_reactions)
            if 50 <= _as_int(reaction.get("adoption_likelihood"), default=50) <= 69
        ],
        key=lambda idx: (-_price_risk_score(persona_reactions[idx].get("price_resistance")), _segment_persona_label(persona_reactions[idx])),
    )

    if supporters:
        add(supporters[0], "초기 지지자의 구매/사용 조건 확인")
    if conditionals:
        add(conditionals[0], "조건부 전환자의 망설임과 전환 조건 확인")
    if blockers:
        add(blockers[0], "회의적인 persona의 비사용 이유 확인")

    for idx in ranked_by_question:
        add(idx, "질문 주제와 연결된 추가 대조군")
        if len(selected) >= limit:
            break
    return selected


def _synthesize_analyst_interviews(
    question: str,
    conversations: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_labels = {
        "price": "가격/지불 조건",
        "trust": "신뢰/근거",
        "usability": "사용성/진입 장벽",
        "need": "필요성/문제 강도",
        "message": "메시지/표현",
        "other": "기타",
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in conversations:
        signal = str(item.get("signal") or "other")
        grouped.setdefault(signal, []).append(item)

    opinion_groups = []
    for signal, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        label = signal_labels.get(signal, signal)
        personas = [str(item.get("persona_name") or item.get("name") or "Persona") for item in items]
        evidence = [str(item.get("reply") or "").strip() for item in items if str(item.get("reply") or "").strip()]
        first_evidence = evidence[0] if evidence else "추가 확인 필요"
        opinion_groups.append(
            {
                "theme": label,
                "personas": personas,
                "opinion": f"{', '.join(personas)} 쪽에서는 {label} 관점의 의견이 있었다.",
                "evidence": first_evidence,
            }
        )

    summaries = [group["opinion"] for group in opinion_groups[:3]]
    next_questions = unique_top(
        [str(item.get("suggested_followup") or "") for item in conversations],
        limit=4,
    )
    return {
        "summary": " ".join(summaries) if summaries else f"'{question}'에 대해 아직 수합된 persona 의견이 없습니다.",
        "opinion_groups": opinion_groups,
        "recommendations": [
            "보고서 확정 전, 반복 등장한 theme을 실제 인터뷰 probe나 랜딩 메시지 실험으로 옮긴다.",
            "서로 반대되는 persona 의견은 평균 점수로 합치지 말고 조건/세그먼트 차이로 기록한다.",
        ],
        "next_questions": next_questions or ["이 의견이 실제 행동으로 이어지는 조건은 무엇인가요?"],
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
        "model_note": "Upstage Solar Pro 3 persona interviews + local deterministic synthesis",
    }
