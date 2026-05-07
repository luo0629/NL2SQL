from __future__ import annotations

from copy import deepcopy
from typing import Any


class SQLRepairResult:
    def __init__(self, sql_plan: dict[str, Any], repaired: bool, fatal: bool, summary: str) -> None:
        self.sql_plan = sql_plan
        self.repaired = repaired
        self.fatal = fatal
        self.summary = summary


class SQLRepairer:
    def repair(
        self,
        sql_plan: dict[str, Any],
        validation_issues: list[dict[str, Any]],
    ) -> SQLRepairResult:
        if not validation_issues:
            return SQLRepairResult(sql_plan=sql_plan, repaired=False, fatal=False, summary="没有需要修复的校验问题。")

        fatal_issues = [issue for issue in validation_issues if not issue.get("repairable", False)]
        if fatal_issues:
            return SQLRepairResult(
                sql_plan=sql_plan,
                repaired=False,
                fatal=True,
                summary=f"存在不可自动修复的问题：{fatal_issues[0].get('code', 'UNKNOWN')}。",
            )

        repaired_plan = deepcopy(sql_plan)
        repaired = False
        repaired_codes: list[str] = []

        for issue in validation_issues:
            code = issue.get("code")
            if code == "WHERE_WITHOUT_VALUE_LINKING":
                repaired = self._repair_where_source(repaired_plan) or repaired
                repaired_codes.append(str(code))
            elif code == "PARAMETER_INDEX_INVALID":
                repaired = self._repair_invalid_parameter_indexes(repaired_plan) or repaired
                repaired_codes.append(str(code))

        if not repaired:
            return SQLRepairResult(
                sql_plan=sql_plan,
                repaired=False,
                fatal=True,
                summary="校验问题被标记为可修复，但当前 Repairer 没有安全修复策略。",
            )

        return SQLRepairResult(
            sql_plan=repaired_plan,
            repaired=True,
            fatal=False,
            summary=f"已修复 SQL Plan 问题：{', '.join(repaired_codes)}。",
        )

    def _repair_where_source(self, sql_plan: dict[str, Any]) -> bool:
        repaired = False
        for clause in sql_plan.get("where", []):
            if isinstance(clause, dict) and not clause.get("source"):
                clause["source"] = "value_linking"
                repaired = True
        return repaired

    def _repair_invalid_parameter_indexes(self, sql_plan: dict[str, Any]) -> bool:
        params = sql_plan.get("params", [])
        if not isinstance(params, list):
            return False

        valid_where = []
        repaired = False
        for clause in sql_plan.get("where", []):
            if not isinstance(clause, dict):
                repaired = True
                continue
            param_index = clause.get("param_index")
            if not isinstance(param_index, int) or param_index < 0 or param_index >= len(params):
                repaired = True
                continue
            valid_where.append(clause)

        if repaired:
            sql_plan["where"] = valid_where
        return repaired
