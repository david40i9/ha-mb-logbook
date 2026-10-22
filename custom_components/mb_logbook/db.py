"""SQLite store for trips, charging sessions and named places.

One file for every car (``/config/mb_logbook.db``). All calls run in the
executor; a lock serialises them because the connection is shared.

Places: a trip or charge end that wasn't in a Home Assistant zone is stored
without a name. User-named places (a point and a radius) fill those gaps when
the rows are read, so naming or renaming a place updates the whole history.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    odo_start_km REAL,
    odo_end_km REAL,
    distance_km REAL NOT NULL,
    soc_start REAL,
    soc_end REAL,
    range_start_km REAL,
    range_end_km REAL,
    start_lat REAL, start_lon REAL,
    end_lat REAL, end_lon REAL,
    start_place TEXT,
    end_place TEXT,
    park_hours REAL,
    park_drain_hours REAL,
    park_soc_drop REAL,
    park_charged INTEGER,
    points TEXT
);
CREATE INDEX IF NOT EXISTS trips_car_start ON trips (car, start_ts);
CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    soc_start REAL,
    soc_end REAL,
    kwh_car REAL,
    kwh_meter REAL,
    cost REAL,
    max_power_kw REAL,
    place TEXT,
    at_home INTEGER
);
CREATE INDEX IF NOT EXISTS charges_car_start ON charges (car, start_ts);
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    radius_m REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS place_anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS place_anchors_place ON place_anchors (place_id);
CREATE TABLE IF NOT EXISTS charge_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location TEXT NOT NULL,
    valid_from REAL NOT NULL,
    rate REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS charge_rates_loc ON charge_rates (location, valid_from);
"""

# Columns added after 0.1.0: (table, column, type)
MIGRATIONS = (
    ("trips", "park_drain_hours", "REAL"),
    ("charges", "lat", "REAL"),
    ("charges", "lon", "REAL"),
    ("charges", "cost_basis", "TEXT"),
)

TRIP_COLS = (
    "car", "start_ts", "end_ts", "odo_start_km", "odo_end_km", "distance_km",
    "soc_start", "soc_end", "range_start_km", "range_end_km",
    "start_lat", "start_lon", "end_lat", "end_lon", "start_place", "end_place",
    "park_hours", "park_drain_hours", "park_soc_drop", "park_charged", "points",
)
CHARGE_COLS = (
    "car", "start_ts", "end_ts", "soc_start", "soc_end", "kwh_car", "kwh_meter",
    "cost", "max_power_kw", "place", "at_home", "lat", "lon", "cost_basis",
)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def rate_at(rates: list[dict[str, Any]], ts: float) -> dict[str, Any] | None:
    """The rate entry in force at ``ts`` (rates sorted by valid_from), or None."""
    current = None
    for r in rates:
        if r["valid_from"] <= ts:
            current = r
        else:
            break
    return current


# A stop this close to a point a place was named from gets that name, even if
# it's outside the place's circle (the car's GPS can be tens of metres off).
ANCHOR_RADIUS_M = 75.0


def match_place(places: list[dict[str, Any]], lat: float | None, lon: float | None):
    """The named place for a point, or None.

    A point matches a place if it is inside the place's circle, or within
    ANCHOR_RADIUS_M of a stop the place was named from (its anchors), so moving
    or shrinking the circle never loses the stops it was created for. The
    closest match wins.
    """
    if lat is None or lon is None:
        return None
    best = None
    best_d = None
    for p in places:
        d = distance_m(lat, lon, p["lat"], p["lon"])
        hit = d <= p["radius_m"]
        for a in p.get("anchors") or ():
            da = distance_m(lat, lon, a[0], a[1])
            if da <= ANCHOR_RADIUS_M:
                hit = True
                d = min(d, da)
        if hit and (best_d is None or d < best_d):
            best, best_d = p, d
    return best


