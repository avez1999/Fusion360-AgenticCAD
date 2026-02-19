import os
import json
import httpx
from dotenv import load_dotenv
from openai import OpenAI

# -----------------------
# Load env
# -----------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ROOT, ".env"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FUSION_URL = os.getenv("FUSION_BRIDGE_URL", "http://127.0.0.1:18080")
FUSION_TOKEN = os.getenv("FUSION_BRIDGE_TOKEN", "")

assert OPENAI_API_KEY, "Missing OPENAI_API_KEY in .env"
assert FUSION_TOKEN, "Missing FUSION_BRIDGE_TOKEN in .env"

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------
# Fusion tool executor
# -----------------------
async def fusion_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as s:
        r = await s.get(f"{FUSION_URL}{path}", headers={"X-Token": FUSION_TOKEN})
        r.raise_for_status()
        return r.json()

async def fusion_tool(tool: str, args: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as s:
        r = await s.post(
            f"{FUSION_URL}/tool",
            headers={"X-Token": FUSION_TOKEN},
            json={"tool": tool, "args": args or {}},
        )
        r.raise_for_status()
        return r.json()

# Map of allowed tools (safety)
ALLOWED_TOOLS = {
    "fusion_ping": lambda args: fusion_get("/ping"),
    "fusion_get_state": lambda args: fusion_get("/state"),
    "fusion_create_sketch_rect_xy": lambda args: fusion_tool("create_sketch_rect_xy", args),
    "fusion_extrude_last_profile": lambda args: fusion_tool("extrude_last_profile", args),
}

# -----------------------
# Agent loop
# -----------------------
SYSTEM = """You are a CAD agent controlling Autodesk Fusion 360.
You cannot directly edit CAD. You must request tool calls from the runtime.

Available tools:
1) fusion_ping(args={})
2) fusion_get_state(args={})
3) fusion_create_sketch_rect_xy(args={"x_mm": float, "y_mm": float})
4) fusion_extrude_last_profile(args={"distance_mm": float, "operation": "newBody|join|cut|intersect"})

Rules:
- Always verify with fusion_get_state before and after geometry ops.
- If a tool returns ok:false or an error, explain and try a small recovery step.
- Output MUST be valid JSON only with this schema:
  {"action": "tool"|"final", "tool_name": str|null, "args": object|null, "message": str}
"""

GOAL = """Goal:
1) Confirm we have an active design.
2) Create a 40mm x 30mm rectangle on XY plane.
3) Extrude 5mm as a new body.
4) Verify result: bodies increased and timeline updated.
"""

def extract_json(text: str) -> dict:
    # Model should output pure JSON; this is a tiny guard.
    text = text.strip()
    return json.loads(text)

async def main(max_steps: int = 8):
    # Initial context given to the model
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": GOAL},
    ]

    for step in range(max_steps):
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=messages,
        )

        model_text = (resp.output_text or "").strip()
        try:
            cmd = extract_json(model_text)
        except Exception as e:
            raise RuntimeError(f"Model did not return valid JSON.\nGot:\n{model_text}\n\nError: {e}")

        if cmd.get("action") == "final":
            print(cmd.get("message", ""))
            return

        if cmd.get("action") != "tool":
            raise RuntimeError(f"Unknown action: {cmd}")

        tool_name = cmd.get("tool_name")
        args = cmd.get("args") or {}

        if tool_name not in ALLOWED_TOOLS:
            raise RuntimeError(f"Tool not allowed: {tool_name}")

        # Execute locally
        try:
            result = await ALLOWED_TOOLS[tool_name](args)
        except Exception as e:
            result = {"ok": False, "error": str(e)}

        # Feed result back to model
        messages.append({"role": "assistant", "content": model_text})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {"tool_result": {"tool_name": tool_name, "args": args, "result": result}},
                    indent=2,
                ),
            }
        )

    raise RuntimeError("Max steps reached without finishing.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
