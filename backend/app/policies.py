from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Client, Policy, PolicyAssignment


@dataclass
class EffectivePolicy:
    policy: Policy | None
    source: str
    conflict: bool = False


def resolve_effective_policy(db: Session, client: Client) -> EffectivePolicy:
    assignments = db.scalars(select(PolicyAssignment)).all()
    by_scope = {(a.scope_type, a.scope_id): a.policy_id for a in assignments}
    selected = by_scope.get(("CLIENT", client.id))
    if selected:
        return EffectivePolicy(db.get(Policy, selected), "CLIENT")
    inbound_ids = {by_scope[("INBOUND", i.id)] for i in client.inbounds if ("INBOUND", i.id) in by_scope}
    if len(inbound_ids) > 1:
        return EffectivePolicy(None, "POLICY_CONFLICT", True)
    if inbound_ids:
        return EffectivePolicy(db.get(Policy, inbound_ids.pop()), "INBOUND")
    selected = by_scope.get(("NODE", client.node_id))
    if selected:
        return EffectivePolicy(db.get(Policy, selected), "NODE")
    selected = by_scope.get(("GLOBAL", 0))
    return EffectivePolicy(db.get(Policy, selected) if selected else None, "GLOBAL" if selected else "NONE")
