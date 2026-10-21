/* MB Logbook card: trips (with route map), parked drain and charging sessions.
 * Data comes from the mb_logbook WebSocket API; the map is Home Assistant's own
 * <ha-map>, so it uses the same map tiles as HA's map card and nothing else. */

const KM_PER_MI = 1.609344;
const PERIODS = [
  ["7", "7 days"],
  ["30", "30 days"],
  ["90", "90 days"],
  ["365", "1 year"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class MBLogbookCard extends HTMLElement {
  setConfig(config) {
    this._config = { days: 30, ...config };
    this._days = String(this._config.days);
    this._tab = "trips";
    this._open = null;
    this._cars = null;
    this._car = null;
    this._trips = [];
    this._charges = [];
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._map) this._map.hass = hass;
    if (first) this._init();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  static getStubConfig() {
    return { days: 30 };
  }

  async _init() {
    try {
      this._cars = await this._hass.callWS({ type: "mb_logbook/cars" });
    } catch (e) {
      this._error = "MB Logbook isn't loaded.";
      this._render();
      return;
    }
    const wanted = this._config.entry_id || (this._config.car || "").toLowerCase();
    this._car =
      this._cars.find((c) => c.entry_id === wanted || c.name.toLowerCase() === wanted) || this._cars[0] || null;
    await this._load();
  }

  async _load() {
    if (!this._car) {
      this._error = "No cars set up yet.";
      this._render();
      return;
    }
    const end = Date.now() / 1000 + 86400;
    const start = Date.now() / 1000 - Number(this._days) * 86400;
    const args = { entry_id: this._car.entry_id, start, end };
    const [trips, charges] = await Promise.all([
      this._hass.callWS({ type: "mb_logbook/trips", ...args }),
      this._hass.callWS({ type: "mb_logbook/charges", ...args }),
    ]);
    this._trips = trips;
    this._charges = charges;
    this._open = null;
    this._render();
  }

  // ----- formatting -----------------------------------------------------------------

  get _miles() {
    return this._car && this._car.unit === "mi";
  }

  _dist(km, digits = 1) {
    if (km == null) return "–";
    const v = this._miles ? km / KM_PER_MI : km;
    return `${v.toFixed(digits)} ${this._miles ? "mi" : "km"}`;
  }

  _kwh(trip) {
    const cap = this._car.capacity_kwh;
    if (!cap || trip.soc_start == null || trip.soc_end == null) return null;
    return Math.max(trip.soc_start - trip.soc_end, 0) / 100 * cap;
  }

  _eff(km, kwh) {
    if (!km || !kwh) return "–";
    return this._miles ? `${(km / KM_PER_MI / kwh).toFixed(1)} mi/kWh` : `${((kwh / km) * 100).toFixed(1)} kWh/100km`;
  }

  _when(ts) {
    const d = new Date(ts * 1000);
    const loc = this._hass.locale?.language || undefined;
    return d.toLocaleString(loc, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  _time(ts) {
    return new Date(ts * 1000).toLocaleTimeString(this._hass.locale?.language || undefined, { hour: "2-digit", minute: "2-digit" });
  }

  _dur(sec) {
    const m = Math.round(sec / 60);
    return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
  }

  _money(v) {
    if (v == null) return "–";
    const cur = this._hass.config.currency || "GBP";
    try {
      return new Intl.NumberFormat(this._hass.locale?.language, { style: "currency", currency: cur }).format(v);
    } catch (e) {
      return v.toFixed(2);
    }
  }

  _place(name, lat) {
    if (name) return esc(name);
    return lat == null ? "–" : "<span class=dim>unnamed</span>";
  }

  // ----- summary ----------------------------------------------------------------------

  _summary() {
    const t = this._trips;
    const km = t.reduce((s, x) => s + x.distance_km, 0);
    let kwh = 0;
    let kmKnown = 0;
    for (const x of t) {
      const k = this._kwh(x);
      if (k) {
        kwh += k;
        kmKnown += x.distance_km;
      }
    }
    const rates = t
      .filter((x) => !x.park_charged && x.park_hours >= 6 && x.park_soc_drop != null)
      .map((x) => (x.park_soc_drop / x.park_hours) * 24)
      .sort((a, b) => a - b);
    const drain = rates.length ? rates[Math.floor(rates.length / 2)] : null;
    const ch = this._charges;
    const chKwh = ch.reduce((s, c) => s + (c.kwh_meter ?? c.kwh_car ?? 0), 0);
    const chCost = ch.reduce((s, c) => s + (c.cost ?? 0), 0);
    const tiles = [
      ["Trips", t.length],
      ["Distance", this._dist(km, 0)],
      ["Energy used", kwh ? `${kwh.toFixed(0)} kWh` : "–"],
      ["Efficiency", this._eff(kmKnown, kwh)],
      ["Idle drain", drain != null ? `${drain.toFixed(2)} %/day` : "–"],
      ["Charged", chKwh ? `${chKwh.toFixed(0)} kWh` : "–"],
    ];
    if (this._car.has_meter) tiles.push(["Charging cost", chCost ? this._money(chCost) : "–"]);
    return tiles.map(([k, v]) => `<div class=tile><div class=k>${k}</div><div class=v>${v}</div></div>`).join("");
  }

  // ----- tables -----------------------------------------------------------------------

  _tripRows() {
    if (!this._trips.length) return `<tr><td colspan=8 class=empty>No trips in this period yet.</td></tr>`;
    return this._trips
      .map((x) => {
        const kwh = this._kwh(x);
        const used = x.soc_start != null && x.soc_end != null ? `${x.soc_start}→${x.soc_end}%` : "–";
        let park = "–";
        if (x.park_hours != null) {
          const h = x.park_hours >= 48 ? `${(x.park_hours / 24).toFixed(1)} d` : `${x.park_hours.toFixed(1)} h`;
          if (x.park_charged) park = `${h} <span class=chip>charged</span>`;
          else if (x.park_soc_drop != null) park = `${h} · −${x.park_soc_drop}%`;
          else park = h;
        }
        const open = this._open === x.id;
        return `<tr class="trip ${open ? "open" : ""}" data-id="${x.id}">
            <td>${this._when(x.start_ts)}<div class=dim>${this._dur(x.end_ts - x.start_ts)}</div></td>
            <td>${this._place(x.start_place, x.start_lat)} → ${this._place(x.end_place, x.end_lat)}</td>
            <td class=n>${this._dist(x.distance_km)}</td>
            <td class=n>${used}</td>
            <td class=n>${kwh ? kwh.toFixed(1) + " kWh" : "–"}</td>
            <td class=n>${this._eff(x.distance_km, kwh)}</td>
            <td class=n>${park}</td>
            <td class=n>${x.point_count > 1 ? "🗺" : ""}</td>
          </tr>${open ? `<tr class=maprow><td colspan=8><div id=mapbox></div></td></tr>` : ""}`;
      })
      .join("");
  }

  _chargeRows() {
    if (!this._charges.length) return `<tr><td colspan=7 class=empty>No charging sessions in this period yet.</td></tr>`;
    return this._charges
      .map((c) => {
        const kwh = c.kwh_meter ?? c.kwh_car;
        const src = c.kwh_meter != null ? "metered" : "from battery %";
        return `<tr>
          <td>${this._when(c.start_ts)}<div class=dim>${this._dur(c.end_ts - c.start_ts)}</div></td>
          <td>${c.place ? esc(c.place) : "<span class=dim>unnamed</span>"}${c.at_home ? " <span class=chip>home</span>" : ""}</td>
          <td class=n>${c.soc_start ?? "–"}→${c.soc_end ?? "–"}%</td>
          <td class=n>${kwh != null ? kwh.toFixed(1) + " kWh" : "–"}<div class=dim>${src}</div></td>
          <td class=n>${c.max_power_kw ? c.max_power_kw.toFixed(1) + " kW" : "–"}</td>
          <td class=n>${this._money(c.cost)}</td>
          <td class=n>${c.kwh_meter && c.cost ? this._money(c.cost / c.kwh_meter) : ""}</td>
        </tr>`;
      })
      .join("");
  }

  // ----- render -----------------------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    if (this._error) {
      root.innerHTML = `<ha-card><div class=pad>${esc(this._error)}</div></ha-card>`;
      return;
    }
    const cars =
      this._cars.length > 1 && !this._config.entry_id
        ? `<div class=seg>${this._cars
            .map((c) => `<button data-car="${c.entry_id}" class="${c.entry_id === this._car.entry_id ? "on" : ""}">${esc(c.name)}${c.trip_active ? " •" : ""}</button>`)
            .join("")}</div>`
        : "";
    const periods = `<div class=seg>${PERIODS.map(([v, l]) => `<button data-days="${v}" class="${v === this._days ? "on" : ""}">${l}</button>`).join("")}</div>`;
    const tabs = `<div class=seg>${[["trips", "Trips"], ["charges", "Charging"]]
      .map(([v, l]) => `<button data-tab="${v}" class="${v === this._tab ? "on" : ""}">${l}</button>`)
      .join("")}</div>`;
    const table =
      this._tab === "trips"
        ? `<table><thead><tr><th>Start</th><th>Route</th><th class=n>Distance</th><th class=n>Battery</th><th class=n>Used</th><th class=n>Efficiency</th><th class=n>Parked before</th><th></th></tr></thead><tbody>${this._tripRows()}</tbody></table>`
        : `<table><thead><tr><th>Start</th><th>Where</th><th class=n>Battery</th><th class=n>Energy</th><th class=n>Peak</th><th class=n>Cost</th><th class=n>Per kWh</th></tr></thead><tbody>${this._chargeRows()}</tbody></table>`;
    root.innerHTML = `<style>${MBLogbookCard.css}</style>
      <ha-card>
        <div class=head><div class=title>${esc(this._config.title || this._car.name + " logbook")}${this._car.trip_active ? ' <span class="chip live">driving</span>' : ""}${this._car.charge_active ? ' <span class="chip live">charging</span>' : ""}</div>${cars}</div>
        <div class=bar>${periods}${tabs}</div>
        <div class=tiles>${this._summary()}</div>
        <div class=scroll>${table}</div>
      </ha-card>`;
    root.querySelectorAll("button[data-car]").forEach((b) =>
      b.addEventListener("click", () => {
        this._car = this._cars.find((c) => c.entry_id === b.dataset.car);
        this._load();
      })
    );
    root.querySelectorAll("button[data-days]").forEach((b) =>
      b.addEventListener("click", () => {
        this._days = b.dataset.days;
        this._load();
      })
    );
    root.querySelectorAll("button[data-tab]").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        this._render();
      })
    );
    root.querySelectorAll("tr.trip").forEach((tr) =>
      tr.addEventListener("click", () => {
        const id = Number(tr.dataset.id);
        this._open = this._open === id ? null : id;
        this._render();
        if (this._open != null) this._showMap(this._open);
      })
    );
  }

  async _ensureMap() {
    if (customElements.get("ha-map")) return true;
    try {
      const helpers = await window.loadCardHelpers();
      helpers.createCardElement({ type: "map", entities: ["zone.home"] });
      await Promise.race([customElements.whenDefined("ha-map"), new Promise((r) => setTimeout(r, 5000))]);
    } catch (e) {
      /* fall through */
    }
    return !!customElements.get("ha-map");
  }

  async _showMap(id) {
    const box = this.shadowRoot.getElementById("mapbox");
    if (!box) return;
    const trip = this._trips.find((t) => t.id === id);
    let points = [];
    try {
      points = await this._hass.callWS({ type: "mb_logbook/trip_points", trip_id: id });
    } catch (e) {
      /* no points */
    }
    points = points.filter((p) => p[1] != null && p[2] != null);
    if (points.length < 2 || !(await this._ensureMap())) {
      box.innerHTML = `<div class="pad dim">No route recorded for this trip.</div>`;
      return;
    }
    const map = document.createElement("ha-map");
    map.hass = this._hass;
    map.autoFit = true;
    map.fitZones = false;
    map.paths = [
      {
        color: "var(--primary-color)",
        gradualOpacity: 0,
        fullDatetime: false,
        name: `${this._time(trip.start_ts)} → ${this._time(trip.end_ts)}`,
        points: points.map((p) => ({ timestamp: new Date(p[0] * 1000), latitude: p[1], longitude: p[2] })),
      },
    ];
    box.innerHTML = "";
    box.appendChild(map);
    this._map = map;
  }
}

