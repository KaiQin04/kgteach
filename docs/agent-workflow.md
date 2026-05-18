# Agent Workflow

`kgteach` is designed for LLM agents that need reliable Go analysis evidence.

Typical flow:

1. Check runtime availability.
   ```bash
   kgteach engine health
   ```
2. Inspect the game.
   ```bash
   kgteach game inspect game.sgf
   ```
3. Ask for a review plan.
   ```bash
   kgteach teach plan game.sgf --student-rank 8k --max-moments 6
   ```
4. Explain one move.
   ```bash
   kgteach teach move game.sgf --turn 97 --student-rank 8k
   ```
5. Compare a user-suggested move.
   ```bash
   kgteach teach compare game.sgf --turn 97 --moves Q10,R12,P11
   ```
6. Validate a variation before explaining it.
   ```bash
   kgteach teach line game.sgf --turn 97 --line "R12 Q10 R10"
   ```

The agent should treat `kgteach` output as structured evidence, then use its
own teaching policy and optional knowledge skills to produce natural language.
