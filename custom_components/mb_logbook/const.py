"""Constants for MB Logbook."""

from __future__ import annotations

DOMAIN = "mb_logbook"
VERSION = "0.3.4"

DB_FILENAME = "mb_logbook.db"
STORAGE_VERSION = 1

# Config entry keys
CONF_CAR_NAME = "car_name"
CONF_ODOMETER = "odometer_entity"
CONF_SOC = "soc_entity"
CONF_IGNITION = "ignition_entity"
CONF_TRACKER = "tracker_entity"
CONF_RANGE = "range_entity"
CONF_TRIP_DISTANCE = "trip_distance_entity"
CONF_CHARGING = "charging_entity"
CONF_CHARGE_POWER = "charge_power_entity"
CONF_CAPACITY = "battery_capacity_kwh"
CONF_CHARGER_ENERGY = "charger_energy_entity"
CONF_PRICE = "price_entity"
CONF_HOME_ZONE = "home_zone"
CONF_STOP_MINUTES = "stop_minutes"
CONF_MIN_DISTANCE = "min_distance_km"
CONF_DISTANCE_UNIT = "distance_unit"

DEFAULT_STOP_MINUTES = 3
DEFAULT_MIN_DISTANCE_KM = 0.3
DEFAULT_HOME_ZONE = "zone.home"

UNIT_KM = "km"
UNIT_MI = "mi"
KM_PER_MI = 1.609344

# mbapi2020 unique_id suffixes ("<vin>_<key>") used to pre-fill the setup form
MB_KEYS = {
    CONF_ODOMETER: ("sensor", "odometer"),
    CONF_SOC: ("sensor", "soc"),
    CONF_IGNITION: ("sensor", "ignitionstate"),
    CONF_TRACKER: ("device_tracker", "tracker"),
    CONF_RANGE: ("sensor", "rangeelectrickm"),
    CONF_TRIP_DISTANCE: ("sensor", "distancestart"),
    CONF_CHARGING: ("binary_sensor", "chargingactive"),
    CONF_CHARGE_POWER: ("sensor", "chargingpowerkw"),
}

# Ignition codes reported by mbapi2020 that mean "off"
IGNITION_OFF = {"0"}
UNKNOWN_STATES = {"unknown", "unavailable", "", "None", None}

# A charging session that stops and restarts within this window is one session
CHARGE_GAP_MINUTES = 10
# Ignore single charger-meter jumps above this (meter resets / glitches)
MAX_METER_STEP_KWH = 25.0
# Parks shorter than this are left out of the idle-drain figure
IDLE_MIN_HOURS = 6.0

SIGNAL_UPDATED = f"{DOMAIN}_updated_{{}}"


def entry_conf(entry) -> dict:
    """Effective settings: the options form replaces the setup form once saved."""
    conf = dict(entry.options) if entry.options else dict(entry.data)
    conf[CONF_CAR_NAME] = entry.data.get(CONF_CAR_NAME) or entry.title
    return conf
