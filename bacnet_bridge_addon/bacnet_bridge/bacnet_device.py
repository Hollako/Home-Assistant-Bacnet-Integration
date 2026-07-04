from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.basetypes import BinaryPV, EngineeringUnits
from bacpypes3.object import (
    AnalogInputObject,
    AnalogOutputObject,
    AnalogValueObject,
    BinaryInputObject,
    BinaryOutputObject,
    BinaryValueObject,
    MultiStateValueObject,
)
from bacpypes3.primitivedata import CharacterString, Real, Unsigned

from .config import AddonConfig

LOGGER = logging.getLogger(__name__)

ON_STATES = {"on", "1", "true", "active", "yes", "open", "unlocked", "home", "detected"}
OFF_STATES = {"off", "0", "false", "inactive", "no", "closed", "locked", "not_home", "clear"}
UNKNOWN_STATES = {"unknown", "unavailable", "none", ""}
COMMON_UNIT_MAP = {
    "%": "percent",
    "percent": "percent",
    "°c": "degreesCelsius",
    "degc": "degreesCelsius",
    "c": "degreesCelsius",
    "celsius": "degreesCelsius",
    "°f": "degreesFahrenheit",
    "degf": "degreesFahrenheit",
    "f": "degreesFahrenheit",
    "fahrenheit": "degreesFahrenheit",
    "lx": "luxes",
    "lux": "luxes",
    "ppm": "partsPerMillion",
    "pa": "pascals",
    "kpa": "kilopascals",
    "w": "watts",
    "kw": "kilowatts",
    "v": "volts",
    "a": "amperes",
    "hz": "hertz",
}


