"""Thin async client for the Home Assistant REST API.

Uses the template API to read the area/light registry (the plain REST API does
not expose areas), and the services API to switch lights.
"""

from __future__ import annotations

import json
import logging

import aiohttp

logger = logging.getLogger(__name__)

# One Jinja template that returns the whole area→lights inventory as JSON.
# `areas()`/`area_name()`/`area_entities()` are HA template functions.
_SNAPSHOT_TEMPLATE = """
{%- set ns = namespace(areas=[]) -%}
{%- for a in areas() -%}
  {%- set lights = area_entities(a) | select('match', 'light\\.') | list -%}
  {%- if lights -%}
    {#- plain `set` does not survive loop iterations in Jinja; use a namespace -#}
    {%- set inner = namespace(entries=[]) -%}
    {%- for e in lights -%}
      {%- set inner.entries = inner.entries + [{
        'entity_id': e,
        'name': state_attr(e, 'friendly_name') or e,
        'state': states(e),
      }] -%}
    {%- endfor -%}
    {%- set ns.areas = ns.areas + [{'id': a, 'name': area_name(a) or a, 'lights': inner.entries}] -%}
  {%- endif -%}
{%- endfor -%}
{{ {'areas': ns.areas} | tojson }}
"""


class HAClient:
    def __init__(self, base_url: str, token: str):
        self._base = base_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def call_service(self, domain: str, service: str, data: dict) -> list:
        url = f"{self._base}/api/services/{domain}/{service}"
        async with self._http().post(url, json=data) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def render_template(self, template: str) -> str:
        url = f"{self._base}/api/template"
        async with self._http().post(url, json={"template": template}) as resp:
            resp.raise_for_status()
            return await resp.text()

    async def lights_snapshot(self) -> dict:
        """Return {'areas': [{'id', 'name', 'lights': [{'entity_id','name','state'}]}]}."""
        rendered = await self.render_template(_SNAPSHOT_TEMPLATE)
        snapshot = json.loads(rendered)
        n = sum(len(a["lights"]) for a in snapshot["areas"])
        logger.info("HA snapshot: %d areas, %d lights", len(snapshot["areas"]), n)
        return snapshot

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
