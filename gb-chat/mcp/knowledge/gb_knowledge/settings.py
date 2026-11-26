"""Settings for the GB Knowledge MCP server, read once from the environment.

This server holds the Corp-ChatWithData service secret. It runs as its own process with its own
environment file, so the secret never enters the agent engine's environment.
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
    # Shared key the GB Chat engine sends as "Authorization: Bearer <key>".
    mcp_key: str
    # Corp-ChatWithData services.
    tenancy_url: str
    retrieval_url: str
    # Signs the short-lived service tokens Corp-ChatWithData services accept (X-Service-Token).
    service_jwt_secret: str
    # The caller name written into those tokens and into the audit trail.
    service_name: str
    default_top_k: int
    max_top_k: int
    max_chunk_chars: int
    timeout_seconds: float
    audit: bool


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise SettingsError(f"{name} must be a whole number, not {raw!r}") from None


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    missing = [n for n in ("KNOWLEDGE_MCP_KEY", "TENANCY_URL", "RETRIEVAL_URL", "SERVICE_JWT_SECRET")
               if not (env.get(n) or "").strip()]
    if missing:
        raise SettingsError("GB Knowledge MCP: set " + ", ".join(missing)
                            + " (see gb-chat/mcp/knowledge/env.example)")
    key = env["KNOWLEDGE_MCP_KEY"].strip()
    if len(key) < 32:
        raise SettingsError("KNOWLEDGE_MCP_KEY must be at least 32 characters (openssl rand -hex 32)")
    secret = env["SERVICE_JWT_SECRET"].strip()
    if len(secret) < 32:
        raise SettingsError("SERVICE_JWT_SECRET must be at least 32 characters, the same value "
                            "Corp-ChatWithData uses")
    return Settings(
        host=env.get("KNOWLEDGE_MCP_HOST", "127.0.0.1"),
        port=_int(env, "KNOWLEDGE_MCP_PORT", 8711),
        mcp_key=key,
        tenancy_url=env["TENANCY_URL"].strip().rstrip("/"),
        retrieval_url=env["RETRIEVAL_URL"].strip().rstrip("/"),
        service_jwt_secret=secret,
        service_name=(env.get("SERVICE_NAME") or "gb-knowledge-mcp").strip(),
        default_top_k=_int(env, "KNOWLEDGE_DEFAULT_TOP_K", 8),
        max_top_k=_int(env, "KNOWLEDGE_MAX_TOP_K", 20),
        max_chunk_chars=_int(env, "KNOWLEDGE_MAX_CHUNK_CHARS", 2000),
        timeout_seconds=float(_int(env, "KNOWLEDGE_TIMEOUT_SECONDS", 60)),
        audit=(env.get("KNOWLEDGE_AUDIT", "true").strip().lower() not in {"0", "false", "no"}),
    )
