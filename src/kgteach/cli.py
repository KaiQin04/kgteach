"""Command-line interface for kgteach."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from kgteach.analysis import build_query_from_game, normalize_analysis_responses
from kgteach.config import KataGoConfig, resolve_katago_config
from kgteach.contract import ErrorCode, envelope_error, envelope_ok
from kgteach.daemon import request_daemon, start_daemon, status_daemon, stop_daemon
from kgteach.game import inspect_sgf, moves_payload, normalize_sgf, position_payload, slice_payload
from kgteach.go_rules import gtp_to_point
from kgteach.katago import build_analysis_query
from kgteach.knowledge import registry_payload
from kgteach.registry import command_schema
from kgteach.runtime import (
    KataGoRuntimeError,
    analyze_query_sync,
    check_runtime_ready,
    query_version_sync,
    runtime_ready,
)
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


class CLIUsageError(ValueError):
    """Raised when CLI arguments cannot be parsed into a command."""


class JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser variant that reports parse failures as JSON envelopes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        """Raise instead of printing usage text to stderr and exiting."""

        raise CLIUsageError(message)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the kgteach CLI."""

    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        payload = _dispatch(args)
        exit_code = 0 if payload.get("ok") else _exit_code(payload)
    except CLIUsageError as exc:
        payload = envelope_error(
            command="unknown",
            code=ErrorCode.INVALID_INPUT,
            message=str(exc),
        )
        exit_code = 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        payload = envelope_error(
            command="unknown",
            code=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        exit_code = 7

    try:
        output = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as exc:  # pragma: no cover - hard guard for JSON purity
        output = json.dumps(
            envelope_error(
                command=str(payload.get("command", "unknown")),
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Command produced non-standard JSON values: {exc}",
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        exit_code = 7
    sys.stdout.write(output + "\n")
    return exit_code


def _build_parser() -> JSONArgumentParser:
    parser = JSONArgumentParser(
        prog="kgteach",
        description="Agent-first KataGo teaching adapter.",
    )
    subparsers = parser.add_subparsers(
        dest="group",
        required=True,
        parser_class=JSONArgumentParser,
    )

    engine = subparsers.add_parser("engine")
    engine_subparsers = engine.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    engine_subparsers.add_parser("health")
    engine_subparsers.add_parser("schema")
    engine_subparsers.add_parser("version")

    daemon = subparsers.add_parser("daemon")
    daemon_subparsers = daemon.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    daemon_subparsers.add_parser("start")
    daemon_subparsers.add_parser("status")
    daemon_subparsers.add_parser("stop")

    config = subparsers.add_parser("config")
    config_subparsers = config.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    config_subparsers.add_parser("init")
    config_subparsers.add_parser("show")

    game = subparsers.add_parser("game")
    game_subparsers = game.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    for name in ("inspect", "normalize", "moves"):
        command = game_subparsers.add_parser(name)
        command.add_argument("sgf_path")
    position = game_subparsers.add_parser("position")
    position.add_argument("sgf_path")
    position.add_argument("--turn", type=int, required=True)
    game_slice = game_subparsers.add_parser("slice")
    game_slice.add_argument("sgf_path")
    game_slice.add_argument("--from-turn", dest="from_turn", type=int, required=True)
    game_slice.add_argument("--to-turn", dest="to_turn", type=int, required=True)

    analyze = subparsers.add_parser("analyze")
    analyze_subparsers = analyze.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    analyze_position = analyze_subparsers.add_parser("position")
    analyze_position.add_argument("--stdin", action="store_true")
    analyze_position.add_argument("--timeout", type=float, default=30.0)
    analyze_position.add_argument("--top", type=int, default=5)
    analyze_position.add_argument("--raw", action="store_true")
    analyze_game = analyze_subparsers.add_parser("game")
    analyze_game.add_argument("sgf_path")
    analyze_game.add_argument("--turns", default="all")
    analyze_game.add_argument("--visits", type=int, default=800)
    analyze_game.add_argument("--timeout", type=float, default=30.0)
    analyze_game.add_argument("--top", type=int, default=5)
    analyze_game.add_argument("--raw", action="store_true")

    teach = subparsers.add_parser("teach")
    teach_subparsers = teach.add_subparsers(
        dest="command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    for name in ("plan", "summary", "mistakes"):
        command = teach_subparsers.add_parser(name)
        command.add_argument("sgf_path")
        command.add_argument("--student-rank", default=None)
        command.add_argument("--visits", type=int, default=800)
        command.add_argument("--timeout", type=float, default=30.0)
        if name == "plan":
            command.add_argument("--max-moments", type=int, default=6)
        if name == "mistakes":
            command.add_argument("--min-score-loss", type=float, default=3.0)
    for name in ("move", "territory", "quiz", "rank-compare"):
        command = teach_subparsers.add_parser(name)
        command.add_argument("sgf_path")
        command.add_argument("--turn", type=int, required=True)
        command.add_argument("--student-rank", default=None)
        command.add_argument("--visits", type=int, default=800)
        command.add_argument("--timeout", type=float, default=30.0)
    compare = teach_subparsers.add_parser("compare")
    compare.add_argument("sgf_path")
    compare.add_argument("--turn", type=int, required=True)
    compare.add_argument("--moves", required=True)
    compare.add_argument("--visits", type=int, default=800)
    compare.add_argument("--timeout", type=float, default=30.0)
    line = teach_subparsers.add_parser("line")
    line.add_argument("sgf_path")
    line.add_argument("--turn", type=int, required=True)
    line.add_argument("--line", required=True)
    line.add_argument("--visits", type=int, default=800)
    line.add_argument("--timeout", type=float, default=30.0)
    rank_compare = teach_subparsers.choices["rank-compare"]
    rank_compare.add_argument("--ranks", required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.group == "engine" and args.command == "health":
        resolved = resolve_katago_config()
        readiness = check_runtime_ready(resolved)
        return envelope_ok(
            command="engine.health",
            data={
                "katago_found": readiness["katago_found"],
                "model_found": readiness["model_found"],
                "config_found": readiness["config_found"],
                "daemon_running": status_daemon()["running"],
                "human_model_found": readiness["human_model_found"],
                "capabilities": readiness["capabilities"],
                "errors": readiness["errors"],
            },
        )
    if args.group == "engine" and args.command == "schema":
        return envelope_ok(
            command="engine.schema",
            data={
                "commands": command_schema(),
                "knowledge_packs": registry_payload(),
            },
        )
    if args.group == "engine" and args.command == "version":
        resolved = resolve_katago_config()
        katago: dict[str, Any] | None = None
        warnings: list[dict[str, str]] = []
        if runtime_ready(resolved):
            try:
                katago = query_version_sync(config=resolved, timeout=10.0)
            except Exception as exc:
                warnings.append(_runtime_warning(exc))
        else:
            warnings.append(_runtime_not_configured_warning())
        return envelope_ok(
            command="engine.version",
            data={
                "kgteach_version": "0.1.0",
                "katago": katago,
            },
            warnings=warnings,
        )
    if args.group == "daemon":
        if args.command == "start":
            status = start_daemon()
        elif args.command == "stop":
            status = stop_daemon()
        else:
            status = status_daemon()
        return envelope_ok(
            command=f"daemon.{args.command}",
            data=status,
        )
    if args.group == "config":
        resolved = resolve_katago_config()
        created = False
        path: str | None = None
        if args.command == "init":
            config_path = Path(".kgteach") / "katago.json"
            created = _init_project_config(config_path, resolved)
            path = str(config_path)
        return envelope_ok(
            command=f"config.{args.command}",
            data={
                "path": path,
                "created": created,
                "katago": {
                    "binary": resolved.binary,
                    "model": resolved.model,
                    "config": resolved.config,
                    "human_model": resolved.human_model,
                },
                "defaults": {
                    "rules": "japanese",
                    "komi": 6.5,
                    "visits": 800,
                    "top": 5,
                    "perspective": "side_to_move",
                },
            },
        )
    if args.group == "game":
        missing = _missing_sgf_error(f"game.{args.command}", args.sgf_path)
        if missing is not None:
            return missing
        try:
            game = normalize_sgf(_read_text(args.sgf_path))
            if args.command == "inspect":
                data = inspect_sgf(_read_text(args.sgf_path))
            elif args.command == "normalize":
                data = {"game": game}
            elif args.command == "moves":
                data = moves_payload(game)
            elif args.command == "position":
                data = position_payload(game, args.turn)
            elif args.command == "slice":
                data = slice_payload(game, start_turn=args.from_turn, end_turn=args.to_turn)
            else:
                data = {}
        except ValueError as exc:
            return envelope_error(
                command=f"game.{args.command}",
                code=ErrorCode.SGF_PARSE_ERROR,
                message=str(exc),
            )
        return envelope_ok(command=f"game.{args.command}", data=data)
    if args.group == "analyze" and args.command == "game":
        missing = _missing_sgf_error("analyze.game", args.sgf_path)
        if missing is not None:
            return missing
        game = normalize_sgf(_read_text(args.sgf_path))
        turns = (
            list(range(0, len(game["moves"]) + 1))
            if args.turns == "all"
            else _parse_turns(args.turns)
        )
        query = _query_from_game(
            game,
            request_id="analyze-game",
            turns=turns,
            visits=args.visits,
        )
        try:
            analysis, warnings = _run_analysis(
                query,
                timeout=args.timeout,
                top=args.top,
                raw=args.raw,
            )
        except KataGoRuntimeError as exc:
            return _runtime_error_envelope("analyze.game", exc)
        return envelope_ok(
            command="analyze.game",
            data={
                "query": query,
                "analysis": analysis,
            },
            warnings=warnings,
        )
    if args.group == "analyze" and args.command == "position":
        if not args.stdin:
            return envelope_error(
                command="analyze.position",
                code=ErrorCode.INVALID_INPUT,
                message="Position analysis requires JSON input on stdin.",
            )
        try:
            position = json.loads(sys.stdin.read())
            query = _query_from_position(position)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return envelope_error(
                command="analyze.position",
                code=ErrorCode.INVALID_INPUT,
                message=str(exc),
            )
        try:
            analysis, warnings = _run_analysis(
                query,
                timeout=args.timeout,
                top=args.top,
                raw=args.raw,
            )
        except KataGoRuntimeError as exc:
            return _runtime_error_envelope("analyze.position", exc)
        return envelope_ok(
            command="analyze.position",
            data={
                "query": query,
                "analysis": analysis,
            },
            warnings=warnings,
        )
    if args.group == "teach":
        command_name = args.command.replace("-", "_")
        missing = _missing_sgf_error(f"teach.{command_name}", args.sgf_path)
        if missing is not None:
            return missing
        try:
            game = normalize_sgf(_read_text(args.sgf_path))
            data = _teach_payload(command_name, game, args)
        except KataGoRuntimeError as exc:
            return _runtime_error_envelope(f"teach.{command_name}", exc)
        except ValueError as exc:
            return envelope_error(
                command=f"teach.{command_name}",
                code=ErrorCode.INVALID_INPUT,
                message=str(exc),
            )
        warnings = data.pop("_warnings", []) if isinstance(data, dict) else []
        return envelope_ok(command=f"teach.{command_name}", data=data, warnings=warnings)
    return envelope_error(
        command="unknown",
        code=ErrorCode.INVALID_INPUT,
        message="Unsupported command.",
    )


def _missing_sgf_error(command: str, sgf_path: str) -> dict[str, Any] | None:
    path = Path(sgf_path)
    if path.exists():
        return None
    return envelope_error(
        command=command,
        code=ErrorCode.SGF_PARSE_ERROR,
        message="SGF file does not exist.",
        path=str(path),
    )


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _init_project_config(config_path: Path, resolved: KataGoConfig) -> bool:
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "katago": {
            "binary": resolved.binary,
            "model": resolved.model,
            "config": resolved.config,
            "human_model": resolved.human_model,
        }
    }
    config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def _query_from_position(position: dict[str, Any]) -> dict[str, Any]:
    board_size = int(position.get("board_size", 19))
    default_turns = [len(position.get("moves", []))]
    return build_analysis_query(
        request_id=str(position.get("id", "analyze-position")),
        moves=position.get("moves", []),
        initial_stones=position.get("initial_stones", []),
        rules=str(position.get("rules", "japanese")),
        komi=float(position.get("komi", 6.5)),
        board_size=(board_size, board_size),
        analyze_turns=[int(turn) for turn in position.get("turns", default_turns)],
        max_visits=int(position.get("max_visits", position.get("visits", 800))),
        include_ownership=bool(position.get("include_ownership", False)),
        include_policy=bool(position.get("include_policy", True)),
        human_sl_profile=position.get("human_sl_profile"),
    )


def _query_from_game(
    game: dict[str, Any],
    *,
    request_id: str,
    turns: list[int],
    visits: int = 800,
    include_ownership: bool = False,
) -> dict[str, Any]:
    return build_query_from_game(
        game,
        request_id=request_id,
        turns=turns,
        visits=visits,
        include_ownership=include_ownership,
        include_policy=True,
    )


def _parse_turns(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _teach_payload(
    command_name: str,
    game: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if command_name == "move":
        turn = int(args.turn)
        analysis, warnings = _analysis_for_turn(
            game,
            turn,
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_move(game, turn=turn, analysis=analysis, student_rank=args.student_rank)
        payload["_warnings"] = warnings
        return payload
    if command_name == "compare":
        analysis, warnings = _analysis_for_turn(
            game,
            int(args.turn),
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_compare(
            game,
            turn=int(args.turn),
            moves=[move.strip() for move in args.moves.split(",") if move.strip()],
            analysis=analysis,
        )
        payload["_warnings"] = warnings
        return payload
    if command_name == "line":
        payload = teach_line(
            game,
            turn=int(args.turn),
            line=[move for move in args.line.split() if move],
        )
        payload["_warnings"] = [_engine_validation_only_warning()]
        return payload
    if command_name == "plan":
        analysis_by_turn, warnings = _analysis_for_game(
            game,
            turns=list(range(1, len(game["moves"]) + 1)),
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_plan(
            game,
            max_moments=int(args.max_moments),
            student_rank=args.student_rank,
            analysis_by_turn=analysis_by_turn,
        )
        payload["_warnings"] = warnings
        return payload
    if command_name == "summary":
        analysis_by_turn, warnings = _analysis_for_game(
            game,
            turns=list(range(1, len(game["moves"]) + 1)),
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_summary(
            game,
            analysis=analysis_by_turn,
            student_rank=args.student_rank,
        )
        payload["_warnings"] = warnings
        return payload
    if command_name == "mistakes":
        analysis_by_turn, warnings = _analysis_for_game(
            game,
            turns=list(range(1, len(game["moves"]) + 1)),
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_mistakes(
            game,
            min_score_loss=float(args.min_score_loss),
            analysis_by_turn=analysis_by_turn,
        )
        payload["_warnings"] = warnings
        return payload
    if command_name == "territory":
        analysis, warnings = _analysis_for_turn(
            game,
            int(args.turn),
            visits=args.visits,
            timeout=args.timeout,
            include_ownership=True,
        )
        payload = teach_territory(game, turn=int(args.turn), analysis=analysis)
        payload["_warnings"] = warnings
        return payload
    if command_name == "quiz":
        analysis, warnings = _analysis_for_turn(
            game,
            int(args.turn),
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_quiz(game, turn=int(args.turn), analysis=analysis)
        payload["_warnings"] = warnings
        return payload
    if command_name == "rank_compare":
        ranks = [rank.strip() for rank in args.ranks.split(",") if rank.strip()]
        analysis, warnings = _rank_compare_analysis(
            game,
            int(args.turn),
            ranks=ranks,
            visits=args.visits,
            timeout=args.timeout,
        )
        payload = teach_rank_compare(
            game,
            turn=int(args.turn),
            ranks=ranks,
            analysis=analysis,
        )
        payload["_warnings"] = warnings
        return payload
    raise ValueError(f"Unsupported teach command: {command_name}")


def _run_analysis(
    query: dict[str, Any],
    *,
    timeout: float,
    top: int,
    raw: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    resolved = resolve_katago_config()
    if not runtime_ready(resolved):
        return None, [_runtime_not_configured_warning()]
    daemon_status = status_daemon()
    if daemon_status.get("running") is True:
        daemon_response = request_daemon(
            {
                "action": "analyze",
                "query": query,
                "timeout": timeout,
            },
            timeout=timeout + 2.0,
        )
        if daemon_response.get("ok") is True:
            data = daemon_response.get("data", {})
            if not isinstance(data, dict) or not isinstance(data.get("responses"), list):
                raise KataGoRuntimeError(
                    ErrorCode.KATAGO_PROTOCOL_ERROR,
                    "Daemon returned malformed analysis data.",
                )
            return normalize_analysis_responses(data["responses"], top_n=top, raw=raw), []
        raise _runtime_error_from_daemon(daemon_response)
    try:
        responses = analyze_query_sync(query, config=resolved, timeout=timeout)
    except KataGoRuntimeError:
        raise
    except Exception as exc:
        return None, [_runtime_warning(exc)]
    return normalize_analysis_responses(responses, top_n=top, raw=raw), []


def _analysis_for_turn(
    game: dict[str, Any],
    turn: int,
    *,
    visits: int,
    timeout: float,
    include_ownership: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    analysis_by_turn, warnings = _analysis_for_game(
        game,
        turns=[turn],
        visits=visits,
        timeout=timeout,
        include_ownership=include_ownership,
    )
    return analysis_by_turn.get(turn), warnings


def _analysis_for_game(
    game: dict[str, Any],
    *,
    turns: list[int],
    visits: int,
    timeout: float,
    include_ownership: bool = False,
    human_sl_profile: str | None = None,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, str]]]:
    query = build_query_from_game(
        game,
        request_id="teach-analysis",
        turns=turns,
        visits=visits,
        include_ownership=include_ownership,
        include_policy=True,
        human_sl_profile=human_sl_profile,
    )
    normalized, warnings = _run_analysis(query, timeout=timeout, top=8, raw=False)
    if normalized is None:
        return {}, warnings
    positions = normalized.get("positions", [])
    if not isinstance(positions, list):
        return {}, warnings
    result: dict[int, dict[str, Any]] = {}
    for item in positions:
        if not isinstance(item, dict):
            continue
        position = item.get("position", {})
        if not isinstance(position, dict):
            continue
        turn = position.get("turn")
        if isinstance(turn, int):
            result[turn] = item
    return result, warnings


def _rank_compare_analysis(
    game: dict[str, Any],
    turn: int,
    *,
    ranks: list[str],
    visits: int,
    timeout: float,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    resolved = resolve_katago_config()
    if not resolved.human_model:
        return None, [
            {
                "code": "HUMAN_POLICY_REQUIRED",
                "message": "Rank compare requires a configured KataGo human SL model.",
            }
        ]

    analysis_turn = max(0, turn - 1)
    played_move = _played_move_for_turn(game, turn)
    best_move, base_warnings = _best_move_for_rank_compare(
        game,
        analysis_turn,
        visits=visits,
        timeout=timeout,
    )
    human_policy: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = list(base_warnings)
    board_size = int(game["board_size"])
    has_human_policy = False
    for rank in ranks:
        analyses, rank_warnings = _analysis_for_game(
            game,
            turns=[analysis_turn],
            visits=visits,
            timeout=timeout,
            human_sl_profile=_human_profile(rank),
        )
        warnings.extend(rank_warnings)
        item = analyses.get(analysis_turn)
        if item is None:
            continue
        policy = _policy_array(item)
        if isinstance(policy, list):
            has_human_policy = True
        human_policy[rank] = {
            "profile": _human_profile(rank),
            "policy_available": isinstance(policy, list),
            "policy_size": len(policy) if isinstance(policy, list) else 0,
            "played_move": played_move,
            "played_policy": _policy_value(policy, played_move, board_size),
            "best_move": best_move,
            "best_policy": _policy_value(policy, best_move, board_size),
        }
    if not human_policy or not has_human_policy:
        return None, warnings or [
            {
                "code": "HUMAN_POLICY_UNAVAILABLE",
                "message": "KataGo did not return humanPolicy for rank comparison.",
            }
        ]
    return {
        "turn": turn,
        "analysis_turn": analysis_turn,
        "played_move": played_move,
        "best_move": best_move,
        "human_policy": human_policy,
    }, warnings


def _best_move_for_rank_compare(
    game: dict[str, Any],
    turn: int,
    *,
    visits: int,
    timeout: float,
) -> tuple[str | None, list[dict[str, str]]]:
    analyses, warnings = _analysis_for_game(
        game,
        turns=[turn],
        visits=visits,
        timeout=timeout,
    )
    item = analyses.get(turn)
    if item is None:
        return None, warnings
    best_moves = item.get("best_moves")
    if not isinstance(best_moves, list) or not best_moves:
        return None, warnings
    best = best_moves[0]
    if not isinstance(best, dict):
        return None, warnings
    move = best.get("move")
    return str(move) if move is not None else None, warnings


def _played_move_for_turn(game: dict[str, Any], turn: int) -> str:
    moves = game.get("moves", [])
    if not isinstance(moves, list) or not 1 <= turn <= len(moves):
        raise ValueError(f"turn must be between 1 and {len(moves)}")
    move = moves[turn - 1]
    if not isinstance(move, dict) or move.get("move") is None:
        raise ValueError(f"missing move for turn {turn}")
    return str(move["move"])


def _policy_array(item: dict[str, Any]) -> list[Any] | None:
    human_policy = item.get("human_policy")
    return human_policy if isinstance(human_policy, list) else None


def _policy_value(
    policy: list[Any] | None,
    move: str | None,
    board_size: int,
) -> float | None:
    if policy is None or move is None:
        return None
    index = _policy_index(move, board_size)
    if index is None or index >= len(policy):
        return None
    value = policy[index]
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _policy_index(move: str, board_size: int) -> int | None:
    if move.lower() == "pass":
        return board_size * board_size
    try:
        x, y_from_bottom = gtp_to_point(move, board_size)
    except ValueError:
        return None
    return (board_size - y_from_bottom - 1) * board_size + x


def _human_profile(rank: str) -> str:
    normalized = rank.strip().lower()
    return normalized if normalized.startswith("rank_") else f"rank_{normalized}"


def _engine_unavailable_warning() -> dict[str, str]:
    return {
        "code": "ENGINE_ANALYSIS_UNAVAILABLE",
        "message": (
            "KataGo engine analysis was not run; payload contains normalized inputs "
            "or heuristic scaffolding only."
        ),
    }


def _runtime_not_configured_warning() -> dict[str, str]:
    return {
        "code": "ENGINE_ANALYSIS_UNAVAILABLE",
        "message": "KataGo binary, model, and config are required for engine-backed analysis.",
    }


def _runtime_warning(exc: Exception) -> dict[str, str]:
    return {
        "code": "ENGINE_ANALYSIS_UNAVAILABLE",
        "message": str(exc),
    }


def _runtime_error_envelope(command: str, exc: KataGoRuntimeError) -> dict[str, Any]:
    return envelope_error(
        command=command,
        code=exc.code,
        message=str(exc),
        **exc.details,
    )


def _runtime_error_from_daemon(response: dict[str, Any]) -> KataGoRuntimeError:
    error = response.get("error", {})
    if not isinstance(error, dict):
        return KataGoRuntimeError(
            ErrorCode.KATAGO_PROTOCOL_ERROR,
            "Daemon returned an error without details.",
        )
    code = str(error.get("code", ErrorCode.KATAGO_PROTOCOL_ERROR.value))
    try:
        error_code = ErrorCode(code)
    except ValueError:
        error_code = ErrorCode.KATAGO_PROTOCOL_ERROR
    return KataGoRuntimeError(
        error_code,
        str(error.get("message", "Daemon analysis failed.")),
    )


def _engine_validation_only_warning() -> dict[str, str]:
    return {
        "code": "ENGINE_VALIDATION_NOT_RUN",
        "message": (
            "The variation passed local validation only; "
            "run KataGo analysis for evaluation."
        ),
    }


def _exit_code(payload: dict[str, Any]) -> int:
    error = payload.get("error")
    if not isinstance(error, dict):
        return 7
    code = error.get("code")
    return {
        ErrorCode.INVALID_INPUT.value: 1,
        ErrorCode.ENGINE_UNAVAILABLE.value: 2,
        ErrorCode.MODEL_NOT_FOUND.value: 3,
        ErrorCode.CONFIG_NOT_FOUND.value: 3,
        ErrorCode.TIMEOUT.value: 4,
        ErrorCode.KATAGO_PROTOCOL_ERROR.value: 5,
        ErrorCode.SGF_PARSE_ERROR.value: 6,
    }.get(str(code), 7)


if __name__ == "__main__":
    raise SystemExit(main())
