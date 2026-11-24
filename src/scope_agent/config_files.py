from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from scope_agent.domain.areas import AreaMap, AreaRule
from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.routing import RoutingConfig


class ConfigFileError(Exception):
    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigFileError(path, str(exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigFileError(path, "top level must be a mapping")
    return data


def load_routing(path: Path) -> RoutingConfig:
    data = _read_mapping(path)
    try:
        return RoutingConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigFileError(path, str(exc)) from exc


def load_dependency_hints(path: Path) -> DependencyHints:
    data = _read_mapping(path)
    try:
        return DependencyHints.model_validate(data)
    except ValidationError as exc:
        raise ConfigFileError(path, str(exc)) from exc


def load_areas(path: Path) -> AreaMap:
    data = _read_mapping(path)
    areas = data.get("areas", {})
    if not isinstance(areas, dict) or not all(isinstance(v, dict) for v in areas.values()):
        raise ConfigFileError(path, "areas must map repo -> {glob: area name}")
    try:
        rules = [
            AreaRule(repo=str(repo), glob=str(glob), area=area)
            for repo, globs in areas.items()
            for glob, area in globs.items()
        ]
        return AreaMap(rules=rules)
    except ValidationError as exc:
        raise ConfigFileError(path, str(exc)) from exc
