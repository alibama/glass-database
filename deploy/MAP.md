# Studios map — basemap tiles / API key

`studios.html` is a **static public file**, so it can't read `.env` itself. It asks
the API for its tile config (`GET /api/map-config.json`), which reads these from
`/opt/glassdatabase/.env`:

```
MAP_TILES_URL=<the full tile URL from your provider, with your key>
MAP_TILES_ATTRIBUTION=<attribution text>   # optional
MAP_TILES_SUBDOMAINS=abcd                   # optional
MAP_TILES_MAXZOOM=19                        # optional
```

Then restart the API: `sudo systemctl restart glassdb-api`. With no `MAP_TILES_URL`
set, the map falls back to CARTO's keyless dark basemap.

## Where the key goes — paste your provider's exact tile URL
Use the raster `{z}/{x}/{y}` URL your dashboard gives you. **The key must match the
provider** — a CARTO key on a Stadia URL returns 401.

- **CARTO** (dark; requires the key as `?key=`, get one at carto.com/basemaps/apikey):
  `https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png?key=YOUR_CARTO_KEY`
- **Stadia Maps** (Alidade Smooth Dark):
  `https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key=YOUR_KEY`
- **MapTiler**:
  `https://api.maptiler.com/maps/streets-v2-dark/{z}/{x}/{y}.png?key=YOUR_KEY`
- **Mapbox**:
  `https://api.mapbox.com/styles/v1/mapbox/dark-v11/tiles/{z}/{x}/{y}?access_token=YOUR_KEY`
Note: CARTO's raster basemaps are officially in a **legacy/retiring** state — they
work today with a key, but if CARTO discontinues them, switch to another raster
provider above (or a MapLibre vector style). The tile URL is a one-line `.env`
change either way.

## Important: a map-tile key is not a secret
It ships to the browser in the tile requests — that's normal and unavoidable for
client-side maps. `.env` just centralizes the config; it does **not** hide the key.
So **restrict the key to `glassdatabase.org`** (referrer/domain allow-list) in the
provider dashboard. Never put a real secret (signing token, DB creds) in
`MAP_TILES_URL`.
