from __future__ import annotations

from pydantic import BaseModel, Field


class TableEnrichment(BaseModel):
    aliases: list[str] = Field(default_factory=list)
    business_terms: list[str] = Field(default_factory=list)


class ColumnEnrichment(BaseModel):
    business_terms: list[str] = Field(default_factory=list)
    semantic_role: str | None = None


class RelationEnrichment(BaseModel):
    confidence: str | None = None
    join_hint: str | None = None


class SchemaEnrichment(BaseModel):
    table_enrichments: dict[str, TableEnrichment] = Field(default_factory=dict)
    column_enrichments: dict[str, dict[str, ColumnEnrichment]] = Field(default_factory=dict)
    relation_enrichments: dict[str, RelationEnrichment] = Field(default_factory=dict)


_SCHEMA_ENRICHMENT = SchemaEnrichment(
    table_enrichments={
        "orders": TableEnrichment(
            aliases=["order", "订单", "订单表"],
            business_terms=["下单", "订单记录", "交易订单"],
        ),
        "order_detail": TableEnrichment(
            aliases=["订单明细", "订单详情"],
            business_terms=["明细", "菜品明细", "套餐明细"],
        ),
        "user": TableEnrichment(
            aliases=["users", "用户", "用户表", "会员"],
            business_terms=["客户", "下单用户", "注册用户"],
        ),
        "dish": TableEnrichment(
            aliases=["菜品", "菜品表"],
            business_terms=["商品", "单品", "菜"],
        ),
        "category": TableEnrichment(
            aliases=["分类", "分类表"],
            business_terms=["菜品分类", "套餐分类"],
        ),
        "setmeal": TableEnrichment(
            aliases=["套餐", "套餐表"],
            business_terms=["套餐商品", "组合套餐"],
        ),
        "setmeal_dish": TableEnrichment(
            aliases=["套餐菜品关系", "套餐明细"],
            business_terms=["套餐包含菜品", "套餐组成"],
        ),
        "shopping_cart": TableEnrichment(
            aliases=["购物车", "购物车表"],
            business_terms=["购物车项", "待下单商品"],
        ),
        "address_book": TableEnrichment(
            aliases=["地址簿", "收货地址"],
            business_terms=["地址", "配送地址", "用户地址"],
        ),
        "employee": TableEnrichment(
            aliases=["员工", "员工表"],
            business_terms=["运营人员", "后台员工"],
        ),
        "dish_flavor": TableEnrichment(
            aliases=["口味", "菜品口味"],
            business_terms=["口味选项", "风味"],
        ),
    },
    column_enrichments={
        "orders": {
            "id": ColumnEnrichment(business_terms=["订单ID", "订单编号"], semantic_role="identifier"),
            "user_id": ColumnEnrichment(business_terms=["用户ID", "下单用户"], semantic_role="foreign_key"),
            "amount": ColumnEnrichment(business_terms=["订单金额", "实付金额", "消费金额"], semantic_role="metric"),
            "order_time": ColumnEnrichment(business_terms=["下单时间", "创建时间"], semantic_role="timestamp"),
            "checkout_time": ColumnEnrichment(business_terms=["结账时间", "支付时间", "完成时间"], semantic_role="timestamp"),
            "status": ColumnEnrichment(business_terms=["订单状态", "交易状态"], semantic_role="dimension"),
            "address_book_id": ColumnEnrichment(business_terms=["地址ID", "收货地址"], semantic_role="foreign_key"),
        },
        "order_detail": {
            "order_id": ColumnEnrichment(business_terms=["订单ID", "所属订单"], semantic_role="foreign_key"),
            "dish_id": ColumnEnrichment(business_terms=["菜品ID", "关联菜品"], semantic_role="foreign_key"),
            "setmeal_id": ColumnEnrichment(business_terms=["套餐ID", "关联套餐"], semantic_role="foreign_key"),
            "number": ColumnEnrichment(business_terms=["数量", "购买数量", "份数"], semantic_role="metric"),
            "amount": ColumnEnrichment(business_terms=["明细金额", "单项金额"], semantic_role="metric"),
            "name": ColumnEnrichment(business_terms=["名称", "菜品名称", "套餐名称"], semantic_role="dimension"),
        },
        "user": {
            "id": ColumnEnrichment(business_terms=["用户ID", "会员ID"], semantic_role="identifier"),
            "name": ColumnEnrichment(business_terms=["用户名", "用户姓名", "会员名称"], semantic_role="dimension"),
            "phone": ColumnEnrichment(business_terms=["手机号", "联系电话"], semantic_role="dimension"),
            "create_time": ColumnEnrichment(business_terms=["注册时间", "创建时间"], semantic_role="timestamp"),
        },
        "dish": {
            "id": ColumnEnrichment(business_terms=["菜品ID"], semantic_role="identifier"),
            "name": ColumnEnrichment(business_terms=["菜品名称", "商品名"], semantic_role="dimension"),
            "category_id": ColumnEnrichment(business_terms=["分类ID", "所属分类"], semantic_role="foreign_key"),
            "price": ColumnEnrichment(business_terms=["价格", "售价", "单价"], semantic_role="metric"),
            "status": ColumnEnrichment(business_terms=["起售状态", "上架状态"], semantic_role="dimension"),
        },
        "category": {
            "id": ColumnEnrichment(business_terms=["分类ID"], semantic_role="identifier"),
            "name": ColumnEnrichment(business_terms=["分类名", "类别名称"], semantic_role="dimension"),
            "type": ColumnEnrichment(business_terms=["分类类型", "菜品/套餐类型"], semantic_role="dimension"),
            "sort": ColumnEnrichment(business_terms=["排序", "展示顺序"], semantic_role="dimension"),
        },
        "setmeal": {
            "id": ColumnEnrichment(business_terms=["套餐ID"], semantic_role="identifier"),
            "name": ColumnEnrichment(business_terms=["套餐名称"], semantic_role="dimension"),
            "category_id": ColumnEnrichment(business_terms=["分类ID", "所属分类"], semantic_role="foreign_key"),
            "price": ColumnEnrichment(business_terms=["套餐价格", "售价"], semantic_role="metric"),
            "status": ColumnEnrichment(business_terms=["起售状态", "上架状态"], semantic_role="dimension"),
        },
        "shopping_cart": {
            "user_id": ColumnEnrichment(business_terms=["用户ID", "所属用户"], semantic_role="foreign_key"),
            "dish_id": ColumnEnrichment(business_terms=["菜品ID", "购物车菜品"], semantic_role="foreign_key"),
            "setmeal_id": ColumnEnrichment(business_terms=["套餐ID", "购物车套餐"], semantic_role="foreign_key"),
            "number": ColumnEnrichment(business_terms=["数量", "加购数量"], semantic_role="metric"),
            "create_time": ColumnEnrichment(business_terms=["加入时间", "创建时间"], semantic_role="timestamp"),
        },
        "address_book": {
            "id": ColumnEnrichment(business_terms=["地址ID"], semantic_role="identifier"),
            "user_id": ColumnEnrichment(business_terms=["用户ID", "所属用户"], semantic_role="foreign_key"),
            "detail": ColumnEnrichment(business_terms=["详细地址", "门牌地址"], semantic_role="dimension"),
        },
    },
    relation_enrichments={
        "dish.category_id->category.id": RelationEnrichment(confidence="high", join_hint="通过菜品分类关联菜品与分类"),
        "setmeal.category_id->category.id": RelationEnrichment(confidence="high", join_hint="通过套餐分类关联套餐与分类"),
        "dish_flavor.dish_id->dish.id": RelationEnrichment(confidence="high", join_hint="通过菜品ID关联菜品与口味"),
        "order_detail.order_id->orders.id": RelationEnrichment(confidence="high", join_hint="通过订单ID关联订单明细与订单主表"),
        "order_detail.dish_id->dish.id": RelationEnrichment(confidence="medium", join_hint="当明细项为菜品时通过菜品ID关联订单明细与菜品"),
        "orders.user_id->user.id": RelationEnrichment(confidence="high", join_hint="通过下单用户ID关联订单与用户"),
        "shopping_cart.user_id->user.id": RelationEnrichment(confidence="high", join_hint="通过用户ID关联购物车与用户"),
        "shopping_cart.dish_id->dish.id": RelationEnrichment(confidence="medium", join_hint="当购物车项为菜品时通过菜品ID关联购物车与菜品"),
        "shopping_cart.setmeal_id->setmeal.id": RelationEnrichment(confidence="medium", join_hint="当购物车项为套餐时通过套餐ID关联购物车与套餐"),
        "setmeal_dish.setmeal_id->setmeal.id": RelationEnrichment(confidence="high", join_hint="通过套餐ID关联套餐与套餐菜品关系"),
        "setmeal_dish.dish_id->dish.id": RelationEnrichment(confidence="high", join_hint="通过菜品ID关联套餐菜品关系与菜品"),
        "orders.address_book_id->address_book.id": RelationEnrichment(confidence="medium", join_hint="通过地址ID关联订单与收货地址"),
    },
)


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _relation_key(from_table: str, from_column: str, to_table: str, to_column: str) -> str:
    return f"{_normalize_key(from_table)}.{_normalize_key(from_column)}->{_normalize_key(to_table)}.{_normalize_key(to_column)}"


def load_schema_enrichment() -> SchemaEnrichment:
    return _SCHEMA_ENRICHMENT.model_copy(deep=True)


def get_table_enrichment(enrichment: SchemaEnrichment, table_name: str) -> TableEnrichment:
    return enrichment.table_enrichments.get(_normalize_key(table_name), TableEnrichment())


def get_column_enrichment(
    enrichment: SchemaEnrichment,
    *,
    table_name: str,
    column_name: str,
) -> ColumnEnrichment:
    table_columns = enrichment.column_enrichments.get(_normalize_key(table_name), {})
    return table_columns.get(_normalize_key(column_name), ColumnEnrichment())


def get_relation_enrichment(
    enrichment: SchemaEnrichment,
    *,
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
) -> RelationEnrichment:
    key = _relation_key(from_table, from_column, to_table, to_column)
    return enrichment.relation_enrichments.get(key, RelationEnrichment())
