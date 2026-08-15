import asyncio
import hashlib
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .adapters.threexui import AdapterError, normalize_base_url
from .calendar import next_occurrence, validate_timezone
from .config import settings
from .database import Base, engine, get_db, utcnow
from .models import Admin, AuditLog, Client, Inbound, JobItem, JobRun, Node, Policy, PolicyAssignment, WebSession
from .policies import resolve_effective_policy
from .security import encrypt_token, hash_password, new_session, verify_password
from .services import audit, create_job, execute_job, probe_node, scheduler_loop, sync_node

Db = Annotated[Session, Depends(get_db)]
stop_event = asyncio.Event()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    stop_event.clear()
    scheduler = asyncio.create_task(scheduler_loop(stop_event))
    yield
    stop_event.set()
    await scheduler


app = FastAPI(title="TrafficManager", version="1.0.0", lifespan=lifespan)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)


class NodeInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    remark: str = ""
    base_url: str
    token: str = Field(min_length=1)
    tls_verify: bool = True
    tags: list[str] = []

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return normalize_base_url(value)


class PolicyInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    quota_bytes: int | None = Field(default=None, ge=0)
    reset_enabled: bool = True
    monthly_day: int = Field(default=1, ge=1, le=31)
    local_time: time = time(0, 0)
    timezone: str = "UTC"
    missing_day_policy: str = "LAST_DAY"
    catchup_enabled: bool = True
    catchup_max_hours: int = Field(default=168, ge=0)
    reactivate_mode: str = "PRESERVE"
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        validate_timezone(value)
        return value

    @field_validator("missing_day_policy")
    @classmethod
    def valid_missing(cls, value: str) -> str:
        if value not in {"LAST_DAY", "SKIP"}:
            raise ValueError("must be LAST_DAY or SKIP")
        return value

    @field_validator("reactivate_mode")
    @classmethod
    def valid_reactivate(cls, value: str) -> str:
        if value not in {"PRESERVE", "ENABLE"}:
            raise ValueError("must be PRESERVE or ENABLE")
        return value


def session_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def utc_json(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def current_admin(db: Db, trafficmanager_session: str | None = Cookie(default=None)) -> Admin:
    if not trafficmanager_session:
        raise HTTPException(401, "Authentication required")
    session = db.get(WebSession, session_hash(trafficmanager_session))
    if not session or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise HTTPException(401, "Session expired")
    return db.get(Admin, session.admin_id)


AdminDep = Annotated[Admin, Depends(current_admin)]


def require_csrf(request: Request, db: Db, trafficmanager_session: str | None = Cookie(default=None), x_csrf_token: str | None = Header(default=None)) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    session = db.get(WebSession, session_hash(trafficmanager_session or ""))
    if not session or not x_csrf_token or session.csrf_token != x_csrf_token:
        raise HTTPException(403, "Invalid CSRF token")


def node_json(node: Node) -> dict[str, Any]:
    return {"id": node.id, "name": node.name, "remark": node.remark, "base_url": node.base_url, "tokenConfigured": True, "enabled": node.enabled, "tls_verify": node.tls_verify, "tags": node.tags, "status": node.status, "last_seen_at": utc_json(node.last_seen_at), "last_error": node.last_error, "api_mode": node.api_mode, "capabilities": node.capabilities_json, "openapi_sha256": node.openapi_sha256, "inbounds": len(node.inbounds), "clients": len(node.clients)}


def client_json(db: Session, client: Client) -> dict[str, Any]:
    effective = resolve_effective_policy(db, client)
    used = client.upload_bytes + client.download_bytes
    quota = effective.policy.quota_bytes if effective.policy else client.quota_remote_bytes
    return {"id": client.id, "node_id": client.node_id, "node": client.node.name, "email": client.email, "comment": client.comment, "enabled": client.enabled, "managed_mode": client.managed_mode, "inbounds": [{"id": i.id, "remote_id": i.remote_id, "remark": i.remark} for i in client.inbounds], "used_bytes": used, "upload_bytes": client.upload_bytes, "download_bytes": client.download_bytes, "quota_bytes": quota, "percentage": round(used * 100 / quota, 2) if quota else None, "policy": effective.policy.name if effective.policy else None, "policy_source": effective.source, "policy_conflict": effective.conflict, "native_reset_conflict": client.remote_reset_mode.lower() not in {"", "0", "disabled", "none"} and bool(effective.policy and effective.policy.reset_enabled), "remote_missing": client.remote_missing, "last_synced_at": utc_json(client.last_synced_at)}


@app.get("/health")
def health(db: Db):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "scheduler": "ok"}


