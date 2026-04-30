from typing import cast

from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_query_endpoint(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "找出最近的收入数据"})

    assert response.status_code == 200
    payload = cast(dict[str, object], response.json())
    assert payload["status"] in {"ready", "mock"}
    assert "sql" in payload
    assert "SELECT" in cast(str, payload["sql"])
    assert "params" in payload
    assert "debug" in payload
    assert payload["rows"] == [{"sql": payload["sql"], "params": payload["params"]}]
    assert payload["row_count"] == 1
    assert payload["columns"] == ["sql", "params"]
    assert payload["execution_summary"] == "查询执行成功，共返回 1 行。"
    debug = cast(dict[str, object], payload["debug"])
    assert "query_understanding" in debug
    assert "sql_plan" in debug


def test_query_endpoint_rows_are_tied_to_generated_sql(client: TestClient) -> None:
    first = cast(dict[str, object], client.post("/api/query", json={"question": "查询订单"}).json())
    second = cast(dict[str, object], client.post("/api/query", json={"question": "查询菜品"}).json())

    assert first["rows"] == [{"sql": first["sql"], "params": first["params"]}]
    assert second["rows"] == [{"sql": second["sql"], "params": second["params"]}]
    assert first["rows"] != [{"id": 1, "name": "mock-row"}]
    assert second["rows"] != [{"id": 1, "name": "mock-row"}]


def test_query_endpoint_includes_debug_trace_contract(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "查询最近一个月销售额最高的前5个菜品和口味"})

    assert response.status_code == 200
    payload = cast(dict[str, object], response.json())
    debug = cast(dict[str, object], payload["debug"])

    assert isinstance(payload["params"], list)
    assert "query_understanding" in debug
    assert "schema_linking" in debug
    assert "schema_links" in debug
    assert "value_links" in debug
    assert "join_path_plan" in debug
    assert "join_paths" in debug
    assert "semantic_brief" in debug
    assert "sql_plan" in debug
    assert "validation" in debug
    assert "repair_attempts" in debug
    assert "execution" in debug


def test_query_endpoint_rejects_empty_question(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": ""})

    assert response.status_code == 422
