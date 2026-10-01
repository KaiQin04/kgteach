# KataGo Setup

`kgteach` uses KataGo's JSON analysis engine.

The expected KataGo command shape is:

```bash
katago analysis -config analysis.cfg -model model.bin.gz
```

Configuration can be supplied in this order:

1. CLI arguments.
2. Environment variables.
3. Project config.
4. User config.
5. Optional local app-managed install discovery on macOS.
6. Built-in defaults.

Environment variables:

```bash
export KGTEACH_KATAGO_BINARY=/path/to/katago
export KGTEACH_KATAGO_MODEL=/path/to/model.bin.gz
export KGTEACH_KATAGO_CONFIG=/path/to/analysis.cfg
export KGTEACH_KATAGO_HUMAN_MODEL=/path/to/human-model.bin.gz
```

Disable fallback discovery when tests or CI should avoid local machine state:

```bash
export KGTEACH_DISABLE_LOCAL_APP_DISCOVERY=1
```

Run a real local smoke test:

```bash
KGTEACH_REAL_KATAGO=1 uv run pytest tests/test_real_katago_smoke.py
```

The daemon keeps one KataGo process alive and caches analysis responses under
the daemon state directory:

```bash
uv run kgteach daemon start
uv run kgteach daemon status
uv run kgteach daemon stop
```

Human supervised-learning models are optional. Without a human model, rank-aware
commands should report that human policy support is unavailable and fall back to
heuristics.

## 分析視角與 `reportAnalysisWinratesAs`

`kgteach` 預設 KataGo 的勝率與目差使用待落子方視角。如果分析設定包含
`reportAnalysisWinratesAs = BLACK`（或 `WHITE`），呼叫正規化函式時必須宣告
來源視角，讓轉換只發生一次：

```python
normalize_analysis_responses(responses, perspective="black", source_perspective="black")
```

`source_perspective` 與 `perspective` 都接受 `side_to_move`、`black`、`white`。
來源視角填錯會讓其中一方的目差符號與勝率被錯誤翻轉。CLI 使用預設來源視角
`side_to_move`，因此 CLI 使用的 KataGo 設定應省略 `reportAnalysisWinratesAs`。
