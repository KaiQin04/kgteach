# Knowledge Pack Contract

`kgteach` keeps Go knowledge outside the core engine adapter. Teaching
commands emit retrieval hooks that an LLM agent can pass to specialized skills
or knowledge packs.

Registered packs:

- `joseki`: corner patterns, approaches, pincers, and common continuations.
- `principles`: shape, sente, gote, direction, thickness, and influence.
- `tesuji`: forcing moves, cuts, nets, ladders, squeezes, and atari.
- `life_death`: eyeshape, liberties, semeai, nakade, and survival.
- `endgame`: yose, sente endgame, reverse sente, counting, and boundaries.
- `pedagogy`: rank-aware lesson framing, review sequencing, and exercises.

Each hook has this compact shape:

```json
{
  "pack": "principles",
  "hook": "knowledge.principles.query",
  "tag": "sente",
  "query": "sente reference for sgf:19:97",
  "position_fingerprint": "sgf:19:97",
  "student_rank": "8k"
}
```

Knowledge packs should return source-backed material and should not override
KataGo evaluation evidence. The agent is responsible for combining `kgteach`
analysis with retrieved knowledge into natural-language teaching.
