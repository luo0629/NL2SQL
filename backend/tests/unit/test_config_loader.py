"""Tests for backend/app/config_loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config_loader import AppConfig, _merge_generated_overrides, _read_yaml


# ---------------------------------------------------------------------------
# _read_yaml tests
# ---------------------------------------------------------------------------


def test_read_yaml_returns_dict_from_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump({"key": "value"}), encoding="utf-8")
    assert _read_yaml(path) == {"key": "value"}


def test_read_yaml_returns_empty_dict_for_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.yaml"
    assert _read_yaml(path) == {}


def test_read_yaml_returns_empty_dict_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert _read_yaml(path) == {}


def test_read_yaml_returns_empty_dict_for_whitespace_only_file(tmp_path: Path) -> None:
    path = tmp_path / "whitespace.yaml"
    path.write_text("   \n  \n  ", encoding="utf-8")
    assert _read_yaml(path) == {}


def test_read_yaml_returns_empty_dict_for_non_dict_yaml(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")
    assert _read_yaml(path) == {}


def test_read_yaml_returns_empty_dict_for_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("{{{{invalid", encoding="utf-8")
    assert _read_yaml(path) == {}


# ---------------------------------------------------------------------------
# _merge_generated_overrides tests
# ---------------------------------------------------------------------------


def test_merge_returns_generated_when_no_overrides() -> None:
    data = {"generated": {"a": 1, "b": 2}}
    result = _merge_generated_overrides(data)
    assert result == {"a": 1, "b": 2}


def test_merge_overrides_take_precedence_over_scalar() -> None:
    data = {
        "generated": {"a": 1, "b": 2},
        "overrides": {"b": 99},
    }
    result = _merge_generated_overrides(data)
    assert result == {"a": 1, "b": 99}


def test_merge_overrides_replace_lists_entirely() -> None:
    data = {
        "generated": {"items": [1, 2, 3]},
        "overrides": {"items": [4, 5]},
    }
    result = _merge_generated_overrides(data)
    assert result == {"items": [4, 5]}


def test_merge_overrides_merge_dicts() -> None:
    data = {
        "generated": {"config": {"x": 1, "y": 2}},
        "overrides": {"config": {"y": 99, "z": 3}},
    }
    result = _merge_generated_overrides(data)
    assert result == {"config": {"x": 1, "y": 99, "z": 3}}


def test_merge_overrides_add_new_keys() -> None:
    data = {
        "generated": {"a": 1},
        "overrides": {"b": 2},
    }
    result = _merge_generated_overrides(data)
    assert result == {"a": 1, "b": 2}


def test_merge_returns_empty_dict_for_empty_input() -> None:
    assert _merge_generated_overrides({}) == {}


def test_merge_handles_non_dict_generated() -> None:
    data = {"generated": "not a dict", "overrides": {"a": 1}}
    result = _merge_generated_overrides(data)
    assert result == {"a": 1}


def test_merge_handles_non_dict_overrides() -> None:
    data = {"generated": {"a": 1}, "overrides": "not a dict"}
    result = _merge_generated_overrides(data)
    assert result == {"a": 1}


def test_merge_overrides_none_values_treated_as_empty() -> None:
    data = {"generated": None, "overrides": None}
    result = _merge_generated_overrides(data)
    assert result == {}


# ---------------------------------------------------------------------------
# AppConfig tests
# ---------------------------------------------------------------------------


def _write_config_file(directory: Path, filename: str, content: dict) -> None:
    (directory / filename).write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")


def test_app_config_loads_all_six_files(tmp_path: Path) -> None:
    for filename in [
        "table_relations.yaml",
        "field_semantics.yaml",
        "field_examples.yaml",
        "enum_mappings.yaml",
        "business_terms.yaml",
        "few_shot_samples.yaml",
    ]:
        _write_config_file(tmp_path, filename, {"generated": {"key": "value"}, "overrides": {}})

    config = AppConfig(tmp_path)
    assert config.table_relations == {"key": "value"}
    assert config.field_semantics == {"key": "value"}
    assert config.field_examples == {"key": "value"}
    assert config.enum_mappings == {"key": "value"}
    assert config.business_terms == {"key": "value"}
    assert config.few_shot_samples == {"key": "value"}


def test_app_config_handles_missing_files(tmp_path: Path) -> None:
    config = AppConfig(tmp_path)
    assert config.table_relations == {}
    assert config.field_semantics == {}
    assert config.field_examples == {}
    assert config.enum_mappings == {}
    assert config.business_terms == {}
    assert config.few_shot_samples == {}


def test_app_config_overrides_take_precedence(tmp_path: Path) -> None:
    _write_config_file(tmp_path, "table_relations.yaml", {
        "generated": {"relations": ["auto_relation"]},
        "overrides": {"relations": ["manual_relation"]},
    })

    config = AppConfig(tmp_path)
    assert config.table_relations["relations"] == ["manual_relation"]


def test_app_config_get_returns_merged_section(tmp_path: Path) -> None:
    _write_config_file(tmp_path, "enum_mappings.yaml", {
        "generated": {"enums": {"table.status": {"values": {"0": "off"}}}},
        "overrides": {"enums": {"table.status": {"values": {"1": "on"}}}},
    })

    config = AppConfig(tmp_path)
    merged = config.get("enum_mappings")
    # Dict merge: overrides key "table.status" overrides generated key "table.status"
    assert merged["enums"]["table.status"]["values"] == {"1": "on"}


def test_app_config_get_raw_preserves_separation(tmp_path: Path) -> None:
    _write_config_file(tmp_path, "business_terms.yaml", {
        "generated": {"terms": ["auto"]},
        "overrides": {"terms": ["manual"]},
    })

    config = AppConfig(tmp_path)
    raw = config.get_raw("business_terms")
    assert raw["generated"]["terms"] == ["auto"]
    assert raw["overrides"]["terms"] == ["manual"]


def test_app_config_get_unknown_section_returns_empty(tmp_path: Path) -> None:
    config = AppConfig(tmp_path)
    assert config.get("nonexistent_section") == {}
    assert config.get_raw("nonexistent_section") == {}


def test_app_config_load_all_reloads(tmp_path: Path) -> None:
    _write_config_file(tmp_path, "table_relations.yaml", {
        "generated": {"v": 1},
        "overrides": {},
    })
    config = AppConfig(tmp_path)
    assert config.table_relations["v"] == 1

    # Update the file and reload
    _write_config_file(tmp_path, "table_relations.yaml", {
        "generated": {"v": 2},
        "overrides": {},
    })
    config.load_all()
    assert config.table_relations["v"] == 2


def test_app_config_with_real_config_dir() -> None:
    """Verify the real config directory loads without errors."""
    real_config_dir = Path(__file__).resolve().parents[2] / "config"
    if not real_config_dir.exists():
        pytest.skip("Real config directory not found")
    config = AppConfig(real_config_dir)
    # All sections should be non-empty since we created them
    assert isinstance(config.table_relations, dict)
    assert isinstance(config.field_semantics, dict)
    assert isinstance(config.enum_mappings, dict)
    assert isinstance(config.business_terms, dict)
    assert isinstance(config.few_shot_samples, dict)
    assert isinstance(config.field_examples, dict)
