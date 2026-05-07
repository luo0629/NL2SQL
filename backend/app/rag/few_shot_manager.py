from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.rag.schema_models import SchemaCatalog


def _detect_tags(question: str) -> set[str]:
    """从 nodes.py 移植的标签检测，避免循环导入。"""
    normalized = question.strip().lower()
    tags: set[str] = set()

    aggregation_keywords = [
        "sum", "count", "avg", "max", "min", "total",
        "总", "统计", "汇总", "平均", "收入", "销售额", "金额",
        "哪些卖得好", "卖得好", "最受欢迎", "最热门", "热门", "火爆",
        "销量", "数量最多", "最多", "最少", "总共", "合计",
        "有多少", "多少个", "多少条", "几个", "几条",
        "排行榜", "排行", "排名",
    ]
    if any(kw in normalized for kw in aggregation_keywords):
        tags.add("aggregation")

    time_range_keywords = [
        "year", "today", "yesterday", "recent", "latest", "newest",
        "最近", "近 ", "近", "这几天", "这个月", "这周", "今年", "去年",
        "前天", "昨天", "今天", "近期", "刚刚", "刚才", "近日",
        "天", "周", "月", "年",
    ]
    if any(kw in normalized for kw in time_range_keywords):
        tags.add("time-range")

    top_n_keywords = [
        "top", "best", "worst",
        "最高", "最低", "排行", "排名", "前",
        "最贵", "最便宜", "最划算", "最好", "最差",
        "最受欢迎", "最热门", "最火", "最畅销",
        "好评", "差评", "热门",
    ]
    if any(kw in normalized for kw in top_n_keywords):
        tags.add("top-n")

    join_keywords = [
        "join", "关联", "同时", "以及", "和", "对应",
        "属于", "包含", "有哪些", "对应的是", "相关的",
        "一起", "连同", "带上", "附带",
    ]
    if any(kw in normalized for kw in join_keywords):
        tags.add("join")

    if not tags:
        tags.add("detail")

    return tags


def _extract_tables_from_sql(sql: str) -> set[str]:
    """从 SQL 文本中提取引用的表名。"""
    tables: set[str] = set()
    # SQL 关键字边界
    keywords = {"select", "from", "join", "on", "where", "group", "order",
                "limit", "having", "union", "intersect", "except", "as",
                "inner", "left", "right", "outer", "cross", "natural"}
    # 按空格和逗号分词
    tokens = re.split(r"[\s,;()]+", sql, flags=re.IGNORECASE)
    expecting_table = False
    for token in tokens:
        if not token:
            continue
        lower = token.lower()
        if lower in ("from", "join", "inner", "left", "right", "outer", "cross", "natural"):
            expecting_table = True
            continue
        if expecting_table:
            if lower not in keywords and lower.isidentifier():
                tables.add(lower)
                continue  # 继续保持 expecting_table 状态以捕获逗号分隔的后续表
            expecting_table = False
    return tables


