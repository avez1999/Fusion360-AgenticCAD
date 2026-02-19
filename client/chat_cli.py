# E:\FusionAgenticCAD\client\chat_cli.py
import os
import json
import asyncio
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

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")
if not FUSION_TOKEN:
    raise RuntimeError("Missing FUSION_BRIDGE_TOKEN in .env")

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------
# FusionBridge helpers
# -----------------------
async def fusion_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as s:
        r = await s.get(f"{FUSION_URL}{path}", headers={"X-Token": FUSION_TOKEN})
        r.raise_for_status()
        return r.json()

async def fusion_tool(tool: str, args: dict | None = None) -> dict:
    # Some features (pattern/combine/gear) can take longer
    async with httpx.AsyncClient(timeout=120.0) as s:
        r = await s.post(
            f"{FUSION_URL}/tool",
            headers={"X-Token": FUSION_TOKEN},
            json={"tool": tool, "args": args or {}},
        )
        r.raise_for_status()
        return r.json()

# -----------------------
# Tool routing (LLM tool_name -> FusionBridge tool string)
# -----------------------
# IMPORTANT: tool strings must match bridge_server _TOOL_MAP keys exactly.
TOOL_ROUTER = {
    # Basics
    "fusion_ping": ("GET", "/ping"),
    "fusion_get_state": ("GET", "/state"),

    # Sketch helpers
    "fusion_create_sketch_on_plane": ("POST", "create_sketch_on_plane"),
    "fusion_create_sketch_rect_xy": ("POST", "create_sketch_rect_xy"),
    "fusion_create_sketch_circle_xy": ("POST", "create_sketch_circle_xy"),
    "fusion_create_sketch_two_circles_xy": ("POST", "create_sketch_two_circles_xy"),
    "fusion_create_sketch_on_last_body_top": ("POST", "create_sketch_on_last_body_top"),
    "fusion_sketch_center_rectangle": ("POST", "sketch_center_rectangle"),
    "fusion_sketch_two_circles_current": ("POST", "sketch_two_circles_current"),
    "fusion_sketch_line": ("POST", "sketch_line"),

    # Solid features
    "fusion_extrude_last_profile": ("POST", "extrude_last_profile"),
    "fusion_extrude_profile": ("POST", "extrude_profile"),
    "fusion_revolve_profile": ("POST", "revolve_profile"),

    # Hole + pattern
    "fusion_hole_on_last_body_top_face": ("POST", "hole_on_last_body_top_face"),
    "fusion_hole_bolt_circle_one": ("POST", "hole_bolt_circle_one"),
    "fusion_circular_pattern_last_feature": ("POST", "circular_pattern_last_feature"),
    "fusion_countersink_hole_on_last_body_top_face": ("POST", "countersink_hole_on_last_body_top_face"),

    # Query helpers
    "fusion_list_bodies": ("POST", "list_bodies"),
    "fusion_get_last_body": ("POST", "get_last_body"),

    # Cleanup / utility
    "fusion_delete_all_bodies": ("POST", "delete_all_bodies"),
    "fusion_combine_all_bodies": ("POST", "combine_all_bodies"),
    "fusion_circular_pattern_last_body": ("POST", "circular_pattern_last_body"),

    # Assembly helpers
    "fusion_component_from_last_body": ("POST", "component_from_last_body"),
    "fusion_rigid_joint_last_two_components": ("POST", "rigid_joint_last_two_components"),

    # Gears
    "fusion_create_spur_gear": ("POST", "create_spur_gear_involute"),
    "fusion_create_rack_gear": ("POST", "create_rack_gear"),
    "fusion_create_helical_gear": ("POST", "create_helical_gear"),
    "fusion_create_internal_gear": ("POST", "create_internal_gear"),
    "fusion_create_bevel_gear": ("POST", "create_bevel_gear"),
}

