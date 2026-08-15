import pytest

from backend.app.database import Base, SessionLocal, engine
from backend.app.models import Client, Inbound, Node
from backend.app.security import encrypt_token
from backend.app.services import create_job, execute_job


class ResetAdapter:
    def __init__(self):
        self.client = {"email": "auto-sync@example.com", "totalGB": 2000, "up": 500, "down": 700}

    async def get_client(self, _email, _inbound_id=None):
        return dict(self.client)

    async def reset_client_traffic(self, _email, _inbound_id=None):
        self.client["up"] = self.client["down"] = 0

    async def verify_client(self, _email, _quota, _inbound_id=None):
        return {"verified": True, "client": dict(self.client)}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_successful_job_updates_local_counters_and_syncs_node(monkeypatch):
    Base.metadata.create_all(engine)
    ciphertext, nonce = encrypt_token("test-token")
    with SessionLocal() as db:
        node = Node(name="auto-sync-node", base_url="https://node.example", token_ciphertext=ciphertext, token_nonce=nonce, api_mode="MODERN")
        inbound = Inbound(node=node, remote_id=1, remark="main")
        client = Client(node=node, email="auto-sync@example.com", managed_mode="MANAGED", upload_bytes=500, download_bytes=700, quota_remote_bytes=2000, inbounds=[inbound])
        db.add_all([node, client])
        db.flush()
        client_id, node_id = client.id, node.id
        job = create_job(db, "RESET_TRAFFIC", "MANUAL", [client.id])
        job_id = job.id

    adapter = ResetAdapter()
    synced = []

    monkeypatch.setattr("backend.app.services.adapter_for", lambda *_args, **_kwargs: adapter)

    async def fake_sync(node_id_to_sync):
        synced.append(node_id_to_sync)
        return {"ok": True}

    monkeypatch.setattr("backend.app.services.sync_node", fake_sync)
    await execute_job(job_id)

    with SessionLocal() as db:
        updated = db.get(Client, client_id)
        assert updated.upload_bytes == 0
        assert updated.download_bytes == 0
        assert updated.quota_remote_bytes == 2000
        assert updated.last_synced_at is not None
    assert synced == [node_id]
