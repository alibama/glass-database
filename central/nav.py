"""
central.nav
===========
The ONE definition of the site menu. Every surface renders this list and nothing
else, so the header never differs between the static pages, the blog and the
Streamlit apps:

* static pages carry an empty ``<ul id="menu" data-nav data-active="<key>">`` and
  ``deploy/site.js`` fills it from its own copy of this list;
* the blog generator (``scripts/build_blog.py``) emits that same empty ``<ul>``;
* the Streamlit ``brand`` package renders it from this module.

``tests/test_nav.py`` asserts the JavaScript copy equals this one and that no page
still hard-codes menu items, so the two cannot drift. Change the menu here and in
``site.js`` (the test tells you if you forget one); words come from docs/NAMING.md.
"""
from __future__ import annotations

import html

# (key, label, href) — keys are stable handles for "which item is active"
NAV: list[tuple[str, str, str]] = [
    ("explore", "Find Artists & Studios", "/explore/"),
    ("games", "Collecting Events", "/games.html"),
    ("glassid", "Glass ID", "/fingerprint/enroll.html"),
    ("glowtbook", "Glowtbook", "/glowtbook/"),
    ("notes", "Field notes", "/blog/"),
    ("about", "About", "/#about"),
]

TAGLINE = "A home for every piece of glass and the people who make it."
BRAND_HTML = "Glass <i>Database</i>"


def items(active: str = "") -> list[dict]:
    return [{"key": k, "label": label, "href": href, "active": k == active} for k, label, href in NAV]


def menu_html(active: str = "", extra: list[tuple[str, str, str]] | None = None) -> str:
    """The <li> items of the primary menu, the active one marked class="on"."""
    out = []
    for k, label, href in NAV + list(extra or []):
        on = ' class="on"' if k == active else ""
        out.append(f'<li><a href="{html.escape(href, quote=True)}"{on}>{html.escape(label)}</a></li>')
    return "".join(out)


def menu_placeholder(active: str = "") -> str:
    """What a static page carries; site.js fills it on load."""
    return f'<ul id="menu" data-nav data-active="{html.escape(active, quote=True)}"></ul>'
