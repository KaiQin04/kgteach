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
