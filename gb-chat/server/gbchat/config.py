"""Server settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent
_GB_CHAT_DIR = _SERVER_DIR.parent


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    engine_mode: str  # "engine" or "mock"
    engine_url: str
    engine_key: str
    workspace: Path
    web_dir: Path
    # Header a sign-in proxy in front of GB Chat sets to the employee's name or email
    # (for example Azure App Service authentication sets X-MS-CLIENT-PRINCIPAL-NAME).
    user_header: str
    max_upload_bytes: int

    @property
    def outputs_dir(self) -> Path:
        return self.workspace / "outputs"

    @property
    def uploads_dir(self) -> Path:
        return self.workspace / "uploads"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    key = env.get("GB_CHAT_ENGINE_KEY", "")
    mode = (env.get("GB_CHAT_ENGINE") or ("engine" if key else "mock")).strip().lower()
    if mode not in {"engine", "mock"}:
        raise SystemExit(f"GB_CHAT_ENGINE must be 'engine' or 'mock', not {mode!r}")
    workspace = Path(env.get("GB_CHAT_WORKSPACE") or _GB_CHAT_DIR / ".workspace").resolve()
    return Settings(
        host=env.get("GB_CHAT_HOST", "127.0.0.1"),
        port=int(env.get("GB_CHAT_PORT", "8080")),
        engine_mode=mode,
        engine_url=env.get("GB_CHAT_ENGINE_URL", "http://127.0.0.1:8642").rstrip("/"),
        engine_key=key,
        workspace=workspace,
        web_dir=Path(env.get("GB_CHAT_WEB_DIR") or _GB_CHAT_DIR / "web").resolve(),
        user_header=env.get("GB_CHAT_USER_HEADER", "X-MS-CLIENT-PRINCIPAL-NAME"),
        max_upload_bytes=int(env.get("GB_CHAT_MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    )
