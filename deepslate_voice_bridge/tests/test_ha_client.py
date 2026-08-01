import json

import pytest
from aiohttp import web

from app.ha_client import HAClient

SNAPSHOT = {
    "areas": [
        {
            "id": "bedroom",
            "name": "Bedroom",
            "lights": [
                {"entity_id": "light.bedroom_bedroom", "name": "Bedroom", "state": "on"},
                {"entity_id": "light.bedroom_hue_iris", "name": "Hue Iris", "state": "off"},
            ],
        }
    ]
}


@pytest.fixture
async def ha_server(aiohttp_server):
    calls = []

    async def template(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        body = await request.json()
        calls.append(("template", body))
        return web.Response(text=json.dumps(SNAPSHOT))

    async def service(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        body = await request.json()
        calls.append((request.match_info["domain"] + "." + request.match_info["service"], body))
        return web.json_response([])

    app = web.Application()
    app.router.add_post("/api/template", template)
    app.router.add_post("/api/services/{domain}/{service}", service)
    server = await aiohttp_server(app)
    server.calls = calls
    return server


async def test_lights_snapshot(ha_server):
    client = HAClient(f"http://127.0.0.1:{ha_server.port}", "test-token")
    snap = await client.lights_snapshot()
    assert snap == SNAPSHOT
    kind, body = ha_server.calls[0]
    assert kind == "template"
    assert "area_entities" in body["template"]
    await client.close()


async def test_call_service(ha_server):
    client = HAClient(f"http://127.0.0.1:{ha_server.port}", "test-token")
    await client.call_service("light", "turn_on", {"area_id": "bedroom", "brightness_pct": 40})
    assert ha_server.calls[-1] == (
        "light.turn_on",
        {"area_id": "bedroom", "brightness_pct": 40},
    )
    await client.close()
