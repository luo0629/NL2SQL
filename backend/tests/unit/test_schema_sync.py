import pytest

from app.rag.schema_sync import sync_schema_metadata


@pytest.mark.anyio
async def test_schema_sync_covers_all_sky_take_out_tables() -> None:
    catalog = await sync_schema_metadata()

    table_names = {table.name for table in catalog.tables}
    assert catalog.database == "sky_take_out"
    assert table_names == {
        "address_book",
        "category",
        "dish",
        "dish_flavor",
        "employee",
        "order_detail",
        "orders",
        "setmeal",
        "setmeal_dish",
        "shopping_cart",
        "user",
    }

    category_table = next(table for table in catalog.tables if table.name == "category")
    assert category_table.primary_keys == ["id"]
    assert any(column.name == "name" for column in category_table.columns)
    assert catalog.relations
