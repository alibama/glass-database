"""The site menu has ONE definition (central/nav.py). This keeps every copy equal to it."""
import html
import json
import re
from pathlib import Path

from central import nav

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def test_site_js_menu_equals_central_nav():
    js = (DEPLOY / "site.js").read_text(encoding="utf-8")
    m = re.search(r"var NAV = (\[\[.*?\]\]);", js)
    assert m, "site.js has no NAV array"
    assert [tuple(x) for x in json.loads(m.group(1))] == nav.NAV
    assert "function menu(" in js and 'ul#menu[data-nav]' in js


def test_static_pages_carry_the_placeholder_not_items():
    pages = sorted(p for p in DEPLOY.glob("*.html"))
    assert pages
    for p in pages:
        s = p.read_text(encoding="utf-8")
        assert re.search(r'<ul id="menu" data-nav data-active="[a-z]*"( data-nav-[a-z-]+="[a-z,]*")*></ul>', s), p.name
        assert '<ul id="menu">' not in s, f"{p.name} hard-codes menu items"
        assert '<small class="tag">' not in s, f"{p.name} still has the header tagline"
    for p in (DEPLOY / "blog").glob("*.html"):
        s = p.read_text(encoding="utf-8")
        assert 'data-nav data-active="notes"' in s, p.name


def test_menu_html_and_placeholder():
    h = nav.menu_html("games")
    labels = [html.unescape(x) for x in re.findall(r">([^<]+)</a>", h)]
    assert labels == [label for _, label, _ in nav.NAV]
    assert '<a href="/games.html" class="on">' in h
    assert nav.menu_placeholder("notes") == '<ul id="menu" data-nav data-active="notes"></ul>'
    assert "Admin" in nav.menu_html("admin", [("admin", "Admin", "/admin/")])


def test_brand_uses_central_nav():
    src = (ROOT / "brand" / "__init__.py").read_text(encoding="utf-8")
    assert "from central import nav" in src and "menu_html(" in src
    assert "gdb-tagline" not in src


def test_home_page_menu_is_spartan():
    """The front page shows Find Artists & Studios and the sign-in; Glowtbook and My account
    appear once signed in. site.js honours the two data-nav-* attributes; other pages are untouched."""
    home = (DEPLOY / "landing-index.html").read_text(encoding="utf-8")
    assert 'data-nav-show="explore" data-nav-signed-in="glowtbook,account"' in home
    js = (DEPLOY / "site.js").read_text(encoding="utf-8")
    assert 'data-nav-show' in js and 'data-nav-signed-in' in js
    for key in ("explore", "glowtbook", "account"):
        assert key in [k for k, _, _ in nav.NAV]
    others = [p for p in DEPLOY.glob("*.html") if p.name != "landing-index.html"]
    assert others and all("data-nav-show" not in p.read_text(encoding="utf-8") for p in others)
