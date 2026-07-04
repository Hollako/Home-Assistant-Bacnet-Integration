const state = {
  status: null,
  entities: [],
  mappings: [],
};

const els = {
  subtitle: document.getElementById("subtitle"),
  haStatus: document.getElementById("haStatus"),
  deviceInstance: document.getElementById("deviceInstance"),
  enabledMappings: document.getElementById("enabledMappings"),
  objectCount: document.getElementById("objectCount"),
  entityRows: document.getElementById("entityRows"),
  mappingRows: document.getElementById("mappingRows"),
  refreshBtn: document.getElementById("refreshBtn"),
  searchInput: document.getElementById("searchInput"),
  typeFilter: document.getElementById("typeFilter"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

async function refresh() {
  els.subtitle.textContent = "Refreshing...";
  const [status, entities, mappings] = await Promise.all([
    api("api/status"),
    api("api/entities"),
    api("api/mappings"),
  ]);
  state.status = status;
  state.entities = entities.entities;
  state.mappings = mappings.mappings;
  render();
}

function render() {
  const status = state.status;
  const objectTotals = Object.values(status.bacnet.objects).reduce((sum, value) => sum + value, 0);
  els.haStatus.textContent = status.home_assistant.connected ? "Connected" : "Waiting";
  els.deviceInstance.textContent = status.config.device_instance;
  els.enabledMappings.textContent = status.mappings.enabled;
  els.objectCount.textContent = objectTotals;
  els.subtitle.textContent = `${status.config.device_name} on ${status.config.bind_address}`;
  renderEntities();
  renderMappings();
}

function renderEntities() {
  const query = els.searchInput.value.trim().toLowerCase();
  const forcedType = els.typeFilter.value;
  const mappingByPoint = new Map(
    state.mappings
      .filter((mapping) => mapping.enabled)
      .map((mapping) => [mappingKey(mapping.entity_id, mapping.object_type, mapping), mapping]),
  );
  const rows = flatPoints()
    .filter(({ entity, point }) => {
      const text = `${entity.entity_id} ${entity.name} ${entity.state} ${point.label} ${point.value ?? ""}`.toLowerCase();
      const allowed = point.allowed_object_types || [point.suggested_object_type || entity.suggested_object_type];
      const typeAllowed = !forcedType || allowed.includes(forcedType);
      return typeAllowed && (!query || text.includes(query));
    })
    .slice(0, 250)
    .map(({ entity, point }) => {
      const objectType = forcedType || point.suggested_object_type || entity.suggested_object_type;
      const mapped = mappingByPoint.get(mappingKey(entity.entity_id, objectType, point));
      const objectLabel = mapped ? `${objectType}-${mapped.instance}` : objectType;
      const valueText = point.value ?? entity.state ?? "";
      return `
        <tr class="${mapped ? "is-published" : ""}">
          <td>
            <div class="entity-name">
              <strong>${escapeHtml(entity.name)}</strong>
              <span>${escapeHtml(entity.entity_id)}</span>
              <span>${escapeHtml(point.label || "State")}</span>
            </div>
          </td>
          <td>${escapeHtml(valueText)}${point.unit ? ` ${escapeHtml(point.unit)}` : ""}</td>
          <td><span class="tag ${mapped ? "published" : ""}">${escapeHtml(objectLabel)}</span></td>
          <td>
            ${mapped ? `
              <span class="instance-text">${escapeHtml(mapped.instance)}</span>
            ` : `
              <input
                class="instance-input"
                type="number"
                min="0"
                max="4194302"
                inputmode="numeric"
                placeholder="Auto"
                aria-label="BACnet object instance"
              >
            `}
          </td>
          <td>
            ${mapped ? `
              <button
                class="unpublish"
                type="button"
                data-delete="${escapeHtml(mapped.id)}"
              >
                Unpublish
              </button>
            ` : `
              <button
                class="primary"
                type="button"
                data-add="${escapeHtml(entity.entity_id)}"
                data-type="${objectType}"
                data-source="${escapeHtml(point.source || "state")}"
                data-attribute="${escapeHtml(point.attribute || "")}"
                data-transform="${escapeHtml(point.transform || "")}"
                data-label="${escapeHtml(point.label || "State")}"
                data-unit="${escapeHtml(point.unit || "")}"
                data-writable="${point.writable ? "true" : "false"}"
              >
                Publish
              </button>
            `}
          </td>
        </tr>
      `;
    });
  els.entityRows.innerHTML = rows.join("") || `<tr><td colspan="5" class="muted">No entities found</td></tr>`;
}

function renderMappings() {
  const rows = state.mappings
    .filter((mapping) => mapping.enabled)
    .map((mapping) => `
      <tr>
        <td>
          <div class="entity-name">
            <strong>${mapping.object_type}-${mapping.instance}</strong>
            <span>${escapeHtml(mapping.object_name || "")}</span>
            <span class="object-edit">
              <input
                class="instance-input"
                type="number"
                min="0"
                max="4194302"
                inputmode="numeric"
                value="${escapeHtml(mapping.instance)}"
                aria-label="BACnet object instance for ${escapeHtml(mapping.object_type)}"
                data-instance-edit="${escapeHtml(mapping.id)}"
              >
              <button class="secondary" type="button" data-save-instance="${escapeHtml(mapping.id)}">Save</button>
            </span>
          </div>
        </td>
        <td>
          <div class="entity-name">
            <strong>${escapeHtml(mapping.entity_id)}</strong>
            <span>${escapeHtml(mapping.point_label || sourceLabel(mapping))}</span>
          </div>
        </td>
        <td>
          ${mapping.last_error ? `<span class="error">${escapeHtml(mapping.last_error)}</span>` : escapeHtml(lastValueText(mapping))}
        </td>
        <td>
          <button class="danger" type="button" data-delete="${mapping.id}">Unpublish</button>
        </td>
      </tr>
    `);
  els.mappingRows.innerHTML = rows.join("") || `<tr><td colspan="4" class="muted">No mappings yet</td></tr>`;
}

async function addMapping(entityId, objectType, point) {
  await api("api/mappings", {
    method: "POST",
    body: JSON.stringify({
      entity_id: entityId,
      object_type: objectType || null,
      instance: point.instance ?? null,
      source: point.source || "state",
      attribute: point.attribute || null,
      transform: point.transform || null,
      point_label: point.label || null,
      units: point.unit || null,
      writable: point.writable,
    }),
  });
  await refresh();
}

async function updateMappingInstance(mappingId, instance) {
  await api(`api/mappings/${mappingId}`, {
    method: "PATCH",
    body: JSON.stringify({ instance }),
  });
  await refresh();
}

async function deleteMapping(mappingId) {
  await api(`api/mappings/${mappingId}`, { method: "DELETE" });
  await refresh();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

els.refreshBtn.addEventListener("click", () => refresh().catch(showError));
els.searchInput.addEventListener("input", renderEntities);
els.typeFilter.addEventListener("change", renderEntities);

document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add]");
  if (add) {
    const instanceInput = add.closest("tr")?.querySelector(".instance-input");
    const instance = normalizeInstance(instanceInput?.value);
    if (instance === undefined) {
      return;
    }
    addMapping(add.dataset.add, add.dataset.type, {
      instance,
      source: add.dataset.source,
      attribute: add.dataset.attribute,
      transform: add.dataset.transform,
      label: add.dataset.label,
      unit: add.dataset.unit,
      writable: add.dataset.writable === "true",
    }).catch(showError);
    return;
  }
  const save = event.target.closest("[data-save-instance]");
  if (save) {
    const input = save.closest("tr")?.querySelector("[data-instance-edit]");
    const instance = normalizeInstance(input?.value);
    if (instance === undefined) {
      return;
    }
    updateMappingInstance(save.dataset.saveInstance, instance).catch(showError);
    return;
  }
  const del = event.target.closest("[data-delete]");
  if (del) {
    deleteMapping(del.dataset.delete).catch(showError);
  }
});

