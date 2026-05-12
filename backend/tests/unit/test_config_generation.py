from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from app.config import get_settings
from app.config_generation import refresh_generated_config_yaml, refresh_startup_schema_artifacts
from app.rag.schema_introspection import LiveSchemaSnapshot, SchemaInspection


def _make_snapshot() -> LiveSchemaSnapshot:
    return LiveSchemaSnapshot(
        default_database="testdb",
        configured_databases=["testdb"],
        supports_named_schemas=True,
        expose_table_database=True,
        inspections={
            "testdb": SchemaInspection(
                table_names=["orders", "users"],
                columns_by_table={
                    "orders": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "订单ID"},
                        {"name": "user_id", "type": "int", "nullable": False, "default": None, "comment": "用户ID"},
                        {"name": "status", "type": "tinyint", "nullable": False, "default": None, "comment": "1=待支付, 2=已支付"},
                    ],
                    "users": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "用户ID"},
                        {"name": "name", "type": "varchar", "nullable": True, "default": None, "comment": "用户名称"},
                    ],
                },
                primary_keys_by_table={"orders": ["id"], "users": ["id"]},
                foreign_keys=[
                    {
                        "from_database": "testdb",
                        "from_table": "orders",
                        "from_column": "user_id",
                        "to_database": "testdb",
                        "to_table": "users",
                        "to_column": "id",
                    }
                ],
                indexes_by_table={"orders": ["idx_orders_user_id"], "users": []},
                comments_by_table={"orders": "订单主表", "users": "用户表"},
            )
        },
    )


def _make_is_enable_scope_snapshot() -> LiveSchemaSnapshot:
    return LiveSchemaSnapshot(
        default_database="jzjc",
        configured_databases=["jzjc"],
        supports_named_schemas=True,
        expose_table_database=True,
        inspections={
            "jzjc": SchemaInspection(
                table_names=[
                    "jiance_price",
                    "hetong_price",
                    "gongcheng_price",
                    "weituo",
                    "parameter",
                    "conclusion_template",
                ],
                columns_by_table={
                    "jiance_price": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                    ],
                    "hetong_price": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": ""},
                    ],
                    "gongcheng_price": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                    ],
                    "weituo": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                    ],
                    "parameter": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                    ],
                    "conclusion_template": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "主键"},
                        {"name": "is_enable", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                        {"name": "is_enabled", "type": "tinyint", "nullable": False, "default": 1, "comment": None},
                    ],
                },
                primary_keys_by_table={
                    "jiance_price": ["id"],
                    "hetong_price": ["id"],
                    "gongcheng_price": ["id"],
                    "weituo": ["id"],
                    "parameter": ["id"],
                    "conclusion_template": ["id"],
                },
                foreign_keys=[],
                indexes_by_table={
                    "jiance_price": [],
                    "hetong_price": [],
                    "gongcheng_price": [],
                    "weituo": [],
                    "parameter": [],
                    "conclusion_template": [],
                },
                comments_by_table={
                    "jiance_price": "检测价格表",
                    "hetong_price": "合同价格表",
                    "gongcheng_price": "工程价格表",
                    "weituo": "委托表",
                    "parameter": "参数表",
                    "conclusion_template": "结论模板表",
                },
            )
        },
    )


def test_refresh_generated_config_yaml_supports_sqlite_schema_refresh(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "generated-config"))
    get_settings.cache_clear()

    with patch("app.config_generation.inspect_live_schema", new=AsyncMock(return_value=_make_snapshot())), patch(
        "app.config_generation.reload_app_config"
    ) as reload_mock:
        result = __import__("asyncio").run(refresh_generated_config_yaml())

    assert result is True
    reload_mock.assert_called_once()
    assert (tmp_path / "generated-config" / "table_relations.yaml").exists()


