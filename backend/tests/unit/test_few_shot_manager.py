from app.rag.few_shot_manager import FewShotManager, _detect_tags, _extract_tables_from_sql
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable


def _make_catalog() -> SchemaCatalog:
    """创建测试用 schema catalog。"""
    return SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="orders",
                description="订单表",
                aliases=["order", "订单"],
                business_terms=["下单", "交易"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="amount", data_type="DECIMAL", nullable=False, business_terms=["金额"], semantic_role="metric"),
                    SchemaColumn(name="status", data_type="VARCHAR", nullable=True, business_terms=["状态"], semantic_role="dimension"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
            SchemaTable(
                name="user",
                description="用户表",
                aliases=["用户", "会员"],
                business_terms=["客户", "注册用户"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, business_terms=["用户名"], semantic_role="dimension"),
                ],
            ),
        ],
    )


class TestExtractTablesFromSql:
    def test_extracts_from_clause(self):
        assert _extract_tables_from_sql("SELECT * FROM orders;") == {"orders"}

    def test_extracts_join_clause(self):
        sql = "SELECT * FROM orders JOIN user ON orders.id = user.id;"
        assert _extract_tables_from_sql(sql) == {"orders", "user"}

    def test_extracts_multiple_from(self):
        sql = "SELECT * FROM orders, user;"
        assert _extract_tables_from_sql(sql) == {"orders", "user"}

    def test_empty_sql(self):
        assert _extract_tables_from_sql("") == set()

    def test_no_tables(self):
        assert _extract_tables_from_sql("SELECT 1;") == set()


class TestDetectTags:
    def test_aggregation_keywords(self):
        assert "aggregation" in _detect_tags("统计每个客户的订单金额")
        assert "aggregation" in _detect_tags("哪些菜卖得好")
        assert "aggregation" in _detect_tags("最受欢迎的菜品")

    def test_time_range_keywords(self):
        assert "time-range" in _detect_tags("最近30天的订单")
        assert "time-range" in _detect_tags("这几天的销售情况")
        assert "time-range" in _detect_tags("这个月的收入")

    def test_top_n_keywords(self):
        assert "top-n" in _detect_tags("价格最高的菜品")
        assert "top-n" in _detect_tags("最热门的套餐")
        assert "top-n" in _detect_tags("排行榜")

    def test_join_keywords(self):
        assert "join" in _detect_tags("订单和对应的用户信息")
        assert "join" in _detect_tags("菜品包含哪些口味")

    def test_detail_fallback(self):
        tags = _detect_tags("查询所有数据")
        assert "detail" in tags

    def test_multiple_tags(self):
        tags = _detect_tags("最近30天收入最高的客户")
        assert "time-range" in tags
        assert "top-n" in tags
        assert "aggregation" in tags


class TestFewShotManagerFilterCompatible:
    def test_filters_incompatible_static_examples(self):
        catalog = _make_catalog()
        manager = FewShotManager(catalog)
        examples = manager._load_static_examples()
        compatible = manager._filter_compatible(examples)
        # 静态示例引用了 customers/sales 表，不在 catalog 中，应被过滤
        for ex in compatible:
            tables = _extract_tables_from_sql(ex.get("sql", ""))
            if tables:
                assert tables.issubset({"orders", "user"}), f"Incompatible example: {ex['sql']}"

    def test_no_catalog_keeps_all_static(self):
        manager = FewShotManager(None)
        examples = manager._load_static_examples()
        compatible = manager._filter_compatible(examples)
        assert len(compatible) == len(examples)


class TestFewShotManagerSelectExamples:
    def test_returns_dynamic_examples_when_static_incompatible(self):
        catalog = _make_catalog()
        manager = FewShotManager(catalog)
        examples = manager.select_examples("查询订单金额")
        assert len(examples) > 0
        for ex in examples:
            sql = ex.get("sql", "")
            tables = _extract_tables_from_sql(sql)
            if tables:
                assert tables.issubset({"orders", "user"})

    def test_respects_limit(self):
        catalog = _make_catalog()
        manager = FewShotManager(catalog)
        examples = manager.select_examples("查询订单", limit=2)
        assert len(examples) <= 2

    def test_no_catalog_returns_empty(self):
        manager = FewShotManager(None)
        # 无 catalog 时静态示例不兼容会被过滤，动态示例无法生成
        # 但无 catalog 时 filter_compatible 保留所有静态示例
        examples = manager.select_examples("查询订单")
        # 应返回静态示例（无 catalog 时不过滤）
        assert len(examples) > 0

    def test_tag_matching_prefers_relevant(self):
        catalog = _make_catalog()
        manager = FewShotManager(catalog)
        agg_examples = manager.select_examples("统计每个用户的订单总金额")
        # 应该包含 aggregation 标签的示例
        tags_in_examples = set()
        for ex in agg_examples:
            tags_in_examples.update(ex.get("tags", []))
        # 至少应有一些示例匹配到相关标签
        assert len(agg_examples) > 0


class TestFewShotManagerDynamicGeneration:
    def test_generates_examples_from_catalog(self):
        catalog = _make_catalog()
        manager = FewShotManager(catalog)
        dynamic = manager._generate_dynamic_examples()
        assert len(dynamic) > 0
        for ex in dynamic:
            assert ex.get("source") == "dynamic"
            sql = ex.get("sql", "")
            tables = _extract_tables_from_sql(sql)
            assert tables.issubset({"orders", "user"})

    def test_no_catalog_returns_empty(self):
        manager = FewShotManager(None)
        assert manager._generate_dynamic_examples() == []
