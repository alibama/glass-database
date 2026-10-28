"""
brand
=====
One shared look and a cross-app nav for the three Streamlit surfaces (Explore,
Glowtbook, Admin), so they read as one product and match the homepage: the
paper palette (warm off-white ground, ink text, a single red accent), Fraunces
headings, Inter body, Plex Mono labels, and a row of square buttons that jump
between Home, Find Artists & Studios, Collecting Events, Glowtbook, and Admin.
Names follow docs/NAMING.md; the keys ('explore', 'glowtbook', 'admin') and the
URLs keep the old words.

The nav uses native `st.link_button`s (not HTML anchors) so the whole button is
clickable; the active page is red-filled, the others are ink-outlined.

Usage — right after st.set_page_config():

    from brand import apply_theme
    apply_theme("explore")   # or "glowtbook" / "admin"
"""
from __future__ import annotations

import streamlit as st

# text lockup matching the homepage `.brand` (Fraunces, the italic word in red)
_MARK = 'Glass <i>Database</i>'
from central import nav as _nav  # noqa: E402  (the one menu definition, shared with site.js)

TAGLINE = _nav.TAGLINE
_ADMIN = [("admin", "Admin", "/admin/")]   # shown only inside the admin console

_NAV_CSS = """
<style>
.gdb-nav { display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap;
  min-height:56px; border-bottom:1px solid var(--ink); margin:0 0 1.2rem; }
.gdb-nav ul { display:flex; gap:clamp(.9rem,2vw,1.8rem); list-style:none; margin:0; padding:0; flex-wrap:wrap; }
.gdb-nav ul a { text-decoration:none; font:400 15px var(--sans); color:var(--ink-2); }
.gdb-nav ul a:hover, .gdb-nav ul a.on { color:var(--red); }
</style>
"""

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..600;1,9..144,300..500&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root {
  --paper:#F4EEE4; --paper-2:#EDE4D6; --paper-3:#E3D7C4;
  --ink:#1F1A16; --ink-2:#4A423A; --muted:#6B6157; --rule:#CFC2AE;
  --red:#B3261E; --red-dk:#8E1D17;
  --serif:"Fraunces",Georgia,serif; --sans:"Inter",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,monospace;
}
/* ground */
.stApp { background:var(--paper); }
[data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"],
[data-testid="stBottom"], [data-testid="stBottomBlockContainer"] { background:var(--paper); }
body, .stApp, .stMarkdown, p, li, label, [data-testid="stMarkdownContainer"],
[data-testid="stWidgetLabel"] p { font-family:var(--sans); color:var(--ink-2); }
strong, b { color:var(--ink); }

/* headings — Fraunces, light weight, optical size for app scale */
h1,h2,h3,h4,h5,h6,[data-testid="stHeading"] h1,[data-testid="stHeading"] h2,[data-testid="stHeading"] h3 {
  font-family:var(--serif) !important;
  font-variation-settings:"opsz" 44,"wght" 350;
  font-weight:350 !important; color:var(--ink) !important; letter-spacing:-.01em; }
h1 em, h2 em, h3 em { font-style:italic; color:var(--red); }

/* NOTE: never hide header[data-testid="stHeader"] (mobile sidebar toggle lives there);
   never shrink block-container top padding (content would slide under the header). */
[data-testid="stSidebar"] { background:var(--paper-2); border-right:1px solid var(--rule); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { background:var(--paper-2); }
[data-testid="stSidebar"] p, [data-testid="stSidebar"] li, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] span, [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color:var(--ink-2); }

a, a:visited { color:var(--red); }
a:hover { color:var(--red-dk); }
code, pre, [data-testid="stCode"] { font-family:var(--mono); }
code:not(pre code), [data-testid="stMarkdownContainer"] code:not(pre code) {
  color:var(--red-dk) !important; background:var(--paper-2) !important; border:1px solid var(--rule); border-radius:0 !important; }
[data-testid="stCode"] pre, pre { background:var(--paper-2) !important; border:1px solid var(--rule); border-radius:0; color:var(--ink) !important; }
hr { border-color:var(--rule) !important; }

/* buttons — square, mono uppercase; primary = red fill, secondary = ink outline */
.stButton>button, .stDownloadButton>button, .stFormSubmitButton>button, [data-testid="stLinkButton"] a,
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-secondary"] {
  border-radius:0 !important; font-family:var(--mono) !important; font-weight:500 !important;
  text-transform:uppercase; letter-spacing:.08em; font-size:.78rem !important; }
.stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"],
.stFormSubmitButton>button[kind="primary"], [data-testid="stLinkButton"] a[kind="primary"],
[data-testid="stBaseButton-primary"], [data-testid="stBaseLinkButton-primary"] {
  background:var(--red) !important; border:1px solid var(--red) !important; color:#fff !important; }
.stButton>button[kind="primary"]:hover, .stFormSubmitButton>button[kind="primary"]:hover,
[data-testid="stLinkButton"] a[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background:var(--red-dk) !important; border-color:var(--red-dk) !important; color:#fff !important; }
.stButton>button[kind="secondary"], .stDownloadButton>button[kind="secondary"],
.stFormSubmitButton>button[kind="secondary"], [data-testid="stLinkButton"] a[kind="secondary"],
[data-testid="stBaseButton-secondary"], [data-testid="stBaseLinkButton-secondary"] {
  background:transparent !important; color:var(--ink) !important; border:1px solid var(--ink) !important; }
.stButton>button[kind="secondary"]:hover, .stDownloadButton>button[kind="secondary"]:hover,
.stFormSubmitButton>button[kind="secondary"]:hover, [data-testid="stLinkButton"] a[kind="secondary"]:hover,
[data-testid="stBaseButton-secondary"]:hover {
  background:var(--ink) !important; color:var(--paper) !important; border-color:var(--ink) !important; }
.stButton>button[kind="tertiary"], [data-testid="stBaseButton-tertiary"] { color:var(--red) !important; border-radius:0 !important; }
.stButton>button:focus-visible, [data-testid="stLinkButton"] a:focus-visible { outline:2px solid var(--red) !important; outline-offset:2px; box-shadow:none !important; }
/* labels inside any button inherit the button's colour (the markdown container would otherwise paint them ink-2) */
.stButton>button *, .stDownloadButton>button *, .stFormSubmitButton>button *, [data-testid="stLinkButton"] a *,
[data-testid^="stBaseButton-"] *, [data-testid^="stBaseLinkButton-"] *, [data-testid="stButtonGroup"] button *,
[data-testid="stPills"] button *, [data-testid="stSegmentedControl"] button *, [data-testid="stFileUploaderDropzone"] button * {
  color:inherit !important; font-family:inherit !important; }
/* ...except Material icon glyphs, which need their own font to render as icons */
[data-testid="stIconMaterial"], .material-symbols-rounded, [data-testid^="stBaseButton-"] [data-testid="stIconMaterial"] {
  font-family:"Material Symbols Rounded" !important; }

/* inputs — paper ground, hairline rule, ink on focus (never white-on-white) */
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input, .stTimeInput input,
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="base-input"] {
  background:var(--paper) !important; color:var(--ink) !important; border-color:var(--rule) !important; border-radius:0 !important; }
[data-baseweb="input"], [data-baseweb="textarea"] { border:1px solid var(--rule) !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within { border-color:var(--ink) !important; box-shadow:none !important; }
[data-baseweb="input"] input, [data-baseweb="base-input"] input, [data-baseweb="textarea"] textarea { border:0 !important; }
.stTextInput input::placeholder, .stTextArea textarea::placeholder, .stNumberInput input::placeholder { color:var(--muted) !important; }
.stNumberInput button, [data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] {
  background:var(--paper-2) !important; color:var(--ink) !important; border-color:var(--rule) !important; border-radius:0 !important; }
/* Streamlit >= 1.6x renders inputs with react-aria (not baseweb): same rules there */
[data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"],
[data-testid="stNumberInput"] [role="group"], [data-testid="stDateInput"] [role="group"],
[data-testid="stTimeInput"] [role="group"], .react-aria-ComboBox [role="group"], .react-aria-TextField > div,
.react-aria-DateField [role="group"], .react-aria-NumberField [role="group"] {
  background:var(--paper) !important; color:var(--ink) !important; border:1px solid var(--rule) !important;
  border-radius:0 !important; box-shadow:none !important; }
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInput"] [role="group"]:focus-within, [data-testid="stDateInput"] [role="group"]:focus-within,
.react-aria-ComboBox [role="group"]:focus-within, .react-aria-TextField > div:focus-within,
.react-aria-ComboBox [role="group"][data-focus-within="true"] { border-color:var(--ink) !important; box-shadow:none !important; }
.react-aria-ComboBox input, .react-aria-ComboBox button, [data-testid="stTextInputField"], .react-aria-TextField input, .react-aria-TextField textarea {
  color:var(--ink) !important; background:transparent !important; }
.react-aria-ComboBox input::placeholder, [data-testid="stTextInputField"]::placeholder { color:var(--muted) !important; }
[data-testid="stSelectboxVirtualDropdown"], [data-testid="stMultiSelectVirtualDropdown"], .react-aria-Popover,
[data-rac][data-trigger="ComboBox"], [data-rac][data-trigger="Select"] {
  background:var(--paper) !important; border:1px solid var(--ink) !important; border-radius:0 !important; box-shadow:none !important; }
[role="option"][data-selected="true"], [role="option"][data-focused="true"], [role="option"][data-hovered="true"] {
  background:var(--paper-3) !important; color:var(--ink) !important; }
[role="option"] [data-item-hl] { background:transparent !important; border-radius:0 !important; }
/* selectbox / multiselect — closed control + its inner text (baseweb builds) */
[data-baseweb="select"] > div, [data-baseweb="select"] div[role="button"], [data-baseweb="select"] > div > div {
  background:var(--paper) !important; border-color:var(--rule) !important; border-radius:0 !important; }
[data-baseweb="select"] > div { border:1px solid var(--rule) !important; }
[data-baseweb="select"] > div:focus-within { border-color:var(--ink) !important; box-shadow:none !important; }
[data-baseweb="select"] div, [data-baseweb="select"] span, [data-baseweb="select"] input,
[data-baseweb="select"] svg { color:var(--ink) !important; fill:var(--ink); }
/* the dropdown popover + its options */
[data-baseweb="popover"] > div, [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"], ul[role="listbox"] {
  background:var(--paper) !important; border:1px solid var(--ink) !important; border-radius:0 !important; box-shadow:none !important; }
[role="option"], [data-baseweb="menu"] li { background:transparent !important; color:var(--ink) !important; font-family:var(--sans); }
[role="option"]:hover, [role="option"][aria-selected="true"], [data-baseweb="menu"] li:hover,
[data-baseweb="menu"] li[aria-selected="true"] { background:var(--paper-3) !important; color:var(--ink) !important; }
/* date picker calendar popover */
[data-baseweb="calendar"], [data-baseweb="datepicker"] { background:var(--paper) !important; color:var(--ink) !important; }
[data-baseweb="calendar"] div, [data-baseweb="calendar"] button { color:var(--ink); }
[data-baseweb="calendar"] [aria-selected="true"] { background:var(--red) !important; color:#fff !important; }
/* multiselect chosen tags + file uploader dropzone */
[data-baseweb="tag"] { background:var(--red) !important; border-radius:0 !important; }
[data-baseweb="tag"] span, [data-baseweb="tag"] svg, [data-baseweb="tag"] [role="presentation"] { color:#fff !important; fill:#fff !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] { background:var(--red) !important; color:#fff !important; border-radius:0 !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] *, [data-testid="stMultiSelectTagsContainer"] [data-tag] button,
[data-testid="stMultiSelectTagsContainer"] [data-tag] svg { color:#fff !important; fill:#fff !important; }
[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploader"] section {
  background:var(--paper-2) !important; border:1px dashed var(--rule) !important; color:var(--ink-2) !important; border-radius:0 !important; }
[data-testid="stFileUploaderDropzone"] small, [data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stFileUploaderDropzoneInstructions"] * { color:var(--ink-2) !important; }
[data-testid="stFileUploaderDropzone"] svg { fill:var(--muted); color:var(--muted); }
[data-testid="stFileUploaderDropzone"] button, [data-testid="stFileUploaderFile"] button {
  background:transparent !important; color:var(--ink) !important; border:1px solid var(--ink) !important; border-radius:0 !important;
  font-family:var(--mono) !important; text-transform:uppercase; letter-spacing:.06em; font-size:.75rem !important; }
[data-testid="stFileUploaderFile"], [data-testid="stFileUploaderFileName"], [data-testid="stFileUploaderFileData"] small { color:var(--ink-2) !important; }
/* checkbox / radio / toggle */
[data-testid="stCheckbox"] span[data-baseweb="checkbox"] > div, [data-baseweb="checkbox"] > div:first-child {
  border-color:var(--ink) !important; border-radius:0 !important; }
[data-baseweb="checkbox"] [aria-checked="true"] ~ div, [data-baseweb="checkbox"] input:checked + div { background:var(--red) !important; }
[data-testid="stRadio"] [data-baseweb="radio"] > div:first-child { border-color:var(--ink) !important; }
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] { background:var(--red) !important; border-color:var(--red) !important; }
/* tabs */
[data-testid="stTabs"] [data-baseweb="tab-list"] { border-bottom:1px solid var(--ink); gap:0; }
[data-baseweb="tab"] { font-family:var(--mono) !important; text-transform:uppercase; letter-spacing:.08em; font-size:.76rem !important;
  color:var(--muted) !important; background:transparent !important; border-radius:0 !important; }
[data-baseweb="tab"][aria-selected="true"] { color:var(--ink) !important; }
[data-baseweb="tab"] p { font-family:var(--mono) !important; font-size:.76rem !important; color:inherit !important; }
[data-baseweb="tab-highlight"] { background:var(--red) !important; height:3px; }
[data-baseweb="tab-border"] { background:transparent !important; }
/* react-aria tabs (Streamlit >= 1.6x) */
[data-testid="stTabs"] [role="tablist"] { border-bottom:1px solid var(--ink); }
[data-testid="stTab"] { font-family:var(--mono) !important; text-transform:uppercase; letter-spacing:.08em; font-size:.76rem !important;
  color:var(--muted) !important; border-radius:0 !important; }
[data-testid="stTab"] p { font-family:var(--mono) !important; font-size:.76rem !important; color:inherit !important; }
[data-testid="stTab"]:hover, [data-testid="stTab"][aria-selected="true"] { color:var(--ink) !important; }
[data-testid="stTab"][aria-selected="true"] > div[data-rac] { background:var(--red) !important; border-radius:0 !important; height:3px; }

/* st.pills / segmented_control -> square chips: selected = red fill, unselected = paper + rule */
[data-testid="stPills"] button, [data-testid="stButtonGroup"] button, [data-testid="stSegmentedControl"] button,
[data-baseweb="button-group"] button {
  border-radius:0 !important; font-family:var(--mono) !important; text-transform:uppercase;
  letter-spacing:.06em; font-size:.72rem !important; border:1px solid var(--rule) !important;
  color:var(--ink-2) !important; background:var(--paper) !important; }
[data-testid="stPills"] button p, [data-testid="stButtonGroup"] button p, [data-testid="stSegmentedControl"] button p {
  font-family:var(--mono) !important; font-size:.72rem !important; color:inherit !important; }
[data-testid="stPills"] button:hover, [data-testid="stButtonGroup"] button:hover, [data-testid="stSegmentedControl"] button:hover {
  border-color:var(--ink) !important; color:var(--ink) !important; }
[data-testid="stPills"] button[aria-checked="true"], [data-testid="stPills"] button[aria-pressed="true"],
[data-testid="stPills"] button[kind="primary"], [data-testid="stPills"] button[kind="pillsActive"],
[data-testid="stButtonGroup"] button[aria-checked="true"], [data-testid="stButtonGroup"] button[aria-pressed="true"],
[data-testid="stSegmentedControl"] button[aria-checked="true"], [data-testid="stSegmentedControl"] button[aria-pressed="true"],
[data-testid="stSegmentedControl"] button[kind="segmented_controlActive"],
[data-baseweb="button-group"] button[aria-pressed="true"] {
  background:var(--red) !important; color:#fff !important; border-color:var(--red) !important; font-weight:500 !important; }
[data-testid="stPills"] button[aria-checked="true"] p, [data-testid="stPills"] button[kind="pillsActive"] p,
[data-testid="stSegmentedControl"] button[aria-checked="true"] p,
[data-testid="stSegmentedControl"] button[kind="segmented_controlActive"] p { color:#fff !important; }

/* captions / labels / metrics */
[data-testid="stCaptionContainer"], .stCaption, small, [data-testid="stCaptionContainer"] p {
  font-family:var(--mono) !important; font-size:.76rem !important; letter-spacing:.02em; color:var(--muted) !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label {
  font-family:var(--mono) !important; text-transform:uppercase; letter-spacing:.1em; font-size:.72rem !important; color:var(--muted) !important; }
[data-testid="stMetricValue"], [data-testid="stMetricValue"] div, [data-testid="stMetricValue"] p {
  font-family:var(--serif) !important; color:var(--ink) !important; font-variation-settings:"opsz" 44,"wght" 350; font-weight:350; }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p { font-family:var(--mono) !important; text-transform:uppercase; letter-spacing:.1em; font-size:.72rem !important; color:var(--muted) !important; }
[data-testid="stMetricDelta"] { font-family:var(--mono); }
[data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stDataEditor"] {
  border:1px solid var(--rule); border-radius:0; }
[data-testid="stTable"] table { border-collapse:collapse; }
[data-testid="stTable"] th { font-family:var(--mono); text-transform:uppercase; letter-spacing:.08em; font-size:.72rem; color:var(--muted); border-bottom:1px solid var(--rule); }
[data-testid="stTable"] td { border-bottom:1px solid var(--rule); color:var(--ink-2); }
[data-testid="stExpander"] { border:0 !important; border-top:1px solid var(--rule) !important; border-bottom:1px solid var(--rule) !important; border-radius:0 !important; }
[data-testid="stExpander"] details { border:0 !important; border-radius:0 !important; background:transparent !important; }
[data-testid="stExpander"] summary { color:var(--ink) !important; }
[data-testid="stExpander"] summary p { color:var(--ink) !important; }
[data-testid="stExpander"] summary:hover, [data-testid="stExpander"] summary:hover svg { color:var(--red) !important; fill:var(--red); }
[data-testid="stForm"] { border:1px solid var(--rule) !important; border-radius:0 !important; background:var(--paper) !important; }
[data-testid="stPopover"] button, [data-testid="stPopoverButton"] { border-radius:0 !important; border:1px solid var(--ink) !important; color:var(--ink) !important; background:transparent !important; }
[data-testid="stPopoverBody"] { background:var(--paper) !important; border:1px solid var(--ink) !important; border-radius:0 !important; }
[data-testid="stToast"] { background:var(--ink) !important; color:var(--paper) !important; border-radius:0 !important; }
[data-testid="stToast"] * { color:var(--paper) !important; }
[data-testid="stTooltipContent"], [data-baseweb="tooltip"] { background:var(--ink) !important; color:var(--paper) !important; border-radius:0 !important; }
[data-testid="stProgress"] > div > div { background:var(--red) !important; }
[data-testid="stImage"] img { border:1px solid var(--rule); }

/* alerts — toned paper, red left rule, ink text */
[data-testid="stAlert"], [data-testid="stAlertContainer"] {
  background:var(--paper-2) !important; color:var(--ink-2) !important; border:0 !important;
  border-left:2px solid var(--red) !important; border-radius:0 !important; }
[data-testid="stAlert"] p, [data-testid="stAlertContainer"] p, [data-testid="stAlert"] li, [data-testid="stAlertContainer"] li,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] { color:var(--ink-2) !important; }
[data-testid="stAlert"] svg, [data-testid="stAlertContainer"] svg { fill:var(--red) !important; color:var(--red) !important; }
[data-testid="stException"] { background:var(--paper-2) !important; border-left:2px solid var(--red) !important; border-radius:0 !important; }

/* brand lockup + rule */
.gdb-brand { display:inline-block; text-decoration:none; color:var(--ink);
  font-family:var(--serif); font-weight:400; font-size:1.45rem; letter-spacing:-.01em; line-height:1;
  font-variation-settings:"opsz" 44,"wght" 400; margin:0 0 .6rem; }
.gdb-brand i { color:var(--red); font-style:italic; }
.gdb-brand:hover { color:var(--ink); }
.gdb-brand:hover i { color:var(--red-dk); }
.gdb-rule { height:1px; margin:.25rem 0 1.1rem; background:var(--rule); }
</style>
"""


def apply_theme(active: str = "") -> None:
    """Inject the shared theme and render the cross-app nav. `active` is one of
    'explore' | 'glowtbook' | 'admin' and gets the red-filled primary style;
    the others are ink-outlined. The text lockup doubles as the Home link."""
    st.html(_CSS)
    extra = _ADMIN if active == "admin" else None
    st.html(_NAV_CSS + f'<nav class="gdb-nav" aria-label="Primary">'
            f'<a class="gdb-brand" href="/" target="_self">{_MARK}</a>'
            f'<ul>{_nav.menu_html(active, extra)}</ul></nav>')


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
