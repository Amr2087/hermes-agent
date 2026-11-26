# GB Chat company tools (MCP servers)

Two MCP servers give the GB Chat agent access to company data in **Corp-ChatWithData**, as tools.
They are fully separate: each has its own code, virtualenv, port, key, `.env` and tests, and
either one can run, stop or be deployed without the other.

| Server | Folder | Port | Tools | Reads from Corp-ChatWithData |
| --- | --- | --- | --- | --- |
| GB Knowledge | `knowledge/` | 8711 | `list_knowledge_bases`, `search_knowledge`, `list_knowledge_files` | tenancy, retrieval |
| GB Power BI | `powerbi/` | 8712 | `list_datasets`, `describe_dataset`, `resolve_entities`, `run_dax`, and `ask_powerbi` (optional) | tenancy, Power BI, and agent-powerbi (optional) |

Neither server calls a model. The GB Chat agent does the reasoning, guided by two skills in
`gb-chat/engine/skills/`: `company-knowledge` and `powerbi-analyst`. The engine sees the tools as
`mcp__gb_knowledge__*` and `mcp__gb_powerbi__*`.

```
GB Chat engine ──HTTP (Bearer key + X-GB-User)──► GB Knowledge MCP ──X-Service-Token──► tenancy, retrieval
               └─HTTP (Bearer key + X-GB-User)──► GB Power BI MCP  ──X-Service-Token──► tenancy
                                                                   └─service principal──► Power BI REST
```

## How access works

- **The engine proves it's GB Chat** with a shared key (`Authorization: Bearer …`). Without it, a
  server answers 401 before any tool runs.
- **The engine says who the employee is** with `X-GB-User: <tenancy user id>`. Hermes sends fixed
  headers per engine, so this is trustworthy only with **one engine per employee**. Today's single
  engine is fine for one pilot user.
- **Every tool call re-checks access in tenancy**: the user is active, their unit is active, they
  hold a grant for that domain, and the domain is the right type. There is no cache, so revoking a
  grant takes effect on the next call.
- **Secrets stay in the servers.** `SERVICE_JWT_SECRET` and `CORE_SECRET_KEY` live only in each
  server's own `.env`, never in `gb-chat/.env` (the engine's). The Power BI client secret is
  decrypted inside the Power BI server, used for a token, and never returned.
- **Every search and query is audited** in tenancy's audit trail (`mcp.knowledge.search`,
  `mcp.powerbi.run_dax`, `mcp.powerbi.ask`), with the employee as the actor.
- **`run_dax` guards:** one `EVALUATE` or `DEFINE … EVALUATE` query, at most 20,000 characters, a
  60-second timeout, and at most 200 rows returned by default (1,000 max) with the true row count.

## Setup

1. **Corp-ChatWithData running** (its `deploy/compose`), with tenancy on 8101 and retrieval on 8103
   reachable from where these servers run.
2. **Configure each server:**
   ```bash
   cd gb-chat/mcp/knowledge && cp env.example .env   # fill in KNOWLEDGE_MCP_KEY, SERVICE_JWT_SECRET
   cd ../powerbi && cp env.example .env               # fill in POWERBI_MCP_KEY, SERVICE_JWT_SECRET, CORE_SECRET_KEY
   ```
   `SERVICE_JWT_SECRET` and `CORE_SECRET_KEY` are the values in Corp-ChatWithData's
   `deploy/compose/.env`. Make each `*_MCP_KEY` with `openssl rand -hex 32`.
3. **Point the engine at them** in `gb-chat/.env`:
   ```bash
   GB_KNOWLEDGE_MCP_URL=http://127.0.0.1:8711/mcp
   GB_KNOWLEDGE_MCP_KEY=<same as KNOWLEDGE_MCP_KEY>
   GB_POWERBI_MCP_URL=http://127.0.0.1:8712/mcp
   GB_POWERBI_MCP_KEY=<same as POWERBI_MCP_KEY>
   GB_CHAT_TENANCY_USER_ID=<the employee's users.id in tenancy>
   ```
4. **Start them**, each in its own terminal or service, then restart the engine:
   ```bash
   ./start-mcp.sh knowledge
   ./start-mcp.sh powerbi
   ./start-engine.sh
   ```
   `start-mcp.sh` creates each server's virtualenv on first run. `start-engine.sh` copies the two
   skills into the engine and writes the `mcp_servers` entries into the engine's `config.yaml`
   (only for servers whose URL is set; comments and other entries are kept).

Health checks: `curl http://127.0.0.1:8711/health` and `curl http://127.0.0.1:8712/health`.

## Tests

No Corp-ChatWithData needed: tenancy, retrieval, Power BI and the agent are faked, and service
tokens are checked the way Corp-ChatWithData checks them.

```bash
cd gb-chat/mcp/knowledge && python -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest
cd gb-chat/mcp/powerbi   && python -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest
```

## Notes

- The data dictionary and entity matching are ported from Corp-ChatWithData's `agent_powerbi`
  (`schema_retriever.py`, `entity_normalizer.py`), so a domain set up for that agent works here
  unchanged. Two small changes in entity matching: Latin names match regardless of case, and a
  shorter name inside a longer matched name (for example "Cairo" inside "Cairo Main") is not
  reported again.
- `ask_powerbi` appears only when `AGENT_POWERBI_URL` is set. It runs the existing DAX pipeline
  end to end, for comparing answers while the tool route is being proven.
- Budgets are not charged here: these tools spend no model tokens. Model usage is the GB Chat
  engine's, and charging it to tenancy budgets is a separate piece of work.
