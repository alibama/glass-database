"""
explore.pieces
==============
The Pieces view's pure helpers: which rows of ``objects`` match a search, a maker or one
piece's link, and the makers worth a filter. UI-free so it imports without Streamlit and is
unit-tested; the app slices the result into pages and builds the heavy card data (images,
credentials) for the page shown only.
"""
from __future__ import annotations

import re
from collections import Counter

SEARCHED = ("title", "maker", "year", "techniques", "materials", "description", "contributor")


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def makers_of(rows) -> list[str]:
    """Distinct makers, most pieces first, then by name; one spelling per maker."""
    counts: Counter = Counter()
    spelling: dict[str, str] = {}
    for r in rows:
        name = str(r.get("maker") or "").strip()
        if not name:
            continue
        key = norm(name)
        counts[key] += 1
        spelling.setdefault(key, name)
    return [spelling[k] for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def filter_pieces(rows, q: str = "", maker: str = "", piece: str = "") -> list[dict]:
    """The rows that match: ``piece`` is a row id (one piece, by its link), ``maker`` an exact
    maker name (case-insensitive), ``q`` a substring of any searched column."""
    out = []
    piece, maker, q = str(piece or "").strip(), norm(maker), norm(q)
    for r in rows:
        if piece and str(r.get("_row_id") or r.get("id") or "") != piece:
            continue
        if maker and norm(r.get("maker")) != maker:
            continue
        if q and not any(q in norm(r.get(c)) for c in SEARCHED):
            continue
        out.append(r)
    return out
