def list_available_tools() -> list[str]:
    # 当前教程阶段可用的工具清单（execute_sql 预留，尚未接入执行节点）。
    return ["retrieve_schema", "generate_sql", "validate_sql", "execute_sql"]
