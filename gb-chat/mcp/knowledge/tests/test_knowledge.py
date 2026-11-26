"""GB Knowledge MCP tests. Corp-ChatWithData is faked with an httpx MockTransport that checks
service tokens the way its services do (HS256, audience = the called service)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid

import httpx
import jwt
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

from gb_knowledge.platform import Platform
from gb_knowledge.server import KnowledgeTools, build_app, user_from_headers
from gb_knowledge.settings import SettingsError, load_settings

SECRET = "s" * 40
KEY = "k" * 64
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
INACTIVE = uuid.UUID("22222222-2222-2222-2222-222222222222")
KB = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER_KB = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
DASH = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")

ENV = {"KNOWLEDGE_MCP_KEY": KEY, "TENANCY_URL": "http://tenancy", "RETRIEVAL_URL": "http://retrieval",
       "SERVICE_JWT_SECRET": SECRET}


class FakeCorp:
    """tenancy + retrieval, enough of each for these tools."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.audits: list[dict] = []
        self.searches: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        token = request.headers.get("X-Service-Token", "")
        try:
            claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience=host)
        except jwt.PyJWTError as exc:
            return httpx.Response(401, json={"detail": str(exc)})
        assert claims["typ"] == "service" and claims["sub"] == "gb-knowledge-mcp"
        self.calls.append((host, path))
        if host == "tenancy":
            return self._tenancy(request, path)
        if host == "retrieval":
            return self._retrieval(request, path)
        return httpx.Response(404)

    def _tenancy(self, request, path):
        if path == f"/internal/users/{USER}":
            return httpx.Response(200, json={"id": str(USER), "can_sign_in": True, "blocking_unit": None})
        if path == f"/internal/users/{INACTIVE}":
            return httpx.Response(200, json={"id": str(INACTIVE), "can_sign_in": False, "blocking_unit": "Retail"})
        if path.startswith("/internal/users/") and path.endswith("/domains"):
            return httpx.Response(200, json=[
                {"id": str(KB), "name": "HR policies", "type": "RAG"},
                {"id": str(DASH), "name": "Sales", "type": "DASHBOARD"}])
        if path.startswith("/internal/users/"):
            return httpx.Response(404, json={"detail": "unknown user"})
        if path.startswith(f"/internal/access/{USER}/domains/"):
            did = path.rsplit("/", 1)[1]
            ok = did in {str(KB), str(DASH)}
            return httpx.Response(200, json={"allowed": ok, "reason": "" if ok else "no grant for this domain"})
        if path == "/internal/audit":
            self.audits.append(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(404)

    def _retrieval(self, request, path):
        if path == "/internal/search":
            body = json.loads(request.content)
            self.searches.append(body)
            return httpx.Response(200, json={"total": 2, "hits": [
                {"text": "Annual leave is 21 days.", "score": 0.91,
                 "reference": {"source": "leave-policy.pdf", "page": 3}, "metadata": {}},
                {"text": "x" * 5000, "score": 0.5, "reference": {}, "metadata": {"source": "handbook.docx"}}]})
        if path == f"/internal/knowledge/{KB}/files":
            return httpx.Response(200, json=[{"source": "leave-policy.pdf", "doc_type": "pdf",
                                              "chunks": 12, "file_url": "/x"}])
        return httpx.Response(404)


@pytest.fixture()
def corp():
    return FakeCorp()


@pytest.fixture()
def settings():
    return load_settings(ENV)


@pytest.fixture()
def platform(corp, settings):
    return Platform(tenancy_url="http://tenancy", retrieval_url="http://retrieval", secret=SECRET,
                    service_name=settings.service_name, transport=httpx.MockTransport(corp.handler))


@pytest.fixture()
def tools(platform, settings):
    return KnowledgeTools(platform, settings)


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_require_secrets():
    with pytest.raises(SettingsError):
        load_settings({})
    with pytest.raises(SettingsError):
        load_settings({**ENV, "KNOWLEDGE_MCP_KEY": "short"})


# ── tools ────────────────────────────────────────────────────────────────────

def test_lists_only_knowledge_bases(tools):
    out = tools.list_knowledge_bases(USER)
    assert out == {"knowledge_bases": [{"id": str(KB), "name": "HR policies"}], "count": 1}


def test_search_returns_cited_passages_and_audits(tools, corp):
    out = tools.search_knowledge(USER, str(KB), "how many leave days?", top_k=50)
    assert corp.searches == [{"domain_id": str(KB), "query": "how many leave days?", "top_k": 20}]
    first, second = out["passages"]
    assert first["source"] == "leave-policy.pdf" and first["location"] == {"page": 3}
    assert second["source"] == "handbook.docx" and second["text"].endswith(" …")
    assert len(second["text"]) <= 2002
    assert corp.audits and corp.audits[0]["action"] == "mcp.knowledge.search"
    assert corp.audits[0]["actor_id"] == str(USER)


def test_search_refuses_without_grant(tools, corp):
    with pytest.raises(Exception, match="no grant"):
        tools.search_knowledge(USER, str(OTHER_KB), "anything")
    assert not corp.searches


def test_search_refuses_a_dashboard_id(tools, corp):
    with pytest.raises(Exception, match="not a knowledge base"):
        tools.search_knowledge(USER, str(DASH), "anything")
    assert not corp.searches


def test_inactive_user_is_refused(tools, corp):
    with pytest.raises(Exception, match="not active"):
        tools.list_knowledge_bases(INACTIVE)


def test_bad_ids_and_queries(tools):
    with pytest.raises(ToolError):
        tools.search_knowledge(USER, "not-an-id", "q")
    with pytest.raises(ToolError):
        tools.search_knowledge(USER, str(KB), "   ")


def test_user_header_is_required():
    with pytest.raises(ToolError):
        user_from_headers({})
    assert user_from_headers({"x-gb-user": str(USER)}) == USER


def test_files(tools):
    out = tools.list_knowledge_files(USER, str(KB))
    assert out["files"] == [{"name": "leave-policy.pdf", "type": "pdf", "chunks": 12}]


# ── over HTTP, through a real MCP client ─────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(platform, settings):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(settings, platform), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_http_requires_the_key(live_server):
    assert httpx.get(live_server + "/health").status_code == 200
    r = httpx.post(live_server + "/mcp", json={})
    assert r.status_code == 401
    r = httpx.post(live_server + "/mcp", json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_mcp_client_end_to_end(live_server):
    async def run():
        headers = {"Authorization": f"Bearer {KEY}", "X-GB-User": str(USER)}
        async with create_mcp_http_client(headers=headers) as http:
            async with streamable_http_client(live_server + "/mcp", http_client=http) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)
                    assert names == ["list_knowledge_bases", "list_knowledge_files", "search_knowledge"]
                    result = await session.call_tool("search_knowledge",
                                                     {"knowledge_base_id": str(KB), "query": "leave"})
                    assert not result.is_error
                    assert "leave-policy.pdf" in result.content[0].text
                    denied = await session.call_tool("search_knowledge",
                                                     {"knowledge_base_id": str(OTHER_KB), "query": "x"})
                    assert denied.is_error and "no grant" in denied.content[0].text
    asyncio.run(run())
