# GitHub SEO and Launch Checklist

Use this checklist when preparing the public repository page, release notes, or
launch post for `kgteach`.

## Positioning

One-line pitch:

> A CLI tool that lets AI agents analyze Go games with KataGo and explain key
> mistakes from SGF files.

The memorable category is not "KataGo wrapper." It is:

> KataGo-powered Go review for AI agents.

## Repository Topics

Recommended GitHub topics:

- `katago`
- `go`
- `go-game`
- `baduk`
- `weiqi`
- `sgf`
- `game-analysis`
- `go-review`
- `ai-agent`
- `llm-tools`
- `mcp-server`
- `cli-tool`

These combine technology, domain, and use case so the repository can be found
by KataGo users, Go players, AI-agent builders, and MCP tool developers.

## Community Profile

The repository should keep these files current:

- `README.md`
- `LICENSE`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `.github/ISSUE_TEMPLATE/`
- `.github/PULL_REQUEST_TEMPLATE.md`

## Release Notes Template

Use this structure for GitHub releases:

````markdown
## What's new

-

## Breaking changes

- None.

## Installation

```bash
uvx --from kgteach kgteach engine health
```

## Example

```bash
kgteach teach plan game.sgf --student-rank 8k --max-moments 6
```

## Checks

- `uv run pytest`
- `uv run ruff check .`
- `uv run mypy src`
````

## Small, Accurate Launch Channels

Start with people who already feel the problem:

- OGS Forum
- r/baduk
- Life in 19x19
- KataGo, Sabaki, and Lizzie communities
- Discord Go servers
- Taiwan, Japan, and Korea Go communities
- MCP and AI-agent tool communities

Avoid generic "I launched a CLI" posts. Lead with the workflow:

> How to let an AI agent review Go games with KataGo from the command line

## Article Outline

Title:

> How to let an AI agent review Go games with KataGo from the command line

Structure:

1. The problem: LLMs should not invent Go analysis.
2. Existing tools: great GUIs, weak agent interface.
3. The adapter: SGF in, KataGo evidence out, JSON contract always.
4. 30-second demo: inspect an SGF and validate a variation.
5. KataGo-backed review plan command.
6. Roadmap and contribution requests.
