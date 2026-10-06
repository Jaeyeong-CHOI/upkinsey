import base64
import http.client
import io
import json
import os
from email.message import Message
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_server_module():
    from upstage_api_sim import server
    return server


class FakeHandler:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.headers = {"Content-Length": str(len(body))}
        if headers:
            self.headers.update(headers)
        self.rfile = io.BytesIO(body)


class UpkinseyServerSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()

    def setUp(self):
        self.env_patch = patch.dict(os.environ)
        self.env_patch.start()
        for key in list(os.environ):
            if key.startswith("UPKINSEY_"):
                os.environ.pop(key)

    def tearDown(self):
        self.env_patch.stop()
        self.server.SIMULATION_JOBS.clear()
        self.server.RATE_LIMIT_STATE.clear()

    def test_no_key_preview_never_downloads_a_persona_panel(self):
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": ""}), \
             patch.object(self.server, "sample_persona_panel") as sample:
            with self.assertRaisesRegex(RuntimeError, "UPSTAGE_API_KEY is not set"):
                self.server.load_or_sample_personas({"sample_size": 100, "seed": 0})
        sample.assert_not_called()

    def test_read_json_body_accepts_object(self):
        payload = {"product_name": "테스트"}
        handler = FakeHandler(json.dumps(payload).encode("utf-8"))

        self.assertEqual(self.server._read_json_body(handler), payload)

    def test_read_json_body_rejects_invalid_json(self):
        handler = FakeHandler(b"{bad json")

        with self.assertRaisesRegex(ValueError, "invalid_json_body"):
            self.server._read_json_body(handler)

    def test_read_json_body_rejects_non_object_json(self):
        handler = FakeHandler(b"[]")

        with self.assertRaisesRegex(ValueError, "request_body_must_be_object"):
            self.server._read_json_body(handler)

    def test_cleanup_finished_jobs_keeps_running_jobs(self):
        self.server.SIMULATION_JOBS.update(
            {
                "old-done": {"status": "done", "updated_at": 0},
                "old-error": {"status": "error", "updated_at": 0},
                "running": {"status": "running", "updated_at": 0},
            }
        )

        self.server._cleanup_finished_jobs(now=3_700)

        self.assertNotIn("old-done", self.server.SIMULATION_JOBS)
        self.assertNotIn("old-error", self.server.SIMULATION_JOBS)
        self.assertIn("running", self.server.SIMULATION_JOBS)

    def test_basic_auth_required_without_credentials_denies(self):
        os.environ["UPKINSEY_REQUIRE_BASIC_AUTH"] = "1"

        self.assertTrue(self.server._basic_auth_enabled())
        self.assertFalse(self.server._basic_auth_configured())
        self.assertFalse(self.server._basic_auth_allowed(None))

    def test_destructive_api_is_disabled_by_default(self):
        self.assertFalse(self.server._destructive_api_enabled())
        os.environ["UPKINSEY_ALLOW_DESTRUCTIVE_API"] = "1"
        self.assertTrue(self.server._destructive_api_enabled())

    def test_active_job_count_respects_limit(self):
        os.environ["UPKINSEY_MAX_ACTIVE_JOBS"] = "1"
        self.server.SIMULATION_JOBS.update({"queued": {"status": "queued"}, "done": {"status": "done", "updated_at": 0}})

        self.assertEqual(self.server._max_active_jobs(), 1)
        self.assertEqual(self.server._active_job_count(), 1)

    def test_rate_limit_blocks_after_window_budget(self):
        os.environ["UPKINSEY_RATE_LIMIT_PER_MINUTE"] = "2"

        self.assertEqual(self.server._check_rate_limit("client", now=100), (True, 0))
        self.assertEqual(self.server._check_rate_limit("client", now=101), (True, 0))
        ok, retry_after = self.server._check_rate_limit("client", now=102)

        self.assertFalse(ok)
        self.assertGreaterEqual(retry_after, 1)

    def test_uploaded_document_requires_pdf_magic(self):
        boundary = "----testboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="fake.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
            "not a pdf\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        handler = FakeHandler(body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})

        with self.assertRaisesRegex(ValueError, "uploaded_document_must_be_pdf"):
            self.server._read_uploaded_document(handler)


    def test_basic_auth_accepts_unicode_credentials(self):
        os.environ.update(UPKINSEY_REQUIRE_BASIC_AUTH="1", UPKINSEY_BASIC_AUTH_USER="운영자",
                          UPKINSEY_BASIC_AUTH_PASSWORD="비밀번호")
        header = "Basic " + base64.b64encode("운영자:비밀번호".encode()).decode()
        self.assertTrue(self.server._basic_auth_allowed(header))
        self.assertFalse(self.server._basic_auth_allowed("Basic " + base64.b64encode("운영자:wrong".encode()).decode()))

    def test_forwarded_headers_do_not_control_rate_limit_identity(self):
        handler = FakeHandler(b"{}", {"X-Forwarded-For": "spoofed", "CF-Connecting-IP": "also-spoofed"})
        handler.client_address = ("127.0.0.1", 1234)
        self.assertEqual(self.server._client_id(handler), "127.0.0.1")

    def test_rate_limit_prunes_expired_client_entries(self):
        self.server.RATE_LIMIT_STATE["old-client"] = [1]
        self.server._check_rate_limit("new-client", now=100)
        self.assertNotIn("old-client", self.server.RATE_LIMIT_STATE)

    def test_bounded_int_handles_nonfinite_numbers(self):
        for value in (float("inf"), float("-inf"), float("nan"), "1e999"):
            with self.subTest(value=value):
                self.assertEqual(self.server._bounded_int(value, default=2, min_value=1, max_value=8), 2)

    def test_json_rejects_nonfinite_numbers(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid_json_body"):
                    self.server._read_json_body(FakeHandler(('{"sample_size":' + value + '}').encode()))

    def test_json_rejects_truncated_body(self):
        with self.assertRaisesRegex(ValueError, "incomplete_request_body"):
            self.server._read_json_body(FakeHandler(b"{}", {"Content-Length": "20"}))

    def test_json_rejects_ambiguous_framing(self):
        handler = FakeHandler(b"{}")
        headers = Message()
        headers["Content-Length"] = "2"
        headers["Content-Length"] = "3"
        handler.headers = headers
        with self.assertRaisesRegex(ValueError, "invalid_content_length"):
            self.server._read_json_body(handler)
        with self.assertRaisesRegex(ValueError, "unsupported_transfer_encoding"):
            self.server._read_json_body(FakeHandler(b"{}", {"Transfer-Encoding": "chunked"}))

    def test_json_rejects_non_json_content_type(self):
        with self.assertRaisesRegex(ValueError, "application_json_required"):
            self.server._read_json_body(FakeHandler(b"{}", {"Content-Type": "text/plain"}))

    def test_json_checks_size_before_reading(self):
        handler = FakeHandler(b"{}", {"Content-Length": "1000001"})
        with self.assertRaisesRegex(ValueError, "request_body_too_large"):
            self.server._read_json_body(handler)
        self.assertEqual(handler.rfile.tell(), 0)

    def test_json_reports_body_read_timeout(self):
        handler = FakeHandler(b"{}")
        with patch.object(handler, "rfile") as body:
            body.read.side_effect = TimeoutError("timed out")
            with self.assertRaisesRegex(ValueError, "request_body_timeout"):
                self.server._read_json_body(handler)

    def test_upload_rejects_truncated_body(self):
        handler = FakeHandler(b"", {"Content-Length": "200", "Content-Type": "multipart/form-data; boundary=test"})
        with self.assertRaisesRegex(ValueError, "incomplete_request_body"):
            self.server._read_uploaded_document(handler)

    def test_async_job_redacts_error_credentials(self):
        os.environ["UPSTAGE_API_KEY"] = "fake-secret-from-config"
        self.server.SIMULATION_JOBS["job"] = {"status": "queued"}
        error = RuntimeError('API failed: fake-secret-from-config {"api_key": "fake-secret-from-json"} Bearer fake-bearer')
        with patch.object(self.server, "load_or_sample_personas", side_effect=error):
            self.server._run_simulation_job("job", {})
        snapshot = self.server._job_snapshot("job")
        self.assertEqual(snapshot["status"], "error")
        for secret in ("fake-secret-from-config", "fake-secret-from-json", "fake-bearer"):
            self.assertNotIn(secret, snapshot["message"])


class UpkinseyServerHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        (cls.root / "frontend-dist").mkdir()
        (cls.root / "frontend-dist" / "index.html").write_text("public index", encoding="utf-8")
        (cls.root / "frontend-dist" / ".env").write_text("private-dotfile", encoding="utf-8")
        (cls.root / "private.txt").write_text("private-outside-root", encoding="utf-8")
        (cls.root / "frontend-dist" / "outside.txt").symlink_to(cls.root / "private.txt")
        (cls.root / "frontend-dist" / "listing").mkdir()
        (cls.root / "frontend-dist" / "listing" / "internal.txt").write_text("internal listing", encoding="utf-8")
        (cls.root / "frontend-dist" / "linked-index").mkdir()
        (cls.root / "frontend-dist" / "linked-index" / "index.html").symlink_to(cls.root / "private.txt")
        cls.root_patch = patch.object(cls.server, "ROOT", cls.root)
        cls.root_patch.start()
        cls.graph_patch = patch.object(cls.server, "GRAPH_STORE", cls.root / "graph" / "test.sqlite3")
        cls.graph_patch.start()
        cls.runs_patch = patch.object(cls.server, "RUN_STORE", cls.root / "runs")
        cls.runs_patch.start()
        (cls.root / "frontend-dist" / "assets").mkdir()
        (cls.root / "frontend-dist" / "assets" / "app-ABCD1234.js").write_text("export {}", encoding="utf-8")

        class QuietHandler(cls.server.Handler):
            def log_message(self, *args):
                pass

        cls.httpd = cls.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)
        cls.httpd.server_close()
        cls.runs_patch.stop()
        cls.graph_patch.stop()
        cls.root_patch.stop()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.env_patch = patch.dict(os.environ)
        self.env_patch.start()
        for key in list(os.environ):
            if key.startswith("UPKINSEY_"):
                os.environ.pop(key)
        self.server.RATE_LIMIT_STATE.clear()
        self.server.SIMULATION_JOBS.clear()

    def tearDown(self):
        self.env_patch.stop()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_head_requires_auth_just_like_get(self):
        os.environ.update(UPKINSEY_REQUIRE_BASIC_AUTH="1", UPKINSEY_BASIC_AUTH_USER="operator",
                          UPKINSEY_BASIC_AUTH_PASSWORD="example-password")
        get_status, _, _ = self.request("GET", "/")
        head_status, headers, body = self.request("HEAD", "/")
        self.assertEqual((get_status, head_status), (401, 401))
        self.assertIn("WWW-Authenticate", headers)
        self.assertEqual(body, b"")
        auth = "Basic " + base64.b64encode(b"operator:example-password").decode()
        self.assertEqual(self.request("HEAD", "/", headers={"Authorization": auth})[0], 200)

    def test_head_health_uses_get_route_without_a_body(self):
        status, headers, body = self.request("HEAD", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(body, b"")

    def test_production_headers_cache_only_successful_fingerprinted_assets(self):
        for method in ("GET", "HEAD"):
            for path, cache in (("/", "no-cache"), ("/assets/app-ABCD1234.js", "public, max-age=31536000, immutable"), ("/api/health", "no-store")):
                status, headers, _ = self.request(method, path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], cache)
                self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, headers, _ = self.request("GET", "/assets/missing-ABCD1234.js")
        self.assertEqual(status, 404)
        self.assertNotIn("immutable", headers.get("Cache-Control", ""))

    def test_graph_summary_requires_auth_but_health_does_not_expose_graph(self):
        os.environ.update(UPKINSEY_REQUIRE_BASIC_AUTH="1", UPKINSEY_BASIC_AUTH_USER="operator",
                          UPKINSEY_BASIC_AUTH_PASSWORD="example-password")
        for method in ("GET", "HEAD"):
            status, headers, _ = self.request(method, "/api/graph/summary")
            self.assertEqual(status, 401)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(self.request(method, "/assets/app-ABCD1234.js")[1]["Cache-Control"], "no-store")
        auth = "Basic " + base64.b64encode(b"operator:example-password").decode()
        status, headers, body = self.request("GET", "/api/graph/summary", headers={"Authorization": auth})
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body), dict)
        self.assertEqual(headers["Cache-Control"], "no-store")
        health = json.loads(self.request("GET", "/api/health")[2])
        self.assertNotIn("graph", health)

    def test_graph_failure_preserves_canonical_saved_run_and_hides_details(self):
        with patch.object(self.server, "load_or_sample_personas", return_value=[{}]), \
             patch.object(self.server, "simulate_market_research", return_value={"ok": True}), \
             patch.object(self.server, "save_simulation_graph", side_effect=RuntimeError("private-path-and-secret")), \
             self.assertLogs("upstage_api_sim.server", level="WARNING") as logs:
            status, _, body = self.request("POST", "/api/simulate", b'{}')
        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertEqual(response["graph"]["status"], "unavailable")
        saved = self.server.load_simulation_run(self.server.RUN_STORE, response["version"]["version_id"])
        self.assertTrue(saved["result"]["ok"])
        self.assertNotIn("private-path-and-secret", body.decode() + " ".join(logs.output))
        self.assertEqual(self.server._active_job_count(), 0)

    def test_public_health_never_reads_the_research_archive(self):
        with patch.object(self.server, "list_simulation_runs", side_effect=AssertionError("health must not scan runs")), \
             patch.object(self.server, "graph_summary", side_effect=AssertionError("health must not read graph")):
            status, _, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("runs", json.loads(body))
        self.assertNotIn("graph", json.loads(body))

    def test_static_files_are_confined_and_not_listed(self):
        self.assertEqual(self.request("GET", "/")[2], b"public index")
        for path in ("/.env", "/%2eenv", "/outside.txt", "/linked-index/", "/listing/", "/../private.txt", "/%2e%2e/private.txt"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, _, body = self.request(method, path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b"private-", body)

    def test_sync_simulation_obeys_active_job_limit(self):
        os.environ["UPKINSEY_MAX_ACTIVE_JOBS"] = "1"
        self.server.SIMULATION_JOBS["existing"] = {"status": "running"}
        with patch.object(self.server, "load_or_sample_personas", return_value=[{}]) as sample:
            with patch.object(self.server, "simulate_market_research", return_value={}) as simulate:
                with patch.object(self.server, "save_simulation_run", return_value={"summary": {}}):
                    status, _, body = self.request("POST", "/api/simulate", b"{}")
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)["error"], "too_many_active_jobs")
        sample.assert_not_called()
        simulate.assert_not_called()

    def test_sync_simulation_releases_slot_after_failure(self):
        with patch.object(self.server, "load_or_sample_personas", side_effect=RuntimeError("upstream unavailable")):
            status, _, _ = self.request("POST", "/api/simulate", b"{}")
        self.assertEqual(status, 502)
        self.assertEqual(self.server._active_job_count(), 0)

    def test_all_paid_routes_share_active_job_budget(self):
        os.environ["UPKINSEY_MAX_ACTIVE_JOBS"] = "1"
        self.server.SIMULATION_JOBS["existing"] = {"status": "running"}
        for path, function in (("/api/persona-chat", "chat_with_persona"),
                               ("/api/analyst-question", "analyst_question_personas"),
                               ("/api/document-brief", "parse_document_to_brief")):
            with self.subTest(path=path):
                with patch.object(self.server, function, return_value={}) as upstream:
                    with patch.object(self.server, "_read_uploaded_document", return_value=(b"%PDF-1.7", "test.pdf", "application/pdf")):
                        status, _, body = self.request("POST", path, b"{}")
                self.assertEqual(status, 429)
                self.assertEqual(json.loads(body)["error"], "too_many_active_jobs")
                upstream.assert_not_called()

    def test_document_failure_releases_shared_slot(self):
        with patch.object(self.server, "_read_uploaded_document", return_value=(b"%PDF-1.7", "test.pdf", "application/pdf")):
            with patch.object(self.server, "parse_document_to_brief", side_effect=RuntimeError("unavailable")):
                status, _, _ = self.request("POST", "/api/document-brief", b"{}")
        self.assertEqual(status, 502)
        self.assertEqual(self.server._active_job_count(), 0)

    def test_sync_simulation_preserves_response_and_releases_slot(self):
        with patch.object(self.server, "load_or_sample_personas", return_value=[{}]):
            with patch.object(self.server, "simulate_market_research", return_value={"ok": True}):
                with patch.object(self.server, "save_simulation_run", return_value={"summary": {"version_id": "test"}}):
                    status, _, body = self.request("POST", "/api/simulate", b"{}")
        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertTrue(response["ok"])
        self.assertEqual(response["version"], {"version_id": "test"})
        self.assertEqual(response["graph"]["run_id"], "test")
        self.assertEqual(self.server._active_job_count(), 0)

    def test_cross_site_mutations_are_blocked_before_upstream_work(self):
        os.environ["UPKINSEY_ALLOW_DESTRUCTIVE_API"] = "1"
        for headers in ({"Origin": "https://foreign.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            for method, path in (("POST", "/api/document-brief"), ("DELETE", "/api/runs")):
                with self.subTest(headers=headers, method=method):
                    with patch.object(self.server, "parse_document_to_brief") as parse:
                        with patch.object(self.server, "clear_simulation_runs") as clear:
                            status, _, body = self.request(method, path, b"{}", headers)
                    self.assertEqual(status, 403)
                    self.assertEqual(json.loads(body)["error"], "cross_origin_request_denied")
                    parse.assert_not_called()
                    clear.assert_not_called()

    def test_same_origin_chat_is_allowed(self):
        host, port = self.httpd.server_address
        headers = {"Origin": f"http://{host}:{port}", "Content-Type": "application/json"}
        with patch.object(self.server, "chat_with_persona", return_value={"reply": "hello"}):
            status, _, body = self.request("POST", "/api/persona-chat", b'{"message":"hello"}', headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"reply": "hello"})

    def test_async_start_and_poll_complete_with_saved_result(self):
        finished = threading.Event()
        original_update = self.server._update_job

        def update_job(job_id, **patch_values):
            original_update(job_id, **patch_values)
            if patch_values.get("status") in {"done", "error"}:
                finished.set()

        with patch.object(self.server, "load_or_sample_personas", return_value=[{}]):
            with patch.object(self.server, "simulate_market_research", return_value={"ok": True}):
                with patch.object(self.server, "save_simulation_run", return_value={"summary": {"version_id": "saved"}}):
                    with patch.object(self.server, "_update_job", side_effect=update_job):
                        status, _, body = self.request("POST", "/api/simulate/start", b"{}")
                        self.assertEqual(status, 200)
                        self.assertTrue(finished.wait(timeout=5))
        job_id = json.loads(body)["job_id"]
        status, _, body = self.request("GET", "/api/simulate/jobs/" + job_id)
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["result"]["version"], {"version_id": "saved"})
        self.assertEqual(result["result"]["graph"]["run_id"], "saved")
        self.assertEqual(self.server._active_job_count(), 0)

    def test_async_start_releases_slot_if_thread_cannot_start(self):
        # Patch after HTTP worker exists, so only the simulation thread fails.
        with patch.object(self.server, "_read_json_body", side_effect=lambda handler: self._fail_thread_start(handler)):
            status, _, _ = self.request("POST", "/api/simulate/start", b"{}")
        self.assertEqual(status, 502)
        self.assertEqual(self.server._active_job_count(), 0)

    def _fail_thread_start(self, handler):
        patcher = patch.object(self.server.threading.Thread, "start", side_effect=RuntimeError("no thread capacity"))
        patcher.start()
        self.addCleanup(patcher.stop)
        return {}

    def test_invalid_nested_chat_input_is_a_client_error(self):
        for path, payload in (
            ("/api/persona-chat", {"brief": [], "message": "hello", "persona": {}}),
            ("/api/persona-chat", {"history": [7], "message": "hello", "persona": {}}),
            ("/api/analyst-question", {"persona_reactions": [7], "question": "hello"}),
        ):
            with self.subTest(path=path, payload=payload):
                with patch.object(self.server, "chat_with_persona", return_value={}) as chat:
                    with patch.object(self.server, "analyst_question_personas", return_value={}) as analyst:
                        status, _, _ = self.request("POST", path, json.dumps(payload))
                self.assertEqual(status, 400)
                chat.assert_not_called()
                analyst.assert_not_called()


if __name__ == "__main__":
    unittest.main()
