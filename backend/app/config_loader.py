"""Config loader -- 统一加载 backend/config/ 下的所有 YAML 配置文件。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)

_CONFIG_FILENAMES = (
    "table_relations.yaml",
    "field_semantics.yaml",
    "field_examples.yaml",
    "enum_mappings.yaml",
    "business_terms.yaml",
    "few_shot_samples.yaml",
    "agent_strategy.yaml",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file, return empty dict on missing/error."""
    if not path.exists():
        logger.warning("Config file not found: %s", path)
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        logger.exception("Failed to load config: %s", path)
        return {}


def _merge_generated_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Merge generated (defaults) with overrides (manual edits). Overrides take precedence."""
    generated = data.get("generated") or {}
    overrides = data.get("overrides") or {}
    if not isinstance(generated, dict):
        generated = {}
    if not isinstance(overrides, dict):
        overrides = {}
    # Deep merge: overrides override generated at the key level
    merged = {**generated}
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], list) and isinstance(value, list):
            if value:  # Only override if the override list is non-empty
                merged[key] = value
        elif key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}  # Merge dicts
        else:
            merged[key] = value
    return merged


class AppConfig:
    """Unified application configuration loaded from YAML files."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._raw: dict[str, dict[str, Any]] = {}
        self._merged: dict[str, dict[str, Any]] = {}
        self.load_all()

    def load_all(self) -> None:
        """Load and merge all config files."""
        for filename in _CONFIG_FILENAMES:
            path = self._config_dir / filename
            key = filename.removesuffix(".yaml")
            raw = _read_yaml(path)
            self._raw[key] = raw
            self._merged[key] = _merge_generated_overrides(raw)
        logger.info("Loaded %d config files from %s", len(self._merged), self._config_dir)

    def get(self, section: str) -> dict[str, Any]:
        """Get merged config for a section (e.g., 'field_semantics')."""
        return self._merged.get(section, {})

    def get_raw(self, section: str) -> dict[str, Any]:
        """Get raw config (with generated/overrides separation) for a section."""
        return self._raw.get(section, {})

    @property
    def table_relations(self) -> dict[str, Any]:
        return self.get("table_relations")

    @property
    def field_semantics(self) -> dict[str, Any]:
        return self.get("field_semantics")

    @property
    def field_examples(self) -> dict[str, Any]:
        return self.get("field_examples")

    @property
    def enum_mappings(self) -> dict[str, Any]:
        return self.get("enum_mappings")

    @property
    def business_terms(self) -> dict[str, Any]:
        return self.get("business_terms")

    @property
    def few_shot_samples(self) -> dict[str, Any]:
        return self.get("few_shot_samples")

    @property
    def agent_strategy(self) -> dict[str, Any]:
        return self.get("agent_strategy")


_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Get or create the module-level AppConfig singleton."""
    global _app_config
    if _app_config is None:
        settings = get_settings()
        _app_config = AppConfig(Path(settings.config_dir))
    return _app_config


def reload_app_config() -> AppConfig:
    """Force reload all config files."""
    global _app_config
    settings = get_settings()
    _app_config = AppConfig(Path(settings.config_dir))
    return _app_config
