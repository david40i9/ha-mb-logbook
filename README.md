<p align="center">
  <img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/custom_components/mb_logbook/brand/icon@2x.png" width="112" alt="">
</p>

<h1 align="center">Mercedes EV Logbook</h1>

<p align="center">
  A permanent trip, mileage, charging and battery logbook for Mercedes-Benz electric cars in Home Assistant.
</p>

<p align="center">
  <a href="https://github.com/david40i9/ha-mercedes-ev-logbook/releases"><img src="https://img.shields.io/github/v/release/david40i9/ha-mercedes-ev-logbook?style=flat-square" alt="Release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-custom-41BDF5?style=flat-square" alt="HACS"></a>
  <a href="https://github.com/david40i9/ha-mercedes-ev-logbook/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/david40i9/ha-mercedes-ev-logbook/validate.yml?style=flat-square&label=validation" alt="Validation"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.3%2B-18BCF2?style=flat-square" alt="Home Assistant 2026.3+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/david40i9/ha-mercedes-ev-logbook?style=flat-square" alt="MIT licence"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/docs/images/overview.png" alt="The Mercedes EV Logbook overview page: monthly figures, mileage by label, and charts">
</p>

Every drive, park and charge of your EQA, EQB, EQE, EQS, EQV, electric CLA or other Mercedes EV, kept in Home Assistant for as long as you like: where you went, how far, how much battery and energy it took, what the car lost while parked, and what each charge cost. Label trips Business, Commute or Personal and export them for mileage claims.

