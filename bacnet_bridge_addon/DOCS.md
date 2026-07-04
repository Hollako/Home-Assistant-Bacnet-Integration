# BACnet Bridge

Set the BACnet bind address to the interface connected to the BACnet VLAN. The value should include the subnet prefix, for example:

```text
10.10.0.250/24
```

Object instances are assigned automatically using these defaults:

```text
AV  1000+
BV  2000+
BO  3000+
AO  4000+
MSV 5000+
AI  6000+
BI  7000+
```

Disabled mappings remain reserved in `/data/mappings.json`, so a BMS point will not accidentally be reused for another Home Assistant entity.
