#!/usr/bin/env bash
# Start the GB Chat server (web UI + API). Needs only Python 3.10+, no packages.
#   ./start-server.sh            uses gb-chat/.env
#   GB_CHAT_ENGINE=mock ./start-server.sh   demo mode, no engine or model needed
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HERE/.env" ]]; then
  set -a; source "$HERE/.env"; set +a
fi
export GB_CHAT_WORKSPACE="${GB_CHAT_WORKSPACE:-$HERE/.workspace}"
cd "$HERE/server"
exec python3 -m gbchat "$@"
