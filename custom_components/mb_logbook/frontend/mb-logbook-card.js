/* MB Logbook card: trips (with route map), parked drain and charging sessions.
 * Data comes from the mb_logbook WebSocket API; the map is Home Assistant's own
 * <ha-map>, so it uses the same map tiles as HA's map card and nothing else. */

const KM_PER_MI = 1.609344;
// Calendar periods; the arrows step back and forward by one period.
const PERIODS = [
  ["week", "Week"],
  ["month", "Month"],
  ["quarter", "3 months"],
  ["year", "Year"],
];
// Older configs used `days:`.
const DAYS_TO_PERIOD = { 7: "week", 30: "month", 90: "quarter", 365: "year" };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class MBLogbookCard extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._period =
      PERIODS.find(([p]) => p === this._config.period)?.[0] || DAYS_TO_PERIOD[this._config.days] || "month";
    this._offset = 0; // 0 = the current period, 1 = the one before, ...
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
    return { period: "month" };
  }

  /** Start/end (seconds) and a label for the chosen period, `offset` periods back. */
  _range() {
    const loc = this._hass?.locale?.language;
    const now = new Date();
    const o = this._offset;
    let start;
    let end;
    let label;
    if (this._period === "week") {
      const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
      start = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() - 7 * o);
      end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7);
      const last = new Date(end.getTime() - 86400000);
      const f = (d, opts) => d.toLocaleDateString(loc, opts);
      label =
        start.getMonth() === last.getMonth()
          ? `${start.getDate()}–${f(last, { day: "numeric", month: "short", year: "numeric" })}`
          : `${f(start, { day: "numeric", month: "short" })} – ${f(last, { day: "numeric", month: "short", year: "numeric" })}`;
    } else if (this._period === "month") {
      start = new Date(now.getFullYear(), now.getMonth() - o, 1);
      end = new Date(start.getFullYear(), start.getMonth() + 1, 1);
      label = start.toLocaleDateString(loc, { month: "long", year: "numeric" });
    } else if (this._period === "quarter") {
      start = new Date(now.getFullYear(), now.getMonth() - 2 - 3 * o, 1);
      end = new Date(start.getFullYear(), start.getMonth() + 3, 1);
      const last = new Date(end.getFullYear(), end.getMonth() - 1, 1);
      label = `${start.toLocaleDateString(loc, { month: "short" })} – ${last.toLocaleDateString(loc, { month: "short", year: "numeric" })}`;
    } else {
      start = new Date(now.getFullYear() - o, 0, 1);
      end = new Date(start.getFullYear() + 1, 0, 1);
      label = String(start.getFullYear());
    }
    return { start: start.getTime() / 1000, end: end.getTime() / 1000, label };
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
    const { start, end } = this._range();
    const args = { entry_id: this._car.entry_id, start, end };
    const [trips, charges, places, rates] = await Promise.all([
      this._hass.callWS({ type: "mb_logbook/trips", ...args }),
      this._hass.callWS({ type: "mb_logbook/charges", ...args }),
      this._hass.callWS({ type: "mb_logbook/places" }),
      this._hass.callWS({ type: "mb_logbook/rates" }).catch(() => ({ locations: [], rates: [] })),
    ]);
    this._rates = rates;
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

  /** Nearby named places from OpenStreetMap, only when the user asks. */
  async _suggestNames() {
    const box = this.shadowRoot.getElementById("psuggestions");
    const save = this.shadowRoot.getElementById("psave");
    if (!box || !save) return;
    const e = this._edit;
    const lat = e ? e.lat : Number(save.dataset.lat);
    const lon = e ? e.lon : Number(save.dataset.lon);
    const radius = e ? e.radius : Number(this.shadowRoot.getElementById("pradius")?.value || 100);
    box.innerHTML = `<span class=dim>Asking OpenStreetMap…</span>`;
    let list;
    try {
      list = await this._hass.callWS({ type: "mb_logbook/suggest_names", lat, lon, radius_m: Math.max(radius, 150) });
    } catch (err) {
      box.innerHTML = `<span class=dim>${esc(err.message || err.code || "Lookup failed")}</span>`;
      return;
    }
    if (!list.length) {
      box.innerHTML = `<span class=dim>Nothing named nearby on OpenStreetMap.</span>`;
      return;
    }
    box.innerHTML = list
      .map(
        (s, i) =>
          `<button class=suggestion data-i="${i}" title="${esc(s.kind)}">${esc(s.name)} <span class=dim>${s.distance_m} m${s.kind ? " · " + esc(s.kind) : ""}</span></button>`
      )
      .join("");
    box.querySelectorAll("button.suggestion").forEach((b) =>
      b.addEventListener("click", () => {
        const input = this.shadowRoot.getElementById("pname");
        input.value = list[Number(b.dataset.i)].name;
        input.focus();
      })
    );
  }

  _date(ts) {
    return new Date(ts * 1000).toLocaleDateString(this._hass.locale?.language, { day: "numeric", month: "short", year: "numeric" });
  }

  _rateText(rate) {
    return rate === 0 ? "Free" : `${this._money(rate)}/kWh`;
  }

  /** Places tab: each charging location with its rate history. */
  _rateSection() {
    const r = this._rates;
    if (!r || !r.locations.length) return "";
    const admin = this._hass.user?.is_admin;
    const now = Date.now() / 1000;
    const today = new Date().toISOString().slice(0, 10);
    const rows = r.locations
      .map((loc, i) => {
        const hist = r.rates.filter((x) => x.location === loc.location).sort((a, b) => a.valid_from - b.valid_from);
        const current = [...hist].reverse().find((x) => x.valid_from <= now);
        const allMetered = loc.charges > 0 && loc.metered === loc.charges;
        const cur = current
          ? `${this._rateText(current.rate)} <span class=dim>since ${this._date(current.valid_from)}</span>`
          : allMetered
            ? `<span class=dim>metered at the live price</span>`
            : `<span class=dim>no rate</span>`;
        const open = this._openRate === i;
        const histRows = hist.length
          ? hist
              .map(
                (x) => `<div class=ratehist>From ${this._date(x.valid_from)}: <b>${this._rateText(x.rate)}</b>${x.valid_from > now ? " <span class=chip>upcoming</span>" : ""}
                  ${admin ? `<button class=ratedel data-rate="${x.id}">Delete</button>` : ""}</div>`
              )
              .join("")
          : `<div class=dim>No rates yet. Charges here have no cost${loc.metered ? " unless metered" : ""}.</div>`;
        const form = admin
          ? `<div class=nameform>
               <span>New rate from</span><input type=date class=rdate value="${today}">
               <input type=number class=rrate min=0 max=10 step=0.001 placeholder="per kWh, e.g. 0.79">
               <label class=dim><input type=checkbox class=rfree> Free</label>
               <button class="primary rsave" data-loc="${i}">Add rate</button>
               <div class=dim>Charges from that date on use the new rate; earlier charges keep the rate they had. Metered home charges always use the meter.</div>
             </div>`
          : "";
        return `<tr><td>${esc(loc.location)}</td><td>${cur}</td><td class=n>${loc.charges}</td>
            <td class=n><button class=pmap data-rates="${i}">${open ? "Hide" : "Rates"}</button></td></tr>
          ${open ? `<tr class=maprow><td colspan=4>${histRows}${form}</td></tr>` : ""}`;
      })
      .join("");
    return `<h3 class=sub>Charging rates</h3>
      <table><thead><tr><th>Where</th><th>Current rate</th><th class=n>Charges</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
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
        <button id=psuggest title="Asks OpenStreetMap for named places near the pin. Only this point is sent.">Suggest names</button>
        <div id=psuggestions class=suggestions></div>
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
    // Costed charges: metered at home, or at a charging place's rate (free counts as 0).
    const costed = ch.filter((c) => c.cost != null);
    const cKwh = costed.reduce((s, c) => s + (c.kwh_meter ?? c.kwh_car ?? 0), 0);
    const cost = costed.reduce((s, c) => s + c.cost, 0);
    const uncosted = ch.length - costed.length;
    return [
      ["Sessions", ch.length],
      ["Energy added", kwh ? `${kwh.toFixed(0)} kWh` : "–"],
      ["At home", ch.filter((c) => c.at_home).length],
      ["Charging cost", costed.length ? this._money(cost) + (uncosted ? ` <small class=dim>+${uncosted} unpriced</small>` : "") : "–"],
      ["Average per kWh", cKwh ? this._money(cost / cKwh) : "–"],
    ];
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

  /** A charging session shown in the trip list, between the trips around it. */
  _chargeLine(c) {
    const kwh = c.kwh_meter ?? c.kwh_car;
    const t = (ts) => this._time(ts);
    const bits = [
      `${t(c.start_ts)}–${t(c.end_ts)}`,
      `${c.soc_start ?? "–"}→${c.soc_end ?? "–"}%`,
      kwh != null ? `${kwh.toFixed(1)} kWh` : null,
      c.cost ? this._money(c.cost) : null,
    ].filter(Boolean);
    const where = c.place ? esc(c.place) : "unnamed place";
    return `<tr class=chargeline><td colspan=8>⚡ Charged at ${where} <span class=dim>· ${bits.join(" · ")}</span></td></tr>`;
  }

  _tripRows() {
    if (!this._trips.length) return `<tr><td colspan=8 class=empty>No trips in this period.</td></tr>`;
    // Newest first, with each charge placed between the trips it fell between.
    const items = [
      ...this._trips.map((x) => ({ ts: x.start_ts, trip: x })),
      ...this._charges.map((c) => ({ ts: c.start_ts, charge: c })),
    ].sort((a, b) => b.ts - a.ts);
    return items
      .map(({ trip: x, charge }) => {
        if (charge) return this._chargeLine(charge);
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
          </tr>${open ? `<tr class=maprow><td colspan=8>${form}<div id=mapbox></div>${this._hass.user?.is_admin && !naming ? `<div class=tripactions><button class=deltrip data-id="${x.id}">Delete this trip</button></div>` : ""}</td></tr>` : ""}`;
      })
      .join("");
  }

  _chargeRows() {
    if (!this._charges.length) return `<tr><td colspan=7 class=empty>No charging sessions in this period.</td></tr>`;
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
          <td class=n>${this._money(c.cost)}<div class=dim>${{ meter: "metered", rate: "place rate", free: "free" }[c.cost_source] || (c.cost == null ? "no rate" : "")}</div></td>
          <td class=n>${c.cost_source === "rate" || c.cost_source === "free" ? this._money(c.rate) : c.kwh_meter && c.cost ? this._money(c.cost / c.kwh_meter) : ""}</td>
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
        : `<div class=periodbar><div class=seg>${PERIODS.map(([v, l]) => `<button data-period="${v}" class="${v === this._period ? "on" : ""}">${l}</button>`).join("")}</div>
           <div class=stepper><button class=step data-step="1" title="Previous">‹</button><span class=range>${esc(this._range().label)}</span><button class=step data-step="-1" title="Next" ${this._offset === 0 ? "disabled" : ""}>›</button></div></div>`;
    const tabs = `<div class=seg>${[["trips", "Trips"], ["charges", "Charging"], ["places", "Places"]]
      .map(([v, l]) => `<button data-tab="${v}" class="${v === this._tab ? "on" : ""}">${l}</button>`)
      .join("")}</div>`;
    const table =
      this._tab === "trips"
        ? `<table><thead><tr><th>Start</th><th>Route</th><th class=n>Distance</th><th class=n>Battery</th><th class=n>Used</th><th class=n>Efficiency</th><th class=n>Parked before</th><th></th></tr></thead><tbody>${this._tripRows()}</tbody></table>`
        : this._tab === "charges"
          ? `<table><thead><tr><th>Start</th><th>Where</th><th class=n>Battery</th><th class=n>Energy</th><th class=n>Peak</th><th class=n>Cost</th><th class=n>Per kWh</th></tr></thead><tbody>${this._chargeRows()}</tbody></table>`
          : `<table><thead><tr><th>Name</th><th>Radius</th><th class=n>Trip ends / charges</th><th></th></tr></thead><tbody>${this._placeRows()}</tbody></table>
             <div class="dim pad">Named places are shared by all cars. Home Assistant zones take priority where they overlap.</div>
             ${this._rateSection()}`;
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
    root.querySelectorAll("button[data-period]").forEach((b) =>
      b.addEventListener("click", () => {
        this._period = b.dataset.period;
        this._offset = 0;
        this._load();
      })
    );
    root.querySelectorAll("button[data-step]").forEach((b) =>
      b.addEventListener("click", () => {
        this._offset = Math.max(0, this._offset + Number(b.dataset.step));
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
    root.querySelectorAll("button[data-rates]").forEach((b) =>
      b.addEventListener("click", () => {
        const i = Number(b.dataset.rates);
        this._openRate = this._openRate === i ? null : i;
        this._render();
      })
    );
    root.querySelectorAll("button.rsave").forEach((b) =>
      b.addEventListener("click", async () => {
        const row = b.closest("td");
        const loc = this._rates.locations[Number(b.dataset.loc)].location;
        const free = row.querySelector(".rfree").checked;
        const rate = free ? 0 : Number(row.querySelector(".rrate").value);
        const date = row.querySelector(".rdate").value;
        if (!date || (!free && !(rate > 0))) return alert("Enter a rate per kWh (or tick Free) and a start date.");
        try {
          await this._hass.callWS({ type: "mb_logbook/rate_save", location: loc, valid_from: date, rate });
        } catch (e) {
          return alert(`Couldn't save the rate: ${e.message || e.code || e}`);
        }
        const keep = this._openRate;
        await this._load();
        this._openRate = keep;
        this._render();
      })
    );
    root.querySelectorAll("button.ratedel").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Delete this rate? Charges it covered will use the rate before it (or none).")) return;
        await this._hass.callWS({ type: "mb_logbook/rate_delete", rate_id: Number(b.dataset.rate) });
        const keep = this._openRate;
        await this._load();
        this._openRate = keep;
        this._render();
      })
    );
    root.querySelectorAll("button.deltrip").forEach((b) =>
      b.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const t = this._trips.find((x) => x.id === Number(b.dataset.id));
        const what = t ? `${this._when(t.start_ts)}, ${this._dist(t.distance_km)}` : "this trip";
        if (!confirm(`Delete the trip on ${what}? This can't be undone.`)) return;
        try {
          await this._hass.callWS({ type: "mb_logbook/delete_trip", trip_id: Number(b.dataset.id) });
        } catch (e) {
          alert(`Couldn't delete the trip: ${e.message || e.code || e}`);
          return;
        }
        await this._load();
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
      root.getElementById("psuggest").addEventListener("click", () => this._suggestNames());
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
  .suggestions { flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 6px; }
  button.suggestion { padding: 4px 10px; border-radius: 14px; border: 1px solid var(--divider-color); background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; font-size: .9em; cursor: pointer; }
  button.suggestion:hover { border-color: var(--primary-color); }
  h3.sub { font-size: 1em; font-weight: 600; margin: 18px 0 6px; }
  .ratehist { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
  button.ratedel { padding: 2px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: none; color: var(--secondary-text-color); font: inherit; font-size: .85em; cursor: pointer; }
  .tripactions { display: flex; justify-content: flex-end; padding-top: 8px; }
  button.deltrip { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--error-color, #f38ba8); background: none; color: var(--error-color, #f38ba8); font: inherit; cursor: pointer; }
  .periodbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .stepper { display: inline-flex; align-items: center; gap: 4px; }
  .stepper .range { min-width: 9em; text-align: center; font-weight: 500; }
  button.step { width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font-size: 1.2em; line-height: 1; cursor: pointer; }
  button.step:disabled { opacity: .35; cursor: default; }
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
  tr.chargeline td { padding: 4px 6px; font-size: .85em; color: var(--success-color, #a6e3a1); background: color-mix(in srgb, var(--success-color, #a6e3a1) 7%, transparent); }
  #mapbox.small { height: 240px; }
  td button.pmap { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font: inherit; cursor: pointer; }
  #mapbox ha-map { height: 100%; display: block; }
`;

const CARD_VERSION = "0.8.0";

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
