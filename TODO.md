# To do

Ideas and planned work for Mercedes EV Logbook. Ranked roughly by value for effort, keeping it local-only and multi-car.

## Open

1. **Drain detail**: idle drain by place and by month (where the car loses most while parked), flagging parks with pre-climate. *(TeslaMate vampire drain dashboard; the "Stats" app shows drain per location.)*
2. **Off-peak share and saving**: how much home charging fell in the off-peak window, and what it would have cost at the peak rate. Uses the off-peak rates from the Charging tab or the live price recorded per session. *(evcc's cheapest-slot planning, turned into a report.)*
3. **Visited places map and timeline**: all trip ends as a heat map, and a day-by-day timeline. *(TeslaMate "visited" dashboard.)*
4. **Charging curve** for rapid charges: store power samples during a session and plot power against battery %. *(TeslaMate charge detail.)* Needs more frequent data than the car may send.
5. **HACS default list**: requested; waiting for review.

## Not planned

- **Charging efficiency (gross vs net)**: needs a home charger meter, which most setups lack.
- **Events for automations** (`mb_logbook_trip_ended` / `mb_logbook_charge_ended`): would depend on each user's own automations.
- **Hour-range filter and previous-period comparison** on the stats.
- **Sending live data to A Better Route Planner**: it sends location and battery data to an outside service, against the local-only aim. Could only ever be opt-in, off by default.

## Done

- [x] Multi-car trips, parked drain, charging sessions (0.1)
- [x] Named places with draggable, resizable circles (0.2 – 0.4)
- [x] Import earlier trips and charges from the recorder (0.3)
- [x] Charges shown between trips; parks keep their charged flag (0.5)
- [x] Sidebar page with overview charts and routes map (0.6)
- [x] Period arrows, delete trip, charging rates per place with history (0.7)
- [x] Suggest names for unnamed places from OpenStreetMap, on request only (0.8)
- [x] Places keep the stops they were named from (0.9)
- [x] Works without a charger meter (cost from the car's charging power) or an ignition sensor (trips from movement) (0.10)
- [x] Trip labels, label filter with totals, trips CSV export (0.11)
- [x] Default trip label per place (0.12)
- [x] Mileage by label on the overview (0.13)
- [x] Sortable columns, business mileage chart (0.14)
- [x] End trips stuck with the ignition on (underground car parks); split a trip at its stop (0.15)
- [x] Battery health trend, efficiency vs outside temperature, monthly summary, merge trips, charges CSV export (0.16)
- [x] Outside temperature from Home Assistant's own weather; stored with each trip (0.17)
- [x] Renamed to Mercedes EV Logbook; brand icon; HACS and hassfest validation; GitHub releases (0.18)
- [x] Price sensors in pence, cents or per MWh converted (0.19)
- [x] Costs from your own rates with an optional off-peak window, no price sensor needed; currency per car (0.20)
- [x] Per-kWh prices shown as entered, up to 4 decimals (0.20.2)
- [x] Two cars on one home charger meter: the meter is split between the cars charging at home by their charging power, never counted twice (0.21)
- [x] Without a charger meter, home kWh and costs come from the car's charging power over the session instead of battery % (0.22)
