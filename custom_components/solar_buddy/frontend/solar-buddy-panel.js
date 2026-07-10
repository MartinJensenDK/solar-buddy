/*
 * Solar Buddy management panel.
 *
 * A dependency-free custom element registered as a Home Assistant sidebar
 * panel. It discovers the integration's own entities (via the entity
 * registry `platform` field) and presents them as one clear control page.
 * All values and actions go through the normal `hass` object, so the panel
 * stays a thin view over the entities that already exist on the device.
 */

const WEEKDAYS = [
  ["mon", "Man"],
  ["tue", "Tir"],
  ["wed", "Ons"],
  ["thu", "Tor"],
  ["fri", "Fre"],
  ["sat", "Lør"],
  ["sun", "Søn"],
];

class SolarBuddyPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._built = false;
    this._renderScheduled = false;

    // One set of delegated listeners for the whole page.
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
  }

  set hass(hass) {
    this._hass = hass;
    this._scheduleRender();
  }

  set panel(panel) {
    this._panel = panel;
    this._scheduleRender();
  }

  set narrow(_narrow) {
    // Layout is responsive via CSS; nothing to do here.
  }

  _scheduleRender() {
    if (this._renderScheduled) return;
    this._renderScheduled = true;
    requestAnimationFrame(() => {
      this._renderScheduled = false;
      this._render();
    });
  }

  // ---- entity discovery -------------------------------------------------

  get _entryId() {
    return this._panel && this._panel.config ? this._panel.config.entry_id : null;
  }

  _entityMap() {
    // translation_key -> entity_id, for every entity owned by this
    // integration (single config entry, so no ambiguity).
    const map = {};
    const registry = this._hass ? this._hass.entities : null;
    if (!registry) return map;
    for (const entityId in registry) {
      const entry = registry[entityId];
      if (!entry || entry.platform !== "solar_buddy") continue;
      if (entry.translation_key) map[entry.translation_key] = entityId;
    }
    return map;
  }

  _stateObj(entityId) {
    if (!entityId || !this._hass) return null;
    return this._hass.states[entityId] || null;
  }

  _fmt(stateObj) {
    if (!stateObj) return "–";
    if (typeof this._hass.formatEntityState === "function") {
      try {
        return this._hass.formatEntityState(stateObj);
      } catch (_err) {
        /* fall through */
      }
    }
    return stateObj.state;
  }

  _name(stateObj, fallback) {
    if (stateObj && stateObj.attributes && stateObj.attributes.friendly_name) {
      return stateObj.attributes.friendly_name;
    }
    return fallback || "";
  }

  // ---- actions ----------------------------------------------------------

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset && el.dataset.action);
    if (!target) return;
    const { action, entity } = target.dataset;
    if (action === "toggle") {
      this._hass.callService("switch", "toggle", { entity_id: entity });
    } else if (action === "press") {
      this._hass.callService("button", "press", { entity_id: entity });
    }
  }

  _onChange(ev) {
    const el = ev.target;
    if (!el || !el.dataset || !el.dataset.action) return;
    const { action, entity } = el.dataset;
    if (action === "select") {
      this._hass.callService("select", "select_option", {
        entity_id: entity,
        option: el.value,
      });
    } else if (action === "set-time") {
      const time = el.value.length === 5 ? `${el.value}:00` : el.value;
      this._hass.callService("time", "set_value", { entity_id: entity, time });
    } else if (action === "set-number") {
      this._hass.callService("number", "set_value", {
        entity_id: entity,
        value: parseFloat(el.value),
      });
    }
  }

  // ---- rendering --------------------------------------------------------

  _render() {
    if (!this._hass) return;

    // Don't rebuild while the user is typing into a field.
    const active = this.shadowRoot.activeElement;
    if (
      this._built &&
      active &&
      active.tagName === "INPUT" &&
      (active.type === "number" || active.type === "time")
    ) {
      return;
    }

    const e = this._entityMap();
    if (Object.keys(e).length === 0) {
      this.shadowRoot.innerHTML = this._shell(
        `<div class="empty">Solar Buddy er ikke konfigureret endnu.</div>`
      );
      this._built = true;
      return;
    }

    const sections = [
      this._headerCard(e),
      this._controlCard(e),
      this._metricsCard(e),
      this._batteryCard(e),
      this._scheduleCard(e),
      this._exportCard(e),
      this._statusCard(e),
    ].filter(Boolean);

    this.shadowRoot.innerHTML = this._shell(sections.join(""));
    this._built = true;
  }

  _shell(inner) {
    return `${this._styles()}<div class="wrap">${inner}</div>`;
  }

  _headerCard(e) {
    const status = this._stateObj(e.status);
    const recommendation = this._stateObj(e.recommendation);
    const auto = this._stateObj(e.automatic_control);
    const isOn = auto && auto.state === "on";
    const override = this._stateObj(e.manual_override);
    const paused = override && override.state === "on";

    const badge = paused
      ? `<span class="badge warn"><ha-icon icon="mdi:hand-back-right"></ha-icon> Manuel pause</span>`
      : isOn
      ? `<span class="badge on"><ha-icon icon="mdi:robot"></ha-icon> Automatik til</span>`
      : `<span class="badge off"><ha-icon icon="mdi:eye-outline"></ha-icon> Kun overvågning</span>`;

    return `
      <ha-card class="hero">
        <div class="hero-row">
          <div class="hero-icon"><ha-icon icon="mdi:solar-power-variant"></ha-icon></div>
          <div class="hero-text">
            <div class="hero-title">Solar Buddy</div>
            <div class="hero-sub">${this._fmt(status)}</div>
          </div>
          ${badge}
        </div>
        ${
          recommendation
            ? `<div class="hero-reco"><ha-icon icon="mdi:lightbulb-on-outline"></ha-icon> ${this._fmt(
                recommendation
              )}</div>`
            : ""
        }
      </ha-card>`;
  }

  _controlCard(e) {
    const auto = this._stateObj(e.automatic_control);
    const isOn = auto && auto.state === "on";
    const toggle = auto
      ? `<button class="bigtoggle ${isOn ? "on" : ""}" data-action="toggle" data-entity="${
          e.automatic_control
        }">
          <ha-icon icon="${isOn ? "mdi:robot" : "mdi:robot-off-outline"}"></ha-icon>
          <span>${isOn ? "Automatik er slået til" : "Automatik er slået fra"}</span>
          <span class="pill">${isOn ? "TIL" : "FRA"}</span>
        </button>`
      : "";

    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:tune"></ha-icon> Styring</div>
        <div class="card-body">
          ${toggle}
          <div class="selects">
            ${this._selectRow(e.strategy, "Strategi")}
            ${this._selectRow(e.priority, "Prioritet")}
          </div>
        </div>
      </ha-card>`;
  }

  _selectRow(entityId, label) {
    const stateObj = this._stateObj(entityId);
    if (!stateObj) return "";
    const options = stateObj.attributes.options || [];
    const opts = options
      .map((opt) => {
        const display = this._hass.formatEntityState
          ? this._hass.formatEntityState(stateObj, opt)
          : opt;
        const selected = opt === stateObj.state ? " selected" : "";
        return `<option value="${opt}"${selected}>${display}</option>`;
      })
      .join("");
    return `
      <label class="field">
        <span class="field-label">${label}</span>
        <select data-action="select" data-entity="${entityId}">${opts}</select>
      </label>`;
  }

  _metricsCard(e) {
    const tiles = [
      this._tile(e.solar_surplus, "mdi:transmission-tower-import", "Soloverskud"),
      this._tile(e.available_ev_power, "mdi:ev-station", "Til bilen"),
      this._tile(e.recommended_ev_current, "mdi:current-ac", "Anbefalet strøm"),
      this._tile(e.current_price, "mdi:cash", "Aktuel elpris"),
      this._tile(e.price_level, "mdi:chart-line-variant", "Prisniveau"),
    ]
      .filter(Boolean)
      .join("");
    if (!tiles) return "";
    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:gauge"></ha-icon> Live</div>
        <div class="metrics">${tiles}</div>
      </ha-card>`;
  }

  _tile(entityId, icon, label) {
    const stateObj = this._stateObj(entityId);
    if (!stateObj) return "";
    return `
      <div class="tile">
        <ha-icon icon="${icon}"></ha-icon>
        <div class="tile-val">${this._fmt(stateObj)}</div>
        <div class="tile-label">${label}</div>
      </div>`;
  }

  _batteryCard(e) {
    const soc = this._stateObj(e.battery_soc);
    if (!soc) return null;
    const charge = this._stateObj(e.battery_charge_power);
    const discharge = this._stateObj(e.battery_discharge_power);
    const pct = Math.max(0, Math.min(100, parseFloat(soc.state) || 0));
    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:home-battery"></ha-icon> Husbatteri</div>
        <div class="card-body">
          <div class="soc">
            <div class="soc-top"><span>Ladestand</span><span>${this._fmt(soc)}</span></div>
            <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
          </div>
          <div class="metrics">
            ${this._tile(e.battery_charge_power, "mdi:battery-charging", "Oplader")}
            ${this._tile(e.battery_discharge_power, "mdi:battery-minus", "Aflader")}
          </div>
        </div>
      </ha-card>`;
  }

  _scheduleCard(e) {
    if (!e.charge_allowed_mon) return null;
    const chips = WEEKDAYS.map(([day, label]) => {
      const entityId = e[`charge_allowed_${day}`];
      const stateObj = this._stateObj(entityId);
      const on = stateObj && stateObj.state === "on";
      return `<button class="chip ${on ? "on" : ""}" data-action="toggle" data-entity="${entityId}">${label}</button>`;
    }).join("");

    const start = this._stateObj(e.ev_schedule_start);
    const end = this._stateObj(e.ev_schedule_end);
    const timeRow =
      start && end
        ? `<div class="times">
            ${this._timeField(e.ev_schedule_start, "Fra", start.state)}
            ${this._timeField(e.ev_schedule_end, "Til", end.state)}
          </div>
          <div class="hint">Er "Fra" og "Til" ens, må bilen lade hele døgnet.</div>`
        : "";

    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:calendar-clock"></ha-icon> Ladeplan for bilen</div>
        <div class="card-body">
          <div class="field-label">Tilladte dage</div>
          <div class="chips">${chips}</div>
          ${timeRow}
        </div>
      </ha-card>`;
  }

  _timeField(entityId, label, value) {
    const hhmm = (value || "00:00:00").slice(0, 5);
    return `
      <label class="field">
        <span class="field-label">${label}</span>
        <input type="time" value="${hhmm}" data-action="set-time" data-entity="${entityId}" />
      </label>`;
  }

  _exportCard(e) {
    const threshold = this._stateObj(e.export_price_threshold);
    if (!threshold) return null;
    const attrs = threshold.attributes || {};
    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:transmission-tower-export"></ha-icon> Eksport af strøm</div>
        <div class="card-body">
          <div class="hint">Stop eksport til nettet når prisen er lig med eller under denne værdi.</div>
          <label class="field">
            <span class="field-label">Pristærskel</span>
            <input type="number"
              value="${threshold.state}"
              min="${attrs.min != null ? attrs.min : -100}"
              max="${attrs.max != null ? attrs.max : 100}"
              step="${attrs.step != null ? attrs.step : 0.01}"
              data-action="set-number" data-entity="${e.export_price_threshold}" />
          </label>
        </div>
      </ha-card>`;
  }

  _statusCard(e) {
    const rows = [
      this._statusRow(e.data_ready, "mdi:database-check"),
      this._statusRow(e.ev_connected, "mdi:ev-plug-type2"),
      this._statusRow(e.solar_surplus_available, "mdi:weather-sunny"),
      this._statusRow(e.next_action, "mdi:clock-outline"),
      this._statusRow(e.last_evaluation, "mdi:update"),
      this._statusRow(e.last_command, "mdi:send-check-outline"),
    ]
      .filter(Boolean)
      .join("");

    const buttons = `
      <div class="actions">
        ${
          e.recalculate
            ? `<button class="action" data-action="press" data-entity="${e.recalculate}"><ha-icon icon="mdi:refresh"></ha-icon> Genberegn</button>`
            : ""
        }
        ${
          e.clear_manual_override
            ? `<button class="action" data-action="press" data-entity="${e.clear_manual_override}"><ha-icon icon="mdi:play"></ha-icon> Ophæv manuel pause</button>`
            : ""
        }
      </div>`;

    return `
      <ha-card>
        <div class="card-title"><ha-icon icon="mdi:information-outline"></ha-icon> Status</div>
        <div class="card-body">
          <div class="status-list">${rows}</div>
          ${buttons}
        </div>
      </ha-card>`;
  }

  _statusRow(entityId, icon) {
    const stateObj = this._stateObj(entityId);
    if (!stateObj) return "";
    return `
      <div class="status-row">
        <ha-icon icon="${icon}"></ha-icon>
        <span class="status-name">${this._name(stateObj)}</span>
        <span class="status-val">${this._fmt(stateObj)}</span>
      </div>`;
  }

  _styles() {
    return `<style>
      :host { display: block; background: var(--primary-background-color); min-height: 100%; }
      .wrap {
        max-width: 960px; margin: 0 auto; padding: 16px;
        display: grid; gap: 16px;
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      ha-card { padding: 16px; }
      .hero, .metrics-wide { grid-column: 1 / -1; }
      @media (max-width: 720px) { .wrap { grid-template-columns: 1fr; } }

      .empty { grid-column: 1 / -1; text-align: center; color: var(--secondary-text-color); padding: 48px 16px; }

      .card-title {
        display: flex; align-items: center; gap: 8px;
        font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color);
        margin-bottom: 12px;
      }
      .card-title ha-icon { color: var(--primary-color); }
      .card-body { display: grid; gap: 14px; }
      .field-label { font-size: .8rem; color: var(--secondary-text-color); }
      .hint { font-size: .8rem; color: var(--secondary-text-color); }

      /* Hero */
      .hero { grid-column: 1 / -1; }
      .hero-row { display: flex; align-items: center; gap: 14px; }
      .hero-icon {
        --mdc-icon-size: 30px; width: 52px; height: 52px; border-radius: 14px;
        display: flex; align-items: center; justify-content: center;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .hero-text { flex: 1; min-width: 0; }
      .hero-title { font-size: 1.3rem; font-weight: 700; color: var(--primary-text-color); }
      .hero-sub { color: var(--secondary-text-color); }
      .hero-reco {
        margin-top: 12px; padding: 10px 12px; border-radius: 10px;
        background: var(--secondary-background-color);
        display: flex; align-items: center; gap: 8px; color: var(--primary-text-color);
      }
      .hero-reco ha-icon { color: var(--warning-color, #ffa600); }

      .badge {
        display: inline-flex; align-items: center; gap: 6px; white-space: nowrap;
        padding: 6px 10px; border-radius: 999px; font-size: .8rem; font-weight: 600;
        --mdc-icon-size: 16px;
      }
      .badge.on { background: rgba(76,175,80,.15); color: var(--success-color, #4caf50); }
      .badge.off { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.warn { background: rgba(255,166,0,.15); color: var(--warning-color, #ffa600); }

      /* Big toggle */
      .bigtoggle {
        display: flex; align-items: center; gap: 12px; width: 100%;
        padding: 14px 16px; border-radius: 12px; cursor: pointer;
        border: 2px solid var(--divider-color); background: var(--card-background-color);
        color: var(--primary-text-color); font-size: 1rem; font-weight: 600;
        --mdc-icon-size: 24px; transition: all .15s ease;
      }
      .bigtoggle .pill {
        margin-left: auto; padding: 4px 12px; border-radius: 999px; font-size: .8rem;
        background: var(--secondary-background-color); color: var(--secondary-text-color);
      }
      .bigtoggle.on {
        border-color: var(--primary-color);
        background: rgba(3,169,244,.08);
      }
      .bigtoggle.on ha-icon { color: var(--primary-color); }
      .bigtoggle.on .pill { background: var(--primary-color); color: var(--text-primary-color, #fff); }

      .selects { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      @media (max-width: 480px) { .selects { grid-template-columns: 1fr; } }
      .field { display: grid; gap: 6px; }
      select, input[type="time"], input[type="number"] {
        padding: 10px 12px; border-radius: 10px; font-size: 1rem;
        border: 1px solid var(--divider-color);
        background: var(--card-background-color); color: var(--primary-text-color);
      }
      select:focus, input:focus { outline: 2px solid var(--primary-color); border-color: var(--primary-color); }

      /* Metrics */
      .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
      .tile {
        background: var(--secondary-background-color); border-radius: 12px; padding: 14px;
        text-align: center; --mdc-icon-size: 22px;
      }
      .tile ha-icon { color: var(--primary-color); }
      .tile-val { font-size: 1.15rem; font-weight: 700; color: var(--primary-text-color); margin-top: 4px; }
      .tile-label { font-size: .78rem; color: var(--secondary-text-color); margin-top: 2px; }

      /* Battery */
      .soc-top { display: flex; justify-content: space-between; font-size: .85rem; color: var(--secondary-text-color); margin-bottom: 6px; }
      .bar { height: 12px; border-radius: 999px; background: var(--divider-color); overflow: hidden; }
      .bar-fill { height: 100%; background: var(--success-color, #4caf50); border-radius: 999px; transition: width .3s ease; }

      /* Chips */
      .chips { display: flex; flex-wrap: wrap; gap: 8px; }
      .chip {
        padding: 8px 14px; border-radius: 999px; cursor: pointer; font-weight: 600; font-size: .9rem;
        border: 1px solid var(--divider-color); background: var(--card-background-color);
        color: var(--secondary-text-color); transition: all .12s ease;
      }
      .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .times { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

      /* Status */
      .status-list { display: grid; gap: 2px; }
      .status-row {
        display: flex; align-items: center; gap: 10px; padding: 8px 0;
        border-bottom: 1px solid var(--divider-color); --mdc-icon-size: 20px;
      }
      .status-row:last-child { border-bottom: none; }
      .status-row ha-icon { color: var(--secondary-text-color); }
      .status-name { color: var(--primary-text-color); }
      .status-val { margin-left: auto; color: var(--secondary-text-color); text-align: right; }

      .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; }
      .action {
        display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
        padding: 10px 16px; border-radius: 10px; font-weight: 600; font-size: .9rem;
        border: 1px solid var(--primary-color); background: transparent; color: var(--primary-color);
        --mdc-icon-size: 18px;
      }
      .action:hover { background: rgba(3,169,244,.08); }
    </style>`;
  }
}

if (!customElements.get("solar-buddy-panel")) {
  customElements.define("solar-buddy-panel", SolarBuddyPanel);
}
