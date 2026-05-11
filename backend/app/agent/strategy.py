from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config_loader import get_app_config


_DEF_LOW_VALUE_SEMANTIC_TERMS = (
    "id",
    "主键",
    "名称",
    "名字",
    "name",
    "status",
    "type",
    "sort",
    "image",
    "description",
    "remark",
    "create_time",
    "update_time",
    "create_user",
    "update_user",
    "deleted",
)

_DEF_EXPLICIT_IDENTIFIER_TERMS = (
    "id",
    "编号",
    "代码",
    "编码",
    "号码",
    "单号",
    "code",
    " no",
    "number",
)

_DEF_INTERNAL_AUDIT_COLUMNS = (
    "create_user",
    "update_user",
    "created_by",
    "updated_by",
    "creator_id",
    "updater_id",
    "created_user_id",
    "updated_user_id",
    "deleted",
    "is_deleted",
    "delete_flag",
)

_DEF_INTERNAL_AUDIT_TIME_COLUMNS = (
    "create_time",
    "update_time",
    "created_at",
    "updated_at",
    "create_date",
    "update_date",
    "modified_at",
    "modified_time",
)

_DEF_DISPLAY_NAME_TOKENS = (
    "name",
    "title",
    "label",
    "subject",
    "summary",
    "description",
    "detail",
    "remark",
)

_DEF_BUSINESS_VALUE_TOKENS = (
    "amount",
    "price",
    "total",
    "status",
    "time",
    "date",
    "quantity",
    "qty",
    "count",
    "number",
    "phone",
    "type",
)


@dataclass(frozen=True)
class AgentTermSets:
    low_value_semantic_terms: frozenset[str] = field(default_factory=lambda: frozenset(_DEF_LOW_VALUE_SEMANTIC_TERMS))
    explicit_identifier_terms: tuple[str, ...] = _DEF_EXPLICIT_IDENTIFIER_TERMS
    internal_audit_columns: frozenset[str] = field(default_factory=lambda: frozenset(_DEF_INTERNAL_AUDIT_COLUMNS))
    internal_audit_time_columns: frozenset[str] = field(default_factory=lambda: frozenset(_DEF_INTERNAL_AUDIT_TIME_COLUMNS))
    display_name_tokens: tuple[str, ...] = _DEF_DISPLAY_NAME_TOKENS
    business_value_tokens: tuple[str, ...] = _DEF_BUSINESS_VALUE_TOKENS


@dataclass(frozen=True)
class JoinPreferenceSettings:
    confidence_bonus: dict[str, float] = field(default_factory=lambda: {"high": 12.0, "medium": 6.0, "low": 0.0})
    default_confidence_bonus: float = 2.0
    relation_type_bonus: dict[str, float] = field(default_factory=lambda: {
        "foreign_key": 40.0,
        "configured": 24.0,
        "inferred-shared-key": 10.0,
    })
    default_relation_type_bonus: float = 4.0
    governance_penalties: dict[str, float] = field(default_factory=lambda: {
        "deprecated_endpoint": 18.0,
        "suspected_endpoint": 10.0,
        "missing_runtime_validated": 2.0,
    })
    runtime_validated_bonus: float = 4.0
    weaker_join_min_gap: float = 4.0


@dataclass(frozen=True)
class FallbackSettings:
    relevant_table_limit: int = 4
    candidate_score_limit: int = 6
    display_column_limit: int = 5
    schema_display_column_limit: int = 6
    fallback_sql_limit: int = 20
    prompt_default_limit: int = 200


@dataclass(frozen=True)
class AgentRuntimeStrategy:
    term_sets: AgentTermSets = field(default_factory=AgentTermSets)
    join_preferences: JoinPreferenceSettings = field(default_factory=JoinPreferenceSettings)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)
    disabled_table_keys: dict[str, frozenset[str]] = field(default_factory=dict)

    def disabled_keys_for(self, table_ref: str) -> frozenset[str]:
        matched: set[str] = set()
        table_variants = _table_ref_variants(table_ref)
        for config_table, columns in self.disabled_table_keys.items():
            if table_variants & _table_ref_variants(config_table):
                matched.update(columns)
        return frozenset(matched)

    def is_disabled_column(self, table_ref: str, column_name: str) -> bool:
        return _normalize_key(column_name) in self.disabled_keys_for(table_ref)


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("`", "")


