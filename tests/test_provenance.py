import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from upstage_api_sim.market_research import simulate_market_research
from upstage_api_sim.provenance import build_provenance, content_sha256


class ProvenanceTests(unittest.TestCase):
    def test_hash_canonicalizes_keys_but_preserves_panel_order_and_content(self):
        self.assertEqual(content_sha256({"a": 1, "b": 2}), content_sha256({"b": 2, "a": 1}))
        self.assertNotEqual(content_sha256([1, 2]), content_sha256([2, 1]))
        self.assertNotEqual(content_sha256({"name": "A"}), content_sha256({"name": "B"}))

    def test_provenance_records_unknown_revisions_without_inventing_them(self):
        data = build_provenance(brief={"seed": 0}, source_count=2,
            selected_personas=[{"dataset_id": "fixture", "sampling": {"seed": 0}}],
            model=None, system_prompt="system", persona_prompts=["persona"],
            started_at="2026-01-01T00:00:00Z", duration_seconds=1.25)
        self.assertEqual(data["sampling_seed"], 0)
        self.assertEqual(data["requested_seed"], 0)
        self.assertIsNone(data["model"])
        self.assertEqual(data["dataset_sources"], [{"dataset_id": "fixture", "revision": None}])

    def test_run_uses_actual_model_and_never_serializes_client_secrets(self):
        class Client:
            config = SimpleNamespace(model="fixture-model-v2", api_key="do-not-save-this", base_url="https://secret.example/?token=private")
            def complete_text(self, *args, **kwargs):
                return json.dumps({"understanding_score": 70, "need_fit_score": 65, "adoption_likelihood": 55})
        result = simulate_market_research({"product_name": "fixture", "sample_size": 1, "seed": 0},
            client=Client(), personas=[{"name": "A", "dataset_id": "fixture", "sampling": {"revision": "abc123"}}])
        self.assertEqual(result["request_budget"]["model"], "fixture-model-v2")
        self.assertEqual(result["provenance"]["model"], "fixture-model-v2")
        self.assertEqual(result["model_note"], "fixture-model-v2")
        self.assertEqual(result["provenance"]["requested_seed"], 0)
        self.assertIsNone(result["provenance"]["sampling_seed"])
        self.assertEqual(result["provenance"]["dataset_sources"][0]["revision"], "abc123")
        self.assertEqual(result["report"]["provenance"], result["provenance"])
        serialized = json.dumps(result)
        self.assertNotIn("do-not-save-this", serialized)
        self.assertNotIn("secret.example", serialized)
        self.assertNotIn("private", serialized)

    def test_requested_seed_does_not_override_observed_panel_seed(self):
        client = Mock()
        client.complete_text.return_value = json.dumps({"understanding_score": 70, "need_fit_score": 65, "adoption_likelihood": 55})
        result = simulate_market_research({}, client=client, personas=[{"name": "fixture", "sampling": {"seed": 17}}])
        self.assertEqual(result["provenance"]["requested_seed"], 42)
        self.assertEqual(result["provenance"]["sampling_seed"], 17)
        self.assertEqual(result["provenance"]["observed_sampling_seeds"], [17])
        self.assertEqual(result["provenance"]["sampling_seed_missing_count"], 0)
        self.assertIn("- Requested seed: 42", result["report_markdown"])
        self.assertIn("- Sampling seed: 17", result["report_markdown"])

    def test_mixed_or_missing_panel_seeds_are_reported_as_unknown(self):
        for records, expected_seeds, missing in (
            ([{"sampling": {"seed": 17}}, {"sampling": {"seed": 42}}], [17, 42], 0),
            ([{"sampling": {"seed": 17}}, {}], [17], 1),
            ([{"sampling": {"seed": True}}, {"sampling": {"seed": "17"}}], [], 2),
            ([], [], 0),
        ):
            with self.subTest(records=records):
                data = build_provenance(
                    brief={"seed": 42}, source_count=len(records), selected_personas=records,
                    model=None, system_prompt="system", persona_prompts=[],
                    started_at="2026-01-01T00:00:00Z", duration_seconds=0,
                )
                self.assertIsNone(data["sampling_seed"])
                self.assertEqual(data["observed_sampling_seeds"], expected_seeds)
                self.assertEqual(data["sampling_seed_missing_count"], missing)

    def test_non_json_panel_metadata_is_rejected_before_any_model_call(self):
        for extra in (float("nan"), float("inf"), {"not-json"}, object()):
            with self.subTest(type=type(extra).__name__):
                client = Mock()
                with self.assertRaisesRegex(ValueError, "finite JSON-compatible"):
                    simulate_market_research({}, client=client, personas=[{"name": "fixture", "extra": extra}])
                client.complete_text.assert_not_called()

    def test_duration_is_finalized_after_model_calls(self):
        client = Mock()
        client.complete_text.return_value = json.dumps({"understanding_score": 70, "need_fit_score": 65, "adoption_likelihood": 55})
        with patch("upstage_api_sim.market_research.time.monotonic", side_effect=[100.0, 102.5]):
            result = simulate_market_research({}, client=client, personas=[{"name": "fixture"}])
        client.complete_text.assert_called_once()
        self.assertEqual(result["provenance"]["duration_seconds"], 2.5)
