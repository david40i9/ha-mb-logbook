/* MB Logbook sidebar page: an overview (month figures, 12-month charts, this
 * month's routes, most-visited places) and the full logbook card.
 * Charts are plain SVG and the map is Home Assistant's own <ha-map>; nothing
 * is loaded from outside Home Assistant. */

const KM_PER_MI = 1.609344;
const ROUTE_COLOURS = ["#89b4fa", "#a6e3a1", "#fab387", "#cba6f7", "#f9e2af", "#94e2d5", "#f38ba8", "#74c7ec"];

const escp = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class MBLogbookPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = "overview";
    this._mPeriod = "month"; // mileage-by-label period
    this._mOffset = 0;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    if (this._map) this._map.hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) this._init();
  }

  set narrow(v) {
    this._narrow = v;
    if (this._menu) this._menu.narrow = v;
  }

  set panel(p) {
    this._panel = p;
  }

  async _init() {
    // Make sure the logbook card is defined (same URL as the dashboard resource).
    const url = this._panel?.config?.card_url;
    if (url && !customElements.get("mb-logbook-card")) {
      try {
        await import(url);
      } catch (e) {
        /* the card tab will say so */
      }
    }
    // Right after a restart the integration may not be ready yet: retry a few times.
    this._cars = [];
    for (let i = 0; i < 6 && !this._cars.length; i++) {
      try {
        this._cars = await this._hass.callWS({ type: "mb_logbook/cars" });
      } catch (e) {
        this._cars = [];
      }
      if (!this._cars.length) await new Promise((r) => setTimeout(r, 2000));
    }
    this._car = this._cars[0] || null;
    this._render();
    if (this._car) this._loadOverview();
  }

  get _miles() {
    return this._car?.unit === "mi";
  }

  _d(km, digits = 0) {
    return (this._miles ? km / KM_PER_MI : km).toFixed(digits);
  }

  get _du() {
    return this._miles ? "mi" : "km";
  }

  _eff(km, kwh) {
    if (!km || !kwh) return null;
    return this._miles ? km / KM_PER_MI / kwh : (kwh / km) * 100;
  }

  get _effUnit() {
    return this._miles ? "mi/kWh" : "kWh/100km";
  }

  _money(v) {
    try {
      return new Intl.NumberFormat(this._hass.locale?.language, {
        style: "currency",
        currency: this._hass.config.currency || "GBP",
      }).format(v);
    } catch (e) {
      return v.toFixed(2);
    }
  }

  _monthName(key, long = false) {
    const [y, m] = key.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleString(this._hass.locale?.language, { month: long ? "long" : "short" });
  }

  // ----- layout ----------------------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    const cars =
      this._cars.length > 1
        ? `<div class=seg>${this._cars
            .map((c) => `<button data-car="${c.entry_id}" class="${c.entry_id === this._car?.entry_id ? "on" : ""}">${escp(c.name)}</button>`)
            .join("")}</div>`
        : "";
    const views = `<div class=seg>${[["overview", "Overview"], ["logbook", "Logbook"]]
      .map(([v, l]) => `<button data-view="${v}" class="${v === this._view ? "on" : ""}">${l}</button>`)
      .join("")}</div>`;
    root.innerHTML = `<style>${MBLogbookPanel.css}</style>
      <div class=toolbar><span id=menu></span><div class=apptitle>MB Logbook${this._car ? ` · ${escp(this._car.name)}` : ""}</div>${cars}${views}</div>
      <div class=content id=content>${this._car ? "" : `<div class=empty>No cars set up yet. Add one under Settings → Devices &amp; services → MB Logbook.</div>`}</div>`;

    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    root.getElementById("menu").appendChild(this._menu);

    root.querySelectorAll("button[data-car]").forEach((b) =>
      b.addEventListener("click", () => {
        this._car = this._cars.find((c) => c.entry_id === b.dataset.car);
        this._render();
        this._view === "overview" ? this._loadOverview() : this._showLogbook();
      })
    );
    root.querySelectorAll("button[data-view]").forEach((b) =>
      b.addEventListener("click", () => {
        this._view = b.dataset.view;
        this._render();
        this._view === "overview" ? this._loadOverview() : this._showLogbook();
      })
    );
    if (this._car && this._view === "logbook") this._showLogbook();
  }

  _showLogbook() {
    const box = this.shadowRoot.getElementById("content");
    if (!this._car) return; // the "no cars" message is already shown
    if (!customElements.get("mb-logbook-card")) {
      box.innerHTML = `<div class=empty>The logbook card didn't load. Refresh the page.</div>`;
      return;
    }
    const card = document.createElement("mb-logbook-card");
    card.setConfig({ entry_id: this._car.entry_id, period: "month", title: " " });
    card.hass = this._hass;
    box.innerHTML = "";
    box.appendChild(card);
    this._card = card;
  }

  // ----- overview --------------------------------------------------------------------

  async _loadOverview() {
    this._card = null;
    const box = this.shadowRoot.getElementById("content");
    box.innerHTML = `<div class=empty>Loading…</div>`;
    const car = this._car;
    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime() / 1000;
    let stats;
    let routes;
    try {
      [stats, routes] = await Promise.all([
        this._hass.callWS({ type: "mb_logbook/stats", entry_id: car.entry_id, months: 12 }),
        this._hass.callWS({ type: "mb_logbook/routes", entry_id: car.entry_id, start: monthStart }),
      ]);
    } catch (e) {
      box.innerHTML = `<div class=empty>Couldn't load the figures: ${escp(e.message || e.code || e)}</div>`;
      return;
    }
    if (this._car !== car || this._view !== "overview") return;
    const months = stats.months;
    const cur = months[months.length - 1];
    const prev = months[months.length - 2];

    const delta = (a, b, fmt) => {
      if (b == null || !b) return "";
      const diff = a - b;
      const sign = diff > 0 ? "+" : diff < 0 ? "−" : "±";
      return `<div class=delta>${sign}${fmt(Math.abs(diff))} vs ${this._monthName(prev.month)}</div>`;
    };
    const eff = (m) => this._eff(m.km_with_kwh, m.kwh_used);
    const drain = (m) => (m.idle_hours ? (m.idle_drop / m.idle_hours) * 24 : null);
    const tile = (k, v, d = "") => `<div class=tile><div class=k>${k}</div><div class=v>${v}</div>${d}</div>`;
    const tiles = [
      tile("Trips", cur.trips, delta(cur.trips, prev?.trips, (x) => x)),
      tile("Distance", `${this._d(cur.distance_km)} ${this._du}`, delta(cur.distance_km, prev?.distance_km, (x) => `${this._d(x)} ${this._du}`)),
      tile("Energy used", `${cur.kwh_used.toFixed(0)} kWh`, delta(cur.kwh_used, prev?.kwh_used, (x) => `${x.toFixed(0)} kWh`)),
      tile("Efficiency", eff(cur) ? `${eff(cur).toFixed(2)} ${this._effUnit}` : "–", prev && eff(prev) && eff(cur) ? delta(eff(cur), eff(prev), (x) => x.toFixed(2)) : ""),
      tile("Charged", `${cur.charged_kwh.toFixed(0)} kWh`, delta(cur.charged_kwh, prev?.charged_kwh, (x) => `${x.toFixed(0)} kWh`)),
      tile("Charging cost", this._money(cur.cost), delta(cur.cost, prev?.cost, (x) => this._money(x))),
      tile("Idle drain", drain(cur) != null ? `${drain(cur).toFixed(2)} %/day` : "–"),
    ].join("");

    const labels = months.map((m) => this._monthName(m.month).slice(0, 3));
    const charts = [
      this._chart(`Distance (${this._du})`, labels, [{ values: months.map((m) => +this._d(m.distance_km)), colour: "#89b4fa" }], (v) => v.toFixed(0)),
      this._chart(
        "Energy (kWh): used driving vs charged",
        labels,
        [
          { name: "Used", values: months.map((m) => m.kwh_used), colour: "#fab387" },
          { name: "Charged", values: months.map((m) => m.charged_kwh), colour: "#a6e3a1" },
        ],
        (v) => v.toFixed(0)
      ),
      this._chart("Charging cost", labels, [{ values: months.map((m) => m.cost), colour: "#f9e2af" }], (v) => this._money(v)),
      this._chart(`Efficiency (${this._effUnit})`, labels, [{ values: months.map((m) => eff(m) || 0), colour: "#cba6f7" }], (v) => (v ? v.toFixed(2) : "")),
    ].join("");

    const top = stats.top_places.length
      ? `<table class=places>${stats.top_places
          .map((p) => `<tr><td>${escp(p.name)}</td><td class=n>${p.count}</td><td class=bar><div style="width:${(p.count / stats.top_places[0].count) * 100}%"></div></td></tr>`)
          .join("")}</table>`
      : `<div class=dim>No named destinations yet.</div>`;

    box.innerHTML = `
      <div class=section><h2>${this._monthName(cur.month, true)} so far</h2><div class=tiles>${tiles}</div></div>
      <div class=section id=mileage></div>
      <div class=grid>${charts}</div>
      <div class=grid2>
        <div class=section><h2>Routes this month <span class=dim>(${routes.length} trips)</span></h2><div id=mapbox></div></div>
        <div class=section><h2>Most visited <span class=dim>(last 12 months)</span></h2>${top}</div>
      </div>`;
    this._loadMileage();
    this._drawRoutes(routes);
  }

  /** Calendar period `offset` periods back: {start, end (seconds), label}. Same as the card. */
  _range(period, offset) {
    const loc = this._hass?.locale?.language;
    const now = new Date();
    let start;
    let end;
    let label;
    if (period === "week") {
      const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
      start = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() - 7 * offset);
      end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7);
      const last = new Date(end.getTime() - 86400000);
      const f = (d, o) => d.toLocaleDateString(loc, o);
      label =
        start.getMonth() === last.getMonth()
          ? `${start.getDate()}–${f(last, { day: "numeric", month: "short", year: "numeric" })}`
          : `${f(start, { day: "numeric", month: "short" })} – ${f(last, { day: "numeric", month: "short", year: "numeric" })}`;
    } else if (period === "month") {
      start = new Date(now.getFullYear(), now.getMonth() - offset, 1);
      end = new Date(start.getFullYear(), start.getMonth() + 1, 1);
      label = start.toLocaleDateString(loc, { month: "long", year: "numeric" });
    } else if (period === "quarter") {
      start = new Date(now.getFullYear(), now.getMonth() - 2 - 3 * offset, 1);
      end = new Date(start.getFullYear(), start.getMonth() + 3, 1);
      const last = new Date(end.getFullYear(), end.getMonth() - 1, 1);
      label = `${start.toLocaleDateString(loc, { month: "short" })} – ${last.toLocaleDateString(loc, { month: "short", year: "numeric" })}`;
    } else {
      start = new Date(now.getFullYear() - offset, 0, 1);
      end = new Date(start.getFullYear() + 1, 0, 1);
      label = String(start.getFullYear());
    }
    return { start: start.getTime() / 1000, end: end.getTime() / 1000, label };
  }

  /** Mileage by trip label for a chosen period (Business first), for mileage claims. */
  async _loadMileage() {
    const box = this.shadowRoot.getElementById("mileage");
    if (!box) return;
    const car = this._car;
    const { start, end, label } = this._range(this._mPeriod, this._mOffset);
    const periods = [["week", "Week"], ["month", "Month"], ["quarter", "3 months"], ["year", "Year"]];
    const head = `<div class=mhead><h2>Mileage by label</h2>
        <div class=seg>${periods.map(([v, l]) => `<button data-mp="${v}" class="${v === this._mPeriod ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class=stepper><button class=step data-ms="1" title="Previous">‹</button><span class=range>${escp(label)}</span><button class=step data-ms="-1" title="Next" ${this._mOffset === 0 ? "disabled" : ""}>›</button></div></div>`;
    box.innerHTML = `${head}<div class=dim>Loading…</div>`;
    this._wireMileage(box);
    let trips;
    try {
      trips = await this._hass.callWS({ type: "mb_logbook/trips", entry_id: car.entry_id, start, end });
    } catch (e) {
      box.innerHTML = `${head}<div class=dim>Couldn't load trips.</div>`;
      this._wireMileage(box);
      return;
    }
    if (this._car !== car) return;
    const groups = new Map();
    for (const t of trips) {
      const k = t.label || "";
      const g = groups.get(k) || { trips: 0, km: 0, auto: 0 };
      g.trips += 1;
      g.km += t.distance_km;
      if (t.label_auto) g.auto += 1;
      groups.set(k, g);
    }
    const order = [...groups.keys()].sort((a, b) => (a === "Business" ? -1 : b === "Business" ? 1 : !a ? 1 : !b ? -1 : a.localeCompare(b)));
    if (!groups.has("Business")) order.unshift("Business");
    const totalKm = trips.reduce((s, t) => s + t.distance_km, 0);
    const tiles = order
      .map((k) => {
        const g = groups.get(k) || { trips: 0, km: 0, auto: 0 };
        const name = k || "Unlabelled";
        return `<div class="tile ${k === "Business" ? "biz" : ""}"><div class=k>${escp(name)}</div><div class=v>${this._d(g.km, 1)} ${this._du}</div>
          <div class=delta>${g.trips} trip${g.trips === 1 ? "" : "s"}${g.auto ? ` · ${g.auto} from place defaults` : ""}</div></div>`;
      })
      .join("");
    box.innerHTML = `${head}<div class=tiles>${tiles}<div class=tile><div class=k>All trips</div><div class=v>${this._d(totalKm, 1)} ${this._du}</div><div class=delta>${trips.length} trips</div></div></div>
      <div class=dim style="margin-top:8px">Label trips (or give places a default label) in the Logbook tab; export them as CSV from there.</div>`;
    this._wireMileage(box);
  }

  _wireMileage(box) {
    box.querySelectorAll("button[data-mp]").forEach((b) =>
      b.addEventListener("click", () => {
        this._mPeriod = b.dataset.mp;
        this._mOffset = 0;
        this._loadMileage();
      })
    );
    box.querySelectorAll("button[data-ms]").forEach((b) =>
      b.addEventListener("click", () => {
        this._mOffset = Math.max(0, this._mOffset + Number(b.dataset.ms));
        this._loadMileage();
      })
    );
  }

  /** Grouped bar chart as inline SVG. series: [{name?, values[], colour}] */
  _chart(title, labels, series, fmt) {
    const W = 600;
    const H = 200;
    const top = 18;
    const bottom = 22;
    const n = labels.length;
    const max = Math.max(1e-9, ...series.flatMap((s) => s.values));
    const slot = W / n;
    const gap = slot * 0.25;
    const bw = (slot - gap) / series.length;
    let bars = "";
    series.forEach((s, si) => {
      s.values.forEach((v, i) => {
        const h = ((H - top - bottom) * v) / max;
        const x = i * slot + gap / 2 + si * bw;
        const y = H - bottom - h;
        const last = i === n - 1;
        bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 1).toFixed(1)}" height="${Math.max(h, v ? 1 : 0).toFixed(1)}" rx="2" fill="${s.colour}" opacity="${last ? 1 : 0.75}"><title>${labels[i]}: ${fmt(v)}</title></rect>`;
        if (last && v) bars += `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" class=val>${fmt(v)}</text>`;
      });
    });
    const xl = labels
      .map((l, i) => `<text x="${(i * slot + slot / 2).toFixed(1)}" y="${H - 6}" class=lbl>${l}</text>`)
      .join("");
    const legend =
      series.length > 1
        ? `<div class=legend>${series.map((s) => `<span><i style="background:${s.colour}"></i>${s.name}</span>`).join("")}</div>`
        : "";
    return `<div class="section chart"><h2>${title}</h2>${legend}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}${xl}</svg></div>`;
  }

  async _drawRoutes(routes) {
    const box = this.shadowRoot.getElementById("mapbox");
    if (!box) return;
    const paths = routes
      .filter((r) => r.points.length >= 2)
      .map((r, i) => ({
        color: ROUTE_COLOURS[i % ROUTE_COLOURS.length],
        gradualOpacity: 0,
        fullDatetime: false,
        points: r.points
          .filter((p) => p[1] != null)
          .map((p) => ({ timestamp: new Date(p[0] * 1000), point: [p[1], p[2]], latitude: p[1], longitude: p[2] })),
      }));
    if (!paths.length) {
      box.innerHTML = `<div class=empty>No routes yet this month.</div>`;
      return;
    }
    if (!customElements.get("ha-map")) {
      try {
        const helpers = await window.loadCardHelpers();
        helpers.createCardElement({ type: "map", entities: ["zone.home"] });
        await Promise.race([customElements.whenDefined("ha-map"), new Promise((r) => setTimeout(r, 5000))]);
      } catch (e) {
        /* fall through */
      }
    }
    if (!customElements.get("ha-map")) {
      box.innerHTML = `<div class=empty>Map unavailable.</div>`;
      return;
    }
    const map = document.createElement("ha-map");
    map.hass = this._hass;
    map.autoFit = false;
    map.fitZones = false;
    map.paths = paths;
    box.innerHTML = "";
    box.appendChild(map);
    this._map = map;
    for (let i = 0; i < 60 && !map._loaded; i++) await new Promise((r) => setTimeout(r, 100));
    await new Promise((r) => setTimeout(r, 150));
    const pts = paths.flatMap((p) => p.points.map((q) => q.point));
    const lats = pts.map((p) => p[0]);
    const lons = pts.map((p) => p[1]);
    map.fitBounds(
      [
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      ],
      { zoom: 16, pad: 0.1 }
    );
  }
}