class LogbookDB:
    """Thin synchronous wrapper; call through hass.async_add_executor_job."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        with self._lock:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
            for table, col, kind in MIGRATIONS:
                cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    def _insert(self, table: str, cols: tuple, row: dict[str, Any]) -> int:
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(sql, tuple(row.get(c) for c in cols))
            self._conn.commit()
            return int(cur.lastrowid)

    # ----- places ------------------------------------------------------------------------

    def places(self) -> list[dict[str, Any]]:
        places = [dict(r) for r in self._exec("SELECT * FROM places ORDER BY name COLLATE NOCASE")]
        anchors: dict[int, list[list[float]]] = {}
        for r in self._exec("SELECT place_id, lat, lon FROM place_anchors"):
            anchors.setdefault(r["place_id"], []).append([r["lat"], r["lon"]])
        for p in places:
            p["anchors"] = anchors.get(p["id"], [])
        return places

    def add_anchor(self, place_id: int, lat: float, lon: float) -> None:
        """Link a stop to a place (skipped if an anchor is already within a few metres)."""
        for r in self._exec("SELECT lat, lon FROM place_anchors WHERE place_id = ?", (place_id,)):
            if distance_m(lat, lon, r["lat"], r["lon"]) < 5:
                return
        self._exec(
            "INSERT INTO place_anchors (place_id, lat, lon) VALUES (?, ?, ?)", (place_id, lat, lon)
        )

    def save_place(self, place: dict[str, Any]) -> int:
        if place.get("id"):
            old = self._exec("SELECT name FROM places WHERE id = ?", (place["id"],))
            self._exec(
                "UPDATE places SET name = ?, lat = ?, lon = ?, radius_m = ? WHERE id = ?",
                (place["name"], place["lat"], place["lon"], place["radius_m"], place["id"]),
            )
            if old and old[0]["name"] != place["name"]:
                # Rates follow the place when it is renamed.
                self._exec(
                    "UPDATE charge_rates SET location = ? WHERE location = ?",
                    (place["name"], old[0]["name"]),
                )
            return int(place["id"])
        return self._insert("places", ("name", "lat", "lon", "radius_m"), place)

    # ----- charging rates ----------------------------------------------------------------
    # A location (an HA zone name or a named place) can have a history of rates:
    # each entry applies from ``valid_from`` until the next one. A charge with no
    # metered cost is costed at the rate in force when it started, so adding a
    # new rate never changes earlier charges.

    def rates(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._exec(
                "SELECT * FROM charge_rates ORDER BY location COLLATE NOCASE, valid_from"
            )
        ]

    def _rates_by_location(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in self.rates():
            out.setdefault(r["location"], []).append(r)
        return out

    def save_rate(self, rate: dict[str, Any]) -> int:
        if rate.get("id"):
            self._exec(
                "UPDATE charge_rates SET location = ?, valid_from = ?, rate = ? WHERE id = ?",
                (rate["location"], rate["valid_from"], rate["rate"], rate["id"]),
            )
            return int(rate["id"])
        return self._insert("charge_rates", ("location", "valid_from", "rate"), rate)

    def delete_rate(self, rate_id: int) -> None:
        self._exec("DELETE FROM charge_rates WHERE id = ?", (rate_id,))

    def charge_locations(self) -> list[dict[str, Any]]:
        """Every place a charge has happened (named), with counts and metered share."""
        places = self.places()
        out: dict[str, dict[str, Any]] = {}
        for r in self._exec("SELECT place, lat, lon, kwh_meter, start_ts FROM charges"):
            name = r["place"]
            if name is None and (p := match_place(places, r["lat"], r["lon"])):
                name = p["name"]
            if name is None:
                continue
            loc = out.setdefault(name, {"location": name, "charges": 0, "metered": 0, "last_ts": 0})
            loc["charges"] += 1
            loc["metered"] += 1 if r["kwh_meter"] else 0
            loc["last_ts"] = max(loc["last_ts"], r["start_ts"])
        return sorted(out.values(), key=lambda x: -x["charges"])

    def delete_place(self, place_id: int) -> None:
        self._exec("DELETE FROM place_anchors WHERE place_id = ?", (place_id,))
        self._exec("DELETE FROM places WHERE id = ?", (place_id,))

    def place_usage(self) -> dict[int, int]:
        """How many trip ends and charges each place currently names."""
        places = self.places()
        counts = {p["id"]: 0 for p in places}
        rows = self._exec(
            "SELECT start_place, start_lat, start_lon, end_place, end_lat, end_lon FROM trips "
            "WHERE start_place IS NULL OR end_place IS NULL"
        )
        for r in rows:
            for name, lat, lon in (
                (r["start_place"], r["start_lat"], r["start_lon"]),
                (r["end_place"], r["end_lat"], r["end_lon"]),
            ):
                if name is None and (p := match_place(places, lat, lon)):
                    counts[p["id"]] += 1
        for r in self._exec("SELECT lat, lon FROM charges WHERE place IS NULL"):
            if p := match_place(places, r["lat"], r["lon"]):
                counts[p["id"]] += 1
        return counts

    def _name_trip(self, d: dict[str, Any], places: list[dict[str, Any]]) -> dict[str, Any]:
        for end in ("start", "end"):
            d[f"{end}_place_id"] = None
            if d.get(f"{end}_place") is None and (
                p := match_place(places, d.get(f"{end}_lat"), d.get(f"{end}_lon"))
            ):
                d[f"{end}_place"] = p["name"]
                d[f"{end}_place_id"] = p["id"]
        return d

    # ----- trips -------------------------------------------------------------------------

    def add_trip(self, trip: dict[str, Any]) -> int:
        row = dict(trip)
        row["points"] = json.dumps(row.get("points") or [])
        row["park_charged"] = 1 if row.get("park_charged") else 0
        return self._insert("trips", TRIP_COLS, row)

    def trips(
        self, car: str, start_ts: float, end_ts: float, limit: int = 2000
    ) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM trips WHERE car = ? AND start_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts DESC LIMIT ?",
            (car, start_ts, end_ts, limit),
        )
        places = self.places()
        out = []
        for r in rows:
            d = dict(r)
            pts = d.pop("points") or "[]"
            d["point_count"] = len(json.loads(pts))
            d["park_charged"] = bool(d["park_charged"])
            out.append(self._name_trip(d, places))
        return out

    def trip_points(self, trip_id: int) -> tuple[str, list] | None:
        rows = self._exec("SELECT car, points FROM trips WHERE id = ?", (trip_id,))
        if not rows:
            return None
        return rows[0]["car"], json.loads(rows[0]["points"] or "[]")

    def routes(self, car: str, start_ts: float, end_ts: float, limit: int = 300) -> list[dict[str, Any]]:
        """Route points of every trip in a period (for an all-trips map)."""
        rows = self._exec(
            "SELECT id, start_ts, points FROM trips WHERE car = ? AND start_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts DESC LIMIT ?",
            (car, start_ts, end_ts, limit),
        )
        return [
            {"id": r["id"], "start_ts": r["start_ts"], "points": json.loads(r["points"] or "[]")}
            for r in rows
        ]

    def delete_trip(self, trip_id: int) -> None:
        self._exec("DELETE FROM trips WHERE id = ?", (trip_id,))

    def last_trip(self, car: str) -> dict[str, Any] | None:
        rows = self._exec(
            "SELECT * FROM trips WHERE car = ? ORDER BY start_ts DESC LIMIT 1", (car,)
        )
        if not rows:
            return None
        d = dict(rows[0])
        d.pop("points", None)
        return self._name_trip(d, self.places())

    # ----- charges -----------------------------------------------------------------------

    def add_charge(self, charge: dict[str, Any]) -> int:
        row = dict(charge)
        row["at_home"] = 1 if row.get("at_home") else 0
        return self._insert("charges", CHARGE_COLS, row)

    def charges(
        self, car: str, start_ts: float, end_ts: float, limit: int = 2000
    ) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM charges WHERE car = ? AND start_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts DESC LIMIT ?",
            (car, start_ts, end_ts, limit),
        )
        places = self.places()
        rates = self._rates_by_location()
        out = []
        for r in rows:
            d = dict(r)
            d["at_home"] = bool(d["at_home"])
            d["place_id"] = None
            if d.get("place") is None and (p := match_place(places, d.get("lat"), d.get("lon"))):
                d["place"] = p["name"]
                d["place_id"] = p["id"]
            self._apply_rate(d, rates)
            out.append(d)
        return out

    @staticmethod
    def _apply_rate(d: dict[str, Any], rates: dict[str, list[dict[str, Any]]]) -> None:
        """Metered cost wins; otherwise cost the charge at its place's rate then."""
        d["rate"] = None
        if d.get("cost") is not None:
            # "meter" (home charger meter) or "power" (car's charging power x live price)
            d["cost_source"] = d.get("cost_basis") or "meter"
            return
        d["cost_source"] = None
        kwh = d.get("kwh_meter") or d.get("kwh_car")
        entry = rate_at(rates.get(d.get("place") or "", []), d["start_ts"])
        if entry is None or kwh is None:
            return
        d["rate"] = entry["rate"]
        d["cost"] = round(kwh * entry["rate"], 4)
        d["cost_source"] = "free" if entry["rate"] == 0 else "rate"

    def last_charge(self, car: str) -> dict[str, Any] | None:
        rows = self.charges(car, 0, 1e12, limit=1)
        return rows[0] if rows else None

    def replace_before(
        self, car: str, cutoff_ts: float, trips: list[dict[str, Any]], charges: list[dict[str, Any]]
    ) -> None:
        """Swap in imported history: drop this car's rows before the cut-off, insert new ones."""
        trip_rows = []
        for t in trips:
            row = dict(t)
            row["points"] = json.dumps(row.get("points") or [])
            row["park_charged"] = 1 if row.get("park_charged") else 0
            trip_rows.append(tuple(row.get(c) for c in TRIP_COLS))
        charge_rows = []
        for c in charges:
            row = dict(c)
            row["at_home"] = 1 if row.get("at_home") else 0
            charge_rows.append(tuple(row.get(col) for col in CHARGE_COLS))
        with self._lock:
            assert self._conn is not None
            with self._conn:  # one transaction
                self._conn.execute("DELETE FROM trips WHERE car = ? AND start_ts < ?", (car, cutoff_ts))
                self._conn.execute("DELETE FROM charges WHERE car = ? AND start_ts < ?", (car, cutoff_ts))
                self._conn.executemany(
                    f"INSERT INTO trips ({', '.join(TRIP_COLS)}) VALUES ({', '.join('?' for _ in TRIP_COLS)})",
                    trip_rows,
                )
                self._conn.executemany(
                    f"INSERT INTO charges ({', '.join(CHARGE_COLS)}) VALUES ({', '.join('?' for _ in CHARGE_COLS)})",
                    charge_rows,
                )

    def first_ts(self, car: str) -> float | None:
        rows = self._exec(
            "SELECT MIN(start_ts) AS t FROM (SELECT start_ts FROM trips WHERE car = ? "
            "UNION ALL SELECT start_ts FROM charges WHERE car = ?)",
            (car, car),
        )
        return rows[0]["t"] if rows else None

    # ----- totals ------------------------------------------------------------------------

    def totals(self, car: str, start_ts: float, end_ts: float) -> dict[str, Any]:
        t = self._exec(
            "SELECT COUNT(*) AS n, COALESCE(SUM(distance_km), 0) AS km, "
            "COALESCE(SUM(CASE WHEN soc_start > soc_end THEN soc_start - soc_end END), 0) AS soc_used, "
            "COALESCE(SUM(CASE WHEN soc_start > soc_end THEN distance_km END), 0) AS km_with_soc "
            "FROM trips WHERE car = ? AND start_ts >= ? AND start_ts < ?",
            (car, start_ts, end_ts),
        )[0]
        # Charges go through charges() so place names and rate costs are applied.
        ch = self.charges(car, start_ts, end_ts, limit=100000)
        return {
            "trips": t["n"],
            "distance_km": t["km"],
            "soc_used": t["soc_used"],
            "distance_km_with_soc": t["km_with_soc"],
            "charges": len(ch),
            "charged_kwh": sum((c.get("kwh_meter") or c.get("kwh_car") or 0) for c in ch),
            "charge_cost": sum((c.get("cost") or 0) for c in ch),
        }

    def idle_parks(self, car: str, since_ts: float, min_hours: float) -> list[tuple]:
        """(hours, soc_drop) for uncharged parks that ended after since_ts."""
        # Drain is measured after any charge (park_drain_hours); rows from before
        # that column existed only count when the park had no charge.
        rows = self._exec(
            "SELECT COALESCE(park_drain_hours, park_hours) AS h, park_soc_drop FROM trips "
            "WHERE car = ? AND start_ts >= ? AND park_soc_drop IS NOT NULL "
            "AND (park_drain_hours IS NOT NULL OR park_charged = 0) "
            "AND COALESCE(park_drain_hours, park_hours) >= ?",
            (car, since_ts, min_hours),
        )
        return [(r["h"], r["park_soc_drop"]) for r in rows]
