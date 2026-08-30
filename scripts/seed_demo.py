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
    from central.dbconn import local_path
    return str(local_path())


if __name__ == "__main__":
    path = seed()
    print(f"Seeded demo database at {path}")
    print("Datasets: " + ", ".join(d[0] for d in DATASETS)
          + "  (one restricted, two with private columns).")
