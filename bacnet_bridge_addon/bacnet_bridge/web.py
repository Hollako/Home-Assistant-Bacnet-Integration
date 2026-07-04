from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from aiohttp import web

from .store import entity_summary


class BridgeWeb:
    def __init__(self, context: Any):
        self.context = context

    def app(self) -> web.Application:
        app = web.Application()
        app.add_routes(
            [
                web.get("/", self.index),
                web.get("/api/health", self.health),
                web.get("/api/status", self.status),
                web.get("/api/entities", self.entities),
                web.get("/api/mappings", self.mappings),
                web.post("/api/mappings", self.add_mapping),
                web.delete("/api/mappings/{mapping_id}", self.disable_mapping),
            ]
        )
        static_path = Path(__file__).with_name("static")
        app.router.add_static("/static/", path=static_path, name="static")
        return app

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(Path(__file__).with_name("static") / "index.html")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def status(self, request: web.Request) -> web.Response:
        return web.json_response(self.context.status())

    async def entities(self, request: web.Request) -> web.Response:
        states = await self.context.ha.get_states()
        summaries = [entity_summary(state) for state in states]
        summaries.sort(key=lambda item: str(item["entity_id"]))
        return web.json_response({"entities": summaries})

    async def mappings(self, request: web.Request) -> web.Response:
        return web.json_response({"mappings": self.context.store.mappings(include_disabled=True)})

    async def add_mapping(self, request: web.Request) -> web.Response:
        try:
            payload: Dict[str, Any] = await request.json()
            entity_id = str(payload["entity_id"])
            state = await self.context.ha.get_state(entity_id)
            if state is None:
                raise web.HTTPNotFound(reason=f"Entity not found: {entity_id}")
            mapping = self.context.store.add_mapping(
                state,
                object_type=payload.get("object_type") or None,
                instance=payload.get("instance") or None,
                object_name=payload.get("object_name") or None,
                units=payload.get("units") or None,
                writable=payload.get("writable"),
                source=payload.get("source") or None,
                attribute=payload.get("attribute") or None,
                transform=payload.get("transform") or None,
                point_label=payload.get("point_label") or None,
            )
            self.context.bacnet.ensure_mapping(mapping)
            value = self.context.bacnet.update_from_ha_state(mapping, state)
            self.context.store.update_mapping_status(mapping["id"], last_state=value, last_error=None)
            return web.json_response({"mapping": mapping}, status=201)
        except web.HTTPException:
            raise
        except Exception as err:
            return web.json_response({"error": str(err)}, status=400)

    async def disable_mapping(self, request: web.Request) -> web.Response:
        mapping_id = request.match_info["mapping_id"]
        try:
            mapping = self.context.store.disable_mapping(mapping_id)
            self.context.bacnet.remove_mapping(mapping)
            return web.json_response({"mapping": mapping})
        except KeyError as err:
            return web.json_response({"error": str(err)}, status=404)
