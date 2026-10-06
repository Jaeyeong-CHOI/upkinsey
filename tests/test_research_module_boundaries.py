"""Keep pure research components importable and legacy call sites compatible."""

import importlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from upstage_api_sim import market_research


SRC = Path(__file__).resolve().parents[1] / "src"
PURE_MODULES = ("research_inputs", "report_markdown", "interview_planning")


class ResearchModuleBoundaryTests(unittest.TestCase):
    def test_pure_components_import_in_any_order_without_orchestration(self):
        script = """
import importlib
import json
import sys
for name in json.loads(sys.argv[1]):
    importlib.import_module("upstage_api_sim." + name)
assert "upstage_api_sim.market_research" not in sys.modules
assert "upstage_api_sim.upstage_client" not in sys.modules
importlib.import_module("upstage_api_sim.market_research")
importlib.import_module("upstage_api_sim.document_parse")
"""
        environment = {**os.environ, "PYTHONPATH": str(SRC)}
        for order in itertools.permutations(PURE_MODULES):
            with self.subTest(order=order):
                result = subprocess.run(
                    [sys.executable, "-c", script, json.dumps(order)],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_import_surface_reexports_component_implementations(self):
        exports = {
            "research_inputs": (
                "validate_brief", "persona_meta", "normalize_persona_for_prompt",
                "persona_context_for_result", "infer_persona_filters_from_brief",
                "persona_filter_score", "select_personas_for_brief", "unique_top",
                "_extract_json",
            ),
            "report_markdown": ("format_report_markdown",),
            "interview_planning": (
                "build_persona_chat_prompt", "build_analyst_question_plan",
                "build_custom_analyst_followup", "select_analyst_target_personas",
            ),
        }
        for module_name, names in exports.items():
            component = importlib.import_module("upstage_api_sim." + module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    self.assertIs(getattr(market_research, name), getattr(component, name))

    def test_chat_keeps_legacy_client_and_prompt_patch_seams(self):
        client = Mock()
        client.complete_text.return_value = '{"reply":"fixture reply","signal":"trust"}'
        with patch.object(market_research, "UpstageClient", return_value=client) as factory:
            with patch.object(market_research, "build_persona_chat_prompt", return_value="fixture prompt") as prompt:
                result = market_research.chat_with_persona({}, {"name": "fixture"}, "Why?")
        factory.assert_called_once_with()
        prompt.assert_called_once_with({}, {"name": "fixture"}, "Why?", None)
        self.assertEqual(client.complete_text.call_args.args[0], "fixture prompt")
        self.assertEqual(result["reply"], "fixture reply")

    def test_analyst_keeps_legacy_chat_patch_seam(self):
        reaction = {"name": "fixture", "adoption_likelihood": 60, "price_resistance": "Medium"}
        reply = {"persona_name": "fixture", "reply": "Need evidence first.", "signal": "trust"}
        with patch.object(market_research, "chat_with_persona", return_value=reply) as chat:
            result = market_research.analyst_question_personas(
                {}, [reaction], "Why?", client=Mock(), max_workers=1, max_rounds=1,
            )
        chat.assert_called_once()
        self.assertEqual(len(result["conversations"]), 1)
        self.assertEqual(result["conversations"][0]["final_reply"], reply["reply"])


class ReportProvenanceTests(unittest.TestCase):
    def test_absent_or_empty_provenance_preserves_legacy_rendering(self):
        baseline = market_research.format_report_markdown({}, {})
        for result in ({"provenance": None}, {"provenance": {}}, {"report": {"provenance": {}}}):
            with self.subTest(result=result):
                self.assertEqual(market_research.format_report_markdown({}, result), baseline)
        self.assertNotIn("## Run provenance", baseline)

    def test_provenance_renders_auditable_fields_not_private_configuration(self):
        provenance = {
            "schema_version": 1,
            "application_version": "0.1.0",
            "model": None,
            "sampling_seed": 0,
            "source_persona_count": 2,
            "selected_persona_count": 1,
            "duration_seconds": 0,
            "panel_sha256": "a" * 64,
            "system_prompt_sha256": "b" * 64,
            "persona_prompts_sha256": "c" * 64,
            "dataset_sources": [{"dataset_id": "fixture/data", "revision": None}],
            "api_key": "private-key-fixture",
            "base_url": "https://private.example.test",
            "config": {"password": "private-password-fixture"},
        }
        before = json.dumps(provenance, sort_keys=True)
        text = market_research.format_report_markdown({}, {"provenance": provenance})
        self.assertIn("## Run provenance", text)
        self.assertIn("- Model: not reported", text)
        self.assertIn("- Sampling seed: 0", text)
        self.assertIn("- Duration (seconds): 0", text)
        self.assertIn("- Dataset: fixture/data (revision: not recorded)", text)
        self.assertIn("a" * 64, text)
        self.assertNotIn("private-", text)
        self.assertNotIn("private.example", text)
        self.assertEqual(json.dumps(provenance, sort_keys=True), before)
        self.assertEqual(
            market_research.format_report_markdown({}, {"report": {"provenance": provenance}}), text,
        )


if __name__ == "__main__":
    unittest.main()
