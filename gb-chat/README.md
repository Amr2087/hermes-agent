# GB Chat

GB Chat is GB Corp's internally built AI assistant, a replacement for Microsoft 365 Copilot. Employees chat
with it in the browser, and it writes, analyzes and **produces finished files**: Word reports, PowerPoint
decks, Excel workbooks, PDFs and web pages.

It has three parts. Employees only ever see the first two.

```
 Browser ──► GB Chat server (gb-chat/server) ──► Agent engine (repository root, unmodified)
  web UI        sign-in header, stable API,         tools, skills, memory, models,
 (gb-chat/web)  file downloads, branding filter     document creation, approvals
```

| Part | Folder | What it is |
| --- | --- | --- |
| Web UI | `web/` | Plain HTML, CSS and JavaScript. No build step, no frameworks, no CDN. |
| GB Chat server | `server/` | Python standard library only. Serves the UI, exposes the GB Chat API, streams turns to the browser, serves files and uploads, and hides the engine's names from employees. |
| Engine setup | `engine/` | The GB Chat persona (`SOUL.md`), engine config, and an env template. |

The engine is the open-source agent in the repository root. It stays **unmodified**, so upstream
fixes can be merged without conflicts. Everything GB Chat-specific lives in this folder.

## Quick start: demo mode (no model needed)

```bash
cd gb-chat
GB_CHAT_ENGINE=mock ./start-server.sh
# open http://127.0.0.1:8080
```

Demo mode uses a built-in stand-in engine with canned answers. It's for working on the UI and showing
the experience. Files it makes are placeholders.

## Running with the real engine

1. Install the engine from the repository root (see the root `README.md`; `./setup-hermes.sh` on
   Linux and macOS). The engine needs Python 3.14 and its `messaging` extra, which carries the web
   server its API runs on. For a plain source checkout:
   ```bash
   uv sync --frozen --python 3.14 --extra messaging
   ```
   The document skills need `python-docx`, `python-pptx` and `openpyxl`. Install them in the engine's
   environment, or turn on the engine's lazy installs (`security.allow_lazy_installs`).
2. Configure:
   ```bash
   cd gb-chat
   cp engine/env.example .env
   # set API_SERVER_KEY and GB_CHAT_ENGINE_KEY to the same long random value,
   # and add the API key for the model provider IT approved
   ```
3. Pick the model once:
   ```bash
   HERMES_HOME=$PWD/.engine-home python -m hermes_cli.main model
   ```
4. Start both processes (two terminals, or two services):
   ```bash
   ./start-engine.sh     # agent engine, local API on 127.0.0.1:8642
   ./start-server.sh     # GB Chat on http://127.0.0.1:8080
   ```

`start-engine.sh` gives the engine its own home in `gb-chat/.engine-home`, installs the GB Chat
persona, and makes `gb-chat/.workspace` the agent's working folder. Files the agent creates go to
`.workspace/outputs` and show up in the chat as download cards and under **My files**. Uploads land
in `.workspace/uploads`.

## What employees can do

- Chat, with streamed answers, tables, lists and code formatted.
- Watch progress: each tool step shows as a plain-English line ("Writing Q3-review.pptx").
- Get files: Word, PowerPoint, Excel, CSV, PDF and HTML, with open and download buttons.
- Attach files (button or drag and drop, up to 25 MB each) for the assistant to read.
- Approve or deny risky actions when the engine asks.
- Stop a running answer, rename or delete chats, search chat history.
- Browse **My files** and **What I can do** (the skills list, with engine-only skills hidden).
- Light and dark mode, and a phone layout.

## Branding

- **Colors:** `web/styles.css`, the `:root` tokens. `--brand` is GB Corp blue `#2a51a3`, from the
  theme color on [gb-corporation.com](https://gb-corporation.com/).
- **Logo:** `web/assets/gb-mark.svg` is a placeholder mark. To use the official logo, replace
  that file with an approved one from GB Corp's brand team.
- **Name and voice:** `engine/SOUL.md` makes the assistant call itself GB Chat. It also sets the house
  style for documents (GB Corp blue, Calibri, title-case headings).
- **Engine names:** the server never shows the engine's product names. It translates tool calls into
  plain labels, removes engine names from errors and skill descriptions (`server/gbchat/labels.py`),
  and hides engine-only skills.

## API (for the UI or future clients)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/me`, `/api/status` | Signed-in user name; engine mode and health |
| GET / POST | `/api/chats` | List chats, create one (`{title}`) |
| PATCH / DELETE | `/api/chats/{id}` | Rename (`{title}`), delete |
| GET | `/api/chats/{id}/messages` | Chat history |
| POST | `/api/chats/{id}/messages` | Send `{text, attachments}`; streams GB Chat events (SSE) |
| POST | `/api/runs/{run}/stop`, `/api/runs/{run}/approval` | Stop a turn; answer an approval (`{choice}`) |
| GET | `/api/skills`, `/api/files` | Skills list; files the assistant made |
| POST | `/api/uploads?name=…` | Upload a file (raw body) |
| GET | `/files/outputs/{name}`, `/files/uploads/{name}` | Open (or `?download=1`) a file |

The streamed event types are documented at the top of `server/gbchat/events.py`. Every
non-GET request must carry the header `X-GB-Chat: 1`, which blocks cross-site request forgery.

## Tests

```bash
cd gb-chat/server
python3 -m unittest discover -s tests
```

## Security notes

- Both processes bind to `127.0.0.1` by default. Put GB Chat behind the company's sign-in proxy
  (for example Azure App Service authentication or an Entra ID reverse proxy). The server reads the
  employee's name from the header set in `GB_CHAT_USER_HEADER`
  (default `X-MS-CLIENT-PRINCIPAL-NAME`).
- Files the assistant makes are served with a sandboxing Content Security Policy, so a generated
  HTML page can't run script as GB Chat.
- The UI has a strict Content Security Policy: no inline script and no third-party origins.

## Not done yet

- **One engine per employee.** This version runs one shared engine. The engine keeps one
  memory and one chat history per instance, so a company rollout needs one isolated engine for each
  employee, with the GB Chat server routing by signed-in user. See the readiness study.
- **Connectors** (Outlook, SharePoint, OneDrive, Teams, Calendar) are added in the engine's MCP and
  plugin config, in `.engine-home/config.yaml`. GB Chat's UI already shows their tool steps as
  "Using a connected app".
- The official GB Corp logo and brand fonts still need to come from the brand team.

See `NOTICE.md` for the engine's license terms.
