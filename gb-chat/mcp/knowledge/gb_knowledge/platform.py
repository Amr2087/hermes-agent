"""Client for the Corp-ChatWithData services this server reads: tenancy and retrieval.

Every call carries a fresh service token in `X-Service-Token`, signed with the shared
SERVICE_JWT_SECRET and addressed to the one service it is for (the token's audience), exactly as
Corp-ChatWithData's own services call each other. Nothing here is cached: a revoked grant takes
effect on the next tool call.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

import httpx
import jwt

logger = logging.getLogger("gb_knowledge.platform")

SERVICE_TOKEN_TTL = 120
RAG = "RAG"


class PlatformError(RuntimeError):
    """A Corp-ChatWithData service refused the call or could not be reached."""


class AccessDenied(PlatformError):
    """The user may not use this knowledge base."""


class Platform:
    def __init__(self, *, tenancy_url: str, retrieval_url: str, secret: str, service_name: str,
                 timeout: float = 60.0, transport: httpx.BaseTransport | None = None) -> None:
        self._tenancy = tenancy_url.rstrip("/")
        self._retrieval = retrieval_url.rstrip("/")
        self._secret = secret
        self._service = service_name
        self._http = httpx.Client(timeout=timeout, transport=transport)

    # ── Service tokens ───────────────────────────────────────────────────────

    def _token(self, audience: str) -> str:
        now = int(time.time())
        return jwt.encode({"sub": self._service, "aud": audience, "iat": now,
                           "exp": now + SERVICE_TOKEN_TTL, "typ": "service"},
                          self._secret, algorithm="HS256")

    def _call(self, method: str, base: str, audience: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"X-Service-Token": self._token(audience), "Accept": "application/json"}
        try:
            return self._http.request(method, base + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(f"{audience} is unreachable ({type(exc).__name__})") from exc

    @staticmethod
    def _json(response: httpx.Response, what: str) -> Any:
        if response.status_code != 200:
            raise PlatformError(f"{what} answered {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise PlatformError(f"{what} returned something that is not JSON") from exc

    # ── tenancy ──────────────────────────────────────────────────────────────

    def user(self, user_id: UUID) -> dict:
        r = self._call("GET", self._tenancy, "tenancy", f"/internal/users/{user_id}")
        if r.status_code == 404:
            raise AccessDenied("this GB Chat user is not registered in Corp-ChatWithData")
        return self._json(r, "tenancy")

    def granted_domains(self, user_id: UUID) -> list[dict]:
        """Ready domains this user holds a grant for, as {id, name, type}."""
        rows = self._json(self._call("GET", self._tenancy, "tenancy",
                                     f"/internal/users/{user_id}/domains"), "tenancy")
        return [d for d in rows if isinstance(d, dict)]

    def knowledge_bases(self, user_id: UUID) -> list[dict]:
        return [d for d in self.granted_domains(user_id) if d.get("type") == RAG]

    def require_knowledge_base(self, user_id: UUID, kb_id: UUID) -> dict:
        """The knowledge base, if this user may search it right now. Two checks, both tenancy's:
        the access decision (active user and unit, a grant) and that the domain is a knowledge base."""
        verdict = self._json(self._call("GET", self._tenancy, "tenancy",
                                        f"/internal/access/{user_id}/domains/{kb_id}"), "tenancy")
        if not verdict.get("allowed"):
            raise AccessDenied(f"access denied: {verdict.get('reason') or 'no grant'}")
        for kb in self.knowledge_bases(user_id):
            if str(kb.get("id")) == str(kb_id):
                return kb
        raise AccessDenied("that id is not a knowledge base you can search (it may be a Power BI "
                           "dataset, or not ready yet)")

    def audit(self, *, user_id: UUID, action: str, entity_id: UUID | None, details: dict) -> None:
        """Best effort: an audit failure is logged, never shown to the user as a failed search."""
        try:
            r = self._call("POST", self._tenancy, "tenancy", "/internal/audit", json={
                "action": action, "entity_type": "domain",
                "entity_id": str(entity_id) if entity_id else None,
                "actor_id": str(user_id), "actor_type": "user",
                "details": {"via": self._service, **details}})
            if r.status_code >= 300:
                logger.warning("audit write refused: %s %s", r.status_code, r.text[:200])
        except PlatformError as exc:
            logger.warning("audit write failed: %s", exc)

    # ── retrieval ────────────────────────────────────────────────────────────

    def search(self, kb_id: UUID, query: str, top_k: int) -> list[dict]:
        r = self._call("POST", self._retrieval, "retrieval", "/internal/search",
                       json={"domain_id": str(kb_id), "query": query, "top_k": top_k})
        body = self._json(r, "retrieval")
        return [h for h in (body.get("hits") or []) if isinstance(h, dict)]

    def files(self, kb_id: UUID) -> list[dict]:
        r = self._call("GET", self._retrieval, "retrieval", f"/internal/knowledge/{kb_id}/files")
        return [f for f in self._json(r, "retrieval") if isinstance(f, dict)]
