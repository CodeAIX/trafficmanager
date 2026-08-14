from copy import deepcopy
from fastapi import FastAPI, Header, HTTPException


def create_mock(reset_works: bool = True) -> FastAPI:
    app = FastAPI()
    client = {"email": "user@example.com", "id": "secret-not-persisted", "enable": True, "totalGB": 2**40, "up": 1234, "down": 5678, "expiryTime": 0}
    inbound = {"id": 1, "remark": "main", "protocol": "vless", "port": 443, "enable": True, "up": 1234, "down": 5678, "settings": {"clients": [client]}, "clientStats": [client]}

    def auth(authorization: str | None):
        if authorization != "Bearer test-token":
            raise HTTPException(401)

    @app.get("/base/panel/api/openapi.json")
    def openapi(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"paths": {"/panel/api/clients/{email}": {}, "/panel/api/clients/{email}/resetTraffic": {}, "/panel/api/inbounds/list": {}}}

    @app.get("/base/panel/api/inbounds/list")
    def inbounds(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"success": True, "obj": [deepcopy(inbound)]}

    @app.get("/base/panel/api/clients/{email}")
    def get_client(email: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"success": True, "obj": deepcopy(client)}

    @app.post("/base/panel/api/clients/{email}")
    def update_client(email: str, body: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        client.update(body)
        return {"success": True}

    @app.post("/base/panel/api/clients/{email}/resetTraffic")
    def reset(email: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        if reset_works:
            client["up"] = client["down"] = 0
        return {"success": True}

    return app

