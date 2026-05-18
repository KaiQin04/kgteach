---
name: kgteach
description: Use kgteach when a user asks for Go, baduk, or weiqi SGF analysis, KataGo-backed review, move comparison, variation validation, quizzes, or rank-aware teaching.
---

# kgteach

Use the kgteach MCP tools or CLI before making factual claims about a Go
position. Treat kgteach as an evidence adapter: it provides stable JSON from
KataGo and teaching hooks, while the agent writes the human explanation.

Prefer these flows:

- Check availability with `engine_health`.
- Inspect an SGF with `game_inspect`.
- Explain a move with `teach_move`.
- Compare alternatives with `teach_compare`.
- Validate user-proposed lines with `teach_line`.
- Use `teach_rank_compare` only when `engine_health` reports `human_policy`.

Do not invent winrate, score, ownership, or Human SL likelihood numbers. If the
tool reports missing engine analysis, say that analysis evidence is unavailable
instead of fabricating it.