MBLogbookPanel.css = `
  :host { display: block; height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); overflow-y: auto; }
  .toolbar { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 8px 16px; min-height: 56px; box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-background-color)); color: var(--app-header-text-color, var(--primary-text-color)); border-bottom: 1px solid var(--divider-color); }
  .apptitle { font-size: 20px; flex: 1; }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 10px; overflow: hidden; }
  .seg button { background: none; border: 0; color: var(--secondary-text-color); padding: 6px 14px; font: inherit; cursor: pointer; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .content { padding: 16px; max-width: 1400px; margin: 0 auto; }
  .empty { padding: 32px; text-align: center; color: var(--secondary-text-color); }
  .section { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); padding: 14px 16px; margin-bottom: 16px;
    border: 1px solid var(--ha-card-border-color, var(--divider-color)); }
  h2 { font-size: 1.05em; font-weight: 600; margin: 0 0 10px; }
  .dim { color: var(--secondary-text-color); font-weight: normal; font-size: .9em; }
  .mhead { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
  .mhead h2 { margin: 0; flex: 1; }
  .stepper { display: inline-flex; align-items: center; gap: 4px; }
  .stepper .range { min-width: 9em; text-align: center; font-weight: 500; }
  button.step { width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font-size: 1.2em; line-height: 1; cursor: pointer; }
  button.step:disabled { opacity: .35; cursor: default; }
  .tile.biz { outline: 2px solid var(--primary-color); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
  .tile { background: var(--secondary-background-color); border-radius: 10px; padding: 10px 12px; }
  .k { font-size: .8em; color: var(--secondary-text-color); }
  .v { font-size: 1.35em; font-weight: 600; margin-top: 2px; }
  .delta { font-size: .78em; color: var(--secondary-text-color); margin-top: 2px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 0 16px; }
  .grid2 { display: grid; grid-template-columns: 2fr 1fr; gap: 0 16px; }
  @media (max-width: 900px) { .grid, .grid2 { grid-template-columns: 1fr; } }
  .chart svg { width: 100%; height: 200px; display: block; }
  .chart .lbl { fill: var(--secondary-text-color); font-size: 12px; text-anchor: middle; }
  .chart .val { fill: var(--primary-text-color); font-size: 12px; text-anchor: middle; font-weight: 600; }
  .legend { display: flex; gap: 14px; font-size: .8em; color: var(--secondary-text-color); margin: -4px 0 6px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
  #mapbox { height: 380px; border-radius: 10px; overflow: hidden; }
  #mapbox ha-map { display: block; height: 100%; }
  table.places { width: 100%; border-collapse: collapse; }
  table.places td { padding: 6px 4px; border-bottom: 1px solid var(--divider-color); }
  table.places .n { text-align: right; width: 3em; color: var(--secondary-text-color); }
  table.places .bar { width: 35%; }
  table.places .bar div { height: 8px; border-radius: 4px; background: var(--primary-color); opacity: .7; }
  mb-logbook-card { display: block; }
`;

if (!customElements.get("mb-logbook-panel")) customElements.define("mb-logbook-panel", MBLogbookPanel);
