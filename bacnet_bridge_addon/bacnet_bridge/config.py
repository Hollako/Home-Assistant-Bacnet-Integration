from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


OBJECT_TYPES = ("AI", "AO", "AV", "BI", "BO", "BV", "MSV")


@dataclass(frozen=True)
class AddonConfig:
    device_instance: int
    device_name: str
    bind_address: str
    log_level: str
    enable_writeback: bool
    watch_interval_seconds: float
    ha_reconnect_seconds: int
    instance_starts: Dict[str, int]

    @classmethod
    def from_file(cls, path: str | Path) -> "AddonConfig":
        with Path(path).open("r", encoding="utf-8") as config_file:
            options = json.load(config_file)
        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: Dict[str, Any]) -> "AddonConfig":
        starts = {
            "AI": int(options.get("ai_start", 6000)),
            "AO": int(options.get("ao_start", 4000)),
            "AV": int(options.get("av_start", 1000)),
            "BI": int(options.get("bi_start", 7000)),
            "BO": int(options.get("bo_start", 3000)),
            "BV": int(options.get("bv_start", 2000)),
            "MSV": int(options.get("msv_start", 5000)),
        }
        for object_type, value in starts.items():
            _validate_instance(value, f"{object_type} start")

        device_instance = int(options.get("device_instance", 50000))
        _validate_instance(device_instance, "device_instance")

        return cls(
            device_instance=device_instance,
            device_name=str(options.get("device_name") or "Home Assistant BACnet Bridge"),
            bind_address=str(options.get("bind_address") or "10.10.0.250/24"),
            log_level=str(options.get("log_level") or "info").upper(),
            enable_writeback=bool(options.get("enable_writeback", True)),
            watch_interval_seconds=float(options.get("watch_interval_seconds", 0.2)),
            ha_reconnect_seconds=int(options.get("ha_reconnect_seconds", 5)),
            instance_starts=starts,
        )

    def safe_dict(self) -> Dict[str, Any]:
        return {
            "device_instance": self.device_instance,
            "device_name": self.device_name,
            "bind_address": self.bind_address,
            "log_level": self.log_level,
            "enable_writeback": self.enable_writeback,
            "watch_interval_seconds": self.watch_interval_seconds,
            "ha_reconnect_seconds": self.ha_reconnect_seconds,
            "instance_starts": dict(self.instance_starts),
        }


def _validate_instance(value: int, label: str) -> None:
    if not 0 <= value <= 4_194_302:
        raise ValueError(f"{label} must be between 0 and 4194302")
