"""GB Power BI MCP tests. tenancy, Power BI and the optional agent are faked with httpx
MockTransports; tenancy checks service tokens the way Corp-ChatWithData does."""

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
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

from gb_powerbi.entities import EntityNormalizer
from gb_powerbi.platform import Platform
from gb_powerbi.powerbi import DaxError, PowerBI, check_dax
from gb_powerbi.server import PowerBITools, build_app
from gb_powerbi.settings import SettingsError, load_settings

SECRET = "s" * 40
KEY = "k" * 64
CORE = Fernet.generate_key().decode()
PBI_SECRET = "pbi-client-secret-value"
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
SALES = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
LOCKED = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
KB = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")

ENV = {"POWERBI_MCP_KEY": KEY, "TENANCY_URL": "http://tenancy", "SERVICE_JWT_SECRET": SECRET,
       "CORE_SECRET_KEY": CORE}

SCHEMAS = {
    "index": {"table_routing": {"sales by branch": "Sales"}, "key_measures": ["Total Sales"],
              "default_tables": ["Sales"]},
    "tables": {"Sales": {"purpose": "One row per invoice", "columns": [
        {"name": "Branch", "type": "text", "desc": "branch name"},
        {"name": "Amount", "type": "decimal", "desc": "EGP"}]},
               "Dim Date": {"quote_name": True, "columns": [{"name": "Year", "type": "int"}]}},
    "measures": {"Total Sales": {"what": "Sum of Amount", "dax": "SUM(Sales[Amount])"}},
}
RULES = {"dax_rules": ["Always use measures for totals"], "relationships": ["Sales[Date] -> 'Dim Date'[Date]"],
         "string_values": {"Sales[Branch]": {"Cairo Main": "head office"}},
         "query_patterns": [{"name": "by branch", "dax": "EVALUATE SUMMARIZECOLUMNS(...)"}],
         "fix_rules": [{"id": 1, "title": "Quote tables with spaces", "wrong": "Dim Date[Year]",
                        "correct": "'Dim Date'[Year]"}]}
ENTITIES = {"categories": {"branch": {"template": '  - Branch: "{exact_name}" (code {code})',
                                      "values": [{"name": "Cairo Main", "code": 7}, {"name": "Cairo", "code": 1},
                                                 {"name": "الإسكندرية", "code": 3}]}}}


class FakeCorp:
    def __init__(self):
        self.audits: list[dict] = []
        self.pbi_queries: list[dict] = []
        self.pbi_fail = False

    def tenancy(self, request: httpx.Request) -> httpx.Response:
        claims = jwt.decode(request.headers["X-Service-Token"], SECRET, algorithms=["HS256"], audience="tenancy")
        assert claims["typ"] == "service" and claims["sub"] == "gb-powerbi-mcp"
        path = request.url.path
        if path == f"/internal/users/{USER}":
            return httpx.Response(200, json={"id": str(USER), "can_sign_in": True})
        if path == f"/internal/users/{USER}/domains":
            return httpx.Response(200, json=[{"id": str(SALES), "name": "Sales", "type": "DASHBOARD"},
                                             {"id": str(KB), "name": "HR", "type": "RAG"}])
        if path.startswith(f"/internal/access/{USER}/domains/"):
            did = path.rsplit("/", 1)[1]
            ok = did in {str(SALES), str(KB)}
            return httpx.Response(200, json={"allowed": ok, "reason": "" if ok else "no grant for this domain"})
        if path == f"/internal/domains/{SALES}/runtime":
            return httpx.Response(200, json={
                "domain_id": str(SALES), "domain_name": "Sales", "type": "DASHBOARD", "source_id": "ws-1",
                "source_key": "ds-1", "prompts": {}, "schemas": SCHEMAS, "config": ENTITIES, "rules": RULES,
                "ready": True, "blockers": [], "credentials": {
                    "tenant_id": "t-1", "client_id": "c-1",
                    "client_secret_encrypted": Fernet(CORE.encode()).encrypt(PBI_SECRET.encode()).decode()}})
        if path == f"/internal/domains/{KB}/runtime":
            return httpx.Response(200, json={"domain_id": str(KB), "domain_name": "HR", "type": "RAG",
                                             "source_id": "", "source_key": "hr", "prompts": {}, "schemas": {},
                                             "config": {}, "rules": {}, "ready": True, "blockers": [],
                                             "credentials": None})
        if path == "/internal/audit":
            self.audits.append(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "nope"})

    def powerbi(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token-for-c-1"
        assert request.url.path == "/v1.0/myorg/groups/ws-1/datasets/ds-1/executeQueries"
        body = json.loads(request.content)
        self.pbi_queries.append(body)
        if self.pbi_fail:
            return httpx.Response(400, json={"error": {"code": "DatasetExecuteQueriesError", "pbi.error": {
                "details": [{"detail": {"value": "Column 'Branchh' cannot be found."}}]}}})
        rows = [{"Sales[Branch]": f"B{i}", "[Total Sales]": i * 10} for i in range(300)]
        return httpx.Response(200, json={"results": [{"tables": [{"rows": rows}]}]})


@pytest.fixture()
def corp():
    return FakeCorp()


@pytest.fixture()
def settings():
    return load_settings(ENV)


@pytest.fixture()
def parts(corp, settings):
    platform = Platform(tenancy_url="http://tenancy", secret=SECRET, core_secret_key=CORE,
                        service_name=settings.service_name, transport=httpx.MockTransport(corp.tenancy))
    seen = {}

    def token(ds):
        seen["secret"] = ds.client_secret
        return f"token-for-{ds.client_id}"

    powerbi = PowerBI(token_provider=token, transport=httpx.MockTransport(corp.powerbi))
    return platform, powerbi, seen


@pytest.fixture()
def tools(parts, settings):
    platform, powerbi, _ = parts
    return PowerBITools(platform, powerbi, settings)


def test_settings_require_secrets():
    with pytest.raises(SettingsError):
        load_settings({})
    with pytest.raises(SettingsError):
        load_settings({**ENV, "SERVICE_JWT_SECRET": "short"})


# ── DAX guard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dax", ["EVALUATE Sales", "  -- note\nEVALUATE ROW(\"x\", 1)",
                                 "DEFINE MEASURE Sales[x] = 1 EVALUATE ROW(\"x\", [x])",
                                 "```dax\nEVALUATE Sales\n```", "/* c */ evaluate Sales"])
