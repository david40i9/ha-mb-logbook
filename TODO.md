# To do

Ideas and planned work for MB Logbook. Not in any order yet.

## Card and page

- [x] **Step through periods**: back and forward arrows next to the period buttons, to move through weeks, months or years (for example "previous week" / "next week"), with the dates shown.
- [x] **Delete trips**: a delete action on a trip (admin only, with confirmation) for bogus or test trips. The `mb_logbook/delete_trip` WebSocket command already exists; the card needs the button. Consider merging two trips as well.

## Charging

- [x] **Charging places with their own rates**: name charging locations (home, work, public sites) and give each a price per kWh (or free), so charges away from home get a cost too.
  - Rates change over time: keep each place's rate history (rate + valid from date) and cost every charge at the rate that applied when it happened, so editing a rate never rewrites old costs.
  - Decide how this fits with the live home price sensor (metered home charging) — probably: a place uses either a sensor or a fixed rate history.

## Ideas (from other EV loggers)

Ranked by value for effort, keeping MB Logbook local-only and multi-car.

1. ~~**Export trips and charges to CSV**~~ (trips 0.11, charges 0.16), filtered by period, car and place — for mileage claims and expenses. *(Tessie and TeslaFi exports; Tessie users use them for tax-time mileage.)*
2. ~~**Business / personal tagging**~~ (labels 0.11, default label per place 0.12) on trips (with a default per place, e.g. trips to work), and a mileage summary per tag. *(TeslaFi / Tessie trip classification.)*
3. *Not planned (depends on a home charger meter most setups lack):* **Charging efficiency (gross vs net)**: metered kWh at the charger vs kWh that reached the battery, per session and per month — we already have both numbers for home charges. *(TeslaMate shows net and gross consumption.)*
4. ~~**Battery health trend**~~ (0.16): projected range at 100% (range ÷ battery %), sampled at high charge levels, plotted month by month. *(TeslaMate "projected 100% range"; Tessie battery health.)*
5. ~~**Efficiency vs outside temperature**~~ (0.16): record the outdoor temperature (any HA weather or temperature sensor) at each trip and chart efficiency against it — explains winter drops. *(TeslaMate efficiency dashboards; Tessie.)*
6. **Drain detail**: idle drain by place and by month (where the car loses most while parked), flagging parks with pre-climate. *(TeslaMate vampire drain dashboard; the "Stats" app shows drain per location.)*
7. **Off-peak share and "if charged at peak" saving** for home charges, using the live price already recorded per session. *(evcc's cheapest-slot planning, turned into a report.)*
8. *Not planned for now:* **Events for automations**: fire `mb_logbook_trip_ended` / `mb_logbook_charge_ended` events with the record, so users can send their own notifications or summaries. *(TeslaMate publishes drives and charges over MQTT for automations.)*
9. **Visited places map / timeline**: all trip ends as a heat map, and a day-by-day timeline. *(TeslaMate "visited" dashboard.)*
10. ~~**Monthly summary notification**~~ (0.16) (opt-in): last month's miles, energy, cost and drain.
11. **Charging curve** for rapid charges: store power samples during a session and plot power vs battery %. *(TeslaMate charge detail.)* Needs more frequent data than the car may send.
12. *Considered, not planned:* sending live data to A Better Route Planner — useful for some, but it sends location and battery data to an outside service, against MB Logbook's local-only aim. Could only ever be an opt-in, off by default.

## Done

- [x] Multi-car trips, parked drain, charging sessions (0.1)
- [x] Named places with draggable, resizable circles (0.2 – 0.4)
- [x] Import history from the recorder (0.3)
- [x] Charges shown between trips; parks keep their charged flag (0.5)
- [x] Sidebar page with overview charts and routes map (0.6)
- [x] Period arrows, delete trip, charging rates with history (0.7)
- [x] Suggest names for unnamed places from OpenStreetMap, on request only (0.8)
- [x] Places keep the stops they were named from (0.9)
- [x] Trip labels, label filter with totals, CSV export (0.11)
- [x] Default trip label per place (0.12)
- [x] Works without a charger meter (cost from the car's charging power) or an ignition sensor (trips from movement) (0.10)
- [x] End trips stuck with the ignition on; split trip (0.15)
- [x] Sortable columns, business mileage chart (0.14)
- [x] Battery health, efficiency vs temperature, monthly summary, merge trips, charges CSV (0.16)
