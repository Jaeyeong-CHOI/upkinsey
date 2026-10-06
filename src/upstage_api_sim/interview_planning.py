"""Pure persona interview planning, target selection, and local synthesis.

These helpers prepare prompts and choose deterministic follow-up questions from
existing responses. API calls and concurrent interview orchestration deliberately
remain in market_research, which also reexports the previous import surface.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from .research_inputs import (
    _as_int,
    _contains_any,
    _first_text,
    _listify,
    _price_risk_score,
    _segment_persona_label,
    _shorten,
    unique_top,
    validate_brief,
)


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