@app.get("/auth/status")
def auth_status(db: Db, trafficmanager_session: str | None = Cookie(default=None)):
    initialized = bool(db.scalar(select(func.count()).select_from(Admin)))
    authenticated, csrf = False, None
    if trafficmanager_session:
        session = db.get(WebSession, session_hash(trafficmanager_session))
        authenticated = bool(session and session.expires_at.replace(tzinfo=timezone.utc) > utcnow())
        csrf = session.csrf_token if authenticated else None
    return {"initialized": initialized, "authenticated": authenticated, "csrfToken": csrf}


@app.post("/auth/setup", status_code=201)
def setup(body: Credentials, response: Response, db: Db):
    if db.scalar(select(func.count()).select_from(Admin)):
        raise HTTPException(409, "Administrator already exists")
    admin = Admin(username=body.username, password_hash=hash_password(body.password))
    db.add(admin)
    db.flush()
    raw, hashed, csrf, expires = new_session()
    db.add(WebSession(id_hash=hashed, admin_id=admin.id, csrf_token=csrf, expires_at=expires))
    db.commit()
    response.set_cookie("trafficmanager_session", raw, httponly=True, secure=settings.session_secure, samesite="strict", max_age=settings.session_timeout_minutes * 60)
    return {"username": admin.username, "csrfToken": csrf}


@app.post("/auth/login")
def login(body: Credentials, response: Response, db: Db):
    admin = db.scalar(select(Admin).where(Admin.username == body.username))
    if not admin or not verify_password(admin.password_hash, body.password):
        raise HTTPException(401, "Invalid username or password")
    raw, hashed, csrf, expires = new_session()
    db.add(WebSession(id_hash=hashed, admin_id=admin.id, csrf_token=csrf, expires_at=expires))
    db.commit()
    response.set_cookie("trafficmanager_session", raw, httponly=True, secure=settings.session_secure, samesite="strict", max_age=settings.session_timeout_minutes * 60)
    return {"username": admin.username, "csrfToken": csrf}


@app.post("/auth/logout", dependencies=[Depends(require_csrf)])
def logout(response: Response, db: Db, _admin: AdminDep, trafficmanager_session: str | None = Cookie(default=None)):
    if trafficmanager_session:
        session = db.get(WebSession, session_hash(trafficmanager_session))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie("trafficmanager_session")
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(db: Db, _admin: AdminDep):
    nodes = db.scalars(select(Node)).all()
    clients = db.scalars(select(Client).where(Client.remote_missing.is_(False))).all()
    data = [client_json(db, client) for client in clients]
    failed = db.scalar(select(func.count()).select_from(JobRun).where(JobRun.status.in_(["FAILED", "PARTIAL"])))
    return {"nodes": len(nodes), "online_nodes": sum(n.status == "ONLINE" for n in nodes), "offline_nodes": sum(n.status == "OFFLINE" for n in nodes), "clients": len(clients), "managed_clients": sum(c.managed_mode == "MANAGED" for c in clients), "observe_clients": sum(c.managed_mode == "OBSERVE" for c in clients), "policy_conflicts": sum(c["policy_conflict"] for c in data), "total_traffic": sum(c["used_bytes"] for c in data), "near_quota_clients": sum(c["percentage"] is not None and c["percentage"] >= 80 for c in data), "failed_jobs": failed or 0}


@app.post("/api/nodes/test", dependencies=[Depends(require_csrf)])
async def test_node(body: NodeInput, _admin: AdminDep):
    try:
        return await probe_node(body.base_url, body.token, body.tls_verify)
    except AdapterError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/nodes")
def list_nodes(db: Db, _admin: AdminDep):
    return [node_json(n) for n in db.scalars(select(Node).order_by(Node.name)).all()]


@app.post("/api/nodes", status_code=201, dependencies=[Depends(require_csrf)])
async def add_node(body: NodeInput, background: BackgroundTasks, db: Db, admin: AdminDep):
    try:
        probe = await probe_node(body.base_url, body.token, body.tls_verify)
    except AdapterError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from exc
    ciphertext, nonce = encrypt_token(body.token)
    node = Node(name=body.name, remark=body.remark, base_url=body.base_url, token_ciphertext=ciphertext, token_nonce=nonce, tls_verify=body.tls_verify, tags=body.tags, status="ONLINE", api_mode=probe["api_mode"], capabilities_json=probe["capabilities"], openapi_sha256=probe["openapi_sha256"], last_seen_at=utcnow())
    db.add(node)
    audit(db, "ADD_NODE", "NODE", body.name, "SUCCESS", source="MANUAL", actor=admin.username, after={"base_url": body.base_url, "tls_verify": body.tls_verify})
    db.commit()
    background.add_task(sync_node, node.id)
    return node_json(node)


