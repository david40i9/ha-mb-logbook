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
    const [trips, charges, places] = await Promise.all([
      this._hass.callWS({ type: "mb_logbook/trips", ...args }),
      this._hass.callWS({ type: "mb_logbook/charges", ...args }),
      this._hass.callWS({ type: "mb_logbook/places" }),
    ]);
    this._trips = trips;
    this._charges = charges;
    this._places = places;
    this._open = null;
    this._naming = null;
    this._edit = null;
    this._render();
  }

  _placeRows() {
    if (!this._places.length)
      return `<tr><td colspan=4 class=empty>No named places yet. Click “unnamed · name it” on a trip or charge to add one.</td></tr>`;
    const admin = this._hass.user?.is_admin;
    const radii = [50, 100, 200, 500, 1000];
    return this._places
      .map((p) => {
        const opts = [...new Set([...radii, Math.round(p.radius_m)])]
          .sort((a, b) => a - b)
          .map((r) => `<option value="${r}" ${r === Math.round(p.radius_m) ? "selected" : ""}>${r < 1000 ? r + " m" : r / 1000 + " km"}</option>`)
          .join("");
        return `<tr data-place="${p.id}">
          <td>${admin ? `<input class=pn value="${esc(p.name)}" maxlength=60>` : esc(p.name)}</td>
          <td>${admin ? `<select class=pr>${opts}</select>` : Math.round(p.radius_m) + " m"}</td>
          <td class=n>${p.uses}</td>
          <td class=n><button class=pmap>${this._openPlace === p.id ? "Hide map" : "Map"}</button>${admin ? ` <button class=psv>Save</button> <button class=pdel>Delete</button>` : ""}</td>
        </tr>${this._openPlace === p.id ? `<tr class=maprow><td colspan=4><div class="dim">Drag the pin to move this place or the circle's edge to resize it, then press Save.</div><div id=mapbox class=small></div></td></tr>` : ""}`;
      })
      .join("");
  }

  /** [[south, west], [north, east]] around a set of [lat, lon] points. */
  _bounds(points) {
    const lats = points.map((p) => p[0]);
    const lons = points.map((p) => p[1]);
    return [
      [Math.min(...lats), Math.min(...lons)],
      [Math.max(...lats), Math.max(...lons)],
    ];
  }

  _circleBounds(lat, lon, radiusM) {
    const dLat = radiusM / 111320;
    const dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180));
    return this._bounds([
      [lat - dLat, lon - dLon],
      [lat + dLat, lon + dLon],
    ]);
  }

  /** The map does its own first positioning when it finishes loading; fit after that. */
  async _whenLoaded(map) {
    for (let i = 0; i < 60 && !map._loaded; i++) await new Promise((r) => setTimeout(r, 100));
    await new Promise((r) => setTimeout(r, 150));
  }

  _editable() {
    const e = this._edit;
    return [
      {
        id: "place",
        location: [e.lat, e.lon],
        radius: e.radius,
        title: e.title || "New place",
        locationEditable: true,
        radiusEditable: true,
      },
    ];
  }

  /**
   * Draw a map in #mapbox: an optional route, and an optional place being
   * edited (drag the pin to move it, drag the circle edge to resize it).
   */
  async _drawMap({ paths = [], edit = false } = {}) {
    const box = this.shadowRoot.getElementById("mapbox");
    if (!box) return;
    if (!(await this._ensureMap())) {
      box.innerHTML = `<div class="pad dim">Map unavailable.</div>`;
      return;
    }
    const map = document.createElement("ha-map");
    map.hass = this._hass;
    map.autoFit = false; // we position it ourselves once it has loaded
    map.fitZones = false;
    map.paths = paths;
    map.editableLocations = edit ? this._editable() : [];
    map.addEventListener("editable-location-moved", (ev) => {
      this._edit.lat = ev.detail.location[0];
      this._edit.lon = ev.detail.location[1];
    });
    map.addEventListener("editable-location-resized", (ev) => {
      this._edit.radius = Math.max(20, Math.round(ev.detail.radius));
      this._syncRadius();
    });
    box.innerHTML = "";
    box.appendChild(map);
    this._map = map;
    await this._whenLoaded(map);
    if (this._map !== map) return; // redrawn meanwhile
    const bounds = edit
      ? this._circleBounds(this._edit.lat, this._edit.lon, this._edit.radius)
      : paths.length
        ? this._bounds(paths.flatMap((p) => p.points.map((q) => [q.latitude, q.longitude])))
        : null;
    if (bounds) map.fitBounds(bounds, { zoom: 17, pad: 0.15 });
  }

  /** Radius picked from the list: resize the circle without moving the view. */
  _setEditRadius(r) {
    if (!this._edit) return;
    this._edit.radius = r;
    if (this._map) this._map.editableLocations = this._editable();
  }

  /** Circle dragged to a new size: show it in the radius list. */
  _syncRadius() {
    const sel =
      this.shadowRoot.getElementById("pradius") ||
      this.shadowRoot.querySelector(`tr[data-place="${this._openPlace}"] .pr`);
    if (!sel) return;
    const r = this._edit.radius;
    if (![...sel.options].some((o) => Number(o.value) === r)) {
      const opt = document.createElement("option");
      opt.value = r;
      opt.textContent = `${r} m`;
      sel.appendChild(opt);
    }
    sel.value = String(r);
  }

  _showPlaceMap(place) {
    this._edit = { lat: place.lat, lon: place.lon, radius: Math.round(place.radius_m), title: place.name };
    this._drawMap({ edit: true });
  }

  /** Map for naming a charge location. */
  _showNamingMap(lat, lon) {
    const r = Number(this.shadowRoot.getElementById("pradius")?.value || 100);
    this._edit = { lat, lon, radius: r };
    this._drawMap({ edit: true });
  }

  async _savePlace(body) {
    try {
      await this._hass.callWS({ type: "mb_logbook/place_save", ...body });
      await this._load();
    } catch (e) {
      alert(`Couldn't save the place: ${e.message || e.code || e}`);
    }
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

  _place(name, lat, kind, id, end) {
    if (name) return esc(name);
    if (lat == null) return "–";
    if (!this._hass.user?.is_admin) return "<span class=dim>unnamed</span>";
    return `<button class=nameit data-kind="${kind}" data-id="${id}" data-end="${end || ""}">unnamed · name it</button>`;
  }

  _nameForm(lat, lon) {
    const radii = [50, 100, 200, 500, 1000]
      .map((r) => `<option value="${r}" ${r === 100 ? "selected" : ""}>${r < 1000 ? r + " m" : "1 km"}</option>`)
      .join("");
    const names = [...new Set((this._places || []).map((p) => p.name))]
      .map((n) => `<option value="${esc(n)}"></option>`)
      .join("");
    return `<div class=nameform>
        <span>Name this place</span>
        <input id=pname list=pnames maxlength=60 placeholder="e.g. Mum's, Tesco Extra" autocomplete=off>
        <datalist id=pnames>${names}</datalist>
        <select id=pradius title="Anything within this distance gets the name">${radii}</select>
        <button class=primary id=psave data-lat="${lat}" data-lon="${lon}">Save</button>
        <button id=pcancel>Cancel</button>
        <div class=dim>Drag the pin on the map to move the place, or the circle's edge to resize it. The name applies to every trip and charge that starts or ends inside the circle, past and future.</div>
      </div>`;
  }

  // ----- summary ----------------------------------------------------------------------

  _summary() {
    if (this._tab === "places") return "";
    const tiles = this._tab === "charges" ? this._chargeTiles() : this._tripTiles();
    return `<div class=tiles>${tiles.map(([k, v]) => `<div class=tile><div class=k>${k}</div><div class=v>${v}</div></div>`).join("")}</div>`;
  }

  _chargeTiles() {
    const ch = this._charges;
    const kwh = ch.reduce((s, c) => s + (c.kwh_meter ?? c.kwh_car ?? 0), 0);
    const metered = ch.filter((c) => c.kwh_meter && c.cost != null);
    const mKwh = metered.reduce((s, c) => s + c.kwh_meter, 0);
    const mCost = metered.reduce((s, c) => s + c.cost, 0);
    const tiles = [
      ["Sessions", ch.length],
      ["Energy added", kwh ? `${kwh.toFixed(0)} kWh` : "–"],
      ["At home", ch.filter((c) => c.at_home).length],
    ];
    if (this._car.has_meter) {
      tiles.push(["Home charging cost", mCost ? this._money(mCost) : "–"]);
      tiles.push(["Average per kWh", mKwh ? this._money(mCost / mKwh) : "–"]);
    }
    return tiles;
  }

  _tripTiles() {
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
    // Battery % only comes in whole steps, so pool the parks (total lost over
    // total time) rather than averaging per-park rates that are mostly 0.
    const dh = (x) => x.park_drain_hours ?? (x.park_charged ? null : x.park_hours);
    const parks = t.filter((x) => x.park_soc_drop != null && dh(x) >= 6);
    const hours = parks.reduce((s, x) => s + dh(x), 0);
    const drain = hours ? (parks.reduce((s, x) => s + x.park_soc_drop, 0) / hours) * 24 : null;
    return [
      ["Trips", t.length],
      ["Distance", this._dist(km, 0)],
      ["Energy used", kwh ? `${kwh.toFixed(0)} kWh` : "–"],
      ["Efficiency", this._eff(kmKnown, kwh)],
      ["Idle drain", drain != null ? `${drain.toFixed(2)} %/day` : "–"],
    ];
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
          // Drain is measured after any charge, so a charged park can still show it.
          const d = x.park_soc_drop;
          const drain = d == null ? "" : d > 0 ? ` · −${d}%` : d < 0 ? ` · +${-d}%` : " · 0%";
          park = `${h}${x.park_charged ? " <span class=chip>charged</span>" : ""}${drain}`;
        }
        const open = this._open === x.id;
        const naming = this._naming && this._naming.kind === "trip" && this._naming.id === x.id ? this._naming : null;
        const form = naming
          ? this._nameForm(naming.end === "start" ? x.start_lat : x.end_lat, naming.end === "start" ? x.start_lon : x.end_lon)
          : "";
        return `<tr class="trip ${open ? "open" : ""}" data-id="${x.id}">
            <td>${this._when(x.start_ts)}<div class=dim>${this._dur(x.end_ts - x.start_ts)}</div></td>
            <td>${this._place(x.start_place, x.start_lat, "trip", x.id, "start")} → ${this._place(x.end_place, x.end_lat, "trip", x.id, "end")}</td>
            <td class=n>${this._dist(x.distance_km)}</td>
            <td class=n>${used}</td>
            <td class=n>${kwh ? kwh.toFixed(1) + " kWh" : "–"}</td>
            <td class=n>${this._eff(x.distance_km, kwh)}</td>
            <td class=n>${park}</td>
            <td class=n>${x.point_count > 1 ? "🗺" : ""}</td>
          </tr>${open ? `<tr class=maprow><td colspan=8>${form}<div id=mapbox></div></td></tr>` : ""}`;
      })
      .join("");
  }

  _chargeRows() {
    if (!this._charges.length) return `<tr><td colspan=7 class=empty>No charging sessions in this period yet.</td></tr>`;
    return this._charges
      .map((c) => {
        const kwh = c.kwh_meter ?? c.kwh_car;
        const src = c.kwh_meter != null ? "metered" : "from battery %";
        const naming = this._naming && this._naming.kind === "charge" && this._naming.id === c.id;
        const formRow = naming
          ? `<tr class=maprow><td colspan=7>${this._nameForm(c.lat, c.lon)}<div id=mapbox class=small></div></td></tr>`
          : "";
        return `<tr>
          <td>${this._when(c.start_ts)}<div class=dim>${this._dur(c.end_ts - c.start_ts)}</div></td>
          <td>${this._place(c.place, c.lat, "charge", c.id)}${c.at_home ? " <span class=chip>home</span>" : ""}</td>
          <td class=n>${c.soc_start ?? "–"}→${c.soc_end ?? "–"}%</td>
          <td class=n>${kwh != null ? kwh.toFixed(1) + " kWh" : "–"}<div class=dim>${src}</div></td>
          <td class=n>${c.max_power_kw ? c.max_power_kw.toFixed(1) + " kW" : "–"}</td>
          <td class=n>${this._money(c.cost)}</td>
          <td class=n>${c.kwh_meter && c.cost ? this._money(c.cost / c.kwh_meter) : ""}</td>
        </tr>${formRow}`;
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
    const periods =
      this._tab === "places"
        ? "<div></div>"
        : `<div class=seg>${PERIODS.map(([v, l]) => `<button data-days="${v}" class="${v === this._days ? "on" : ""}">${l}</button>`).join("")}</div>`;
    const tabs = `<div class=seg>${[["trips", "Trips"], ["charges", "Charging"], ["places", "Places"]]
      .map(([v, l]) => `<button data-tab="${v}" class="${v === this._tab ? "on" : ""}">${l}</button>`)
      .join("")}</div>`;
    const table =
      this._tab === "trips"
        ? `<table><thead><tr><th>Start</th><th>Route</th><th class=n>Distance</th><th class=n>Battery</th><th class=n>Used</th><th class=n>Efficiency</th><th class=n>Parked before</th><th></th></tr></thead><tbody>${this._tripRows()}</tbody></table>`
        : this._tab === "charges"
          ? `<table><thead><tr><th>Start</th><th>Where</th><th class=n>Battery</th><th class=n>Energy</th><th class=n>Peak</th><th class=n>Cost</th><th class=n>Per kWh</th></tr></thead><tbody>${this._chargeRows()}</tbody></table>`
          : `<table><thead><tr><th>Name</th><th>Radius</th><th class=n>Trip ends / charges</th><th></th></tr></thead><tbody>${this._placeRows()}</tbody></table>
             <div class="dim pad">Named places are shared by all cars. Home Assistant zones take priority where they overlap.</div>`;
    root.innerHTML = `<style>${MBLogbookCard.css}</style>
      <ha-card>
        <div class=head><div class=title>${esc(this._config.title || this._car.name + " logbook")}${this._car.trip_active ? ' <span class="chip live">driving</span>' : ""}${this._car.charge_active ? ' <span class="chip live">charging</span>' : ""}</div>${cars}</div>
        <div class=bar>${periods}${tabs}</div>
        ${this._summary()}
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
        this._naming = null;
        this._open = null;
        this._openPlace = null;
        this._render();
      })
    );
    root.querySelectorAll("tr.trip").forEach((tr) =>
      tr.addEventListener("click", () => {
        const id = Number(tr.dataset.id);
        this._open = this._open === id ? null : id;
        this._naming = null;
        this._render();
        if (this._open != null) this._showMap(this._open);
      })
    );
    root.querySelectorAll("button.nameit").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = Number(b.dataset.id);
        this._naming = { kind: b.dataset.kind, id, end: b.dataset.end };
        if (b.dataset.kind === "trip") this._open = id;
        this._render();
        if (b.dataset.kind === "trip") this._showMap(id);
        else {
          const c = this._charges.find((x) => x.id === id);
          this._showNamingMap(c.lat, c.lon);
        }
        root.getElementById("pname")?.focus();
      })
    );
    root.getElementById("pradius")?.addEventListener("change", (e) => this._setEditRadius(Number(e.target.value)));
    const save = root.getElementById("psave");
    if (save) {
      const submit = () => {
        const name = root.getElementById("pname").value.trim();
        if (!name) return root.getElementById("pname").focus();
        // Use the pin/circle as positioned on the map, if it was moved.
        const e = this._edit;
        this._savePlace({
          name,
          lat: e ? e.lat : Number(save.dataset.lat),
          lon: e ? e.lon : Number(save.dataset.lon),
          radius_m: e ? e.radius : Number(root.getElementById("pradius").value),
        });
      };
      save.addEventListener("click", submit);
      root.getElementById("pname").addEventListener("keydown", (e) => e.key === "Enter" && submit());
      root.getElementById("pcancel").addEventListener("click", () => {
        this._naming = null;
        this._render();
        if (this._open != null) this._showMap(this._open);
      });
    }
    root.querySelectorAll("tr[data-place]").forEach((tr) => {
      const p = this._places.find((x) => x.id === Number(tr.dataset.place));
      tr.querySelector(".pmap").addEventListener("click", () => {
        this._openPlace = this._openPlace === p.id ? null : p.id;
        this._render();
        if (this._openPlace != null) this._showPlaceMap(p);
      });
      tr.querySelector(".pr")?.addEventListener("change", (e) => {
        if (this._openPlace === p.id) this._setEditRadius(Number(e.target.value));
      });
      tr.querySelector(".psv")?.addEventListener("click", () => {
        const name = tr.querySelector(".pn").value.trim();
        if (!name) return;
        const e = this._openPlace === p.id ? this._edit : null; // moved/resized on the map
        this._savePlace({
          place_id: p.id,
          name,
          lat: e ? e.lat : p.lat,
          lon: e ? e.lon : p.lon,
          radius_m: e ? e.radius : Number(tr.querySelector(".pr").value),
        });
      });
      tr.querySelector(".pdel")?.addEventListener("click", async () => {
        if (!confirm(`Delete “${p.name}”? Trips there will show as unnamed again.`)) return;
        await this._hass.callWS({ type: "mb_logbook/place_delete", place_id: p.id });
        await this._load();
      });
    });
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
    const paths = [];
    if (points.length >= 2)
      paths.push({
        color: "var(--primary-color)",
        gradualOpacity: 0,
        fullDatetime: false,
        name: `${this._time(trip.start_ts)} → ${this._time(trip.end_ts)}`,
        // HA 2026.x reads `point: [lat, lon]`; latitude/longitude kept for older versions.
        points: points.map((p) => ({ timestamp: new Date(p[0] * 1000), point: [p[1], p[2]], latitude: p[1], longitude: p[2] })),
      });
    // While naming one end, show it as an editable place and zoom to it.
    let edit = false;
    if (this._naming?.kind === "trip" && this._naming.id === id) {
      const lat = this._naming.end === "start" ? trip.start_lat : trip.end_lat;
      const lon = this._naming.end === "start" ? trip.start_lon : trip.end_lon;
      if (lat != null) {
        const r = Number(this.shadowRoot.getElementById("pradius")?.value || 100);
        this._edit = { lat, lon, radius: r };
        edit = true;
      }
    }
    if (!paths.length && !edit) {
      box.innerHTML = `<div class="pad dim">No route recorded for this trip.</div>`;
      return;
    }
    await this._drawMap({ paths, edit });
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
  button.nameit { background: none; border: 1px dashed var(--divider-color); border-radius: 8px; color: var(--secondary-text-color); font: inherit; font-size: .85em; padding: 1px 6px; cursor: pointer; }
  button.nameit:hover { color: var(--primary-color); border-color: var(--primary-color); }
  .nameform { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 0 10px; }
  .nameform input, td input.pn { flex: 1; min-width: 160px; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
  .nameform select, td select.pr { padding: 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
  .nameform button, td button.psv, td button.pdel { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font: inherit; cursor: pointer; }
  .nameform button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .nameform .dim { flex-basis: 100%; }
  #mapbox { height: 320px; border-radius: 10px; overflow: hidden; }
  #mapbox.small { height: 240px; }
  td button.pmap { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font: inherit; cursor: pointer; }
  #mapbox ha-map { height: 100%; display: block; }
`;

const CARD_VERSION = "0.5.0";

if (!customElements.get("mb-logbook-card")) {
  customElements.define("mb-logbook-card", MBLogbookCard);
  console.info(`%c MB-LOGBOOK-CARD %c ${CARD_VERSION} `, "background:#89b4fa;color:#1e1e2e;font-weight:600", "background:#313244;color:#cdd6f4");
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "mb-logbook-card",
    name: "MB Logbook",
    description: "Trips, route maps, parked drain and charging sessions from MB Logbook.",
  });
}