def test_refresh_generated_config_yaml_writes_generated_sections_and_preserves_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://user:pass@localhost/testdb")
    monkeypatch.setenv("DATABASE_NAMES", "testdb")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    get_settings.cache_clear()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "table_relations.yaml").write_text(
        yaml.safe_dump(
            {
                "generated": {"relations": []},
                "overrides": {"relations": [{"from_table": "manual.a", "from_column": "x", "to_table": "manual.b", "to_column": "y"}]},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with patch("app.config_generation.inspect_live_schema", new=AsyncMock(return_value=_make_snapshot())), patch(
        "app.config_generation.reload_app_config"
    ) as reload_mock:
        result = __import__("asyncio").run(refresh_generated_config_yaml())

    assert result is True
    reload_mock.assert_called_once()

    table_relations = yaml.safe_load((config_dir / "table_relations.yaml").read_text(encoding="utf-8"))
    field_semantics = yaml.safe_load((config_dir / "field_semantics.yaml").read_text(encoding="utf-8"))
    enum_mappings = yaml.safe_load((config_dir / "enum_mappings.yaml").read_text(encoding="utf-8"))
    business_terms = yaml.safe_load((config_dir / "business_terms.yaml").read_text(encoding="utf-8"))

    assert table_relations["generated"]["relations"][0]["from_table"] == "testdb.orders"
    assert table_relations["generated"]["relations"][0]["to_table"] == "testdb.users"
    assert table_relations["overrides"]["relations"][0]["from_table"] == "manual.a"

    assert "testdb.orders" in field_semantics["generated"]["fields"]
    assert field_semantics["generated"]["fields"]["testdb.orders"]["status"]["value_range"] == "1=待支付, 2=已支付"
    assert field_semantics["generated"]["fields"]["testdb.orders"]["id"]["semantic_role"] == "identifier"

    assert enum_mappings["generated"]["enums"]["testdb.orders.status"]["values"] == {"1": "待支付", "2": "已支付"}

    aliases = [item["alias"] for item in business_terms["generated"]["terms"]]
    assert "订单" in aliases
    assert "用户" in aliases


def test_refresh_generated_config_yaml_avoids_meaningless_rewrite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    get_settings.cache_clear()

    with patch("app.config_generation.inspect_live_schema", new=AsyncMock(return_value=_make_snapshot())), patch(
        "app.config_generation.reload_app_config"
    ):
        first_result = __import__("asyncio").run(refresh_generated_config_yaml())
        table_relations_path = tmp_path / "config" / "table_relations.yaml"
        first_content = table_relations_path.read_text(encoding="utf-8")
        second_result = __import__("asyncio").run(refresh_generated_config_yaml())
        second_content = table_relations_path.read_text(encoding="utf-8")

    assert first_result is True
    assert second_result is False
    assert first_content == second_content


def test_refresh_generated_config_yaml_adds_scoped_is_enable_enum_mappings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://user:pass@localhost/jzjc")
    monkeypatch.setenv("DATABASE_NAMES", "jzjc")
    monkeypatch.setenv(
        "SCHEMA_INCLUDE_TABLES",
        ",".join(
            [
                "jzjc.jiance_price",
                "jzjc.hetong_price",
                "jzjc.gongcheng_price",
                "jzjc.weituo",
            ]
        ),
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    get_settings.cache_clear()

    with patch("app.config_generation.inspect_live_schema", new=AsyncMock(return_value=_make_is_enable_scope_snapshot())), patch(
        "app.config_generation.reload_app_config"
    ):
        result = __import__("asyncio").run(refresh_generated_config_yaml())

    assert result is True

    enum_mappings = yaml.safe_load((tmp_path / "config" / "enum_mappings.yaml").read_text(encoding="utf-8"))
    generated_enums = enum_mappings["generated"]["enums"]

    expected_tables = {
        "jzjc.jiance_price.is_enable",
        "jzjc.hetong_price.is_enable",
        "jzjc.gongcheng_price.is_enable",
        "jzjc.weituo.is_enable",
    }
    assert expected_tables.issubset(generated_enums.keys())
    for key in expected_tables:
        assert generated_enums[key]["values"] == {"0": "不启用", "1": "启用"}

    assert "jzjc.parameter.is_enable" not in generated_enums
    assert "jzjc.conclusion_template.is_enable" not in generated_enums
    assert "jzjc.conclusion_template.is_enabled" not in generated_enums


def test_refresh_startup_schema_artifacts_uses_single_snapshot_for_config_and_semantics(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    get_settings.cache_clear()

    snapshot = _make_snapshot()
    with patch("app.config_generation.inspect_live_schema", new=AsyncMock(return_value=snapshot)) as inspect_mock, patch(
        "app.config_generation.reload_app_config"
    ), patch("app.config_generation.sync_schema_metadata", new=AsyncMock()) as sync_mock:
        result = __import__("asyncio").run(refresh_startup_schema_artifacts())

    assert result is True
    inspect_mock.assert_awaited_once()
    sync_mock.assert_awaited_once_with(snapshot=snapshot, yaml_enabled_override=True)
