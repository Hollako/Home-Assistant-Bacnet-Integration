# BACnet Bridge App

A direct Home Assistant BACnet bridge that publishes selected Home Assistant entities as BACnet/IP objects.

## Flow

```text
Home Assistant entities -> BACnet Bridge App -> BacPypes -> BACnet/IP
```

The App stores entity mappings in `/data/mappings.json`. Object instances are auto-assigned from per-type counters and are not reused after a mapping is disabled.

## Install

1. Add this repository URL to the Home Assistant App Store.
2. Open the **BACnet Bridge** App.
3. Set `bind_address` to the IP/subnet on the BACnet VLAN, for example `10.10.0.250/24`.
4. Set a unique `device_instance`.
5. Start the App and open the web UI.

## Notes

- The App uses `host_network: true` so BACnet/IP broadcast can work on the host interface.
- The Ethernet/VLAN interface should be configured in Home Assistant OS or the host network settings first.
- Write-back is enabled only for supported writable Home Assistant domains.
