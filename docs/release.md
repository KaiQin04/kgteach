# Release Checklist

Use this checklist before publishing a `kgteach` release.

## Preflight

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run mypy src
uv build
```

Optional real engine smoke test:

```bash
KGTEACH_REAL_KATAGO=1 uv run pytest tests/test_real_katago_smoke.py -q
```

Optional MCP smoke test:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"engine_health","arguments":{}}}' \
  | uv run kgteach-mcp
```

## Version

Update all of these together:

- `pyproject.toml`
- `src/kgteach/mcp_server.py`
- plugin manifests under `plugins/kgteach/`
- local marketplace manifests, when included

## Publish

Publishing is handled by `.github/workflows/publish.yml` on GitHub release
publication. Configure PyPI Trusted Publishing for the repository before the
first release.

Manual fallback:

```bash
uv build
uv publish
```

## Post-Release Smoke

After the package is available:

```bash
uvx --from kgteach kgteach engine health
uvx --from kgteach kgteach-mcp
```

For plugin mode, verify the plugin bundle with the target host you plan to
support.
