# kgteach

[![CI](https://github.com/KaiQin04/kgteach/actions/workflows/ci.yml/badge.svg)](https://github.com/KaiQin04/kgteach/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A CLI tool that lets AI agents analyze Go games with KataGo and explain key
mistakes from SGF files.

`kgteach` is built for Go, baduk, and weiqi review workflows where an LLM agent
needs evidence before it teaches. KataGo supplies the position analysis,
`kgteach` turns that analysis into stable JSON, and the agent uses that
evidence to explain candidate moves, mistakes, variations, quizzes, and
rank-aware study plans.

## Why This Exists

Most KataGo tools are designed for people looking at a board in a GUI. AI
agents need a different interface: inspect an SGF, call KataGo, compare moves,
validate a proposed variation, and return a compact payload that is safe to
quote in a teaching conversation.

`kgteach` solves that adapter layer. It is not a KataGo replacement, a Go GUI,
or a natural-language teacher by itself. It is the tool layer that lets an AI
Go tutor use KataGo without inventing winrates, score losses, ownership maps,
or rank-specific claims.

In about three minutes from a local checkout, you can:

- Check whether KataGo, models, configs, and Human SL support are available.
- Inspect an SGF and get normalized game metadata.
- Ask for a review plan of the most teachable mistakes.
- Validate a variation before an agent explains it to a learner.

## Demo

Inspect an SGF:

```bash
uv run kgteach game inspect fixtures/simple_9x9.sgf
```

Formatted output:

```json
{
  "ok": true,
  "schema_version": "0.1.0",
  "command": "game.inspect",
  "data": {
    "board_size": 9,
    "rules": "Chinese",
    "komi": 6.5,
    "metadata": {
      "game_name": "Simple 9x9",
      "black_player": "Student",
      "white_player": "Teacher",
      "result": "W+R"
    },
    "move_count": 4,
    "initial_stone_count": 0,
    "warnings": []
  },
  "warnings": [],
  "debug": null
}
```

Validate a learner's variation before explaining it:

```bash
uv run kgteach teach line fixtures/simple_9x9.sgf --turn 2 --line "C3 D4"
```

The response tells an agent that the line is legal by local rules, that engine
evaluation is still needed, and which player moves at each ply. This is the
core pattern: `kgteach` gives machine-readable evidence; the agent writes the
human explanation.

With a local KataGo analysis engine configured, the same workflow can produce a
rank-aware review plan:

```bash
uv run kgteach teach plan path/to/game.sgf --student-rank 8k --max-moments 6
```

## Quickstart

This project is managed with `uv`.

```bash
uv sync
uv run kgteach engine health
uv run kgteach game inspect fixtures/simple_9x9.sgf
```

For KataGo-backed review, configure a local KataGo binary, model, and analysis
config:

```bash
uv run kgteach config init
uv run kgteach teach plan path/to/game.sgf --student-rank 8k --max-moments 6
```

See [KataGo setup](docs/katago-setup.md) for environment variables, macOS local
discovery, daemon mode, and optional Human SL model support.

## Example Use Cases

- **AI Go tutor**: review a student's SGF and explain the biggest mistakes with
  KataGo evidence.
- **LLM agent tool**: expose Go analysis through JSON-only CLI commands or the
  bundled stdio MCP server.
- **Baduk or weiqi study bot**: compare candidate moves, generate quizzes, and
  produce rank-aware follow-up questions.
- **Variation checker**: validate captures, suicide, simple ko, pass moves, and
  turn color before discussing a line.
- **Knowledge-pack bridge**: return concept tags and hooks for joseki,
  direction of play, tesuji, life-and-death, endgame, and pedagogy skills.

## Why Not Existing Tools?

- GUI tools are optimized for humans, not autonomous agents.
- Raw KataGo analysis is powerful but too low-level for teaching workflows.
- LLMs need a stable contract so they can cite evidence instead of guessing.
- Go education needs more than a best move: it needs candidate comparison,
  legality checks, rank context, and follow-up prompts.

`kgteach` keeps those concerns separate. KataGo analyzes positions, `kgteach`
normalizes evidence, and the teaching agent decides how to explain the lesson.

## Command Surface

All agent-facing commands write exactly one JSON payload to stdout. Logs,
warnings, and progress messages belong on stderr.

Common flows:

```bash
kgteach engine health
kgteach game inspect game.sgf
kgteach teach plan game.sgf --student-rank 8k --max-moments 6
kgteach teach move game.sgf --turn 97 --student-rank 8k
kgteach teach compare game.sgf --turn 97 --moves Q10,R12,P11
kgteach teach line game.sgf --turn 97 --line "R12 Q10 R10"
kgteach teach quiz game.sgf --turn 97 --student-rank 8k
```

Agent and MCP details:

- [Agent workflow](docs/agent-workflow.md)
- [JSON schema notes](docs/schema.md)
- [Knowledge packs](docs/knowledge-packs.md)
- [Local plugin testing](docs/local-plugin-testing.md)

## Project Status

`kgteach` is currently alpha software with the v0.1 agent-facing command
surface:

- JSON-only CLI output for agent calls.
- SGF normalization and game inspection.
- KataGo JSON-line protocol routing and runtime error mapping.
- Local daemon lifecycle plus Unix-socket analyze routing and persistent disk
  cache.
- Optional local KataGo install auto-discovery on macOS.
- Variation legality checks for captures, suicide, simple ko, pass, and turn
  color.
- Compact analyze and teach commands.
- Rank-compare JSON shape with human policy likelihood extraction when a
  KataGo Human SL model is configured.
- Agent plugin manifests and bundled stdio MCP transport.
- Knowledge hooks for joseki, principles, tesuji, life-and-death, endgame, and
  pedagogy skills.

When KataGo files are missing, commands return explicit engine or
`analysis_required` markers instead of fabricated evaluations.

## Roadmap

- Publish signed GitHub releases and PyPI packages for easier `uvx` usage.
- Add richer terminal demos and example SGF walkthroughs.
- Expand rank-aware review examples around KataGo Human SL models.
- Provide sample knowledge packs for joseki, tesuji, and endgame concepts.
- Add more agent integration guides for MCP-capable hosts.
- Build regression fixtures for common teaching scenarios and illegal lines.

## Contributing

Contributions are welcome when they preserve the core contract: factual Go
analysis evidence first, natural-language teaching second.

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Useful contributions include:

- Better SGF fixtures and edge cases.
- Teaching payload improvements that remain machine-readable.
- KataGo setup documentation for more platforms.
- Agent integration examples.
- Tests for legality, error envelopes, and runtime failures.

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

## License

MIT. See [LICENSE](LICENSE).
