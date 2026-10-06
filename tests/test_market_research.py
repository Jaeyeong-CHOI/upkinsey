import json
import threading
import time
import unittest

from upstage_api_sim.market_research import (
    assess_brief_quality,
    analyst_question_personas,
    aggregate_market_research,
    build_assumption_stress_test,
    build_analyst_question_plan,
    build_custom_analyst_followup,
    build_competitive_benchmark,
    build_decision_board,
    build_decision_sensitivity,
    build_evidence_quality,
    build_experiment_backlog,
    build_field_validation_tracker,
    build_focus_group_simulation_plan,
    build_founder_decision_memo,
    build_interview_discussion_guide,
    build_intent_cohort_contrast,
    build_message_angle_tests,
    build_next_run_brief_variants,
    build_persona_evidence_pack,
    build_persona_panel_profile,
    build_pricing_sensitivity,
    build_request_budget,
    build_recruiting_screener,
    build_research_sprint,
    build_research_type_lens,
    build_segment_recommendations,
    build_switching_analysis,
    build_validation_survey,
    build_validation_plan,
    chat_with_persona,
    format_report_markdown,
    infer_persona_filters_from_brief,
    mine_objections,
    normalize_persona_for_prompt,
    persona_meta,
    select_personas_for_brief,
    select_analyst_target_personas,
    simulate_market_research,
    validate_brief,
)
from upstage_api_sim.upstage_client import UpstageClient, UpstageConfig


