"""Light-control tools exposed to the Deepslate model.

Definitions use the SDK's FunctionToolDict shape. Execution resolves the
model's fuzzy `area` / `name` strings against the HA snapshot and issues
light.turn_on / light.turn_off service calls. execute() never raises — errors
come back as the result string so the model can verbalize them.
"""

from __future__ import annotations

import json
import logging

from app.ha_client import HAClient

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "control_lights",
            "description": (
                "Turn lights on or off, optionally with brightness and color. "
                "Target either a whole area (room) via 'area' or a single light "
                "via 'name'. At least one of area/name is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Room/area name, e.g. 'bedroom', 'kitchen'.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Name of a single light, e.g. 'Hue Iris'.",
                    },
                    "action": {"type": "string", "enum": ["on", "off"]},
                    "brightness_pct": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Brightness percent (only with action 'on').",
                    },
                    "color": {
                        "type": "string",
                        "description": "CSS color name, e.g. 'red', 'warm white' -> 'warmwhite'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_lights",
            "description": (
                "Get the current state of lights, optionally filtered by area. "
                "Use to answer questions like 'which lights are on?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "Optional area filter."}
                },
            },
        },
    },
]


class ToolExecutor:
    def __init__(self, ha: HAClient, snapshot: dict):
        self._ha = ha
        self._snapshot = snapshot

    def update_snapshot(self, snapshot: dict) -> None:
        self._snapshot = snapshot

    def _area_names(self) -> list[str]:
        return [a["name"] for a in self._snapshot["areas"]]

    def _find_area(self, query: str) -> dict | None:
        q = query.strip().lower()
        for a in self._snapshot["areas"]:
            if q == a["name"].lower() or q == a["id"].lower():
                return a
        for a in self._snapshot["areas"]:
            if q in a["name"].lower() or q in a["id"].lower():
                return a
        return None

    def _find_light(self, query: str) -> dict | None:
        q = query.strip().lower()
        lights = [l for a in self._snapshot["areas"] for l in a["lights"]]
        for l in lights:
            if q == l["name"].strip().lower() or q == l["entity_id"]:
                return l
        for l in lights:
            if q in l["name"].strip().lower():
                return l
        return None

    async def execute(self, name: str, params: dict) -> str:
        try:
            if name == "control_lights":
                return await self._control_lights(params)
            if name == "get_lights":
                return self._get_lights(params)
            return f"Error: unknown tool '{name}'."
        except Exception as e:  # never propagate — the model must get an answer
            logger.exception("tool %s failed", name)
            return f"Error executing {name}: {e}"

    async def _control_lights(self, params: dict) -> str:
        action = params.get("action")
        if action not in ("on", "off"):
            return "Error: 'action' must be 'on' or 'off'."

        data: dict = {}
        target_desc = ""
        if params.get("name"):
            light = self._find_light(str(params["name"]))
            if light is None:
                return (
                    f"Error: no light named '{params['name']}'. Known lights: "
                    + ", ".join(
                        l["name"].strip() for a in self._snapshot["areas"] for l in a["lights"]
                    )
                )
            data["entity_id"] = light["entity_id"]
            target_desc = light["name"].strip()
        elif params.get("area"):
            area = self._find_area(str(params["area"]))
            if area is None:
                return (
                    f"Error: no area matching '{params['area']}'. Known areas: "
                    + ", ".join(self._area_names())
                )
            data["area_id"] = area["id"]
            target_desc = f"all lights in {area['name']}"
        else:
            return "Error: provide 'area' or 'name'."

        if action == "on":
            if params.get("brightness_pct") is not None:
                data["brightness_pct"] = int(params["brightness_pct"])
            if params.get("color"):
                data["color_name"] = str(params["color"]).replace(" ", "")
        await self._ha.call_service("light", f"turn_{action}", data)
        return f"OK: turned {action} {target_desc}."

    def _get_lights(self, params: dict) -> str:
        areas = self._snapshot["areas"]
        if params.get("area"):
            area = self._find_area(str(params["area"]))
            if area is None:
                return (
                    f"Error: no area matching '{params['area']}'. Known areas: "
                    + ", ".join(self._area_names())
                )
            areas = [area]
        report = {
            a["name"]: {l["name"].strip(): l["state"] for l in a["lights"]} for a in areas
        }
        return json.dumps(report, ensure_ascii=False)
