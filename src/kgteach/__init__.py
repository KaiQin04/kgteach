"""Agent-first KataGo teaching adapter.

Library entry points mirror the CLI: build a query from a normalized game,
run it through an ``AnalysisRuntime`` (or any engine), normalize the raw
responses, then hand the result to the ``teach_*`` services.
"""

from kgteach.analysis import build_query_from_game, normalize_analysis_responses
from kgteach.config import KataGoConfig, resolve_katago_config
from kgteach.contract import SCHEMA_VERSION, ErrorCode, envelope_error, envelope_ok
from kgteach.engine_runtime import AnalysisRuntime
from kgteach.game import normalize_sgf
from kgteach.teach_service import (
    teach_compare,
    teach_line,
    teach_mistakes,
    teach_move,
    teach_plan,
    teach_quiz,
    teach_rank_compare,
    teach_summary,
    teach_territory,
)
from kgteach.teaching import classify_severity

__all__ = [
    "SCHEMA_VERSION",
    "ErrorCode",
    "envelope_ok",
    "envelope_error",
    "KataGoConfig",
    "resolve_katago_config",
    "AnalysisRuntime",
    "build_query_from_game",
    "normalize_analysis_responses",
    "normalize_sgf",
    "teach_move",
    "teach_compare",
    "teach_plan",
    "teach_summary",
    "teach_line",
    "teach_territory",
    "teach_quiz",
    "teach_mistakes",
    "teach_rank_compare",
    "classify_severity",
]
