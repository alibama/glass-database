"""
central.manifest
================
The single place that decides WHAT gets ingested and HOW it's exposed.

Each DATASET maps one worksheet -> one table, tagged with:
  * domain     — for grouping in the API
  * visibility — "public" (served by the API) or "restricted" (managed but not
                 exposed without an admin token)
  * key_columns— the natural key used to build a stable row id so re-importing
                 an edited sheet UPDATES rows instead of duplicating them
                 (falls back to a content hash when no key is present)

Spreadsheet machinery (Summary, Mapping, Rules, Dashboard, Lookup, Read Me,
Geocode Queue, empty Intake, exact duplicates) is intentionally NOT ingested;
those are listed in SKIP with a reason so the choice is transparent.

Column-level privacy is handled by PRIVATE_COLUMN_PATTERNS: any column whose
slug matches is withheld from public API responses even inside a public table,
so emails, phones, claim tokens and internal notes never leak. This mirrors the
Removal & Correction Policy's contact-removal promise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    table: str
    source_file: str
    sheet: str
    domain: str
    visibility: str = "public"          # public | restricted
    key_columns: tuple[str, ...] = ()   # ORIGINAL header names (pre-slug)
    description: str = ""


# --- What we ingest ---------------------------------------------------------
DATASETS: list[Dataset] = [
    # Artists
    Dataset("artists", "Artists_Working_with_Glass.xlsx", "Publicly Listed", "artists",
            "public", ("Timestamp", "Artist Name"),
            "Current artist registry (public intake form)."),
    Dataset("artists_legacy", "Artists_Working_with_Glass.xlsx", "Artists working with glass (OG)", "artists",
            "public", ("Artist Name",), "Legacy curated artist list."),
    # Studios & programs (the map data)
    Dataset("studios", "V4_Glass_Studios_Worldwide.xlsx", "PUBLIC_Studios", "studios",
            "public", ("id",), "Public studios directory (curated, geocoded)."),
    Dataset("programs", "V4_Glass_Studios_Worldwide.xlsx", "PUBLIC_Programs", "programs",
            "public", ("id",), "Public education programs (curated, geocoded)."),
    Dataset("studios_internal", "V4_Glass_Studios_Worldwide.xlsx", "Studios", "studios",
            "restricted", ("id",), "Full studios table incl. internal fields (claim tokens, notes)."),
    Dataset("programs_internal", "V4_Glass_Studios_Worldwide.xlsx", "Programs", "programs",
            "restricted", ("id",), "Full programs table incl. internal fields."),
    Dataset("studios_directory_legacy", "V4_Glass_Studios_Worldwide.xlsx", "Studios Directory (OG)", "studios",
            "public", ("Studio Name", "City"), "Legacy studios directory."),
    Dataset("studio_intake", "V4_Glass_Studios_Worldwide.xlsx", "Intake 2", "intake",
            "restricted", ("Timestamp", "Studio Name"), "Raw studio submissions (contains contact info)."),
    # Education & opportunities
    Dataset("university_programs", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "University Programs", "education", "public", ("Institution", "Program / Department"),
            "University glass programs with cost estimates."),
    Dataset("funding", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "Fieldwide Funding", "education", "public", ("Funding Source", "Type"),
            "Field-wide funding sources."),
    Dataset("opportunities", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "Opportunities (Publicly Listed)", "opportunities", "public", ("Timestamp", "Opportunity Title"),
            "Residencies, fellowships, grants (public intake)."),
    Dataset("opportunities_legacy", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "Opportunities (OG)", "opportunities", "public", ("Opportunity",),
            "Legacy curated opportunities."),
    Dataset("resources", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "Resources (Publicly Listed)", "resources", "public", ("Timestamp", "Name"),
            "Community resources (public intake)."),
    Dataset("resources_legacy", "Opportunities_and_Education_in_Glass_as_a_Field_of_Study__residencies__fellowships__apprenticeships__grants__schools__resources_.xlsx",
            "Resources (OG)", "resources", "public", ("Name",), "Legacy curated resources."),
    # Museum
    Dataset("museum_artists", "Museum_Collected_Glass_Artists_.xlsx", "Glass Artists by Museum", "museum",
            "public", ("Museum", "Artist"), "Artists held in museum collections."),
    Dataset("rakow_commissions", "Museum_Collected_Glass_Artists_.xlsx", "Rakow Commissions 1986-2025", "museum",
            "public", ("Year", "Artist"), "Corning Rakow Commission recipients."),
    # Events & open calls
    Dataset("events", "Glass_Shows__Events___Open_Calls.xlsx", "Shows & Events", "events",
            "public", ("Timestamp", "Event/show name"), "Shows and events."),
    Dataset("open_calls", "Glass_Shows__Events___Open_Calls.xlsx", "Open Calls", "events",
            "public", ("Timestamp", "Show/Event/Exhibition Name"), "Open calls for entry."),
    # Trade shows
    Dataset("trade_craft_retail", "Trade_Shows__retail__wholsale__craft__fine_art_.xlsx", "Craft Retail & Gift", "trade",
            "public", ("Show", "City"), "Craft/retail/gift trade shows."),
    Dataset("trade_wholesale", "Trade_Shows__retail__wholsale__craft__fine_art_.xlsx", "Wholesale", "trade",
            "public", ("Show", "City"), "Wholesale trade shows."),
    Dataset("trade_interior_arch", "Trade_Shows__retail__wholsale__craft__fine_art_.xlsx", "Interior Design & Arch", "trade",
            "public", ("Show", "City"), "Interior design & architecture shows."),
    Dataset("art_fairs", "Trade_Shows__retail__wholsale__craft__fine_art_.xlsx", "Art Fairs", "trade",
            "public", ("Fair", "City"), "Fine-art fairs."),
    # Feedback
    Dataset("comments", "COMMENTS___CONCERNS__Responses_.xlsx", "Comment and Concerns ", "feedback",
            "restricted", ("Timestamp",), "Community comments & concerns (may contain contact info)."),
]

# --- Deliberately skipped (documented for transparency) --------------------
SKIP = {
    ("Artists_Working_with_Glass.xlsx", "Summary"): "computed summary",
    ("Artists_Working_with_Glass.xlsx", "Mapping"): "helper lookup",
    ("Artists_Working_with_Glass.xlsx", "Rules"): "helper lookup",
    ("Museum_Collected_Glass_Artists_.xlsx", "Tacoma Residency Note"): "prose note",
    ("Museum_Collected_Glass_Artists_.xlsx", "Read Me"): "prose note",
    ("V4_Glass_Studios_Worldwide.xlsx", "Lookup"): "helper lookup",
    ("V4_Glass_Studios_Worldwide.xlsx", "Dashboard"): "live formulas",
    ("V4_Glass_Studios_Worldwide.xlsx", "Geocode Queue"): "work queue",
    ("V4_Glass_Studios_Worldwide.xlsx", "Intake"): "empty",
    ("V4_Glass_Studios_Worldwide.xlsx", "Read me"): "prose note",
    ("V4_Glass_Studios_Worldwide.xlsx", "Summary (OG)"): "computed summary",
    ("V4_Glass_Studios_Worldwide.xlsx", "Copy of University Programs"): "exact duplicate",
}

# --- Column privacy ---------------------------------------------------------
# Slugs matching any of these are withheld from public API output, even in a
# public table. (Timestamps and provenance columns stay public on purpose.)
PRIVATE_COLUMN_PATTERNS = [
    r"email", r"e_mail", r"phone", r"contact", r"claim_token",
    r"internal_notes", r"street_address", r"^address$", r"^source$",
]

# Columns that should be typed REAL (for map/sort) rather than TEXT.
REAL_COLUMNS = {"lat", "lng", "latitude", "longitude"}

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(header: str, used: set[str] | None = None) -> str:
    s = _slug_re.sub("_", (header or "").strip().lower()).strip("_")
    if not s:
        s = "col"
    if s[0].isdigit():
        s = "c_" + s
    s = s[:48]
    if used is not None:
        base, i = s, 2
        while s in used:
            s = f"{base}_{i}"; i += 1
        used.add(s)
    return s


def is_private_column(slug: str) -> bool:
    return any(re.search(p, slug) for p in PRIVATE_COLUMN_PATTERNS)
