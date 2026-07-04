from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

import aiohttp

LOGGER = logging.getLogger(__name__)

StateHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class HomeAssistantClient:
    def __init__(
        self,
        token: str,
        reconnect_seconds: int,
        rest_base: str = "http://supervisor/core/api",
        websocket_url: str = "ws://supervisor/core/websocket",
    ):
        self.token = token
        self.reconnect_seconds = reconnect_seconds
        self.rest_base = rest_base.rstrip("/")
        self.websocket_url = websocket_url
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self.connected = False

    async def close(self) -> None:
        await self.session.close()

    async def get_states(self) -> List[Dict[str, Any]]:
        async with self.session.get(f"{self.rest_base}/states") as response:
            response.raise_for_status()
            return await response.json()

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        async with self.session.get(f"{self.rest_base}/states/{entity_id}") as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            return await response.json()

    async def call_service(self, domain: str, service: str, data: Dict[str, Any]) -> None:
        async with self.session.post(f"{self.rest_base}/services/{domain}/{service}", json=data) as response:
            response.raise_for_status()
            await response.read()

    async def subscribe_state_changes(
        self,
        mapped_entities: Callable[[], Iterable[str]],
        handler: StateHandler,
    ) -> None:
        while True:
            try:
                await self._subscribe_once(mapped_entities, handler)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                LOGGER.exception("ha_event_stream_error")
                await asyncio.sleep(self.reconnect_seconds)

    async def _subscribe_once(
        self,
        mapped_entities: Callable[[], Iterable[str]],
        handler: StateHandler,
    ) -> None:
        async with self.session.ws_connect(self.websocket_url, heartbeat=30) as websocket:
            await self._authenticate(websocket)
            await websocket.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
            subscribe_response = await websocket.receive_json()
            if not subscribe_response.get("success", False):
                raise RuntimeError(f"Home Assistant subscription failed: {subscribe_response}")
            self.connected = True
            LOGGER.info("ha_event_stream_connected")

            async for message in websocket:
                if message.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError(f"Home Assistant websocket error: {websocket.exception()}")
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = message.json()
                event = payload.get("event") or {}
                event_data = event.get("data") or {}
                entity_id = event_data.get("entity_id")
                if entity_id not in set(mapped_entities()):
                    continue
                new_state = event_data.get("new_state")
                if new_state:
                    await handler(new_state)

    async def _authenticate(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        first = await websocket.receive_json()
        if first.get("type") == "auth_required":
            await websocket.send_json({"type": "auth", "access_token": self.token})
            auth_response = await websocket.receive_json()
            if auth_response.get("type") != "auth_ok":
                raise RuntimeError(f"Home Assistant authentication failed: {auth_response}")
        elif first.get("type") == "auth_ok":
            return
        else:
            raise RuntimeError(f"Unexpected Home Assistant websocket greeting: {first}")
