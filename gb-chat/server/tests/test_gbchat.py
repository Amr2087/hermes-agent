"""GB Chat server tests. Run from gb-chat/server:  python3 -m unittest discover -s tests"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gbchat import files
from gbchat.config import load_settings
from gbchat.engine import EngineClient, MockEngine, messages_from_transcript
from gbchat.events import TurnTranslator, parse_sse
from gbchat.labels import scrub, tool_label, visible_skills
from gbchat.server import GBChatServer, compose_message


class LabelTests(unittest.TestCase):
    def test_tool_labels_are_plain_words(self):
        self.assertEqual(tool_label("skill_view", {"name": "microsoft-office"}), "Using the Office documents skill")
        self.assertEqual(tool_label("write_file", {"path": "outputs/Q3.pptx"}), "Writing Q3.pptx (PowerPoint deck)")
        self.assertEqual(tool_label("terminal", {"command": "ls"}), "Running a command")
        self.assertEqual(tool_label("browser_click"), "Using the browser")
        self.assertEqual(tool_label("some_new_tool"), "Some new tool")

    def test_scrub_removes_engine_names(self):
        self.assertEqual(scrub("Hermes Agent failed: Nous Research key missing"), "GB Chat failed: GB Chat key missing")
        self.assertEqual(scrub("hermès bag"), "hermès bag")

    def test_engine_skill_is_hidden(self):
        names = [s["name"] for s in visible_skills([{"name": "hermes-agent"}, {"name": "pdf", "category": "x"}])]
        self.assertEqual(names, ["pdf"])


class EventTests(unittest.TestCase):
    def test_parse_sse(self):
        raw = [b": keepalive\n", b"\n", b"event: assistant.delta\n", b'data: {"delta": "Hi"}\n', b"\n",
               b"event: done\n", b"data: {}\n", b"\n"]
        self.assertEqual(list(parse_sse(raw)), [("assistant.delta", {"delta": "Hi"}), ("done", {})])

    def test_translate_full_turn(self):
        t = TurnTranslator()
        out = []
        for name, payload in [
            ("run.started", {"run_id": "run_1"}),
            ("tool.started", {"run_id": "run_1", "tool_name": "write_file", "args": {"path": "outputs/a.docx"}}),
            ("tool.completed", {"run_id": "run_1", "tool_name": "write_file", "args": {"path": "outputs/a.docx"}}),
            ("tool.started", {"run_id": "run_1", "tool_name": "terminal"}),
            ("tool.failed", {"run_id": "run_1", "tool_name": "terminal"}),
            ("assistant.delta", {"run_id": "run_1", "delta": "Done"}),
            ("assistant.commentary", {"run_id": "run_1", "text": "dup", "already_streamed": True}),
            ("approval.request", {"run_id": "run_1", "request_id": "r1", "description": "rm -rf x",
                                  "choices": ["once", "session", "deny"]}),
            ("assistant.completed", {"run_id": "run_1", "content": "Done"}),
            ("run.completed", {"run_id": "run_1"}),
        ]:
            out.extend(t.translate(name, payload))
        types = [e["type"] for e in out]
        self.assertEqual(types, ["run", "step", "step", "step", "step", "text", "approval", "final", "end"])
        self.assertEqual(out[1]["id"], out[2]["id"])
        self.assertEqual(out[2]["status"], "done")
        self.assertEqual(out[4]["status"], "failed")
        self.assertEqual(out[6]["request_id"], "r1")
        self.assertEqual(out[-1]["status"], "completed")

    def test_failed_turn_hides_engine_troubleshooting(self):
        t = TurnTranslator()
        out = t.translate("assistant.completed", {"completed": False, "interrupted": False,
                                                  "content": "Run `hermes fallback add` to fix"})
        self.assertEqual(out[0]["type"], "error")
        self.assertNotIn("hermes", out[0]["message"].lower())
        self.assertIn("hermes fallback", t.failure_detail)

    def test_errors_are_scrubbed(self):
        out = TurnTranslator().translate("error", {"message": "Hermes Agent crashed"})
        self.assertEqual(out, [{"type": "error", "message": "GB Chat crashed"}])

    def test_transcript_keeps_people_and_answers(self):
        rows = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": [{}]},
                {"role": "tool", "content": "x"}, {"role": "assistant", "content": [{"type": "text", "text": "yo"}]}]
        self.assertEqual([m["text"] for m in messages_from_transcript(rows)], ["hi", "yo"])


class FileTests(unittest.TestCase):
    def test_names_cannot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertIsNone(files.resolve_in(folder, "../secret"))
            self.assertIsNone(files.resolve_in(folder, ".env"))
            self.assertEqual(files.clean_name("../../etc/passwd"), "passwd")
            self.assertEqual(files.clean_name("C:\\Users\\me\\Q3 report.docx"), "Q3 report.docx")
            self.assertIsNotNone(files.resolve_in(folder, "Q3 report.docx"))

    def test_compose_message_lists_uploads(self):
        self.assertIn("uploads/a.xlsx", compose_message("Summarize", ["a.xlsx"]))
        self.assertEqual(compose_message("  hi ", []), "hi")


class _Server:
    def __init__(self, tmp: str, **env):
        base = {"GB_CHAT_PORT": "0", "GB_CHAT_WORKSPACE": tmp, "GB_CHAT_ENGINE": "mock"}
        base.update(env)
        self.settings = load_settings(base)
        engine = MockEngine(self.settings.outputs_dir, step_delay=0) if base["GB_CHAT_ENGINE"] == "mock" else None
        self.httpd = GBChatServer(self.settings, engine)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def call(self, method, path, body=None, headers=None, raw=False):
        data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-GB-Chat", "1")
        if data is not None and not isinstance(body, bytes):
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return (resp, payload) if raw else json.loads(payload)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = _Server(self.tmp.name)

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def test_ui_is_served_with_security_headers(self):
        resp, body = self.srv.call("GET", "/", raw=True)
        self.assertIn(b"GB Chat", body)
        self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])
        resp, body = self.srv.call("GET", "/app.js", raw=True)
        self.assertIn("javascript", resp.headers["Content-Type"])

    def test_mutations_need_the_csrf_header(self):
        req = urllib.request.Request(self.srv.url + "/api/chats", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_full_turn_with_file(self):
        chat = self.srv.call("POST", "/api/chats", {"title": "Deck"})["chat"]
        resp, body = self.srv.call("POST", f"/api/chats/{chat['id']}/messages",
                                   {"text": "Make a PowerPoint deck on safety"}, raw=True)
        self.assertEqual(resp.headers["Content-Type"].split(";")[0], "text/event-stream")
        events = [e for _, e in parse_sse(body.splitlines(keepends=True))]
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "run")
        self.assertIn("step", types)
        self.assertIn("file", types)
        self.assertEqual(types[-1], "end")
        file_event = next(e for e in events if e["type"] == "file")
        self.assertEqual(file_event["name"], "Demo-deck-outline.html")
        resp, _ = self.srv.call("GET", file_event["url"] + "?download=1", raw=True)
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        self.assertIn("sandbox", resp.headers["Content-Security-Policy"])
        history = self.srv.call("GET", f"/api/chats/{chat['id']}/messages")["messages"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        listed = self.srv.call("GET", "/api/files")["files"]
        self.assertEqual(listed[0]["name"], "Demo-deck-outline.html")

    def test_approval_round_trip(self):
        chat = self.srv.call("POST", "/api/chats", {})["chat"]
        result = {}

        def run_turn():
            _, body = self.srv.call("POST", f"/api/chats/{chat['id']}/messages", {"text": "delete old files"}, raw=True)
            result["events"] = [e for _, e in parse_sse(body.splitlines(keepends=True))]

        t = threading.Thread(target=run_turn)
        t.start()
        engine: MockEngine = self.srv.httpd.engine
        for _ in range(100):
            if engine._approvals:
                break
            time.sleep(0.02)
        run_id = next(iter(engine._approvals))
        self.srv.call("POST", f"/api/runs/{run_id}/approval", {"choice": "deny", "request_id": "demo_1"})
        t.join(5)
        types = [e["type"] for e in result["events"]]
        self.assertIn("approval", types)
        self.assertEqual(result["events"][-1], {"type": "end", "status": "completed"})

    def test_upload_and_bad_names(self):
        up = self.srv.call("POST", "/api/uploads?name=Sales%20Q3.csv", b"a,b\n1,2\n",
                           headers={"Content-Type": "application/octet-stream"})["file"]
        self.assertEqual(up["name"], "Sales Q3.csv")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.srv.call("GET", "/files/outputs/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(ctx.exception.code, 404)

    def test_skills_hide_engine_internals(self):
        names = [s["name"] for s in self.srv.call("GET", "/api/skills")["skills"]]
        self.assertIn("microsoft-office", names)
        self.assertNotIn("hermes-agent", names)


class _FakeEngine(BaseHTTPRequestHandler):
    """Speaks the engine's REST API, enough to check EngineClient's requests."""
    seen: list = []

    def log_message(self, *a):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path.startswith("/api/sessions?"):
            return self._json({"data": [{"id": "s1", "title": "T", "preview": "p", "last_active": 5}]})
        if self.path.startswith("/v1/skills"):
            return self._json([{"name": "pdf"}])
        self._json({"error": {"message": "nope"}}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.seen.append(("POST", self.path, body))
        if self.path == "/api/sessions":
            return self._json({"session": {"id": "s2", "title": body.get("title")}}, 201)
        if self.path.endswith("/chat/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for name, data in [("run.started", {"run_id": "r9"}), ("assistant.delta", {"delta": "ok"}),
                               ("assistant.completed", {"content": "ok"}), ("run.completed", {}), ("done", {})]:
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            return
        if self.path.endswith("/approval"):
            return self._json({"ok": True})
        self._json({"error": "unknown"}, 404)


class SkillsFallbackTests(unittest.TestCase):
    def test_skills_page_survives_engine_error(self):
        class Broken(MockEngine):
            def skills(self):
                from gbchat.engine import EngineError
                raise EngineError("Failed to enumerate skills", 500)

        with tempfile.TemporaryDirectory() as d:
            settings = load_settings({"GB_CHAT_PORT": "0", "GB_CHAT_WORKSPACE": d, "GB_CHAT_ENGINE": "mock"})
            httpd = GBChatServer(settings, Broken(settings.outputs_dir))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                url = f"http://127.0.0.1:{httpd.server_address[1]}/api/skills"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    names = [s["name"] for s in json.loads(resp.read())["skills"]]
                self.assertIn("microsoft-office", names)
            finally:
                httpd.shutdown()
                httpd.server_close()


class EngineClientTests(unittest.TestCase):
    def setUp(self):
        _FakeEngine.seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEngine)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = EngineClient(f"http://127.0.0.1:{self.httpd.server_address[1]}", "k3y")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_client_speaks_engine_api(self):
        self.assertEqual(self.client.list_chats()[0], {"id": "s1", "title": "T", "preview": "p", "updated": 5})
        self.assertEqual(_FakeEngine.seen[0][2], "Bearer k3y")
        self.assertEqual(self.client.create_chat("Hello")["id"], "s2")
        events = list(self.client.stream_turn("s2", "hi"))
        self.assertEqual([n for n, _ in events][-1], "done")
        self.assertEqual(_FakeEngine.seen[-1][2], {"message": "hi"})
        self.client.approve("r9", "once", "req", 3)
        self.assertEqual(_FakeEngine.seen[-1][2], {"choice": "once", "request_id": "req", "execution_generation": 3})
        with self.assertRaises(Exception) as ctx:
            self.client.messages("missing")
        self.assertIn("nope", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