function showError(error) {
  els.subtitle.textContent = error.message;
}

function normalizeInstance(value) {
  const text = String(value || "").trim();
  if (!text) {
    return null;
  }
  const instance = Number(text);
  if (!Number.isInteger(instance) || instance < 0 || instance > 4194302) {
    showError(new Error("Object instance must be a whole number between 0 and 4194302"));
    return undefined;
  }
  return instance;
}

function flatPoints() {
  return state.entities.flatMap((entity) => {
    const points = Array.isArray(entity.points) && entity.points.length
      ? entity.points
      : [{
          key: "state",
          label: "State",
          source: "state",
          suggested_object_type: entity.suggested_object_type,
          value: entity.state,
          unit: entity.unit,
        }];
    return points.map((point) => ({ entity, point }));
  });
}

function sourceKey(item) {
  const source = item.source || "state";
  return source === "attribute" ? `attribute:${item.attribute || ""}` : "state";
}

function mappingKey(entityId, objectType, item) {
  return `${entityId}:${objectType}:${sourceKey(item)}`;
}

function sourceLabel(mapping) {
  return sourceKey(mapping) === "state" ? "State" : sourceKey(mapping).replace("attribute:", "").replaceAll("_", " ");
}

function lastValueText(mapping) {
  if (mapping.last_state === null || mapping.last_state === undefined) {
    return "";
  }
  return `${mapping.last_state}${mapping.units ? ` ${mapping.units}` : ""}`;
}

refresh().catch(showError);
setInterval(() => refresh().catch(() => {}), 30000);
