"""Per-experiment deployment configuration persistence and compatibility."""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("config_viewer_builder", ROOT / "tools/build_index.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
from runtime_config import read_runtime_config, save_runtime_config


class RuntimeConfigTest(unittest.TestCase):
    def test_unrecorded_or_external_deployment_stays_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(read_runtime_config(d), {"env": {}, "flags": {}, "note": ""})

    def test_files_override_explicit_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d)
            (path / "env.json").write_text(json.dumps({"CUDA_VISIBLE_DEVICES": None}))
            result = read_runtime_config(d, {
                "deployment_env": {"CUDA_VISIBLE_DEVICES": "0"},
                "deployment_flags": {"--disable-cuda-graph": True},
                "runtime_config_note": "Recorded at launch",
            })
            self.assertEqual(result["env"]["CUDA_VISIBLE_DEVICES"], None)
            self.assertEqual(result["flags"]["--disable-cuda-graph"], True)
            self.assertEqual(result["note"], "Recorded at launch")

    def test_sensitive_values_are_redacted_before_save(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d)
            source = path / "input.json"
            source.write_text(json.dumps({
                "HF_TOKEN": "fake-private-value", "MAX_TOKENS": 8192,
                "CUDA_VISIBLE_DEVICES": None, "SGLANG_CACHE_DIR": "/cache",
                "download": "https://user:password@example.test/model?signature=secret",
                "nested": {"api_key": "fake-key", "mamba_dtype": "bfloat16"},
            }))
            target = path / "run"; target.mkdir()
            with mock.patch.dict(os.environ, {"DEPLOYMENT_ENV_FILE": str(source)}, clear=True):
                save_runtime_config(target)
            saved = (target / "env.json").read_text()
            self.assertNotIn("fake-private-value", saved)
            self.assertNotIn("fake-key", saved)
            self.assertNotIn("signature", saved)
            self.assertNotIn("password", saved)
            values = json.loads(saved)
            self.assertEqual(values["MAX_TOKENS"], 8192)
            self.assertIsNone(values["CUDA_VISIBLE_DEVICES"])
            self.assertEqual(values["SGLANG_CACHE_DIR"], "/cache")
            self.assertEqual(values["download"], "https://example.test/model")
            self.assertEqual(json.loads((target / "flags.json").read_text()), {})

    def test_reused_output_does_not_keep_stale_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d)
            (path / "env.json").write_text('{"OLD_EXPERIMENT": "value"}')
            (path / "flags.json").write_text('{"--old-flag": true}')
            with mock.patch.dict(os.environ, {}, clear=True):
                save_runtime_config(path)
            self.assertEqual(read_runtime_config(path)["env"], {})
            self.assertEqual(read_runtime_config(path)["flags"], {})

    def test_non_object_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "flags.json").write_text('["--unstructured"]')
            with self.assertRaises(ValueError):
                read_runtime_config(d)

    def test_native_token_record_has_its_own_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d); case = root / "native"; case.mkdir()
            for filename, value in {
                "meta.json": {"inference_source": "Native deployment"},
                "gen_speckeep.json": {"text": "", "meta_info": {}},
                "gen_skipdrop.json": {"text": "", "meta_info": {}},
                "chat.json": {"choices": [{"message": {"content": ""}}]},
                "env.json": {"NATIVE_CACHE": "/cache/native"},
                "flags.json": {"--native-backend": "test"},
            }.items():
                (case / filename).write_text(json.dumps(value))
            with mock.patch.object(builder, "TOK", root):
                record = builder.collect_tokens("native")
            self.assertEqual(record["runtimeConfig"]["env"]["NATIVE_CACHE"], "/cache/native")
            self.assertEqual(record["runtimeConfig"]["flags"]["--native-backend"], "test")


if __name__ == "__main__":
    unittest.main()
