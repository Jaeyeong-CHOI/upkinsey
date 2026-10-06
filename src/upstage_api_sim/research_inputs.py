"""Pure brief/persona normalization and shared research signal coercion.

This leaf module owns input bounds, compact persona context, panel ranking, and
stable text/numeric helpers. It must not depend on orchestration, report rendering,
or API clients so these operations remain usable offline and independently.
"""
from __future__ import annotations

import json
import re
from typing import Any


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


PRICE_RISK_SCORE = {"Low": 0, "Low-Medium": 1, "Medium": 2, "High": 3}


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


def _price_risk_score(label: Any) -> int:
    return PRICE_RISK_SCORE.get(str(label), 2)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _segment_persona_label(reaction: dict[str, Any]) -> str:
    name = _first_text(reaction.get("name"), "Persona")
    meta = _first_text(reaction.get("meta"))
    return f"{name} ({meta})" if meta else name


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
