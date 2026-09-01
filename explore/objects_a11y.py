"""
explore.objects_a11y
=====================
Render the objects collection as accessible, mDLAUG-aligned HTML so it holds up
for blind/low-vision users on mobile — and so the mDLAUG repair extension finds
little to fix. Pure and UI-free (no Streamlit), so it's unit-testable; the app
just feeds it data and st.html()s the result.

mDLAUG situations addressed here (see sites.uwm.edu/mdlaug and the alibama/mdlaug
coverage table). Each is tagged in the markup with data-mdlaug-ok:
  COM1  skip link + labelled region                 RED1  aria-live "N found"
  ACC4  collection items as a named list            NAV3  per-item position
  NAV4  Contents jump-links for a long item          EVA1  descriptive item names
  ACC2  real descriptive alt text on images          ACC1  file links w/ format+size
  RED4  unavailable features say why                  ACC3  structured data as tables
"""
from __future__ import annotations

import base64
import html


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _kb(b64: str) -> int:
    try:
        return max(1, round(len(base64.b64decode(b64)) / 1024))
    except Exception:
        return max(1, round(len(b64) * 3 / 4 / 1024))


def _alt(title, maker, year, materials, role, caption) -> str:
    parts = [title or "Glass object"]
    if maker:
        parts.append(f"by {maker}")
    if year:
        parts.append(str(year))
    lead = ", ".join(parts)
    tail = []
    if materials:
        tail.append(str(materials))
    role_txt = (role or "").replace("-", " ") or "photo"
    tail.append(f"{role_txt} view")
    if caption:
        tail.append(str(caption))
    return _esc(lead + ". " + ". ".join(tail) + ".")


_CSS = """
<style>
.gdb-a11y .visually-hidden{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;
  overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.gdb-a11y a.gdb-skip{position:absolute;left:-999px;top:auto}
.gdb-a11y a.gdb-skip:focus{position:static;display:inline-block;margin:.3rem 0;padding:.4rem .8rem;
  background:#1c1222;color:#fff;border-radius:8px}
.gdb-a11y ul.gdb-obj-list{list-style:none;padding:0;margin:0}
.gdb-a11y article{border:1px solid #ece8ef;border-radius:14px;padding:1.1rem 1.2rem;margin:0 0 1.2rem}
.gdb-a11y figure{margin:.4rem 0}
.gdb-a11y figure img{max-width:min(340px,100%);height:auto;border-radius:10px}
.gdb-a11y figcaption{color:#5b5666;font-size:.9rem;margin-top:.25rem}
.gdb-a11y dl{display:grid;grid-template-columns:auto 1fr;gap:.15rem .8rem;margin:.5rem 0}
.gdb-a11y dt{font-weight:600;color:#3f3a48}
.gdb-a11y table{border-collapse:collapse;margin:.5rem 0;width:100%;max-width:36rem}
.gdb-a11y caption{text-align:left;font-weight:600;margin-bottom:.25rem}
.gdb-a11y th,.gdb-a11y td{border:1px solid #e6e2ea;padding:.3rem .55rem;text-align:left;font-size:.92rem}
.gdb-a11y th[scope=col]{background:#faf7fb}
.gdb-a11y ul.gdb-files{margin:.5rem 0 0;padding-left:1.1rem}
.gdb-a11y .gdb-cred{background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:.6rem .8rem;margin:.5rem 0}
.gdb-a11y .gdb-unverified{color:#9a3412}
</style>
"""


