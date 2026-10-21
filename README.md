# MB Logbook

A Home Assistant integration that keeps a permanent logbook for Mercedes-Benz electric cars:

- **Trips**: start and end time, distance, battery used, kWh and efficiency, start and end place, and the route.
- **Parked drain**: how much battery each car lost while parked between trips (parks that included a charge are marked, not counted).
- **Charging sessions**: battery % added, kWh, and, with a home charger energy meter and an electricity price sensor, the metered energy and cost of each home charge credited to the car that was charging.
- **Named places**: a start or end outside your Home Assistant zones shows as unnamed; name it from the card (with a radius) and every past and future trip or charge there takes that name. Places are shared by all cars and managed in the card's Places tab.
- **Several cars**: add each car separately; the card switches between them.

It reads the entities of the [mbapi2020](https://github.com/ReneNulschDE/mbapi2020) integration (any car that exposes an odometer, battery %, ignition state and GPS tracker works). It does not talk to Mercedes itself.

**Privacy:** everything stays in Home Assistant. Places are named from your own Home Assistant zones and your own named places; there is no address lookup service. The route map is Home Assistant's built-in map component, which loads the same map tiles as the standard map card.

## Status

Early development (0.x). Expect changes.

## Install

Copy `custom_components/mb_logbook` into your `config/custom_components` folder and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → MB Logbook** and pick your car.

## Setup

1. Pick the car from the Mercedes integration; its sensors are filled in automatically.
2. Enter the usable battery capacity in kWh (used to turn battery % into kWh).
3. Optional: a home charger energy meter (a total kWh sensor) and an electricity price sensor (per kWh). While a car is charging in the home zone, the meter's increase and its cost at the live price are credited to that car's session.

## Existing history

When a car is added, MB Logbook rebuilds its earlier trips and charges from Home Assistant's recorded history (as far back as your recorder keeps it), using the same rules as live logging. Run the **MB Logbook: Import history** action to do it again; it only replaces previously imported rows, never live ones.

## Card

Registered automatically as a dashboard resource (and updated on each version); nothing to add by hand.

```yaml
type: custom:mb-logbook-card
days: 30          # 7, 30, 90 or 365
# car: EQS        # optional: lock the card to one car (by name)
```

Click a trip to show its route. Click **unnamed · name it** next to a start or end to name that place (admins only).

## Sensors (per car)

Trips, distance, energy and efficiency this month; last trip distance, energy and efficiency; idle drain (% per day: battery lost over time parked, across uncharged parks of 6 h or more in the last 30 days); last charge energy and cost; energy charged and charging cost this month; trip in progress; charging session.

## How trips are detected

A trip starts when the ignition leaves "off" and ends when it has been off for a few minutes (3 by default). Distance comes from the car's 0.1-resolution "distance since start" counter, checked against the odometer (which may only report whole miles). Ignition events that don't move the car (for example pre-conditioning) are ignored. The data is stored in `config/mb_logbook.db` (SQLite) and is included in Home Assistant backups.

## Limits

- Mercedes reports battery % in whole numbers, so kWh for short trips is approximate (1% of a large battery is about 1 kWh).
- The car sends its position about every two minutes while driving, so routes are drawn point to point.

## License

MIT
