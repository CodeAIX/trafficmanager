from datetime import date, datetime, time
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, utcnow


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    remark: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(2048))
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    token_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    api_mode: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    openapi_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    inbounds: Mapped[list["Inbound"]] = relationship(cascade="all, delete-orphan", back_populates="node")
    clients: Mapped[list["Client"]] = relationship(cascade="all, delete-orphan", back_populates="node")


class Inbound(Base):
    __tablename__ = "inbounds"
    __table_args__ = (UniqueConstraint("node_id", "remote_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[int]
    remark: Mapped[str] = mapped_column(String(255), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="")
    port: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_up: Mapped[int] = mapped_column(BigInteger, default=0)
    last_down: Mapped[int] = mapped_column(BigInteger, default=0)
    raw_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node: Mapped[Node] = relationship(back_populates="inbounds")
    clients: Mapped[list["Client"]] = relationship(secondary="client_inbounds", back_populates="inbounds")


class ClientInbound(Base):
    __tablename__ = "client_inbounds"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True)
    inbound_id: Mapped[int] = mapped_column(ForeignKey("inbounds.id", ondelete="CASCADE"), primary_key=True)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("node_id", "email"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str] = mapped_column(String(255), default="")
    local_remark: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    managed_mode: Mapped[str] = mapped_column(String(16), default="OBSERVE")
    quota_remote_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    download_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    expiry_time: Mapped[int] = mapped_column(BigInteger, default=0)
    remote_reset_mode: Mapped[str] = mapped_column(String(64), default="disabled")
    remote_missing: Mapped[bool] = mapped_column(default=False)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(String(32), default="OK")
    node: Mapped[Node] = relationship(back_populates="clients")
    inbounds: Mapped[list[Inbound]] = relationship(secondary="client_inbounds", back_populates="clients")


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    reset_enabled: Mapped[bool] = mapped_column(default=True)
    monthly_day: Mapped[int] = mapped_column(default=1)
    local_time: Mapped[time] = mapped_column(Time, default=time(0, 0))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    missing_day_policy: Mapped[str] = mapped_column(String(16), default="LAST_DAY")
    catchup_enabled: Mapped[bool] = mapped_column(default=True)
    catchup_max_hours: Mapped[int] = mapped_column(default=168)
    reactivate_mode: Mapped[str] = mapped_column(String(16), default="PRESERVE")
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PolicyAssignment(Base):
    __tablename__ = "policy_assignments"
    __table_args__ = (UniqueConstraint("scope_type", "scope_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[int] = mapped_column(default=0)


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("policy_id", "cycle_key", "type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"))
    cycle_key: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_targets: Mapped[int] = mapped_column(default=0)
    success_count: Mapped[int] = mapped_column(default=0)
    failure_count: Mapped[int] = mapped_column(default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list["JobItem"]] = relationship(cascade="all, delete-orphan")


class JobItem(Base):
    __tablename__ = "job_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    before_quota: Mapped[int | None] = mapped_column(BigInteger)
    before_up: Mapped[int | None] = mapped_column(BigInteger)
    before_down: Mapped[int | None] = mapped_column(BigInteger)
    after_quota: Mapped[int | None] = mapped_column(BigInteger)
    after_up: Mapped[int | None] = mapped_column(BigInteger)
    after_down: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    attempt_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    source: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(255))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id"))


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
