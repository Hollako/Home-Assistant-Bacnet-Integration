# Changelog

## 0.3.0

- Add HVAC climate point discovery for current temperature, target temperature, HVAC mode, fan mode, swing mode, and preset mode where Home Assistant exposes them.
- Add climate writeback for target temperature and writable mode points.
- Keep command and status points distinct when they use the same Home Assistant entity.

## 0.2.8

- Give the BACnet Mappings panel more room and reduce horizontal scrolling.
- Wrap long Home Assistant entity IDs inside the mappings table.
- Show inline validation when a manually edited BACnet object instance conflicts with an existing object.

## 0.2.7

- Use a fixed-height Web UI layout so only the entity and mapping lists scroll.
- Expand the Web UI to use the full available Ingress width.

## 0.2.4

- Add manual BACnet object instance editing.
- Reuse freed object instance numbers after unpublishing.
- Validate duplicate object instances by object type.
