from app.rag.value_mapping_loader import merge_column_description


def test_merge_column_description_prefers_db_description() -> None:
    assert (
        merge_column_description(
            db_description="是否上架",
            fallback_mapping="1=起售,0=停售",
        )
        == "是否上架 | values: 1=起售,0=停售"
    )


def test_merge_column_description_falls_back_when_db_missing() -> None:
    assert (
        merge_column_description(
            db_description=None,
            fallback_mapping="1=起售,0=停售",
        )
        == "1=起售,0=停售"
    )


def test_merge_column_description_returns_none_when_both_missing() -> None:
    assert (
        merge_column_description(
            db_description="   ",
            fallback_mapping=None,
        )
        is None
    )

