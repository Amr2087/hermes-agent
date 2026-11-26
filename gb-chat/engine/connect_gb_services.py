"""Connect the engine to GB Chat's own MCP servers and skills. Run by start-engine.sh on every start.

- Skills: copies gb-chat/engine/skills/* into <engine home>/skills/gb-corp/ (always refreshed, like
  SOUL.md, so a stale copy can't linger).
- MCP servers: for each GB server whose URL is set in gb-chat/.env, writes its entry under
  `mcp_servers` in <engine home>/config.yaml; for one whose URL is not set, removes GB Chat's
  entry. Other entries and comments in config.yaml are left alone (round-trip YAML).

The entries hold no secrets: the key and the employee id stay as ${VAR} references, which the
engine fills in from its environment at connect time. A missing value fails closed (the engine
refuses to connect and names the variable).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# server name in config.yaml  ->  env var holding its URL, env var holding its key, tools to expose
SERVERS = {
    "gb_knowledge": ("GB_KNOWLEDGE_MCP_URL", "GB_KNOWLEDGE_MCP_KEY",
                     ["list_knowledge_bases", "search_knowledge", "list_knowledge_files"]),
    "gb_powerbi": ("GB_POWERBI_MCP_URL", "GB_POWERBI_MCP_KEY",
                   ["list_datasets", "describe_dataset", "resolve_entities", "run_dax", "ask_powerbi"]),
}
USER_VAR = "GB_CHAT_TENANCY_USER_ID"
MANAGED_MARK = "managed-by: gb-chat"


def entry(url_var: str, key_var: str, tools: list[str]) -> dict:
    return {
        "url": f"${{{url_var}}}",
        "headers": {
            "Authorization": f"Bearer ${{{key_var}}}",
            # The employee this engine works for (their Corp-ChatWithData tenancy user id).
            "X-GB-User": f"${{{USER_VAR}}}",
        },
        # Only the server's own tools (not the generic prompt/resource helpers).
        "tools": {"include": list(tools), "resources": False, "prompts": False},
        "description": MANAGED_MARK,
    }


def _plain(value):
    """ruamel's commented maps and sequences as plain dicts and lists, for comparison."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def sync_skills(engine_home: Path) -> list[str]:
    src = HERE / "skills"
    dst = engine_home / "skills" / "gb-corp"
    names = []
    if not src.is_dir():
        return names
    dst.mkdir(parents=True, exist_ok=True)
    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").is_file()):
        target = dst / skill.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill, target)
        names.append(skill.name)
    return names


def sync_mcp(config_path: Path, env: dict[str, str]) -> list[str]:
    from ruamel.yaml import YAML  # the engine's own YAML library

    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
    if data is None:
        data = {}
    servers = data.get("mcp_servers") or {}
    changed, active = False, []
    for name, (url_var, key_var, tools) in SERVERS.items():
        wanted = (env.get(url_var) or "").strip()
        current = servers.get(name)
        ours = current is None or (isinstance(current, dict) and current.get("description") == MANAGED_MARK)
        if not ours:
            # Someone configured this name by hand: leave it.
            active.append(name)
            continue
        if wanted:
            new = entry(url_var, key_var, tools)
            if current is None or _plain(current) != new:
                servers[name] = new
                changed = True
            active.append(name)
        elif current is not None:
            del servers[name]
            changed = True
    if changed:
        if servers:
            data["mcp_servers"] = servers
        elif "mcp_servers" in data:
            del data["mcp_servers"]
        with config_path.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh)
    return active


def main() -> int:
    engine_home = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["HERMES_HOME"]).resolve()
    env = dict(os.environ)
    skills = sync_skills(engine_home)
    active = sync_mcp(engine_home / "config.yaml", env)
    if active and not (env.get(USER_VAR) or "").strip():
        print(f"warning: {USER_VAR} is not set in gb-chat/.env, so the GB company tools will refuse "
              "to connect. Set it to this employee's Corp-ChatWithData user id.", file=sys.stderr)
    print(f"GB Chat: skills {', '.join(skills) or 'none'}; company tools {', '.join(active) or 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