# -----------------------
# Strong system prompt to prevent tool name drift
# -----------------------
SYSTEM = f"""You are a CAD agent controlling Autodesk Fusion 360 through tools.

You MUST respond with ONLY valid JSON (no extra text, no markdown) with this schema:
{{
  "action": "tool" | "final",
  "tool_name": string | null,
  "args": object | null,
  "message": string
}}

You may ONLY request ONE of these tool_name values (exact spelling):
{json.dumps(sorted(TOOL_ROUTER.keys()), indent=2)}

Units:
- All dimensions are in millimeters unless explicitly stated.

Tool argument schemas:

Basics:
- fusion_ping: {{}}
- fusion_get_state: {{}}

Sketch tools:
- fusion_create_sketch_on_plane: {{"plane":"XY"|"XZ"|"YZ"}}
- fusion_create_sketch_rect_xy: {{"x_mm": number, "y_mm": number}}
- fusion_create_sketch_circle_xy: {{"r_mm": number, "cx_mm": number, "cy_mm": number}}
- fusion_create_sketch_two_circles_xy: {{"od_mm": number, "id_mm": number}}
- fusion_create_sketch_on_last_body_top: {{}}
- fusion_sketch_center_rectangle: {{"w_mm": number, "h_mm": number, "cx_mm": number, "cy_mm": number}}
- fusion_sketch_two_circles_current: {{"od_mm": number, "id_mm": number}}
- fusion_sketch_line: {{"x1_mm": number, "y1_mm": number, "x2_mm": number, "y2_mm": number}}

Solid feature tools:
- fusion_extrude_last_profile: {{"distance_mm": number, "operation":"newBody"|"join"|"cut"|"intersect"}}
- fusion_extrude_profile: {{"sketch_index_from_end": integer, "profile_index": integer, "distance_mm": number, "operation":"newBody"|"join"|"cut"|"intersect"}}
- fusion_revolve_profile: {{"sketch_index_from_end": integer, "profile_index": integer, "axis_line_index": integer, "angle_deg": number}}

Holes + pattern tools:
- fusion_hole_on_last_body_top_face: {{"dia_mm": number, "depth_mm": number, "x_mm": number, "y_mm": number}}
- fusion_hole_bolt_circle_one: {{"bcd_mm": number, "hole_dia_mm": number, "depth_mm": number, "angle_deg": number}}
- fusion_circular_pattern_last_feature: {{"qty": integer, "angle": "360 deg" or similar}}
- fusion_countersink_hole_on_last_body_top_face: {{"hole_dia_mm": number, "cs_dia_mm": number, "cs_angle_deg": number, "depth_mm": number, "x_mm": number, "y_mm": number}}

Query helpers:
- fusion_list_bodies: {{}}
- fusion_get_last_body: {{}}

Cleanup / utility:
- fusion_delete_all_bodies: {{}}
- fusion_combine_all_bodies: {{"operation":"join"|"cut"|"intersect"}}
- fusion_circular_pattern_last_body: {{"qty": integer, "angle": "360 deg" or similar}}

Assembly:
- fusion_component_from_last_body: {{"name": string}}
- fusion_rigid_joint_last_two_components: {{}}

Gears:
- fusion_create_spur_gear: {{
    "teeth": integer,
    "module_mm": number,
    "pressure_angle_deg": number,
    "thickness_mm": number,
    "bore_mm": number,
    "backlash_mm": number
  }}
- fusion_create_rack_gear: {{
    "teeth": integer,
    "module_mm": number,
    "pressure_angle_deg": number,
    "thickness_mm": number
  }}
- fusion_create_helical_gear: {{...}}
- fusion_create_internal_gear: {{...}}
- fusion_create_bevel_gear: {{...}}

Rules:
- Before creating geometry, call fusion_get_state to ensure an active design exists.
- After each major feature, call fusion_get_state to verify (timeline/bodies).
- If a tool fails, do one recovery step (usually fusion_get_state), then end with action:"final" describing what failed.
- Do NOT fabricate “unsupported environment” messages if a matching tool exists; call the tool instead.
"""

def _parse_json_only(text: str) -> dict:
    text = (text or "").strip()
    # If the model accidentally adds surrounding text, try to extract first JSON object.
    if not text.startswith("{"):
        i = text.find("{")
        j = text.rfind("}")
        if i != -1 and j != -1 and j > i:
            text = text[i : j + 1]
    return json.loads(text)

async def _execute_tool(tool_name: str, args: dict) -> dict:
    if tool_name not in TOOL_ROUTER:
        return {"ok": False, "error": f"Tool not allowed: {tool_name}"}

    kind = TOOL_ROUTER[tool_name]
    if kind[0] == "GET":
        return await fusion_get(kind[1])

    # POST tool
    return await fusion_tool(kind[1], args)

async def agent_turn(user_text: str, messages: list) -> tuple[str, list]:
    messages.append({"role": "user", "content": user_text})

    for _ in range(12):
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=messages,
        )
        raw = (resp.output_text or "").strip()

        try:
            cmd = _parse_json_only(raw)
        except Exception as e:
            messages.append({"role": "assistant", "content": raw})
            return f"Model did not return valid JSON.\nRaw:\n{raw}\nError: {e}", messages

        messages.append({"role": "assistant", "content": json.dumps(cmd)})

        action = cmd.get("action")
        if action == "final":
            return cmd.get("message", ""), messages

        if action != "tool":
            return f"Unknown action: {cmd}", messages

        tool_name = cmd.get("tool_name")
        args = cmd.get("args") or {}

        if not tool_name:
            return f"Missing tool_name in: {cmd}", messages

        # Execute tool
        try:
            result = await _execute_tool(tool_name, args)
        except Exception as e:
            result = {"ok": False, "error": str(e)}

        # Provide tool result back to the model (explicitly)
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "tool_result": {
                            "tool_name": tool_name,
                            "args": args,
                            "result": result,
                        }
                    },
                    indent=2,
                ),
            }
        )

    return "Max steps reached without finishing.", messages

async def main():
    messages = [{"role": "system", "content": SYSTEM}]

    print("Fusion CAD Chatbot (Local)")
    print("Type 'exit' to quit.\n")
    print("Example prompts:")
    print("  Delete everything.")
    print("  Create a flange: disc OD 120 mm, thickness 12 mm; hub OD 70 mm, hub height 20 mm; bore 35 mm.")
    print("  Add 6 bolt holes on BCD 90 mm, hole dia 8 mm with countersink dia 14 mm at 82°.")
    print("  Create spur gear: teeth 24, module 2 mm, pressure angle 20°, thickness 10 mm, bore 10 mm.\n")

    while True:
        user_text = input("You: ").strip()
        if user_text.lower() in {"exit", "quit"}:
            break

        reply, messages = await agent_turn(user_text, messages)
        print("\nBot:", reply, "\n")

if __name__ == "__main__":
    asyncio.run(main())
