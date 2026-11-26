#!/usr/bin/env bash
# Start one of GB Chat's company-tool MCP servers:
#   ./start-mcp.sh knowledge   # GB Knowledge: Corp-ChatWithData knowledge bases (port 8711)
#   ./start-mcp.sh powerbi     # GB Power BI: Corp-ChatWithData Power BI datasets (port 8712)
#
# Each server is its own process with its own virtualenv and its own .env (gb-chat/mcp/<name>/.env),
# so the Corp-ChatWithData secrets it holds never enter the agent engine's environment.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${1:-}"
case "$NAME" in
  knowledge) PKG=gb_knowledge ;;
  powerbi)   PKG=gb_powerbi ;;
  *) echo "usage: $0 knowledge|powerbi" >&2; exit 2 ;;
esac
DIR="$HERE/mcp/$NAME"

if [[ ! -f "$DIR/.env" ]]; then
  echo "Missing $DIR/.env. Copy $DIR/env.example to $DIR/.env and fill it in." >&2
  exit 1
fi
if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  echo "Creating $DIR/.venv ..." >&2
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install --quiet "$DIR"
fi

set -a; source "$DIR/.env"; set +a
cd "$DIR"
exec "$DIR/.venv/bin/python" -m "$PKG"
