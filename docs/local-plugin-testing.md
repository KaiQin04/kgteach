# Local Plugin Testing

This document shows how to test the `kgteach` plugin bundle from a local source
checkout. The product surface is the plugin. The bundled MCP server is the
internal tool transport used by plugin hosts or a low-level debug aid.

## One-Command Local Setup

From the repository root:

```bash
scripts/setup-local.sh
```

The setup script:

- Runs `uv sync`.
- Downloads the official KataGo Human SL model if it is missing.
- Writes `.kgteach/katago.json` for local machine paths.
- Runs `uv run kgteach engine health`.

Generated local files are ignored by git.

## Plugin Bundle

The local plugin bundle is:

```text
plugins/kgteach/
```

Host-specific manifests live inside that bundle. Use your target host's local
plugin loading command to point at `plugins/kgteach/`.

## Smoke-Test The Bundled Transport

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"engine_health","arguments":{}}}' \
  | uv run kgteach-mcp
```

Each response should be a single JSON-RPC line. This verifies the plugin
transport, not a separate user-facing installation surface.

For direct project-level transport debugging outside plugin mode, generate root
`.mcp.json` explicitly:

```bash
scripts/setup-local.sh --write-project-mcp
```

Root `.mcp.json` is ignored by git and should not be part of normal package
installation.
