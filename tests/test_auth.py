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
            observed = Client(node=node, email="observed@example.com", local_remark="Zulu", managed_mode="OBSERVE", quota_remote_bytes=4096)
            alphabetic = Client(node=node, email="alphabetic@example.com", local_remark="Alpha", managed_mode="OBSERVE")
            db.add_all([node, observed, alphabetic])
            db.commit()
            observed_id = observed.id

        ordered_clients = client.get("/api/clients")
        assert ordered_clients.status_code == 200
        assert [item["local_remark"] for item in ordered_clients.json()[:2]] == ["Alpha", "Zulu"]

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
        in_use = client.delete(f"/api/policies/{policy_id}", headers=headers)
        assert in_use.status_code == 409
        assert in_use.json()["detail"]["code"] == "POLICY_IN_USE"
        policies = client.get("/api/policies")
        assert policies.status_code == 200
        assert policies.json()[0]["node_ids"] == [node.id]
        assert "quota_bytes" not in policies.json()[0]

        renamed = client.put(
            f"/api/policies/{policy_id}",
            headers=headers,
            json={
                "name": "monthly-renamed",
                "reset_enabled": True,
                "monthly_day": 1,
                "local_time": "00:00",
                "timezone": "UTC",
                "enabled": True,
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "monthly-renamed"

        updated = client.patch(
            f"/api/clients/{observed_id}",
            headers=headers,
            json={"local_remark": "原始节点：荷兰 VPS", "policy_id": policy_id},
        )
        assert updated.status_code == 200
        assert updated.json()["local_remark"] == "原始节点：荷兰 VPS"
        assert updated.json()["assigned_policy_id"] == policy_id
        assert updated.json()["policy_source"] == "CLIENT"
        assert updated.json()["quota_bytes"] == 4096

        dedicated = client.put(
            f"/api/clients/{observed_id}/dedicated-policy",
            headers=headers,
            json={
                "reset_enabled": True,
                "monthly_day": 15,
                "local_time": "08:30",
                "timezone": "Asia/Shanghai",
            },
        )
        assert dedicated.status_code == 200
        dedicated_policy_id = dedicated.json()["assigned_policy_id"]
        assert dedicated_policy_id != policy_id
        assert "quota_bytes" not in dedicated.json()["policy_config"]
        assert dedicated.json()["quota_bytes"] == 4096
        assert dedicated.json()["policy_config"]["monthly_day"] == 15
        assert dedicated.json()["policy_config"]["local_time"] == "08:30"

        inherited = client.patch(
            f"/api/clients/{observed_id}",
            headers=headers,
            json={"policy_id": None},
        )
        assert inherited.status_code == 200
        assert inherited.json()["assigned_policy_id"] is None
        assert inherited.json()["policy_source"] == "NODE"

        deleted = client.delete(f"/api/policies/{dedicated_policy_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}
        assert dedicated_policy_id not in {item["id"] for item in client.get("/api/policies").json()}

        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["version"] == "1.0.0"
