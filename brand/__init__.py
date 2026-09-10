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
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT,WONK@0,9..144,100..900,0..100,0..1;1,9..144,100..900,0..100,0..1&family=Archivo:wght@100..900&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root {
  --fd-bg-top:#0A0D11; --fd-bg-base:#1A140F; --fd-rule:#24221F; --fd-rule-soft:#2A2824;
  --fd-amber:#E8A44A; --fd-teal:#5EC8BD; --fd-molten:#E25836; --fd-violet:#967AD2;
  --fd-bone:#EEE7DB; --fd-dim:#A8A29A; --fd-faint:#6E665C;
  --fd-serif:"Fraunces",Georgia,serif; --fd-sans:"Archivo",system-ui,sans-serif;
  --fd-mono:"IBM Plex Mono",ui-monospace,monospace;
}
/* ground */
.stApp { background-color:var(--fd-bg-top);
  background-image:
    radial-gradient(115% 62% at 22% 100%, rgba(232,164,74,.08) 0%, rgba(232,164,74,0) 60%),
    linear-gradient(180deg, var(--fd-bg-top) 0%, #12100E 60%, var(--fd-bg-base) 100%);
  background-attachment:fixed; }
[data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] { background:transparent; }
body, .stApp, .stMarkdown, p, li, label, [data-testid="stMarkdownContainer"] {
  font-family:var(--fd-sans); color:var(--fd-dim); }

/* headings — Fraunces, explicit axis order; weight 300 fallback if the font fails */
h1,h2,h3,h4,[data-testid="stHeading"] {
  font-family:var(--fd-serif) !important;
  font-variation-settings:"opsz" 44,"wght" 340,"SOFT" 0,"WONK" 0;
  font-weight:300 !important; color:var(--fd-bone) !important; letter-spacing:-.005em; }

/* NOTE: never hide header[data-testid="stHeader"] (mobile sidebar toggle lives there);
   never shrink block-container top padding (content would slide under the header). */
[data-testid="stSidebar"] { background:#0E0B09; border-right:1px solid var(--fd-rule); }
[data-testid="stSidebar"] * { color:var(--fd-dim); }

a, a:visited { color:var(--fd-amber); }
a:hover { color:var(--fd-teal); }
code, pre, [data-testid="stCode"] { font-family:var(--fd-mono); color:var(--fd-teal); }

/* buttons — mono pills; primary = amber, secondary = outline */
.stButton>button, .stDownloadButton>button, .stFormSubmitButton>button, [data-testid="stLinkButton"] a {
  border-radius:999px !important; font-family:var(--fd-mono) !important; font-weight:500 !important;
  text-transform:uppercase; letter-spacing:.08em; font-size:.8rem !important; }
.stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"],
.stFormSubmitButton>button[kind="primary"], [data-testid="stLinkButton"] a[kind="primary"] {
  background:var(--fd-amber) !important; border:0 !important; color:var(--fd-bg-top) !important; }
.stButton>button[kind="secondary"], [data-testid="stLinkButton"] a[kind="secondary"] {
  background:transparent !important; color:var(--fd-bone) !important; border:1px solid var(--fd-rule-soft) !important; }
.stButton>button:hover, [data-testid="stLinkButton"] a:hover { border-color:var(--fd-amber) !important; }

/* inputs */
input, textarea, [data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"]>div,
.stTextInput input, .stNumberInput input, .stDateInput input {
  background:rgba(238,231,219,.05) !important; color:var(--fd-bone) !important;
  border-color:var(--fd-rule-soft) !important; }
[data-baseweb="tab"] { font-family:var(--fd-mono); text-transform:uppercase; letter-spacing:.06em; font-size:.8rem; }
/* st.pills / segmented_control -> Field Data chips */
[data-testid="stPills"] button, [data-testid="stButtonGroup"] button {
  border-radius:999px !important; font-family:var(--fd-mono) !important; text-transform:uppercase;
  letter-spacing:.05em; font-size:.75rem !important; border:1px solid var(--fd-rule-soft) !important;
  color:var(--fd-dim) !important; background:transparent !important; }
[data-testid="stPills"] button[aria-checked="true"], [data-testid="stPills"] button[kind="primary"],
[data-testid="stButtonGroup"] button[aria-checked="true"] {
  background:var(--fd-amber) !important; color:var(--fd-bg-top) !important; border-color:var(--fd-amber) !important; }

/* captions / metrics */
[data-testid="stCaptionContainer"], .stCaption, small { color:var(--fd-faint) !important; }
[data-testid="stMetricValue"] { font-family:var(--fd-serif); color:var(--fd-bone); font-variation-settings:"opsz" 40,"wght" 380; }
[data-testid="stMetricLabel"] { font-family:var(--fd-mono); text-transform:uppercase; letter-spacing:.08em; color:var(--fd-faint); }
[data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stExpander"] {
  border:1px solid var(--fd-rule); border-radius:10px; }
[data-testid="stAlert"] { border-radius:10px; }

/* brand lockup + rule */
.gdb-brand { display:inline-flex; align-items:center; gap:.5rem; text-decoration:none;
  color:var(--fd-bone); font-family:var(--fd-mono); font-weight:500; letter-spacing:.14em;
  text-transform:uppercase; font-size:.95rem; margin:0 0 .5rem; }
.gdb-brand svg { width:22px; height:26px; }
.gdb-rule { height:1px; margin:.25rem 0 1.1rem;
  background:linear-gradient(90deg, var(--fd-amber), var(--fd-rule-soft) 45%, transparent);
  border-radius:2px; }
</style>
"""


def apply_theme(active: str = "") -> None:
    """Inject the shared theme and render the cross-app nav. `active` is one of
    'explore' | 'glowtbook' | 'admin' and gets the molten primary style.
    The glass mark doubles as the Home link."""
    st.html(_CSS)
    st.html(f'<a class="gdb-brand" href="/" target="_self">{_MARK}Glass Database</a>')
    cols = st.columns(len(_NAV))
    for col, (key, label, href) in zip(cols, _NAV):
        col.link_button(label, href, use_container_width=True,
                        type="primary" if key == active else "secondary")
    st.html('<div class="gdb-rule"></div>')


def _client_ip() -> str:
    """Best-effort client IP from the proxy headers (used only to derive a rotating
    daily hash — never stored)."""
    try:
        import streamlit as st
        h = st.context.headers
        return (h.get("X-Forwarded-For") or h.get("X-Real-Ip") or "").split(",")[0].strip()
    except Exception:
        return ""


def _dnt() -> bool:
    try:
        import streamlit as st
        return st.context.headers.get("DNT") == "1"
    except Exception:
        return False


def track(surface: str, view: str = "") -> None:
    """Log one view per session per (surface, view) change. No-op on Do-Not-Track."""
    try:
        import streamlit as st
        if _dnt():
            return
        key = f"{surface}:{view}"
        if st.session_state.get("_gdb_seen") == key:
            return
        st.session_state["_gdb_seen"] = key
        from central import analytics
        from central.dbconn import connect
        ip = _client_ip()
        analytics.log(connect(), surface, view, "view", ip=ip, country=analytics.country_for(ip))
    except Exception:
        pass