MBLogbookCard.css = `
  ha-card { padding: 12px 14px 8px; }
  .pad { padding: 12px; }
  .head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
  .title { font-size: 1.2em; font-weight: 600; }
  .bar { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 10px; overflow: hidden; }
  .seg button { background: none; border: 0; color: var(--secondary-text-color); padding: 6px 12px; font: inherit; cursor: pointer; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; margin-bottom: 10px; }
  .tile { background: var(--secondary-background-color); border-radius: 10px; padding: 8px 10px; }
  .k { font-size: .78em; color: var(--secondary-text-color); }
  .v { font-size: 1.1em; font-weight: 600; margin-top: 2px; }
  .scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: .9em; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 6px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  td { padding: 6px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  .n { text-align: right; white-space: nowrap; }
  tr.trip { cursor: pointer; }
  tr.trip:hover, tr.trip.open { background: var(--secondary-background-color); }
  .dim { color: var(--secondary-text-color); font-size: .85em; }
  .empty { text-align: center; color: var(--secondary-text-color); padding: 18px; }
  .chip { font-size: .75em; padding: 1px 6px; border-radius: 8px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .chip.live { background: var(--success-color, #43a047); color: #fff; }
  #mapbox { height: 320px; border-radius: 10px; overflow: hidden; }
  #mapbox ha-map { height: 100%; display: block; }
`;

if (!customElements.get("mb-logbook-card")) {
  customElements.define("mb-logbook-card", MBLogbookCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "mb-logbook-card",
    name: "MB Logbook",
    description: "Trips, route maps, parked drain and charging sessions from MB Logbook.",
  });
}
