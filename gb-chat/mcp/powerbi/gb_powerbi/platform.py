"""Client for Corp-ChatWithData's tenancy (and, optionally, its Power BI agent).

Every call carries a fresh service token in `X-Service-Token`, signed with the shared
SERVICE_JWT_SECRET and addressed to the one service it is for. Nothing is cached: a revoked
grant or a rotated secret takes effect on the next tool call.

Credentials: tenancy returns each org unit's Power BI client secret as Fernet ciphertext. It is
decrypted here, used to get a Power BI token, and never returned to the agent.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import UUID

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("gb_powerbi.platform")

SERVICE_TOKEN_TTL = 120
DASHBOARD = "DASHBOARD"


class PlatformError(RuntimeError):
    """A Corp-ChatWithData service refused the call or could not be reached."""


class AccessDenied(PlatformError):
    """The user may not query this dataset."""


@dataclass(frozen=True)
class Dataset:
    """One Power BI domain, resolved for one call. `client_secret` never leaves this server."""

    domain_id: str
    name: str
    workspace_id: str
    dataset_id: str
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    schemas: dict = field(default_factory=dict, repr=False)
    entities: dict = field(default_factory=dict, repr=False)
    rules: dict = field(default_factory=dict, repr=False)
    prompts: dict = field(default_factory=dict, repr=False)

    def agent_payload(self) -> dict:
        """The `domain` block Corp-ChatWithData's own Power BI agent expects."""
        return {"domain_id": self.domain_id, "domain_name": self.name,
                "workspace_id": self.workspace_id, "dataset_id": self.dataset_id,
                "credentials": {"tenant_id": self.tenant_id, "client_id": self.client_id,
                                "client_secret": self.client_secret},
                "prompts": self.prompts, "data_dictionary_schemas": self.schemas,
                "entity_normalizer_dictionaries": self.entities, "rules": self.rules}


