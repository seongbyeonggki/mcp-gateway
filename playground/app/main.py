import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel

SYSTEM_PROMPT = "You are a helpful assistant. Always respond in Korean (한국어), regardless of the language of the tool results or the user's message."

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:3000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
PLAYGROUND_USER_ID = os.environ.get("PLAYGROUND_USER_ID", "playground")

STATIC_DIR = Path(__file__).parent / "static"

MAX_TOOL_HOPS = 5


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    model: str | None = None


async def get_jwt() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/auth/token", json={"userId": PLAYGROUND_USER_ID}
        )
        resp.raise_for_status()
        return resp.json()["token"]


@asynccontextmanager
async def mcp_session(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(f"{GATEWAY_URL}/mcp", headers=headers) as (
        read,
        write,
        _get_session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def mcp_tool_to_ollama_tool(tool: Any) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


app = FastAPI(title="MCP Gateway Playground")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "playground"}


@app.get("/api/config")
async def config():
    return {"defaultModel": OLLAMA_MODEL}


@app.get("/api/tools")
async def list_tools():
    """List the MCP tools currently exposed by the gateway/mcp-server."""
    try:
        token = await get_jwt()
        async with mcp_session(token) as session:
            result = await session.list_tools()
    except httpx.HTTPError as err:
        raise HTTPException(status_code=502, detail=f"Failed to reach gateway: {err}")

    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
            for t in result.tools
        ]
    }


def make_step(kind: str, title: str, detail: Any, duration_ms: float | None = None) -> dict:
    return {
        "type": kind,
        "title": title,
        "detail": detail,
        "durationMs": round(duration_ms, 1) if duration_ms is not None else None,
    }


def ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def call_ollama(messages: list[dict], tools: list[dict], model: str) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
            },
        )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Ollama model '{model}' not found. Pull it first, e.g.: "
                    f"docker compose exec ollama ollama pull {model}"
                ),
            )
        resp.raise_for_status()
        return resp.json()


async def chat_stream(req: ChatRequest) -> AsyncIterator[bytes]:
    """
    Drive a tool-calling conversation, yielding one NDJSON line per hop of the
    call flow as it happens: Playground -> Gateway (/auth/token) -> Playground
    -> Gateway (/mcp, JWT) -> mcp-server -> Playground -> Ollama (/api/chat)
    -> [tool call] -> ... until the model produces a final answer.
    """
    model = req.model or OLLAMA_MODEL

    t0 = time.perf_counter()
    try:
        token = await get_jwt()
    except httpx.HTTPError as err:
        yield ndjson({"type": "error", "detail": f"Failed to reach gateway: {err}"})
        return
    yield ndjson(
        make_step(
            "auth",
            "Gateway 인증 토큰 발급 (Playground → Gateway)",
            {"request": f"POST {GATEWAY_URL}/auth/token", "response": f"JWT issued (len={len(token)})"},
            (time.perf_counter() - t0) * 1000,
        )
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m.role, "content": m.content} for m in req.history]
    messages.append({"role": "user", "content": req.message})

    t0 = time.perf_counter()
    try:
        async with mcp_session(token) as session:
            yield ndjson(
                make_step(
                    "mcp_connect",
                    "MCP 세션 연결 (Playground → Gateway → mcp-server)",
                    {"request": f"POST {GATEWAY_URL}/mcp (Authorization: Bearer <jwt>)"},
                    (time.perf_counter() - t0) * 1000,
                )
            )

            t0 = time.perf_counter()
            tools_result = await session.list_tools()
            ollama_tools = [mcp_tool_to_ollama_tool(t) for t in tools_result.tools]
            yield ndjson(
                make_step(
                    "mcp_list_tools",
                    "MCP 툴 목록 조회 (tools/list)",
                    {"tools": [t.name for t in tools_result.tools]},
                    (time.perf_counter() - t0) * 1000,
                )
            )

            for hop in range(1, MAX_TOOL_HOPS + 1):
                t0 = time.perf_counter()
                try:
                    data = await call_ollama(messages, ollama_tools, model)
                except HTTPException as err:
                    yield ndjson({"type": "error", "detail": err.detail})
                    return
                duration = (time.perf_counter() - t0) * 1000
                assistant_message = data.get("message", {})
                tool_calls = assistant_message.get("tool_calls") or []

                yield ndjson(
                    make_step(
                        "llm",
                        f"Ollama 추론 #{hop} (Playground → Ollama /api/chat, model={model})",
                        {
                            "messagesSent": len(messages),
                            "toolsOffered": [t["function"]["name"] for t in ollama_tools],
                            "content": assistant_message.get("content", ""),
                            "toolCallsRequested": [
                                {
                                    "name": c.get("function", {}).get("name"),
                                    "arguments": c.get("function", {}).get("arguments"),
                                }
                                for c in tool_calls
                            ],
                        },
                        duration,
                    )
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.get("content", ""),
                        **({"tool_calls": tool_calls} if tool_calls else {}),
                    }
                )

                if not tool_calls:
                    yield ndjson(
                        {"type": "final", "reply": assistant_message.get("content", ""), "model": model}
                    )
                    return

                for call in tool_calls:
                    fn = call.get("function", {})
                    tool_name = fn.get("name")
                    tool_args = fn.get("arguments") or {}

                    t0 = time.perf_counter()
                    try:
                        tool_result = await session.call_tool(tool_name, tool_args)
                        result_text = "\n".join(
                            block.text for block in tool_result.content if hasattr(block, "text")
                        )
                        is_error = tool_result.isError
                    except Exception as err:  # noqa: BLE001 - surface any tool failure to the model
                        result_text = f"Tool error: {err}"
                        is_error = True
                    duration = (time.perf_counter() - t0) * 1000

                    yield ndjson(
                        make_step(
                            "tool_call",
                            f"MCP 툴 호출: {tool_name} (Playground → Gateway → mcp-server)",
                            {"arguments": tool_args, "result": result_text, "isError": is_error},
                            duration,
                        )
                    )
                    messages.append({"role": "tool", "content": result_text})
    except httpx.HTTPError as err:
        yield ndjson({"type": "error", "detail": f"Failed to reach MCP server via gateway: {err}"})
        return

    yield ndjson(
        {
            "type": "final",
            "reply": "Stopped after reaching the maximum number of tool call hops.",
            "model": model,
        }
    )


@app.post("/api/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(chat_stream(req), media_type="application/x-ndjson")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
