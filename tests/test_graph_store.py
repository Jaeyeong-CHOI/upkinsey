import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from pathlib import Path

from upstage_api_sim.graph_store import graph_summary, init_graph_store, save_simulation_graph
from upstage_api_sim.run_store import save_simulation_run


class GraphStoreTests(unittest.TestCase):
    def _sample_result(self):
        return {
            "adoption_score": 66,
            "need_fit_score": 72,
            "price_risk": "Medium",
            "persona_reactions": [
                {
                    "name": "김사장",
                    "meta": "39세 · 부산 · 카페 사장",
                    "stance": "긍정형",
                    "understanding_score": 82,
                    "need_fit_score": 78,
                    "adoption_likelihood": 74,
                    "price_resistance": "Medium",
                    "concern": "월 구독 가격이 부담될 수 있음",
                    "positive_drivers": ["답글 시간 절약"],
                    "top_risks": ["가격 부담"],
                    "next_validation_question": "무료 체험 후 전환할 기준은?",
                    "persona_context": {"source": {"uuid": "persona-1"}},
                },
                {
                    "name": "박회사",
                    "meta": "33세 · 경기 · 회사원",
                    "stance": "관망형",
                    "understanding_score": 75,
                    "need_fit_score": 64,
                    "adoption_likelihood": 48,
                    "price_resistance": "Low",
                    "concern": "자동 답변 품질을 믿기 어려움",
                    "positive_drivers": ["반복 업무 감소"],
                    "top_risks": ["신뢰 부족"],
                    "next_validation_question": "답변 근거를 보면 신뢰가 생기는가?",
                    "persona_context": {"source": {"uuid": "persona-2"}},
                },
            ],
            "report": {
                "objections": [
                    {
                        "category": "가격 저항",
                        "objection": "가격 부담",
                        "count": 1,
                        "suggested_fix": "무료 체험과 절약 시간을 함께 제시",
                    }
                ],
                "segment_recommendations": [
                    {
                        "segment": "우선 검증 타깃",
                        "role": "beachhead",
                        "persona_count": 1,
                        "avg_adoption": 74,
                        "avg_need_fit": 78,
                        "primary_driver": "답글 시간 절약",
                        "primary_objection": "가격 부담",
                    }
                ],
                "decision_board": {
                    "decision": "Segment Pivot",
                    "confidence": "medium",
                    "next_step": "카페 사장 세그먼트부터 실제 인터뷰",
                },
            },
        }

    def test_save_simulation_graph_projects_run_entities_edges_and_observations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "graph.sqlite3"
            graph = save_simulation_graph(
                db_path,
                {
                    "product_name": "사장님 리뷰비서",
                    "research_type": "Concept test",
                    "target_market": "리뷰 관리 부담이 큰 카페 사장",
                    "pricing": ["월 19000원"],
                },
                self._sample_result(),
                version_id="20260529-080000-aaaaaaaa",
                observed_at="2026-05-29T00:00:00+00:00",
            )
            summary = graph_summary(db_path)

        self.assertEqual(graph["run_id"], "20260529-080000-aaaaaaaa")
        self.assertGreaterEqual(summary["entity_types"]["product"], 1)
        self.assertEqual(summary["entity_types"]["persona"], 2)
        self.assertGreaterEqual(summary["entity_types"]["objection"], 3)
        self.assertEqual(summary["entity_types"]["segment"], 1)
        self.assertEqual(summary["entity_types"]["decision"], 1)
        self.assertEqual(summary["relations"]["evaluates"], 1)
        self.assertEqual(summary["relations"]["tested_with"], 2)
        self.assertEqual(summary["relations"]["reacted_to"], 2)
        self.assertGreaterEqual(summary["observations"], 10)

    def test_save_simulation_graph_is_idempotent_for_same_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "graph.sqlite3"
            brief = {"product_name": "사장님 리뷰비서", "research_type": "Concept test"}
            result = self._sample_result()
            first = save_simulation_graph(db_path, brief, result, version_id="20260529-080000-aaaaaaaa")
            second = save_simulation_graph(db_path, brief, result, version_id="20260529-080000-aaaaaaaa")
            with closing(sqlite3.connect(db_path)) as conn:
                observations = conn.execute("SELECT COUNT(*) FROM graph_observations").fetchone()[0]

        self.assertGreater(first["new_observations"], 0)
        self.assertEqual(second["new_observations"], 0)
        self.assertEqual(observations, first["observations"])

    def test_graph_summary_does_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing" / "graph.sqlite3"
            summary = graph_summary(path)
            self.assertFalse(summary["exists"])
            self.assertFalse(path.parent.exists())

    def test_graph_summary_contains_corrupt_or_uninitialized_database_errors(self):
        for contents in (b"", b"not a SQLite database"):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "graph.sqlite3"
                path.write_bytes(contents)
                summary = graph_summary(path)
                self.assertFalse(summary["available"])
                self.assertEqual(summary["error"], "graph_store_unavailable")
                self.assertEqual(path.read_bytes(), contents)
                self.assertNotIn(str(path), json.dumps(summary))

    def test_unknown_schema_is_not_overwritten_or_projected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.sqlite3"
            init_graph_store(path)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("UPDATE graph_meta SET value = '99' WHERE key = 'schema_version'")
                conn.commit()
            with self.assertRaisesRegex(ValueError, "unsupported_graph_schema_version"):
                save_simulation_graph(path, {"product_name": "fixture"}, self._sample_result())
            with closing(sqlite3.connect(path)) as conn:
                version = conn.execute("SELECT value FROM graph_meta WHERE key = 'schema_version'").fetchone()[0]
                self.assertEqual(version, "99")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0], 0)
            self.assertEqual(graph_summary(path)["error"], "unsupported_graph_schema_version")

    def test_non_finite_projection_rolls_back_without_invalid_json(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "graph.sqlite3"
                result = self._sample_result()
                result["persona_reactions"][0]["adoption_likelihood"] = value
                with self.assertRaises(ValueError):
                    save_simulation_graph(path, {"product_name": "fixture"}, result)
                summary = graph_summary(path)
                self.assertEqual(summary["entities"], 0)
                self.assertEqual(summary["observations"], 0)

    def test_projection_response_does_not_expose_local_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.sqlite3"
            response = save_simulation_graph(path, {"product_name": "fixture"}, self._sample_result())
            self.assertEqual(response["store"], "local_sqlite")
            self.assertNotIn(str(path), json.dumps(response))

    def test_backfill_uses_configured_data_directory_and_skips_bad_runs(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_graph_store.py"
        spec = importlib.util.spec_from_file_location("graph_backfill_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runs = root / "simulation_runs"
            runs.mkdir()
            (runs / "valid.json").write_text(json.dumps({"version_id": "fixture-run", "brief": {"product_name": "fixture"}, "result": self._sample_result()}))
            (runs / "bad.json").write_text("{bad json")
            with mock.patch.dict(os.environ, {"UPKINSEY_DATA_DIR": tmpdir}), mock.patch("sys.argv", [str(script)]):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    module.main()
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["processed"], 1)
            self.assertEqual(len(summary["skipped"]), 1)
            self.assertTrue((root / "graph" / "upkinsey.sqlite3").exists())

    def test_backfill_preserves_live_projection_metadata_and_counts(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_graph_store.py"
        spec = importlib.util.spec_from_file_location("graph_backfill_metadata_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "graph" / "upkinsey.sqlite3"
            brief = {"product_name": "fixture"}
            result = self._sample_result()
            saved = save_simulation_run(root / "simulation_runs", brief, result)
            live_result = {**result, "version": saved["summary"]}
            save_simulation_graph(path, brief, live_result, observed_at=saved["summary"]["created_at"])
            before = graph_summary(path)
            with closing(sqlite3.connect(path)) as conn:
                before_properties = conn.execute("SELECT properties_json FROM graph_entities WHERE entity_type = 'run'").fetchone()[0]
            self.assertEqual(json.loads(before_properties)["created_at"], saved["summary"]["created_at"])

            with mock.patch.dict(os.environ, {"UPKINSEY_DATA_DIR": tmpdir}), mock.patch("sys.argv", [str(script)]):
                with contextlib.redirect_stdout(io.StringIO()):
                    module.main()

            self.assertEqual(graph_summary(path), before)
            with closing(sqlite3.connect(path)) as conn:
                after_properties = conn.execute("SELECT properties_json FROM graph_entities WHERE entity_type = 'run'").fetchone()[0]
            self.assertEqual(after_properties, before_properties)


if __name__ == "__main__":
    unittest.main()
