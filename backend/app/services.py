import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .adapters import AdapterError, adapter_for
from .adapters.threexui import detect_adapter
from .calendar import cycle_key, next_occurrence
from .database import SessionLocal, utcnow
from .models import AuditLog, Client, Inbound, JobItem, JobRun, Node, Policy
from .policies import resolve_effective_policy
from .security import decrypt_token

client_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def audit(db: Session, action: str, scope: str, target: str, result: str, *, source: str = "SYSTEM", actor: str = "system", before=None, after=None, job_id=None) -> None:
    db.add(AuditLog(actor=actor, source=source, action=action, scope=scope, target=target, before_json=before, after_json=after, result=result, job_id=job_id))


async def probe_node(base_url: str, token: str, tls_verify: bool) -> dict:
    adapter, capabilities, sha = await detect_adapter(base_url, token, tls_verify)
    try:
        status, inbounds, clients = await asyncio.gather(adapter.get_server_status(), adapter.list_inbounds(), adapter.list_clients())
        return {
            "api_mode": "MODERN" if capabilities["modern_clients_api"] else "LEGACY",
            "capabilities": capabilities,
            "openapi_sha256": sha,
            "server_status": status,
            "inbounds": len(inbounds),
            "clients": len({c["email"] for c in clients}),
        }
    finally:
        await adapter.close()


async def sync_node(node_id: int) -> dict:
    with SessionLocal() as db:
        node = db.get(Node, node_id)
        if not node:
            raise ValueError("Node not found")
        token = decrypt_token(node.token_ciphertext, node.token_nonce)
        adapter = adapter_for(node.api_mode, node.base_url, token, node.tls_verify)
        try:
            remote_inbounds = await adapter.list_inbounds()
            remote_clients = await adapter.list_clients()
            now = utcnow()
            inbound_map: dict[int, Inbound] = {}
            for raw in remote_inbounds:
                remote_id = int(raw["id"])
                inbound = db.scalar(select(Inbound).where(Inbound.node_id == node.id, Inbound.remote_id == remote_id))
                if inbound is None:
                    inbound = Inbound(node_id=node.id, remote_id=remote_id)
                    db.add(inbound)
                inbound.remark = str(raw.get("remark", ""))
                inbound.protocol = str(raw.get("protocol", ""))
                inbound.port = int(raw.get("port", 0) or 0)
                inbound.enabled = bool(raw.get("enable", True))
                inbound.last_up = int(raw.get("up", 0) or 0)
                inbound.last_down = int(raw.get("down", 0) or 0)
                inbound.raw_metadata_json = {k: raw.get(k) for k in ("id", "remark", "protocol", "port", "enable")}
                inbound.last_synced_at = now
                db.flush()
                inbound_map[remote_id] = inbound
            seen: set[str] = set()
            grouped: dict[str, list[dict]] = defaultdict(list)
            for raw in remote_clients:
                grouped[str(raw["email"])].append(raw)
            for email, copies in grouped.items():
                seen.add(email)
                client = db.scalar(select(Client).where(Client.node_id == node.id, Client.email == email))
                if client is None:
                    client = Client(node_id=node.id, email=email, managed_mode="OBSERVE")
                    db.add(client)
                latest = copies[0]
                client.comment = str(latest.get("comment", ""))
                client.enabled = bool(latest.get("enable", True))
                client.quota_remote_bytes = max(int(c.get("totalGB", 0) or 0) for c in copies)
                client.upload_bytes = sum(int(c.get("up", 0) or 0) for c in copies)
                client.download_bytes = sum(int(c.get("down", 0) or 0) for c in copies)
                client.expiry_time = int(latest.get("expiryTime", 0) or 0)
                client.remote_reset_mode = str(latest.get("reset", "disabled"))
                client.remote_missing = False
                client.missing_since = None
                client.last_synced_at = now
                client.sync_status = "OK"
                client.inbounds = [inbound_map[int(c["inbound_id"])] for c in copies if int(c["inbound_id"]) in inbound_map]
            for local in db.scalars(select(Client).where(Client.node_id == node.id)).all():
                if local.email not in seen and not local.remote_missing:
                    local.remote_missing, local.missing_since, local.sync_status = True, now, "REMOTE_MISSING"
            node.status, node.last_seen_at, node.last_error = "ONLINE", now, None
            audit(db, "SYNC_NODE", "NODE", node.name, "SUCCESS", after={"inbounds": len(remote_inbounds), "clients": len(seen)})
            db.commit()
            return {"inbounds": len(remote_inbounds), "clients": len(seen)}
        except Exception as exc:
            node.status, node.last_error = "OFFLINE", str(exc)
            audit(db, "SYNC_NODE", "NODE", node.name, "FAILED")
            db.commit()
            raise
        finally:
            await adapter.close()


