from app.agent.nodes import (
    _detect_question_tags,
    _fallback_query_understanding,
    _infer_primary_table,
    _extract_catalog_business_terms,
    build_fallback_sql,
)
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
                    SchemaColumn(name="amount", data_type="DECIMAL", nullable=False, business_terms=["金额", "订单金额"], semantic_role="metric"),
                    SchemaColumn(name="status", data_type="VARCHAR", nullable=True, business_terms=["状态"], semantic_role="dimension"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
            SchemaTable(
                name="dish",
                description="菜品表",
                aliases=["菜品", "商品"],
                business_terms=["菜", "单品"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, business_terms=["菜品名称"], semantic_role="dimension"),
                    SchemaColumn(name="price", data_type="DECIMAL", nullable=False, business_terms=["价格", "售价"], semantic_role="metric"),
                ],
            ),
        ],
    )


class TestDetectQuestionTags:
    def test_aggregation_colloquial(self):
        assert "aggregation" in _detect_question_tags("哪些菜卖得好")
        assert "aggregation" in _detect_question_tags("最受欢迎的菜品是什么")
        assert "aggregation" in _detect_question_tags("最热门的套餐")
        assert "aggregation" in _detect_question_tags("销量最高的商品")
        assert "aggregation" in _detect_question_tags("有多少个订单")

    def test_aggregation_technical(self):
        assert "aggregation" in _detect_question_tags("统计订单总金额")
        assert "aggregation" in _detect_question_tags("汇总每个客户的消费")

    def test_time_range_colloquial(self):
        assert "time-range" in _detect_question_tags("这几天的订单")
        assert "time-range" in _detect_question_tags("这个月的销售情况")
        assert "time-range" in _detect_question_tags("近期有什么热门菜品")

    def test_time_range_technical(self):
        assert "time-range" in _detect_question_tags("最近30天的订单")
        assert "time-range" in _detect_question_tags("今天的收入")

    def test_top_n_colloquial(self):
        assert "top-n" in _detect_question_tags("最贵的菜品")
        assert "top-n" in _detect_question_tags("最便宜的套餐")
        assert "top-n" in _detect_question_tags("最好吃的菜")
        assert "top-n" in _detect_question_tags("排行榜")

    def test_top_n_technical(self):
        assert "top-n" in _detect_question_tags("价格最高的前10个菜品")
        assert "top-n" in _detect_question_tags("排名前5的订单")

    def test_join_colloquial(self):
        assert "join" in _detect_question_tags("订单属于哪个用户")
        assert "join" in _detect_question_tags("菜品包含哪些口味")
        assert "join" in _detect_question_tags("订单和对应的用户信息")

    def test_join_technical(self):
        assert "join" in _detect_question_tags("关联查询订单和用户")
        assert "join" in _detect_question_tags("同时查看菜品和分类")

    def test_detail_fallback(self):
        tags = _detect_question_tags("查询所有数据")
        assert tags == ["detail"]

    def test_multiple_tags(self):
        tags = _detect_question_tags("最近30天收入最高的客户")
        assert "time-range" in tags
        assert "aggregation" in tags
        assert "top-n" in tags


class TestInferPrimaryTable:
    def test_matches_by_table_name(self):
        catalog = _make_catalog()
        assert _infer_primary_table("查询订单信息", catalog) == "orders"

    def test_matches_by_alias(self):
        catalog = _make_catalog()
        assert _infer_primary_table("查看菜品列表", catalog) == "dish"

    def test_matches_by_business_term(self):
        catalog = _make_catalog()
        assert _infer_primary_table("交易记录", catalog) == "orders"

    def test_no_match_returns_none(self):
        catalog = _make_catalog()
        result = _infer_primary_table("随便查点什么", catalog)
        # 无匹配时返回 None（score 全为 0）
        assert result is None

    def test_no_catalog_returns_none(self):
        assert _infer_primary_table("查询订单", None) is None

    def test_empty_question(self):
        catalog = _make_catalog()
        # 空问题返回第一张表
        result = _infer_primary_table("", catalog)
        assert result == "orders"


class TestBuildFallbackSql:
    def test_with_catalog(self):
        catalog = _make_catalog()
        sql = build_fallback_sql("查询订单", catalog)
        assert "orders" in sql
        assert "SELECT" in sql
        assert "LIMIT" in sql
        assert ";" in sql

    def test_no_catalog_returns_safe_sql(self):
        sql = build_fallback_sql("查询订单", None)
        assert sql == "SELECT 1 AS result;"

    def test_catalog_no_match_uses_first_table(self):
        catalog = _make_catalog()
        sql = build_fallback_sql("随便查点什么xyz", catalog)
        # 无匹配时使用第一张表
        assert "orders" in sql

    def test_sql_is_read_only(self):
        catalog = _make_catalog()
        sql = build_fallback_sql("查询订单", catalog)
        assert "INSERT" not in sql.upper()
        assert "DELETE" not in sql.upper()
        assert "UPDATE" not in sql.upper()
        assert "DROP" not in sql.upper()


class TestExtractCatalogBusinessTerms:
    def test_extracts_terms(self):
        catalog = _make_catalog()
        terms, markers = _extract_catalog_business_terms(catalog)
        assert "订单表" in terms
        assert "菜品表" in terms
        assert "下单" in terms
        assert "金额" in terms

    def test_extracts_condition_markers(self):
        catalog = _make_catalog()
        _, markers = _extract_catalog_business_terms(catalog)
        # dimension/foreign_key/timestamp 列的 description 作为条件标记
        assert "状态" in markers

    def test_no_catalog(self):
        terms, markers = _extract_catalog_business_terms(None)
        assert terms == []
        assert markers == []


class TestFallbackQueryUnderstanding:
    def test_with_catalog(self):
        catalog = _make_catalog()
        result = _fallback_query_understanding("查询订单金额", catalog)
        assert "target_mentions" in result
        assert "订单表" in result["target_mentions"] or "金额" in result["target_mentions"]

    def test_no_catalog(self):
        result = _fallback_query_understanding("查询订单", None)
        assert result["source"] == "deterministic"
        assert "target_mentions" in result

    def test_aggregation_intent(self):
        result = _fallback_query_understanding("统计订单总金额")
        assert result["intent"] == "aggregate"

    def test_select_intent(self):
        result = _fallback_query_understanding("查询所有数据")
        assert result["intent"] == "select"

    def test_limit_detection(self):
        result = _fallback_query_understanding("前10个订单")
        assert result["limit"] == 10

    def test_order_direction(self):
        result = _fallback_query_understanding("价格最高的菜品")
        assert any(item.get("direction") == "DESC" for item in result["order_by"])

    def test_time_range_detection(self):
        catalog = _make_catalog()
        result = _fallback_query_understanding("最近的订单", catalog)
        assert result["time_range"] is not None