def build_objects_html(objects: list[dict], verify_base: str = "https://glassdatabase.org") -> str:
    """Return an accessible HTML document for the list of contributed objects."""
    n = len(objects)
    if n == 0:
        return (_CSS + '<section class="gdb-a11y">'
                '<p role="status" aria-live="polite" data-mdlaug-ok="RED1">'
                'No objects have been published yet. '
                '<a href="/glowtbook/">Add one in Glowtbook</a>.</p></section>')

    out = [_CSS, '<section class="gdb-a11y" aria-labelledby="gdb-obj-h">']
    out.append('<a class="gdb-skip" href="#gdb-objects" data-mdlaug-ok="COM1">Skip to the objects list</a>')
    out.append('<h2 id="gdb-obj-h">Published glass objects</h2>')
    out.append(f'<p role="status" aria-live="polite" data-mdlaug-ok="RED1">'
               f'{n} object{"s" if n != 1 else ""} found.</p>')
    out.append('<p>Pieces contributed through Glowtbook — a condensed public rendition; '
               'originals stay with the contributor. '
               '<a href="/glowtbook/">Add your own piece</a>.</p>')

    # NAV4: Contents jump-links (overview of a lengthy item)
    out.append('<nav aria-label="Objects on this page" data-mdlaug-ok="NAV4"><h3 class="visually-hidden">Contents</h3><ol>')
    for o in objects:
        name = o.get("title") or "Untitled"
        if o.get("maker"):
            name += f" — {o['maker']}"
        out.append(f'<li><a href="#obj-{_esc(o["id"])}">{_esc(name)}</a></li>')
    out.append('</ol></nav>')

    out.append('<ul id="gdb-objects" role="list" class="gdb-obj-list" '
               'aria-label="Contributed glass objects" data-mdlaug-ok="ACC4">')
    for i, o in enumerate(objects, 1):
        oid = _esc(o["id"])
        title = o.get("title") or "Untitled"
        # EVA1: a descriptive accessible name (title + maker + year)
        subtitle = " · ".join(x for x in [o.get("maker") or "maker unknown",
                                          str(o.get("year") or "").strip() or "—"] if x)
        out.append(f'<li><article id="obj-{oid}" aria-labelledby="obj-{oid}-h" data-mdlaug-ok="EVA1">')
        out.append(f'<h3 id="obj-{oid}-h">{_esc(title)}</h3>')
        # NAV3: per-item position, announced to screen readers
        out.append(f'<p data-mdlaug-ok="NAV3"><span class="visually-hidden">Item {i} of {n}. </span>'
                   f'<em>{_esc(subtitle)}</em></p>')

        # ACC2: images with real, descriptive alt text
        for role, caption, b64 in o.get("images", []):
            alt = _alt(title, o.get("maker"), o.get("year"), o.get("materials"), role, caption)
            cap = (role or "").replace("-", " ")
            if caption:
                cap = f"{cap}: {caption}" if cap else caption
            out.append('<figure data-mdlaug-ok="ACC2">'
                       f'<img src="data:image/jpeg;base64,{b64}" alt="{alt}" loading="lazy">'
                       + (f'<figcaption>{_esc(cap)}</figcaption>' if cap else '')
                       + '</figure>')
        if o.get("video_url"):
            out.append(f'<p><a href="{_esc(o["video_url"])}" data-mdlaug-ok="ACC1">'
                       'Play the condensed video (MP4)<span class="visually-hidden"> '
                       '(opens in a new browser tab)</span></a></p>')

        # ACC3/COM4-style: structured facts as a definition list, not free text
        pairs = [("Techniques", o.get("techniques")), ("Materials", o.get("materials")),
                 ("Dimensions", o.get("dimensions")), ("Stated value", o.get("value_display")),
                 ("Contributor", o.get("contributor"))]
        pairs = [(k, v) for k, v in pairs if v]
        if pairs:
            out.append('<dl>' + "".join(f'<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>' for k, v in pairs) + '</dl>')
        if o.get("description"):
            out.append(f'<p>{_esc(o["description"])}</p>')
        out.append(f'<p class="gdb-unverified" data-mdlaug-ok="RED4">Provenance: '
                   f'{_esc(o.get("sourcing") or "self-reported")} — unverified.</p>')

        # ACC3/COM4: provenance events as a real data table with header scope
        events = o.get("events") or []
        if events:
            out.append('<table data-mdlaug-ok="ACC3">'
                       f'<caption>Provenance events for {_esc(title)}</caption>'
                       '<thead><tr><th scope="col">Event</th><th scope="col">Date</th>'
                       '<th scope="col">Actor</th><th scope="col">Place</th></tr></thead><tbody>')
            for e in events:
                out.append('<tr>'
                           f'<td>{_esc(e.get("event_type") or "—")}</td>'
                           f'<td>{_esc(e.get("event_date") or "—")}</td>'
                           f'<td>{_esc(e.get("actor") or "—")}</td>'
                           f'<td>{_esc(e.get("location") or "—")}</td></tr>')
            out.append('</tbody></table>')

        # Content Credentials, as text (no hidden disclosure to miss)
        creds = o.get("creds")
        if o.get("has_credentials") and creds:
            cred_pairs = [("Signed by", creds.get("issuer")),
                          ("Creator", ", ".join(creds.get("creator") or []) or None),
                          ("Actions", " → ".join(creds.get("actions") or []) or None),
                          ("Validation", creds.get("validation_state"))]
            cred_pairs = [(k, v) for k, v in cred_pairs if v]
            out.append('<section class="gdb-cred" aria-label="Content Credentials for '
                       f'{_esc(title)}"><h4>Content Credentials (C2PA)</h4><dl>'
                       + "".join(f'<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>' for k, v in cred_pairs)
                       + '</dl><p>Self-signed test certificate — reads as untrusted until a '
                       'C2PA Trust-List certificate is installed.</p></section>')

        # ACC1: file links named with format + size, external links warn of new tab
        files = []
        primary = next((im for im in o.get("images", []) if im[0] == "primary"), None) \
            or (o.get("images") or [None])[0]
        if primary:
            b64 = primary[2]
            fname = _esc((o.get("content_hash") or o["id"]))
            files.append(f'<li><a href="data:image/jpeg;base64,{b64}" download="{fname}.jpg">'
                         f'Download the signed image (JPEG, {_kb(b64)} KB)</a></li>')
        if o.get("manifest_json"):
            mb64 = base64.b64encode(o["manifest_json"].encode()).decode()
            files.append(f'<li><a href="data:application/json;base64,{mb64}" '
                         f'download="{_esc(o.get("content_hash") or o["id"])}.manifest.json">'
                         f'Download the provenance manifest (JSON, {_kb(mb64)} KB)</a></li>')
        if o.get("verify_url"):
            files.append(f'<li><a href="{_esc(o["verify_url"])}" target="_blank" rel="noopener">'
                         'Verify on Content Credentials<span class="visually-hidden"> '
                         '(opens in a new browser tab)</span></a></li>')
        if files:
            out.append(f'<ul class="gdb-files" aria-label="Downloads and verification for {_esc(title)}" '
                       f'data-mdlaug-ok="ACC1">{"".join(files)}</ul>')
        out.append('</article></li>')
    out.append('</ul></section>')
    return "".join(out)