def _table_ref_variants(table_ref: str) -> set[str]:
    normalized = _normalize_key(table_ref)
    if not normalized:
        return set()
    parts = [part for part in normalized.split(".") if part]
    variants = {normalized}
    if parts:
        variants.add(parts[-1])
    if len(parts) >= 2:
        variants.add(".".join(parts[-2:]))
    return variants


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_key(str(item))
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_float_map(value: Any, default: dict[str, float]) -> dict[str, float]:
    result = dict(default)
    if not isinstance(value, dict):
        return result
    for key, raw in value.items():
        normalized_key = _normalize_key(str(key))
        try:
            result[normalized_key] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _normalize_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _normalize_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _normalize_disabled_table_keys(value: Any) -> dict[str, frozenset[str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, frozenset[str]] = {}
    for raw_table, raw_columns in value.items():
        table_key = _normalize_key(str(raw_table))
        columns = frozenset(_normalize_string_list(raw_columns))
        if table_key and columns:
            normalized[table_key] = columns
    return normalized


def build_agent_runtime_strategy(config: dict[str, Any] | None = None) -> AgentRuntimeStrategy:
    payload = config if isinstance(config, dict) else {}

    term_sets = AgentTermSets(
        low_value_semantic_terms=frozenset(_normalize_string_list(payload.get("low_value_semantic_terms")) or _DEF_LOW_VALUE_SEMANTIC_TERMS),
        explicit_identifier_terms=_normalize_string_list(payload.get("explicit_identifier_terms")) or _DEF_EXPLICIT_IDENTIFIER_TERMS,
        internal_audit_columns=frozenset(_normalize_string_list(payload.get("internal_audit_columns")) or _DEF_INTERNAL_AUDIT_COLUMNS),
        internal_audit_time_columns=frozenset(_normalize_string_list(payload.get("internal_audit_time_columns")) or _DEF_INTERNAL_AUDIT_TIME_COLUMNS),
        display_name_tokens=_normalize_string_list(payload.get("display_name_tokens")) or _DEF_DISPLAY_NAME_TOKENS,
        business_value_tokens=_normalize_string_list(payload.get("business_value_tokens")) or _DEF_BUSINESS_VALUE_TOKENS,
    )

    join_payload = payload.get("join_preferences") if isinstance(payload.get("join_preferences"), dict) else {}
    default_join = JoinPreferenceSettings()
    join_preferences = JoinPreferenceSettings(
        confidence_bonus=_normalize_float_map(join_payload.get("confidence_bonus"), default_join.confidence_bonus),
        default_confidence_bonus=_normalize_float(join_payload.get("default_confidence_bonus"), default_join.default_confidence_bonus),
        relation_type_bonus=_normalize_float_map(join_payload.get("relation_type_bonus"), default_join.relation_type_bonus),
        default_relation_type_bonus=_normalize_float(join_payload.get("default_relation_type_bonus"), default_join.default_relation_type_bonus),
        governance_penalties=_normalize_float_map(join_payload.get("governance_penalties"), default_join.governance_penalties),
        runtime_validated_bonus=_normalize_float(join_payload.get("runtime_validated_bonus"), default_join.runtime_validated_bonus),
        weaker_join_min_gap=_normalize_float(join_payload.get("weaker_join_min_gap"), default_join.weaker_join_min_gap),
    )

    fallback_payload = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    default_fallback = FallbackSettings()
    fallback = FallbackSettings(
        relevant_table_limit=_normalize_int(fallback_payload.get("relevant_table_limit"), default_fallback.relevant_table_limit),
        candidate_score_limit=_normalize_int(fallback_payload.get("candidate_score_limit"), default_fallback.candidate_score_limit),
        display_column_limit=_normalize_int(fallback_payload.get("display_column_limit"), default_fallback.display_column_limit),
        schema_display_column_limit=_normalize_int(fallback_payload.get("schema_display_column_limit"), default_fallback.schema_display_column_limit),
        fallback_sql_limit=_normalize_int(fallback_payload.get("fallback_sql_limit"), default_fallback.fallback_sql_limit),
        prompt_default_limit=_normalize_int(fallback_payload.get("prompt_default_limit"), default_fallback.prompt_default_limit),
    )

    return AgentRuntimeStrategy(
        term_sets=term_sets,
        join_preferences=join_preferences,
        fallback=fallback,
        disabled_table_keys=_normalize_disabled_table_keys(payload.get("disabled_table_keys")),
    )


def get_agent_runtime_strategy() -> AgentRuntimeStrategy:
    return build_agent_runtime_strategy(get_app_config().get("agent_strategy"))
