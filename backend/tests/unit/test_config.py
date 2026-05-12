from pathlib import Path

from app.config import Settings


def test_settings_parses_database_names_and_schema_scope_key() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://user:pass@127.0.0.1:3306?charset=utf8mb4",
        database_names="jc_config, jc_experimental",
    )

    assert settings.database_names == ["jc_config", "jc_experimental"]
    assert settings.effective_database_names == ["jc_config", "jc_experimental"]
    assert settings.schema_scope_key == (
        "mysql+asyncmy://user:***@127.0.0.1:3306?charset=utf8mb4"
        "|databases=jc_config,jc_experimental|tables="
    )


def test_settings_schema_scope_ignores_url_database_when_database_names_are_set() -> None:
    without_url_database = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://user:pass@127.0.0.1:3306?charset=utf8mb4",
        database_names="jc_config, jc_experimental",
    )
    with_url_database = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://user:pass@127.0.0.1:3306/jc_config?charset=utf8mb4",
        database_names="jc_config, jc_experimental",
    )

    assert without_url_database.effective_database_names == ["jc_config", "jc_experimental"]
    assert with_url_database.effective_database_names == ["jc_config", "jc_experimental"]
    assert without_url_database.schema_scope_key == with_url_database.schema_scope_key


def test_settings_falls_back_to_database_url_database() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://user:pass@127.0.0.1:3306/jc_config",
    )

    assert settings.database_names == []
    assert settings.effective_database_names == ["jc_config"]
    assert settings.schema_scope_key == "mysql+asyncmy://user:***@127.0.0.1:3306/jc_config|databases=jc_config|tables="


def test_settings_parses_and_dedupes_schema_include_tables() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://user:pass@127.0.0.1:3306?charset=utf8mb4",
        database_names="jc_experimental",
        schema_include_tables=(
            "jc_experimental.weituo, jc_experimental.weituo_clearing_detail, "
            "JC_EXPERIMENTAL.WEITUO, `jc_experimental.weituo_settle_bill`"
        ),
    )

    assert settings.schema_include_tables == [
        "jc_experimental.weituo",
        "jc_experimental.weituo_clearing_detail",
        "JC_EXPERIMENTAL.WEITUO",
        "`jc_experimental.weituo_settle_bill`",
    ]
    assert settings.effective_schema_include_tables == [
        "jc_experimental.weituo",
        "jc_experimental.weituo_clearing_detail",
        "jc_experimental.weituo_settle_bill",
    ]
    assert settings.schema_scope_key == (
        "mysql+asyncmy://user:***@127.0.0.1:3306?charset=utf8mb4"
        "|databases=jc_experimental"
        "|tables=jc_experimental.weituo,jc_experimental.weituo_clearing_detail,jc_experimental.weituo_settle_bill"
    )



def test_env_example_uses_generic_database_placeholders() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    content = env_example.read_text(encoding="utf-8")

    assert "DATABASE_NAMES=your_database" in content
    assert "SCHEMA_INCLUDE_TABLES=your_database.your_table_a,your_database.your_table_b" in content
    assert "jc_experimental" not in content
    assert "jc_config" not in content
