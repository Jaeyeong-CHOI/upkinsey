"""Deterministic Markdown export for the canonical research result schema.

Rendering consumes data only: it never calls a model, samples personas, or writes
files. The legacy market_research module reexports the formatter for compatibility.
"""
from __future__ import annotations

from typing import Any

from .research_inputs import (
    _listify,
    _segment_persona_label,
    validate_brief,
)


def _markdown_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _markdown_bullets(items: list[Any], *, empty: str = "n/a", limit: int = 6) -> list[str]:
    values = [str(item).strip() for item in items if str(item).strip()][:limit]
    if not values:
        values = [empty]
    return [f"- {_markdown_escape(item)}" for item in values]


def _provenance_markdown(provenance: dict[str, Any]) -> list[str]:
    """Render only audit fields; never serialize arbitrary configuration."""

    lines = ["## Run provenance", ""]
    for key, label in (
        ("schema_version", "Provenance schema"),
        ("application_version", "Application version"),
        ("model", "Model"),
        ("started_at", "Started at"),
        ("duration_seconds", "Duration (seconds)"),
        ("requested_seed", "Requested seed"),
        ("sampling_seed", "Sampling seed"),
        ("observed_sampling_seeds", "Observed panel seeds"),
        ("sampling_seed_missing_count", "Personas without a recorded seed"),
        ("source_persona_count", "Source personas"),
        ("selected_persona_count", "Selected personas"),
        ("panel_sha256", "Panel SHA-256"),
        ("system_prompt_sha256", "System prompt SHA-256"),
        ("persona_prompts_sha256", "Persona prompts SHA-256"),
    ):
        value = provenance.get(key)
        if value is None or value == "":
            value = "not reported"
        lines.append(f"- {label}: {_markdown_escape(value)}")
    sources = provenance.get("dataset_sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict) or not source.get("dataset_id"):
                continue
            dataset_id = _markdown_escape(source["dataset_id"])
            revision = _markdown_escape(source.get("revision") or "not recorded")
            lines.append(f"- Dataset: {dataset_id} (revision: {revision})")
    lines += ["", "Fingerprints support traceability; model responses are not guaranteed to be reproducible.", ""]
    return lines


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

    provenance = aggregate.get("provenance") or report.get("provenance")
    if isinstance(provenance, dict) and provenance:
        lines += _provenance_markdown(provenance)

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
