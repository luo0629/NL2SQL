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
    assert payload["status"] == "mock"
    assert "sql" in payload
    assert "SELECT" in cast(str, payload["sql"])
    assert payload["rows"] == [{"id": 1, "name": "mock-row"}]
    assert payload["row_count"] == 1
    assert payload["columns"] == ["id", "name"]
    assert payload["execution_summary"] == "查询执行成功，共返回 1 行。"
