from app.rag.candidate_sql_plan_builder import CandidateSQLPlanBuilder
from app.rag.candidate_table_builder import CandidateTableBuilder
from app.rag.confidence_judge import ConfidenceJudge
from app.rag.join_path_planner import JoinPathPlanner
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable
from app.rag.sql_generator import SQLGenerator
from app.rag.sql_planner import SQLPlanner


def _restaurant_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="restaurant",
        tables=[
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="user_id", data_type="bigint", nullable=False),
                    SchemaColumn(name="amount", data_type="decimal", nullable=False),
                ],
            ),
            SchemaTable(
                name="user",
                columns=[SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True)],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="orders",
                from_column="user_id",
                to_table="user",
                to_column="id",
                relation_type="many-to-one",
                confidence="high",
            )
        ],
    )


def _crm_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="crm",
        tables=[
            SchemaTable(
                name="tickets",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="customer_id", data_type="bigint", nullable=False),
                    SchemaColumn(name="title", data_type="varchar", nullable=False),
                ],
            ),
            SchemaTable(
                name="customers",
                columns=[SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True)],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="tickets",
                from_column="customer_id",
                to_table="customers",
                to_column="id",
                relation_type="many-to-one",
                confidence="high",
            )
        ],
    )


def test_multi_fixture_pipeline_supports_restaurant_and_crm() -> None:
    scenarios = [
        {
            "question": "近30天收入最高的用户是谁",
            "catalog": _restaurant_catalog(),
            "query_understanding": {"metrics": [{"term": "金额", "aggregation": "SUM"}], "dimensions": ["用户"], "group_by": [{"term": "用户"}], "limit": 5},
            "schema_linking": {
                "matched_tables": [
                    {"table_name": "orders", "score": 96, "matched_columns": [{"column_name": "user_id", "score": 90}, {"column_name": "amount", "score": 88}]},
                    {"table_name": "user", "score": 80, "matched_columns": [{"column_name": "id", "score": 80}]},
                ]
            },
            "value_links": [],
        },
        {
            "question": "查询包含辣子鸡的菜品",
            "catalog": _restaurant_catalog(),
            "query_understanding": {"limit": 10},
            "schema_linking": {"matched_tables": [{"table_name": "orders", "score": 80, "matched_columns": [{"column_name": "id", "score": 50}]}]},
            "value_links": [{"mention": "辣子鸡", "table": "orders", "column": "id", "db_value": "123", "match_type": "semantic", "like_intent": True}],
            "expect_join": False,
        },
        {
            "question": "最近工单最多的客户",
            "catalog": _crm_catalog(),
            "query_understanding": {"metrics": [{"term": "工单", "aggregation": "COUNT"}], "dimensions": ["客户"], "group_by": [{"term": "客户"}], "limit": 5},
            "schema_linking": {
                "matched_tables": [
                    {"table_name": "tickets", "score": 94, "matched_columns": [{"column_name": "customer_id", "score": 86}]},
                    {"table_name": "customers", "score": 78, "matched_columns": [{"column_name": "id", "score": 76}]},
                ]
            },
            "value_links": [],
        },
        {
            "question": "标题包含支付失败的工单",
            "catalog": _crm_catalog(),
            "query_understanding": {"limit": 20},
            "schema_linking": {"matched_tables": [{"table_name": "tickets", "score": 92, "matched_columns": [{"column_name": "title", "score": 88}]}]},
            "value_links": [{"mention": "支付失败", "table": "tickets", "column": "title", "db_value": "支付失败", "match_type": "exact", "like_intent": True}],
        },
        {
            "question": "查询用户关联订单明细",
            "catalog": _restaurant_catalog(),
            "query_understanding": {"possible_multi_table": True, "limit": 50},
            "schema_linking": {
                "matched_tables": [
                    {"table_name": "orders", "score": 88, "matched_columns": [{"column_name": "user_id", "score": 85}]},
                    {"table_name": "user", "score": 80, "matched_columns": [{"column_name": "id", "score": 70}]},
                ]
            },
            "value_links": [],
            "expect_join": True,
        },
    ]

    for scenario in scenarios:
        candidate_tables = CandidateTableBuilder().build(
            scenario["query_understanding"],
            scenario["schema_linking"],
            scenario["value_links"],
        ).model_dump()
        join_plan = JoinPathPlanner().plan_from_candidate_tables(candidate_tables, scenario["catalog"]).model_dump()
        candidate_bundle = CandidateSQLPlanBuilder().build(
            query_understanding=scenario["query_understanding"],
            schema_linking=scenario["schema_linking"],
            value_links=scenario["value_links"],
            candidate_tables=candidate_tables,
            join_path_plan=join_plan,
        )
        assert candidate_bundle.candidates
        selected = SQLPlanner().select_from_candidates([item.model_dump() for item in candidate_bundle.candidates])
        assert selected is not None
        sql_result = SQLGenerator().generate(selected.model_dump())
        assert sql_result is not None
        assert "SELECT" in sql_result.sql
        if scenario["value_links"]:
            assert ":p0" in sql_result.sql
        if "expect_join" in scenario:
            has_join = "JOIN " in sql_result.sql.upper()
            assert has_join is scenario["expect_join"]
        confidence = ConfidenceJudge().judge(
            schema_linking=scenario["schema_linking"],
            value_links=scenario["value_links"],
            join_path_plan=join_plan,
            candidate_plans=[item.model_dump() for item in candidate_bundle.candidates],
            candidate_tables=candidate_tables,
        )
        assert confidence.final_confidence >= 0.0
