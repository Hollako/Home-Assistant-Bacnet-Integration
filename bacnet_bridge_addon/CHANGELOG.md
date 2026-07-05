# Changelog

## 0.3.11

- Add publishable Home Assistant entity availability points for BACnet.

## 0.3.10

- Give the BACnet object name editor more room in the mappings table.

## 0.3.9

- Allow editing the BACnet object name for published mappings.

## 0.3.8

- Show inline validation when publishing an entity with a duplicate BACnet object instance.

## 0.3.7

- Disable caching for the web UI shell and add versioned frontend asset URLs.

## 0.3.6

- Add a dedicated domain filter for the Home Assistant entity list.

## 0.3.5

- Broaden entity search to include Home Assistant state attributes and registry metadata.

## 0.3.4

- Include Home Assistant area names in entity search results.

## 0.3.3

- Improve entity search with tokenized matching and domain filters.

## 0.3.2

- Test release to verify App Store update detection.

## 0.3.1

- Set the entity and mapping panels to an even 50/50 split.

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
