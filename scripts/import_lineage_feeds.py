#!/usr/bin/env python3
"""
Import lineage (who taught whom) from partner studios' read-only feeds.

Rows land in ``lineage_submissions`` as **pending** — nothing is public until a reviewer
approves it in the Review queue. Safe to re-run (cron, by hand): rows are keyed by feed
URL + student + mentor + relation, so a run updates instead of duplicating. See
``central/lineage_feeds.py`` for the rules.

    python -m scripts.import_lineage_feeds                    # every configured feed
    python -m scripts.import_lineage_feeds --dry-run          # what would change
    python -m scripts.import_lineage_feeds --url https://cvilleglass.org/api/residents.php
    python -m scripts.import_lineage_feeds --file residents.json --url https://cvilleglass.org/api/residents.php

Feeds: ``central.lineage_feeds.DEFAULT_FEEDS`` plus ``GLASSDB_LINEAGE_FEEDS`` (comma-separated).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", action="append", help="a feed URL (repeatable); default: every configured feed")
    ap.add_argument("--file", help="read the feed JSON from this file instead of fetching (needs one --url)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--no-notify", action="store_true", help="don't post a Discord note for reviewers")
    a = ap.parse_args(argv)

    from central import lineage_feeds
    from central.dbconn import connect
    conn = connect()
    kw = {"dry_run": a.dry_run, "notify_reviewers": not a.no_notify}
    if a.file:
        if not a.url or len(a.url) != 1:
            ap.error("--file needs exactly one --url (the feed's address, kept as the rows' source)")
        feed = json.loads(Path(a.file).read_text(encoding="utf-8"))
        results = [lineage_feeds.import_feed(conn, a.url[0], feed=feed, **kw)]
    else:
        results = lineage_feeds.import_all(conn, a.url, **kw)
    failed = False
    for r in results:
        if r.get("error"):
            failed = True
            print(f"✗ {r['feed']}: {r['error']}")
            continue
        tag = " (dry run)" if r["dry_run"] else ""
        print(f"✓ {r['feed']}{tag}: {r['rows']} rows · {r['new']} new · {r['changed']} changed · "
              f"{r['restored']} restored · {r['unchanged']} unchanged · {r['withdrawn']} withdrawn")
    if any(r.get("new") or r.get("changed") or r.get("restored") for r in results) and not a.dry_run:
        print("New and changed rows are pending: approve them in the Review queue (/review.html).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
