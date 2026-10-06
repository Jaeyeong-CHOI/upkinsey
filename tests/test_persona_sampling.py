import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from upstage_api_sim.personas import sampling
from upstage_api_sim.personas.nemotron import DATASET_ID


class FakeStream:
    def __init__(self, rows, *, fail=False):
        self.rows = rows
        self.fail = fail
        self.shuffle_options = None

    def shuffle(self, **kwargs):
        self.shuffle_options = kwargs
        return self

    def take(self, size):
        for row in self.rows[:size]:
            yield row
            if self.fail:
                raise RuntimeError("fixture interrupted download")


def raw_rows(count=2):
    return [{"uuid": f"fixture-{index}", "persona": f"테스트{index} 씨는 지역 가게를 운영합니다."} for index in range(count)]


def cached_rows(count=2, *, seed=42, revision=None, dataset_id=DATASET_ID):
    return [{"dataset_id": dataset_id, "uuid": f"cached-{index}", "name": f"fixture-{index}", "sampling": {
        "dataset_id": dataset_id, "split": "train", "seed": seed,
        "buffer_size": 10_000, "method": "server_streaming_shuffle_take",
        **({"revision": revision} if revision is not None else {}),
    }} for index in range(count)]


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class PersonaSamplingTests(unittest.TestCase):
    def test_valid_legacy_cache_is_reused_without_loading_and_gets_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "nemotron_seed42_n2.jsonl"
            rows = cached_rows()
            write_jsonl(path, rows)
            original = path.read_bytes()
            loader = mock.Mock(side_effect=AssertionError("must not download"))
            actual = sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=loader)
            self.assertEqual(actual, rows)
            self.assertEqual(path.read_bytes(), original)
            loader.assert_not_called()
            manifest = json.loads(path.with_suffix(".jsonl.manifest.json").read_text())
            self.assertEqual(manifest["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(manifest["sample_size"], 2)
            self.assertEqual(manifest["output"], path.name)
            self.assertIsNone(manifest["revision"])
            self.assertNotIn(directory, json.dumps(manifest))

    def test_invalid_cache_is_preserved_in_trash_and_replaced(self):
        cases = {
            "wrong seed": "".join(json.dumps(row) + "\n" for row in cached_rows(seed=999)),
            "wrong dataset": "".join(json.dumps(row) + "\n" for row in cached_rows(dataset_id="other/source")),
            "short": "".join(json.dumps(row) + "\n" for row in cached_rows(count=1)),
            "long": "".join(json.dumps(row) + "\n" for row in cached_rows(count=3)),
            "corrupt": "{bad json\n",
            "non object": "[]\n[]\n",
        }
        for label, contents in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "nemotron_seed42_n2.jsonl"
                path.write_text(contents)
                loader = mock.Mock(return_value=FakeStream(raw_rows()))
                records = sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=loader)
                loader.assert_called_once_with(DATASET_ID, split="train", streaming=True)
                self.assertEqual(len(records), 2)
                quarantined = list((root / ".trash").glob("*/" + path.name))
                self.assertEqual(len(quarantined), 1)
                self.assertEqual(quarantined[0].read_text(), contents)
                self.assertEqual(records[0]["sampling"]["seed"], 42)

    def test_legacy_cli_manifest_is_upgraded_without_replacing_valid_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "nemotron_seed42_n2.jsonl"
            rows = cached_rows()
            for row in rows:
                row["sampling"]["method"] = "streaming_shuffle_take"
            write_jsonl(path, rows)
            content = path.read_bytes()
            manifest_path = path.with_suffix(".jsonl.manifest.json")
            legacy = {"dataset_id": DATASET_ID, "split": "train", "sampling_seed": 42,
                      "sample_size": 2, "buffer_size": 10_000, "sampling_method": "streaming_shuffle_take",
                      "output": str(path), "sampled_uuids": [row["uuid"] for row in rows]}
            manifest_path.write_text(json.dumps(legacy))
            loader = mock.Mock(side_effect=AssertionError("must not download"))
            self.assertEqual(sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=loader), rows)
            loader.assert_not_called()
            self.assertEqual(path.read_bytes(), content)
            upgraded = json.loads(manifest_path.read_text())
            self.assertEqual(upgraded["output"], path.name)
            self.assertEqual(upgraded["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(len(list((root / ".trash").glob("*/*.manifest.json"))), 1)

    def test_revision_and_buffer_scope_identity_and_are_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for revision in ("main", "refs/test-panel"):
                stream = FakeStream(raw_rows())
                loader = mock.Mock(return_value=stream)
                records = sampling.load_or_sample_personas(root, sample_size=2, seed=0, revision=revision, loader=loader)
                loader.assert_called_once_with(DATASET_ID, split="train", streaming=True, revision=revision)
                self.assertEqual(stream.shuffle_options, {"seed": 0, "buffer_size": 10_000})
                self.assertEqual(records[0]["sampling"]["revision"], revision)
            self.assertEqual(len(list(root.glob("*.jsonl"))), 2)
            self.assertFalse((root / "nemotron_seed0_n2.jsonl").exists())
            for manifest in root.glob("*.manifest.json"):
                self.assertEqual(json.loads(manifest.read_text())["revision_kind"], "requested_reference")
            loader = mock.Mock(side_effect=AssertionError("must not reload"))
            sampling.load_or_sample_personas(root, sample_size=2, seed=0, revision="main", loader=loader)
            loader.assert_not_called()
            other_loader = mock.Mock(return_value=FakeStream(raw_rows()))
            sampling.load_or_sample_personas(root, sample_size=2, seed=0, revision="main", buffer_size=50, loader=other_loader)
            self.assertEqual(len(list(root.glob("*.jsonl"))), 3)

    def test_manifest_checksum_mismatch_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=lambda *a, **k: FakeStream(raw_rows()))
            path = root / "nemotron_seed42_n2.jsonl"
            contents = path.read_text().replace("fixture-0", "altered-id")
            path.write_text(contents)
            loader = mock.Mock(return_value=FakeStream(raw_rows()))
            sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=loader)
            loader.assert_called_once()
            self.assertEqual(len(list((root / ".trash").glob("*/*.manifest.json"))), 1)

    def test_failed_or_short_stream_never_publishes_cache(self):
        for stream in (FakeStream([]), FakeStream(raw_rows(1)), FakeStream(raw_rows(), fail=True)):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises((ValueError, RuntimeError)):
                    sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=lambda *a, **k: stream)
                self.assertEqual(list(root.iterdir()), [])

    def test_manifest_publish_failure_leaves_no_usable_cache_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_write = sampling._atomic_write
            def fail_manifest(path, content):
                if path.name.endswith(".manifest.json"):
                    raise OSError("fixture disk failure")
                return original_write(path, content)
            with mock.patch.object(sampling, "_atomic_write", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "fixture disk failure"):
                    sampling.load_or_sample_personas(root, sample_size=2, seed=42, loader=lambda *a, **k: FakeStream(raw_rows()))
            self.assertEqual(list(root.glob("*.jsonl")), [])
            self.assertEqual(list(root.glob("*.manifest.json")), [])
            self.assertEqual(list(root.glob("*.tmp")), [])
            self.assertEqual(len(list((root / ".trash").glob("*/*.jsonl"))), 1)

    def test_invalid_options_do_not_call_loader(self):
        for params in ({"sample_size": 0, "seed": 1}, {"sample_size": True, "seed": 1}, {"sample_size": 2, "seed": -1}, {"sample_size": 2, "seed": False}):
            with self.subTest(params=params), tempfile.TemporaryDirectory() as directory:
                loader = mock.Mock()
                with self.assertRaises(ValueError):
                    sampling.load_or_sample_personas(directory, loader=loader, **params)
                loader.assert_not_called()

    def test_sampling_cli_keeps_default_options_and_accepts_revision(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "sample_nemotron_personas.py"
        spec = importlib.util.spec_from_file_location("persona_sampler_cli_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, {"UPKINSEY_PERSONA_REVISION": ""}), mock.patch("sys.argv", [str(script)]):
            args = module.parse_args()
        self.assertEqual((args.n, args.seed, args.buffer_size), (10, 42, 10_000))
        self.assertEqual(args.output, Path("data/personas/sample.jsonl"))
        self.assertIsNone(args.revision)
        with mock.patch("sys.argv", [str(script), "--revision", "main"]):
            self.assertEqual(module.parse_args().revision, "main")


if __name__ == "__main__":
    unittest.main()
