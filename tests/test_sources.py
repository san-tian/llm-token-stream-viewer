"""Source attribution and response retention checks using synthetic completions."""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("viewer_builder", ROOT / "tools/build_index.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SourcesTest(unittest.TestCase):
    def test_run_label_overrides_index_and_endpoint(self):
        self.assertEqual(builder.inference_source(
            {"inference_source": "Deployment B", "endpoint": "https://api.example.test/v1/chat/completions"},
            {"inference_source": "Deployment A"}), "Deployment B")

    def test_index_label_and_legacy_unknown(self):
        self.assertEqual(builder.inference_source({}, {"inference_source": "Archive"}), "Archive")
        self.assertEqual(builder.inference_source({"source": "raw/some-file.json"}), "未标注来源")

    def test_endpoint_fallback_omits_sensitive_url_components(self):
        self.assertEqual(builder.inference_source({
            "endpoint": "https://user:password@example.test:8443/private/key?token=secret#fragment"
        }), "https://example.test:8443")
        self.assertEqual(builder.inference_source({
            "endpoint": "http://[fd7a::1]:30000/v1/chat/completions"
        }), "http://[fd7a::1]:30000")
        self.assertEqual(builder.inference_source({"endpoint": "https://example.test:invalid"}), "未标注来源")

    def test_content_only_completion_can_be_rendered(self):
        self.assertEqual(builder.verdict("", "A plain response.")["kind"], "clean")

    def test_each_source_retains_a_complete_request_and_response(self):
        with tempfile.TemporaryDirectory() as temp:
            cases = pathlib.Path(temp)
            for name, source in [("a1", "A"), ("a2", "A"), ("b1", "B"), ("legacy", None)]:
                run = cases / "shared" / name
                run.mkdir(parents=True)
                meta = {"run": name, "temperature": 0}
                if source:
                    meta["inference_source"] = source
                (run / "meta.json").write_text(json.dumps(meta))
                (run / "env.json").write_text(json.dumps({"EXAMPLE_ENV": name}))
                (run / "flags.json").write_text(json.dumps({"--example-backend": name}))
                (run / "request.json").write_text(json.dumps({"messages": [{"role": "user", "content": name}]}))
                (run / "body.json").write_text(json.dumps({
                    "choices": [{"finish_reason": "stop",
                                 "message": {"content": "answer from " + name, "reasoning_content": ""}}]
                }))
            with mock.patch.object(builder, "CASES", cases):
                runs = builder.collect_runs("shared", {})
            by_name = {run["label"]: run for run in runs}
            for name in ["a1", "b1", "legacy"]:
                self.assertTrue(by_name[name]["primaryForSource"])
                self.assertIn("answer from " + name, by_name[name]["response"]["text"])
                self.assertIn(name, by_name[name]["request"]["text"])
                self.assertEqual(by_name[name]["runtimeConfig"]["env"]["EXAMPLE_ENV"], name)
                self.assertEqual(by_name[name]["runtimeConfig"]["flags"]["--example-backend"], name)
            self.assertFalse(by_name["a2"]["primaryForSource"])
            self.assertEqual(by_name["a2"]["response"]["text"], "")
            self.assertEqual(by_name["legacy"]["source"], "未标注来源")
            self.assertEqual(sum(run["primary"] for run in runs), 1)


if __name__ == "__main__":
    unittest.main()
