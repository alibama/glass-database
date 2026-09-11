# Studio open-data API contract

For a studio's live operating data to show up on glassdatabase.org (the map + the
homepage "Live from a working studio" section), its app exposes a small **read-only,
CC-BY, CORS-open** HTTP API on a **public https URL** (e.g. a Tailscale Funnel). The
Glass Database polls it **at most once per 15 minutes** and never before an admin has
approved the submitted source.

## Required
```
GET  {base}/summary   ->  200 application/json, a single object
```
The Glass Database reads these keys from `/summary` (first match wins, so any of the
aliases work):

| Shown as | Keys it looks for |
|---|---|
| Firings logged | `firings`, `count`, `firings_logged`, `n` |
| Energy (est.)  | `energy_kwh`, `kwh`, `energy` |
| Cost (est.)    | `cost_usd`, `cost` |
| Since          | `since`, `start`, `first` |

Example that works today:
```json
{ "firings": 4, "energy_kwh": 128.5, "cost_usd": 18.0, "since": "2026-09-11" }
```
> If your `/summary` uses different key names, tell me the real ones and I'll add them
> to the alias list — or standardize on the table above and it just works.

## Optional
```
GET  {base}/firings         ->  JSON array (recent firings; the map may show a count)
GET  {base}/devices         ->  JSON (no MAC/BLE addresses or secrets)
GET  {base}/firings.csv     ->  CSV (for bulk download / research)
```

## Rules
- **https only**, on a **publicly reachable** host (Tailscale **Funnel**, not Serve —
  glassdatabase.org must reach it from the open internet), with a **valid cert**.
- **CORS-open** (`Access-Control-Allow-Origin: *`) so browsers can read it directly if
  needed. Server-side polling (what we do) doesn't need CORS, but keep it open for the
  ecosystem.
- **No secrets, no device addresses** — this is public open data.
- **CC-BY-4.0** attribution; include a studio name.
- Keep `/summary` cheap — it's polled on a 15-min cache; a full recompute per request
  is fine at that rate but don't make it heavy.

## How a studio gets listed
On **glassdatabase.org/studios.html → "Publish your studio's data"**, submit the
studio name, location (lat/lng), and the API base URL. It lands **pending**; an admin
reviews the URL (it's checked for https + a public address) and approves it, after
which it's polled and appears on the map with a live-data marker. Submission never
triggers a fetch — approval does.
