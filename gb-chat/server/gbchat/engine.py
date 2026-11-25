"""Clients for the agent engine.

``EngineClient`` talks to the engine's local REST API (sessions, streamed turns, approvals,
skills). ``MockEngine`` is a built-in stand-in with the same interface and event shapes, so the
UI can be developed and demoed without an engine or a model key.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

from gbchat.events import parse_sse

EngineEvent = tuple[str, dict[str, Any]]


class EngineError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def chat_from_session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "preview": row.get("preview") or "",
        "updated": row.get("last_active") or row.get("started_at") or 0,
    }


def messages_from_transcript(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep what a person said and what the assistant answered; drop tool plumbing."""
    out = []
    for row in rows:
        role = row.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _flatten_content(row.get("content")).strip()
        if not text:
            continue
        out.append({"role": role, "text": text, "at": row.get("timestamp")})
    return out


class EngineClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Any = None, *, stream: bool = False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if stream:
            req.add_header("Accept", "text/event-stream")
        try:
            return urllib.request.urlopen(req, timeout=None if stream else self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
                message = (detail.get("error") or {}).get("message") if isinstance(detail.get("error"), dict) \
                    else detail.get("error") or detail.get("message")
            except ValueError:
                message = None
            raise EngineError(message or f"Engine returned HTTP {exc.code}", exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise EngineError(
                "The GB Chat engine is not reachable. Ask IT to check that it is running.", 503) from exc

    def _json(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        with self._request(method, path, body) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    # -- chats ------------------------------------------------------------------------------
    def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        data = self._json("GET", f"/api/sessions?limit={int(limit)}&source=api_server")
        return [chat_from_session(r) for r in data.get("data", []) if not r.get("hidden")]

    def create_chat(self, title: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        data = self._json("POST", "/api/sessions", body)
        return chat_from_session(data.get("session") or {})

    def rename_chat(self, chat_id: str, title: str) -> None:
        self._json("PATCH", f"/api/sessions/{urllib.parse.quote(chat_id, safe='')}", {"title": title})

    def delete_chat(self, chat_id: str) -> None:
        self._json("DELETE", f"/api/sessions/{urllib.parse.quote(chat_id, safe='')}")

    def messages(self, chat_id: str) -> list[dict[str, Any]]:
        data = self._json("GET", f"/api/sessions/{urllib.parse.quote(chat_id, safe='')}/messages?inline_images=false")
        return messages_from_transcript(data.get("data", []))

    # -- turns ------------------------------------------------------------------------------
    def stream_turn(self, chat_id: str, message: str) -> Iterator[EngineEvent]:
        path = f"/api/sessions/{urllib.parse.quote(chat_id, safe='')}/chat/stream"
        resp = self._request("POST", path, {"message": message}, stream=True)
        try:
            yield from parse_sse(resp)
        finally:
            resp.close()

    def stop(self, run_id: str) -> None:
        self._json("POST", f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stop", {})

    def approve(self, run_id: str, choice: str, request_id: str | None, generation: Any) -> None:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        if isinstance(generation, int):
            body["execution_generation"] = generation
        self._json("POST", f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/approval", body)

    # -- capabilities -----------------------------------------------------------------------
    def skills(self) -> list[dict[str, Any]]:
        data = self._json("GET", "/v1/skills")
        return data if isinstance(data, list) else data.get("data", [])

    def health(self) -> bool:
        try:
            self._json("GET", "/health")
            return True
        except EngineError:
            return False


# ------------------------------------------------------------------------------------------
# Demo engine
# ------------------------------------------------------------------------------------------

_MOCK_SKILLS = [
    {"name": "microsoft-office", "description": "Create, read, and edit Word, PowerPoint, and Excel files.",
     "category": "productivity"},
    {"name": "pdf", "description": "Create, fill, merge and extract text from PDF files.", "category": "productivity"},
    {"name": "meeting-action-items", "description": "Turn meeting notes into owners and due dates.",
     "category": "productivity"},
    {"name": "grounded-citations", "description": "Research a question and cite every claim.", "category": "research"},
    {"name": "hermes-agent", "description": "Engine manual (hidden from employees).", "category": "internal"},
]


class MockEngine:
    """In-memory stand-in for the engine. Replies are canned; files it writes are real."""

    def __init__(self, outputs_dir: Path, step_delay: float = 0.35):
        self.outputs_dir = outputs_dir
        self.step_delay = step_delay
        self._chats: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, tuple[threading.Event, list[str]]] = {}
        self._stopped: set[str] = set()
        self._lock = threading.Lock()

    def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            chats = sorted(self._chats.values(), key=lambda c: c["updated"], reverse=True)
        return [{k: c[k] for k in ("id", "title", "preview", "updated")} for c in chats[:limit]]

    def create_chat(self, title: str | None = None) -> dict[str, Any]:
        chat = {"id": f"demo_{uuid.uuid4().hex[:10]}", "title": title or "", "preview": "",
                "updated": time.time(), "messages": []}
        with self._lock:
            self._chats[chat["id"]] = chat
        return {k: chat[k] for k in ("id", "title", "preview", "updated")}

    def _chat(self, chat_id: str) -> dict[str, Any]:
        chat = self._chats.get(chat_id)
        if chat is None:
            raise EngineError("Chat not found", 404)
        return chat

    def rename_chat(self, chat_id: str, title: str) -> None:
        with self._lock:
            self._chat(chat_id)["title"] = title

    def delete_chat(self, chat_id: str) -> None:
        with self._lock:
            self._chats.pop(chat_id, None)

    def messages(self, chat_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._chat(chat_id)["messages"])

    def skills(self) -> list[dict[str, Any]]:
        return list(_MOCK_SKILLS)

    def health(self) -> bool:
        return True

    def stop(self, run_id: str) -> None:
        self._stopped.add(run_id)
        pending = self._approvals.get(run_id)
        if pending:
            pending[1].append("deny")
            pending[0].set()

    def approve(self, run_id: str, choice: str, request_id: str | None, generation: Any) -> None:
        pending = self._approvals.get(run_id)
        if not pending:
            raise EngineError("No approval is waiting for this run", 409)
        pending[1].append(choice)
        pending[0].set()

    # -- the canned turn --------------------------------------------------------------------
    def _plan(self, message: str) -> tuple[str | None, str, str]:
        """(file name or None, skill, answer) for a demo request."""
        text = message.lower()
        if any(w in text for w in ("deck", "slides", "powerpoint", "pptx", "presentation")):
            return "Demo-deck-outline.html", "microsoft-office", "deck"
        if any(w in text for w in ("excel", "xlsx", "spreadsheet", "workbook", "budget", "csv")):
            return "Demo-budget.csv", "microsoft-office", "sheet"
        if any(w in text for w in ("word", "docx", "report", "memo", "letter", "document", "html", "page")):
            return "Demo-report.html", "microsoft-office", "report"
        return None, "", "chat"

    def _write_demo_file(self, name: str, kind: str, prompt: str) -> None:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        path = self.outputs_dir / name
        if kind == "sheet":
            path.write_text(
                "Line item,Q1,Q2,Q3,Q4\nSalaries,120000,120000,125000,125000\n"
                "Marketing,30000,45000,40000,60000\nTravel,8000,9000,7500,12000\n", encoding="utf-8")
            return
        safe = (prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))[:300]
        title = "Deck outline" if kind == "deck" else "Report"
        path.write_text(
            "<!doctype html><html><head><meta charset='utf-8'><title>GB Chat demo</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:48px;color:#1B2230}"
            "h1{color:#2A51A3}</style></head><body>"
            f"<h1>{title} (demo)</h1><p>This file was made by GB Chat's demo engine for:</p>"
            f"<blockquote>{safe}</blockquote><p>Connect the real engine to get full Word, "
            "PowerPoint and Excel files.</p></body></html>", encoding="utf-8")

    def stream_turn(self, chat_id: str, message: str) -> Iterator[EngineEvent]:
        with self._lock:
            chat = self._chat(chat_id)
            chat["messages"].append({"role": "user", "text": message, "at": time.time()})
            chat["preview"] = message[:120]
            chat["updated"] = time.time()
        run_id = f"run_demo_{uuid.uuid4().hex[:8]}"
        yield "run.started", {"run_id": run_id}
        file_name, skill, kind = self._plan(message)
        steps: list[tuple[str, dict[str, Any]]] = []
        if skill:
            steps.append(("skill_view", {"name": skill}))
            steps.append(("write_file", {"path": f"outputs/{file_name}"}))
            steps.append(("read_file", {"path": f"outputs/{file_name}"}))
        else:
            steps.append(("session_search", {"query": message[:40]}))

        if "delete" in message.lower():
            event = threading.Event()
            answers: list[str] = []
            self._approvals[run_id] = (event, answers)
            yield "approval.request", {
                "run_id": run_id, "request_id": "demo_1", "description": "Delete files in outputs/",
                "command": "rm outputs/*", "choices": ["once", "deny"]}
            event.wait(timeout=300)
            self._approvals.pop(run_id, None)
            if not answers or answers[0] == "deny":
                reply = "Okay, I left your files alone."
                yield from self._say(run_id, chat, reply)
                return

        for tool, args in steps:
            if run_id in self._stopped:
                yield "run.cancelled", {"run_id": run_id}
                yield "done", {}
                return
            yield "tool.started", {"run_id": run_id, "tool_name": tool, "args": args}
            time.sleep(self.step_delay)
            if tool == "write_file" and file_name:
                self._write_demo_file(file_name, kind, message)
            yield "tool.completed", {"run_id": run_id, "tool_name": tool, "args": args}

        if file_name:
            reply = (f"Here's a first draft: **{file_name}**.\n\n"
                     "This is GB Chat's demo engine, so the file is a placeholder. With the real engine "
                     "connected, the same request produces a finished Office file.\n\n"
                     "- Open it with the download button below\n- Tell me what to change and I'll revise it")
        else:
            reply = ("Hi, I'm **GB Chat**, GB Corp's internal assistant. I'm running in demo mode, so my "
                     "answers are canned.\n\nTry asking me for:\n\n"
                     "1. A Word report or memo\n2. A PowerPoint deck\n3. An Excel budget\n\n"
                     "| Format | What I make |\n| --- | --- |\n| .docx | Reports, letters, memos |\n"
                     "| .pptx | Decks with GB Corp styling |\n| .xlsx | Models, budgets, trackers |")
        yield from self._say(run_id, chat, reply)

    def _say(self, run_id: str, chat: dict[str, Any], reply: str) -> Iterator[EngineEvent]:
        for i in range(0, len(reply), 12):
            yield "assistant.delta", {"run_id": run_id, "delta": reply[i:i + 12]}
            time.sleep(0.01)
        with self._lock:
            chat["messages"].append({"role": "assistant", "text": reply, "at": time.time()})
            chat["updated"] = time.time()
        yield "assistant.completed", {"run_id": run_id, "content": reply}
        yield "run.completed", {"run_id": run_id}
        yield "done", {}
