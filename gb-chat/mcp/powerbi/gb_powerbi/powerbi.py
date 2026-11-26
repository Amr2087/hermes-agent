"""Run DAX against a Power BI dataset with the domain's service principal.

The same calls Corp-ChatWithData makes (`executeQueries` over REST, an MSAL client-credentials
token cached per service principal), with the guard rails a general agent needs: one read-only
query per call, a length limit, a timeout and a row cap.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

import httpx

from .platform import Dataset

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
API = "https://api.powerbi.com/v1.0/myorg"


class DaxError(RuntimeError):
    """The query was refused, by the guard here or by the dataset. The message is for the model."""


# ── Guard ────────────────────────────────────────────────────────────────────

_LINE_COMMENT = re.compile(r"(--|//)[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STRING = re.compile(r'"(?:[^"]|"")*"')


def check_dax(dax: str, max_chars: int) -> str:
    """Return the query to run, or raise DaxError saying what to change.

    executeQueries is read-only by design, so this is about shape, not safety from writes: one
    query statement (DEFINE ... EVALUATE ... or EVALUATE ...), not empty, not huge.
    """
    text = (dax or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*|\s*```$", "", text).strip()
    if not text:
        raise DaxError("the DAX query is empty")
    if len(text) > max_chars:
        raise DaxError(f"the DAX query is {len(text):,} characters; keep it under {max_chars:,}")
    bare = _STRING.sub('""', _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", text))).strip()
    first = bare.split(None, 1)[0].upper() if bare else ""
    if first not in {"EVALUATE", "DEFINE"}:
        raise DaxError("a query must start with EVALUATE (or DEFINE ... EVALUATE). Wrap a measure like "
                       'this: EVALUATE ROW("Value", [Measure])')
    if not re.search(r"\bEVALUATE\b", bare, re.I):
        raise DaxError("a DEFINE block must be followed by EVALUATE")
    return text


# ── Execution ────────────────────────────────────────────────────────────────

TokenProvider = Callable[[Dataset], str]

_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_lock = threading.Lock()


def msal_token(ds: Dataset) -> str:
    key = (ds.tenant_id, ds.client_id)
    with _lock:
        cached = _tokens.get(key)
        if cached and time.time() < cached[1] - 60:
            return cached[0]
    from msal import ConfidentialClientApplication

    app = ConfidentialClientApplication(ds.client_id, client_credential=ds.client_secret,
                                        authority=f"https://login.microsoftonline.com/{ds.tenant_id}")
    result = app.acquire_token_for_client(scopes=[POWERBI_SCOPE])
    if "access_token" not in result:
        # Azure's own text names the real problem (expired secret, wrong tenant, missing consent).
        raise DaxError(f"could not sign in to Power BI: {result.get('error_description') or result.get('error')}")
    with _lock:
        _tokens[key] = (result["access_token"], time.time() + int(result.get("expires_in") or 3600))
    return result["access_token"]


class PowerBI:
    def __init__(self, *, timeout: float = 60.0, token_provider: TokenProvider = msal_token,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._token = token_provider
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def execute(self, ds: Dataset, dax: str) -> list[dict]:
        if not (ds.workspace_id and ds.dataset_id):
            raise DaxError("this dataset has no workspace or dataset id configured in Corp-ChatWithData")
        url = f"{API}/groups/{ds.workspace_id}/datasets/{ds.dataset_id}/executeQueries"
        try:
            r = self._http.post(url, json={"queries": [{"query": dax}],
                                           "serializerSettings": {"includeNulls": True}},
                                headers={"Authorization": f"Bearer {self._token(ds)}"})
        except httpx.TimeoutException as exc:
            raise DaxError("the query timed out: narrow it (fewer columns, a filter, TOPN)") from exc
        except httpx.HTTPError as exc:
            raise DaxError(f"Power BI is unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise DaxError(f"Power BI refused the query ({r.status_code}): {reason(r.text)}")
        try:
            return list(r.json()["results"][0]["tables"][0]["rows"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DaxError("Power BI returned an unexpected result shape") from exc


def reason(text: str) -> str:
    """The message inside Power BI's error envelope, or the body itself."""
    try:
        error = json.loads(text).get("error") or {}
        for d in error.get("pbi.error", {}).get("details") or []:
            detail = (d.get("detail") or {}).get("value")
            if detail:
                return str(detail)[:600]
        return str(error.get("message") or text)[:600]
    except (ValueError, AttributeError):
        return text[:600]
