import os
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

PORT = int(os.environ.get("PORT", 3000))
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3001")
JWT_SECRET = os.environ["JWT_SECRET"]

HOP_BY_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}

limiter = Limiter(key_func=get_remote_address)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class TokenRequest(BaseModel):
    userId: str


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway", "mcpServerUrl": MCP_SERVER_URL}


@app.post("/auth/token")
async def issue_token(body: TokenRequest):
    payload = {
        "userId": body.userId,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"token": token}


def auth_middleware(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@app.post("/mcp")
@limiter.limit("60/minute")
async def proxy_mcp(request: Request, _auth: None = Depends(auth_middleware)):
    body = await request.body()
    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.post(
                f"{MCP_SERVER_URL}/mcp/", content=body, headers=forward_headers
            )
    except httpx.HTTPError as err:
        print(f"Proxy error: {err}")
        return JSONResponse(
            status_code=502, content={"error": "Bad Gateway: failed to reach MCP server"}
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
