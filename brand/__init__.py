"""
brand
=====
One shared look and a cross-app nav for the three Streamlit surfaces (Explore,
Glowtbook, Admin), so they read as one product and match the homepage: the
furnace/molten palette, Fraunces headings, the glass mark, and a row of buttons
that jump between Home, Explore, Glowtbook, and Admin.

The nav uses native `st.link_button`s (not HTML anchors) so the whole button is
clickable, and the active page is shown with the molten "primary" style.

Usage — right after st.set_page_config():

    from brand import apply_theme
    apply_theme("explore")   # or "glowtbook" / "admin"
"""
from __future__ import annotations

import streamlit as st

# a compact molten-gather mark (inline so it needs no served asset)
_MARK = (
    '<svg viewBox="0 0 40 46" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<defs><linearGradient id="gm" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#fde68a"/><stop offset="1" stop-color="#ea580c"/>'
    '</linearGradient></defs>'
    '<rect x="18" y="2" width="4" height="23" rx="2" fill="#cbd5e1"/>'
    '<ellipse cx="20" cy="31" rx="13" ry="14" fill="url(#gm)"/>'
    '<ellipse cx="15" cy="27" rx="3.4" ry="5" fill="#fff7ed" opacity="0.5"/></svg>'
)

# key, label, href  (the brand mark doubles as Home)
_NAV = [
    ("explore", "Explore", "/explore/"),
    ("glowtbook", "Glowtbook", "/glowtbook/"),
    ("admin", "Admin", "/admin/"),
]

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,900&display=swap');
:root {
  --molten:#fb923c; --molten-deep:#ea580c; --ember:#f59e0b;
  --purple:#7c3aed; --furnace:#1c1222; --furnace-2:#2a1a30; --ink:#23202a;
}
h1, h2, h3, h4, [data-testid="stHeading"] {
  font-family:'Fraunces', Georgia, serif !important; letter-spacing:-.01em; }
/* NOTE: do NOT hide header[data-testid="stHeader"] — on mobile it holds the
   control that opens the sidebar. And do NOT shrink the block-container top
   padding: Streamlit uses it to clear the fixed header, so reducing it slides
   content (the nav) under the header, where it can't be clicked. */

/* brand lockup */
.gdb-brand { display:inline-flex; align-items:center; gap:.5rem; text-decoration:none;
  color:var(--ink); font-family:'Fraunces', Georgia, serif; font-weight:900; font-size:1.35rem; }
.gdb-brand svg { width:26px; height:30px; }
.gdb-rule { height:3px; margin:.25rem 0 1.1rem;
  background:linear-gradient(90deg, var(--ember), var(--molten-deep) 45%, transparent);
  border-radius:2px; }

/* nav + form primary buttons: molten pill */
[data-testid="stLinkButton"] a { border-radius:999px !important; font-weight:600 !important; }
.stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"],
.stFormSubmitButton>button[kind="primary"] {
  background:linear-gradient(180deg, var(--ember), var(--molten-deep));
  border:0; color:#2a1400; font-weight:600; }
a { color:var(--molten-deep); }
</style>
"""


def apply_theme(active: str = "") -> None:
    """Inject the shared theme and render the cross-app nav. `active` is one of
    'explore' | 'glowtbook' | 'admin' and gets the molten primary style.
    The glass mark doubles as the Home link."""
    st.html(_CSS)
    brand, spacer, *btns = st.columns([2.4, 0.4, 1, 1.15, 1], vertical_alignment="center")
    brand.html(f'<a class="gdb-brand" href="/" target="_self">{_MARK}Glass Database</a>')
    for col, (key, label, href) in zip(btns, _NAV):
        col.link_button(label, href, use_container_width=True,
                        type="primary" if key == active else "secondary")
    st.html('<div class="gdb-rule"></div>')
