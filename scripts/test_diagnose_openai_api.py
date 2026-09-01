import importlib.util
import json
import os
import sys
import threading
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path


SCRIPT = Path(__file__).with_name("diagnose_openai_api.py")
SPEC = importlib.util.spec_from_file_location("diagnose_openai_api", SCRIPT)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = doctor
SPEC.loader.exec_module(doctor)
FAKE_KEY = "sd" + "-super-secret-test-key"


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def _send(self, status, payload, content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/v1/models":
            if self.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
                self._send(401, {"error": {"message": f"bad key {FAKE_KEY}"}})
                return
            self._send(200, {"object": "list", "data": [{"id": "test-model", "object": "model"}]})
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": {"message": "not found"}})
            return
        if body.get("stream"):
            raw = b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
            self._send(200, raw, "text/event-stream")
            return
        if body.get("tools"):
            self._send(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "get_current_time", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
            return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})


@contextmanager
def mock_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class DoctorTests(unittest.TestCase):
    def test_normalize_resource_url(self):
        self.assertEqual(
            doctor.normalize_base_url("https://example.com/v1/chat/completions"),
            "https://example.com/v1",
        )

    def test_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            doctor.normalize_base_url("https://user:secret@example.com/v1")

    def test_redacts_common_keys(self):
        value = doctor.redact(
            {"authorization": "Bearer secret", "message": f"api_key=abc {FAKE_KEY}"}
        )
        self.assertEqual(value["authorization"], "[REDACTED]")
        self.assertNotIn("abc", value["message"])
        self.assertNotIn(FAKE_KEY, value["message"])

    def test_live_probes_and_json_output(self):
        with mock_server() as base_url:
            previous = os.environ.get("DOCTOR_TEST_KEY")
            os.environ["DOCTOR_TEST_KEY"] = FAKE_KEY
            stdout = StringIO()
            try:
                with redirect_stdout(stdout):
                    exit_code = doctor.main(
                        [
                            "--base-url",
                            base_url,
                            "--api-key-env",
                            "DOCTOR_TEST_KEY",
                            "--model",
                            "test-model",
                            "--all",
                            "--format",
                            "json",
                            "--confirm-live-probe",
                        ]
                    )
            finally:
                if previous is None:
                    os.environ.pop("DOCTOR_TEST_KEY", None)
                else:
                    os.environ["DOCTOR_TEST_KEY"] = previous
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(
            [item["probe"] for item in report["results"]],
            ["models", "chat", "stream", "tools"],
        )
        self.assertNotIn(FAKE_KEY, stdout.getvalue())

    def test_requires_confirmation_before_reading_key(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = doctor.main(["--base-url", "https://example.com/v1"])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-probe", stderr.getvalue())

    def test_401_report_is_redacted(self):
        with mock_server() as base_url:
            result = doctor.probe_models(base_url, "sd-wrong-secret-key", 2)
        self.assertEqual(result.code, "authentication_failed")
        self.assertNotIn(FAKE_KEY, result.evidence)
        self.assertIn("[REDACTED]", result.evidence)


if __name__ == "__main__":
    unittest.main()
