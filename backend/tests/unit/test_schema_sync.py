import pytest

from app.rag.schema_sync import _schema_include_tables_by_database, _table_names_for_schema, sync_schema_metadata


def test_schema_include_tables_are_grouped_by_database_and_deduped() -> None:
    grouped = _schema_include_tables_by_database(
        [
            "jc_experimental.weituo_clearing_detail",
            "jc_experimental.weituo",
            "JC_EXPERIMENTAL.WEITUO",
            "weituo_settle_bill",
            "unknown_db.ignored_table",
            "invalid.too.many.parts",
        ],
        ["jc_experimental", "jc_config"],
    )

    assert grouped == {
        "jc_experimental": ["weituo_clearing_detail", "weituo", "weituo_settle_bill"],
        "jc_config": [],
    }


def test_empty_schema_include_tables_disables_filter() -> None:
    assert _schema_include_tables_by_database([], ["jc_experimental"]) is None


def test_schema_sync_does_not_scan_all_tables_when_include_tables_are_configured() -> None:
    class FakeInspector:
        def get_table_names(self, **kwargs):
            raise AssertionError("get_table_names should not be called when whitelist is configured")

    table_names = _table_names_for_schema(
        FakeInspector(),
        "jc_experimental",
        ["weituo_clearing_detail", "weituo"],
    )

    assert table_names == ["weituo_clearing_detail", "weituo"]


def test_schema_sync_scans_all_tables_when_include_tables_are_empty() -> None:
    class FakeInspector:
        def get_table_names(self, **kwargs):
            return ["weituo", "weituo_clearing_detail"]

    table_names = _table_names_for_schema(FakeInspector(), "jc_experimental", None)

    assert table_names == ["weituo", "weituo_clearing_detail"]


@pytest.mark.anyio
async def test_schema_sync_respects_configured_jc_experimental_whitelist() -> None:
    catalog = await sync_schema_metadata()

    table_names = [table.name for table in catalog.tables]
    assert catalog.database == "jc_experimental"
    assert table_names == [
        "weituo_clearing_detail",
        "weituo",
        "weituo_clearing_bill",
        "weituo_settle_bill",
    ]
    assert all(table.database == "jc_experimental" for table in catalog.tables)

    weituo_table = next(table for table in catalog.tables if table.name == "weituo")
    assert weituo_table.columns
