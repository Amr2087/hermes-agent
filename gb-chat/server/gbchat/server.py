"""GB Chat HTTP server (Python standard library only).

Serves the web UI from ``web/`` and the GB Chat API under ``/api``. Turns stream back to the
browser as server-sent events in GB Chat's own event format (see ``gbchat.events``).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from gbchat import __version__, files
from gbchat.config import Settings, load_settings
from gbchat.engine import EngineClient, EngineError, MockEngine
from gbchat.events import TurnTranslator
from gbchat.labels import CORE_SKILLS, scrub, visible_skills

log = logging.getLogger("gbchat")

_CHAT = r"(?P<chat>[A-Za-z0-9_.:\-]{1,200})"
_RUN = r"(?P<run>[A-Za-z0-9_.:\-]{1,200})"
_FILE = r"(?P<name>[^/]{1,200})"

APP_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}
# Files the assistant wrote are untrusted content: never let them run script on this origin.
FILE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'; img-src data: 'self'; style-src 'unsafe-inline'",
}


def compose_message(text: str, attachments: list[str]) -> str:
    text = text.strip()
    if attachments:
        listed = ", ".join(f"uploads/{name}" for name in attachments)
        text = f"{text}\n\n(Attached files, in the uploads folder: {listed})" if text else \
            f"Please look at these files: {listed}"
    return text


def make_engine(settings: Settings):
    if settings.engine_mode == "mock":
        return MockEngine(settings.outputs_dir)
    return EngineClient(settings.engine_url, settings.engine_key)


class GBChatServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: Settings, engine=None):
        self.settings = settings
        self.engine = engine or make_engine(settings)
        settings.outputs_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        super().__init__((settings.host, settings.port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: GBChatServer
    server_version = "GBChat/" + __version__
    sys_version = ""

    # -- helpers ----------------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or APP_HEADERS).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self._send(status, body, "application/json; charset=utf-8", {**APP_HEADERS, "Cache-Control": "no-store"})

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise ValueError("Request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _csrf_ok(self) -> bool:
        # Browsers can't add a custom header cross-site without a CORS preflight, which we never allow.
        return self.headers.get("X-GB-Chat") == "1"

    @property
    def engine(self):
        return self.server.engine

    @property
    def settings(self) -> Settings:
        return self.server.settings

    def _user(self) -> str:
        name = (self.headers.get(self.settings.user_header) or "").strip()
        return name[:120]

    # -- routing ----------------------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path, query = urllib.parse.unquote(parsed.path), urllib.parse.parse_qs(parsed.query)
        routes = [
            ("GET", r"/api/me", self.api_me),
            ("GET", r"/api/status", self.api_status),
            ("GET", r"/api/chats", self.api_list_chats),
            ("POST", r"/api/chats", self.api_create_chat),
            ("PATCH", rf"/api/chats/{_CHAT}", self.api_rename_chat),
            ("DELETE", rf"/api/chats/{_CHAT}", self.api_delete_chat),
            ("GET", rf"/api/chats/{_CHAT}/messages", self.api_messages),
            ("POST", rf"/api/chats/{_CHAT}/messages", self.api_send),
            ("POST", rf"/api/runs/{_RUN}/stop", self.api_stop),
            ("POST", rf"/api/runs/{_RUN}/approval", self.api_approve),
            ("GET", r"/api/skills", self.api_skills),
            ("GET", r"/api/files", self.api_files),
            ("POST", r"/api/uploads", self.api_upload),
            ("GET", rf"/files/outputs/{_FILE}", self.file_output),
            ("GET", rf"/files/uploads/{_FILE}", self.file_upload),
        ]
        try:
            path_known = False
            for verb, pattern, handler in routes:
                match = re.fullmatch(pattern, path)
                if not match:
                    continue
                path_known = True
                if verb == method:
                    if method != "GET" and not self._csrf_ok():
                        return self._error(403, "Missing X-GB-Chat header")
                    return handler(query=query, **match.groupdict())
            if path_known:
                return self._error(405, "Method not allowed")
            if method == "GET" and not path.startswith(("/api/", "/files/")):
                return self.static(path)
            self._error(404, "Not found")
        except EngineError as exc:
            self._error(exc.status if 400 <= exc.status < 600 else 502, scrub(str(exc)))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("Unhandled error on %s %s", method, path)
            self._error(500, "Something went wrong on the GB Chat server.")

    # -- static UI --------------------------------------------------------------------------
    def static(self, path: str) -> None:
        web = self.settings.web_dir
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (web / rel).resolve()
        try:
            target.relative_to(web)
        except ValueError:
            return self._error(404, "Not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # Single-page app: unknown paths get the shell.
            target = web / "index.html"
        cache = "no-cache" if target.suffix in {".html", ".js", ".css"} else "public, max-age=86400"
        self._send(200, target.read_bytes(), files.content_type(target),
                   {**APP_HEADERS, "Cache-Control": cache})

    # -- API --------------------------------------------------------------------------------
    def api_me(self, **_: Any) -> None:
        self._json({"name": self._user() or "", "app": "GB Chat", "company": "GB Corp", "version": __version__})

    def api_status(self, **_: Any) -> None:
        self._json({"engine": self.settings.engine_mode, "healthy": self.engine.health()})

    def api_list_chats(self, **_: Any) -> None:
        self._json({"chats": self.engine.list_chats()})

    def api_create_chat(self, **_: Any) -> None:
        body = self._body_json()
        title = str(body.get("title") or "").strip()[:80] or None
        self._json({"chat": self.engine.create_chat(title)}, 201)

    def api_rename_chat(self, chat: str, **_: Any) -> None:
        title = str(self._body_json().get("title") or "").strip()[:80]
        if not title:
            raise ValueError("Title is required")
        self.engine.rename_chat(chat, title)
        self._json({"ok": True})

    def api_delete_chat(self, chat: str, **_: Any) -> None:
        self.engine.delete_chat(chat)
        self._json({"ok": True})

    def api_messages(self, chat: str, **_: Any) -> None:
        self._json({"messages": self.engine.messages(chat)})

    def api_skills(self, **_: Any) -> None:
        try:
            skills = self.engine.skills()
        except EngineError as exc:
            # The page still works when the engine can't list skills: show the core ones.
            log.warning("Engine skills list unavailable (%s); showing the built-in list", exc)
            skills = CORE_SKILLS
        self._json({"skills": visible_skills(skills)})

    def api_files(self, **_: Any) -> None:
        self._json({"files": files.list_files(self.settings.outputs_dir, "/files/outputs")})

    def api_stop(self, run: str, **_: Any) -> None:
        self.engine.stop(run)
        self._json({"ok": True})

    def api_approve(self, run: str, **_: Any) -> None:
        body = self._body_json()
        choice = str(body.get("choice") or "")
        if choice not in {"once", "session", "always", "deny"}:
            raise ValueError("choice must be once, session, always or deny")
        self.engine.approve(run, choice, body.get("request_id"), body.get("generation"))
        self._json({"ok": True})

    def api_upload(self, query: dict[str, list[str]], **_: Any) -> None:
        name = files.clean_name((query.get("name") or [""])[0])
        if not name:
            raise ValueError("Give the file a plain name")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("The file is empty")
        if length > self.settings.max_upload_bytes:
            return self._error(413, f"Files can be up to {self.settings.max_upload_bytes // (1024 * 1024)} MB")
        path = files.unique_path(self.settings.uploads_dir, name)
        remaining = length
        with open(path, "wb") as fh:
            while remaining:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        self._json({"file": files.describe(path, "/files/uploads")}, 201)

    def _serve_file(self, folder: Path, name: str, query: dict[str, list[str]]) -> None:
        path = files.resolve_in(folder, name)
        if path is None or not path.is_file():
            return self._error(404, "File not found")
        disposition = "attachment" if (query.get("download") or ["0"])[0] == "1" else "inline"
        quoted = urllib.parse.quote(path.name)
        self._send(200, path.read_bytes(), files.content_type(path), {
            **FILE_HEADERS, "Cache-Control": "no-store",
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quoted}"})

    def file_output(self, name: str, query: dict[str, list[str]], **_: Any) -> None:
        self._serve_file(self.settings.outputs_dir, name, query)

    def file_upload(self, name: str, query: dict[str, list[str]], **_: Any) -> None:
        self._serve_file(self.settings.uploads_dir, name, query)

    # -- the streamed turn ------------------------------------------------------------------
    def api_send(self, chat: str, **_: Any) -> None:
        body = self._body_json()
        attachments = [n for n in (files.clean_name(str(a)) for a in body.get("attachments") or []) if n]
        message = compose_message(str(body.get("text") or ""), attachments)
        if not message:
            raise ValueError("Type a message first")

        outputs = self.settings.outputs_dir
        before = files.snapshot(outputs)
        stream = self.engine.stream_turn(chat, message)  # raises EngineError before headers go out

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in APP_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

        translator = TurnTranslator()
        ended = False
        try:
            for name, payload in stream:
                for event in translator.translate(name, payload):
                    if event["type"] == "end":
                        ended = True
                        for path in files.changed_since(outputs, before):
                            self._emit({"type": "file", **files.describe(path, "/files/outputs")})
                    self._emit(event)
        except EngineError as exc:
            self._emit({"type": "error", "message": scrub(str(exc))})
        except (BrokenPipeError, ConnectionResetError):
            close = getattr(stream, "close", None)
            if close:
                close()
            return
        if translator.failure_detail:
            log.warning("Turn %s failed in the engine: %s", translator.run_id, translator.failure_detail)
        if not ended:
            for path in files.changed_since(outputs, before):
                self._emit({"type": "file", **files.describe(path, "/files/outputs")})
            self._emit({"type": "end", "status": "failed" if not translator.final_seen else "completed"})

    def _emit(self, event: dict[str, Any]) -> None:
        data = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"event: {event['type']}\ndata: {data}\n\n".encode())
        self.wfile.flush()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    server = GBChatServer(settings)
    mode = "demo engine (no model)" if settings.engine_mode == "mock" else f"engine at {settings.engine_url}"
    print(f"GB Chat {__version__} on http://{settings.host}:{settings.port}  ·  {mode}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
