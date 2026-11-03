"""
central.links
=============
The site's deep links, in one place, so a URL's shape changes once. The explorer reads
these query strings (``explore/app.py``): ``?view=datasets&q=&table=`` opens the Directory
on a search in a table, ``?view=objects&piece=`` opens the Pieces view on one piece and
``?view=objects&maker=`` on everything by one maker. A record's permanent address is
``/record/<gid>``.
"""
from __future__ import annotations

from urllib.parse import quote


def record_url(gid: str, base: str = "") -> str:
    return f"{base}/record/{gid}"


def directory_url(q: str, table: str = "", base: str = "") -> str:
    url = f"{base}/explore/?view=datasets&q={quote(q or '')}"
    return url + (f"&table={quote(table)}" if table else "")


def piece_url(row_id, base: str = "") -> str:
    return f"{base}/explore/?view=objects&piece={quote(str(row_id), safe='')}"


def maker_pieces_url(maker: str, base: str = "") -> str:
    return f"{base}/explore/?view=objects&maker={quote(maker or '')}"
