"""Settings for the GB Power BI MCP server, read once from the environment.

This server holds two Corp-ChatWithData secrets (the service-token secret and the key that
decrypts each org unit's Power BI credentials). It runs as its own process with its own
environment file, so neither secret enters the agent engine's environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(SystemExit):
    """A required setting is missing or invalid. Exits with a readable message."""


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    mcp_key: str
    tenancy_url: str
    service_jwt_secret: str
    # Fernet key Corp-ChatWithData encrypts each org unit's service-principal secret with.
    core_secret_key: str
    service_name: str
    default_rows: int
    max_rows: int
    max_dax_chars: int
    query_timeout_seconds: float
    # Optional: Corp-ChatWithData's own DAX pipeline, offered as ask_powerbi for comparison.
    agent_url: str
    agent_audience: str
    audit: bool


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise SettingsError(f"{name} must be a whole number, not {raw!r}") from None


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    required = ("POWERBI_MCP_KEY", "TENANCY_URL", "SERVICE_JWT_SECRET", "CORE_SECRET_KEY")
    missing = [n for n in required if not (env.get(n) or "").strip()]
    if missing:
        raise SettingsError("GB Power BI MCP: set " + ", ".join(missing)
                            + " (see gb-chat/mcp/powerbi/env.example)")
    key = env["POWERBI_MCP_KEY"].strip()
    if len(key) < 32:
        raise SettingsError("POWERBI_MCP_KEY must be at least 32 characters (openssl rand -hex 32)")
    secret = env["SERVICE_JWT_SECRET"].strip()
    if len(secret) < 32:
        raise SettingsError("SERVICE_JWT_SECRET must be at least 32 characters, the same value "
                            "Corp-ChatWithData uses")
    return Settings(
        host=env.get("POWERBI_MCP_HOST", "127.0.0.1"),
        port=_int(env, "POWERBI_MCP_PORT", 8712),
        mcp_key=key,
        tenancy_url=env["TENANCY_URL"].strip().rstrip("/"),
        service_jwt_secret=secret,
        core_secret_key=env["CORE_SECRET_KEY"].strip(),
        service_name=(env.get("SERVICE_NAME") or "gb-powerbi-mcp").strip(),
        default_rows=_int(env, "POWERBI_DEFAULT_ROWS", 200),
        max_rows=_int(env, "POWERBI_MAX_ROWS", 1000),
        max_dax_chars=_int(env, "POWERBI_MAX_DAX_CHARS", 20000),
        query_timeout_seconds=float(_int(env, "POWERBI_QUERY_TIMEOUT_SECONDS", 60)),
        agent_url=(env.get("AGENT_POWERBI_URL") or "").strip().rstrip("/"),
        agent_audience=(env.get("AGENT_POWERBI_AUDIENCE") or "agent-powerbi").strip(),
        audit=(env.get("POWERBI_AUDIT", "true").strip().lower() not in {"0", "false", "no"}),
    )
