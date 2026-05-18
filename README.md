# kgteach

[![CI](https://github.com/KaiQin04/kgteach/actions/workflows/ci.yml/badge.svg)](https://github.com/KaiQin04/kgteach/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Agent-first KataGo teaching adapter.

`kgteach` turns KataGo analysis-engine output into stable JSON that an LLM
teaching agent can use for Go review, candidate comparison, variation checking,
quiz generation, and follow-up questions.

## What This Is Not

- This is not a Go GUI.
- This is not a KataGo replacement.
- This is not a natural-language Go teacher by itself.

`kgteach` is a machine-first CLI adapter for teaching agents. KataGo provides
the analysis evidence, `kgteach` normalizes and summarizes that evidence, and
the agent decides how to explain it to a human learner.

## Project Status

The current implementation provides the agent-facing v0.1 command surface:

- JSON-only CLI output for agent calls.
- SGF normalization and game inspection.
- KataGo JSON-line protocol routing and runtime error mapping.
- Local daemon lifecycle plus Unix-socket analyze routing and persistent disk cache.
- Optional local KataGo install auto-discovery on macOS.
- Variation legality checks for captures, suicide, simple ko, pass, and turn color.
- Compact analyze and teach commands.
- Rank-compare JSON shape with human policy likelihood extraction when a KataGo
  human SL model is configured.
- Agent plugin manifests.
- Bundled stdio MCP transport used internally by plugin hosts.
- Knowledge hooks for external joseki, principles, tesuji, life-and-death,
  endgame, and pedagogy skills.

KataGo-backed analysis requires a local KataGo binary, model, and analysis
config. Without those files, teaching commands still return stable JSON with
explicit `analysis_required` markers rather than fabricated evaluations.

## Installation

This project is managed with `uv`.

```bash
uv sync
uv run kgteach engine health
```

One-command local setup:

```bash
scripts/setup-local.sh
```

The setup script creates local machine config. Direct root `.mcp.json`
generation is available only for low-level debugging with
`scripts/setup-local.sh --write-project-mcp`.

Set `KGTEACH_DISABLE_LOCAL_APP_DISCOVERY=1` to disable local app-managed KataGo
discovery in CI or reproducible test environments.

Development checks:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Optional real KataGo smoke test:

```bash
KGTEACH_REAL_KATAGO=1 uv run pytest tests/test_real_katago_smoke.py
```

Bundled plugin transport smoke test:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"engine_health","arguments":{}}}' \
  | uv run kgteach-mcp
```

Local plugin testing:

- Plugin bundle: `plugins/kgteach/`
- Plugin skill: `plugins/kgteach/skills/kgteach/SKILL.md`
- Direct root `.mcp.json` is optional debug-only and is not the primary install path.
- Detailed guide: `docs/local-plugin-testing.md`

## Command Contract

All agent-facing commands write exactly one JSON payload to stdout. Logs,
warnings, and progress messages must go to stderr.

Success envelope:

```json
{
  "ok": true,
  "schema_version": "0.1.0",
  "command": "teach.move",
  "data": {},
  "warnings": [],
  "debug": null
}
```

Error envelope:

```json
{
  "ok": false,
  "schema_version": "0.1.0",
  "command": "teach.move",
  "error": {
    "code": "TIMEOUT",
    "message": "Analysis did not finish within 30 seconds."
  },
  "warnings": [],
  "partial": null
}
```

## Knowledge Hooks

`kgteach` does not hard-code a joseki encyclopedia or Go theory textbook into
the core package. Teaching commands return hooks that an agent can pass to
external knowledge skills or knowledge packs:

```json
{
  "concept_tags": ["sente", "shape weakness", "corner joseki"],
  "position_fingerprint": {
    "board_size": 19,
    "turn": 97,
    "local_region": "upper_right",
    "hash": "..."
  },
  "knowledge_queries": [
    {
      "type": "joseki",
      "anchor": "star_point_low_pincer",
      "region": "upper_right"
    },
    {
      "type": "principle",
      "tags": ["sente", "attack timing"]
    }
  ],
  "knowledge_refs": [],
  "suggested_followups": []
}
```

This keeps the KataGo adapter reliable and lets specialized skills provide
human knowledge such as joseki, direction of play, tesuji, endgame, and
rank-aware pedagogy.