@app.get("/api/nodes/{node_id}")
def get_node(node_id: int, db: Db, _admin: AdminDep):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return node_json(node)


@app.post("/api/nodes/{node_id}/sync", dependencies=[Depends(require_csrf)])
async def run_sync(node_id: int, db: Db, _admin: AdminDep):
    if not db.get(Node, node_id):
        raise HTTPException(404, "Node not found")
    try:
        return await sync_node(node_id)
    except AdapterError as exc:
        raise HTTPException(502, {"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/clients")
def list_clients(db: Db, _admin: AdminDep, search: str | None = None, node_id: int | None = None, managed_mode: str | None = None):
    query = select(Client).where(Client.remote_missing.is_(False))
    if search:
        query = query.where(Client.email.contains(search))
    if node_id:
        query = query.where(Client.node_id == node_id)
    if managed_mode:
        query = query.where(Client.managed_mode == managed_mode.upper())
    return [client_json(db, c) for c in db.scalars(query.order_by(Client.email)).unique().all()]


@app.patch("/api/clients/{client_id}", dependencies=[Depends(require_csrf)])
def update_client(client_id: int, body: dict, db: Db, admin: AdminDep):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    if "managed_mode" in body:
        mode = str(body["managed_mode"]).upper()
        if mode not in {"MANAGED", "OBSERVE", "IGNORE"}:
            raise HTTPException(422, "Invalid managed mode")
        before, client.managed_mode = client.managed_mode, mode
        audit(db, "SET_MANAGED_MODE", "CLIENT", f"{client.node.name}/{client.email}", "SUCCESS", source="MANUAL", actor=admin.username, before={"managed_mode": before}, after={"managed_mode": mode})
    if "policy_id" in body:
        existing = db.scalar(select(PolicyAssignment).where(PolicyAssignment.scope_type == "CLIENT", PolicyAssignment.scope_id == client.id))
        if body["policy_id"] is None and existing:
            db.delete(existing)
        elif body["policy_id"] is not None:
            if not db.get(Policy, int(body["policy_id"])):
                raise HTTPException(422, "Policy not found")
            if existing:
                existing.policy_id = int(body["policy_id"])
            else:
                db.add(PolicyAssignment(policy_id=int(body["policy_id"]), scope_type="CLIENT", scope_id=client.id))
    db.commit()
    return client_json(db, client)


class JobRequest(BaseModel):
    client_ids: list[int] = []
    start_new_cycle: bool = False


def require_managed_clients(db: Session, client_ids: list[int]) -> list[Client]:
    requested_ids = set(client_ids)
    if not requested_ids:
        raise HTTPException(422, {"code": "NO_CLIENTS_SELECTED", "message": "Select at least one client"})
    clients = db.scalars(select(Client).where(Client.id.in_(requested_ids))).all()
    found_ids = {client.id for client in clients}
    missing_ids = sorted(requested_ids - found_ids)
    if missing_ids:
        raise HTTPException(404, {"code": "CLIENT_NOT_FOUND", "message": "One or more selected clients no longer exist", "client_ids": missing_ids})
    blocked = [client for client in clients if client.managed_mode != "MANAGED" or client.remote_missing]
    if blocked:
        raise HTTPException(409, {
            "code": "CLIENT_NOT_MANAGED",
            "message": "Only Managed clients can be reset. Change Mode to MANAGED first.",
            "clients": [{"id": client.id, "email": client.email, "mode": client.managed_mode} for client in blocked],
        })
    return clients


@app.post("/api/clients/reset-preview", dependencies=[Depends(require_csrf)])
def reset_preview(body: JobRequest, db: Db, _admin: AdminDep):
    clients = require_managed_clients(db, body.client_ids)
    return {"nodes": len({c.node_id for c in clients}), "inbounds": len({i.id for c in clients for i in c.inbounds}), "clients": len(clients), "quota_change": body.start_new_cycle, "inbound_aggregate_counters": False}


@app.post("/api/clients/bulk-reset", status_code=202, dependencies=[Depends(require_csrf)])
def bulk_reset(body: JobRequest, background: BackgroundTasks, db: Db, admin: AdminDep):
    if body.client_ids:
        clients = require_managed_clients(db, body.client_ids)
    else:
        clients = db.scalars(select(Client).where(Client.managed_mode == "MANAGED", Client.remote_missing.is_(False))).all()
        if not clients:
            raise HTTPException(409, {"code": "NO_MANAGED_CLIENTS", "message": "No Managed clients are available to reset"})
    job = create_job(db, "MONTHLY_CYCLE" if body.start_new_cycle else "RESET_TRAFFIC", "MANUAL", [c.id for c in clients])
    audit(db, "CREATE_JOB", "JOB", str(job.id), "PENDING", source="MANUAL", actor=admin.username, job_id=job.id)
    db.commit()
    background.add_task(execute_job, job.id)
    return {"job_id": job.id, "targets": len(clients)}


@app.post("/api/clients/{client_id}/reset", status_code=202, dependencies=[Depends(require_csrf)])
def reset_client(client_id: int, background: BackgroundTasks, db: Db, _admin: AdminDep):
    client = db.get(Client, client_id)
    if not client or client.managed_mode != "MANAGED":
        raise HTTPException(409, "Client must be Managed")
    job = create_job(db, "RESET_TRAFFIC", "MANUAL", [client.id])
    background.add_task(execute_job, job.id)
    return {"job_id": job.id}


def policy_json(db: Session, policy: Policy) -> dict:
    node_ids = db.scalars(select(PolicyAssignment.scope_id).where(PolicyAssignment.policy_id == policy.id, PolicyAssignment.scope_type == "NODE")).all()
    return {"id": policy.id, "name": policy.name, "description": policy.description, "quota_bytes": policy.quota_bytes, "reset_enabled": policy.reset_enabled, "monthly_day": policy.monthly_day, "local_time": policy.local_time.strftime("%H:%M"), "timezone": policy.timezone, "missing_day_policy": policy.missing_day_policy, "catchup_enabled": policy.catchup_enabled, "catchup_max_hours": policy.catchup_max_hours, "reactivate_mode": policy.reactivate_mode, "enabled": policy.enabled, "next_run_at": utc_json(policy.next_run_at), "node_ids": node_ids}


@app.get("/api/policies")
def list_policies(db: Db, _admin: AdminDep):
    return [policy_json(db, p) for p in db.scalars(select(Policy).order_by(Policy.name)).all()]


@app.post("/api/policies", status_code=201, dependencies=[Depends(require_csrf)])
def add_policy(body: PolicyInput, db: Db, admin: AdminDep):
    policy = Policy(**body.model_dump())
    if policy.enabled and policy.reset_enabled:
        policy.next_run_at = next_occurrence(utcnow(), policy.monthly_day, policy.local_time, policy.timezone, policy.missing_day_policy)
    db.add(policy)
    db.flush()
    audit(db, "CREATE_POLICY", "POLICY", policy.name, "SUCCESS", source="MANUAL", actor=admin.username)
    db.commit()
    return policy_json(db, policy)


@app.put("/api/policies/{policy_id}", dependencies=[Depends(require_csrf)])
def update_policy(policy_id: int, body: PolicyInput, db: Db, admin: AdminDep):
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(404, "Policy not found")
    for key, value in body.model_dump().items():
        setattr(policy, key, value)
    policy.next_run_at = next_occurrence(utcnow(), policy.monthly_day, policy.local_time, policy.timezone, policy.missing_day_policy) if policy.enabled and policy.reset_enabled else None
    audit(db, "UPDATE_POLICY", "POLICY", policy.name, "SUCCESS", source="MANUAL", actor=admin.username)
    db.commit()
    return policy_json(db, policy)


class NodeAssignments(BaseModel):
    node_ids: list[int] = []


@app.put("/api/policies/{policy_id}/node-assignments", dependencies=[Depends(require_csrf)])
def replace_node_assignments(policy_id: int, body: NodeAssignments, db: Db, admin: AdminDep):
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(404, "Policy not found")
    requested = set(body.node_ids)
    found = set(db.scalars(select(Node.id).where(Node.id.in_(requested))).all()) if requested else set()
    if missing := sorted(requested - found):
        raise HTTPException(404, {"code": "NODE_NOT_FOUND", "message": "One or more nodes do not exist", "node_ids": missing})
    current = db.scalars(select(PolicyAssignment).where(PolicyAssignment.scope_type == "NODE", PolicyAssignment.policy_id == policy.id)).all()
    for assignment in current:
        if assignment.scope_id not in requested:
            db.delete(assignment)
    for node_id in requested:
        assignment = db.scalar(select(PolicyAssignment).where(PolicyAssignment.scope_type == "NODE", PolicyAssignment.scope_id == node_id))
        if assignment:
            assignment.policy_id = policy.id
        else:
            db.add(PolicyAssignment(policy_id=policy.id, scope_type="NODE", scope_id=node_id))
    audit(db, "ASSIGN_POLICY_NODES", "POLICY", policy.name, "SUCCESS", source="MANUAL", actor=admin.username, after={"node_ids": sorted(requested)})
    db.commit()
    return policy_json(db, policy)


@app.post("/api/policies/{policy_id}/assign", dependencies=[Depends(require_csrf)])
def assign_policy(policy_id: int, body: dict, db: Db, _admin: AdminDep):
    if not db.get(Policy, policy_id):
        raise HTTPException(404, "Policy not found")
    scope_type, scope_id = str(body.get("scope_type", "")).upper(), int(body.get("scope_id", 0))
    if scope_type not in {"GLOBAL", "NODE", "INBOUND", "CLIENT"}:
        raise HTTPException(422, "Invalid scope")
    existing = db.scalar(select(PolicyAssignment).where(PolicyAssignment.scope_type == scope_type, PolicyAssignment.scope_id == scope_id))
    if existing:
        existing.policy_id = policy_id
    else:
        db.add(PolicyAssignment(policy_id=policy_id, scope_type=scope_type, scope_id=scope_id))
    db.commit()
    return {"ok": True}


def job_json(job: JobRun, detail: bool = False) -> dict:
    result = {"id": job.id, "type": job.type, "source": job.source, "policy_id": job.policy_id, "cycle_key": job.cycle_key, "status": job.status, "created_at": utc_json(job.created_at), "started_at": utc_json(job.started_at), "finished_at": utc_json(job.finished_at), "total_targets": job.total_targets, "success_count": job.success_count, "failure_count": job.failure_count, "summary": job.summary}
    if detail:
        result["items"] = [{"id": i.id, "node_id": i.node_id, "client_id": i.client_id, "status": i.status, "attempt_count": i.attempt_count, "error": i.error, "before": {"quota": i.before_quota, "up": i.before_up, "down": i.before_down}, "after": {"quota": i.after_quota, "up": i.after_up, "down": i.after_down}} for i in job.items]
    return result


@app.get("/api/jobs")
def list_jobs(db: Db, _admin: AdminDep):
    return [job_json(j) for j in db.scalars(select(JobRun).order_by(JobRun.created_at.desc()).limit(200)).all()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Db, _admin: AdminDep):
    job = db.get(JobRun, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job_json(job, True)


@app.post("/api/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(require_csrf)])
def retry_job(job_id: int, background: BackgroundTasks, db: Db, _admin: AdminDep):
    old = db.get(JobRun, job_id)
    if not old:
        raise HTTPException(404, "Job not found")
    failed_ids = [i.client_id for i in old.items if i.status != "SUCCESS"]
    job = create_job(db, old.type, "RETRY", failed_ids)
    background.add_task(execute_job, job.id)
    return {"job_id": job.id, "targets": len(failed_ids)}


@app.get("/api/audit")
def list_audit(db: Db, _admin: AdminDep):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(500)).all()
    return [{"id": r.id, "timestamp": utc_json(r.timestamp), "actor": r.actor, "source": r.source, "action": r.action, "scope": r.scope, "target": r.target, "before": r.before_json, "after": r.after_json, "result": r.result, "job_id": r.job_id} for r in rows]


@app.get("/api/settings")
def get_settings(_admin: AdminDep):
    return {"sync_interval_minutes": settings.sync_interval_minutes, "default_ui_timezone": settings.app_timezone, "global_concurrency": settings.global_concurrency, "per_node_concurrency": settings.per_node_concurrency, "network_retries": settings.network_retries, "verify_retries": settings.verify_retries, "session_timeout_minutes": settings.session_timeout_minutes}


@app.get("/api/settings/backup")
def backup(db: Db, _admin: AdminDep):
    db.execute(text("PRAGMA wal_checkpoint(FULL)"))
    source = settings.data_dir / "app.db"
    target = settings.data_dir / "trafficmanager-backup.db"
    shutil.copy2(source, target)
    return FileResponse(target, filename=f"trafficmanager-{datetime.now():%Y%m%d-%H%M%S}.db")


static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = static_dir / path
        response_path = candidate if candidate.is_file() else static_dir / "index.html"
        headers = {"Cache-Control": "no-cache"} if response_path.name == "index.html" else None
        return FileResponse(response_path, headers=headers)
