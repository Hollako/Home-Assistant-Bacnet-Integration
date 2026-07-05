from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from .store import entity_summary

LOGGER = logging.getLogger(__name__)


class BridgeWeb:
    def __init__(self, context: Any):
        self.context = context
        self._metadata_cache: Dict[str, Dict[str, Any]] = {}
        self._metadata_cache_time = 0.0

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
                web.patch("/api/mappings/{mapping_id}", self.update_mapping),
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
        metadata = await self._entity_metadata()
        summaries = []
        for state in states:
            summary = entity_summary(state)
            summary.update(metadata.get(str(summary["entity_id"]), {}))
            summaries.append(summary)
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
            requested_instance = payload.get("instance")
            if requested_instance == "":
                requested_instance = None
            mapping = self.context.store.add_mapping(
                state,
                object_type=payload.get("object_type") or None,
                instance=requested_instance,
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

    async def update_mapping(self, request: web.Request) -> web.Response:
        mapping_id = request.match_info["mapping_id"]
        try:
            payload: Dict[str, Any] = await request.json()
            instance = payload.get("instance")
            if instance is None or str(instance).strip() == "":
                raise ValueError("instance is required")

            previous = dict(self.context.store.get_mapping(mapping_id))
            mapping = self.context.store.update_mapping_instance(mapping_id, int(instance))
            if int(previous["instance"]) != int(mapping["instance"]):
                self.context.bacnet.remove_mapping(previous)
                self.context.bacnet.ensure_mapping(mapping)

            state = await self.context.ha.get_state(mapping["entity_id"])
            if state is not None:
                value = self.context.bacnet.update_from_ha_state(mapping, state)
                self.context.store.update_mapping_status(mapping["id"], last_state=value, last_error=None)
            return web.json_response({"mapping": mapping})
        except KeyError as err:
            return web.json_response({"error": str(err)}, status=404)
        except Exception as err:
            return web.json_response({"error": str(err)}, status=400)

    async def _entity_metadata(self) -> Dict[str, Dict[str, Any]]:
        now = time.monotonic()
        if now - self._metadata_cache_time < 60:
            return self._metadata_cache

        try:
            entities, areas, devices = await asyncio.gather(
                self.context.ha.get_entity_registry(),
                self.context.ha.get_area_registry(),
                self.context.ha.get_device_registry(),
            )
        except Exception:
            LOGGER.exception("ha_registry_lookup_failed")
            return self._metadata_cache

        area_names = {
            str(area.get("area_id")): area.get("name")
            for area in areas or []
            if area.get("area_id") and area.get("name")
        }
        devices_by_id = {
            str(device_id): device
            for device in devices or []
            for device_id in [device.get("id") or device.get("device_id")]
            if device_id
        }
        metadata: Dict[str, Dict[str, Any]] = {}
        for entity in entities or []:
            entity_id = entity.get("entity_id")
            if not entity_id:
                continue
            area_id = _entity_area_id(entity, devices_by_id)
            area_name = area_names.get(str(area_id)) if area_id else None
            metadata[str(entity_id)] = {
                "area_id": area_id,
                "area_name": area_name,
                "registry_name": entity.get("name") or entity.get("original_name"),
                "registry_original_name": entity.get("original_name"),
                "registry_search_text": _searchable_metadata_text(entity, area_id, area_name),
            }
        self._metadata_cache = metadata
        self._metadata_cache_time = now
        return self._metadata_cache


def _entity_area_id(entity: Dict[str, Any], devices_by_id: Dict[str, Dict[str, Any]]) -> Optional[str]:
    area_id = entity.get("area_id")
    if area_id:
        return str(area_id)
    device_id = entity.get("device_id")
    device = devices_by_id.get(str(device_id)) if device_id else None
    device_area_id = device.get("area_id") if device else None
    return str(device_area_id) if device_area_id else None


def _searchable_metadata_text(entity: Dict[str, Any], area_id: Optional[str], area_name: Optional[str]) -> str:
    values = [
        entity.get("entity_id"),
        entity.get("name"),
        entity.get("original_name"),
        area_id,
        area_name,
    ]
    for key in ("aliases", "labels", "categories"):
        value = entity.get(key)
        if isinstance(value, list):
            values.extend(item for item in value if _is_searchable_metadata_value(item))
        elif _is_searchable_metadata_value(value):
            values.append(value)
    return " ".join(str(value) for value in values if value is not None and str(value).strip())


def _is_searchable_metadata_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))
