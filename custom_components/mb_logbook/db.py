"""SQLite store for trips and charging sessions.

One file for every car (``/config/mb_logbook.db``). All calls run in the
executor; a lock serialises them because the connection is shared.
"""

from __future__ import annotations

import json
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
"""

TRIP_COLS = (
    "car", "start_ts", "end_ts", "odo_start_km", "odo_end_km", "distance_km",
    "soc_start", "soc_end", "range_start_km", "range_end_km",
    "start_lat", "start_lon", "end_lat", "end_lon", "start_place", "end_place",
    "park_hours", "park_soc_drop", "park_charged", "points",
)
CHARGE_COLS = (
    "car", "start_ts", "end_ts", "soc_start", "soc_end", "kwh_car", "kwh_meter",
    "cost", "max_power_kw", "place", "at_home",
)


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

    def add_trip(self, trip: dict[str, Any]) -> int:
        row = dict(trip)
        row["points"] = json.dumps(row.get("points") or [])
        row["park_charged"] = 1 if row.get("park_charged") else 0
        sql = (
            f"INSERT INTO trips ({', '.join(TRIP_COLS)}) "
            f"VALUES ({', '.join('?' for _ in TRIP_COLS)})"
        )
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(sql, tuple(row.get(c) for c in TRIP_COLS))
            self._conn.commit()
            return int(cur.lastrowid)

    def add_charge(self, charge: dict[str, Any]) -> int:
        row = dict(charge)
        row["at_home"] = 1 if row.get("at_home") else 0
        sql = (
            f"INSERT INTO charges ({', '.join(CHARGE_COLS)}) "
            f"VALUES ({', '.join('?' for _ in CHARGE_COLS)})"
        )
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(sql, tuple(row.get(c) for c in CHARGE_COLS))
            self._conn.commit()
            return int(cur.lastrowid)

    def trips(
        self, car: str, start_ts: float, end_ts: float, limit: int = 2000
    ) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM trips WHERE car = ? AND start_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts DESC LIMIT ?",
            (car, start_ts, end_ts, limit),
        )
        out = []
        for r in rows:
            d = dict(r)
            pts = d.pop("points") or "[]"
            d["point_count"] = len(json.loads(pts))
            d["park_charged"] = bool(d["park_charged"])
            out.append(d)
        return out

    def trip_points(self, trip_id: int) -> tuple[str, list] | None:
        rows = self._exec("SELECT car, points FROM trips WHERE id = ?", (trip_id,))
        if not rows:
            return None
        return rows[0]["car"], json.loads(rows[0]["points"] or "[]")

    def delete_trip(self, trip_id: int) -> None:
        self._exec("DELETE FROM trips WHERE id = ?", (trip_id,))

    def charges(
        self, car: str, start_ts: float, end_ts: float, limit: int = 2000
    ) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM charges WHERE car = ? AND start_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts DESC LIMIT ?",
            (car, start_ts, end_ts, limit),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["at_home"] = bool(d["at_home"])
            out.append(d)
        return out

    def last_trip(self, car: str) -> dict[str, Any] | None:
        rows = self._exec(
            "SELECT * FROM trips WHERE car = ? ORDER BY start_ts DESC LIMIT 1", (car,)
        )
        if not rows:
            return None
        d = dict(rows[0])
        d.pop("points", None)
        return d

    def last_charge(self, car: str) -> dict[str, Any] | None:
        rows = self._exec(
            "SELECT * FROM charges WHERE car = ? ORDER BY start_ts DESC LIMIT 1", (car,)
        )
        return dict(rows[0]) if rows else None

    def totals(self, car: str, start_ts: float, end_ts: float) -> dict[str, Any]:
        t = self._exec(
            "SELECT COUNT(*) AS n, COALESCE(SUM(distance_km), 0) AS km, "
            "COALESCE(SUM(CASE WHEN soc_start > soc_end THEN soc_start - soc_end END), 0) AS soc_used, "
            "COALESCE(SUM(CASE WHEN soc_start > soc_end THEN distance_km END), 0) AS km_with_soc "
            "FROM trips WHERE car = ? AND start_ts >= ? AND start_ts < ?",
            (car, start_ts, end_ts),
        )[0]
        c = self._exec(
            "SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(kwh_meter, kwh_car)), 0) AS kwh, "
            "COALESCE(SUM(cost), 0) AS cost FROM charges "
            "WHERE car = ? AND start_ts >= ? AND start_ts < ?",
            (car, start_ts, end_ts),
        )[0]
        return {
            "trips": t["n"],
            "distance_km": t["km"],
            "soc_used": t["soc_used"],
            "distance_km_with_soc": t["km_with_soc"],
            "charges": c["n"],
            "charged_kwh": c["kwh"],
            "charge_cost": c["cost"],
        }

    def idle_parks(self, car: str, since_ts: float, min_hours: float) -> list[tuple]:
        """(hours, soc_drop) for uncharged parks that ended after since_ts."""
        rows = self._exec(
            "SELECT park_hours, park_soc_drop FROM trips WHERE car = ? AND start_ts >= ? "
            "AND park_charged = 0 AND park_hours >= ? AND park_soc_drop IS NOT NULL",
            (car, since_ts, min_hours),
        )
        return [(r["park_hours"], r["park_soc_drop"]) for r in rows]
