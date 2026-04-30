from app.rag.join_path_planner import JoinPathPlanner
from app.rag.schema_linker import LinkedColumn, LinkedTable, SchemaLinkingResult
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable


def build_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="sky_take_out",
        tables=[
            SchemaTable(
                name="orders",
                description="订单主表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="user_id", data_type="bigint", nullable=False),
                    SchemaColumn(name="address_book_id", data_type="bigint", nullable=True),
                ],
                primary_keys=["id"],
                searchable_terms=["orders", "订单"],
            ),
            SchemaTable(
                name="user",
                description="用户表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                ],
                primary_keys=["id"],
                searchable_terms=["user", "用户"],
            ),
            SchemaTable(
                name="address_book",
                description="地址表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                ],
                primary_keys=["id"],
                searchable_terms=["address_book", "地址"],
            ),
            SchemaTable(
                name="dish",
                description="菜品表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="category_id", data_type="bigint", nullable=True),
                ],
                primary_keys=["id"],
                searchable_terms=["dish", "菜品"],
            ),
            SchemaTable(
                name="category",
                description="分类表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                ],
                primary_keys=["id"],
                searchable_terms=["category", "分类"],
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
                join_hint="通过下单用户ID关联订单与用户",
            ),
            SchemaRelation(
                from_table="orders",
                from_column="address_book_id",
                to_table="address_book",
                to_column="id",
                relation_type="many-to-one",
                confidence="medium",
                join_hint="通过地址ID关联订单与地址",
            ),
            SchemaRelation(
                from_table="orders",
                from_column="id",
                to_table="dish",
                to_column="id",
                relation_type="one-to-many",
                confidence="medium",
                join_hint="测试用中间关系：订单可关联菜品",
            ),
            SchemaRelation(
                from_table="dish",
                from_column="category_id",
                to_table="category",
                to_column="id",
                relation_type="many-to-one",
                confidence="high",
                join_hint="通过分类ID关联菜品与分类",
            ),
        ],
        synced_at="2026-04-29T00:00:00Z",
    )


def build_linking_result(*table_names: str) -> SchemaLinkingResult:
    linked_tables = []
    score = 100
    for table_name in table_names:
        linked_tables.append(
            LinkedTable(
                table_name=table_name,
                score=score,
                matched_terms=[table_name],
                matched_columns=[LinkedColumn(column_name="id", score=10, matched_terms=[table_name])],
                rationale=f"命中 {table_name}",
            )
        )
        score -= 10

    return SchemaLinkingResult(
        question="测试问题",
        matched_tables=linked_tables,
        matched_relations=[
            relation for relation in build_catalog().relations if relation.from_table in table_names and relation.to_table in table_names
        ],
        unresolved_terms=[],
        linking_summary="测试 linking 结果",
    )


def test_join_path_planner_builds_single_hop_plan() -> None:
    planner = JoinPathPlanner()
    catalog = build_catalog()
    linking_result = build_linking_result("orders", "user")

    plan = planner.plan(linking_result, catalog)

    assert plan.primary_table == "orders"
    assert plan.tables_in_plan == ["orders", "user"]
    assert len(plan.edges) == 1
    assert plan.edges[0].left_table == "orders"
    assert plan.edges[0].right_table == "user"
    assert plan.edges[0].source == "schema_relation"
    assert plan.requires_distinct is False
    assert plan.plan_confidence == "high"


def test_join_path_planner_marks_medium_confidence_for_medium_edge() -> None:
    planner = JoinPathPlanner()
    catalog = build_catalog()
    linking_result = build_linking_result("orders", "address_book")

    plan = planner.plan(linking_result, catalog)

    assert plan.primary_table == "orders"
    assert plan.tables_in_plan == ["orders", "address_book"]
    assert len(plan.edges) == 1
    assert plan.edges[0].confidence == "medium"
    assert plan.plan_confidence == "medium"


def test_join_path_planner_builds_multi_hop_plan() -> None:
    planner = JoinPathPlanner()
    catalog = build_catalog()
    linking_result = build_linking_result("orders", "category")
    linking_result.matched_relations = catalog.relations

    plan = planner.plan(linking_result, catalog)

    assert plan.primary_table == "orders"
    assert plan.tables_in_plan == ["orders", "category"]
    assert len(plan.edges) == 2
    assert plan.edges[0].left_table == "orders"
    assert plan.edges[0].right_table == "dish"
    assert plan.edges[1].left_table == "dish"
    assert plan.edges[1].right_table == "category"
    assert plan.plan_confidence == "medium"


def test_join_path_planner_marks_requires_distinct_for_one_to_many_path() -> None:
    planner = JoinPathPlanner()
    catalog = build_catalog()
    linking_result = build_linking_result("orders", "dish")

    plan = planner.plan(linking_result, catalog)

    assert plan.requires_distinct is True
    assert "建议去重" in plan.planning_summary


def test_join_path_planner_reports_ambiguous_paths() -> None:
    catalog = build_catalog()
    catalog.relations.append(
        SchemaRelation(
            from_table="orders",
            from_column="id",
            to_table="user",
            to_column="id",
            relation_type="one-to-one",
            confidence="low",
            join_hint="测试用歧义关系",
        )
    )
    linking_result = build_linking_result("orders", "user")
    linking_result.matched_relations = [
        relation
        for relation in catalog.relations
        if relation.from_table == "orders" and relation.to_table == "user"
    ]

    plan = JoinPathPlanner().plan(linking_result, catalog)

    assert plan.ambiguous_paths == ["orders<->user"]
    assert "候选歧义路径" in plan.planning_summary


def test_join_path_planner_reports_unresolved_tables_when_no_path_exists() -> None:
    planner = JoinPathPlanner()
    catalog = build_catalog()
    linking_result = build_linking_result("user", "category")
    linking_result.matched_relations = []

    plan = planner.plan(linking_result, catalog)

    assert plan.primary_table == "user"
    assert plan.tables_in_plan == ["user"]
    assert plan.edges == []
    assert plan.unresolved_tables == ["category"]
    assert plan.plan_confidence == "none"
    assert "未解决表: category" in plan.planning_summary
