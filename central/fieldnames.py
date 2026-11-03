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
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

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


# --- grid questions ----------------------------------------------------------------------
# A form's grid question lands as one column per row of the grid, labelled "Question [Row]"
# ("Hot Glass & Flameworking Techniques [Offhand Blown Glass]"), each holding the level the
# person ticked ("Primary Focus", "Secondary", "Occasional Use") or nothing. Sixteen columns
# all called "Techniques" are no use to anyone: each column is labelled by its row, and the
# rows of one question read as ONE field ("Techniques: Offhand Blown Glass (primary), Cane
# & Murrine (occasional)") and ONE filter whose choices are the rows.
_GRID = re.compile(r"^\s*(?P<q>.+?)\s*\[(?P<row>[^\]]+)\]\s*$", re.S)
_YES = {"yes", "y", "true", "x", "1", "✓", "✔", "checked", "on"}
_LEVEL_RANK = {"primary": 0, "secondary": 1, "occasional": 2}
_EMPTY = {"", "nan", "none", "null"}


def grid_parts(label: str) -> tuple[str, str] | None:
    """("Hot Glass & Flameworking Techniques", "Offhand Blown Glass") for a grid column's label."""
    m = _GRID.match(label or "")
    return (m.group("q").strip(), m.group("row").strip()) if m else None


def group_label(question: str) -> str:
    """The one word for a grid question: every technique grid is "Techniques"; otherwise the
    question's own noun."""
    return "Techniques" if "technique" in (question or "").lower() else clean_label("", question)


def level_word(value) -> str:
    """"Primary Focus" → "primary", "Occasional Use" → "occasional"; a bare tick → ""."""
    v = re.sub(r"\s+", " ", str(value if value is not None else "")).strip().lower()
    if v in _YES:
        return ""
    for suffix in (" focus", " use", " technique"):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
    return v


def is_blank(value) -> bool:
    return str(value if value is not None else "").strip().lower() in _EMPTY


def grid_summary(row: dict, members: list[tuple[str, str]]) -> str:
    """One line for a grid's ticked rows, primary first: "Offhand Blown Glass (primary), Cane
    & Murrine (occasional)"."""
    items = []
    for col, label in members:
        v = row.get(col)
        if is_blank(v):
            continue
        lvl = level_word(v)
        items.append((_LEVEL_RANK.get(lvl.split()[0] if lvl else "", 9), label.lower(),
                      f"{label} ({lvl})" if lvl else label))
    return ", ".join(t for _, _, t in sorted(items))


def unique_names(names: Iterable[str]) -> list[str]:
    """The same names, a repeat numbered: "Techniques", "Techniques (2)", "Techniques (3)"."""
    out, seen = [], Counter()
    for n in names:
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


@dataclass
class Labelled:
    """A table's public columns, labelled for people: ``labels`` maps each column to a unique
    noun (a grid column to its row), ``groups`` maps a grid's label to its (column, row)
    members in form order."""
    labels: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @property
    def grid_cols(self) -> set[str]:
        return {c for members in self.groups.values() for c, _ in members}


def labelled_columns(cols: Iterable[tuple[str, str]]) -> Labelled:
    """Labels and grid groups for ``[(column, raw label), …]``. A label that would repeat
    takes the column's own words, then a number, so a table never has two columns with
    one name (the explorer's table and download need that)."""
    out = Labelled()
    raw: dict[str, str] = {}
    for col, label in cols:
        raw[col] = label or ""
        parts = grid_parts(label or "")
        if parts:
            question, row = parts
            row = re.sub(r"\s+", " ", row).strip(" \t:-–—,.;/?!'\"&")
            out.labels[col] = (row[:1].upper() + row[1:]) if row else col   # the row names the column; its parentheses stay
            out.groups.setdefault(group_label(question), []).append((col, out.labels[col]))
        else:
            out.labels[col] = clean_label(col, label or "")
    counts = Counter(out.labels.values())
    taken = set(out.labels.values())
    kept: set[str] = set()
    for col, lab in list(out.labels.items()):
        if counts[lab] < 2 or lab not in kept:               # the first column keeps the label
            kept.add(lab)
            continue
        words = _noun((col or "").replace("_", " "))
        if words and words != lab and words not in taken:
            out.labels[col] = words
            taken.add(words)
    fixed = dict(zip(out.labels, unique_names(out.labels.values())))
    out.labels = fixed
    out.groups = {g: [(c, fixed[c]) for c, _ in members] for g, members in out.groups.items()}
    return out
