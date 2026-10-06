import json
from pathlib import Path
import tempfile
import unittest

from upstage_api_sim.run_store import (
    clear_simulation_runs,
    compare_simulation_run,
    compare_run_summaries,
    delete_simulation_run,
    list_simulation_runs,
    load_simulation_run,
    save_simulation_run,
)


class RunStoreTests(unittest.TestCase):
    def test_save_list_and_load_simulation_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            saved = save_simulation_run(
                tmpdir,
                {"product_name": "테스트", "research_type": "Pricing test", "sample_size": 30},
                {
                    "adoption_score": 64,
                    "need_fit_score": 72,
                    "price_risk": "Medium",
                    "persona_reactions": [{"name": "A"}, {"name": "B"}],
                    "evidence_quality": {
                        "score": 58,
                        "level": "directional",
                        "confidence": "medium",
                        "warnings": ["small panel"],
                    },
                    "request_budget": {
                        "requested_sample_size": 30,
                        "solar_persona_calls": 2,
                        "estimated_total_model_calls": 2,
                        "planned_batches": 1,
                        "max_parallel_requests": 2,
                        "warnings": [],
                    },
                    "panel_profile": {
                        "selection_mode": "target_filtered",
                        "persona_filter_source": "user",
                        "source_persona_count": 30,
                        "selected_persona_count": 2,
                        "target_filter_match_count": 2,
                        "warnings": ["selected panel이 작습니다."],
                    },
                    "report": {"decision_board": {"decision": "Refine"}},
                },
            )

            runs = list_simulation_runs(tmpdir)
            loaded = load_simulation_run(tmpdir, saved["summary"]["version_id"])

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["product_name"], "테스트")
        self.assertEqual(runs[0]["research_type"], "Pricing test")
        self.assertEqual(runs[0]["persona_count"], 2)
        self.assertEqual(runs[0]["decision"], "Refine")
        self.assertEqual(runs[0]["evidence_quality_score"], 58)
        self.assertEqual(runs[0]["evidence_quality_level"], "directional")
        self.assertEqual(runs[0]["actual_persona_calls"], 2)
        self.assertEqual(runs[0]["panel_selection_mode"], "target_filtered")
        self.assertEqual(runs[0]["target_filter_match_count"], 2)
        self.assertEqual(loaded["brief"]["product_name"], "테스트")
        self.assertEqual(loaded["result"]["version"]["adoption_score"], 64)

    def test_summarize_run_reads_guardrails_from_nested_report_for_old_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            saved = save_simulation_run(
                tmpdir,
                {"product_name": "레거시", "research_type": "Concept test", "sample_size": 8},
                {
                    "adoption_score": 40,
                    "need_fit_score": 45,
                    "persona_reactions": [{"name": "A"}],
                    "report": {
                        "decision_board": {"decision": "Hold"},
                        "evidence_quality": {"score": 32, "level": "exploratory", "confidence": "low", "warnings": ["tiny"]},
                        "request_budget": {"requested_sample_size": 8, "actual_persona_count": 1, "warnings": ["mismatch"]},
                        "panel_profile": {"selection_mode": "unfiltered", "selected_persona_count": 1, "warnings": ["tiny"]},
                    },
                },
            )

            runs = list_simulation_runs(tmpdir)

        self.assertEqual(saved["summary"]["evidence_quality_score"], 32)
        self.assertEqual(runs[0]["evidence_quality_level"], "exploratory")
        self.assertEqual(runs[0]["actual_persona_calls"], 1)
        self.assertEqual(runs[0]["panel_selection_mode"], "unfiltered")

    def test_load_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                load_simulation_run(tmpdir, "../secret")

    def test_compare_run_summaries_reports_metric_and_decision_deltas(self):
        comparison = compare_run_summaries(
            {
                "version_id": "20260510-120000-bbbbbbbb",
                "adoption_score": 68,
                "need_fit_score": 70,
                "price_risk": "Low-Medium",
                "decision": "Segment Pivot",
                "evidence_quality_score": 66,
            },
            {
                "version_id": "20260510-110000-aaaaaaaa",
                "adoption_score": 60,
                "need_fit_score": 73,
                "price_risk": "Medium",
                "decision": "Refine",
                "evidence_quality_score": 54,
            },
        )

        self.assertEqual(comparison["metrics"][0]["delta"], 8.0)
        self.assertEqual(comparison["metrics"][0]["direction"], "up")
        self.assertEqual(comparison["metrics"][1]["direction"], "down")
        self.assertEqual(comparison["metrics"][2]["label"], "Evidence quality")
        self.assertEqual(comparison["metrics"][2]["delta"], 12.0)
        self.assertTrue(comparison["price_risk_changed"])
        self.assertTrue(comparison["decision_changed"])
        self.assertIn("Decision changed", comparison["summary"])

    def test_compare_simulation_run_prefers_previous_same_product_and_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runs = [
                (
                    "20260510-100000-11111111",
                    {"product_name": "테스트", "research_type": "Pricing test"},
                    {"adoption_score": 55, "need_fit_score": 60, "price_risk": "Medium", "persona_reactions": []},
                ),
                (
                    "20260510-110000-22222222",
                    {"product_name": "다른 제품", "research_type": "Concept test"},
                    {"adoption_score": 20, "need_fit_score": 25, "price_risk": "High", "persona_reactions": []},
                ),
                (
                    "20260510-120000-33333333",
                    {"product_name": "테스트", "research_type": "Pricing test"},
                    {"adoption_score": 63, "need_fit_score": 58, "price_risk": "Low-Medium", "persona_reactions": []},
                ),
            ]
            for version_id, brief, result in runs:
                (root / f"{version_id}.json").write_text(
                    json.dumps({"version_id": version_id, "created_at": "2026-05-10T00:00:00+00:00", "brief": brief, "result": result}),
                    encoding="utf-8",
                )

            comparison = compare_simulation_run(tmpdir, "20260510-120000-33333333")

        self.assertEqual(comparison["baseline_version_id"], "20260510-100000-11111111")
        self.assertEqual(comparison["metrics"][0]["delta"], 8.0)
        self.assertEqual(comparison["metrics"][1]["delta"], -2.0)

    def test_delete_simulation_run_moves_file_to_trash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trash = Path(tmpdir) / "trash"
            saved = save_simulation_run(tmpdir, {"product_name": "삭제 테스트"}, {"persona_reactions": []})
            version_id = saved["summary"]["version_id"]

            result = delete_simulation_run(tmpdir, version_id, trash_dir=trash)

            self.assertEqual(result["deleted"], 1)
            self.assertEqual(list_simulation_runs(tmpdir), [])
            self.assertTrue(Path(result["trash_path"]).exists())
            with self.assertRaises(FileNotFoundError):
                load_simulation_run(tmpdir, version_id)

    def test_clear_simulation_runs_moves_all_files_to_trash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trash = Path(tmpdir) / "trash"
            save_simulation_run(tmpdir, {"product_name": "A"}, {"persona_reactions": []})
            save_simulation_run(tmpdir, {"product_name": "B"}, {"persona_reactions": []})

            result = clear_simulation_runs(tmpdir, trash_dir=trash)

            self.assertEqual(result["deleted"], 2)
            self.assertEqual(list_simulation_runs(tmpdir), [])
            self.assertEqual(len(list(trash.glob("**/*.json"))), 2)


if __name__ == "__main__":
    unittest.main()


class RunChronologyTests(unittest.TestCase):
    def test_same_second_runs_follow_creation_time_not_random_id(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        class Clock:
            ticks = 0
            @classmethod
            def now(cls, tz):
                cls.ticks += 1
                return datetime(2026, 10, 6, 12, 0, 0, cls.ticks, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with patch('upstage_api_sim.run_store.datetime', Clock):
                with patch('upstage_api_sim.run_store.uuid.uuid4', side_effect=[SimpleNamespace(hex='ffffffff'), SimpleNamespace(hex='00000000')]):
                    first = save_simulation_run(directory, {'product_name': 'same'}, {})
                    second = save_simulation_run(directory, {'product_name': 'same'}, {})
            runs = list_simulation_runs(directory)
            self.assertEqual(runs[0]['version_id'], second['summary']['version_id'])
            comparison = compare_simulation_run(directory, second['summary']['version_id'])
            self.assertEqual(comparison['baseline_version_id'], first['summary']['version_id'])
            stored = json.loads(Path(second['path']).read_text())
            self.assertEqual(stored['schema_version'], 1)
