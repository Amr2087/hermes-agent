#!/usr/bin/env bash
# Start the GB Chat agent engine (headless, API only).
#
# The engine is the unmodified agent code in the parent folder. This script gives it its own
# home (gb-chat/.engine-home), the GB Chat persona and config, and turns on its local API.
# Employees never see the engine directly; they use the GB Chat server (start-server.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_SRC="$(cd "$HERE/.." && pwd)"
ENGINE_HOME="${GB_ENGINE_HOME:-$HERE/.engine-home}"
WORKSPACE="${GB_CHAT_WORKSPACE:-$HERE/.workspace}"

if [[ -f "$HERE/.env" ]]; then
  set -a; source "$HERE/.env"; set +a
fi
if [[ -z "${API_SERVER_KEY:-}" ]]; then
  echo "API_SERVER_KEY is not set. Copy engine/env.example to gb-chat/.env and fill it in." >&2
  exit 1
fi

mkdir -p "$ENGINE_HOME" "$WORKSPACE/outputs" "$WORKSPACE/uploads"
WORKSPACE="$(cd "$WORKSPACE" && pwd)"

# The persona is owned by GB Chat: always refresh it so a stale copy can't linger.
cp "$HERE/engine/SOUL.md" "$ENGINE_HOME/SOUL.md"
# Config is seeded once, then owned by the engine home (the engine writes to it too).
if [[ ! -f "$ENGINE_HOME/config.yaml" ]]; then
  sed "s#__WORKSPACE__#$WORKSPACE#g" "$HERE/engine/config.yaml" > "$ENGINE_HOME/config.yaml"
fi

export HERMES_HOME="$ENGINE_HOME"
export API_SERVER_ENABLED=true
cd "$WORKSPACE"
exec python -m hermes_cli.main gateway run "$@"