class Platform:
    def __init__(self, *, tenancy_url: str, secret: str, core_secret_key: str, service_name: str,
                 agent_url: str = "", agent_audience: str = "agent-powerbi", timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._tenancy = tenancy_url.rstrip("/")
        self._agent = agent_url.rstrip("/")
        self._agent_audience = agent_audience
        self._secret = secret
        self._service = service_name
        try:
            self._box = Fernet(core_secret_key.encode())
        except (ValueError, TypeError) as exc:
            raise PlatformError("CORE_SECRET_KEY is not a valid Fernet key") from exc
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @property
    def has_agent(self) -> bool:
        return bool(self._agent)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _token(self, audience: str) -> str:
        now = int(time.time())
        return jwt.encode({"sub": self._service, "aud": audience, "iat": now,
                           "exp": now + SERVICE_TOKEN_TTL, "typ": "service"},
                          self._secret, algorithm="HS256")

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"X-Service-Token": self._token("tenancy"), "Accept": "application/json"}
        try:
            return self._http.request(method, self._tenancy + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(f"tenancy is unreachable ({type(exc).__name__})") from exc

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if response.status_code != 200:
            raise PlatformError(f"tenancy answered {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise PlatformError("tenancy returned something that is not JSON") from exc

    # ── tenancy ──────────────────────────────────────────────────────────────

    def user(self, user_id: UUID) -> dict:
        r = self._call("GET", f"/internal/users/{user_id}")
        if r.status_code == 404:
            raise AccessDenied("this GB Chat user is not registered in Corp-ChatWithData")
        return self._json(r)

    def datasets(self, user_id: UUID) -> list[dict]:
        """Ready Power BI domains this user holds a grant for, as {id, name, type}."""
        rows = self._json(self._call("GET", f"/internal/users/{user_id}/domains"))
        return [d for d in rows if isinstance(d, dict) and d.get("type") == DASHBOARD]

    def require_dataset(self, user_id: UUID, domain_id: UUID) -> Dataset:
        """The dataset with its decrypted credentials, if this user may query it right now."""
        verdict = self._json(self._call("GET", f"/internal/access/{user_id}/domains/{domain_id}"))
        if not verdict.get("allowed"):
            raise AccessDenied(f"access denied: {verdict.get('reason') or 'no grant'}")
        r = self._call("GET", f"/internal/domains/{domain_id}/runtime")
        if r.status_code == 404:
            raise AccessDenied("no such dataset")
        rt = self._json(r)
        if rt.get("type") != DASHBOARD:
            raise AccessDenied("that id is not a Power BI dataset (it may be a knowledge base)")
        if not rt.get("ready"):
            raise PlatformError("this dataset is not ready: " + "; ".join(rt.get("blockers") or ["unknown"]))
        creds = rt.get("credentials") or {}
        cipher = creds.get("client_secret_encrypted") or ""
        if not (creds.get("tenant_id") and creds.get("client_id") and cipher):
            raise PlatformError("this dataset has no Power BI credentials in Corp-ChatWithData")
        try:
            secret = self._box.decrypt(cipher.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise PlatformError("cannot decrypt the dataset's credentials: CORE_SECRET_KEY does not "
                                "match Corp-ChatWithData's") from exc
        return Dataset(domain_id=str(rt.get("domain_id") or domain_id), name=rt.get("domain_name") or "",
                       workspace_id=str(rt.get("source_id") or ""), dataset_id=str(rt.get("source_key") or ""),
                       tenant_id=str(creds["tenant_id"]), client_id=str(creds["client_id"]),
                       client_secret=secret, schemas=rt.get("schemas") or {}, entities=rt.get("config") or {},
                       rules=rt.get("rules") or {}, prompts=rt.get("prompts") or {})

    def audit(self, *, user_id: UUID, action: str, entity_id: str | None, details: dict) -> None:
        """Best effort: an audit failure is logged, never shown as a failed query."""
        try:
            r = self._call("POST", "/internal/audit", json={
                "action": action, "entity_type": "domain", "entity_id": entity_id,
                "actor_id": str(user_id), "actor_type": "user",
                "details": {"via": self._service, **details}})
            if r.status_code >= 300:
                logger.warning("audit write refused: %s %s", r.status_code, r.text[:200])
        except PlatformError as exc:
            logger.warning("audit write failed: %s", exc)

    # ── Corp-ChatWithData's own Power BI agent (optional) ────────────────────

    def ask_agent(self, dataset: Dataset, question: str, history: list[dict], timeout: float = 300.0) -> dict:
        """Run the existing DAX pipeline end to end and collect its answer, DAX and rows."""
        if not self._agent:
            raise PlatformError("ask_powerbi is not configured (AGENT_POWERBI_URL is empty)")
        headers = {"X-Service-Token": self._token(self._agent_audience), "Accept": "text/event-stream"}
        body = {"question": question, "history": history, "domain": dataset.agent_payload()}
        out: dict[str, Any] = {"answer": "", "dax": [], "rows": None, "error": None}
        try:
            with self._http.stream("POST", self._agent + "/api/query", json=body, headers=headers,
                                   timeout=timeout) as r:
                if r.status_code != 200:
                    r.read()
                    raise PlatformError(f"the Power BI agent answered {r.status_code}: {r.text[:300]}")
                for event in _sse_events(r.iter_lines()):
                    step, data = event.get("step"), event.get("data")
                    if step == "answer" and isinstance(data, str):
                        out["answer"] = data
                    elif step == "dax" and data:
                        out["dax"].append(data)
                    elif step == "rows":
                        out["rows"] = data
                    elif step == "error":
                        out["error"] = data
        except httpx.HTTPError as exc:
            raise PlatformError(f"the Power BI agent is unreachable ({type(exc).__name__})") from exc
        return out


def _sse_events(lines: Iterator[str]) -> Iterator[dict]:
    for line in lines:
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event
