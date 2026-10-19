#!/usr/bin/env python3
"""
Seed a small, synthetic demo database so the project runs out of the box.

None of this is real data — it's invented so you can clone the repo, seed, and
have every surface (API, explorer, admin, Glowtbook) show something without
needing the real spreadsheets. It follows the same self-describing registry the
real ingest builds (`_datasets` / `_columns`), including one restricted dataset
and a couple of private columns so the withholding behaviour is visible.

Usage:
    python -m scripts.seed_demo                 # writes data/glassdb.db
    GLASSDB_PATH=/tmp/demo.db python -m scripts.seed_demo
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from central.dbconn import connect  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

# (table, domain, visibility, description, columns[(name,label,is_public)], rows[dict])
DATASETS = [
    ("studios", "studios", "public",
     "Glass studios and hot shops (demo data)",
     [("name", "Studio Name", 1), ("city", "City", 1), ("region", "Region", 1),
      ("country", "Country", 1), ("type", "Type", 1), ("founded", "Founded", 1),
      ("lat", "Lat", 1), ("lng", "Lng", 1), ("email_address", "Email", 0)],
     [
        dict(name="Blue Ridge Hot Glass", city="Crozet", region="VA", country="USA",
             type="Hot shop", founded="2009", lat="38.069", lng="-78.701",
             email_address="hello@example.com"),
        dict(name="Tidewater Glassworks", city="Norfolk", region="VA", country="USA",
             type="Studio", founded="2015", lat="36.851", lng="-76.285",
             email_address="studio@example.com"),
        dict(name="Cascade Flameworks", city="Portland", region="OR", country="USA",
             type="Flameworking", founded="2012", lat="45.512", lng="-122.658",
             email_address="info@example.com"),
        dict(name="Murano Vetro Collettivo", city="Venice", region="Veneto",
             country="Italy", type="Hot shop", founded="1998", lat="45.459",
             lng="12.353", email_address="ciao@example.com"),
        dict(name="Kingsway Kiln Studio", city="Bristol", region="England",
             country="UK", type="Kiln / fusing", founded="2018", lat="51.454",
             lng="-2.588", email_address="hello@example.co.uk"),
     ]),
    ("artists", "artists", "public",
     "Artists working in glass (demo data)",
     [("artist_name", "Artist Name", 1), ("based_in", "Based In", 1),
      ("primary_technique", "Primary Technique", 1),
      ("birth_year", "Birth Year", 1), ("website", "Website", 1),
      ("phone", "Phone", 0)],
     [
        dict(artist_name="Rae Sutter", based_in="Crozet, VA",
             primary_technique="Blown / cane", birth_year="1984",
             website="https://example.com/rae", phone="555-0100"),
        dict(artist_name="Ivo Marchetti", based_in="Venice, IT",
             primary_technique="Incalmo", birth_year="1971",
             website="https://example.com/ivo", phone="555-0101"),
        dict(artist_name="Nadia Okonkwo", based_in="Portland, OR",
             primary_technique="Flameworking", birth_year="1990",
             website="https://example.com/nadia", phone="555-0102"),
        dict(artist_name="Bea Lindqvist", based_in="Bristol, UK",
             primary_technique="Kiln casting", birth_year="1965",
             website="https://example.com/bea", phone="555-0103"),
        dict(artist_name="Theo Park", based_in="Norfolk, VA",
             primary_technique="Murrine", birth_year="1996",
             website="https://example.com/theo", phone="555-0104"),
     ]),
    ("studio_intake", "studios", "restricted",
     "Raw studio intake — restricted, never served publicly (demo data)",
     [("name", "Studio Name", 1), ("email", "Email", 0), ("notes", "Notes", 0)],
     [
        dict(name="Pending Applicant A", email="a@example.com", notes="follow up"),
        dict(name="Pending Applicant B", email="b@example.com", notes="needs photos"),
     ]),
]


def seed() -> str:
    c = connect()
    c.executescript("""
      CREATE TABLE IF NOT EXISTS _datasets (tbl TEXT PRIMARY KEY, domain TEXT,
        source_file TEXT, source_sheet TEXT, visibility TEXT, row_count INTEGER,
        description TEXT, updated_at TEXT);
      CREATE TABLE IF NOT EXISTS _columns (tbl TEXT, column TEXT, label TEXT,
        ordinal INTEGER, is_public INTEGER, PRIMARY KEY (tbl, column));
    """)
    for tbl, domain, vis, desc, cols, rows in DATASETS:
        c.execute(f'DROP TABLE IF EXISTS "{tbl}"')
        coldefs = ", ".join(f'"{name}" TEXT' for name, _, _ in cols)
        c.execute(f'CREATE TABLE "{tbl}" (_row_id TEXT PRIMARY KEY, '
                  f'_source_file TEXT, _source_sheet TEXT, _imported_at TEXT, {coldefs})')
        for i, (name, label, pub) in enumerate(cols):
            c.execute("INSERT OR REPLACE INTO _columns (tbl,column,label,ordinal,is_public) "
                      "VALUES (?,?,?,?,?)", (tbl, name, label, i, pub))
        for n, row in enumerate(rows):
            rid = f"{tbl}-{n:04d}"
            names = [nm for nm, _, _ in cols]
            allc = ["_row_id", "_source_file", "_source_sheet", "_imported_at", *names]
            vals = [rid, "demo-seed", "demo", NOW, *[row.get(nm, "") for nm in names]]
            c.execute(f'INSERT INTO "{tbl}" ({", ".join(chr(34)+x+chr(34) for x in allc)}) '
                      f'VALUES ({", ".join("?" for _ in allc)})', vals)
        c.execute("""INSERT OR REPLACE INTO _datasets
            (tbl,domain,source_file,source_sheet,visibility,row_count,description,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (tbl, domain, "demo-seed", "demo", vis, len(rows), desc, NOW))
    c.commit()
    # the demo publishes out of the box: approve every seeded row through the gate
    from central import approvals
    approvals.ensure_approvals(c)
    for tbl, _, _, _, _, rows in DATASETS:
        approvals.approve_all(c, tbl)
    _seed_objects_and_season(c)
    from central.dbconn import local_path
    return str(local_path())