It builds on the [MercedesME 2020 (mbapi2020)](https://github.com/ReneNulschDE/mbapi2020) integration and never talks to Mercedes itself. Everything stays on your Home Assistant.

> *Screenshots use made-up demo data.*

## Highlights

| | |
|---|---|
| 🚗 **Trips** | Start and end, distance, battery used, kWh, efficiency, outside temperature and the route on a map |
| 🅿️ **Parked drain** | What the car lost between trips, and an idle-drain sensor in % per day |
| ⚡ **Charging** | Every session with kWh, peak power and cost, from your own rates per place (with an optional off-peak window) or a live price sensor; rate history keeps old costs right |
| 🏷️ **Mileage claims** | Business / Commute / Personal labels, a default label per place, mileage by label and CSV export |
| 📍 **Your own places** | Name any stop with a draggable, resizable circle; the name reaches back through all your history |
| 🔋 **Battery health** | The range the car predicts at 100%, month by month |
| 🌡️ **Efficiency vs temperature** | How much winter really costs you, from Home Assistant's own weather |
| 🕘 **Your past trips too** | On install it rebuilds earlier trips and charges from Home Assistant's history |
| 🛠️ **Fix the odd bad trip** | Split, merge or delete |
| 🚙🚙 **Several cars** | Add each car; the card and page switch between them |

## Screenshots

**Trips:** labels (picked or automatic from the place), charges shown between trips, sortable columns and CSV export.

<img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/docs/images/trips.png" alt="Trips tab">

**Naming a place:** click *unnamed · name it* on any stop. Drag the pin and the circle's edge to fit the spot, type a name or press **Suggest names** for nearby shops and venues (from OpenStreetMap, only when you press it), and every past and future trip there takes the name.

<img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/docs/images/name-place.png" alt="Naming a place: map with a resizable circle and suggested business names">

**Charging:** sessions with energy, peak power and cost, and charging rates per place (here Home with an off-peak window) with their history.

<img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/docs/images/charging.png" alt="Charging tab">

**Places:** named places and default trip labels. Open **Map** to drag a place's pin or resize its circle.

<img src="https://raw.githubusercontent.com/david40i9/ha-mercedes-ev-logbook/main/docs/images/places.png" alt="Places tab">

## Install

1. In **HACS**, open the ⋮ menu → **Custom repositories**, add `https://github.com/david40i9/ha-mercedes-ev-logbook` with type **Integration**.
2. Find **Mercedes EV Logbook** in HACS, select **Download**, then restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Mercedes EV Logbook** and pick your car.

<details>
<summary>Manual install</summary>

Download the latest [release](https://github.com/david40i9/ha-mercedes-ev-logbook/releases), copy `custom_components/mb_logbook` into your `config/custom_components` folder and restart Home Assistant.
</details>

The **Mercedes EV Logbook** page appears in the sidebar, and the card is registered for your dashboards automatically.

## Setup

Pick the car from the Mercedes integration and its sensors are filled in for you. Enter the **usable battery capacity** in kWh:

| Model | Usable kWh (approx.) |
|---|---|
| EQA / EQB | 66.5 |
| CLA (electric) | 85 |
| EQC | 80 |
| EQE saloon / SUV | 89 – 90.6 |
| EQS saloon / SUV | 107.8 – 108.4 |
| EQV | 90 |

Check your model year's spec sheet; capacities changed between versions. Choose the **currency** for charging costs (it starts as Home Assistant's). Everything can be changed later with **Configure**.

### What it uses

| | Needed? | What it adds |
|---|---|---|
| Odometer, battery %, GPS tracker | **Yes** | Trips, distance, energy and routes |
| Ignition state | Optional | The most precise trip start and end (otherwise trips are detected from the car moving) |
| Trip distance counter | Optional | 0.1-mile/km trip distances when the odometer only reports whole units |
| Charging active, charging power | Optional | Charging sessions, peak power, and home kWh when there is no charger meter |
| Electricity price sensor | Optional | Live price for home charges (per kWh or MWh, pounds, pence, euros, cents, öre…; converted automatically). Without one, home charges use the rates you set in the Charging tab |
| Home charger energy meter | Optional | The most accurate home energy, losses included (any total-kWh sensor: Zappi, Wallbox, Ohme, smart plug, CT clamp…). Without one, home kWh comes from the car's charging power over the session, else its battery % |
| Outside temperature | Optional | Defaults to Home Assistant's weather for your home; or pick any temperature sensor |

Any car that provides these entities works, not only Mercedes. Pick them yourself in the setup form.

## Using it

**Sidebar page.** *Overview* shows this month against last; mileage by label for any week, month, 3 months or year; 12-month charts of distance, energy, charging cost and efficiency; battery health; efficiency against outside temperature; this month's routes; and your most-visited places. *Logbook* is the full card for the selected car.

**Trips.** Click a trip to see its route. Choose a label from the dropdown, or give a place a default label (Places tab) and trips there are labelled automatically. Filter by label and the totals follow; **Export CSV** saves the trips shown.

**Places.** A stop outside your Home Assistant zones shows as *unnamed · name it*. Name it and drag the circle to fit; every past and future trip or charge there takes the name. Press **Suggest names** for nearby shops and venues from OpenStreetMap.

**Charging rates.** Give any charging place, Home included, a price per kWh (or mark it free) from a date, with an optional off-peak price and time window for EV tariffs (for example 7p from 00:30 to 05:30). Each charge is costed at the rate in force when it started, split by how much of it fell in the off-peak window, so a new rate never changes older charges. No price sensor or energy integration is needed. Costs are shown in the currency chosen in the car's options.

**Fixing trips.** Open a trip for **Split at the stop** (two drives logged as one, for example after the car lost signal in an underground car park), **Merge with next trip**, or **Delete this trip**.

**Monthly summary** (optional, in the car's options): last month's trips, distance, energy, charging and idle drain, sent on the 1st at 09:00 as a Home Assistant notification or through any notify service. The **Send monthly summary** action sends it now.

<details>
<summary>Dashboard card</summary>

The card is registered as a dashboard resource automatically; add it from the card picker or in YAML:

```yaml
type: custom:mb-logbook-card
period: month     # week, month, quarter or year
# car: EQS        # optional: lock the card to one car (by name)
```
</details>

<details>
<summary>Sensors (per car)</summary>

| Sensor | |
|---|---|
| Trips, distance, energy used, efficiency | This month |
| Last trip distance, energy, efficiency | With from, to and battery as attributes |
| Idle drain | % per day lost while parked (uncharged parks of 6 h or more, last 30 days) |
| Last charge energy and cost | |
| Charged, charging cost | This month |
| Trip in progress, charging session | Binary sensors |
</details>

<details>
<summary>Actions</summary>

- **Import history**: rebuild trips and charges from Home Assistant's recorded history for the time before the car was added. It runs once automatically; re-running replaces only imported rows, never live ones.
- **Send monthly summary**: send last month's summary now.
</details>

<details>
<summary>How trips are detected</summary>

A trip starts when the ignition leaves "off" and ends when it has been off for a few minutes (3 by default). If the car stops moving for 30 minutes while its ignition still says "on" (it can lose signal before reporting the ignition off), the trip ends at the last movement and the next drive starts a new one.

Without an ignition sensor, a trip starts when the car moves (odometer or trip counter up, or a position jump of 200 m or more) and ends after the same few minutes without movement.

Distance comes from the car's 0.1-resolution "distance since start" counter, checked against the odometer. Ignition events that don't move the car, such as pre-conditioning, are ignored.

Data is stored in `config/mb_logbook.db` (SQLite), which is included in Home Assistant backups.
</details>

## Privacy

Everything stays in Home Assistant. Places are named from your own zones and the places you name. The one exception is optional: **Suggest names** sends the single point you are naming to OpenStreetMap's Photon service (falling back to the Overpass API), only when you press it. Route maps use Home Assistant's own map component and tiles.

## Limits

- Mercedes reports battery % in whole numbers, so kWh for short trips is approximate (1% of a large battery is about 1 kWh).
- The car sends its position about every two minutes while driving, so routes are drawn point to point.

## Contributing

Bug reports and ideas are welcome in [Issues](https://github.com/david40i9/ha-mercedes-ev-logbook/issues). Planned work is in [TODO.md](TODO.md).

---

**Not affiliated with Mercedes-Benz.** This is an independent community project. Mercedes-Benz and EQ are trademarks of Mercedes-Benz Group AG, used here only to say which cars it works with. Licensed under the [MIT licence](LICENSE).
