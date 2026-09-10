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
Use the raster `{z}/{x}/{y}` URL your dashboard gives you. Drop-in dark styles:

- **Stadia Maps** (Alidade Smooth Dark — closest to the current look):
  `https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key=YOUR_KEY`
- **MapTiler**:
  `https://api.maptiler.com/maps/streets-v2-dark/{z}/{x}/{y}.png?key=YOUR_KEY`
- **Mapbox**:
  `https://api.mapbox.com/styles/v1/mapbox/dark-v11/tiles/{z}/{x}/{y}?access_token=YOUR_KEY`
- **CARTO (keyed):** paste the raster tile URL from your CARTO dashboard. If CARTO
  only offers a *vector* style (not a raster `{z}/{x}/{y}` URL), use Stadia or
  MapTiler above — both give a simple keyed raster URL and look nearly identical.

## Important: a map-tile key is not a secret
It ships to the browser in the tile requests — that's normal and unavoidable for
client-side maps. `.env` just centralizes the config; it does **not** hide the key.
So **restrict the key to `glassdatabase.org`** (referrer/domain allow-list) in the
provider dashboard. Never put a real secret (signing token, DB creds) in
`MAP_TILES_URL`.
