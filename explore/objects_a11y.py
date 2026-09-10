"""
explore.objects_a11y
=====================
Render the objects collection as an accessible, mDLAUG-aligned **grid of cards** so
it's attractive to scan and still holds up for blind/low-vision users on mobile.
Pure and UI-free (no Streamlit), so it's unit-testable; the app feeds it data and
st.html()s the result.

mDLAUG situations addressed (tagged in markup with data-mdlaug-ok):
  COM1 skip link + labelled region     RED1 aria-live "N found"
  ACC4 collection items as a named list NAV3 per-item position
  ACC2 descriptive alt text on images   ACC1 file links w/ format+size
  RED4 unavailable features say why      ACC3 structured data as tables
Verify is surfaced up top (a callout) and on each fingerprinted card (a prominent
"Verify this piece" action) instead of being buried at the bottom.
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
  background:#E8A44A;color:#0A0D11;border-radius:8px}
.gdb-a11y .gdb-verify-callout{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem 1rem;
  background:linear-gradient(180deg,rgba(232,164,74,.10),rgba(232,164,74,.02));border:1px solid rgba(232,164,74,.4);border-radius:14px;
  padding:.9rem 1.1rem;margin:1rem 0 .4rem}
.gdb-a11y .gdb-verify-callout h3{margin:0;font-size:1.05rem;color:#EEE7DB;font-family:"Fraunces",Georgia,serif;font-weight:400}
.gdb-a11y .gdb-verify-callout p{margin:0;color:#A8A29A;font-size:.92rem;flex:1 1 16rem}
.gdb-a11y .gdb-verify-btn{display:inline-block;padding:.55rem 1rem;border-radius:10px;font-weight:700;
  text-decoration:none;background:#E8A44A;color:#0A0D11;white-space:nowrap}
.gdb-a11y ul.gdb-grid{list-style:none;padding:0;margin:1rem 0 0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1.1rem}
.gdb-a11y .gdb-card{border:1px solid #24221F;border-radius:14px;overflow:hidden;background:#16130F;
  display:flex;flex-direction:column;height:100%}
.gdb-a11y .gdb-card figure{margin:0}
.gdb-a11y .gdb-card figure img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;background:#05070a}
.gdb-a11y .gdb-card figcaption{padding:.2rem .9rem 0;color:#6E665C;font-size:.82rem}
.gdb-a11y .gdb-more{display:flex;gap:.3rem;flex-wrap:wrap;padding:.4rem .9rem 0}
.gdb-a11y .gdb-more img{width:46px;height:46px;object-fit:cover;border-radius:6px;border:1px solid #ece8ef;background:#faf7fb}
.gdb-a11y .gdb-body{padding:.7rem .9rem .9rem;display:flex;flex-direction:column;gap:.4rem}
.gdb-a11y .gdb-card h3{margin:.1rem 0 0;font-size:1.05rem;line-height:1.2;font-family:"Fraunces",Georgia,serif;color:#EEE7DB;font-weight:400}
.gdb-a11y .gdb-sub{color:#A8A29A;font-size:.88rem}
.gdb-a11y .gdb-tech{font-size:.85rem;color:#A8A29A}
.gdb-a11y .gdb-fp{background:rgba(232,164,74,.10);border:1px solid rgba(232,164,74,.45);border-radius:9px;padding:.45rem .6rem;font-size:.86rem;color:#EEE7DB}
.gdb-a11y .gdb-fp a{font-weight:700;color:#E8A44A}
.gdb-a11y .gdb-cred-badge{font-size:.82rem;color:#5EC8BD}
.gdb-a11y .gdb-unverified{color:#6E665C;font-size:.82rem}
.gdb-a11y details{border-top:1px solid #24221F;margin-top:.2rem}
.gdb-a11y summary{cursor:pointer;padding:.5rem 0 .3rem;font-size:.86rem;color:#A8A29A;font-weight:600}
.gdb-a11y details dl{display:grid;grid-template-columns:auto 1fr;gap:.15rem .7rem;margin:.4rem 0;font-size:.86rem}
.gdb-a11y details dt{font-weight:600;color:#A8A29A}
.gdb-a11y table{border-collapse:collapse;margin:.4rem 0;width:100%;font-size:.82rem}
.gdb-a11y caption{text-align:left;font-weight:600;margin-bottom:.2rem;font-size:.85rem}
.gdb-a11y th,.gdb-a11y td{border:1px solid #24221F;padding:.25rem .45rem;text-align:left;color:#A8A29A}
.gdb-a11y th[scope=col]{background:#1A140F;color:#EEE7DB}
.gdb-a11y ul.gdb-files{margin:.4rem 0 0;padding-left:1.1rem;font-size:.85rem}
.gdb-a11y .gdb-cred{background:rgba(94,200,189,.08);border:1px solid rgba(94,200,189,.35);border-radius:10px;padding:.5rem .7rem;margin:.4rem 0;font-size:.85rem;color:#A8A29A}
</style>
"""


