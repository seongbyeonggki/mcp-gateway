from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

mcp = FastMCP("mcp-server", stateless_http=True)


@mcp.tool()
def calculate(
    a: float, b: float, op: Literal["add", "subtract", "multiply", "divide"]
) -> str:
    """Perform a basic arithmetic operation on two numbers."""
    if op == "add":
        result = a + b
    elif op == "subtract":
        result = a - b
    elif op == "multiply":
        result = a * b
    else:
        if b == 0:
            raise ValueError("Division by zero is not allowed")
        result = a / b

    if result == int(result):
        result = int(result)
    return str(result)


@mcp.tool()
def get_current_time() -> str:
    """Get the current time in Korea (Asia/Seoul)."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-server"})


app = mcp.streamable_http_app()
app.routes.append(Route("/health", health))
