from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Admin, WebSession


def test_setup_session_is_recognized_by_auth_status():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.execute(delete(WebSession))
        db.execute(delete(Admin))
        db.commit()

    with TestClient(app) as client:
        setup = client.post(
            "/auth/setup",
            json={"username": "owner", "password": "strong-password"},
        )
        assert setup.status_code == 201
        assert client.cookies.get("trafficmanager_session")

        status = client.get("/auth/status")
        assert status.status_code == 200
        assert status.json()["initialized"] is True
        assert status.json()["authenticated"] is True
        assert status.json()["csrfToken"] == setup.json()["csrfToken"]
        assert client.get("/api/dashboard").status_code == 200
