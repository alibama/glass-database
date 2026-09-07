# Harvesting content — consent, ownership, control

A way to feature glass-related content from an artist's own website/social in the
directory **without taking control away from them**. Three non-negotiables:

1. **Consent first.** Only artists who ticked harvest consent on their (approved)
   directory entry are eligible. `record_candidate()` re-checks it every time — a
   candidate from a non-consenting artist is simply refused.
2. **The artist stays the owner.** Every harvested image is signed with C2PA
   provenance that asserts the **artist as creator/owner** and records that it was
   *harvested with consent* from a named source, with a rights line
   ("© the artist … retains ownership and controls publication").
3. **Nothing is published automatically.** Items land as **pending** and an admin
   (or, later, the artist) approves each one in **Admin → 🌾 Harvest** before it can
   appear. Approved images are served C2PA-intact at `/api/harvest/<id>/image`.

## How it runs
`central/harvest.py` is the source-agnostic core (consent gate, C2PA signing,
pending store, approval). The fetching is a separate runner so the core stays
testable:

```bash
pip install playwright beautifulsoup4 && playwright install chromium
python deploy/harvest_runner.py --artist "Jane Doe" --website https://janedoe.art
```

Each candidate is recorded pending; you approve them item-by-item in the admin.

## The important legal/ethical bits (please read)
- **Instagram: use the API, not a scraper.** Instagram's terms prohibit scraping,
  and browser-scraping gets IPs blocked. The runner's Instagram path uses the
  **Instagram Basic Display / Graph API with the artist's own OAuth token** — i.e.
  the artist authorises access to their *own* media. That's the correct, durable
  route. Don't point a headless browser at instagram.com.
- **Websites: respect robots.txt** (the runner checks it) and the site's terms.
  Consent from the artist covers *their* content; it doesn't override a platform's
  rules.
- **Consent is per-artist and revocable.** If an artist withdraws consent, stop
  harvesting and remove their pending/published harvested items. (Untick the flag
  on their entry; the gate then refuses new candidates.)
- **We assert ownership, we don't claim it.** The C2PA credential attributes the
  work to the artist and documents the harvest — it is not a claim of rights by the
  site. Keep it that way.

## Why this shape
It mirrors the rest of the platform: consent + provenance + a human approval step,
with the person who made the work in control of what appears. Harvest is a
convenience for *them*, not a way around them.
