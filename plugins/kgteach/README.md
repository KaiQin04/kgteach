# kgteach Plugin

This is the local agent plugin for `kgteach`. It bundles the `kgteach` MCP
server as its internal tool transport and includes skill metadata for agent use.

For local source checkout testing, run `scripts/setup-local.sh` from the
repository root. After the Python package is published, the plugin can run
through `uvx --from kgteach kgteach-mcp`.
