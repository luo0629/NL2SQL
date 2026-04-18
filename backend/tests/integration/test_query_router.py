from typing import cast

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_query_endpoint() -> None:
    client = TestClient(app)

    response = client.post("/api/query", json={"question": "找出最近的收入数据"})

    assert response.status_code == 200
    payload = cast(dict[str, object], response.json())
    assert payload["status"] == "mock"
    assert "sql" in payload
