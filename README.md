# MB Logbook

A Home Assistant integration that keeps a permanent logbook for Mercedes-Benz electric cars:

- **Trips**: start and end time, distance, battery used, kWh and efficiency, start and end place, and the route.
- **Parked drain**: how much battery each car lost while parked between trips (parks that included a charge are marked, not counted).
- **Charging sessions**: battery % added, kWh, and, with a home charger energy meter and an electricity price sensor, the metered energy and cost of each home charge credited to the car that was charging.
- **Named places**: a start or end outside your Home Assistant zones shows as unnamed; name it from the card (with a radius) and every past and future trip or charge there takes that name. The stop you named it from stays linked even if you move the circle (the car's GPS can be tens of metres out), and later stops near it get the name too; naming another stop with an existing name links it to that place. Places are shared by all cars and managed in the card's Places tab.
- **Charging rates** (Charging tab): give any charging place (an HA zone or a named place) a price per kWh, or mark it free, with a start date. Charges without a meter are costed at the rate that applied when they started, so adding a new rate never changes older charges. Metered home charges always use the meter and the live price.
- **Trip labels and mileage export**: label each trip (Business, Personal, Commute, or your own), filter by label (the totals follow), and export the trips shown as CSV for mileage claims. Labels are kept separately, so re-importing history never loses them. Give a place a default label (Places tab) and trips ending there, or else starting there, take it automatically, shown as "auto" until you pick one yourself.
- **Several cars**: add each car separately; the card switches between them.

It reads the entities of the [mbapi2020](https://github.com/ReneNulschDE/mbapi2020) integration (any car that exposes an odometer, battery %, ignition state and GPS tracker works). It does not talk to Mercedes itself.

**Privacy:** everything stays in Home Assistant. Places are named from your own Home Assistant zones and your own named places; there is no automatic address lookup. The one exception is optional: when naming a place you can press **Suggest names**, which looks up named shops and venues near the pin in OpenStreetMap data (Photon by Komoot, falling back to the Overpass API). That sends only that one point, only when you press it. The route map is Home Assistant's built-in map component, which loads the same map tiles as the standard map card.

## Status

Early development (0.x). Expect changes.

## Requirements

**Needed**
- A car integration that gives you, as Home Assistant entities: an **odometer**, a **battery %** sensor and a **GPS device tracker**. The Mercedes integration (mbapi2020) fills these in automatically; for other cars, pick them yourself.
- The **usable battery capacity** in kWh (to turn battery % into kWh).

**Optional — each adds something**
- **Ignition state**: the most precise trip start and end. Without it, a trip starts when the odometer or trip counter moves (or the car's position jumps) and ends after a few minutes without movement.
- **Trip distance counter** (distance since start): 0.1-unit trip distances when the odometer only reports whole miles/km.
- **Charging active** sensor: charging sessions. **Charging power**: peak power, and the energy and cost of home charges when there's no charger meter.
- **Electricity price** sensor (per kWh): costs home charges at the live price — from the home charger meter if you have one, otherwise from the car's charging power.
- **Home charger energy meter** (a total kWh sensor, e.g. from a Zappi, Sigen, Wallbox or smart plug): the most accurate home energy and cost.
- **Charging rates** (set in the card): a fixed price per kWh, or free, for any other place you charge.
## Install

Copy `custom_components/mb_logbook` into your `config/custom_components` folder and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → MB Logbook** and pick your car.

## Setup

1. Pick the car from the Mercedes integration; its sensors are filled in automatically.
2. Enter the usable battery capacity in kWh (used to turn battery % into kWh).
3. Optional: a home charger energy meter (a total kWh sensor) and an electricity price sensor (per kWh). While a car is charging in the home zone, the meter's increase and its cost at the live price are credited to that car's session.

## Existing history

When a car is added, MB Logbook rebuilds its earlier trips and charges from Home Assistant's recorded history (as far back as your recorder keeps it), using the same rules as live logging. Run the **MB Logbook: Import history** action to do it again; it only replaces previously imported rows, never live ones.

## Sidebar page

MB Logbook adds an **MB Logbook** page to the sidebar:

- **Overview**: this month's trips, distance, energy, efficiency, charging and idle drain against last month; 12-month charts of distance, energy used vs charged, charging cost and efficiency; a map of this month's routes; your most-visited places.
- **Logbook**: the full logbook (trips, charging, places) for the selected car.

## Card

Registered automatically as a dashboard resource (and updated on each version); nothing to add by hand.

```yaml
type: custom:mb-logbook-card
period: month     # week, month, quarter or year (use the arrows to step back)
# car: EQS        # optional: lock the card to one car (by name)
```

Click a trip to show its route. Click **unnamed · name it** next to a start or end to name that place (admins only). On the map, drag the pin to move the place and drag the circle's edge to resize it; the Places tab lets you adjust existing places the same way.

## Sensors (per car)

Trips, distance, energy and efficiency this month; last trip distance, energy and efficiency; idle drain (% per day: battery lost over time parked, across uncharged parks of 6 h or more in the last 30 days); last charge energy and cost; energy charged and charging cost this month; trip in progress; charging session.

## How trips are detected

A trip starts when the ignition leaves "off" and ends when it has been off for a few minutes (3 by default). Without an ignition sensor, a trip starts when the car moves (odometer or trip counter going up, or a position jump of 200 m or more) and ends after the same few minutes without movement. Distance comes from the car's 0.1-resolution "distance since start" counter, checked against the odometer (which may only report whole miles). Ignition events that don't move the car (for example pre-conditioning) are ignored. The data is stored in `config/mb_logbook.db` (SQLite) and is included in Home Assistant backups.

## Limits

- Mercedes reports battery % in whole numbers, so kWh for short trips is approximate (1% of a large battery is about 1 kWh).
- The car sends its position about every two minutes while driving, so routes are drawn point to point.

## License

MIT