class FewShotManager:
    def __init__(self, catalog: SchemaCatalog | None = None) -> None:
        self.catalog = catalog
        self._schema_table_names: set[str] = set()
        if catalog:
            self._schema_table_names = {table.name.lower() for table in catalog.tables}

    def select_examples(self, question: str, limit: int = 3) -> list[dict[str, object]]:
        """选择与当前 schema 兼容且与问题标签匹配的 few-shot 示例。"""
        static_examples = self._load_static_examples()
        compatible_static = self._filter_compatible(static_examples)
        dynamic_examples = self._generate_dynamic_examples()

        # 合并：静态优先，动态补充
        all_examples = compatible_static + dynamic_examples
        if not all_examples:
            return []

        question_tags = _detect_tags(question)
        scored: list[tuple[int, int, dict[str, object]]] = []
        for idx, example in enumerate(all_examples):
            example_tags = set(cast(list[str], example.get("tags", [])))
            overlap = len(question_tags & example_tags)
            scored.append((overlap, idx, example))

        # 按重叠度降序，同分按原始顺序
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [example for _, _, example in scored[:limit]]

    def _load_static_examples(self) -> list[dict[str, object]]:
        """加载静态 few-shot 示例文件。"""
        examples_path = (
            Path(__file__).resolve().parents[1] / "prompts" / "few_shot_examples.json"
        )
        if not examples_path.exists():
            return []

        try:
            raw = json.loads(examples_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, list):
            return []

        normalized: list[dict[str, object]] = []
        for example in raw:
            if not isinstance(example, dict):
                continue
            question = example.get("question")
            sql = example.get("sql")
            tags = example.get("tags", [])
            if not isinstance(question, str) or not isinstance(sql, str):
                continue
            normalized_tags = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()]
            normalized.append({
                "question": question.strip(),
                "sql": sql.strip(),
                "tags": normalized_tags,
                "source": "static",
            })
        return normalized

    def _filter_compatible(self, examples: list[dict[str, object]]) -> list[dict[str, object]]:
        """过滤掉引用了当前 schema 中不存在的表的示例。"""
        if not self._schema_table_names:
            # 无 schema 信息时保留所有静态示例
            return examples

        compatible: list[dict[str, object]] = []
        for example in examples:
            sql = cast(str, example.get("sql", ""))
            referenced_tables = _extract_tables_from_sql(sql)
            if not referenced_tables:
                # 无法提取表名时保留
                compatible.append(example)
                continue
            # 所有引用的表都必须在当前 schema 中存在
            if referenced_tables.issubset(self._schema_table_names):
                compatible.append(example)
        return compatible

    def _generate_dynamic_examples(self) -> list[dict[str, object]]:
        """基于当前 schema 动态生成 few-shot 示例。"""
        if not self.catalog or not self.catalog.tables:
            return []

        examples: list[dict[str, object]] = []
        tables = self.catalog.tables

        # 为每种查询类型生成示例
        for table in tables[:5]:  # 限制表数量避免过多
            columns = table.columns
            if not columns:
                continue

            # detail 类型：简单 SELECT
            detail_cols = [col.name for col in columns if not col.is_primary_key][:3]
            if detail_cols:
                col_list = ", ".join(detail_cols)
                examples.append({
                    "question": f"查询{table.description or table.name}的基本信息",
                    "sql": f"SELECT {col_list} FROM {table.name} LIMIT 20;",
                    "tags": ["detail"],
                    "source": "dynamic",
                })

            # 找到可聚合的数值列
            numeric_cols = [
                col for col in columns
                if any(t in col.data_type.lower() for t in ["int", "float", "decimal", "numeric", "real"])
                and not col.is_primary_key
            ]

            # aggregation 类型
            if numeric_cols:
                metric_col = numeric_cols[0]
                group_cols = [col for col in columns if col.semantic_role in ("dimension", "foreign_key") and not col.is_primary_key]
                if group_cols:
                    group_col = group_cols[0]
                    examples.append({
                        "question": f"统计每个{group_col.description or group_col.name}的{metric_col.description or metric_col.name}总和",
                        "sql": f"SELECT {group_col.name}, SUM({metric_col.name}) AS total FROM {table.name} GROUP BY {group_col.name} ORDER BY total DESC LIMIT 10;",
                        "tags": ["aggregation"],
                        "source": "dynamic",
                    })

            # 找到时间列
            time_cols = [
                col for col in columns
                if col.semantic_role == "timestamp" or any(t in col.name.lower() for t in ["time", "date", "created", "updated"])
            ]

            # time-range 类型
            if time_cols:
                time_col = time_cols[0]
                examples.append({
                    "question": f"查询最近的{table.description or table.name}记录",
                    "sql": f"SELECT * FROM {table.name} ORDER BY {time_col.name} DESC LIMIT 20;",
                    "tags": ["detail", "time-range"],
                    "source": "dynamic",
                })

            # top-n 类型
            if numeric_cols and time_cols:
                metric_col = numeric_cols[0]
                examples.append({
                    "question": f"{table.description or table.name}中{metric_col.description or metric_col.name}最高的记录",
                    "sql": f"SELECT * FROM {table.name} ORDER BY {metric_col.name} DESC LIMIT 10;",
                    "tags": ["top-n"],
                    "source": "dynamic",
                })

        # join 类型：利用 schema 中的关联关系
        if self.catalog.relations and len(tables) >= 2:
            relation = self.catalog.relations[0]
            examples.append({
                "question": f"查询{relation.from_table}及其关联的{relation.to_table}信息",
                "sql": f"SELECT * FROM {relation.from_table} JOIN {relation.to_table} ON {relation.from_table}.{relation.from_column} = {relation.to_table}.{relation.to_column} LIMIT 20;",
                "tags": ["join", "detail"],
                "source": "dynamic",
            })

        return examples