def create_job(db: Session, job_type: str, source: str, client_ids: list[int], policy_id: int | None = None, cycle: str | None = None) -> JobRun:
    job = JobRun(type=job_type, source=source, policy_id=policy_id, cycle_key=cycle, total_targets=len(client_ids))
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError("This policy cycle job already exists")
    for client_id in client_ids:
        client = db.get(Client, client_id)
        if client:
            db.add(JobItem(job_id=job.id, node_id=client.node_id, client_id=client.id))
    db.commit()
    return job


async def sync_client_quota(client_id: int, actor: str = "system") -> dict:
    node_id = 0
    result: dict = {}
    with SessionLocal() as db:
        client = db.get(Client, client_id)
        if not client or client.remote_missing:
            raise AdapterError("CLIENT_NOT_FOUND", "Client is missing from the source node")
        if client.managed_mode != "MANAGED":
            raise AdapterError("CLIENT_NOT_MANAGED", "Client must be Managed before syncing quota")
        effective = resolve_effective_policy(db, client)
        if effective.conflict:
            raise AdapterError("POLICY_CONFLICT", "Client has conflicting policies")
        if not effective.policy:
            raise AdapterError("NO_EFFECTIVE_POLICY", "Client has no effective policy")
        desired_quota = int(effective.policy.quota_bytes or 0)
        node = db.get(Node, client.node_id)
        node_id = node.id
        node_name, email = node.name, client.email
        inbound_ids = sorted({inbound.remote_id for inbound in client.inbounds})
        operation_inbounds = [None] if node.api_mode == "MODERN" else (inbound_ids or [None])
        adapter = adapter_for(node.api_mode, node.base_url, decrypt_token(node.token_ciphertext, node.token_nonce), node.tls_verify)
        key = f"{client.node_id}:{client.email}"
        try:
            async with client_locks[key]:
                before_clients = [await adapter.get_client(email, inbound_id) for inbound_id in operation_inbounds]
                before_quotas = [int(item.get("totalGB", item.get("total", 0)) or 0) for item in before_clients]
                for inbound_id, before_quota in zip(operation_inbounds, before_quotas, strict=True):
                    if before_quota != desired_quota:
                        await adapter.update_client_quota(email, desired_quota, inbound_id)
                verified_clients: list[dict] = []
                for wait in (0.05, 2, 5):
                    await asyncio.sleep(wait)
                    verified_clients = [await adapter.get_client(email, inbound_id) for inbound_id in operation_inbounds]
                    if all(int(item.get("totalGB", item.get("total", 0)) or 0) == desired_quota for item in verified_clients):
                        break
                else:
                    raise AdapterError("VERIFY_FAILED", "Quota did not match after synchronization")
                client.quota_remote_bytes = desired_quota
                client.last_synced_at = utcnow()
                client.sync_status = "OK"
                result = {"client_id": client.id, "quota_bytes": desired_quota, "before_quota_bytes": max(before_quotas, default=0)}
                audit(db, "SYNC_CLIENT_QUOTA", "CLIENT", f"{node_name}/{email}", "SUCCESS", source="MANUAL", actor=actor, before={"quota": result["before_quota_bytes"]}, after={"quota": desired_quota})
                db.commit()
        except Exception as exc:
            client.sync_status = "QUOTA_SYNC_FAILED"
            audit(db, "SYNC_CLIENT_QUOTA", "CLIENT", f"{node_name}/{email}", exc.code if isinstance(exc, AdapterError) else "FAILED", source="MANUAL", actor=actor)
            db.commit()
            raise
        finally:
            await adapter.close()
    try:
        await sync_node(node_id)
    except Exception:
        pass
    return result


