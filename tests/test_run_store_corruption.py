"""One malformed run must not make every saved run inaccessible."""
import json
import tempfile
import unittest
from pathlib import Path

from upstage_api_sim.run_store import delete_simulation_run, list_simulation_runs, load_simulation_run, save_simulation_run


class RunStoreCorruptionTests(unittest.TestCase):
    def test_list_skips_corrupt_utf8_nonobjects_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = save_simulation_run(directory, {"product_name": "fixture"}, {})
            for index, body in enumerate((b"\xff", b"[]", b"null", b"{")):
                (Path(directory) / f"20990101-000000-{index:08x}.json").write_bytes(body)
            runs = list_simulation_runs(directory)
            self.assertEqual([r["version_id"] for r in runs], [saved["summary"]["version_id"]])
            self.assertEqual(list_simulation_runs(directory, limit=0), [])

    def test_corrupt_run_has_clear_error_and_remains_recoverable_in_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, body in enumerate((b"\xff", b"[]", b"null", b"{")):
                version = f"20990101-000000-{index:08x}"
                path = Path(directory) / f"{version}.json"
                path.write_bytes(body)
                with self.assertRaisesRegex(ValueError, "invalid_version_file"):
                    load_simulation_run(directory, version)
                receipt = delete_simulation_run(directory, version)
                self.assertFalse(path.exists())
                self.assertEqual(Path(receipt["trash_path"]).read_bytes(), body)

    def test_malformed_optional_sections_do_not_break_history(self):
        with tempfile.TemporaryDirectory() as directory:
            version = "20990101-000000-00000000"
            run = {"version_id": version, "brief": ["invalid"], "result": {
                "personas": 7,
                "evidence_quality": {"warnings": 7},
                "request_budget": {"warnings": "invalid"},
                "panel_profile": {"warnings": False},
            }}
            (Path(directory) / f"{version}.json").write_text(json.dumps(run))
            summary = list_simulation_runs(directory)[0]
            for field in ("persona_count", "evidence_warning_count", "request_budget_warning_count", "panel_warning_count"):
                self.assertEqual(summary[field], 0)
            self.assertEqual(load_simulation_run(directory, version)["brief"], {})
