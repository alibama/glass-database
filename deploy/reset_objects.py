#!/usr/bin/env python3
"""
Wipe the PUBLISHED objects and their submissions — a clean slate for the public
registry — WITHOUT touching artists, studios, techniques, users, newsletter,
opportunities, harvest, feedback, or settings.

    python deploy/reset_objects.py            # prompts, shows counts
    python deploy/reset_objects.py --yes      # no prompt

Run on the box (or locally against the same GLASSDB_PATH). Removes rows from:
objects, object_images, object_submissions, object_submission_images, and the
matching _approvals rows; resets the _datasets counts.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/opt/glassdatabase")
try:
    from central.dbconn import connect
except Exception:
    sys.path.insert(0, ".")
    from central.dbconn import connect

TABLES = ["object_images", "objects", "object_submission_images", "object_submissions"]


def _count(c, t):
    try:
        return c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()
    c = connect()

    print("Current row counts:")
    for t in TABLES:
        print(f"  {t}: {_count(c, t)}")
    if not args.yes:
        if input("\nDelete ALL of the above (objects + submissions)? Type 'wipe': ").strip() != "wipe":
            print("Aborted."); return

    for t in TABLES:
        try:
            c.execute(f'DELETE FROM "{t}"')
        except Exception as e:
            print(f"  (skip {t}: {e})")
    # drop the approval rows for the object tables
    for t in ("objects", "object_submissions"):
        try:
            c.execute("DELETE FROM _approvals WHERE tbl=?", (t,))
        except Exception:
            pass
    # reset dataset counts
    for t in ("objects", "object_submissions"):
        try:
            c.execute(f'UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM "{t}") WHERE tbl=?', (t,))
        except Exception:
            pass
    c.commit()
    print("\nDone. Objects registry is clear:")
    for t in TABLES:
        print(f"  {t}: {_count(c, t)}")
    print("Artists, studios, techniques, users, etc. are untouched.")


if __name__ == "__main__":
    main()
