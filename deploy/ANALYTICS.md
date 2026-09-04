# Analytics

Two layers, both self-hosted and privacy-respecting — no third-party trackers, no
ad networks, nothing that clashes with the project's data ethics.

## 1. In-app usage analytics (built in)

**Admin → 📊 Analytics.** Captures what the log-based tools can't see through
Streamlit's WebSocket: which **surface/view** people use, and key **events**
(submissions, feedback). Design:

- **No cookies. No raw IPs stored.** A visitor is a per-day rotating hash
  (`sha256(ip + day + salt)`), so daily uniques are approximate and nobody is
  trackable across days. The `analytics_events` table has no IP column.
- **Do-Not-Track honoured** — `DNT: 1` requests aren't logged.
- **Views counted once per session** per surface/view (Streamlit reruns don't
  double-count).
- **Country is optional** and off by default (see below).

Retention: `central.analytics.prune(conn, keep_days=400)` — wire to a cron if you
want a rolling window.

### Optional country lookup (no third party)
Country resolves server-side from a local **MaxMind GeoLite2** database — the IP
never leaves your box:
```bash
# free MaxMind account -> download GeoLite2-Country.mmdb
pip install geoip2 --break-system-packages
# then in /opt/glassdatabase/.env:
GEOIP_DB=/opt/glassdatabase/data/GeoLite2-Country.mmdb
```
Leave it unset and the dashboard simply omits country.

## 2. Log-based analytics with GoAccess (recommended for geo + raw traffic)

The richest geo/referrer/traffic picture comes from the Apache logs you already
have — parsed by **GoAccess** into a live HTML dashboard. Zero client code, works
retroactively on existing logs.

```bash
sudo apt-get install -y goaccess
# one-off HTML report (add --geoip-database for country/city):
sudo goaccess /var/log/apache2/glassdatabase-access.log \
  --log-format=COMBINED -o /var/www/html/stats.html \
  --geoip-database=/opt/glassdatabase/data/GeoLite2-City.mmdb
# or live, self-refreshing:
sudo goaccess /var/log/apache2/glassdatabase-access.log \
  --log-format=COMBINED -o /var/www/html/stats.html --real-time-html
```
GoAccess gives you top pages (by URL path — `/explore/`, `/glowtbook/`, `/api/…`),
countries/cities, referrers, browsers, and bandwidth. Protect `stats.html` behind
the same basic-auth as `/admin/` if you expose it.

Note: Streamlit is a single-page app over WebSocket, so log page-view counts track
*URL surfaces*, not in-app view switches — that's exactly what layer 1 adds.

## 3. If you want a hosted-style dashboard

A cookieless product like **Umami** (self-hosted, open-source, free) or
**Plausible** drops a tiny script on the **static** pages (landing page, capture
apps) and gives pretty dashboards + referrers with no consent banner. It won't see
inside the Streamlit apps — pair it with layer 1 for that.

## Recommendation

- Turn on **layer 1** now (it's built — nothing to install) for "what parts of the
  site do people use." Add `GEOIP_DB` if you want country in the same dashboard.
- Add **GoAccess** (layer 2) for the traffic/geo/referrer picture off your logs.
- Reach for **Umami/Plausible** only if you want a polished public-pages dashboard.

All three avoid cookies and third-party ad trackers, and keep the data on your box
— consistent with how the rest of the site treats people's data.
