# Home Assistant BACnet Bridge

Publish selected Home Assistant entities as BACnet/IP objects directly from Home Assistant.

This repository is a Home Assistant App repository. The bridge runs as a Home Assistant App, opens through the Home Assistant sidebar, and lets you choose which entities become BACnet objects.

## What It Does

- Discovers Home Assistant entities through the Supervisor API.
- Publishes selected entities as BACnet/IP objects using BacPypes.
- Supports command and status points for writable devices such as lights and switches.
- Supports dimmable light brightness as percent values.
- Auto-assigns BACnet object instances from per-type counters.
- Keeps disabled mappings reserved so BMS references are not accidentally reused.
- Stores mappings in the App data folder at `/data/mappings.json`.

## Current Point Model

For a dimmable light, publish the points you need:

| Purpose | Suggested BACnet object |
| --- | --- |
| On/off command | Binary Output, `BO` |
| On/off status | Binary Input, `BI` |
| Brightness command | Analog Output, `AO` |
| Brightness status | Analog Input, `AI` |

The same Home Assistant entity can be published more than once when each BACnet object represents a different point.

## Install

1. In Home Assistant, open the App Store.
2. Add this GitHub repository URL as a custom App repository.
3. Install **BACnet Bridge**.
4. Configure the BACnet device instance and bind address.
5. Start the App and open **BACnet Bridge** from the sidebar.

Default configuration:

```yaml
device_instance: 50000
bind_address: 10.10.0.250/24
```

The bind address must be the Home Assistant host interface connected to the BACnet network.

## Updating

Do not uninstall the App for normal updates. Update or rebuild the existing App so Home Assistant keeps the App configuration and `/data/mappings.json`.

If you uninstall, keep **Remove App Data** off if you want to preserve mappings.

## BACnet Instance Ranges

| Object type | Start |
| --- | ---: |
| AV | 1000 |
| BV | 2000 |
| BO | 3000 |
| AO | 4000 |
| MSV | 5000 |
| AI | 6000 |
| BI | 7000 |

## Notes

- BACnet/IP discovery uses UDP broadcast on port 47808.
- The App uses host networking so BACnet can bind to the Home Assistant host interface.
- Wi-Fi clients can discover the bridge only when the network allows BACnet broadcast traffic between the client and Home Assistant.
