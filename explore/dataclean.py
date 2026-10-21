"""
explore.dataclean
==================
Pure, UI-free helpers for the explorer: deciding which columns are worth
charting/filtering, and trimming numeric series so histograms don't render giant
empty gaps. Kept separate from app.py so it imports without Streamlit and can be
unit-tested directly.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

# Columns that are coordinates or identifiers: useful for the map, never for
# grouping/filtering/distribution.
GEO_COLS = {"lat", "lng", "latitude", "longitude"}
ID_HINTS = ("_id", "timestamp", "geo_precision")


def is_excluded(col: str) -> bool:
    c = col.lower()
    return c in GEO_COLS or c == "id" or any(h in c for h in ID_HINTS)


def classify(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Split columns into good-to-group-by categoricals and chart-worthy numerics.
    Coordinates and identifiers are excluded — lat/lng belong on the map only."""
    cats, nums = [], []
    n = max(len(df), 1)
    for col in df.columns:
        if is_excluded(col):
            continue
        s = df[col].replace("", pd.NA).dropna()
        if s.empty:
            continue
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().mean() > 0.6 and num.nunique() > 3:
            nums.append(col)
            continue
        nd = s.nunique()
        if 2 <= nd <= 120 and nd < 0.9 * n:
            cats.append(col)
    return cats, nums


def clean_numeric(raw: pd.Series):
    """Coerce to numbers and trim outliers so histograms don't render giant empty
    gaps. Year-like columns are clamped to a plausible window and binned by decade;
    everything else is clipped to the 2nd–98th percentile. Returns (series, is_year)."""
    s = pd.to_numeric(raw.replace("", pd.NA), errors="coerce").dropna()
    if s.empty:
        return s, False
    is_year = s.between(1000, 2100).mean() > 0.7
    if is_year:
        s = s[s.between(1850, date.today().year)]      # plausible birth years here
    else:
        lo, hi = s.quantile(0.02), s.quantile(0.98)
        if hi > lo:
            s = s[(s >= lo) & (s <= hi)]
    return s, is_year


# --- per-person privacy in the public tables ---------------------------------
# Names and dates of birth are kept OUT of the row view and the CSV download in
# Explore; they may still feed the aggregate charts (a decade histogram of birth
# years says something about the field without pointing at anyone). Website
# links stay, so a reader can still reach a maker on their own terms.
_NAME_EXACT = {"artist_name", "artist", "display_name", "full_name", "first_name", "last_name",
               "led_by", "student_name", "mentor_name", "contact_name", "person", "founder"}
_NAME_PREFIX = ("artist", "person", "maker", "student", "mentor", "contact", "instructor", "teacher")
_DOB = re.compile(r"(^|_)(birth|dob|born|death)(_|$)|birth_year|death_year|date_of_birth|year_of_birth")


def is_person_name(col: str) -> bool:
    c = (col or "").lower()
    return c in _NAME_EXACT or (c.endswith("_name") and c.startswith(_NAME_PREFIX))


def is_dob(col: str) -> bool:
    return bool(_DOB.search((col or "").lower()))


def person_identifying(cols) -> tuple[list[str], list[str]]:
    """(name columns, date-of-birth columns) among `cols` — both withheld per row;
    only the DOB ones are allowed into aggregate charts."""
    cols = list(cols)
    return [c for c in cols if is_person_name(c)], [c for c in cols if is_dob(c)]
