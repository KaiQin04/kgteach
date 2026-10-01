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

## 當函式庫使用

外部服務（例如 GoReview worker）可將完整線性化棋史轉成 `game` dict，
直接使用與 CLI 相同的 query、分析正規化與教學流程。

```python
import asyncio
from typing import Any

import kgteach

game = {
    "board_size": 19,
    "rules": "chinese",
    "komi": 7.5,
    "initial_stones": [],
    "moves": [
        {"turn": 1, "player": "B", "move": "Q16"},
        {"turn": 2, "player": "W", "move": "D4"},
    ],
}


async def analyze_and_teach() -> dict[str, Any]:
    """Analyze a complete history and close the runtime after teaching."""

    query = kgteach.build_query_from_game(
        game, request_id="pos-1", turns=[2], visits=32
    )
    runtime = kgteach.AnalysisRuntime(cache_dir=None)
    try:
        responses, cache_hit = await runtime.analyze(query, timeout=10.0)
        analysis = kgteach.normalize_analysis_responses(
            responses, perspective="black"
        )
        return kgteach.teach_move(game, turn=2, analysis=analysis["positions"][0])
    finally:
        await runtime.close()


lesson = asyncio.run(analyze_and_teach())
```

讓子局面的 `initial_stones` 使用 `{"player": "B", "move": "D4"}` 等項目；
`moves` 保留完整棋史與 pass。常駐 worker 應在同一個 event loop 重用
`AnalysisRuntime`，於服務停止時呼叫 `close()`。

`cache_dir=None` 只將分析結果存於記憶體；傳入 `Path` 才啟用磁碟快取。
可透過 `config_resolver` 與 `client_factory` 注入設定與 client。

若 KataGo 設定使用黑方視角，正規化時傳入 `source_perspective="black"`；
白方視角則傳入 `"white"`，詳見 [KataGo 設定](katago-setup.md)。
套件版本為 `0.2.0`，JSON 契約的 `SCHEMA_VERSION` 保持 `0.1.0`。
