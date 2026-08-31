"""
brand
=====
One shared look and a cross-app nav bar for the three Streamlit surfaces
(Explore, Glowtbook, Admin), so they read as one product and match the homepage:
the furnace/molten palette, Fraunces headings, the glass mark, and pills that
jump between Home, Explore, Glowtbook, and Admin.

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

_NAV_ITEMS = [
    ("home", "Home", "/"),
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
header[data-testid="stHeader"] { background:transparent; }
.block-container { padding-top:1.1rem; }
a { color:var(--molten-deep); }
.stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"],
.stFormSubmitButton>button[kind="primary"] {
  background:linear-gradient(180deg, var(--ember), var(--molten-deep));
  border:0; color:#2a1400; font-weight:600; }
.stButton>button[kind="primary"]:hover, .stFormSubmitButton>button[kind="primary"]:hover {
  filter:brightness(1.06); color:#2a1400; }

/* shared nav bar */
.gdb-nav { display:flex; align-items:center; justify-content:space-between; gap:1rem;
  flex-wrap:wrap; background:linear-gradient(180deg, var(--furnace), var(--furnace-2));
  color:#f6eef2; border-radius:14px; padding:.55rem .9rem; margin:0 0 1.3rem; }
.gdb-nav .brand { display:flex; align-items:center; gap:.5rem; text-decoration:none;
  color:#fff; font-family:'Fraunces', serif; font-weight:900; font-size:1.12rem; }
.gdb-nav .brand svg { width:26px; height:29px; }
.gdb-nav .links { display:flex; gap:.25rem; flex-wrap:wrap; }
.gdb-nav .links a { color:#e9dfee; text-decoration:none; padding:.34rem .82rem;
  border-radius:999px; font-weight:600; font-size:.94rem; }
.gdb-nav .links a:hover { background:rgba(255,255,255,.10); }
.gdb-nav .links a.active { background:linear-gradient(180deg, var(--ember), var(--molten-deep));
  color:#2a1400; }
.gdb-nav .links a.glowtbook.active {
  background:linear-gradient(180deg, #a78bfa, var(--purple)); color:#fff; }
</style>
"""


def apply_theme(active: str = "") -> None:
    """Inject the shared theme and render the cross-app nav bar. `active` is one of
    'home' | 'explore' | 'glowtbook' | 'admin' and highlights that pill."""
    links = "".join(
        f'<a class="{key} {"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in _NAV_ITEMS
    )
    st.markdown(
        _CSS
        + f'<div class="gdb-nav"><a class="brand" href="/">{_MARK}Glass Database</a>'
        + f'<div class="links">{links}</div></div>',
        unsafe_allow_html=True,
    )
