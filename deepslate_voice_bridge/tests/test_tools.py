import json

import pytest

from app.tools import TOOL_DEFINITIONS, ToolExecutor

SNAPSHOT = {
    "areas": [
        {
            "id": "bedroom",
            "name": "Bedroom",
            "lights": [
                {"entity_id": "light.bedroom_bedroom", "name": "Bedroom", "state": "on"},
                {"entity_id": "light.bedroom_hue_iris", "name": "Hue Iris", "state": "off"},
            ],
        },
        {
            "id": "kitchen",
            "name": "Kitchen",
            "lights": [
                {"entity_id": "light.kitchen_kitchen", "name": "Kitchen ", "state": "off"}
            ],
        },
    ]
}


class FakeHA:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def call_service(self, domain, service, data):
        if self.fail:
            raise RuntimeError("HA unreachable")
        self.calls.append((domain, service, data))
        return []


@pytest.fixture
def ha():
    return FakeHA()


@pytest.fixture
def executor(ha):
    return ToolExecutor(ha, SNAPSHOT)


def test_definitions_shape():
    assert {t["function"]["name"] for t in TOOL_DEFINITIONS} == {"control_lights", "get_lights"}
    for t in TOOL_DEFINITIONS:
        assert t["type"] == "function"
        assert t["function"]["parameters"]["type"] == "object"


async def test_turn_on_area(executor, ha):
    result = await executor.execute("control_lights", {"area": "bedroom", "action": "on"})
    assert result.startswith("OK")
    assert ha.calls == [("light", "turn_on", {"area_id": "bedroom"})]


async def test_turn_off_by_fuzzy_name(executor, ha):
    result = await executor.execute("control_lights", {"name": "iris", "action": "off"})
    assert result.startswith("OK")
    assert ha.calls == [("light", "turn_off", {"entity_id": "light.bedroom_hue_iris"})]


async def test_brightness_and_color(executor, ha):
    await executor.execute(
        "control_lights",
        {"area": "Kitchen", "action": "on", "brightness_pct": 40, "color": "warm white"},
    )
    assert ha.calls == [
        (
            "light",
            "turn_on",
            {"area_id": "kitchen", "brightness_pct": 40, "color_name": "warmwhite"},
        )
    ]


async def test_unknown_area_lists_valid(executor, ha):
    result = await executor.execute("control_lights", {"area": "garage", "action": "on"})
    assert result.startswith("Error")
    assert "Bedroom" in result and "Kitchen" in result
    assert ha.calls == []


async def test_get_lights_filtered(executor):
    result = json.loads(await executor.execute("get_lights", {"area": "bedroom"}))
    assert result == {"Bedroom": {"Bedroom": "on", "Hue Iris": "off"}}


async def test_ha_failure_becomes_error_string():
    executor = ToolExecutor(FakeHA(fail=True), SNAPSHOT)
    result = await executor.execute("control_lights", {"area": "bedroom", "action": "on"})
    assert result.startswith("Error executing control_lights")


async def test_unknown_tool(executor):
    assert (await executor.execute("nope", {})).startswith("Error")
