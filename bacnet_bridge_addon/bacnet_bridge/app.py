from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from aiohttp import web

from .bacnet_device import BACnetBridgeDevice
from .config import AddonConfig, validate_bind_address_on_host
from .ha_client import HomeAssistantClient
from .store import MappingStore
from .web import BridgeWeb

LOGGER = logging.getLogger(__name__)


@dataclass
class BridgeContext:
    config: AddonConfig
    store: MappingStore
    bacnet: BACnetBridgeDevice
    ha: HomeAssistantClient

    def mapped_entities(self) -> Iterable[str]:
        return {mapping["entity_id"] for mapping in self.store.enabled_mappings()}

    def status(self) -> Dict[str, Any]:
        return {
            "config": self.config.safe_dict(),
            "home_assistant": {"connected": self.ha.connected},
            "bacnet": self.bacnet.status(),
            "mappings": {
                "enabled": len(self.store.enabled_mappings()),
                "total": len(self.store.mappings(include_disabled=True)),
                "counters": dict(self.store.data.get("counters", {})),
            },
        }


async def async_main() -> None:
    args = parse_args()
    config = AddonConfig.from_file(args.options)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    token = os.getenv("SUPERVISOR_TOKEN") or os.getenv("HA_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is not available. Enable homeassistant_api in config.yaml.")

    try:
        host_address = validate_bind_address_on_host(config.bind_address)
    except ValueError as err:
        LOGGER.error("configuration_error %s", err)
        raise
    LOGGER.info(
        "bind_address_validated bind_address=%s interface=%s",
        host_address.address,
        host_address.interface_name,
    )

    store = MappingStore(args.store, config)
    store.load()
    bacnet = BACnetBridgeDevice(config)
    ha = HomeAssistantClient(token=token, reconnect_seconds=config.ha_reconnect_seconds)
    context = BridgeContext(config=config, store=store, bacnet=bacnet, ha=ha)

    for mapping in store.enabled_mappings():
        bacnet.ensure_mapping(mapping)

    await hydrate_initial_states(context)

    web_runner = await start_web(context, args.host, args.port)
    tasks = [
        asyncio.create_task(ha.subscribe_state_changes(context.mapped_entities, lambda state: on_ha_state(context, state))),
    ]
    if config.enable_writeback:
        tasks.append(
            asyncio.create_task(
                bacnet.watch_writable(
                    lambda object_type, instance: store.find_by_object(object_type, instance),
                    lambda mapping, value: on_bacnet_write(context, mapping, value),
                )
            )
        )

    stop_event = asyncio.Event()
    register_stop_handlers(stop_event)
    LOGGER.info(
        "bridge_started device_instance=%s bind_address=%s mappings=%s",
        config.device_instance,
        config.bind_address,
        len(store.enabled_mappings()),
    )

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await web_runner.cleanup()
        await ha.close()
        bacnet.close()


async def hydrate_initial_states(context: BridgeContext) -> None:
    for mapping in context.store.enabled_mappings():
        try:
            state = await context.ha.get_state(mapping["entity_id"])
            if not state:
                context.store.update_mapping_status(mapping["id"], last_error="Entity not found")
                continue
            value = context.bacnet.update_from_ha_state(mapping, state)
            context.store.update_mapping_status(mapping["id"], last_state=value, last_error=None)
        except Exception as err:
            context.bacnet.metrics["ha_update_errors"] += 1
            context.store.update_mapping_status(mapping["id"], last_error=str(err))
            LOGGER.exception("initial_state_update_failed entity_id=%s", mapping.get("entity_id"))


async def on_ha_state(context: BridgeContext, state: Dict[str, Any]) -> None:
    for mapping in context.store.mappings_for_entity(state["entity_id"]):
        try:
            value = context.bacnet.update_from_ha_state(mapping, state)
            context.store.update_mapping_status(mapping["id"], last_state=value, last_error=None)
        except Exception as err:
            context.bacnet.metrics["ha_update_errors"] += 1
            context.store.update_mapping_status(mapping["id"], last_error=str(err))
            LOGGER.exception("ha_state_update_failed entity_id=%s", state.get("entity_id"))


async def on_bacnet_write(context: BridgeContext, mapping: Dict[str, Any], value: Any) -> None:
    try:
        domain, service, data = service_call_for_write(mapping, value)
        await context.ha.call_service(domain, service, data)
    except Exception:
        context.bacnet.metrics["writeback_errors"] += 1
        LOGGER.exception("bacnet_writeback_failed entity_id=%s value=%s", mapping.get("entity_id"), value)


def service_call_for_write(mapping: Dict[str, Any], value: Any) -> tuple[str, str, Dict[str, Any]]:
    entity_id = mapping["entity_id"]
    domain = entity_id.split(".", 1)[0]
    object_type = mapping["object_type"]
    source = str(mapping.get("source") or "state").lower()
    attribute = mapping.get("attribute")

    if source == "attribute" and domain == "light" and attribute == "brightness" and object_type in {"AO", "AV"}:
        brightness_pct = max(0.0, min(100.0, float(value)))
        if brightness_pct <= 0:
            return "light", "turn_off", {"entity_id": entity_id}
        return "light", "turn_on", {"entity_id": entity_id, "brightness_pct": int(round(brightness_pct))}

    if domain == "climate" and object_type in {"AO", "AV"} and source == "attribute" and attribute == "temperature":
        return "climate", "set_temperature", {"entity_id": entity_id, "temperature": float(value)}

    if object_type in {"BO", "BV"}:
        on = bool(value)
        if domain in {"switch", "light", "input_boolean", "fan"}:
            return domain, "turn_on" if on else "turn_off", {"entity_id": entity_id}
        if domain == "cover":
            return domain, "open_cover" if on else "close_cover", {"entity_id": entity_id}
        if domain == "lock":
            return domain, "unlock" if on else "lock", {"entity_id": entity_id}
    if object_type in {"AO", "AV"} and domain in {"number", "input_number"}:
        return domain, "set_value", {"entity_id": entity_id, "value": float(value)}
    if object_type == "MSV":
        states = [str(item) for item in mapping.get("states") or []]
        index = int(value) - 1
        if not 0 <= index < len(states):
            raise ValueError(f"MSV value {value} is outside mapped states")
        selected = states[index]
        if domain == "climate":
            if source == "state":
                return "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": selected}
            if attribute == "fan_mode":
                return "climate", "set_fan_mode", {"entity_id": entity_id, "fan_mode": selected}
            if attribute == "swing_mode":
                return "climate", "set_swing_mode", {"entity_id": entity_id, "swing_mode": selected}
            if attribute == "preset_mode":
                return "climate", "set_preset_mode", {"entity_id": entity_id, "preset_mode": selected}
        if domain in {"select", "input_select"}:
            return domain, "select_option", {"entity_id": entity_id, "option": selected}

    raise ValueError(f"Write-back is not supported for {entity_id} as {object_type}")


async def start_web(context: BridgeContext, host: str, port: int) -> web.AppRunner:
    app = BridgeWeb(context).app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    LOGGER.info("web_started host=%s port=%s", host, port)
    return runner


def register_stop_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--options", default="/data/options.json")
    parser.add_argument("--store", default="/data/mappings.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    return parser.parse_args()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