async def _execute_item(job_id: int, item_id: int) -> bool:
    with SessionLocal() as db:
        job, item = db.get(JobRun, job_id), db.get(JobItem, item_id)
        client = db.get(Client, item.client_id) if item else None
        if not job or not item or not client:
            return False
        key = f"{client.node_id}:{client.email}"
        async with client_locks[key]:
            item.started_at, item.status, item.attempt_count = utcnow(), "RUNNING", item.attempt_count + 1
            db.commit()
            node = db.get(Node, client.node_id)
            effective = resolve_effective_policy(db, client)
            desired_quota = int(effective.policy.quota_bytes or 0) if job.type == "MONTHLY_CYCLE" and effective.policy else None
            inbound_ids = [i.remote_id for i in client.inbounds]
            adapter = adapter_for(node.api_mode, node.base_url, decrypt_token(node.token_ciphertext, node.token_nonce), node.tls_verify)
            try:
                before = await adapter.get_client(client.email, inbound_ids[0] if inbound_ids else None)
                item.before_quota = int(before.get("totalGB", before.get("total", 0)) or 0)
                item.before_up, item.before_down = int(before.get("up", 0) or 0), int(before.get("down", 0) or 0)
                operation_inbounds = [None] if node.api_mode == "MODERN" else (inbound_ids or [None])
                if desired_quota is not None and item.before_quota != desired_quota:
                    for inbound_id in operation_inbounds:
                        await adapter.update_client_quota(client.email, desired_quota, inbound_id)
                for inbound_id in operation_inbounds:
                    await adapter.reset_client_traffic(client.email, inbound_id)
                verified = None
                for wait in (0.05, 2, 5):
                    await asyncio.sleep(wait)
                    verified = await adapter.verify_client(client.email, desired_quota, inbound_ids[0] if inbound_ids else None)
                    if verified["verified"]:
                        break
                after = verified["client"]
                item.after_quota = int(after.get("totalGB", after.get("total", 0)) or 0)
                item.after_up, item.after_down = int(after.get("up", 0) or 0), int(after.get("down", 0) or 0)
                if not verified["verified"]:
                    raise AdapterError("VERIFY_FAILED", "Traffic or quota did not match after verification")
                client.quota_remote_bytes = item.after_quota
                client.upload_bytes = item.after_up
                client.download_bytes = item.after_down
                client.last_synced_at = utcnow()
                client.sync_status = "OK"
                item.status = "SUCCESS"
                audit(db, job.type, "CLIENT", f"{node.name}/{client.email}", "SUCCESS", source=job.source, before={"quota": item.before_quota, "up": item.before_up, "down": item.before_down}, after={"quota": item.after_quota, "up": item.after_up, "down": item.after_down}, job_id=job.id)
                return True
            except Exception as exc:
                item.status = exc.code if isinstance(exc, AdapterError) else "FAILED"
                item.error = str(exc)
                audit(db, job.type, "CLIENT", f"{node.name}/{client.email}", item.status, source=job.source, job_id=job.id)
                return False
            finally:
                item.finished_at = utcnow()
                db.commit()
                await adapter.close()


async def execute_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(JobRun, job_id)
        if not job:
            return
        job.status, job.started_at = "RUNNING", utcnow()
        item_ids = [i.id for i in job.items if i.status != "SUCCESS"]
        db.commit()
    results = await asyncio.gather(*(_execute_item(job_id, item_id) for item_id in item_ids), return_exceptions=True)
    success = sum(r is True for r in results)
    with SessionLocal() as db:
        job = db.get(JobRun, job_id)
        all_success = len([i for i in job.items if i.status == "SUCCESS"])
        failed = len(job.items) - all_success
        node_ids = {item.node_id for item in job.items}
        job.success_count, job.failure_count, job.finished_at = all_success, failed, utcnow()
        job.status = "SUCCESS" if failed == 0 else ("PARTIAL" if all_success else "FAILED")
        job.summary = f"{all_success} succeeded, {failed} failed"
        db.commit()
    if node_ids:
        await asyncio.gather(*(sync_node(node_id) for node_id in node_ids), return_exceptions=True)


async def scheduler_tick() -> None:
    now = datetime.now(timezone.utc)
    to_run: list[int] = []
    with SessionLocal() as db:
        policies = db.scalars(select(Policy).where(Policy.enabled.is_(True), Policy.reset_enabled.is_(True))).all()
        for policy in policies:
            if policy.next_run_at is None:
                policy.next_run_at = next_occurrence(now, policy.monthly_day, policy.local_time, policy.timezone, policy.missing_day_policy)
                continue
            due = policy.next_run_at.replace(tzinfo=timezone.utc) if policy.next_run_at.tzinfo is None else policy.next_run_at
            if due <= now:
                lateness = (now - due).total_seconds() / 3600
                targets = [c.id for c in db.scalars(select(Client).where(Client.managed_mode == "MANAGED", Client.remote_missing.is_(False))).all() if (r := resolve_effective_policy(db, c)).policy and r.policy.id == policy.id and not r.conflict]
                if policy.catchup_enabled and lateness <= policy.catchup_max_hours:
                    try:
                        job = create_job(db, "MONTHLY_CYCLE", "SCHEDULED", targets, policy.id, cycle_key(due, policy.timezone))
                        to_run.append(job.id)
                    except ValueError:
                        pass
                else:
                    missed = JobRun(type="MONTHLY_CYCLE", source="SCHEDULED", policy_id=policy.id, cycle_key=cycle_key(due, policy.timezone), status="MISSED", summary="Outside catch-up window")
                    db.add(missed)
                policy.next_run_at = next_occurrence(now, policy.monthly_day, policy.local_time, policy.timezone, policy.missing_day_policy)
        db.commit()
    for job_id in to_run:
        asyncio.create_task(execute_job(job_id))


async def scheduler_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await scheduler_tick()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            continue