def test_guard_accepts_queries(dax):
    assert check_dax(dax, 1000).upper().lstrip("`DAX\n").strip()


@pytest.mark.parametrize("dax", ["", "SUM(Sales[Amount])", "DEFINE MEASURE Sales[x] = 1",
                                 '"EVALUATE" Sales', "x" * 2000])
def test_guard_refuses_non_queries(dax):
    with pytest.raises(DaxError):
        check_dax(dax, 1000)


# ── entities ─────────────────────────────────────────────────────────────────

def test_entities_prefer_the_longest_name_and_handle_arabic():
    n = EntityNormalizer(ENTITIES)
    names = [m.exact_name for m in n.find("sales for cairo main and الاسكندريه last year")]
    assert sorted(names) == sorted(["الإسكندرية", "Cairo Main"])  # "Cairo" alone is not a second match
    assert '"Cairo Main" (code 7)' in n.corrections(n.find("cairo main"))


# ── tools ────────────────────────────────────────────────────────────────────

def test_lists_only_dashboards(tools):
    assert tools.list_datasets(USER) == {"datasets": [{"id": str(SALES), "name": "Sales"}], "count": 1}


def test_describe_overview_then_detail(tools):
    overview = tools.describe_dataset(USER, str(SALES))
    assert overview["tables"] == ["Sales", "Dim Date"] and overview["measures"] == ["Total Sales"]
    assert "sales by branch" in overview["overview"] and "Always use measures" in overview["dax_rules"]
    assert "client_secret" not in json.dumps(overview) and PBI_SECRET not in json.dumps(overview)
    detail = tools.describe_dataset(USER, str(SALES), tables=["Sales", "Nope"], measures=["[Total Sales]"])
    assert "Sales[Branch]" in detail["tables"] and "SUM(Sales[Amount])" in detail["measures"]
    assert "Cairo Main" in detail["string_values"] and "'Dim Date'[Year]" in detail["fix_rules"]
    assert detail["unknown"] == {"tables": ["Nope"], "measures": []}


def test_resolve_entities(tools):
    out = tools.resolve_entities(USER, str(SALES), "Cairo Main sales")
    assert out["matches"][0]["exact_name"] == "Cairo Main" and out["matches"][0]["code"] == 7