class BACnetBridgeDevice:
    def __init__(self, config: AddonConfig):
        self.config = config
        self.app = self._create_application(config)
        self.started_at = time.time()
        self.metrics = {
            "objects_created": 0,
            "ha_updates": 0,
            "ha_update_errors": 0,
            "bacnet_writes_detected": 0,
            "writeback_errors": 0,
        }
        self.ai: Dict[int, AnalogInputObject] = {}
        self.ao: Dict[int, AnalogOutputObject] = {}
        self.av: Dict[int, AnalogValueObject] = {}
        self.bi: Dict[int, BinaryInputObject] = {}
        self.bo: Dict[int, BinaryOutputObject] = {}
        self.bv: Dict[int, BinaryValueObject] = {}
        self.msv: Dict[int, MultiStateValueObject] = {}
        self.last_values: Dict[str, Any] = {}

    def _create_application(self, config: AddonConfig) -> Application:
        parser = SimpleArgumentParser()
        args = parser.parse_args(
            [
                "--address",
                config.bind_address,
                "--instance",
                str(config.device_instance),
                "--name",
                config.device_name,
            ]
        )
        return Application.from_args(args)

    def ensure_mapping(self, mapping: Dict[str, Any]) -> None:
        object_type = mapping["object_type"]
        instance = int(mapping["instance"])
        name = mapping.get("object_name")
        units = mapping.get("units")
        states = mapping.get("states")

        if object_type == "AI":
            self._ensure_ai(instance, name, units)
        elif object_type == "AO":
            self._ensure_ao(instance, name, units)
        elif object_type == "AV":
            self._ensure_av(instance, name, units)
        elif object_type == "BI":
            self._ensure_bi(instance, name)
        elif object_type == "BO":
            self._ensure_bo(instance, name)
        elif object_type == "BV":
            self._ensure_bv(instance, name)
        elif object_type == "MSV":
            self._ensure_msv(instance, name, states)
        else:
            raise ValueError(f"Unsupported BACnet object type: {object_type}")

    def remove_mapping(self, mapping: Dict[str, Any]) -> None:
        object_type = mapping["object_type"]
        instance = int(mapping["instance"])
        registry = self._registry_for_type(object_type)
        obj = registry.pop(instance, None)
        self.last_values.pop(self._last_key(object_type, instance), None)
        if obj is None:
            return
        delete_method = getattr(self.app, "delete_object", None)
        if callable(delete_method):
            try:
                delete_method(obj)
            except Exception:
                LOGGER.exception("bacnet_object_delete_failed type=%s instance=%s", object_type, instance)

    def update_from_ha_state(self, mapping: Dict[str, Any], state: Dict[str, Any]) -> Any:
        self.ensure_mapping(mapping)
        object_type = mapping["object_type"]
        instance = int(mapping["instance"])
        value = state_to_bacnet_value(mapping, state)
        if value is None:
            return None

        obj = self._object_for_type(object_type, instance)
        current_value = self._read_present_value(object_type, obj)
        if current_value != value:
            self._write_present_value(object_type, obj, value)
            self._announce_cov(obj)

        self.last_values[self._last_key(object_type, instance)] = value
        self.metrics["ha_updates"] += 1
        return value

    async def watch_writable(
        self,
        mapping_lookup: Callable[[str, int], Optional[Dict[str, Any]]],
        on_write: Callable[[Dict[str, Any], Any], Awaitable[None]],
    ) -> None:
        while True:
            try:
                await self._check_registry("AO", self.ao, mapping_lookup, on_write)
                await self._check_registry("AV", self.av, mapping_lookup, on_write)
                await self._check_registry("BO", self.bo, mapping_lookup, on_write)
                await self._check_registry("BV", self.bv, mapping_lookup, on_write)
                await self._check_registry("MSV", self.msv, mapping_lookup, on_write)
                await asyncio.sleep(self.config.watch_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("bacnet_write_watch_error")
                await asyncio.sleep(1.0)

    async def _check_registry(
        self,
        object_type: str,
        registry: Dict[int, Any],
        mapping_lookup: Callable[[str, int], Optional[Dict[str, Any]]],
        on_write: Callable[[Dict[str, Any], Any], Awaitable[None]],
    ) -> None:
        for instance, obj in list(registry.items()):
            current = self._read_present_value(object_type, obj)
            last_key = self._last_key(object_type, instance)
            previous = self.last_values.get(last_key, current)
            if current == previous:
                continue
            self.last_values[last_key] = current
            mapping = mapping_lookup(object_type, instance)
            if not mapping or not mapping.get("writable", False):
                continue
            self.metrics["bacnet_writes_detected"] += 1
            await on_write(mapping, current)

    def status(self) -> Dict[str, Any]:
        return {
            "uptime_seconds": int(time.time() - self.started_at),
            "metrics": dict(self.metrics),
            "objects": {
                "AI": len(self.ai),
                "AO": len(self.ao),
                "AV": len(self.av),
                "BI": len(self.bi),
                "BO": len(self.bo),
                "BV": len(self.bv),
                "MSV": len(self.msv),
            },
        }

    def close(self) -> None:
        close_method = getattr(self.app, "close", None)
        if callable(close_method):
            close_method()

    def _ensure_ai(self, instance: int, name: Optional[str], units: Optional[str]) -> None:
        if instance in self.ai:
            return
        base_name = name or f"AI {instance}"
        obj = AnalogInputObject(
            objectIdentifier=("analogInput", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=Real(0.0),
            units=self._engineering_units(units),
        )
        self._add_object(obj, base_name, instance)
        self.ai[instance] = obj
        self.metrics["objects_created"] += 1

    def _ensure_ao(self, instance: int, name: Optional[str], units: Optional[str]) -> None:
        if instance in self.ao:
            return
        base_name = name or f"AO {instance}"
        obj = AnalogOutputObject(
            objectIdentifier=("analogOutput", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=Real(0.0),
            units=self._engineering_units(units),
        )
        self._add_object(obj, base_name, instance)
        self.ao[instance] = obj
        self.last_values[self._last_key("AO", instance)] = 0.0
        self.metrics["objects_created"] += 1

    def _ensure_av(self, instance: int, name: Optional[str], units: Optional[str]) -> None:
        if instance in self.av:
            return
        base_name = name or f"AV {instance}"
        obj = AnalogValueObject(
            objectIdentifier=("analogValue", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=Real(0.0),
            units=self._engineering_units(units),
        )
        self._add_object(obj, base_name, instance)
        self.av[instance] = obj
        self.last_values[self._last_key("AV", instance)] = 0.0
        self.metrics["objects_created"] += 1

    def _ensure_bi(self, instance: int, name: Optional[str]) -> None:
        if instance in self.bi:
            return
        base_name = name or f"BI {instance}"
        obj = BinaryInputObject(
            objectIdentifier=("binaryInput", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=BinaryPV.inactive,
        )
        self._add_object(obj, base_name, instance)
        self.bi[instance] = obj
        self.metrics["objects_created"] += 1

    def _ensure_bo(self, instance: int, name: Optional[str]) -> None:
        if instance in self.bo:
            return
        base_name = name or f"BO {instance}"
        obj = BinaryOutputObject(
            objectIdentifier=("binaryOutput", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=BinaryPV.inactive,
        )
        self._add_object(obj, base_name, instance)
        self.bo[instance] = obj
        self.last_values[self._last_key("BO", instance)] = False
        self.metrics["objects_created"] += 1

    def _ensure_bv(self, instance: int, name: Optional[str]) -> None:
        if instance in self.bv:
            return
        base_name = name or f"BV {instance}"
        obj = BinaryValueObject(
            objectIdentifier=("binaryValue", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=BinaryPV.inactive,
        )
        self._add_object(obj, base_name, instance)
        self.bv[instance] = obj
        self.last_values[self._last_key("BV", instance)] = False
        self.metrics["objects_created"] += 1

    def _ensure_msv(self, instance: int, name: Optional[str], states: Optional[list[str]]) -> None:
        if instance in self.msv:
            return
        resolved_states = states or ["State 1"]
        number_of_states = max(1, len(resolved_states))
        base_name = name or f"MSV {instance}"
        obj = MultiStateValueObject(
            objectIdentifier=("multiStateValue", Unsigned(instance)),
            objectName=CharacterString(base_name),
            presentValue=Unsigned(1),
            numberOfStates=Unsigned(number_of_states),
            stateText=[CharacterString(state) for state in resolved_states],
        )
        self._add_object(obj, base_name, instance)
        self.msv[instance] = obj
        self.last_values[self._last_key("MSV", instance)] = 1
        self.metrics["objects_created"] += 1

    def _add_object(self, obj: Any, base_name: str, instance: int) -> None:
        try:
            self.app.add_object(obj)
            return
        except RuntimeError as err:
            if "already an object with name" not in str(err):
                raise
        obj.objectName = CharacterString(f"{base_name} [{instance}]")
        self.app.add_object(obj)

    def _object_for_type(self, object_type: str, instance: int) -> Any:
        registry = self._registry_for_type(object_type)
        return registry[instance]

    def _registry_for_type(self, object_type: str) -> Dict[int, Any]:
        return {
            "AI": self.ai,
            "AO": self.ao,
            "AV": self.av,
            "BI": self.bi,
            "BO": self.bo,
            "BV": self.bv,
            "MSV": self.msv,
        }[object_type]

    def _read_present_value(self, object_type: str, obj: Any) -> Any:
        if object_type in {"AI", "AO", "AV"}:
            return float(obj.presentValue)
        if object_type in {"BI", "BO", "BV"}:
            return obj.presentValue == BinaryPV.active
        if object_type == "MSV":
            return int(obj.presentValue)
        raise ValueError(f"Unsupported BACnet object type: {object_type}")

    def _write_present_value(self, object_type: str, obj: Any, value: Any) -> None:
        if object_type in {"AI", "AO", "AV"}:
            obj.presentValue = Real(float(value))
        elif object_type in {"BI", "BO", "BV"}:
            obj.presentValue = BinaryPV.active if bool(value) else BinaryPV.inactive
        elif object_type == "MSV":
            obj.presentValue = Unsigned(int(value))
        else:
            raise ValueError(f"Unsupported BACnet object type: {object_type}")

    def _announce_cov(self, obj: Any) -> None:
        try:
            result = getattr(obj, "announce_change_of_value", lambda: None)()
            if asyncio.iscoroutine(result):
                asyncio.get_running_loop().create_task(result)
        except Exception:
            LOGGER.exception("bacnet_cov_announce_failed")

    def _engineering_units(self, units: Optional[str]) -> EngineeringUnits:
        if not units:
            return EngineeringUnits.noUnits
        unit_text = str(units).strip()
        mapped = COMMON_UNIT_MAP.get(unit_text.lower())
        normalized = unit_text.replace(" ", "").replace("-", "")
        for candidate in (mapped, unit_text, normalized, normalized.lower()):
            if not candidate:
                continue
            if hasattr(EngineeringUnits, candidate):
                return getattr(EngineeringUnits, candidate)
        return EngineeringUnits.noUnits

    def _last_key(self, object_type: str, instance: int) -> str:
        return f"{object_type}:{instance}"


def state_to_bacnet_value(mapping: Dict[str, Any], state: Dict[str, Any]) -> Any:
    object_type = mapping["object_type"]
    raw_value = _source_value(mapping, state)
    if raw_value is None:
        return None
    raw_state = str(raw_value).strip()
    lowered = raw_state.lower()
    if lowered in UNKNOWN_STATES:
        return None

    if object_type in {"AI", "AO", "AV"}:
        try:
            return float(raw_state)
        except ValueError:
            return None
    if object_type in {"BI", "BO", "BV"}:
        if lowered in ON_STATES:
            return True
        if lowered in OFF_STATES:
            return False
        return None
    if object_type == "MSV":
        states = [str(item) for item in mapping.get("states") or []]
        if raw_state in states:
            return states.index(raw_state) + 1
        try:
            value = int(raw_state)
        except ValueError:
            return None
        if 1 <= value <= max(1, len(states)):
            return value
        return None
    return None


def _source_value(mapping: Dict[str, Any], state: Dict[str, Any]) -> Any:
    source = str(mapping.get("source") or "state").lower()
    if source != "attribute":
        return state.get("state")

    attribute = mapping.get("attribute")
    attributes = state.get("attributes") or {}
    transform = mapping.get("transform")
    if attribute == "brightness" or transform == "brightness_pct":
        return _brightness_to_percent(attributes.get("brightness"), state.get("state"))
    if not attribute:
        return None
    return attributes.get(attribute)


def _brightness_to_percent(raw_brightness: Any, raw_state: Any = None) -> Optional[float]:
    if str(raw_state).lower() == "off":
        return 0.0
    if raw_brightness is None:
        return None
    try:
        brightness = float(raw_brightness)
    except (TypeError, ValueError):
        return None
    brightness = max(0.0, min(255.0, brightness))
    return round((brightness / 255.0) * 100.0, 1)
