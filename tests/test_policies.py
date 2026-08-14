from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.database import Base
from backend.app.models import Client, Inbound, Node, Policy, PolicyAssignment
from backend.app.policies import resolve_effective_policy


def test_precedence_and_inbound_conflict():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        node = Node(name="n", base_url="https://n", token_ciphertext=b"x", token_nonce=b"x")
        client = Client(node=node, email="a@example.com")
        i1, i2 = Inbound(node=node, remote_id=1), Inbound(node=node, remote_id=2)
        client.inbounds = [i1, i2]
        policies = [Policy(name=x) for x in ("global", "node", "inbound1", "inbound2", "client")]
        db.add_all([node, client, *policies]); db.flush()
        db.add_all([PolicyAssignment(policy_id=policies[0].id, scope_type="GLOBAL", scope_id=0), PolicyAssignment(policy_id=policies[1].id, scope_type="NODE", scope_id=node.id), PolicyAssignment(policy_id=policies[2].id, scope_type="INBOUND", scope_id=i1.id), PolicyAssignment(policy_id=policies[3].id, scope_type="INBOUND", scope_id=i2.id)])
        db.flush()
        assert resolve_effective_policy(db, client).conflict
        db.add(PolicyAssignment(policy_id=policies[4].id, scope_type="CLIENT", scope_id=client.id)); db.flush()
        result = resolve_effective_policy(db, client)
        assert result.policy.name == "client" and result.source == "CLIENT"
