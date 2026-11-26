"""GB Knowledge: company knowledge bases (Corp-ChatWithData RAG) as MCP tools for GB Chat.

Tools, not an agent: this server never calls a model. It checks who is asking, checks their
grant in tenancy, and returns retrieval's evidence (chunks with their sources). The GB Chat
agent writes the answer and cites the sources.

Who is asking: the engine sends `X-GB-User: <tenancy user id>` with every request, and
`Authorization: Bearer <KNOWLEDGE_MCP_KEY>`. The header is trusted only because the key proves the
request came from a GB Chat engine configured for that user. Requests without the key are
refused before they reach a tool.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Mapping
from uuid import UUID

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .platform import AccessDenied, Platform, PlatformError
from .settings import Settings

logger = logging.getLogger("gb_knowledge")

USER_HEADER = "x-gb-user"

INSTRUCTIONS = (
    "Search GB Corp's internal knowledge bases (policies, procedures, reports, manuals). "
    "Call list_knowledge_bases first to see what this employee can search, then search_knowledge. "
    "Answer only from the returned passages and cite each claim with its source. If nothing "
    "relevant comes back, say so instead of guessing."
)


# ── Tool logic (plain methods, so tests can call them without a transport) ───

class KnowledgeTools:
    def __init__(self, platform: Platform, settings: Settings) -> None:
        self.platform = platform
        self.settings = settings

    def list_knowledge_bases(self, user_id: UUID) -> dict:
        self._require_user(user_id)
        kbs = self.platform.knowledge_bases(user_id)
        return {"knowledge_bases": [{"id": str(k["id"]), "name": k.get("name", "")} for k in kbs],
                "count": len(kbs)}

    def search_knowledge(self, user_id: UUID, knowledge_base_id: str, query: str,
                         top_k: int | None = None) -> dict:
        kb_id = _uuid(knowledge_base_id, "knowledge_base_id")
        query = (query or "").strip()
        if not query:
            raise ToolError("query is empty: say what to look for")
        if len(query) > 2000:
            raise ToolError("query is too long: keep it under 2,000 characters")
        k = top_k or self.settings.default_top_k
        k = max(1, min(int(k), self.settings.max_top_k))
        self._require_user(user_id)
        kb = self.platform.require_knowledge_base(user_id, kb_id)
        hits = self.platform.search(kb_id, query, k)
        passages = [self._passage(i, h) for i, h in enumerate(hits, 1)]
        if self.settings.audit:
            self.platform.audit(user_id=user_id, action="mcp.knowledge.search", entity_id=kb_id,
                                details={"query": query[:500], "hits": len(passages)})
        return {"knowledge_base": {"id": str(kb_id), "name": kb.get("name", "")},
                "query": query, "passages": passages, "count": len(passages),
                "note": ("Cite passages by their source. No passages means the knowledge base has "
                         "nothing on this: say so." if passages else
                         "Nothing relevant was found in this knowledge base.")}

    def list_knowledge_files(self, user_id: UUID, knowledge_base_id: str) -> dict:
        kb_id = _uuid(knowledge_base_id, "knowledge_base_id")
        self._require_user(user_id)
        kb = self.platform.require_knowledge_base(user_id, kb_id)
        files = self.platform.files(kb_id)
        return {"knowledge_base": {"id": str(kb_id), "name": kb.get("name", "")},
                "files": [{"name": f.get("source", ""), "type": f.get("doc_type", ""),
                           "chunks": f.get("chunks", 0)} for f in files],
                "count": len(files)}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_user(self, user_id: UUID) -> None:
        user = self.platform.user(user_id)
        if not user.get("can_sign_in", False):
            unit = user.get("blocking_unit")
            raise AccessDenied("your account is not active in Corp-ChatWithData"
                               + (f" (unit '{unit}' is inactive)" if unit else ""))

    def _passage(self, n: int, hit: dict) -> dict:
        ref = hit.get("reference") if isinstance(hit.get("reference"), dict) else {}
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        source = (ref.get("source") or ref.get("file") or ref.get("title")
                  or meta.get("source") or meta.get("file_name") or "unknown source")
        location = {k: ref.get(k, meta.get(k)) for k in ("page", "sheet", "section", "timestamp", "start")
                    if ref.get(k, meta.get(k)) not in (None, "")}
        text = str(hit.get("text") or "")
        limit = self.settings.max_chunk_chars
        return {"n": n, "source": str(source), "location": location,
                "score": round(float(hit.get("score") or 0.0), 4),
                "text": text if len(text) <= limit else text[:limit] + " …"}


def _uuid(value: Any, name: str) -> UUID:
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError):
        raise ToolError(f"{name} must be an id from the list tool, not {value!r}") from None


def user_from_headers(headers: Mapping[str, str] | None) -> UUID:
    raw = ""
    if headers:
        raw = headers.get(USER_HEADER) or headers.get("X-GB-User") or ""
    try:
        return UUID(raw.strip())
    except ValueError:
        raise ToolError("GB Chat did not say which employee is asking (X-GB-User is missing or "
                        "not a Corp-ChatWithData user id)") from None


def _run(fn, *args, **kwargs) -> Any:
    """Turn platform failures into tool errors the model can read and act on."""
    try:
        return fn(*args, **kwargs)
    except AccessDenied as exc:
        raise ToolError(str(exc)) from None
    except PlatformError as exc:
        logger.warning("platform error: %s", exc)
        raise ToolError(f"the knowledge service is unavailable right now: {exc}") from None


# ── MCP server ───────────────────────────────────────────────────────────────

def build_server(settings: Settings, platform: Platform | None = None) -> tuple[MCPServer, KnowledgeTools]:
    platform = platform or Platform(tenancy_url=settings.tenancy_url, retrieval_url=settings.retrieval_url,
                                    secret=settings.service_jwt_secret, service_name=settings.service_name,
                                    timeout=settings.timeout_seconds)
    tools = KnowledgeTools(platform, settings)
    mcp = MCPServer(name="gb-knowledge", title="GB Corp knowledge bases", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_knowledge_bases(ctx: Context) -> dict:
        """List the company knowledge bases this employee may search, with their ids."""
        return _run(tools.list_knowledge_bases, user_from_headers(ctx.headers))

    @mcp.tool()
    def search_knowledge(knowledge_base_id: str, query: str, ctx: Context, top_k: int | None = None) -> dict:
        """Search one knowledge base and return the most relevant passages with their sources.

        knowledge_base_id: an id from list_knowledge_bases.
        query: what to look for, in the employee's words or a focused rephrasing.
        top_k: how many passages to return (default 8, at most 20).
        """
        return _run(tools.search_knowledge, user_from_headers(ctx.headers), knowledge_base_id, query, top_k)

    @mcp.tool()
    def list_knowledge_files(knowledge_base_id: str, ctx: Context) -> dict:
        """List the documents in one knowledge base (names and types), for questions like
        "what's in the HR policies base?"."""
        return _run(tools.list_knowledge_files, user_from_headers(ctx.headers), knowledge_base_id)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "gb-knowledge"})

    return mcp, tools


class BearerKeyMiddleware:
    """Refuse every HTTP request (except /health) that lacks the shared key."""

    def __init__(self, app, key: str) -> None:
        self.app = app
        self._key = key.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token.encode(), self._key):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def build_app(settings: Settings, platform: Platform | None = None):
    mcp, _ = build_server(settings, platform)
    app = mcp.streamable_http_app(host=settings.host, stateless_http=True)
    return BearerKeyMiddleware(app, settings.mcp_key)