def test_run_dax_caps_rows_audits_and_keeps_the_secret_server_side(tools, corp, parts):
    out = tools.run_dax(USER, str(SALES), "EVALUATE Sales", max_rows=50)
    assert out["row_count"] == 300 and len(out["rows"]) == 50 and out["truncated"]
    assert out["columns"] == ["Sales[Branch]", "[Total Sales]"]
    assert parts[2]["secret"] == PBI_SECRET  # decrypted for the token call only
    assert PBI_SECRET not in json.dumps(out)
    assert corp.audits[-1]["action"] == "mcp.powerbi.run_dax" and corp.audits[-1]["details"]["ok"]


def test_run_dax_returns_power_bi_errors_for_the_model_to_fix(tools, corp):
    corp.pbi_fail = True
    with pytest.raises(ToolError, match="Branchh"):
        tools.run_dax(USER, str(SALES), "EVALUATE VALUES(Sales[Branchh])")
    assert corp.audits[-1]["details"]["ok"] is False


def test_guard_runs_before_any_call(tools, corp):
    with pytest.raises(ToolError):
        tools.run_dax(USER, str(SALES), "SUM(Sales[Amount])")
    assert not corp.pbi_queries and not corp.audits


def test_no_grant_and_wrong_type_are_refused(tools, corp):
    with pytest.raises(Exception, match="no grant"):
        tools.run_dax(USER, str(LOCKED), "EVALUATE Sales")
    with pytest.raises(Exception, match="not a Power BI dataset"):
        tools.describe_dataset(USER, str(KB))
    assert not corp.pbi_queries


def test_wrong_core_key_is_a_clear_error(corp, settings):
    platform = Platform(tenancy_url="http://tenancy", secret=SECRET, core_secret_key=Fernet.generate_key().decode(),
                        service_name=settings.service_name, transport=httpx.MockTransport(corp.tenancy))
    with pytest.raises(Exception, match="CORE_SECRET_KEY"):
        platform.require_dataset(USER, SALES)


def test_ask_powerbi_collects_the_pipeline_stream(corp, settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "agent":
            jwt.decode(request.headers["X-Service-Token"], SECRET, algorithms=["HS256"], audience="agent-powerbi")
            body = json.loads(request.content)
            assert body["domain"]["credentials"]["client_secret"] == PBI_SECRET
            stream = "".join(f"data: {json.dumps(e)}\n\n" for e in [
                {"step": "dax", "data": "EVALUATE Sales"}, {"step": "rows", "data": [{"a": 1}]},
                {"step": "answer", "data": "Sales were 10."}])
            return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})
        return corp.tenancy(request)
    platform = Platform(tenancy_url="http://tenancy", secret=SECRET, core_secret_key=CORE,
                        service_name=settings.service_name, agent_url="http://agent",
                        transport=httpx.MockTransport(handler))
    tools = PowerBITools(platform, PowerBI(token_provider=lambda ds: "t"), settings)
    out = tools.ask_powerbi(USER, str(SALES), "total sales?")
    assert out["answer"] == "Sales were 10." and out["dax"] == ["EVALUATE Sales"] and out["rows"] == [{"a": 1}]


# ── over HTTP, through a real MCP client ─────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(parts, settings):
    platform, powerbi, _ = parts
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(settings, platform, powerbi), host="127.0.0.1",
                                           port=port, log_level="warning"))
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
    assert httpx.post(live_server + "/mcp", json={}).status_code == 401


def test_mcp_client_end_to_end(live_server):
    async def run():
        headers = {"Authorization": f"Bearer {KEY}", "X-GB-User": str(USER)}
        async with create_mcp_http_client(headers=headers) as http:
            async with streamable_http_client(live_server + "/mcp", http_client=http) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)
                    assert names == ["describe_dataset", "list_datasets", "resolve_entities", "run_dax"]
                    result = await session.call_tool("run_dax", {"dataset_id": str(SALES),
                                                                 "dax": "EVALUATE Sales", "max_rows": 2})
                    assert not result.is_error
                    assert json.loads(result.content[0].text)["row_count"] == 300
    asyncio.run(run())


def test_mcp_without_user_header_is_refused(live_server):
    async def run():
        async with create_mcp_http_client(headers={"Authorization": f"Bearer {KEY}"}) as http:
            async with streamable_http_client(live_server + "/mcp", http_client=http) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("list_datasets", {})
                    assert result.is_error and "X-GB-User" in result.content[0].text
    asyncio.run(run())
