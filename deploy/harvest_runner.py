#!/usr/bin/env python3
"""
Harvest runner — fetch glass-related images from a *consenting* artist's own
website (and, via the official API, their Instagram) and hand each candidate to
central.harvest.record_candidate(), which re-checks consent, signs C2PA ownership
back to the artist, and stores it as PENDING for per-item admin approval.

Run on the server, e.g.:
    python deploy/harvest_runner.py --artist "Jane Doe" --website https://janedoe.art

Requirements for the website path:
    pip install playwright beautifulsoup4 && playwright install chromium

IMPORTANT — read deploy/HARVEST.md first:
  * Only run for artists who ticked harvest consent (record_candidate enforces it).
  * Respect robots.txt and each site's terms.
  * For Instagram, DO NOT scrape — use the Instagram Basic Display / Graph API with
    the artist's own OAuth token (scraping violates Instagram's terms). The
    --instagram-token path below uses the API, not a browser.
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
import urllib.request


def _robots_allows(url: str) -> bool:
    import urllib.robotparser
    p = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt"); rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return True


def from_website(url: str, max_images: int = 20) -> list[tuple[bytes, str]]:
    """Collect candidate images from a page with Playwright (renders JS galleries)."""
    if not _robots_allows(url):
        print(f"robots.txt disallows {url} — skipping."); return []
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page()
        pg.goto(url, wait_until="networkidle", timeout=30000)
        srcs = pg.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)")
        for s in srcs:
            if not s or s.startswith("data:"):
                continue
            try:
                with urllib.request.urlopen(s, timeout=20) as r:
                    data = r.read()
                if len(data) > 40_000:            # skip icons/thumbnails
                    out.append((data, s))
            except Exception:
                pass
            if len(out) >= max_images:
                break
        br.close()
    return out


def from_instagram(token: str, max_images: int = 20) -> list[tuple[bytes, str]]:
    """Instagram Basic Display API (the artist's own OAuth token) — NOT scraping."""
    api = ("https://graph.instagram.com/me/media?fields=media_type,media_url,permalink"
           f"&access_token={token}")
    out = []
    try:
        import json
        with urllib.request.urlopen(api, timeout=20) as r:
            data = json.loads(r.read())
        for m in data.get("data", [])[:max_images]:
            if m.get("media_type") == "IMAGE" and m.get("media_url"):
                with urllib.request.urlopen(m["media_url"], timeout=20) as ir:
                    out.append((ir.read(), m.get("permalink") or m["media_url"]))
    except Exception as ex:
        print("Instagram API error:", ex)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", required=True)
    ap.add_argument("--website")
    ap.add_argument("--instagram-token")
    ap.add_argument("--max", type=int, default=20)
    args = ap.parse_args()

    sys.path.insert(0, "/opt/glassdatabase")
    from central import harvest
    from central.dbconn import connect
    conn = connect()
    if not harvest.consent_ok(conn, args.artist):
        print(f"'{args.artist}' has not consented (or isn't an approved artist). Nothing harvested.")
        return

    candidates = []
    if args.website:
        candidates += [(b, "website", u) for b, u in from_website(args.website, args.max)]
    if args.instagram_token:
        candidates += [(b, "instagram", u) for b, u in from_instagram(args.instagram_token, args.max)]

    kept = 0
    for data, source, src_url in candidates:
        rid = harvest.record_candidate(conn, args.artist, source, src_url, data)
        if rid:
            kept += 1
    print(f"Recorded {kept} pending item(s) for '{args.artist}'. Approve them in Admin → Harvest.")


if __name__ == "__main__":
    main()
