import os
from dotenv import load_dotenv
import httpx

from mcp.server.fastmcp import FastMCP

# Load .env from project root
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ROOT, ".env"))

FUSION_URL = os.getenv("FUSION_BRIDGE_URL", "http://127.0.0.1:18080")
FUSION_TOKEN = os.getenv("FUSION_BRIDGE_TOKEN", "")

mcp = FastMCP("fusion-mcp")

async def _fusion_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{FUSION_URL}{path}", headers={"X-Token": FUSION_TOKEN})
        r.raise_for_status()
        return r.json()

async def _fusion_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{FUSION_URL}{path}", json=payload, headers={"X-Token": FUSION_TOKEN})
        r.raise_for_status()
        return r.json()

@mcp.tool()
async def fusion_ping() -> dict:
    """Health check for the Fusion bridge."""
    return await _fusion_get("/ping")

@mcp.tool()
async def fusion_get_state() -> dict:
    """Return current Fusion design state (parameters, bodies, timeline)."""
    return await _fusion_get("/state")

@mcp.tool()
async def fusion_create_sketch_rect_xy(x_mm: float = 40.0, y_mm: float = 30.0) -> dict:
    """Create a rectangle sketch on XY plane."""
    return await _fusion_post(
        "/tool",
        {"tool": "create_sketch_rect_xy", "args": {"x_mm": x_mm, "y_mm": y_mm}},
    )

@mcp.tool()
async def fusion_extrude_last_profile(distance_mm: float = 5.0, operation: str = "newBody") -> dict:
    """Extrude the first profile of the last sketch."""
    return await _fusion_post(
        "/tool",
        {"tool": "extrude_last_profile", "args": {"distance_mm": distance_mm, "operation": operation}},
    )

# ✅ Your MCP SDK version supports this:
# ['streamable_http_app', 'run_streamable_http_async', ...]
# So expose the ASGI app for uvicorn like this:
app = mcp.streamable_http_app()
