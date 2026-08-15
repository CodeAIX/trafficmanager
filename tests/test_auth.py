from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Admin, Client, Node, Policy, WebSession


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

        with SessionLocal() as db:
            node = Node(name="test-node", base_url="https://node.example", token_ciphertext=b"encrypted", token_nonce=b"nonce")
            observed = Client(node=node, email="observed@example.com", managed_mode="OBSERVE")
            db.add_all([node, observed])
            db.commit()
            observed_id = observed.id

        headers = {"X-CSRF-Token": setup.json()["csrfToken"]}
        blocked = client.post("/api/clients/reset-preview", headers=headers, json={"client_ids": [observed_id]})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "CLIENT_NOT_MANAGED"

        with SessionLocal() as db:
            db.get(Client, observed_id).managed_mode = "MANAGED"
            db.commit()

        preview = client.post("/api/clients/reset-preview", headers=headers, json={"client_ids": [observed_id]})
        assert preview.status_code == 200
        assert preview.json()["clients"] == 1

        with SessionLocal() as db:
            policy = Policy(name="monthly", quota_bytes=1000)
            db.add(policy)
            db.commit()
            policy_id = policy.id

        assigned = client.put(
            f"/api/policies/{policy_id}/node-assignments",
            headers=headers,
            json={"node_ids": [node.id]},
        )
        assert assigned.status_code == 200
        assert assigned.json()["node_ids"] == [node.id]
        policies = client.get("/api/policies")
        assert policies.status_code == 200
        assert policies.json()[0]["node_ids"] == [node.id]
