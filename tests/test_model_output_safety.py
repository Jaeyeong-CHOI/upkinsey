"""Regression checks for malformed model data and misleading fallback output."""
import json
import unittest
from unittest.mock import Mock

from upstage_api_sim.document_parse import normalize_extracted_brief
from upstage_api_sim.market_research import _as_int, _extract_json, simulate_persona_reaction, simulate_market_research, validate_brief


class ModelOutputSafetyTests(unittest.TestCase):
    def test_nonobject_model_json_has_clear_error(self):
        for value in ("[]", "null", '"string"', "42"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    _extract_json(value)

    def test_fenced_json_object_still_supported(self):
        self.assertEqual(_extract_json('```json\n{"name":"fixture"}\n```'), {"name": "fixture"})

    def test_nonfinite_scores_fall_back_without_overflow(self):
        for value in (float("inf"), float("-inf"), float("nan"), "1e10000"):
            with self.subTest(value=value):
                self.assertEqual(_as_int(value, default=37), 37)
                self.assertEqual(normalize_extracted_brief({"confidence": value})["confidence"], 0)

    def test_null_brief_fields_are_missing_not_literal_none(self):
        brief = validate_brief({"description": None, "product_name": None})
        self.assertEqual(brief["description"], "")
        self.assertEqual(brief["product_name"], "제품")

    def test_explicit_empty_panel_never_silently_uses_sample_personas(self):
        client = Mock()
        with self.assertRaisesRegex(ValueError, "at least one persona"):
            simulate_market_research({"sample_size": 2}, personas=[], client=client)
        client.complete_text.assert_not_called()

    def test_missing_or_invalid_model_scores_are_not_invented(self):
        valid = {"understanding_score": 70, "need_fit_score": 65, "adoption_likelihood": 55}
        for value in (None, True, "unknown", float("inf"), -1, 101):
            with self.subTest(value=value):
                client = Mock()
                client.complete_text.return_value = json.dumps({**valid, "adoption_likelihood": value})
                with self.assertRaisesRegex(ValueError, "adoption_likelihood"):
                    simulate_persona_reaction({}, {"name": "fixture"}, client=client)
        client = Mock()
        client.complete_text.return_value = "{}"
        with self.assertRaisesRegex(ValueError, "understanding_score"):
            simulate_persona_reaction({}, {"name": "fixture"}, client=client)
