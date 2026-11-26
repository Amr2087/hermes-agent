"""Turn engine tool calls and skills into plain GB Chat wording.

Employees see "Writing Q3-review.pptx", never a raw tool name. Skills that only describe the
engine itself are hidden from the capabilities list.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

# Skills about the engine's own operation, or not useful in a company assistant.
HIDDEN_SKILLS = frozenset({
    "hermes-agent", "claude-code", "codex", "teams-meeting-pipeline", "apple-notes",
    "apple-reminders", "findmy", "imessage", "spotify", "pokemon-player", "minecraft-modpack-server",
})

SKILL_TITLES = {
    "microsoft-office": "Office documents",
    "pdf": "PDF documents",
    "web-pages": "Web pages",
    "pptx-author": "PowerPoint decks",
    "excel-author": "Excel workbooks",
    "google-workspace": "Google Workspace",
    "meeting-action-items": "Meeting action items",
    "document-to-action-items": "Action items from documents",
    "weekly-review-planning": "Weekly planning",
    "grounded-citations": "Research with citations",
    "powerbi-analyst": "Power BI analysis",
    "company-knowledge": "Company knowledge",
}

# Shown on "What I can do" when the engine can't list its skills.
CORE_SKILLS = [
    {"name": "microsoft-office", "category": "documents",
     "description": "Create, read and edit Word, PowerPoint and Excel files."},
    {"name": "pdf", "category": "documents", "description": "Create, fill, merge and extract text from PDF files."},
    {"name": "web-pages", "category": "documents",
     "description": "Build self-contained web pages and one-page reports in HTML."},
    {"name": "meeting-action-items", "category": "productivity",
     "description": "Turn meeting notes into decisions, owners and due dates."},
    {"name": "document-to-action-items", "category": "productivity",
     "description": "Pull the action items out of a document."},
    {"name": "grounded-citations", "category": "research",
     "description": "Research a question on the web and cite every claim."},
]

_FILE_KINDS = {
    ".docx": "Word document", ".pptx": "PowerPoint deck", ".xlsx": "Excel workbook",
    ".csv": "spreadsheet", ".pdf": "PDF", ".html": "web page", ".htm": "web page",
    ".md": "document", ".png": "image", ".jpg": "image", ".svg": "image",
}

_TOOL_LABELS = {
    "terminal": "Running a command",
    "execute_code": "Running code",
    "read_file": "Reading a file",
    "search_files": "Searching files",
    "patch": "Editing a file",
    "web_search": "Searching the web",
    "web_extract": "Reading a web page",
    "skills_list": "Checking available skills",
    "memory": "Updating memory",
    "session_search": "Searching past chats",
    "delegate_task": "Working on a subtask",
    "vision_analyze": "Looking at an image",
    "image_generate": "Creating an image",
    "todo": "Planning the steps",
    "clarify": "Asking you a question",
    "send_message": "Sending a message",
    "cronjob": "Scheduling a task",
    "text_to_speech": "Creating audio",
}

# GB Chat's own MCP servers (gb-chat/mcp). The engine names their tools mcp__<server>__<tool>.
_GB_TOOL_LABELS = {
    "mcp__gb_knowledge__list_knowledge_bases": "Checking your knowledge bases",
    "mcp__gb_knowledge__search_knowledge": "Searching company knowledge",
    "mcp__gb_knowledge__list_knowledge_files": "Listing knowledge base documents",
    "mcp__gb_powerbi__list_datasets": "Checking your Power BI datasets",
    "mcp__gb_powerbi__describe_dataset": "Reading the dataset's data dictionary",
    "mcp__gb_powerbi__resolve_entities": "Matching names to the data",
    "mcp__gb_powerbi__run_dax": "Querying Power BI",
    "mcp__gb_powerbi__ask_powerbi": "Asking the Power BI assistant",
}

_ENGINE_NAMES = re.compile(r"\b(?:Hermes(?:[ -]Agent)?|Nous Research)\b")


def skill_title(name: str) -> str:
    return SKILL_TITLES.get(name) or name.replace("-", " ").replace("_", " ").capitalize()


def _file_label(verb: str, path: Any) -> str:
    if not isinstance(path, str) or not path.strip():
        return f"{verb} a file"
    name = PurePosixPath(path.strip()).name
    return f"{verb} {name}"


def tool_label(tool: str | None, args: Any = None) -> str:
    """One short line describing what a tool call is doing."""
    tool = (tool or "").strip()
    args = args if isinstance(args, dict) else {}
    if tool == "skill_view":
        name = args.get("name") or args.get("skill") or ""
        return f"Using the {skill_title(str(name))} skill" if name else "Loading a skill"
    if tool == "write_file":
        path = args.get("path") or args.get("file_path")
        kind = _FILE_KINDS.get(PurePosixPath(str(path or "")).suffix.lower())
        return _file_label("Writing", path) + (f" ({kind})" if kind else "")
    if tool == "read_file":
        return _file_label("Reading", args.get("path") or args.get("file_path"))
    if tool == "patch":
        return _file_label("Editing", args.get("path") or args.get("file_path"))
    if tool.startswith("browser_"):
        return "Using the browser"
    if tool in _GB_TOOL_LABELS:
        return _GB_TOOL_LABELS[tool]
    if tool.startswith("mcp_") or tool in {"tool_call", "tool_search", "tool_describe"}:
        return "Using a connected app"
    if tool in _TOOL_LABELS:
        return _TOOL_LABELS[tool]
    words = tool.replace("_", " ").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else "Working"


def scrub(text: Any) -> str:
    """Remove the engine's product names from text the engine wrote (labels, errors)."""
    if not isinstance(text, str):
        return ""
    return _ENGINE_NAMES.sub("GB Chat", text)


def visible_skills(skills: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for skill in skills:
        name = str(skill.get("name") or "")
        if not name or name in HIDDEN_SKILLS:
            continue
        out.append({
            "name": name,
            "title": skill_title(name),
            "description": scrub(skill.get("description") or ""),
            "category": str(skill.get("category") or "other"),
        })
    out.sort(key=lambda s: (s["category"], s["title"]))
    return out
