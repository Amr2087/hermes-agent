"""GB Power BI: Corp-ChatWithData Power BI datasets as MCP tools for GB Chat.

Tools, not an agent: the GB Chat agent writes the DAX, guided by the `powerbi-analyst` skill and
by what these tools return. The deterministic parts of Corp-ChatWithData's DAX pipeline stay
deterministic here: the data dictionary (describe_dataset), entity resolution
(resolve_entities) and execution with its error text (run_dax). Its full pipeline is optionally
offered as ask_powerbi, to compare answers while the tool route is being proven.

Who is asking: the engine sends `X-GB-User: <tenancy user id>` and
`Authorization: Bearer <POWERBI_MCP_KEY>`. Every tool re-checks the grant in tenancy. The
service-principal secret is decrypted here and never returned.
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

from .catalog import Catalog
from .entities import EntityNormalizer
from .platform import AccessDenied, Platform, PlatformError
from .powerbi import DaxError, PowerBI, check_dax
from .settings import Settings

logger = logging.getLogger("gb_powerbi")

USER_HEADER = "x-gb-user"
MAX_CELL_CHARS = 500

INSTRUCTIONS = (
    "Query GB Corp's Power BI datasets with DAX. Load the powerbi-analyst skill before the first "
    "query. Order: list_datasets, describe_dataset, resolve_entities on the question, write DAX "
    "using only names from describe_dataset, run_dax, fix and retry on error (at most 5 tries), "
    "then answer from the returned rows only."
)


class PowerBITools:
    def __init__(self, platform: Platform, powerbi: PowerBI, settings: Settings) -> None:
        self.platform = platform
        self.powerbi = powerbi
        self.settings = settings

    # ── tools ────────────────────────────────────────────────────────────────

    def list_datasets(self, user_id: UUID) -> dict:
        self._require_user(user_id)
        rows = self.platform.datasets(user_id)
        return {"datasets": [{"id": str(d["id"]), "name": d.get("name", "")} for d in rows],
                "count": len(rows)}

    def describe_dataset(self, user_id: UUID, dataset_id: str, tables: list[str] | None = None,
                         measures: list[str] | None = None) -> dict:
        ds = self._dataset(user_id, dataset_id)
        cat = Catalog(ds.schemas, ds.rules)
        if not tables and not measures:
            return _compact({
                "dataset": {"id": ds.domain_id, "name": ds.name},
                "overview": cat.index_context(),
                "tables": cat.table_names(),
                "measures": cat.measure_names(),
                "suggested_tables": cat.default_tables(),
                "relationships": cat.relationships(),
                "dax_rules": cat.dax_rules(),
                "key_facts": cat.key_facts(),
                "reference": cat.reference_blocks(),
                "entity_resolution": ("available: call resolve_entities with the question"
                                      if EntityNormalizer(ds.entities).configured else "not configured"),
                "next": "Call describe_dataset again with the tables and measures you will use, "
                        "to get their columns, definitions, exact string values and query patterns.",
            })
        unknown_t = [t for t in tables or [] if t not in cat.tables]
        unknown_m = [m.strip("[]") for m in measures or [] if m.strip("[]") not in cat.measures]
        return _compact({
            "dataset": {"id": ds.domain_id, "name": ds.name},
            "tables": cat.tables_context(tables or []),
            "measures": cat.measures_context([m.strip("[]") for m in measures or []]),
            "string_values": cat.string_values(tables or []),
            "query_patterns": cat.query_patterns(),
            "fix_rules": cat.fix_rules(),
            "unknown": ({"tables": unknown_t, "measures": unknown_m}
                        if unknown_t or unknown_m else None),
        })

    def resolve_entities(self, user_id: UUID, dataset_id: str, text: str) -> dict:
        ds = self._dataset(user_id, dataset_id)
        normalizer = EntityNormalizer(ds.entities)
        if not normalizer.configured:
            return {"matches": [], "corrections": "",
                    "note": "This dataset has no entity dictionary; check names against string values."}
        matches = normalizer.find(text or "")
        return {"matches": [m.to_dict() for m in matches],
                "corrections": normalizer.corrections(matches),
                "note": ("Use these exact values in your DAX filters." if matches
                         else "No known names found in the text.")}

    def run_dax(self, user_id: UUID, dataset_id: str, dax: str, max_rows: int | None = None) -> dict:
        limit = max(1, min(int(max_rows or self.settings.default_rows), self.settings.max_rows))
        try:
            query = check_dax(dax, self.settings.max_dax_chars)
        except DaxError as exc:
            raise ToolError(str(exc)) from None
        ds = self._dataset(user_id, dataset_id)
        ok, rows, error = True, [], ""
        try:
            rows = self.powerbi.execute(ds, query)
        except DaxError as exc:
            ok, error = False, str(exc)
        if self.settings.audit:
            self.platform.audit(user_id=user_id, action="mcp.powerbi.run_dax", entity_id=ds.domain_id,
                                details={"dax": query[:2000], "ok": ok, "rows": len(rows),
                                         **({"error": error[:300]} if error else {})})
        if not ok:
            raise ToolError(error + "  Fix the query and run it again. describe_dataset with the "
                                    "tables you used returns their exact columns and the fix rules.")
        columns = list(rows[0].keys()) if rows else []
        return {"dataset": {"id": ds.domain_id, "name": ds.name}, "dax": query, "columns": columns,
                "rows": [_trim_row(r) for r in rows[:limit]], "row_count": len(rows),
                "truncated": len(rows) > limit,
                "note": ("No rows: the filters matched nothing. Check names with resolve_entities."
                         if not rows else "")}

    def ask_powerbi(self, user_id: UUID, dataset_id: str, question: str) -> dict:
        if not self.platform.has_agent:
            raise ToolError("ask_powerbi is not configured on this server")
        ds = self._dataset(user_id, dataset_id)
        out = self.platform.ask_agent(ds, question, [])
        if self.settings.audit:
            self.platform.audit(user_id=user_id, action="mcp.powerbi.ask", entity_id=ds.domain_id,
                                details={"question": question[:500], "ok": not out.get("error")})
        if out.get("error"):
            raise ToolError(f"the Power BI pipeline failed: {out['error']}")
        rows = out.get("rows")
        if isinstance(rows, list):
            rows = [_trim_row(r) for r in rows[: self.settings.default_rows] if isinstance(r, dict)]
        return {"dataset": {"id": ds.domain_id, "name": ds.name}, "answer": out.get("answer", ""),
                "dax": out.get("dax", []), "rows": rows}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_user(self, user_id: UUID) -> None:
        user = self.platform.user(user_id)
        if not user.get("can_sign_in", False):
            unit = user.get("blocking_unit")
            raise AccessDenied("your account is not active in Corp-ChatWithData"
                               + (f" (unit '{unit}' is inactive)" if unit else ""))

    def _dataset(self, user_id: UUID, dataset_id: str):
        did = _uuid(dataset_id, "dataset_id")
        self._require_user(user_id)
        return self.platform.require_dataset(user_id, did)


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in ("", None, [], {})}


def _trim_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
            v = v[:MAX_CELL_CHARS] + " …"
        out[k] = v
    return out


def _uuid(value: Any, name: str) -> UUID:
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError):
        raise ToolError(f"{name} must be an id from list_datasets, not {value!r}") from None


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
    try:
        return fn(*args, **kwargs)
    except AccessDenied as exc:
        raise ToolError(str(exc)) from None
    except PlatformError as exc:
        logger.warning("platform error: %s", exc)
        raise ToolError(f"the Power BI service is unavailable right now: {exc}") from None


# ── MCP server ───────────────────────────────────────────────────────────────

def build_server(settings: Settings, platform: Platform | None = None,
                 powerbi: PowerBI | None = None) -> tuple[MCPServer, PowerBITools]:
    platform = platform or Platform(tenancy_url=settings.tenancy_url, secret=settings.service_jwt_secret,
                                    core_secret_key=settings.core_secret_key,
                                    service_name=settings.service_name, agent_url=settings.agent_url,
                                    agent_audience=settings.agent_audience)
    powerbi = powerbi or PowerBI(timeout=settings.query_timeout_seconds)
    tools = PowerBITools(platform, powerbi, settings)
    mcp = MCPServer(name="gb-powerbi", title="GB Corp Power BI", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_datasets(ctx: Context) -> dict:
        """List the Power BI datasets this employee may query, with their ids."""
        return _run(tools.list_datasets, user_from_headers(ctx.headers))

    @mcp.tool()
    def describe_dataset(dataset_id: str, ctx: Context, tables: list[str] | None = None,
                         measures: list[str] | None = None) -> dict:
        """Describe a dataset's data dictionary.

        Without tables or measures: the overview (table routing, table and measure names,
        relationships, DAX rules, key facts). With them: their columns, measure definitions, exact
        string values, query patterns and fix rules. Use only names this tool returns.
        """
        return _run(tools.describe_dataset, user_from_headers(ctx.headers), dataset_id, tables, measures)

    @mcp.tool()
    def resolve_entities(dataset_id: str, text: str, ctx: Context) -> dict:
        """Find customer, branch, region or other names in the employee's question and return the
        exact values stored in the dataset (handles Arabic spelling variants). Use the returned
        values verbatim in DAX filters."""
        return _run(tools.resolve_entities, user_from_headers(ctx.headers), dataset_id, text)

    @mcp.tool()
    def run_dax(dataset_id: str, dax: str, ctx: Context, max_rows: int | None = None) -> dict:
        """Run one read-only DAX query (EVALUATE ... or DEFINE ... EVALUATE ...) and return its rows.

        On a DAX error the tool fails with Power BI's message: fix the query and run it again.
        max_rows caps the rows returned (default 200); row_count is the true total.
        """
        return _run(tools.run_dax, user_from_headers(ctx.headers), dataset_id, dax, max_rows)

    if platform.has_agent:
        @mcp.tool()
        def ask_powerbi(dataset_id: str, question: str, ctx: Context) -> dict:
            """Ask Corp-ChatWithData's existing Power BI pipeline a question end to end. Use only
            when the employee asks to compare with it, or after run_dax has failed five times."""
            return _run(tools.ask_powerbi, user_from_headers(ctx.headers), dataset_id, question)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "gb-powerbi"})

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
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)


def build_app(settings: Settings, platform: Platform | None = None, powerbi: PowerBI | None = None):
    mcp, _ = build_server(settings, platform, powerbi)
    app = mcp.streamable_http_app(host=settings.host, stateless_http=True)
    return BearerKeyMiddleware(app, settings.mcp_key)
