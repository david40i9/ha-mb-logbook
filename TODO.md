# To do

Ideas and planned work for MB Logbook. Not in any order yet.

## Card and page

- [x] **Step through periods**: back and forward arrows next to the period buttons, to move through weeks, months or years (for example "previous week" / "next week"), with the dates shown.
- [x] **Delete trips**: a delete action on a trip (admin only, with confirmation) for bogus or test trips. The `mb_logbook/delete_trip` WebSocket command already exists; the card needs the button. Consider merging two trips as well.

## Charging

- [x] **Charging places with their own rates**: name charging locations (home, work, public sites) and give each a price per kWh (or free), so charges away from home get a cost too.
  - Rates change over time: keep each place's rate history (rate + valid from date) and cost every charge at the rate that applied when it happened, so editing a rate never rewrites old costs.
  - Decide how this fits with the live home price sensor (metered home charging) — probably: a place uses either a sensor or a fixed rate history.

## Done

- [x] Multi-car trips, parked drain, charging sessions (0.1)
- [x] Named places with draggable, resizable circles (0.2 – 0.4)
- [x] Import history from the recorder (0.3)
- [x] Charges shown between trips; parks keep their charged flag (0.5)
- [x] Sidebar page with overview charts and routes map (0.6)
