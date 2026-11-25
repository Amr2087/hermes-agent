"""Translate the engine's turn events into GB Chat events.

GB Chat events (sent to the browser as server-sent events, all with a ``type`` field):

  run       {run_id}                         the turn started; run_id is used for stop/approve
  text      {delta}                          a piece of the answer
  note      {text}                           a short progress note from the assistant
  step      {id, label, status}              a tool step: status running | done | failed
  thinking  {}                               the model is reasoning (no content is exposed)
  approval  {run_id, request_id, generation, title, detail, choices}
  final     {content}                        the full answer text
  file      {name, url, size, kind}          a file the turn created or changed
  error     {message}
  end       {status}                         completed | failed | cancelled | interrupted
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

from gbchat.labels import scrub, tool_label


def parse_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse a server-sent event stream into (event name, JSON payload) pairs."""
    event, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                text = "\n".join(data)
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = {"text": text}
                yield event, payload if isinstance(payload, dict) else {"value": payload}
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        try:
            yield event, json.loads("\n".join(data))
        except ValueError:
            pass


def failure_message(exit_reason: object) -> str:
    reason = str(exit_reason or "").lower()
    if "budget" in reason or "iteration" in reason or "limit" in reason:
        return "This task needed more steps than GB Chat is allowed in one go. Try splitting it into smaller requests."
    return ("GB Chat couldn't finish this answer because its AI model didn't respond. Try again in a minute. "
            "If it keeps happening, ask IT to check the model provider.")


class TurnTranslator:
    """Stateful: pairs tool.started with its completion so each step keeps one id."""

    def __init__(self) -> None:
        self._open_steps: dict[str, list[str]] = {}
        self._step_counter = 0
        self.run_id: str | None = None
        self.final_seen = False
        self.failure_detail = ""

    def _new_step(self, tool: str) -> str:
        self._step_counter += 1
        step_id = f"s{self._step_counter}"
        self._open_steps.setdefault(tool, []).append(step_id)
        return step_id

    def _close_step(self, tool: str) -> str:
        stack = self._open_steps.get(tool) or []
        return stack.pop(0) if stack else self._new_step_closed()

    def _new_step_closed(self) -> str:
        self._step_counter += 1
        return f"s{self._step_counter}"

    def translate(self, name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = payload.get("run_id")
        if isinstance(run_id, str) and run_id and self.run_id is None:
            self.run_id = run_id
            out: list[dict[str, Any]] = [{"type": "run", "run_id": run_id}]
        else:
            out = []

        if name == "assistant.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append({"type": "text", "delta": delta})
        elif name == "assistant.commentary":
            text = payload.get("text")
            if isinstance(text, str) and text.strip() and not payload.get("already_streamed"):
                out.append({"type": "note", "text": text.strip()})
        elif name == "tool.started":
            tool = str(payload.get("tool_name") or payload.get("tool") or "")
            out.append({"type": "step", "id": self._new_step(tool),
                        "label": tool_label(tool, payload.get("args")), "status": "running"})
        elif name in {"tool.completed", "tool.failed"}:
            tool = str(payload.get("tool_name") or payload.get("tool") or "")
            failed = name == "tool.failed" or bool(payload.get("error"))
            out.append({"type": "step", "id": self._close_step(tool),
                        "label": tool_label(tool, payload.get("args")),
                        "status": "failed" if failed else "done"})
        elif name == "tool.progress":
            if payload.get("tool_name") == "_thinking":
                out.append({"type": "thinking"})
        elif name == "approval.request":
            choices = [c for c in (payload.get("choices") or ["once", "deny"]) if isinstance(c, str)]
            out.append({
                "type": "approval",
                "run_id": payload.get("run_id") or self.run_id,
                "request_id": payload.get("request_id") or payload.get("prompt_id"),
                "generation": payload.get("execution_generation"),
                "title": scrub(payload.get("title") or "GB Chat needs your permission"),
                "detail": scrub(payload.get("description") or payload.get("command") or ""),
                "choices": choices,
            })
        elif name == "assistant.completed":
            self.final_seen = True
            if payload.get("completed") is False and not payload.get("interrupted"):
                # On a failed turn the engine's "answer" is its own troubleshooting text (CLI
                # commands, provider internals). Employees get a plain message; the server logs
                # the original.
                self.failure_detail = str(payload.get("content") or "")
                out.append({"type": "error", "message": failure_message(payload.get("turn_exit_reason"))})
            else:
                out.append({"type": "final", "content": payload.get("content") or ""})
        elif name.startswith("run.") and name not in {"run.started"}:
            status = name.split(".", 1)[1]
            if status in {"completed", "failed", "cancelled", "interrupted"}:
                if status == "failed" and payload.get("error"):
                    out.append({"type": "error", "message": scrub(str(payload.get("error")))})
                out.append({"type": "end", "status": status})
        elif name == "error":
            out.append({"type": "error", "message": scrub(payload.get("message") or "Something went wrong.")})
        return out
