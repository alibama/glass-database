"""
central.fieldnames
==================
What a column *is*, by its name and its label, for every surface that shows a table's rows:
the Directory (``explore/``), the record pages (``central/records.py``) and the read API.
Pure functions, no database, so each surface applies the same rule.

* ``is_excluded``: coordinates and identifiers (the map uses them; nothing else should).
* ``is_person_name`` / ``is_dob`` / ``is_demographic``: the columns that are about a person
  rather than about their work. Dates of birth and demographics (ethnicity, gender, age …)
  never appear per row anywhere public, and never become a filter; the explorer may still
  count them in its aggregate charts, which say something about the field without pointing
  at anyone.
* ``clean_label``: the words on the page. A table ingested from a form carries the form's
  questions as labels ("What's your Ethnicity?", "Birth Year (What year were you born?)").
  A directory filters to find someone, so a label has to read as a noun, not as a question
  put to the reader.
"""
from __future__ import annotations

import re

GEO_COLS = {"lat", "lng", "latitude", "longitude"}
ID_HINTS = ("_id", "timestamp", "geo_precision")

_NAME_EXACT = {"artist_name", "artist", "display_name", "full_name", "first_name", "last_name",
               "led_by", "student_name", "mentor_name", "contact_name", "person", "founder"}
_NAME_PREFIX = ("artist", "person", "maker", "student", "mentor", "contact", "instructor", "teacher")
_DOB = re.compile(r"(^|_)(birth|dob|born|death|died)(_|$)|birth_year|death_year|date_of_birth|year_of_birth")
# "where were you born and where do you reside" is a place, not a date of birth
_PLACE_WORDS = re.compile(r"reside|where_|base|nationality|location|country|city|town|from_")
_DEMOGRAPHIC = re.compile(r"ethnic|gender|pronoun|\brace\b|religio|disab|sexual|orientation|income|salary|"
                          r"marital|citizen|immigra|veteran|\bage\b|how old|years old", re.I)


def is_excluded(col: str) -> bool:
    c = (col or "").lower()
    return c in GEO_COLS or c == "id" or any(h in c for h in ID_HINTS)


def is_person_name(col: str) -> bool:
    c = (col or "").lower()
    return c in _NAME_EXACT or (c.endswith("_name") and c.startswith(_NAME_PREFIX))


def is_dob(col: str) -> bool:
    c = (col or "").lower()
    if not _DOB.search(c):
        return False
    return not _PLACE_WORDS.search(c)                 # "born and where you reside" is a base, kept


def is_demographic(col: str, label: str = "") -> bool:
    """Ethnicity, gender, age and the like: never per row, never a filter."""
    return bool(_DEMOGRAPHIC.search((col or "").replace("_", " ")) or _DEMOGRAPHIC.search(label or ""))


# --- the words on the page ------------------------------------------------------------
_Q_WORDS = ("what|where|which|who|whom|whose|when|why|how|do|does|did|are|is|were|was|have|has|had|"
            "can|could|would|should|will|please|tell|list|describe")
_Q_START = re.compile(rf"\b(?:{_Q_WORDS})\b", re.I)
_YOU = re.compile(r"\byou(?:r|rs|rself)?\b|\bmy\b|\bme\b", re.I)
_TRIM = " \t:-–—,.;/()?!'\"&"
# when the label is only a question, the noun comes from the thing it asks about
_KNOWN = (
    ("study or work under", "Studied or worked under"), ("work under", "Worked under"),
    ("studied", "Studied under"), ("study", "Studied under"), ("mentor", "Mentors"),
    ("primary focus", "Primary focus"), ("discipline", "Primary focus"), ("technique", "Techniques"),
    ("medium", "Technique / medium"), ("based", "Based in"), ("reside", "Based in"),
    ("nationality", "Nationality / base"), ("birth", "Birth year"), ("born", "Birth year"),
    ("death", "Year of death"), ("alive", "Year of death"), ("website", "Website"),
    ("instagram", "Instagram"), ("social", "Website"), ("email", "Email"), ("phone", "Phone"),
    ("ethnic", "Ethnicity"), ("gender", "Gender"), ("pronoun", "Pronouns"), ("role", "Roles"),
    ("training", "Training"), ("education", "Training"), ("degree", "Training"),
    ("inspiration", "Inspiration"), ("award", "Awards"), ("honor", "Awards"), ("honour", "Awards"),
    ("collection", "Collections"), ("highlight", "Career highlights"), ("proud", "Career highlights"),
    ("teach", "Teaching"), ("class", "Classes"), ("workshop", "Workshops"), ("exhibit", "Exhibitions"),
    ("gallery", "Galleries"), ("residenc", "Residencies"), ("commission", "Commissions"),
    ("studio", "Studio"), ("status", "Status"), ("year", "Years"), ("name", "Name"),
)
_STOP = {"what", "s", "is", "are", "your", "you", "do", "did", "does", "where", "were", "was", "how", "who",
         "which", "when", "why", "have", "has", "a", "an", "the", "of", "in", "to", "be", "used", "still",
         "currently", "please", "any", "or", "and"}


def _noun(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip(_TRIM)
    return s[:1].upper() + s[1:] if s else ""


def clean_label(col: str, label: str = "") -> str:
    """A short noun for a column: the words before any question, without the parenthetical
    explanation, the first line only. A label that is nothing but a question ("What's your
    Ethnicity?") becomes the thing it asks about."""
    if not (label or "").strip():
        return _noun((col or "").replace("_", " ")) or (col or "")
    s = label.strip().split("\n")[0]
    s = re.sub(r"\([^)]*\)?", "", s)                    # parentheticals, even one left open
    if "?" in s:
        head = s.split("?", 1)[0]
        m = _Q_START.search(head)
        if m and m.start() > 0 and not _YOU.search(head[:m.start()]):
            head = head[:m.start()]                     # "Primary Focus What is your…" → "Primary Focus"
        s = head
    s = re.split(r"(?<=[.!;])\s", s, 1)[0]              # the first sentence
    s = _noun(s)
    if s and len(s) <= 48 and not _YOU.search(s) and not _Q_START.match(s):
        return s
    hay = f"{s.lower()} {(col or '').replace('_', ' ').lower()}"
    for word, noun in _KNOWN:
        if word in hay:
            return noun
    words = [w for w in re.split(r"[\s_]+", (col or "").lower()) if w and w not in _STOP]
    return _noun(" ".join(words)) or s or (col or "")
