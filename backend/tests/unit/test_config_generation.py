from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from app.config import get_settings
from app.config_generation import refresh_generated_config_yaml
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


def test_refresh_generated_config_yaml_skips_non_mysql_driver(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "generated-config"))
    get_settings.cache_clear()

    result = __import__("asyncio").run(refresh_generated_config_yaml())

    assert result is False
    assert not (tmp_path / "generated-config" / "table_relations.yaml").exists()


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
