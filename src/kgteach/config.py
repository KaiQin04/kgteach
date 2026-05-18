"""KataGo adapter configuration resolution."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

DEFAULT_BINARY: Final = "katago"
ENV_PREFIX: Final = "KGTEACH_KATAGO_"
DISABLE_GOREVIEW_DISCOVERY_ENV: Final = "KGTEACH_DISABLE_GOREVIEW_DISCOVERY"
DISABLE_LOCAL_APP_DISCOVERY_ENV: Final = "KGTEACH_DISABLE_LOCAL_APP_DISCOVERY"
PROJECT_CONFIG_PATH: Final = Path(".kgteach") / "katago.json"
USER_CONFIG_PATH: Final = Path(".config") / "kgteach" / "katago.json"
GOREVIEW_BINARY_PATH: Final = Path(
    "Library/Application Support/GoReview/engines/katago/katago"
)
GOREVIEW_MODELS_PATH: Final = Path("Library/Application Support/GoReview/models")
GOREVIEW_CONFIG_PATH: Final = Path(
    "Library/Application Support/GoReview/KataGo/analysis-b10c128.cfg"
)

_UNSET: Final = object()
_FIELDS: Final = ("binary", "model", "config", "human_model")
_ENV_NAMES: Final = {
    "binary": f"{ENV_PREFIX}BINARY",
    "model": f"{ENV_PREFIX}MODEL",
    "config": f"{ENV_PREFIX}CONFIG",
    "human_model": f"{ENV_PREFIX}HUMAN_MODEL",
}


@dataclass(frozen=True, slots=True)
class KataGoConfig:
    """Resolved filesystem configuration for launching KataGo."""

    binary: str = DEFAULT_BINARY
    model: str | None = None
    config: str | None = None
    human_model: str | None = None


def resolve_katago_config(
    *,
    binary: str | None = None,
    model: str | None = None,
    config: str | None = None,
    human_model: str | None = None,
    env: Mapping[str, str] | None = None,
    project_file: str | Path | None | object = _UNSET,
    user_file: str | Path | None | object = _UNSET,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> KataGoConfig:
    """Resolve KataGo config from explicit args, env, project, user, and defaults.

    Precedence is, from highest to lowest: explicit keyword arguments, environment
    variables, project config, user config, then built-in defaults. Passing
    ``project_file=None`` or ``user_file=None`` disables that file source.
    """

    environ = os.environ if env is None else env
    project_path = _default_project_file(cwd) if project_file is _UNSET else project_file
    user_path = _default_user_file(home) if user_file is _UNSET else user_file

    merged: dict[str, str | None] = {
        "binary": DEFAULT_BINARY,
        "model": None,
        "config": None,
        "human_model": None,
    }
    local_app_discovery_disabled = (
        environ.get(DISABLE_LOCAL_APP_DISCOVERY_ENV) == "1"
        or environ.get(DISABLE_GOREVIEW_DISCOVERY_ENV) == "1"
    )
    if not local_app_discovery_disabled:
        merged.update(_goreview_config(home))
    merged.update(_load_config_file(user_path))
    merged.update(_load_config_file(project_path))
    merged.update(_env_config(environ))
    merged.update(
        _compact(
            {
                "binary": binary,
                "model": model,
                "config": config,
                "human_model": human_model,
            }
        )
    )

    return KataGoConfig(
        binary=str(merged["binary"] or DEFAULT_BINARY),
        model=_optional_str(merged["model"]),
        config=_optional_str(merged["config"]),
        human_model=_optional_str(merged["human_model"]),
    )


def _default_project_file(cwd: str | Path | None) -> Path:
    root = Path.cwd() if cwd is None else Path(cwd)
    return root / PROJECT_CONFIG_PATH


def _default_user_file(home: str | Path | None) -> Path:
    root = Path.home() if home is None else Path(home)
    return root / USER_CONFIG_PATH


def _goreview_config(home: str | Path | None) -> dict[str, str]:
    root = Path.home() if home is None else Path(home)
    binary = root / GOREVIEW_BINARY_PATH
    config = root / GOREVIEW_CONFIG_PATH
    models_dir = root / GOREVIEW_MODELS_PATH
    discovered: dict[str, str] = {}
    if binary.exists():
        discovered["binary"] = str(binary)
    if config.exists():
        discovered["config"] = str(config)
    model = _first_model(models_dir)
    if model is not None:
        discovered["model"] = str(model)
    human_model = _first_human_model(models_dir)
    if human_model is not None:
        discovered["human_model"] = str(human_model)
    return discovered


def _first_model(models_dir: Path) -> Path | None:
    if not models_dir.exists():
        return None
    models = [model for model in sorted(models_dir.glob("*.bin.gz")) if not _is_human_model(model)]
    if not models:
        return None
    return models[0]


def _first_human_model(models_dir: Path) -> Path | None:
    if not models_dir.exists():
        return None
    models = [model for model in sorted(models_dir.glob("*.bin.gz")) if _is_human_model(model)]
    if not models:
        return None
    return models[0]


def _is_human_model(path: Path) -> bool:
    return "human" in path.name.lower()


def _load_config_file(path: str | Path | None | object) -> dict[str, str]:
    if path is None or path is _UNSET:
        return {}
    if not isinstance(path, str | Path):
        return {}

    config_path = Path(path)
    if not config_path.exists():
        return {}

    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}

    if isinstance(loaded.get("katago"), dict):
        loaded = loaded["katago"]

    return _compact({field: loaded.get(field) for field in _FIELDS})


def _env_config(env: Mapping[str, str]) -> dict[str, str]:
    return _compact({field: env.get(env_name) for field, env_name in _ENV_NAMES.items()})


def _compact(values: Mapping[str, Any]) -> dict[str, str]:
    compacted: dict[str, str] = {}
    for key, value in values.items():
        if key not in _FIELDS or value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            compacted[key] = normalized
    return compacted


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
