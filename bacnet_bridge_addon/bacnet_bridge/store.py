from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import OBJECT_TYPES, AddonConfig


BINARY_DOMAINS = {"binary_sensor", "input_boolean", "switch", "light", "fan", "cover", "lock", "automation"}
NUMERIC_DOMAINS = {"sensor", "number", "input_number", "counter"}
STATE_DOMAINS = {"select", "input_select", "media_player", "climate", "alarm_control_panel"}
WRITABLE_DOMAINS = {"input_boolean", "switch", "light", "fan", "cover", "lock", "number", "input_number", "select", "input_select", "climate"}


class MappingStore:
    def __init__(self, path: str | Path, config: AddonConfig):
        self.path = Path(path)
        self.config = config
        self.data: Dict[str, Any] = {
            "version": 1,
            "counters": dict(config.instance_starts),
            "mappings": [],
        }

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        with self.path.open("r", encoding="utf-8") as store_file:
            loaded = json.load(store_file)
        loaded.setdefault("version", 1)
        loaded.setdefault("counters", {})
        loaded.setdefault("mappings", [])
        for object_type in OBJECT_TYPES:
            loaded["counters"][object_type] = max(
                int(loaded["counters"].get(object_type, self.config.instance_starts[object_type])),
                self.config.instance_starts[object_type],
            )
        self.data = loaded

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="mappings_", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                json.dump(self.data, tmp_file, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def mappings(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        records = list(self.data.get("mappings", []))
        if include_disabled:
            return records
        return [record for record in records if record.get("enabled", True)]

    def enabled_mappings(self) -> List[Dict[str, Any]]:
        return self.mappings(include_disabled=False)

    def mappings_for_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        return [
            mapping
            for mapping in self.enabled_mappings()
            if mapping.get("entity_id") == entity_id
        ]

    def find_by_object(self, object_type: str, instance: int) -> Optional[Dict[str, Any]]:
        object_type = object_type.upper()
        for mapping in self.enabled_mappings():
            if mapping.get("object_type") == object_type and int(mapping.get("instance")) == instance:
                return mapping
        return None

    def get_mapping(self, mapping_id: str) -> Dict[str, Any]:
        return self._find(mapping_id)

    def add_mapping(
        self,
        entity_state: Dict[str, Any],
        object_type: Optional[str] = None,
        instance: Optional[int] = None,
        object_name: Optional[str] = None,
        units: Optional[str] = None,
        writable: Optional[bool] = None,
        states: Optional[List[str]] = None,
        source: Optional[str] = None,
        attribute: Optional[str] = None,
        transform: Optional[str] = None,
        point_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity_id = str(entity_state["entity_id"])
        source = _normalize_source(source, attribute)
        attribute = str(attribute).strip() if attribute else None
        transform = str(transform).strip() if transform else None
        point_label = str(point_label).strip() if point_label else _default_point_label(source, attribute)
        object_type = (object_type or suggest_object_type(entity_state, source, attribute, transform)).upper()
        if object_type not in OBJECT_TYPES:
            raise ValueError(f"Unsupported BACnet object type: {object_type}")

        for existing in self.enabled_mappings():
            if (
                existing.get("entity_id") == entity_id
                and existing.get("object_type") == object_type
                and _mapping_source_key(existing) == _source_key(source, attribute, transform)
            ):
                raise ValueError(f"{entity_id} {_source_key(source, attribute, transform)} is already published as {object_type}")

        resolved_instance = self._reserve_instance(object_type, instance)
        now = int(time.time())
        domain = entity_id.split(".", 1)[0]
        resolved_units = units if units is not None else _default_units(entity_state, source, attribute, transform)
        mapping = {
            "id": str(uuid.uuid4()),
            "entity_id": entity_id,
            "domain": domain,
            "source": source,
            "attribute": attribute,
            "transform": transform,
            "point_label": point_label,
            "object_type": object_type,
            "instance": resolved_instance,
            "object_name": object_name or _default_object_name(entity_state, point_label),
            "units": resolved_units,
            "writable": bool(writable) if writable is not None else _default_writable(entity_state, object_type, source, attribute),
            "enabled": True,
            "states": states or _default_states(entity_state, object_type, source, attribute, transform),
            "created_at": now,
            "updated_at": now,
            "last_state": None,
            "last_error": None,
        }
        self.data["mappings"].append(mapping)
        self.save()
        return mapping

    def disable_mapping(self, mapping_id: str) -> Dict[str, Any]:
        mapping = self._find(mapping_id)
        mapping["enabled"] = False
        mapping["deleted_at"] = int(time.time())
        mapping["updated_at"] = int(time.time())
        self._sync_counter(mapping["object_type"])
        self.save()
        return mapping

    def update_mapping_instance(self, mapping_id: str, instance: int) -> Dict[str, Any]:
        mapping = self._find(mapping_id)
        if not mapping.get("enabled", True):
            raise ValueError("Disabled mappings cannot be edited")

        object_type = str(mapping["object_type"]).upper()
        resolved_instance = int(instance)
        _validate_object_instance(resolved_instance)

        current_instance = int(mapping["instance"])
        if resolved_instance == current_instance:
            return mapping

        used = _used_instances(self.enabled_mappings(), object_type, exclude_mapping_id=mapping_id)
        if resolved_instance in used:
            raise ValueError(f"{object_type} {resolved_instance} is already in use")

        mapping["instance"] = resolved_instance
        mapping["updated_at"] = int(time.time())
        self._sync_counter(object_type)
        self.save()
        return mapping

    def update_mapping_object_name(self, mapping_id: str, object_name: str) -> Dict[str, Any]:
        mapping = self._find(mapping_id)
        if not mapping.get("enabled", True):
            raise ValueError("Disabled mappings cannot be edited")

        resolved_name = _normalize_object_name(object_name)
        if mapping.get("object_name") == resolved_name:
            return mapping

        mapping["object_name"] = resolved_name
        mapping["updated_at"] = int(time.time())
        self.save()
        return mapping

    def update_mapping_status(
        self,
        mapping_id: str,
        *,
        last_state: Any = None,
        last_error: Optional[str] = None,
    ) -> None:
        mapping = self._find(mapping_id)
        mapping["last_state"] = last_state
        mapping["last_error"] = last_error
        mapping["updated_at"] = int(time.time())
        self.save()

    def _find(self, mapping_id: str) -> Dict[str, Any]:
        for mapping in self.data.get("mappings", []):
            if mapping.get("id") == mapping_id:
                return mapping
        raise KeyError(f"Mapping not found: {mapping_id}")

    def _reserve_instance(self, object_type: str, requested: Optional[int]) -> int:
        used = _used_instances(self.enabled_mappings(), object_type)
        start = self.config.instance_starts[object_type]

        if requested is not None:
            instance = int(requested)
            _validate_object_instance(instance)
            if instance in used:
                raise ValueError(f"{object_type} {instance} is already in use")
            self._sync_counter(object_type, used | {instance})
            return instance

        instance = _next_available_instance(start, used)
        self._sync_counter(object_type, used | {instance})
        return instance

    def _sync_counter(self, object_type: str, used: Optional[set[int]] = None) -> None:
        if used is None:
            used = _used_instances(self.enabled_mappings(), object_type)
        self.data.setdefault("counters", {})[object_type] = _next_available_instance(
            self.config.instance_starts[object_type],
            used,
        )


def suggest_object_type(
    entity_state: Dict[str, Any],
    source: Optional[str] = None,
    attribute: Optional[str] = None,
    transform: Optional[str] = None,
) -> str:
    entity_id = str(entity_state.get("entity_id", ""))
    domain = entity_id.split(".", 1)[0]
    state = str(entity_state.get("state", "")).lower()
    source = _normalize_source(source, attribute)

    if source == "attribute":
        if attribute == "brightness" or transform == "brightness_pct":
            return "AI"
        if _looks_numeric((entity_state.get("attributes") or {}).get(attribute or "")):
            return "AI"
    if domain in {"switch", "light", "input_boolean", "fan", "cover", "lock"}:
        return "BO"
    if domain in BINARY_DOMAINS or state in {"on", "off", "true", "false", "open", "closed"}:
        return "BI"
    if domain in NUMERIC_DOMAINS and _looks_numeric(entity_state.get("state")):
        return "AI"
    if domain in STATE_DOMAINS:
        return "MSV"
    return "AV"


def entity_summary(entity_state: Dict[str, Any]) -> Dict[str, Any]:
    attributes = entity_state.get("attributes") or {}
    points = point_options(entity_state)
    return {
        "entity_id": entity_state.get("entity_id"),
        "domain": str(entity_state.get("entity_id", "")).split(".", 1)[0],
        "state": entity_state.get("state"),
        "name": attributes.get("friendly_name") or entity_state.get("entity_id"),
        "unit": _state_unit(entity_state),
        "search_text": _entity_search_text(entity_state),
        "suggested_object_type": points[0]["suggested_object_type"] if points else suggest_object_type(entity_state),
        "points": points,
    }


def _entity_search_text(entity_state: Dict[str, Any]) -> str:
    attributes = entity_state.get("attributes") or {}
    values: List[Any] = [
        entity_state.get("entity_id"),
        entity_state.get("state"),
    ]
    for key, value in attributes.items():
        values.append(key)
        if _is_searchable_value(value):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if _is_searchable_value(item))
    return " ".join(str(value) for value in values if value is not None and str(value).strip())


def _is_searchable_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def point_options(entity_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    entity_id = str(entity_state.get("entity_id", ""))
    domain = entity_id.split(".", 1)[0]
    state = str(entity_state.get("state", "")).lower()
    attributes = entity_state.get("attributes") or {}
    points: List[Dict[str, Any]] = []

    def add(
        key: str,
        label: str,
        object_type: str,
        *,
        source: str = "state",
        attribute: Optional[str] = None,
        transform: Optional[str] = None,
        writable: bool = False,
        unit: Optional[str] = None,
        value: Any = None,
        allowed_object_types: Optional[List[str]] = None,
    ) -> None:
        points.append(
            {
                "key": key,
                "label": label,
                "source": source,
                "attribute": attribute,
                "transform": transform,
                "suggested_object_type": object_type,
                "writable": writable,
                "unit": unit,
                "value": value if value is not None else entity_state.get("state"),
                "allowed_object_types": allowed_object_types or [object_type],
            }
        )

    if domain in {"switch", "light", "input_boolean", "fan", "cover", "lock"}:
        add("state_command", "State Command", "BO", writable=True, allowed_object_types=["BO", "BV"])
        add("state_status", "State Status", "BI", allowed_object_types=["BI", "BV"])
    elif domain in BINARY_DOMAINS or state in {"on", "off", "true", "false", "open", "closed"}:
        add("state_status", "State Status", "BI", allowed_object_types=["BI", "BV"])
    elif domain in {"number", "input_number"}:
        add("value_command", "Value Command", "AO", writable=True, unit=_state_unit(entity_state), allowed_object_types=["AO", "AV"])
        add("value_status", "Value Status", "AI", unit=_state_unit(entity_state), allowed_object_types=["AI", "AV"])
    elif domain in NUMERIC_DOMAINS and _looks_numeric(entity_state.get("state")):
        add("value_status", "Value Status", "AI", unit=_state_unit(entity_state), allowed_object_types=["AI", "AV"])
    elif domain == "climate":
        temperature_unit = attributes.get("temperature_unit") or _state_unit(entity_state)
        if _looks_numeric(attributes.get("current_temperature")):
            add("current_temperature", "Current Temperature", "AI", source="attribute", attribute="current_temperature", unit=temperature_unit, value=attributes.get("current_temperature"), allowed_object_types=["AI", "AV"])
        if _looks_numeric(attributes.get("temperature")):
            add("target_temperature_command", "Target Temperature Command", "AO", source="attribute", attribute="temperature", transform="climate_temperature_command", writable=True, unit=temperature_unit, value=attributes.get("temperature"), allowed_object_types=["AO", "AV"])
            add("target_temperature_status", "Target Temperature Status", "AI", source="attribute", attribute="temperature", transform="climate_temperature_status", unit=temperature_unit, value=attributes.get("temperature"), allowed_object_types=["AI", "AV"])
        if _state_list(attributes.get("hvac_modes")):
            add("hvac_mode_command", "HVAC Mode Command", "MSV", transform="hvac_mode_command", writable=True)
            add("hvac_mode_status", "HVAC Mode Status", "MSV", transform="hvac_mode_status")
        if _state_list(attributes.get("fan_modes")) and attributes.get("fan_mode"):
            add("fan_mode_command", "Fan Mode Command", "MSV", source="attribute", attribute="fan_mode", transform="fan_mode_command", writable=True, value=attributes.get("fan_mode"))
            add("fan_mode_status", "Fan Mode Status", "MSV", source="attribute", attribute="fan_mode", transform="fan_mode_status", value=attributes.get("fan_mode"))
        if _state_list(attributes.get("swing_modes")) and attributes.get("swing_mode"):
            add("swing_mode_command", "Swing Mode Command", "MSV", source="attribute", attribute="swing_mode", transform="swing_mode_command", writable=True, value=attributes.get("swing_mode"))
            add("swing_mode_status", "Swing Mode Status", "MSV", source="attribute", attribute="swing_mode", transform="swing_mode_status", value=attributes.get("swing_mode"))
        if _state_list(attributes.get("preset_modes")) and attributes.get("preset_mode"):
            add("preset_mode_command", "Preset Mode Command", "MSV", source="attribute", attribute="preset_mode", transform="preset_mode_command", writable=True, value=attributes.get("preset_mode"))
            add("preset_mode_status", "Preset Mode Status", "MSV", source="attribute", attribute="preset_mode", transform="preset_mode_status", value=attributes.get("preset_mode"))
        if not points:
            add("state_value", "State Value", "MSV")
    elif domain in STATE_DOMAINS:
        add("state_value", "State Value", "MSV")
    else:
        add("state", "State", suggest_object_type(entity_state))

    if domain == "light" and _supports_brightness(entity_state):
        brightness_pct = _brightness_to_percent(attributes.get("brightness"), entity_state.get("state"))
        add(
            "brightness_command",
            "Brightness Command",
            "AO",
            source="attribute",
            attribute="brightness",
            transform="brightness_pct",
            writable=True,
            unit="%",
            value=brightness_pct,
            allowed_object_types=["AO", "AV"],
        )
        add(
            "brightness_status",
            "Brightness Status",
            "AI",
            source="attribute",
            attribute="brightness",
            transform="brightness_pct",
            unit="%",
            value=brightness_pct,
            allowed_object_types=["AI", "AV"],
        )

    return points


def _used_instances(
    mappings: Iterable[Dict[str, Any]],
    object_type: str,
    *,
    exclude_mapping_id: Optional[str] = None,
) -> set[int]:
    return {
        int(mapping["instance"])
        for mapping in mappings
        if mapping.get("object_type") == object_type and "instance" in mapping
        and mapping.get("id") != exclude_mapping_id
    }


def _next_available_instance(start: int, used: set[int]) -> int:
    instance = start
    while instance in used:
        instance += 1
        _validate_object_instance(instance)
    return instance


def _default_object_name(entity_state: Dict[str, Any], point_label: Optional[str] = None) -> str:
    attributes = entity_state.get("attributes") or {}
    friendly = attributes.get("friendly_name")
    base = str(friendly or entity_state.get("entity_id", "HA Entity"))
    if point_label:
        base = f"{base} {point_label}"
    return _normalize_object_name(base)


def _normalize_object_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("BACnet object name is required")
    return name[:64]


def _state_unit(entity_state: Dict[str, Any]) -> Optional[str]:
    attributes = entity_state.get("attributes") or {}
    unit = attributes.get("unit_of_measurement")
    return str(unit) if unit else None


def _default_units(
    entity_state: Dict[str, Any],
    source: str,
    attribute: Optional[str],
    transform: Optional[str],
) -> Optional[str]:
    if source == "attribute" and (attribute == "brightness" or transform == "brightness_pct"):
        return "%"
    if source == "attribute" and attribute in {"current_temperature", "temperature"}:
        attributes = entity_state.get("attributes") or {}
        unit = attributes.get("temperature_unit") or attributes.get("unit_of_measurement")
        return str(unit) if unit else None
    return _state_unit(entity_state)


def _default_writable(entity_state: Dict[str, Any], object_type: str, source: str, attribute: Optional[str]) -> bool:
    entity_id = str(entity_state.get("entity_id", ""))
    domain = entity_id.split(".", 1)[0]
    if source == "attribute":
        if domain == "climate" and attribute == "temperature":
            return object_type in {"AO", "AV"}
        if domain == "climate" and attribute in {"fan_mode", "swing_mode", "preset_mode"}:
            return object_type == "MSV"
        return domain == "light" and attribute == "brightness" and object_type in {"AO", "AV"}
    if domain == "climate":
        return object_type == "MSV" and transform == "hvac_mode_command"
    return domain in WRITABLE_DOMAINS and object_type in {"AO", "AV", "BO", "BV", "MSV"}


def _default_states(
    entity_state: Dict[str, Any],
    object_type: str,
    source: str,
    attribute: Optional[str],
    transform: Optional[str],
) -> Optional[List[str]]:
    if object_type != "MSV":
        return None
    attributes = entity_state.get("attributes") or {}
    domain = str(entity_state.get("entity_id", "")).split(".", 1)[0]
    if domain == "climate":
        if source == "state" and transform in {"hvac_mode_command", "hvac_mode_status"}:
            return _state_list(attributes.get("hvac_modes")) or ["off"]
        if attribute == "fan_mode":
            return _state_list(attributes.get("fan_modes")) or ["auto"]
        if attribute == "swing_mode":
            return _state_list(attributes.get("swing_modes")) or ["off"]
        if attribute == "preset_mode":
            return _state_list(attributes.get("preset_modes")) or ["none"]
    options = attributes.get("options")
    if isinstance(options, list) and options:
        return [str(option)[:64] for option in options]
    state = entity_state.get("state")
    if state and state not in ("unknown", "unavailable"):
        return [str(state)[:64]]
    return ["State 1"]


def _looks_numeric(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _normalize_source(source: Optional[str], attribute: Optional[str]) -> str:
    if source:
        source = str(source).strip().lower()
    if source in {"attribute", "state"}:
        return source
    return "attribute" if attribute else "state"


def _source_key(source: str, attribute: Optional[str], transform: Optional[str] = None) -> str:
    base = f"attribute:{attribute}" if source == "attribute" else "state"
    return f"{base}:{transform}" if transform else base


def _mapping_source_key(mapping: Dict[str, Any]) -> str:
    return _source_key(
        _normalize_source(mapping.get("source"), mapping.get("attribute")),
        mapping.get("attribute"),
        mapping.get("transform"),
    )


def _default_point_label(source: str, attribute: Optional[str]) -> str:
    if source == "attribute" and attribute:
        return str(attribute).replace("_", " ").title()
    return "State"


def _supports_brightness(entity_state: Dict[str, Any]) -> bool:
    attributes = entity_state.get("attributes") or {}
    if "brightness" in attributes:
        return True
    supported_color_modes = attributes.get("supported_color_modes")
    if isinstance(supported_color_modes, list):
        return any(mode not in {"onoff", "unknown"} for mode in supported_color_modes)
    return False


def _state_list(value: Any) -> Optional[List[str]]:
    if isinstance(value, list) and value:
        return [str(item)[:64] for item in value]
    return None


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


def _validate_object_instance(value: int) -> None:
    if not 0 <= value <= 4_194_302:
        raise ValueError("BACnet object instance must be between 0 and 4194302")
