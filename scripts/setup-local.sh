#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup-local.sh [options]

Set up kgteach for local CLI and plugin testing.

Options:
  --skip-human-sl        Do not download the official KataGo Human SL model.
  --write-project-mcp   Also write root .mcp.json for direct transport debugging.
  --help                Show this help.

Outputs:
  .kgteach/katago.json        Local machine KataGo config, ignored by git.
  .mcp.json                   Optional direct transport debug config.
EOF
}

SKIP_HUMAN_SL=0
WRITE_PROJECT_MCP=0
for arg in "$@"; do
  case "$arg" in
    --skip-human-sl)
      SKIP_HUMAN_SL=1
      ;;
    --write-project-mcp)
      WRITE_PROJECT_MCP=1
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="${UV:-}"
if [ -z "$UV_BIN" ]; then
  if [ -x "/opt/homebrew/bin/uv" ]; then
    UV_BIN="/opt/homebrew/bin/uv"
  else
    UV_BIN="$(command -v uv)"
  fi
fi

if [ -z "$UV_BIN" ]; then
  echo "uv is required but was not found on PATH or at /opt/homebrew/bin/uv." >&2
  exit 1
fi

HUMAN_MODEL_URL="https://media.katagotraining.org/uploaded/networks/models_extra/b18c384nbt-humanv0.bin.gz"
KGTEACH_DATA_DIR="${KGTEACH_DATA_DIR:-$HOME/.local/share/kgteach}"
KGTEACH_MODELS="$KGTEACH_DATA_DIR/models"
HUMAN_MODEL="$KGTEACH_MODELS/b18c384nbt-humanv0.bin.gz"

cd "$REPO_ROOT"
"$UV_BIN" sync
KGTEACH_MCP_BIN="$REPO_ROOT/.venv/bin/kgteach-mcp"
if [ ! -x "$KGTEACH_MCP_BIN" ]; then
  echo "kgteach-mcp entrypoint was not created at $KGTEACH_MCP_BIN." >&2
  exit 1
fi

if [ "$SKIP_HUMAN_SL" -eq 0 ] && [ ! -f "$HUMAN_MODEL" ]; then
  mkdir -p "$KGTEACH_MODELS"
  tmp_file="$HUMAN_MODEL.tmp"
  echo "Downloading KataGo Human SL model from official KataGo training media..."
  curl -fL --retry 3 --output "$tmp_file" "$HUMAN_MODEL_URL"
  mv "$tmp_file" "$HUMAN_MODEL"
fi

mkdir -p "$REPO_ROOT/.kgteach"
if [ -f "$HUMAN_MODEL" ]; then
  cat > "$REPO_ROOT/.kgteach/katago.json" <<EOF
{
  "katago": {
    "human_model": "$HUMAN_MODEL"
  }
}
EOF
fi

if [ "$WRITE_PROJECT_MCP" -eq 1 ]; then
  cat > "$REPO_ROOT/.mcp.json" <<EOF
{
  "mcpServers": {
    "kgteach": {
      "type": "stdio",
      "command": "$KGTEACH_MCP_BIN",
      "args": [],
      "env": {}
    }
  }
}
EOF
fi

"$UV_BIN" run kgteach engine health

cat <<EOF

Local setup complete.

Plugin bundle:
  $REPO_ROOT/plugins/kgteach

Bundled plugin transport config:
  $REPO_ROOT/plugins/kgteach/.mcp.json

Direct project transport debug:
  scripts/setup-local.sh --write-project-mcp
EOF
