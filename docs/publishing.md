# Package Publishing

`kgteach` is packaged with `uv` and `uv_build`.

## Build Locally

```bash
uv build
```

Expected outputs:

```text
dist/kgteach-<version>.tar.gz
dist/kgteach-<version>-py3-none-any.whl
```

## PyPI Trusted Publishing

The publish workflow uses `pypa/gh-action-pypi-publish` with GitHub OIDC.
Before publishing the first release:

1. Create the PyPI project.
2. Add a trusted publisher for the GitHub repository.
3. Ensure the workflow file is `.github/workflows/publish.yml`.
4. Publish a GitHub release.

The workflow runs tests, lint, type check, builds the package, and publishes to
PyPI only after a GitHub release is published.

## CLI Entrypoints

The package exposes:

- `kgteach`
- `kg`
- `kgteach-mcp`

`kgteach-mcp` is the stdio MCP server used internally by local plugin configs.