class FakeClient:
    def __init__(self, delay=0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.calls = []

    def complete_text(self, prompt, **kwargs):
        time.sleep(self.delay)
        name = "Persona"
        for candidate in ["전기태", "김다희", "최은지", "설숙자", "김사장", "박회사", "이학생"]:
            if candidate in prompt:
                name = candidate
                break
        with self.lock:
            self.calls.append(name)
        adoption = {"전기태": 35, "김다희": 78, "최은지": 62, "설숙자": 70}.get(name, 50)
        return json.dumps(
            {
                "name": name,
                "meta": "테스트 메타",
                "stance": "긍정형" if adoption >= 65 else "관망형",
                "understanding_score": 80,
                "need_fit_score": 75,
                "adoption_likelihood": adoption,
                "price_resistance": "Medium",
                "concern": f"{name}의 우려",
                "positive_drivers": ["실용성"],
                "top_risks": ["가격 저항"],
                "next_validation_question": "결제 조건을 확인해야 하는가?",
                "used_persona_fields": ["occupation", "persona"],
            },
            ensure_ascii=False,
        )


class FakeChatClient:
    def __init__(self):
        self.prompt = None

    def complete_text(self, prompt, **kwargs):
        self.prompt = prompt
        return json.dumps(
            {
                "persona_name": "김다희",
                "reply": "저는 가족 식단 조율에 도움이 되면 써볼 수 있지만, 추천 근거가 먼저 보여야 안심될 것 같아요.",
                "signal": "trust",
                "suggested_followup": "추천 근거를 어떤 방식으로 보여주면 신뢰가 생기나요?",
            },
            ensure_ascii=False,
        )


class PartiallyFailingClient(FakeClient):
    def complete_text(self, prompt, **kwargs):
        if "실패 persona" in prompt:
            raise RuntimeError("synthetic upstream 429")
        return super().complete_text(prompt, **kwargs)


class MarketResearchTests(unittest.TestCase):
    def test_assess_brief_quality_flags_missing_research_inputs(self):
        quality = assess_brief_quality({"product_name": "아이디어", "description": "좋은 앱"})

        self.assertEqual(quality["verdict"], "incomplete")
        self.assertLess(quality["score"], 60)
        self.assertIn("가격/과금 옵션", quality["missing_fields"])
        self.assertIn("타깃 고객", quality["missing_fields"])
        self.assertTrue(quality["recommended_questions"])

    def test_assess_brief_quality_accepts_specific_brief(self):
        quality = assess_brief_quality(
            {
                "product_name": "사장님 리뷰비서",
                "description": "카페 사장이 네이버/카카오 리뷰를 직접 확인하고 답글 쓰는 시간을 줄이는 리뷰 관리 SaaS",
                "features": ["리뷰 요약", "답글 초안", "반복 불만 트렌드"],
                "pricing": ["월 19,000원", "무료 체험"],
                "target_market": "리뷰 관리 부담이 큰 30-50대 카페·음식점 소상공인",
                "hypothesis": "답글 시간을 줄인다는 메시지가 명확하면 월 구독 결제 의향이 높아질 것이다.",
            }
        )

        self.assertEqual(quality["verdict"], "ready")
        self.assertGreaterEqual(quality["score"], 80)
        self.assertEqual(quality["missing_fields"], [])

    def test_validate_brief_bounds_sample_size(self):
        brief = validate_brief({"product_name": "x", "sample_size": 9999, "seed": -1, "target_market": "소상공인"})
        self.assertEqual(brief["sample_size"], 500)
        self.assertEqual(brief["seed"], 0)
        self.assertEqual(brief["target_market"], "소상공인")

    def test_validate_brief_preserves_current_alternatives(self):
        brief = validate_brief({"product_name": "x", "current_alternatives": "현재는 네이버 검색과 전화 문의로 해결"})

        self.assertEqual(brief["current_alternatives"], "현재는 네이버 검색과 전화 문의로 해결")

    def test_assess_brief_quality_uses_current_alternatives_as_switching_context(self):
        quality = assess_brief_quality(
            {
                "product_name": "대기없는 병원접수",
                "description": "병원 방문 전에 모바일로 접수하고 예상 대기 시간과 준비 서류를 알려주는 서비스",
                "features": ["사전 접수", "예상 대기 시간", "준비 서류 안내"],
                "pricing": ["무료", "프리미엄 월 2,900원"],
                "target_market": "동네 병원을 자주 방문하는 보호자와 만성질환자",
                "hypothesis": "대기 시간이 줄어든다는 메시지가 명확하면 사용 의향이 높아질 것이다.",
                "current_alternatives": "현재는 병원에 직접 전화하거나 현장에서 대기표를 받고 기다린다.",
            }
        )

        self.assertNotIn("현재 대체 행동/경쟁 대안", quality["missing_fields"])

    def test_validate_brief_normalizes_persona_filters(self):
        brief = validate_brief(
            {
                "product_name": "x",
                "persona_filters": {
                    "occupations": ["카페 사장", "자영업"],
                    "keywords": ["리뷰 관리"],
                    "age_min": 25,
                    "age_max": 70,
                    "panel_limit": 999,
                },
            }
        )

        self.assertEqual(brief["persona_filters"]["occupations"], ["카페 사장", "자영업"])
        self.assertEqual(brief["persona_filters"]["age_min"], 25)
        self.assertEqual(brief["persona_filters"]["age_max"], 70)
        self.assertEqual(brief["persona_filters"]["panel_limit"], 200)

    def test_select_personas_for_brief_prioritizes_target_filters(self):
        personas = [
            {
                "name": "박회사",
                "demographics": {"age": 44, "province": "서울", "occupation": "회사원"},
                "persona": "대기업에서 일하며 리뷰 관리는 하지 않는다.",
            },
            {
                "name": "김사장",
                "demographics": {"age": 39, "province": "부산", "occupation": "카페 사장"},
                "life_domains": {"professional": "동네 카페를 운영하며 리뷰 답글과 반복 불만 관리에 시간을 쓴다."},
            },
            {
                "name": "이학생",
                "demographics": {"age": 21, "province": "대구", "occupation": "대학생"},
                "persona": "리뷰 앱을 자주 본다.",
            },
        ]
        brief = validate_brief(
            {
                "product_name": "사장님 리뷰비서",
                "persona_filters": {
                    "occupations": ["카페", "사장"],
                    "keywords": ["리뷰", "답글"],
                    "age_min": 25,
                    "panel_limit": 1,
                },
            }
        )

        selected = select_personas_for_brief(personas, brief)

        self.assertEqual([persona["name"] for persona in selected], ["김사장"])

    def test_infer_persona_filters_from_target_market(self):
        filters = infer_persona_filters_from_brief(
            {
                "product_name": "사장님 리뷰비서",
                "description": "카페 사장이 네이버/카카오 리뷰 답글 쓰는 시간을 줄이는 SaaS",
                "target_market": "리뷰 관리 부담이 큰 30-50대 카페·음식점 소상공인",
            }
        )

        self.assertIn("카페", filters["occupations"])
        self.assertIn("리뷰", filters["keywords"])
        self.assertEqual(filters["age_min"], 30)
        self.assertEqual(filters["age_max"], 59)

    def test_simulate_market_research_infers_target_filters_for_ui_brief(self):
        personas = [
            {
                "name": "박회사",
                "demographics": {"age": 44, "province": "서울", "occupation": "회사원"},
                "persona": "대기업에서 일하며 리뷰 관리는 하지 않는다.",
            },
            {
                "name": "김사장",
                "demographics": {"age": 39, "province": "부산", "occupation": "카페 사장"},
                "life_domains": {"professional": "동네 카페를 운영하며 리뷰 답글과 반복 불만 관리에 시간을 쓴다."},
            },
            {
                "name": "이학생",
                "demographics": {"age": 21, "province": "대구", "occupation": "대학생"},
                "persona": "리뷰 앱을 자주 본다.",
            },
        ]

        result = simulate_market_research(
            {
                "product_name": "사장님 리뷰비서",
                "description": "카페 사장의 리뷰 답글 시간을 줄이는 서비스",
                "target_market": "리뷰 관리 부담이 큰 30-50대 카페·음식점 소상공인",
            },
            personas=personas,
            client=FakeClient(delay=0),
            max_workers=3,
        )

        self.assertEqual(result["request_plan"]["persona_selection"], "target_filtered")
        self.assertEqual(result["request_plan"]["persona_filter_source"], "inferred_target_market")
        self.assertEqual(result["persona_reactions"][0]["name"], "김사장")
        self.assertEqual(result["panel_profile"]["persona_filter_source"], "inferred_target_market")

    def test_inferred_target_filters_do_not_shrink_requested_sample(self):
        personas = [
            {
                "name": "김보호자",
                "demographics": {"age": 64, "province": "서울", "occupation": "보호자"},
                "persona": "부모님 건강과 복약 알림을 챙긴다.",
            },
            {
                "name": "박시니어",
                "demographics": {"age": 71, "province": "부산", "occupation": "은퇴자"},
                "persona": "고령 반려견과 함께 지낸다.",
            },
            *[
                {
                    "name": f"일반{i}",
                    "demographics": {"age": 25 + i, "province": "경기", "occupation": "회사원"},
                    "persona": "일반 소비자 persona",
                }
                for i in range(28)
            ],
        ]

        result = simulate_market_research(
            {
                "product_name": "강아지 로봇",
                "description": "노인들을 심심하지 않게 해주는 강아지 로봇",
                "target_market": "노인과 보호자",
                "sample_size": 30,
            },
            personas=personas,
            client=FakeClient(delay=0),
            max_workers=8,
        )

        self.assertEqual(result["request_plan"]["persona_selection"], "target_filtered")
        self.assertEqual(result["request_budget"]["requested_sample_size"], 30)
        self.assertEqual(result["request_budget"]["actual_persona_count"], 30)
        self.assertEqual(result["panel_profile"]["selected_persona_count"], 30)
        self.assertEqual(len(result["persona_reactions"]), 30)


    def test_build_interview_discussion_guide_creates_field_ready_script(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "리뷰 답글과 반복 불만을 요약해주는 소상공인 SaaS",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 19,000원"],
            "target_market": "리뷰 관리가 부담인 카페 사장",
            "current_alternatives": "영업 후 직접 리뷰를 확인하고 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "adoption_likelihood": 82,
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["자동 답글이 어색할 수 있음"],
            },
            {
                "name": "박사장",
                "adoption_likelihood": 38,
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["월 구독료 부담"],
            },
        ]
        guide = build_interview_discussion_guide(
            brief,
            reactions,
            validation_plan={
                "objective": "리뷰 관리 문제와 결제 전환 조건 확인",
                "interview_questions": ["어떤 리뷰에 답글을 꼭 달아야 하나요?"],
                "success_criteria": ["3명 이상이 최근 리뷰 관리 시간을 구체적으로 설명"],
            },
            recruiting_screener={"target_profile": "카페·음식점 사장"},
            objections=[{"category": "가격 부담", "objection": "월 구독료가 부담", "suggested_fix": "무료 체험"}],
            switching_analysis={"switching_triggers": ["답글 작성 시간이 절반 이하로 줄어드는 순간"]},
            pricing_sensitivity={"recommended_price_probe": "무료 체험 후 결제 기준 확인"},
            message_angle_tests=[{"headline": "리뷰 답글 시간을 줄이세요", "pass_signal": "무료 체험 신청"}],
        )

        self.assertEqual(guide["participant_profile"], "카페·음식점 사장")
        self.assertIn("사장님 리뷰비서", guide["concept_read"])
        self.assertIn("영업 후 직접 리뷰", guide["warmup_questions"][1])
        self.assertEqual(guide["concept_reaction_tasks"][1]["what_to_listen_for"], "답글 작성 시간이 절반 이하로 줄어드는 순간")
        self.assertIn("월 19,000원", guide["pricing_probe"])
        self.assertIn("월 구독료", guide["objection_probes"][0]["probe"])
        self.assertTrue(any("Synthetic watchlist" in row for row in guide["note_taking_rubric"]))

    def test_build_validation_survey_creates_bounded_quant_survey(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "리뷰 답글과 반복 불만을 요약해주는 소상공인 SaaS",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 19,000원", "14일 무료 체험"],
            "target_market": "리뷰 관리가 부담인 카페 사장",
            "current_alternatives": "영업 후 직접 리뷰를 확인하고 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "adoption_likelihood": 82,
                "need_fit_score": 78,
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["자동 답글이 어색할 수 있음"],
                "next_validation_question": "자동 답글을 믿으려면 어떤 샘플이 필요한가?",
            },
            {
                "name": "박사장",
                "adoption_likelihood": 44,
                "need_fit_score": 55,
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["월 구독료 부담"],
            },
        ]

        survey = build_validation_survey(
            brief,
            reactions,
            validation_plan={"success_criteria": ["5명 중 3명 이상이 리뷰 관리 시간을 구체적으로 설명"]},
            message_angle_tests=[{"headline": "리뷰 답글 시간을 줄이세요", "subcopy": "답글 초안과 반복 불만 요약"}],
            pricing_sensitivity={"recommended_price_probe": "무료 체험 후 결제 기준 확인"},
            switching_analysis={"current_alternatives": "직접 리뷰 확인"},
            evidence_quality={"level": "directional"},
        )

        self.assertEqual(survey["estimated_length"], "5-7 minutes")
        self.assertIn("n=50-80", survey["recommended_completes"])
        self.assertEqual(survey["randomization_plan"]["arms"], ["message_1"])
        self.assertEqual(survey["question_blocks"][0]["questions"][1]["id"], "q_current_alternative")
        self.assertIn("직접 리뷰 확인", survey["question_blocks"][0]["questions"][1]["question"])
        self.assertIn("리뷰 답글 시간을 줄이세요", survey["question_blocks"][1]["questions"][0]["arms"][0]["headline"])
        self.assertIn("무료 체험 후 결제 기준 확인", survey["question_blocks"][3]["questions"][1]["question"])

    def test_build_field_validation_tracker_maps_synthetic_to_field_metrics(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "리뷰 답글과 반복 불만을 요약해주는 소상공인 SaaS",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 19,000원"],
            "target_market": "리뷰 관리가 부담인 카페 사장",
            "current_alternatives": "영업 후 직접 리뷰를 확인하고 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "adoption_likelihood": 82,
                "need_fit_score": 78,
                "price_resistance": "Low",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["자동 답글이 어색할 수 있음"],
            },
            {
                "name": "박사장",
                "adoption_likelihood": 44,
                "need_fit_score": 55,
                "price_resistance": "High",
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["월 구독료 부담"],
            },
        ]

        tracker = build_field_validation_tracker(
            brief,
            reactions,
            validation_survey={
                "recommended_completes": "n=50-80 qualified respondents",
                "randomization_plan": {"arms": ["message_1"]},
                "question_blocks": [
                    {"questions": [{"id": "q_current_alternative"}, {"id": "q_need_fit"}]},
                    {"questions": [{"id": "q_next_action"}]},
                ],
            },
            decision_board={"decision": "Refine"},
            evidence_quality={"level": "directional"},
        )

        self.assertEqual(tracker["recommended_field_sample"], "n=50-80 qualified respondents")
        self.assertEqual(tracker["synthetic_baseline"]["adoption_score"], 63)
        self.assertEqual(tracker["synthetic_baseline"]["positive_intent_share"], 50)
        self.assertIn("q_need_fit", tracker["synthetic_baseline"]["survey_question_ids"])
        self.assertTrue(any(column["column"] == "researcher_decision" for column in tracker["field_data_columns"]))
        self.assertTrue(any(metric["metric"] == "top objection match" for metric in tracker["comparison_metrics"]))
        self.assertTrue(any("field top objection" in rule for rule in tracker["calibration_rules"]))

    def test_simulate_market_research_parallel_persona_calls(self):
        client = FakeClient(delay=0.08)
        start = time.perf_counter()
        result = simulate_market_research({"product_name": "테스트 제품"}, client=client, max_workers=4)
        elapsed = time.perf_counter() - start

        self.assertEqual(len(client.calls), 4)
        # If calls were serial this would be ~0.32s plus overhead; keep threshold loose.
        self.assertLess(elapsed, 0.25)
        self.assertEqual(result["request_plan"]["mode"], "parallel_persona_calls")
        self.assertEqual(result["request_plan"]["estimated_total_model_calls"], 4)
        self.assertEqual(result["request_plan"]["planned_batches"], 1)
        self.assertEqual(result["request_plan"]["persona_selection"], "unfiltered")
        self.assertIn("evidence_quality_score", result["request_plan"])
        self.assertEqual(result["panel_profile"]["selected_persona_count"], 4)
        self.assertEqual(result["panel_profile"], result["report"]["panel_profile"])
        self.assertEqual(result["request_budget"]["solar_persona_calls"], 4)
        self.assertEqual(result["request_budget"]["local_aggregation_calls"], 0)
        self.assertEqual(len(result["persona_reactions"]), 4)
        self.assertIn("adoption_score", result)
        self.assertIn("evidence_quality", result)
        self.assertIn("Persona panel coverage", result["report_markdown"])
        self.assertIn("Run budget & bounds", result["report_markdown"])
        self.assertIn("Evidence quality guardrail", result["report_markdown"])
        self.assertIn("Interview discussion guide", result["report_markdown"])
        self.assertIn("Focus group simulation plan", result["report_markdown"])
        self.assertIn("Field validation calibration tracker", result["report_markdown"])
        self.assertIn("interview_discussion_guide", result["report"])
        self.assertEqual(result["interview_discussion_guide"], result["report"]["interview_discussion_guide"])
        self.assertIn("focus_group_simulation_plan", result["report"])
        self.assertEqual(result["focus_group_simulation_plan"], result["report"]["focus_group_simulation_plan"])
        self.assertIn("field_validation_tracker", result["report"])
        self.assertEqual(result["field_validation_tracker"], result["report"]["field_validation_tracker"])

    def test_simulate_market_research_honors_supplied_panel_size(self):
        personas = [
            {
                "name": f"테스트{i}",
                "demographics": {"age": 30 + i, "province": "서울", "occupation": "테스터"},
                "persona": f"테스트 persona {i}",
            }
            for i in range(25)
        ]
        result = simulate_market_research(
            {"product_name": "테스트 제품", "sample_size": 25},
            client=FakeClient(delay=0),
            personas=personas,
            max_workers=8,
        )

        self.assertEqual(len(result["persona_reactions"]), 25)
        self.assertEqual(result["request_budget"]["actual_persona_count"], 25)
        self.assertEqual(result["request_budget"]["requested_sample_size"], 25)
        self.assertEqual(result["panel_profile"]["selected_persona_count"], 25)

    def test_simulate_market_research_reports_progress(self):
        personas = [
            {"name": f"진행{i}", "demographics": {"age": 30, "occupation": "테스터"}, "persona": "진행 확인"}
            for i in range(5)
        ]
        events = []

        simulate_market_research(
            {"product_name": "진행 테스트", "sample_size": 5},
            client=FakeClient(delay=0),
            personas=personas,
            max_workers=3,
            progress_callback=events.append,
        )

        self.assertTrue(events)
        self.assertEqual(events[0]["completed"], 0)
        self.assertEqual(events[0]["total"], 5)
        self.assertTrue(any(event.get("completed") == 5 for event in events))
        self.assertEqual(events[-1]["stage"], "aggregation")

    def test_simulate_market_research_keeps_partial_successes(self):
        personas = [
            {"name": "성공", "demographics": {"age": 30, "occupation": "테스터"}, "persona": "성공 persona"},
            {"name": "실패", "demographics": {"age": 31, "occupation": "테스터"}, "persona": "실패 persona"},
        ]

        result = simulate_market_research(
            {"product_name": "부분 실패 테스트", "sample_size": 2},
            client=PartiallyFailingClient(delay=0),
            personas=personas,
            max_workers=2,
        )

        self.assertEqual(len(result["persona_reactions"]), 1)
        self.assertEqual(len(result["partial_failures"]), 1)
        self.assertEqual(result["request_budget"]["failed_persona_calls"], 1)
        self.assertEqual(result["request_budget"]["solar_persona_calls"], 2)
        self.assertEqual(result["request_budget"]["estimated_total_model_calls"], 2)
        self.assertEqual(result["request_budget"]["actual_persona_count"], 1)
        self.assertEqual(result["report"]["request_budget"], result["request_budget"])
        self.assertTrue(any("failed" in warning.lower() for warning in result["evidence_quality"]["warnings"]))
        self.assertIn("1 persona API calls failed", result["report_markdown"])
        self.assertEqual(result["report_markdown"], result["report"]["markdown"])
        self.assertIn("not purchase probabilities", result["report_markdown"])
        self.assertIn("/100", result["report"]["executive_summary"])

    def test_build_persona_panel_profile_surfaces_target_coverage(self):
        personas = [
            {"name": "김사장", "demographics": {"age": 39, "province": "부산", "occupation": "카페 사장"}},
            {"name": "이사장", "demographics": {"age": 45, "province": "서울", "occupation": "음식점 사장"}},
            {"name": "박회사", "demographics": {"age": 33, "province": "경기", "occupation": "회사원"}},
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "sample_size": 50,
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "persona_filters": {"occupations": ["사장"], "keywords": ["리뷰"], "age_min": 25, "panel_limit": 2},
        }

        selected = select_personas_for_brief(personas, validate_brief(brief))
        profile = build_persona_panel_profile(brief, selected, source_persona_count=len(personas))

        self.assertEqual(profile["selection_mode"], "target_filtered")
        self.assertEqual(profile["source_persona_count"], 3)
        self.assertEqual(profile["selected_persona_count"], 2)
        self.assertEqual(profile["target_filter_match_count"], 2)
        self.assertEqual(profile["top_provinces"][0]["count"], 1)
        self.assertTrue(any("sample_size" in warning for warning in profile["warnings"]))
        self.assertTrue(profile["recommendations"])

    def test_build_request_budget_exposes_bounded_call_plan(self):
        budget = build_request_budget(
            {"product_name": "테스트", "sample_size": 100},
            persona_count=12,
            max_parallel_requests=5,
        )

        self.assertEqual(budget["mode"], "bounded_parallel_persona_calls")
        self.assertEqual(budget["solar_persona_calls"], 12)
        self.assertEqual(budget["local_aggregation_calls"], 0)
        self.assertEqual(budget["estimated_total_model_calls"], 12)
        self.assertEqual(budget["max_parallel_requests"], 5)
        self.assertEqual(budget["planned_batches"], 3)
        self.assertTrue(any("requested sample_size" in warning for warning in budget["warnings"]))

    def test_simulate_market_research_applies_target_persona_panel_limit(self):
        personas = [
            {"name": "김사장", "demographics": {"age": 39, "occupation": "카페 사장"}},
            {"name": "이학생", "demographics": {"age": 21, "occupation": "대학생"}},
        ]
        result = simulate_market_research(
            {
                "product_name": "사장님 리뷰비서",
                "persona_filters": {"occupations": ["사장"], "age_min": 25, "panel_limit": 1},
            },
            personas=personas,
            client=FakeClient(delay=0.01),
            max_workers=2,
        )

        self.assertEqual(result["request_plan"]["persona_selection"], "target_filtered")
        self.assertEqual(result["request_plan"]["persona_count"], 1)
        self.assertEqual(result["request_plan"]["source_persona_count"], 2)
        self.assertEqual(result["panel_profile"]["selected_persona_count"], 1)
        self.assertEqual(len(result["persona_reactions"]), 1)

    def test_build_founder_decision_memo_summarizes_actionable_next_gate(self):
        reactions = [
            {
                "name": "김사장",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "adoption_likelihood": 72,
                "need_fit_score": 78,
            },
            {
                "name": "박사장",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["AI 답글 품질 신뢰 부족"],
                "adoption_likelihood": 48,
                "need_fit_score": 58,
            },
        ]

        memo = build_founder_decision_memo(
            {"product_name": "사장님 리뷰비서"},
            reactions,
            decision_board={"decision": "Refine", "next_step": "가격 메시지와 답글 품질 증거를 먼저 검증"},
            evidence_quality={"level": "exploratory", "warnings": ["panel이 작아 방향성으로만 해석하세요."]},
            validation_plan={"recruiting_focus": "리뷰 답글 부담이 큰 카페 사장", "success_criteria": ["5명 중 3명 이상이 결제 조건 질문"]},
            experiment_backlog=[{"experiment": "답글 시간 절약 랜딩 smoke test"}],
            decision_sensitivity={"decision_boundary": "borderline_refine", "recommended_action": "10명 인터뷰에서 결제 gate 재확인"},
            assumption_stress_test={"assumptions": [{"falsification_test": "AI 답글 초안을 직접 보여주고 수정 부담을 측정"}]},
        )

        self.assertEqual(memo["decision"], "Refine")
        self.assertIn("adoption 60/100", memo["headline"])
        self.assertEqual(memo["why_it_may_work"], "답글 시간 절약")
        self.assertEqual(memo["primary_kill_risk"], "AI 답글 품질 신뢰 부족")
        self.assertIn("10명 인터뷰", memo["decision_gate"])
        self.assertTrue(any("Kill-risk" in action for action in memo["next_48h_actions"]))
        self.assertIn("panel이 작아", memo["caveat"])

    def test_aggregate_market_research_adds_objection_cards(self):
        reactions = [
            {
                "name": "A",
                "meta": "30대 · 서울",
                "stance": "관망형",
                "concern": "가격 우려",
                "positive_drivers": ["편리함"],
                "top_risks": ["월 구독료 부담", "개인정보 입력 우려"],
                "next_validation_question": "월 구독 가격을 받아들일 수 있는가?",
                "adoption_likelihood": 45,
                "need_fit_score": 55,
            },
            {
                "name": "B",
                "meta": "40대 · 경기",
                "stance": "조건부 긍정",
                "concern": "결제 우려",
                "positive_drivers": ["편리함"],
                "top_risks": ["유료 가격 부담", "추천 정확도 신뢰 부족"],
                "next_validation_question": "유료 결제 조건은 무엇인가?",
                "adoption_likelihood": 60,
                "need_fit_score": 65,
            },
        ]

        result = aggregate_market_research({"product_name": "테스트"}, reactions)
        objections = result["report"]["objections"]

        self.assertEqual(objections[0]["category"], "가격 부담")
        self.assertEqual(objections[0]["count"], 2)
        self.assertIn("suggested_fix", objections[0])
        self.assertEqual(result["report"]["top_risks"], ["월 구독료 부담", "개인정보 입력 우려", "추천 정확도 신뢰 부족"])
        self.assertIn("segment_recommendations", result["report"])
        self.assertIn("intent_cohort_contrast", result["report"])
        self.assertIn("decision_board", result["report"])
        self.assertIn("validation_plan", result["report"])
        self.assertIn("recruiting_screener", result["report"])
        self.assertIn("experiment_backlog", result["report"])
        self.assertIn("pricing_sensitivity", result["report"])
        self.assertIn("research_type_lens", result["report"])
        self.assertIn("persona_evidence_pack", result["report"])
        self.assertIn("competitive_benchmark", result["report"])
        self.assertIn("founder_memo", result["report"])
        self.assertIn("next_48h_actions", result["report"]["founder_memo"])
        self.assertEqual(result["persona_evidence_pack"], result["report"]["persona_evidence_pack"])

    def test_build_competitive_benchmark_maps_alternatives_to_switching_probes(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "current_alternatives": "네이버 리뷰 직접 확인 / 수기 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "adoption_likelihood": 76,
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
            },
            {
                "name": "박사장",
                "adoption_likelihood": 52,
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["AI 답글 품질 신뢰 부족"],
            },
        ]

        benchmark = build_competitive_benchmark(
            brief,
            reactions,
            switching_analysis={
                "current_alternatives": brief["current_alternatives"],
                "switching_triggers": ["답글 작성 시간이 절반 이하로 줄어드는 순간"],
                "must_prove": ["답글 시간 절약"],
            },
            objections=[{"category": "가격 부담", "suggested_fix": "무료 체험과 결제 전 가치 확인"}],
        )

        self.assertEqual(benchmark["primary_barrier"], "가격 부담")
        self.assertEqual([row["alternative"] for row in benchmark["benchmarks"]], ["네이버 리뷰 직접 확인", "수기 답글 작성"])
        self.assertIn("사장님 리뷰비서", benchmark["benchmarks"][0]["product_advantage_to_test"])
        self.assertIn("나란히", benchmark["recommended_next_probe"])

    def test_aggregate_market_research_adds_markdown_report_export(self):
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "stance": "긍정형",
                "concern": "답글 톤과 가격 조건을 확인하고 싶음",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 충분한가?",
                "adoption_likelihood": 74,
                "need_fit_score": 80,
                "price_resistance": "Low-Medium",
            }
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "카페 사장이 리뷰를 요약하고 답글 초안을 빠르게 검토하는 서비스",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 19,000원"],
            "target_market": "리뷰 답글 부담이 큰 카페 사장",
            "hypothesis": "답글 시간이 줄면 구독 의향이 높아질 것이다.",
            "current_alternatives": "현재는 네이버 리뷰를 직접 확인하고 수기로 답글 작성",
        }

        result = aggregate_market_research(brief, reactions)
        markdown = result["report_markdown"]

        self.assertEqual(markdown, result["report"]["markdown"])
        self.assertIn("# Upkinsey Market Insight Report — 사장님 리뷰비서", markdown)
        self.assertIn("## Current alternative & switching triggers", markdown)
        self.assertIn("## Competitive benchmark matrix", markdown)
        self.assertIn("## Pricing sensitivity lab", markdown)
        self.assertIn("## Research type lens", markdown)
        self.assertIn("## Founder decision memo", markdown)
        self.assertIn("## Persona evidence pack", markdown)
        self.assertIn("## Decision board", markdown)
        self.assertIn("## Decision sensitivity guardrail", markdown)
        self.assertIn("## Recruiting screener pack", markdown)
        self.assertIn("## Message angle tests", markdown)
        self.assertIn("## Experiment backlog", markdown)
        self.assertIn("## Intent cohort contrast", markdown)
        self.assertIn("## Focus group simulation plan", markdown)
        self.assertIn("| Persona | Stance | Adoption | Need fit | Main concern |", markdown)
        self.assertIn("Synthetic pre-research signal only", format_report_markdown(brief, result))

    def test_build_pricing_sensitivity_creates_price_ladder(self):
        reactions = [
            {
                "name": "A",
                "meta": "30대 · 서울 · 직장인",
                "adoption_likelihood": 78,
                "need_fit_score": 82,
                "price_resistance": "Low-Medium",
                "top_risks": ["가격 대비 효과 확인 필요"],
                "next_validation_question": "월 9,900원도 납득 가능한가?",
            },
            {
                "name": "B",
                "meta": "40대 · 부산 · 자영업",
                "adoption_likelihood": 58,
                "need_fit_score": 68,
                "price_resistance": "High",
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "무료 체험이 있으면 써볼까?",
            },
        ]
        brief = {"product_name": "테스트", "pricing": ["무료 체험", "월 9,900원", "월 29,000원"]}

        sensitivity = build_pricing_sensitivity(brief, reactions)

        self.assertEqual(sensitivity["price_sensitive_persona_count"], 1)
        self.assertEqual([option["parsed_price_krw"] for option in sensitivity["options"]], [0, 9900, 29000])
        self.assertGreater(
            sensitivity["options"][0]["estimated_adoption_after_friction"],
            sensitivity["options"][-1]["estimated_adoption_after_friction"],
        )
        self.assertIn("무료 체험", sensitivity["recommended_price_probe"])
        self.assertIn("Synthetic pricing sensitivity", sensitivity["disclaimer"])

    def test_build_segment_recommendations_prioritizes_beachhead_then_barriers(self):
        reactions = [
            {
                "name": "A",
                "meta": "30대 · 서울 · 직장인",
                "adoption_likelihood": 82,
                "need_fit_score": 86,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["시간 절약"],
                "top_risks": ["개인정보 입력 우려"],
                "next_validation_question": "무료 체험 후 결제할까?",
            },
            {
                "name": "B",
                "meta": "40대 · 경기 · 자영업",
                "adoption_likelihood": 63,
                "need_fit_score": 70,
                "price_resistance": "High",
                "positive_drivers": ["업무 부담 감소"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "어떤 가격이면 쓸까?",
            },
            {
                "name": "C",
                "meta": "70대 · 부산 · 은퇴",
                "adoption_likelihood": 34,
                "need_fit_score": 45,
                "price_resistance": "Medium",
                "positive_drivers": [],
                "top_risks": ["사용법이 복잡함"],
                "next_validation_question": "앱 설치 없이 쓸 수 있을까?",
            },
        ]

        segments = build_segment_recommendations(reactions)

        self.assertEqual([segment["role"] for segment in segments], ["beachhead", "conditional", "risk-learning"])
        self.assertEqual(segments[0]["segment"], "우선 검증 타깃")
        self.assertEqual(segments[0]["avg_adoption"], 82)
        self.assertIn("A (30대 · 서울 · 직장인)", segments[0]["persona_examples"])
        self.assertEqual(segments[1]["primary_objection"], "월 구독료 부담")
        self.assertIn("비사용 이유", segments[2]["validation_action"])

    def test_build_persona_evidence_pack_selects_supporters_barriers_and_followups(self):
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "stance": "긍정형",
                "adoption_likelihood": 82,
                "need_fit_score": 86,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 충분한가?",
            },
            {
                "name": "박회사",
                "meta": "33세 · 경기 · 회사원",
                "stance": "회의형",
                "adoption_likelihood": 32,
                "need_fit_score": 44,
                "price_resistance": "High",
                "positive_drivers": [],
                "top_risks": ["필요성 낮음", "대체재가 이미 충분함"],
                "next_validation_question": "현재 방식보다 나은 점이 무엇인가?",
            },
        ]

        pack = build_persona_evidence_pack(reactions)

        self.assertIn("대표 반응", pack["summary"])
        self.assertEqual(pack["supporter_cards"][0]["persona"], "김사장 (39세 · 부산 · 카페 사장)")
        self.assertIn("답글 시간 절약", pack["supporter_cards"][0]["signal"])
        self.assertEqual(pack["barrier_cards"][0]["persona"], "박회사 (33세 · 경기 · 회사원)")
        self.assertIn("필요성 낮음", pack["barrier_cards"][0]["interview_probe"])
        self.assertEqual(len(pack["validation_followups"]), 2)
        self.assertIn("not present them as real customer quotes", pack["disclaimer"])

    def test_build_intent_cohort_contrast_explains_high_and_low_intent_gap(self):
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 82,
                "need_fit_score": 86,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약", "반복 불만 파악"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 충분한가?",
            },
            {
                "name": "이사장",
                "meta": "45세 · 서울 · 음식점 사장",
                "adoption_likelihood": 64,
                "need_fit_score": 72,
                "price_resistance": "Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["답글 품질 신뢰 부족"],
            },
            {
                "name": "박회사",
                "meta": "33세 · 경기 · 회사원",
                "adoption_likelihood": 32,
                "need_fit_score": 44,
                "price_resistance": "High",
                "positive_drivers": [],
                "top_risks": ["필요성 낮음", "대체재가 이미 충분함"],
            },
        ]

        contrast = build_intent_cohort_contrast(reactions)

        self.assertEqual(contrast["strongest_cohort"], "high_intent")
        self.assertEqual(contrast["weakest_cohort"], "low_intent")
        self.assertEqual(contrast["adoption_gap"], 50)
        self.assertIn("large intent gap", contrast["summary"])
        self.assertEqual([cohort["cohort"] for cohort in contrast["cohorts"]], ["high_intent", "conditional_intent", "low_intent"])
        self.assertIn("답글 시간 절약", contrast["cohorts"][0]["shared_drivers"])
        self.assertIn("필요성 낮음", contrast["cohorts"][2]["shared_objections"])

    def test_build_focus_group_simulation_plan_balances_supporters_and_skeptics(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "리뷰 답글과 반복 불만을 요약해주는 소상공인 SaaS",
            "target_market": "리뷰 관리가 부담인 카페 사장",
            "current_alternatives": "영업 후 직접 리뷰를 확인하고 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 82,
                "need_fit_score": 86,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
            },
            {
                "name": "이사장",
                "meta": "45세 · 서울 · 음식점 사장",
                "adoption_likelihood": 64,
                "need_fit_score": 72,
                "price_resistance": "Medium",
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["답글 품질 신뢰 부족"],
            },
            {
                "name": "박회사",
                "meta": "33세 · 경기 · 회사원",
                "adoption_likelihood": 32,
                "need_fit_score": 44,
                "price_resistance": "High",
                "positive_drivers": [],
                "top_risks": ["필요성 낮음", "대체재가 이미 충분함"],
            },
        ]

        plan = build_focus_group_simulation_plan(
            brief,
            reactions,
            intent_cohort_contrast=build_intent_cohort_contrast(reactions),
            objections=mine_objections(reactions),
        )

        self.assertIn("사장님 리뷰비서", plan["objective"])
        self.assertEqual([item["role"] for item in plan["participant_mix"]], ["supporter", "conditional", "skeptic"])
        self.assertIn("김사장", plan["participant_mix"][0]["personas"][0])
        self.assertIn("박회사", plan["participant_mix"][2]["personas"][0])
        self.assertEqual(plan["discussion_protocol"][1]["stage"], "Current alternative comparison")
        self.assertIn("action label", " ".join(plan["interaction_rules"]))
        self.assertIn("real_user_probe", plan["capture_template"]["columns"])

    def test_build_decision_board_recommends_segment_pivot_for_beachhead_signal(self):
        reactions = [
            {
                "name": "A",
                "meta": "30대 · 서울 · 직장인",
                "adoption_likelihood": 82,
                "need_fit_score": 84,
                "price_resistance": "Low-Medium",
                "top_risks": ["개인정보 입력 우려"],
            },
            {
                "name": "B",
                "meta": "70대 · 부산 · 은퇴",
                "adoption_likelihood": 34,
                "need_fit_score": 46,
                "price_resistance": "Medium",
                "top_risks": ["사용법이 복잡함"],
            },
        ]
        segments = build_segment_recommendations(reactions)

        board = build_decision_board(reactions, brief_quality={"score": 82}, segments=segments)

        self.assertEqual(board["decision"], "Segment Pivot")
        self.assertIn("beachhead", board["next_step"])
        self.assertIn("Synthetic pre-research", board["disclaimer"])

    def test_build_decision_board_refines_incomplete_brief_before_interpreting_scores(self):
        board = build_decision_board(
            [{"adoption_likelihood": 88, "need_fit_score": 90, "price_resistance": "Low"}],
            brief_quality={"score": 42},
        )

        self.assertEqual(board["decision"], "Refine")
        self.assertEqual(board["confidence"], "low")
        self.assertIn("브리프", board["rationale"])

    def test_build_decision_sensitivity_flags_threshold_crossing(self):
        reactions = [
            {"adoption_likelihood": 66, "need_fit_score": 72},
            {"adoption_likelihood": 71, "need_fit_score": 68},
            {"adoption_likelihood": 74, "need_fit_score": 73},
            {"adoption_likelihood": 62, "need_fit_score": 65},
        ]

        sensitivity = build_decision_sensitivity(
            {"product_name": "테스트", "target_market": "카페 사장"},
            reactions,
            decision_board={"decision": "Refine"},
        )

        self.assertEqual(sensitivity["risk_level"], "high")
        self.assertEqual(sensitivity["decision_boundary"], "threshold_crossing")
        self.assertIn(70, range(sensitivity["adoption_band"]["range"][0], sensitivity["adoption_band"]["range"][1] + 1))
        self.assertIn("no explicit persona filter", sensitivity["panel_note"])
        self.assertIn("not a statistical confidence interval", sensitivity["disclaimer"])

    def test_build_decision_sensitivity_recognizes_robust_go_floor(self):
        reactions = [
            {"adoption_likelihood": 88, "need_fit_score": 84},
            {"adoption_likelihood": 90, "need_fit_score": 86},
            {"adoption_likelihood": 86, "need_fit_score": 82},
            {"adoption_likelihood": 89, "need_fit_score": 85},
            {"adoption_likelihood": 87, "need_fit_score": 84},
            {"adoption_likelihood": 91, "need_fit_score": 87},
            {"adoption_likelihood": 88, "need_fit_score": 83},
            {"adoption_likelihood": 90, "need_fit_score": 86},
        ]

        sensitivity = build_decision_sensitivity(
            {"product_name": "테스트", "persona_filters": {"keywords": ["리뷰"]}},
            reactions,
            decision_board={"decision": "Go"},
        )

        self.assertEqual(sensitivity["risk_level"], "low")
        self.assertEqual(sensitivity["decision_boundary"], "clears_go_thresholds")
        self.assertGreaterEqual(sensitivity["adoption_band"]["range"][0], 70)
        self.assertEqual(sensitivity["panel_note"], "target-filtered panel")

    def test_build_evidence_quality_warns_on_small_unfiltered_panel(self):
        reactions = [
            {"name": "A", "stance": "조건부 긍정", "adoption_likelihood": 76},
            {"name": "B", "stance": "관망형", "adoption_likelihood": 42},
            {"name": "C", "stance": "회의형", "adoption_likelihood": 31},
            {"name": "D", "stance": "긍정형", "adoption_likelihood": 83},
        ]
        brief = {"product_name": "사장님 리뷰비서", "target_market": "리뷰 관리 부담이 큰 카페 사장"}

        quality = build_evidence_quality(brief, reactions, brief_quality={"score": 86})

        self.assertEqual(quality["level"], "exploratory")
        self.assertEqual(quality["confidence"], "low")
        self.assertEqual(quality["persona_count"], 4)
        self.assertEqual(quality["adoption_range"], [31, 83])
        self.assertTrue(any("8명 미만" in warning for warning in quality["warnings"]))
        self.assertTrue(any("panel filter" in warning for warning in quality["warnings"]))
        self.assertTrue(any("20명" in action for action in quality["recommended_actions"]))

    def test_build_evidence_quality_rewards_target_filtered_large_panel(self):
        stances = ["조건부 긍정", "긍정형", "관망형"]
        reactions = [
            {"name": f"P{idx}", "stance": stances[idx % len(stances)], "adoption_likelihood": 64 + (idx % 4)}
            for idx in range(50)
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "persona_filters": {"occupations": ["카페", "사장"], "panel_limit": 50},
        }

        quality = build_evidence_quality(brief, reactions, brief_quality={"score": 90})

        self.assertEqual(quality["level"], "decision_support")
        self.assertEqual(quality["confidence"], "medium")
        self.assertEqual(quality["score"], 100)
        self.assertTrue(any("target segment" in strength for strength in quality["strengths"]))

    def test_build_switching_analysis_turns_current_alternative_into_triggers(self):
        reactions = [
            {
                "name": "A",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 74,
                "need_fit_score": 80,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담", "기존 방식이 익숙함"],
            },
            {
                "name": "B",
                "meta": "45세 · 서울 · 자영업",
                "adoption_likelihood": 58,
                "need_fit_score": 66,
                "price_resistance": "Medium",
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["답글 품질 신뢰 부족"],
            },
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }

        analysis = build_switching_analysis(
            brief,
            reactions,
            objections=[{"category": "가격 부담", "suggested_fix": "무료 체험 후 월 구독 전환 조건 제시"}],
        )

        self.assertEqual(analysis["current_alternatives"], brief["current_alternatives"])
        self.assertTrue(any("답글 시간 절약" in item for item in analysis["switching_triggers"]))
        self.assertTrue(any("네이버/카카오" in item for item in analysis["validation_tests"]))
        self.assertIn("가격 부담", analysis["must_prove"])

    def test_build_validation_plan_turns_synthetic_signals_into_interview_plan(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 76,
                "need_fit_score": 82,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 답글 자동화가 충분한가?",
            }
        ]
        segments = build_segment_recommendations(reactions)
        objections = mine_objections(reactions)
        board = build_decision_board(reactions, brief_quality={"score": 88}, segments=segments, objections=objections)

        plan = build_validation_plan(
            brief,
            reactions,
            segments=segments,
            objections=objections,
            decision_board=board,
            brief_quality={"score": 88},
        )

        self.assertIn("카페 사장", plan["recruiting_focus"])
        self.assertIn("김사장", plan["recruiting_focus"])
        self.assertIn("월 19,000원", plan["interview_questions"][0])
        self.assertTrue(any("월 구독료" in question for question in plan["interview_questions"]))
        self.assertTrue(plan["success_criteria"])

    def test_build_recruiting_screener_operationalizes_fieldwork_selection(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 76,
                "need_fit_score": 82,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 답글 자동화가 충분한가?",
            },
            {
                "name": "이사장",
                "meta": "45세 · 서울 · 음식점 사장",
                "adoption_likelihood": 38,
                "need_fit_score": 55,
                "price_resistance": "High",
                "positive_drivers": ["불만 트렌드 파악"],
                "top_risks": ["가격 부담", "AI 답글 신뢰 부족"],
                "next_validation_question": "AI 답글을 얼마나 수정해야 하는가?",
            },
        ]
        segments = build_segment_recommendations(reactions)
        objections = mine_objections(reactions)
        validation_plan = build_validation_plan(brief, reactions, segments=segments, objections=objections)

        screener = build_recruiting_screener(
            brief,
            reactions,
            validation_plan=validation_plan,
            segments=segments,
            objections=objections,
            intent_cohort_contrast=build_intent_cohort_contrast(reactions),
            evidence_quality={"level": "directional"},
        )

        self.assertIn("리뷰 관리 부담", screener["target_profile"])
        self.assertIn("6-8명", screener["recommended_completes"])
        self.assertTrue(any("네이버/카카오" in item for item in screener["must_have_criteria"]))
        self.assertEqual(screener["screener_questions"][0]["source_signal"], "problem_recency")
        self.assertTrue(any(cell["cell"] == "objection_heavy_or_low_intent" for cell in screener["quota_cells"]))
        self.assertTrue(screener["routing_rules"])

    def test_build_experiment_backlog_prioritizes_actionable_learning_tests(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        backlog = build_experiment_backlog(
            brief,
            segments=[{"segment": "우선 검증 타깃", "primary_driver": "답글 시간 절약"}],
            objections=[{"category": "가격 부담", "suggested_fix": "무료 체험 후 월 구독 전환 조건 제시"}],
            decision_board={"decision": "Refine"},
            switching_analysis={"current_alternatives": brief["current_alternatives"]},
            validation_plan={"recruiting_focus": "우선 검증 타깃: 리뷰 관리 부담이 큰 카페 사장"},
            evidence_quality={"level": "directional"},
        )

        self.assertEqual(len(backlog), 3)
        self.assertEqual(backlog[0]["experiment"], "핵심 가치 메시지 smoke test")
        self.assertEqual(backlog[1]["priority"], "P0")
        self.assertIn("답글 시간 절약", backlog[0]["hypothesis"])
        self.assertIn("무료 체험", backlog[1]["hypothesis"])
        self.assertIn("네이버/카카오", backlog[2]["hypothesis"])

    def test_build_message_angle_tests_turns_signals_into_copy_cards(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 76,
                "need_fit_score": 82,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 답글 자동화가 충분한가?",
            }
        ]

        cards = build_message_angle_tests(
            brief,
            reactions,
            segments=[{"segment": "우선 검증 타깃", "primary_driver": "답글 시간 절약", "persona_examples": ["김사장 (39세 · 부산 · 카페 사장)"]}],
            objections=[{"category": "가격 부담", "objection": "월 구독료 부담", "suggested_fix": "무료 체험 후 월 구독 전환 조건 제시"}],
            switching_analysis={"current_alternatives": brief["current_alternatives"], "switching_triggers": ["답글 시간 절약"]},
        )

        self.assertEqual([card["angle"] for card in cards], ["core-value", "objection-reversal", "switching-trigger"])
        self.assertIn("답글 시간 절약", cards[0]["headline"])
        self.assertIn("무료 체험", cards[1]["subcopy"])
        self.assertIn("네이버/카카오", cards[2]["headline"])
        self.assertIn("25%", cards[0]["pass_signal"])

    def test_build_research_type_lens_focuses_selected_study_mode(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "research_type": "Message test",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 76,
                "need_fit_score": 82,
                "price_resistance": "Low-Medium",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
            }
        ]
        messages = build_message_angle_tests(brief, reactions)

        lens = build_research_type_lens(brief, reactions, message_angle_tests=messages)

        self.assertEqual(lens["lens"], "Message test lens")
        self.assertEqual(lens["primary_output"], "Message angle tests")
        self.assertIn("답글 시간 절약", lens["interpretation"])
        self.assertIn("message_angle_tests", lens["supporting_sections"])

    def test_build_research_type_lens_routes_pricing_mode_to_price_probe(self):
        brief = {"product_name": "테스트", "research_type": "Pricing test", "pricing": ["무료", "월 19,000원"]}
        reactions = [
            {
                "name": "A",
                "adoption_likelihood": 52,
                "need_fit_score": 70,
                "price_resistance": "High",
                "top_risks": ["월 구독료 부담"],
            }
        ]
        pricing = build_pricing_sensitivity(brief, reactions)

        lens = build_research_type_lens(brief, reactions, pricing_sensitivity=pricing)

        self.assertEqual(lens["lens"], "Pricing sensitivity lens")
        self.assertEqual(lens["primary_output"], "Pricing sensitivity lab")
        self.assertIn("검증", lens["recommended_next_action"])

    def test_build_assumption_stress_test_prioritizes_falsifiable_risks(self):
        brief = {
            "product_name": "사장님 리뷰비서",
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "pricing": ["월 39,000원"],
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "adoption_likelihood": 38,
                "need_fit_score": 78,
                "price_resistance": "High",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담", "답글 품질 신뢰 부족"],
            },
            {
                "name": "이사장",
                "meta": "45세 · 서울 · 음식점 사장",
                "adoption_likelihood": 48,
                "need_fit_score": 73,
                "price_resistance": "Medium",
                "positive_drivers": ["반복 불만 파악"],
                "top_risks": ["가격 대비 효과 확인 필요"],
            },
        ]
        objections = mine_objections(reactions)
        switching = build_switching_analysis(brief, reactions, objections=objections)
        pricing = build_pricing_sensitivity(brief, reactions)

        stress = build_assumption_stress_test(
            brief,
            reactions,
            objections=objections,
            switching_analysis=switching,
            pricing_sensitivity=pricing,
        )

        self.assertEqual(stress["overall_risk"], "High")
        self.assertEqual(stress["assumptions"][0]["area"], "value")
        self.assertIn("falsification_test", stress["assumptions"][0])
        self.assertTrue(any(card["area"] == "pricing" and card["risk_level"] == "High" for card in stress["assumptions"]))
        self.assertTrue(stress["watchouts"])

    def test_aggregate_market_research_includes_assumption_stress_test_in_markdown(self):
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "stance": "조건부 긍정",
                "concern": "가격과 답글 품질을 확인하고 싶음",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 39,000원도 납득 가능한가?",
                "adoption_likelihood": 52,
                "need_fit_score": 78,
                "price_resistance": "High",
            }
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "소상공인 리뷰를 자동 요약하고 답글 초안을 작성하는 SaaS",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 39,000원"],
            "target_market": "리뷰 관리 부담이 큰 카페 사장",
            "hypothesis": "답글 시간을 줄이면 구독 의향이 높아질 것이다.",
            "current_alternatives": "네이버/카카오 리뷰를 직접 확인하고 수기로 답글 작성",
        }

        result = aggregate_market_research(brief, reactions)

        self.assertIn("assumption_stress_test", result["report"])
        self.assertIn("## Assumption stress test", result["report_markdown"])
        self.assertIn("Falsification test", result["report_markdown"])

    def test_build_research_sprint_turns_decision_into_week_plan(self):
        sprint = build_research_sprint(
            {
                "product_name": "사장님 리뷰비서",
                "target_market": "리뷰 관리 부담이 큰 카페 사장",
                "current_alternatives": "네이버 리뷰를 직접 확인하고 수기로 답글 작성",
            },
            decision_board={"decision": "Segment Pivot", "confidence": "medium"},
            validation_plan={
                "recommended_sample": "5-8명 카페 사장 인터뷰",
                "recruiting_focus": "최근 한 달 리뷰 답글을 직접 쓴 카페 사장",
            },
            experiment_backlog=[
                {
                    "experiment": "답글 시간 절약 메시지 smoke test",
                    "pass_threshold": "5명 중 3명이 샘플 리뷰로 답글 초안을 요청",
                }
            ],
            assumption_stress_test={
                "assumptions": [
                    {
                        "area": "value",
                        "falsification_test": "현재 답글 작성 시간을 먼저 측정한다",
                    }
                ]
            },
        )

        self.assertEqual(sprint["name"], "5-day validation sprint")
        self.assertEqual(sprint["decision_context"], "Segment Pivot")
        self.assertEqual(len(sprint["day_plan"]), 5)
        self.assertIn("beachhead segment", sprint["objective"])
        self.assertIn("카페 사장", sprint["day_plan"][0]["tasks"][1])
        self.assertIn("답글 시간 절약 메시지 smoke test", sprint["primary_experiment"])
        self.assertIn("타깃을 좁혀", sprint["decision_gate"])

    def test_build_next_run_brief_variants_creates_rerun_ready_briefs(self):
        variants = build_next_run_brief_variants(
            {
                "product_name": "사장님 리뷰비서",
                "description": "카페 사장이 리뷰를 요약하고 답글 초안을 빠르게 검토하는 서비스",
                "features": ["리뷰 요약", "답글 초안"],
                "pricing": ["월 19,000원"],
                "target_market": "리뷰 답글 부담이 큰 카페 사장",
                "current_alternatives": "네이버 리뷰를 직접 확인하고 수기로 답글 작성",
                "hypothesis": "답글 시간이 줄면 구독 의향이 높아질 것이다.",
            },
            decision_board={"decision": "Refine"},
            segments=[{"segment": "조건부 전환 타깃", "primary_driver": "답글 시간 절약"}],
            objections=[{"category": "가격 부담", "suggested_fix": "무료 체험과 월간 ROI 예시 제시"}],
            switching_analysis={"current_alternatives": "네이버 리뷰를 직접 확인하고 수기로 답글 작성"},
            pricing_sensitivity={"recommended_price_probe": "월 19,000원 조건으로 결제 의향 확인"},
            message_angle_tests=[{"headline": "리뷰 답글 시간을 절반으로 줄이세요"}],
        )

        self.assertEqual([variant["variant"] for variant in variants], ["objection_reducer", "switching_trigger", "beachhead_segment"])
        self.assertIn("가격 부담", variants[0]["title"])
        self.assertEqual(variants[0]["brief"]["research_type"], "Objection mining")
        self.assertIn("무료 체험", variants[0]["brief"]["hypothesis"])
        self.assertIn("네이버 리뷰", variants[1]["brief"]["current_alternatives"])
        self.assertIn("답글 시간 절약", variants[2]["brief"]["hypothesis"])

    def test_aggregate_market_research_includes_research_sprint_in_report_and_markdown(self):
        reactions = [
            {
                "name": "김사장",
                "meta": "39세 · 부산 · 카페 사장",
                "stance": "조건부 긍정",
                "concern": "답글 톤과 가격 조건을 확인하고 싶음",
                "positive_drivers": ["답글 시간 절약"],
                "top_risks": ["월 구독료 부담"],
                "next_validation_question": "월 19,000원에 충분한가?",
                "adoption_likelihood": 74,
                "need_fit_score": 80,
                "price_resistance": "Low-Medium",
            }
        ]
        brief = {
            "product_name": "사장님 리뷰비서",
            "description": "카페 사장이 리뷰를 요약하고 답글 초안을 빠르게 검토하는 서비스",
            "features": ["리뷰 요약", "답글 초안"],
            "pricing": ["월 19,000원"],
            "target_market": "리뷰 답글 부담이 큰 카페 사장",
            "hypothesis": "답글 시간이 줄면 구독 의향이 높아질 것이다.",
            "current_alternatives": "현재는 네이버 리뷰를 직접 확인하고 수기로 답글 작성",
        }

        result = aggregate_market_research(brief, reactions)
        sprint = result["report"]["research_sprint"]

        self.assertEqual(sprint["name"], "5-day validation sprint")
        self.assertEqual(len(sprint["day_plan"]), 5)
        self.assertIn("primary_experiment", sprint)
        self.assertIn("next_run_brief_variants", result["report"])
        self.assertEqual(len(result["report"]["next_run_brief_variants"]), 3)
        self.assertIn("## Next-run brief variants", result["report_markdown"])
        self.assertIn("## Research sprint plan", result["report_markdown"])
        self.assertIn("| Day | Focus | Output | Tasks |", result["report_markdown"])
        self.assertIn("Stop conditions", result["report_markdown"])

    def test_normalize_nemotron_persona_for_prompt_flattens_nested_fields(self):
        persona = {
            "dataset_id": "nvidia/Nemotron-Personas-Korea",
            "uuid": "abc",
            "name": "박민수",
            "persona": "박민수 씨는 실용적인 소비를 중시합니다.",
            "demographics": {"age": 38, "province": "대전", "occupation": "카페 사장"},
            "life_domains": {"family": "가족과 매장 운영 시간을 조율합니다.", "professional": "동네 카페를 운영합니다."},
            "capabilities": {"skills_list": ["고객 응대", "매장 운영"]},
            "interests": {"hobbies_list": ["커피", "동네 모임"]},
            "goals": "반복 업무 시간을 줄이고 싶어합니다.",
            "name_parse": {"confidence": 0.8},
        }

        normalized = normalize_persona_for_prompt(persona)

        self.assertEqual(persona_meta(persona), "38세 · 대전 · 카페 사장")
        self.assertEqual(normalized["name"], "박민수")
        self.assertEqual(normalized["occupation"], "카페 사장")
        self.assertEqual(normalized["family_context"], "가족과 매장 운영 시간을 조율합니다.")
        self.assertEqual(normalized["source"]["uuid"], "abc")
        self.assertEqual(normalized["source"]["name_parse_confidence"], 0.8)

    def test_persona_reactions_include_prompt_persona_context(self):
        personas = [
            {
                "name": "김다희",
                "demographics": {"age": 34, "province": "서울", "occupation": "직장인"},
                "persona": "퇴근 후 부모님 건강과 식단을 챙긴다.",
                "life_domains": {
                    "family": "부모님과 주말마다 식사한다.",
                    "professional": "마케팅팀에서 일하며 모바일 서비스를 자주 쓴다.",
                },
                "interests": {"hobbies_list": ["요리", "러닝"]},
                "capabilities": {"skills_list": ["엑셀", "운전"]},
                "goals": "부모님 건강 관리를 더 쉽게 하고 싶다.",
                "uuid": "persona-uuid-1",
                "unused_private_field": "result에 노출되면 안 됨",
            }
        ]

        result = simulate_market_research(
            {"product_name": "건강 식단 앱", "sample_size": 1},
            personas=personas,
            client=FakeClient(delay=0),
            max_workers=1,
        )

        context = result["persona_reactions"][0]["persona_context"]
        self.assertEqual(context["name"], "김다희")
        self.assertEqual(context["occupation"], "직장인")
        self.assertIn("부모님", context["family_context"])
        self.assertEqual(context["interests"], ["요리", "러닝"])
        self.assertEqual(context["source"]["uuid"], "persona-uuid-1")
        self.assertNotIn("unused_private_field", context)

    def test_chat_with_persona_returns_grounded_reply(self):
        client = FakeChatClient()
        result = chat_with_persona(
            {"product_name": "AI 식단 코치 앱", "pricing": ["월 4,900원"]},
            {
                "name": "김다희",
                "meta": "46세 · 경기 · 가정 내 의사결정자",
                "stance": "조건부 긍정",
                "adoption_likelihood": 62,
                "need_fit_score": 70,
                "price_resistance": "Medium",
                "concern": "추천 정확도와 데이터 관리 방식을 확인하고 싶어함",
            },
            "이 가격이면 왜 망설이나요?",
            history=[{"role": "user", "content": "첫 반응은 어때요?"}],
            client=client,
        )

        self.assertEqual(result["persona_name"], "김다희")
        self.assertEqual(result["signal"], "trust")
        self.assertIn("추천 근거", result["reply"])
        self.assertIn("[USER QUESTION]", client.prompt)

    def test_select_analyst_target_personas_covers_question_and_intent_range(self):
        reactions = [
            {"name": "지지자", "adoption_likelihood": 84, "need_fit_score": 80, "price_resistance": "Low", "positive_drivers": ["시간 절약"], "top_risks": []},
            {"name": "조건부", "adoption_likelihood": 61, "need_fit_score": 68, "price_resistance": "Medium", "top_risks": ["가격 부담"]},
            {"name": "회의자", "adoption_likelihood": 32, "need_fit_score": 40, "price_resistance": "High", "top_risks": ["구독 가격이 부담"]},
            {"name": "신뢰우려", "adoption_likelihood": 55, "need_fit_score": 62, "price_resistance": "Low", "top_risks": ["추천 근거를 믿기 어려움"]},
        ]

        targets = select_analyst_target_personas(reactions, "가격이 제일 큰 장벽인지 물어봐줘", limit=4)

        self.assertEqual(len(targets), 4)
        self.assertIn("회의자", [target["name"] for target in targets])
        self.assertIn("지지자", [target["name"] for target in targets])
        self.assertTrue(any("먼저" in target["reason"] or "조건" in target["reason"] for target in targets))

    def test_analyst_question_personas_returns_targets_transcripts_and_synthesis(self):
        client = FakeChatClient()
        user_question = "가격보다 신뢰가 더 큰 장벽인지 물어봐줘"
        reactions = [
            {"name": "김다희", "meta": "46세 · 경기", "stance": "조건부 긍정", "understanding_score": 81, "adoption_likelihood": 62, "need_fit_score": 70, "price_resistance": "Medium", "concern": "추천 근거 확인 필요", "top_risks": ["추천 근거 부족"], "used_persona_fields": ["family_context", "goals"], "persona_context": {"name": "김다희", "age": 46, "province": "경기", "occupation": "보호자", "family_context": "부모님 식단을 챙긴다."}},
            {"name": "박회의", "meta": "38세 · 서울", "stance": "회의형", "adoption_likelihood": 35, "need_fit_score": 44, "price_resistance": "High", "concern": "월 구독료 부담", "top_risks": ["가격 저항"]},
        ]

        result = analyst_question_personas(
            {"product_name": "AI 식단 코치 앱", "target_market": "가족 식단을 챙기는 보호자"},
            reactions,
            user_question,
            client=client,
            target_limit=2,
            max_workers=2,
        )

        self.assertEqual(result["question"], user_question)
        self.assertEqual(len(result["target_personas"]), 2)
        self.assertIn("concern", result["target_personas"][0])
        self.assertIn("top_risks", result["target_personas"][0])
        self.assertIn("understanding_score", result["target_personas"][0])
        self.assertTrue(any("family_context" in target.get("used_persona_fields", []) for target in result["target_personas"]))
        self.assertTrue(any((target.get("persona_context") or {}).get("occupation") == "보호자" for target in result["target_personas"]))
        self.assertEqual(len(result["conversations"]), 2)
        self.assertEqual(result["conversations"][0]["messages"][0]["role"], "analyst")
        self.assertEqual(result["conversations"][0]["messages"][1]["role"], "persona")
        self.assertNotEqual(result["conversations"][0]["messages"][0]["content"], user_question)
        self.assertIn("question_plan", result["conversations"][0])
        self.assertIn("primary_question", result["conversations"][0]["question_plan"])
        self.assertIn("probe_sequence", result["conversations"][0]["question_plan"])
        self.assertTrue(result["conversations"][0]["generated_questions"])
        self.assertGreaterEqual(result["conversations"][0]["round_count"], 2)
        self.assertLessEqual(result["conversations"][0]["round_count"], 5)
        self.assertNotIn("앞서", result["conversations"][0]["messages"][2]["content"])
        self.assertNotIn("원래 질문", result["conversations"][0]["messages"][2]["content"])
        self.assertNotIn("반복하지 말고", result["conversations"][0]["messages"][2]["content"])
        self.assertNotIn("새로운 조건", result["conversations"][0]["messages"][2]["content"])
        self.assertLessEqual(len(result["conversations"][0]["messages"][2]["content"]), 360)
        self.assertIn(result["conversations"][0]["stop_reason"], {"enough_information", "repeated_answer", "max_rounds"})
        self.assertIn("의견", result["synthesis"]["summary"])
        self.assertTrue(result["synthesis"]["opinion_groups"])

    def test_build_analyst_question_plan_rewrites_user_question_into_probe(self):
        user_question = "가격보다 신뢰가 더 큰 장벽인지 물어봐줘"
        plan = build_analyst_question_plan(
            {"product_name": "AI 식단 코치 앱", "pricing": ["월 4,900원"]},
            {
                "name": "김다희",
                "stance": "조건부 긍정",
                "concern": "추천 근거와 개인정보 관리가 먼저 확인되어야 함",
                "positive_drivers": ["가족 식단 조율"],
                "top_risks": ["신뢰 부족"],
            },
            user_question,
        )

        self.assertEqual(plan["research_question"], user_question)
        self.assertIn("price", plan["signals"])
        self.assertIn("trust", plan["signals"])
        self.assertNotEqual(plan["primary_question"], user_question)
        self.assertIn("가격", plan["primary_question"])
        self.assertIn("믿", plan["primary_question"])
        self.assertTrue(plan["followup_questions"])
        phases = [probe["phase"] for probe in plan["probe_sequence"]]
        self.assertIn("past_behavior", phases)
        self.assertIn("current_alternative", phases)
        self.assertIn("proof", phases)
        self.assertIn("price_condition", phases)

    def test_build_custom_analyst_followup_uses_interview_ladder(self):
        plan = build_analyst_question_plan(
            {"product_name": "AI 식단 코치 앱", "pricing": ["월 4,900원"]},
            {"name": "김다희", "stance": "조건부 긍정", "concern": "추천 근거 부족"},
            "가격과 신뢰를 더 물어봐줘",
        )
        first_followup = build_custom_analyst_followup(
            {"product_name": "AI 식단 코치 앱"},
            {"name": "김다희", "concern": "추천 근거 부족"},
            plan,
            [
                {"role": "analyst", "content": plan["primary_question"]},
                {"role": "persona", "content": "가격도 조금 부담이고, 추천을 믿어도 되는지 아직 잘 모르겠어요."},
            ],
            {"persona_name": "김다희", "reply": "가격도 조금 부담이고, 추천을 믿어도 되는지 아직 잘 모르겠어요."},
            round_number=2,
        )
        self.assertIn("최근", first_followup)
        self.assertNotIn("반복하지", first_followup)
        self.assertNotIn("분석 목표", first_followup)

        second_followup = build_custom_analyst_followup(
            {"product_name": "AI 식단 코치 앱"},
            {"name": "김다희", "concern": "추천 근거 부족"},
            plan,
            [
                {"role": "analyst", "content": plan["primary_question"]},
                {"role": "persona", "content": "가격도 조금 부담이고, 추천을 믿어도 되는지 아직 잘 모르겠어요."},
                {"role": "analyst", "content": first_followup},
                {"role": "persona", "content": "최근 부모님 식단을 정할 때마다 검색을 오래 했고, 그때 정확한 추천인지 불안했어요."},
            ],
            {"persona_name": "김다희", "reply": "최근 부모님 식단을 정할 때마다 검색을 오래 했고, 그때 정확한 추천인지 불안했어요."},
            round_number=3,
        )
        self.assertIn("지금", second_followup)
        self.assertNotEqual(first_followup, second_followup)


class UpstagePayloadValidationTests(unittest.TestCase):
    def test_build_payload_validates_messages(self):
        client = UpstageClient(UpstageConfig(api_key="test", max_retries=0))
        with self.assertRaises(ValueError):
            client.build_payload([])
        with self.assertRaises(ValueError):
            client.build_payload([{"role": "hacker", "content": "x"}])
        payload = client.build_payload([{"role": "user", "content": "hello"}], max_tokens=12)
        self.assertEqual(payload["model"], "solar-pro3")
        self.assertEqual(payload["max_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
