# kgteach Plugin

This plugin lets AI agents analyze Go, baduk, and weiqi SGF files with KataGo
through the `kgteach` MCP server.

Use it when an agent needs factual KataGo evidence before explaining a move,
comparing candidates, validating a variation, generating a quiz, or producing a
rank-aware review plan.

For local source checkout testing, run `scripts/setup-local.sh` from the
repository root. After the Python package is published, the plugin can run
through `uvx --from kgteach kgteach-mcp`.
