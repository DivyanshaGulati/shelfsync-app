from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_db_health_with_mocked_connection():
    """Tests endpoint behavior only. Real DB test must run on a Mac with PostgreSQL."""
    engine = MagicMock()
    with patch("app.api.routes.get_engine", return_value=engine):
        response = client.get("/api/health/db")
    assert response.status_code == 200
    engine.connect.assert_called_once()


def test_cors():
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
