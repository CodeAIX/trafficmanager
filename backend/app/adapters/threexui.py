import asyncio
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def normalize_base_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Base URL must use http:// or https://")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


@dataclass
class ProbeResult:
    mode: str
    capabilities: dict[str, bool]
    openapi_sha256: str | None
    server_status: dict[str, Any]
    inbounds: list[dict[str, Any]]
    clients: list[dict[str, Any]]


class ThreeXUIAdapter(ABC):
    def __init__(self, base_url: str, token: str, tls_verify: bool = True, transport=None):
        self.base_url = normalize_base_url(base_url)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            verify=tls_verify,
            timeout=httpx.Timeout(30, connect=10),
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        waits = (0, 2, 5)
        for attempt, wait in enumerate(waits):
            if wait:
                await asyncio.sleep(wait)
            try:
                response = await self.client.request(method, path, **kwargs)
            except httpx.ConnectError as exc:
                if attempt < len(waits) - 1:
                    continue
                raise AdapterError("CONNECTION_FAILED", "Unable to connect to node") from exc
            except httpx.TimeoutException as exc:
                if attempt < len(waits) - 1:
                    continue
                raise AdapterError("TIMEOUT", "Node request timed out") from exc
            if response.status_code in (401, 403):
                raise AdapterError("AUTH_FAILED", f"Authentication failed (HTTP {response.status_code})", response.status_code)
            if response.status_code in (502, 503, 504) and attempt < len(waits) - 1:
                continue
            if response.status_code == 404:
                raise AdapterError("API_UNSUPPORTED", f"Endpoint not supported: {path}", 404)
            if response.is_error:
                raise AdapterError("REMOTE_ERROR", f"Remote API returned HTTP {response.status_code}", response.status_code)
            try:
                body = response.json()
            except ValueError as exc:
                raise AdapterError("INVALID_RESPONSE", "Remote API did not return valid JSON") from exc
            if isinstance(body, dict) and body.get("success") is False:
                raise AdapterError("REMOTE_ERROR", str(body.get("msg") or "Remote operation failed"))
            return body.get("obj", body) if isinstance(body, dict) else body
        raise AdapterError("REMOTE_ERROR", "Remote request failed")

    async def get_server_status(self) -> dict[str, Any]:
        for path in ("/panel/api/server/status", "/panel/api/server/getStatus"):
            try:
                return await self._request("GET", path)
            except AdapterError as exc:
                if exc.status_code != 404:
                    raise
        return {}

    @abstractmethod
    async def list_inbounds(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_client(self, email: str, inbound_id: int | None = None) -> dict[str, Any]: ...

    @abstractmethod
    async def reset_client_traffic(self, email: str, inbound_id: int | None = None) -> None: ...

    async def list_clients(self) -> list[dict[str, Any]]:
        clients: list[dict[str, Any]] = []
        for inbound in await self.list_inbounds():
            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                try:
                    settings = json.loads(settings)
                except ValueError:
                    settings = {}
            stats = {s.get("email"): s for s in inbound.get("clientStats", [])}
            for raw in settings.get("clients", []):
                email = raw.get("email")
                if not email:
                    continue
                stat = stats.get(email, {})
                clients.append({
                    "email": email,
                    "comment": raw.get("comment", ""),
                    "enable": raw.get("enable", True),
                    "totalGB": int(raw.get("totalGB", stat.get("total", 0)) or 0),
                    "up": int(stat.get("up", 0) or 0),
                    "down": int(stat.get("down", 0) or 0),
                    "expiryTime": int(raw.get("expiryTime", 0) or 0),
                    "reset": str(raw.get("reset", raw.get("resetMode", "disabled"))),
                    "inbound_id": int(inbound["id"]),
                })
        return clients

    async def verify_client(self, email: str, quota_bytes: int | None, inbound_id: int | None = None) -> dict[str, Any]:
        current = await self.get_client(email, inbound_id)
        used = int(current.get("up", 0) or 0) + int(current.get("down", 0) or 0)
        quota = int(current.get("totalGB", current.get("total", 0)) or 0)
        return {"verified": used <= 1024 and (quota_bytes is None or quota == quota_bytes), "client": current}


class LegacyThreeXUIAdapter(ThreeXUIAdapter):
    async def list_inbounds(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/panel/api/inbounds/list")
        return result if isinstance(result, list) else []

    async def get_client(self, email: str, inbound_id: int | None = None) -> dict[str, Any]:
        for client in await self.list_clients():
            if client["email"] == email and (inbound_id is None or client["inbound_id"] == inbound_id):
                return client
        raise AdapterError("INVALID_RESPONSE", f"Client {email} not found", 404)

    async def _full_inbound(self, inbound_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")

    async def reset_client_traffic(self, email: str, inbound_id: int | None = None) -> None:
        if inbound_id is None:
            raise AdapterError("INVALID_RESPONSE", "Inbound id is required for legacy reset")
        await self._request("POST", f"/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}")


class ModernThreeXUIAdapter(LegacyThreeXUIAdapter):
    @staticmethod
    def _email_path(email: str) -> str:
        return quote(email, safe="")

    async def get_client(self, email: str, inbound_id: int | None = None) -> dict[str, Any]:
        try:
            encoded = self._email_path(email)
            result = await self._request("GET", f"/panel/api/clients/get/{encoded}")
            client = dict(result.get("client", result)) if isinstance(result, dict) else {}
            traffic = await self._request("GET", f"/panel/api/clients/traffic/{encoded}")
            if isinstance(traffic, dict):
                client["up"] = int(traffic.get("up", 0) or 0)
                client["down"] = int(traffic.get("down", 0) or 0)
            return client
        except AdapterError as exc:
            if exc.status_code != 404:
                raise
            return await super().get_client(email, inbound_id)

    async def reset_client_traffic(self, email: str, inbound_id: int | None = None) -> None:
        try:
            await self._request("POST", f"/panel/api/clients/resetTraffic/{self._email_path(email)}")
        except AdapterError as exc:
            if exc.status_code != 404:
                raise
            await super().reset_client_traffic(email, inbound_id)


async def detect_adapter(base_url: str, token: str, tls_verify: bool = True, transport=None) -> tuple[ThreeXUIAdapter, dict[str, bool], str | None]:
    probe = LegacyThreeXUIAdapter(base_url, token, tls_verify, transport)
    try:
        spec = await probe._request("GET", "/panel/api/openapi.json")
        encoded = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
        paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
        modern = any(path.startswith("/panel/api/clients") for path in paths)
        capabilities = {
            "openapi": True,
            "modern_clients_api": modern,
            "reset_single_client": any("resetClientTraffic" in path or "resetTraffic" in path for path in paths),
            "reset_inbound_clients": any("resetAllClientTraffics" in path for path in paths),
            "reset_inbound_total": any("resetAllTraffics" in path for path in paths),
        }
        sha = hashlib.sha256(encoded).hexdigest()
    except AdapterError as exc:
        if exc.status_code != 404:
            await probe.close()
            raise
        modern, sha = False, None
        capabilities = {"openapi": False, "modern_clients_api": False, "reset_single_client": True, "reset_inbound_clients": True, "reset_inbound_total": True}
    await probe.close()
    cls = ModernThreeXUIAdapter if modern else LegacyThreeXUIAdapter
    return cls(base_url, token, tls_verify, transport), capabilities, sha


def adapter_for(mode: str, base_url: str, token: str, tls_verify: bool = True) -> ThreeXUIAdapter:
    return (ModernThreeXUIAdapter if mode == "MODERN" else LegacyThreeXUIAdapter)(base_url, token, tls_verify)