def build_objects_html(objects: list[dict], verify_base: str = "https://glassdatabase.org") -> str:
    """Return an accessible HTML grid for the list of contributed objects."""
    n = len(objects)
    if n == 0:
        return (_CSS + '<section class="gdb-a11y">'
                '<p role="status" aria-live="polite" data-mdlaug-ok="RED1">'
                'No objects have been published yet. '
                '<a href="/glowtbook/">Add one in Glowtbook</a>.</p></section>')

    any_fp = any((o.get("fingerprint") or {}).get("rating") is not None for o in objects)
    out = [_CSS, '<section class="gdb-a11y" aria-labelledby="gdb-obj-h">']
    out.append('<a class="gdb-skip" href="#gdb-objects" data-mdlaug-ok="COM1">Skip to the objects</a>')
    out.append('<h2 id="gdb-obj-h">Published glass objects</h2>')
    out.append(f'<p role="status" aria-live="polite" data-mdlaug-ok="RED1">'
               f'{n} object{"s" if n != 1 else ""} found.</p>')

    # Verify surfaced UP TOP (was buried at the bottom before).
    if any_fp:
        out.append('<aside class="gdb-verify-callout" aria-label="Verify a physical piece">'
                   '<h3>Have a physical piece?</h3>'
                   '<p>Pieces with a fingerprint show a <strong>Verify this piece</strong> button — '
                   'capture the object with your phone camera and match it against the fingerprint on '
                   'file, right in your browser. Nothing you capture leaves your device.</p></aside>')
    else:
        out.append('<p>Pieces contributed through Glowtbook — a condensed public rendition; originals '
                   'stay with the contributor. <a href="/glowtbook/">Add your own piece</a>.</p>')

    out.append('<ul id="gdb-objects" role="list" class="gdb-grid" '
               'aria-label="Contributed glass objects" data-mdlaug-ok="ACC4">')
    for i, o in enumerate(objects, 1):
        oid = _esc(o["id"])
        title = o.get("title") or "Untitled"
        subtitle = " · ".join(x for x in [o.get("maker") or "maker unknown",
                                          str(o.get("year") or "").strip() or "—"] if x)
        out.append(f'<li><article class="gdb-card" id="obj-{oid}" aria-labelledby="obj-{oid}-h" '
                   'data-mdlaug-ok="EVA1">')

        # Card image (primary) with descriptive alt (ACC2)
        primary = next((im for im in o.get("images", []) if im[0] == "primary"), None) \
            or (o.get("images") or [None])[0]
        if primary:
            role, caption, b64 = primary
            alt = _alt(title, o.get("maker"), o.get("year"), o.get("materials"), role, caption)
            out.append(f'<figure data-mdlaug-ok="ACC2"><img src="data:image/jpeg;base64,{b64}" '
                       f'alt="{alt}" loading="lazy"></figure>')
        # Additional views of the SAME object stay on this one card (not new cards)
        extra = [im for im in o.get("images", []) if im is not primary]
        if extra:
            strip = "".join(
                f'<img src="data:image/jpeg;base64,{eb}" loading="lazy" '
                f'alt="{_alt(title, o.get("maker"), o.get("year"), o.get("materials"), er, ec)}">'
                for er, ec, eb in extra)
            out.append(f'<div class="gdb-more" aria-label="{len(extra)} more view'
                       f'{"s" if len(extra) != 1 else ""} of {_esc(title)}">{strip}</div>')

        out.append('<div class="gdb-body">')
        out.append(f'<h3 id="obj-{oid}-h">{_esc(title)}</h3>')
        out.append(f'<p class="gdb-sub" data-mdlaug-ok="NAV3"><span class="visually-hidden">'
                   f'Item {i} of {n}. </span>{_esc(subtitle)}</p>')
        if o.get("techniques"):
            out.append(f'<p class="gdb-tech">{_esc(o["techniques"])}</p>')

        # Fingerprint + Verify — prominent, on the card
        fp = o.get("fingerprint")
        if fp and fp.get("rating") is not None:
            out.append('<p class="gdb-fp" data-mdlaug-ok="EVA1"><strong>Physical fingerprint:</strong> '
                       f'{_esc(fp.get("rating"))}/100 ({_esc(fp.get("tier") or "")}) — '
                       f'<a href="/fingerprint/verify.html?object={oid}">Verify this piece'
                       '<span class="visually-hidden"> (opens the camera capture app in a new browser '
                       'tab)</span></a></p>')

        # Credentials badge (visible), detail lives in the disclosure below
        if o.get("has_credentials") and o.get("creds"):
            vs = o["creds"].get("validation_state") or "signed"
            out.append(f'<p class="gdb-cred-badge">🔐 Content Credentials — {_esc(vs)}</p>')
        out.append(f'<p class="gdb-unverified" data-mdlaug-ok="RED4">Provenance: '
                   f'{_esc(o.get("sourcing") or "self-reported")} — unverified.</p>')

        # Everything heavy in an accessible disclosure so the grid stays scannable
        det = []
        pairs = [("Materials", o.get("materials")), ("Dimensions", o.get("dimensions")),
                 ("Stated value", o.get("value_display")), ("Contributor", o.get("contributor"))]
        pairs = [(k, v) for k, v in pairs if v]
        if pairs:
            det.append('<dl>' + "".join(f'<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>' for k, v in pairs) + '</dl>')
        if o.get("description"):
            det.append(f'<p>{_esc(o["description"])}</p>')
        if o.get("video_url"):
            det.append(f'<p><a href="{_esc(o["video_url"])}" target="_blank" rel="noopener" '
                       'data-mdlaug-ok="ACC1">Play the condensed video (MP4)<span class="visually-hidden">'
                       ' (opens in a new browser tab)</span></a></p>')

        events = o.get("events") or []
        if events:
            det.append('<table data-mdlaug-ok="ACC3">'
                       f'<caption>Provenance events for {_esc(title)}</caption>'
                       '<thead><tr><th scope="col">Event</th><th scope="col">Date</th>'
                       '<th scope="col">Actor</th><th scope="col">Place</th></tr></thead><tbody>')
            for e in events:
                det.append('<tr>'
                           f'<td>{_esc(e.get("event_type") or "—")}</td>'
                           f'<td>{_esc(e.get("event_date") or "—")}</td>'
                           f'<td>{_esc(e.get("actor") or "—")}</td>'
                           f'<td>{_esc(e.get("location") or "—")}</td></tr>')
            det.append('</tbody></table>')

        creds = o.get("creds")
        if o.get("has_credentials") and creds:
            cred_pairs = [("Signed by", creds.get("issuer")),
                          ("Creator", ", ".join(creds.get("creator") or []) or None),
                          ("Actions", " → ".join(creds.get("actions") or []) or None),
                          ("Validation", creds.get("validation_state"))]
            cred_pairs = [(k, v) for k, v in cred_pairs if v]
            det.append('<div class="gdb-cred"><strong>Content Credentials (C2PA)</strong><dl>'
                       + "".join(f'<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>' for k, v in cred_pairs)
                       + '</dl><p>Self-signed test certificate — reads as untrusted until a '
                       'C2PA Trust-List certificate is installed.</p></div>')

        # ACC1: downloads via API URLs (data: URIs get stripped by st.html's sanitizer)
        base = (verify_base or "").rstrip("/")
        files = []
        if primary:
            import base64 as _b64
            try:
                _fmt = "PNG" if _b64.b64decode(primary[2])[:4] == b"\x89PNG" else "JPEG"
            except Exception:
                _fmt = "JPEG"
            files.append(f'<li><a href="{base}/api/objects/{oid}/image" download>'
                         f'Download the signed image ({_fmt}, {_kb(primary[2])} KB)</a></li>')
        if o.get("manifest_json"):
            files.append(f'<li><a href="{base}/api/objects/{oid}/manifest.json" download>'
                         'Download the provenance manifest (JSON)</a></li>')
        if o.get("has_credentials"):
            files.append('<li>Verify the Content Credentials — download the image above, then drop '
                         'it into <a href="https://verify.contentauthenticity.org/" target="_blank" '
                         'rel="noopener">Content Authenticity Verify<span class="visually-hidden"> '
                         '(opens in a new browser tab)</span></a> or '
                         '<a href="https://c2paviewer.com/" target="_blank" rel="noopener">C2PA Viewer'
                         '<span class="visually-hidden"> (opens in a new browser tab)</span></a>.</li>')
        if files:
            det.append(f'<ul class="gdb-files" aria-label="Downloads and verification for {_esc(title)}" '
                       f'data-mdlaug-ok="ACC1">{"".join(files)}</ul>')

        if det:
            out.append(f'<details><summary>Provenance, credentials &amp; downloads</summary>'
                       f'{"".join(det)}</details>')
        out.append('</div></article></li>')
    out.append('</ul></section>')
    return "".join(out)
