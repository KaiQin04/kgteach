# Contributing to kgteach

Thanks for helping make Go review safer for AI agents.

`kgteach` is an evidence adapter. KataGo produces analysis, `kgteach` returns
stable machine-readable payloads, and the agent writes the human explanation.
Contributions should preserve that separation.

## Good First Contributions

- Add SGF fixtures that cover real review edge cases.
- Improve setup docs for KataGo on macOS, Linux, or Windows.
- Add tests for error envelopes, illegal variations, or parser behavior.
- Improve teaching payloads while keeping them deterministic and JSON-friendly.
- Add agent integration examples for MCP-capable hosts.

## Development Setup

Use the project-managed environment:

```bash
uv sync
```

Run the standard checks:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Run the optional real KataGo smoke test only when a local KataGo binary, model,
and analysis config are available:

```bash
KGTEACH_REAL_KATAGO=1 uv run pytest tests/test_real_katago_smoke.py
```

## Design Rules

- Commands must write exactly one JSON payload to stdout.
- Logs, warnings, progress, and debug text must go to stderr.
- Do not fabricate winrate, score, ownership, Human SL, or rank claims.
- Missing engine evidence should be explicit through errors, warnings, or
  `analysis_required` markers.
- Keep natural-language teaching outside the core adapter unless the payload is
  a short machine-readable hint.

## Pull Requests

Before opening a pull request:

- Include tests for behavior changes.
- Update README or docs when user-facing commands change.
- Keep fixtures small unless a larger SGF is necessary for the edge case.
- Mention whether the change was tested with real KataGo or only local unit
  tests.

## Issues

Useful bug reports include:

- The command you ran.
- The SGF shape or a minimal fixture.
- Whether KataGo is configured locally.
- Expected JSON behavior.
- Actual JSON output or error envelope.

Please avoid pasting private games unless you have permission to share them.