# --- demo objects + a demo season ------------------------------------------------
# Three published pieces (so Explore shows "Verify" and Seasons has something to
# enter), then "Pumpkin Patch 2026" with two approved entries, a few votes and one
# verified claim. Everything here is prefixed demo-/obj-demo- and deleted before
# it is re-inserted, so re-running the seed is idempotent.
DEMO_OBJECTS = [
    dict(row_id="obj-demo-0001", title="Harvest Pumpkin No. 4", maker="Rae Sutter", year="2026",
         colour=("orange", "#e8a44a"), uid="demo-maker-rae", studio="Blue Ridge Hot Glass",
         edition=12, price="$85 at the studio shop"),
    dict(row_id="obj-demo-0002", title="Blue Heirloom Ornament", maker="Theo Park", year="2025",
         colour=("teal", "#5ec8bd"), uid="demo-maker-theo", studio="Tidewater Glassworks",
         edition=20, price="$60, ships in the US"),
    dict(row_id="obj-demo-0003", title="Reticello Tumbler", maker="Ivo Marchetti", year="2024",
         colour=("violet", "#967ad2"), uid="demo-maker-ivo", studio="Murano Vetro Collettivo",
         edition=1, price=""),
]


def _demo_png_b64(hex_colour: str) -> str:
    """A 600×450 PNG: the object's colour pooling out of the dark ground."""
    import base64
    import io

    from PIL import Image, ImageDraw, ImageOps
    rgb = tuple(int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    grad = Image.radial_gradient("L").resize((600, 450))
    img = ImageOps.colorize(ImageOps.invert(grad), black=(10, 13, 17), white=rgb).convert("RGB")
    d = ImageDraw.Draw(img)
    d.ellipse((190, 95, 410, 355), outline=(238, 231, 219), width=3)
    d.ellipse((230, 135, 370, 315), outline=(238, 231, 219), width=1)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _seed_objects_and_season(c) -> None:
    import hashlib
    import json

    from central import approvals, seasons
    from glowtbook.contribute import ensure_central_objects, ensure_object_staging
    ensure_central_objects(c)
    ensure_object_staging(c)
    seasons.ensure(c)

    # --- wipe what we own ---------------------------------------------------
    old = c.execute("SELECT id FROM seasons WHERE slug=?", ("pumpkin-patch-2026",)).fetchone()
    if old:
        for t in ("season_votes", "season_claims", "season_entries", "season_badges"):
            c.execute(f"DELETE FROM {t} WHERE season_id=?", (old[0],))
        c.execute("DELETE FROM seasons WHERE id=?", (old[0],))
    c.execute("DELETE FROM season_claims WHERE object_row_id LIKE 'obj-demo-%' "
              "OR collector_uid LIKE 'demo-%'")
    c.execute("DELETE FROM season_entries WHERE object_row_id LIKE 'obj-demo-%'")
    c.execute("DELETE FROM season_badges WHERE uid LIKE 'demo-%'")
    c.execute("DELETE FROM object_images WHERE object_row_id LIKE 'obj-demo-%'")
    c.execute("DELETE FROM object_submissions WHERE content_hash LIKE 'demo-%'")
    c.execute("DELETE FROM objects WHERE _row_id LIKE 'obj-demo-%'")
    c.execute("DELETE FROM _approvals WHERE tbl='objects' AND row_id LIKE 'obj-demo-%'")

    # --- objects ----------------------------------------------------------------
    for o in DEMO_OBJECTS:
        chash = "demo-" + hashlib.sha1(o["row_id"].encode()).hexdigest()[:24]
        manifest = {
            "title": o["title"], "maker": o["maker"], "year": o["year"],
            "techniques": ["Offhand Blown Glass"], "demo": True,
            "fingerprint": {"rating": 82, "tier": "Strong",
                            "metadata": {"dominantColor": {"name": o["colour"][0],
                                                           "hex": o["colour"][1]},
                                         "hasEmbeddings": False},
                            "frames": [{"file": "frames/000.jpg", "dhash": [1, 2],
                                        "chist": [10] * 64, "sector": 0, "cell": "0:1"}]},
        }
        c.execute("""INSERT OR REPLACE INTO objects
            (_row_id,_source_file,_source_sheet,_imported_at,title,maker,year,techniques,materials,
             dimensions,description,contributor,sourcing,value_display,has_credentials,
             manifest_json,content_hash,published_at,fingerprint_json,fingerprint_rating,
             fingerprint_tier)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o["row_id"], "demo-seed", "demo", NOW, o["title"], o["maker"], o["year"],
             "Offhand Blown Glass", "Soda-lime glass", "",
             f"Demo piece: {o['title']} by {o['maker']} — invented for the seed.",
             o["maker"], "studio", "", 0, json.dumps(manifest), chash, NOW,
             json.dumps(manifest["fingerprint"]), 82, "Strong"))
        c.execute("INSERT INTO object_images (object_row_id, role, caption, image_b64) "
                  "VALUES (?,?,?,?)", (o["row_id"], "primary", o["title"],
                                       _demo_png_b64(o["colour"][1])))
        # the review-queue row that says who published it (ownership for Seasons)
        c.execute("""INSERT INTO object_submissions (content_hash, submitted_uid, submitted_by,
                     title, maker, year, techniques, status) VALUES (?,?,?,?,?,?,?,?)""",
                  (chash, o["uid"], o["maker"], o["title"], o["maker"], o["year"],
                   "Offhand Blown Glass", "approved"))
    approvals.set_status(c, "objects", [o["row_id"] for o in DEMO_OBJECTS], "approved",
                         reviewer="demo-seed")
    c.execute("UPDATE _datasets SET row_count=(SELECT COUNT(*) FROM objects), updated_at=? "
              "WHERE tbl='objects'", (NOW,))
    c.commit()

    # --- season ---------------------------------------------------------------
    sid = seasons.create_season(
        c, title="Pumpkin Patch 2026", slug="pumpkin-patch-2026",
        theme="Blown-glass pumpkins from every shop",
        description="October is pumpkin season in every hot shop we know. Enter yours, send "
                    "collectors to your shop, and let the crowd pick a favourite.",
        starts="2026-10-01", entries_close="2026-10-25", voting_closes="2026-10-31",
        ends="2026-11-02", votes_per_voter=3, status="open",
        rules="One entry per piece, and the piece must already be published through Glowtbook "
              "(fingerprinted, credentialed, approved). Editions are welcome — say how many. "
              "Collectors claim a piece by scanning it with Verify; a match of 70+ counts "
              "straight away, anything lower waits for the artist or an admin. Three votes "
              "per person, swap them as often as you like until voting closes.",
        prize_note="Bragging rights, a homepage feature, and the Crowd Favourite badge")
    entry_ids = []
    for o in DEMO_OBJECTS[:2]:
        eid = seasons.enter(c, sid, object_row_id=o["row_id"], artist_uid=o["uid"],
                            artist_name=o["maker"], title=o["title"], studio=o["studio"],
                            edition_size=o["edition"], price_note=o["price"])
        seasons.approve_entry(c, eid, True)
        entry_ids.append(eid)
    for voter, eid in (("demo-voter-1", entry_ids[0]), ("demo-voter-2", entry_ids[0]),
                       ("demo-voter-3", entry_ids[0]), ("demo-voter-1", entry_ids[1])):
        seasons.vote(c, sid, eid, voter)
    seasons.claim(c, object_row_id=DEMO_OBJECTS[0]["row_id"], collector_uid="demo-collector",
                  collector_name="Demo Collector", confidence=91,
                  report={"verdict": "Match likely", "views": 4, "demo": True},
                  location="Crozet, VA")
    c.commit()


if __name__ == "__main__":
    path = seed()
    print(f"Seeded demo database at {path}")
    print("Datasets: " + ", ".join(d[0] for d in DATASETS)
          + "  (one restricted, two with private columns).")
