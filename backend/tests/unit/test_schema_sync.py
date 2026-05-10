import pytest

from app.rag.schema_models import SchemaColumn, SchemaTable
from app.rag.schema_sync import (
    _infer_relations_from_shared_columns,
    _infer_relations_from_shared_columns_with_probes,
    _schema_include_tables_by_database,
    _table_names_for_schema,
    sync_schema_metadata,
)


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


def test_infer_relations_from_shared_columns_prefers_business_keys_and_skips_audit_fields() -> None:
    relations = _infer_relations_from_shared_columns(
        [
            SchemaTable(
                name="orders",
                database="testdb",
                columns=[
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                    SchemaColumn(name="create_time", data_type="TIMESTAMP", nullable=True, description="创建时间", semantic_role="internal"),
                ],
            ),
            SchemaTable(
                name="payments",
                database="testdb",
                columns=[
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                    SchemaColumn(name="create_time", data_type="TIMESTAMP", nullable=True, description="创建时间", semantic_role="internal"),
                ],
            ),
        ]
    )

    assert any(
        relation.from_column == "order_no" and relation.to_column == "order_no"
        for relation in relations
    )
    assert all(
        relation.from_column != "create_time" and relation.to_column != "create_time"
        for relation in relations
    )


@pytest.mark.anyio
async def test_inferred_relation_probes_promote_high_overlap_business_keys() -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.samples = {
                ("testdb.orders", "order_no"): ["O1", "O2", "O3", "O4"],
                ("testdb.payments", "order_no"): ["O1", "O2", "O3", "O5"],
                ("testdb.orders", "trace_no"): ["TMP1", None, None, "TMP2"],
                ("testdb.payments", "trace_no"): ["TMP1", "TMP1", "TMP1", None],
            }
            self.calls: list[tuple[str, str]] = []

        async def sample_column_values(self, table: str, column: str, **_: object) -> list[object]:
            self.calls.append((table, column))
            return self.samples[(table, column)]

    relations = await _infer_relations_from_shared_columns_with_probes(
        [
            SchemaTable(
                name="orders",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension"),
                    SchemaColumn(name="trace_no", data_type="VARCHAR", nullable=True, description="trace number", semantic_role="dimension"),
                ],
            ),
            SchemaTable(
                name="payments",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension"),
                    SchemaColumn(name="trace_no", data_type="VARCHAR", nullable=True, description="trace number", semantic_role="dimension"),
                ],
            ),
        ],
        FakeExecutor(),
        probe_enabled=True,
        probe_top_k=4,
        probe_sample_limit=4,
        probe_timeout_seconds=1.0,
    )

    by_column = {relation.from_column: relation for relation in relations}
    assert by_column["order_no"].ranking_score is not None
    assert by_column["trace_no"].ranking_score is not None
    assert by_column["order_no"].ranking_score > by_column["trace_no"].ranking_score
    assert by_column["order_no"].validation_summary is not None
    assert "sample_probe" in by_column["order_no"].validation_summary
    assert by_column["trace_no"].confidence in {"low", "medium"}
    assert "轻量验证" in (by_column["order_no"].join_hint or "")


@pytest.mark.anyio
async def test_inferred_relation_probes_respect_top_k_budget() -> None:
    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def sample_column_values(self, table: str, column: str, **_: object) -> list[object]:
            self.calls += 1
            return [f"{table}-{column}-1", f"{table}-{column}-2"]

    executor = CountingExecutor()
    await _infer_relations_from_shared_columns_with_probes(
        [
            SchemaTable(
                name="orders",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                    SchemaColumn(name="invoice_no", data_type="VARCHAR", nullable=False, description="发票编号", semantic_role="dimension"),
                    SchemaColumn(name="shipment_no", data_type="VARCHAR", nullable=False, description="物流单号", semantic_role="dimension"),
                ],
            ),
            SchemaTable(
                name="payments",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                    SchemaColumn(name="invoice_no", data_type="VARCHAR", nullable=False, description="发票编号", semantic_role="dimension"),
                    SchemaColumn(name="shipment_no", data_type="VARCHAR", nullable=False, description="物流单号", semantic_role="dimension"),
                ],
            ),
        ],
        executor,
        probe_enabled=True,
        probe_top_k=1,
        probe_sample_limit=4,
        probe_timeout_seconds=1.0,
    )

    assert executor.calls == 2


@pytest.mark.anyio
async def test_inferred_relation_probes_single_candidate_pairs_too() -> None:
    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def sample_column_values(self, table: str, column: str, **_: object) -> list[object]:
            self.calls += 1
            return ["A001", "A002"]

    executor = CountingExecutor()
    relations = await _infer_relations_from_shared_columns_with_probes(
        [
            SchemaTable(
                name="orders",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                ],
            ),
            SchemaTable(
                name="payments",
                database="testdb",
                primary_keys=["id"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                ],
            ),
        ],
        executor,
        probe_enabled=True,
        probe_top_k=1,
        probe_sample_limit=4,
        probe_timeout_seconds=1.0,
    )

    assert executor.calls == 2
    assert relations[0].validation_summary is not None
    assert "sample_probe" in relations[0].validation_summary
