# BACnet Bridge App

Publish selected Home Assistant entities as BACnet/IP objects without Node-RED or MQTT in the middle.

## Flow

```text
Home Assistant entities -> BACnet Bridge add-on -> BacPypes -> BACnet/IP
```

The add-on stores entity mappings in `/data/mappings.json`. Object instances are auto-assigned from per-type counters and are not reused after a mapping is disabled.

## Install

1. Copy this repository into a Home Assistant add-on repository location, or add the repository URL to the Add-on Store.
2. Open the **BACnet Bridge** add-on.
3. Set `bind_address` to the IP/subnet on the BACnet VLAN, for example `10.10.0.250/24`.
4. Set a unique `device_instance`.
5. Start the add-on and open the web UI.

## Notes

- The add-on uses `host_network: true` so BACnet/IP broadcast can work on the host interface.
- The Ethernet/VLAN interface should be configured in Home Assistant OS or the host network settings first.
- Write-back is enabled only for supported writable Home Assistant domains.
