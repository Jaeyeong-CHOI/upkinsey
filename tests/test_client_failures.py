"""Transport failures must be bounded and must not disclose credentials."""
import io
import traceback
import unittest
import urllib.error
from unittest.mock import patch

from upstage_api_sim.document_parse import call_document_parse_api
from upstage_api_sim.upstage_client import UpstageClient, UpstageConfig


class ClientFailureTests(unittest.TestCase):
    secret = "fixture-upstage-secret-12345"

    def client(self, **kwargs):
        return UpstageClient(UpstageConfig(api_key=self.secret, **kwargs))

    def http_error(self, status, body):
        error = urllib.error.HTTPError("https://example.test/chat", status, "fixture", {}, io.BytesIO(body.encode()))
        self.addCleanup(error.close)
        return error

    def test_chat_http_error_redacts_before_truncating(self):
        error = self.http_error(401, "x" * 1190 + self.secret)
        with patch("urllib.request.urlopen", side_effect=error) as opened:
            try:
                self.client().complete_text("test")
            except RuntimeError as exc:
                self.assertNotIn(self.secret[:10], str(exc))
                self.assertNotIn(self.secret, traceback.format_exc())
            else:
                self.fail("expected upstream error")
        self.assertEqual(opened.call_count, 1)

    def test_chat_transport_error_redacts_traceback(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(self.secret)):
            try:
                self.client(max_retries=0).complete_text("test")
            except RuntimeError as exc:
                self.assertIn("REDACTED", str(exc))
                self.assertNotIn(self.secret, traceback.format_exc())
            else:
                self.fail("expected transport failure")

    def test_retryable_failure_retries_only_up_to_budget(self):
        with patch("urllib.request.urlopen", side_effect=[self.http_error(503, "busy") for _ in range(3)]) as opened:
            with patch.object(UpstageClient, "_sleep_before_retry") as slept:
                with self.assertRaisesRegex(RuntimeError, "503"):
                    self.client(max_retries=2).complete_text("test")
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(slept.call_count, 2)

    def test_retry_jitter_never_exceeds_configured_cap(self):
        with patch("upstage_api_sim.upstage_client.time.sleep") as slept:
            with patch("upstage_api_sim.upstage_client.random.uniform", return_value=0.35):
                self.client(max_retry_delay_seconds=2)._sleep_before_retry(8, None)
        slept.assert_called_once_with(2)

    def test_missing_completion_has_actionable_error(self):
        for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, []):
            with self.subTest(payload=payload):
                client = self.client()
                with patch.object(client, "chat_completion", return_value=payload):
                    with self.assertRaisesRegex(RuntimeError, "no text completion"):
                        client.complete_text("test")

    def test_document_parse_redacts_http_and_transport_failures(self):
        errors = [self.http_error(401, "x" * 1190 + self.secret), urllib.error.URLError(self.secret)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch.dict("os.environ", {"UPSTAGE_API_KEY": self.secret, "UPSTAGE_MAX_RETRIES": "0", "UPSTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0"}):
                    with patch("urllib.request.urlopen", side_effect=error):
                        try:
                            call_document_parse_api(b"%PDF-fixture")
                        except RuntimeError as exc:
                            self.assertNotIn(self.secret[:10], str(exc))
                            self.assertNotIn(self.secret, traceback.format_exc())
                        else:
                            self.fail("expected document failure")
