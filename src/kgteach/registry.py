"""Command registry exposed to teaching agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A compact public command description."""

    name: str
    summary: str
    output: str


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "engine.health",
        "Check CLI, config, model, and daemon availability.",
        "EngineHealth",
    ),
    CommandSpec("engine.version", "Query kgteach and KataGo version metadata.", "EngineVersion"),
    CommandSpec("engine.schema", "List supported commands and schema names.", "EngineSchema"),
    CommandSpec("daemon.start", "Start the local kgteach daemon.", "DaemonStatus"),
    CommandSpec("daemon.status", "Report daemon process status.", "DaemonStatus"),
    CommandSpec("daemon.stop", "Stop the local kgteach daemon.", "DaemonStatus"),
    CommandSpec("config.init", "Create a local kgteach config file.", "Config"),
    CommandSpec("config.show", "Show resolved config.", "Config"),
    CommandSpec("game.inspect", "Read SGF metadata without deep analysis.", "GameInspect"),
    CommandSpec("game.normalize", "Convert SGF into kgteach internal JSON.", "Game"),
    CommandSpec("game.moves", "List all moves in an SGF.", "GameMoves"),
    CommandSpec("game.position", "Return the position around one turn.", "GamePosition"),
    CommandSpec("game.slice", "Return a move range from a game.", "GameSlice"),
    CommandSpec("analyze.position", "Analyze one position.", "PositionAnalysis"),
    CommandSpec(
        "analyze.game",
        "Analyze one or more turns with KataGo analyzeTurns.",
        "GameAnalysis",
    ),
    CommandSpec("teach.plan", "Find the most teachable review moments.", "ReviewPlan"),
    CommandSpec("teach.summary", "Return a compact game storyline.", "TeachingSummary"),
    CommandSpec("teach.move", "Explain one move with candidate deltas and hooks.", "MoveTeaching"),
    CommandSpec("teach.compare", "Compare candidate moves.", "MoveComparison"),
    CommandSpec("teach.line", "Validate a proposed variation.", "LineTeaching"),
    CommandSpec(
        "teach.territory",
        "Summarize ownership and score by regions.",
        "TerritoryTeaching",
    ),
    CommandSpec("teach.quiz", "Generate a compact teaching quiz.", "Quiz"),
    CommandSpec("teach.mistakes", "List mistakes above a configured threshold.", "Mistakes"),
    CommandSpec(
        "teach.rank_compare",
        "Compare move likelihoods across human ranks.",
        "RankCompare",
    ),
)


def command_schema() -> dict[str, dict[str, str]]:
    """Return the public command registry keyed by command name."""

    return {
        item.name: {
            "summary": item.summary,
            "output": item.output,
        }
        for item in COMMANDS
    }
