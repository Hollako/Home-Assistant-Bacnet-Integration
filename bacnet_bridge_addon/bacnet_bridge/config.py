from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


OBJECT_TYPES = ("AI", "AO", "AV", "BI", "BO", "BV", "MSV")


@dataclass(frozen=True)
class HostIPv4Address:
    interface_name: str
    address: ipaddress.IPv4Interface


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
        bind_address = str(options.get("bind_address") or "10.10.0.250/24")
        bind_interface = parse_bind_address(bind_address)

        return cls(
            device_instance=device_instance,
            device_name=str(options.get("device_name") or "Home Assistant BACnet Bridge"),
            bind_address=str(bind_interface),
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


def parse_bind_address(value: str) -> ipaddress.IPv4Interface:
    raw_value = str(value or "").strip()
    if "/" not in raw_value:
        raise ValueError("bind_address must include a subnet prefix, for example 10.10.0.250/24")
    try:
        interface = ipaddress.ip_interface(raw_value)
    except ValueError as err:
        raise ValueError(f"bind_address must be a valid IPv4 address with subnet prefix: {raw_value}") from err
    if interface.version != 4:
        raise ValueError("bind_address must be an IPv4 address, for example 10.10.0.250/24")
    return interface


def validate_bind_address_on_host(value: str) -> HostIPv4Address:
    requested = parse_bind_address(value)
    addresses = host_ipv4_addresses()
    exact_matches = [address for address in addresses if address.address == requested]
    if exact_matches:
        return exact_matches[0]

    ip_matches = [address for address in addresses if address.address.ip == requested.ip]
    if ip_matches:
        available = ", ".join(f"{address.address} on {address.interface_name}" for address in ip_matches)
        raise ValueError(
            f"bind_address {requested} has the wrong subnet prefix. "
            f"Matching host address: {available}"
        )

    available = ", ".join(f"{address.address} on {address.interface_name}" for address in addresses)
    if not available:
        available = "none found"
    raise ValueError(
        f"bind_address {requested} is not assigned to this Home Assistant host. "
        f"Available IPv4 addresses: {available}"
    )


def host_ipv4_addresses() -> list[HostIPv4Address]:
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "addr", "show"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as err:
        raise ValueError("Could not read host IPv4 addresses using `ip -j -4 addr show`") from err
    return parse_ip_addr_json(result.stdout)


def parse_ip_addr_json(raw_value: str) -> list[HostIPv4Address]:
    try:
        interfaces = json.loads(raw_value)
    except json.JSONDecodeError as err:
        raise ValueError("Could not parse host IPv4 address information") from err

    addresses: list[HostIPv4Address] = []
    if not isinstance(interfaces, list):
        return addresses

    for item in interfaces:
        if not isinstance(item, dict):
            continue
        interface_name = str(item.get("ifname") or "unknown")
        for address_info in item.get("addr_info") or []:
            if not isinstance(address_info, dict) or address_info.get("family") != "inet":
                continue
            local = address_info.get("local")
            prefixlen = address_info.get("prefixlen")
            if local is None or prefixlen is None:
                continue
            try:
                address = ipaddress.ip_interface(f"{local}/{prefixlen}")
            except ValueError:
                continue
            if address.version == 4:
                addresses.append(HostIPv4Address(interface_name=interface_name, address=address))
    return addresses
