#!/usr/bin/env python3
"""
Build the blog — Markdown in, paper-theme pages out (styled by /paper.css). No dependencies.

    python -m scripts.build_blog                 # blog/posts/*.md -> deploy/blog/
    python -m scripts.build_blog --out /tmp/b    # elsewhere (tests)

Each post is a Markdown file in ``blog/posts/`` with a front-matter block::

    ---
    title: Blowing Glass in Lima
    date: 2026-10-17
    author: Glass Database
    series: Theoretical studies
    summary: One sentence shown on the index, the homepage and in the feed.
    ---

The renderer covers the Markdown this blog uses — headings, paragraphs,
emphasis, links, inline code, bullet and numbered lists, pipe tables, block
quotes, rules and raw HTML lines — so adding a post needs no build tooling and
no new package. Output:

    deploy/blog/index.html        the list, newest first
    deploy/blog/<slug>.html       one page per post
    deploy/blog/feed.xml          RSS 2.0
    deploy/blog/posts.json        machine-readable list (the homepage reads it at build time)

It also refreshes the "From the blog" block on the homepage: whatever sits
between ``<!-- blog:latest -->`` and ``<!-- /blog:latest -->`` in
``deploy/landing-index.html`` is replaced with the latest posts, so the front
page never goes stale when a post lands. ``deploy/install.sh`` runs this before
it copies the static pages.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "blog" / "posts"
OUT = ROOT / "deploy" / "blog"
LANDING = ROOT / "deploy" / "landing-index.html"
SITE_TITLE = "Glass Database"
BLOG_TITLE = "Field notes"
BLOG_TAGLINE = "Studies, essays and updates from the open record for glass."
WORDS_PER_MINUTE = 220


# --- front matter + a small Markdown renderer --------------------------------
def parse_post(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip('"').strip("'")
            body = text[end + 4:].lstrip("\n")
    stem = path.stem
    slug = meta.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    d = meta.get("date") or (re.match(r"(\d{4}-\d{2}-\d{2})", stem).group(1)
                             if re.match(r"\d{4}-\d{2}-\d{2}", stem) else date.today().isoformat())
    words = len(re.findall(r"\w+", body))
    return {"slug": slug, "title": meta.get("title") or slug.replace("-", " ").title(),
            "date": d, "author": meta.get("author", ""), "series": meta.get("series", ""),
            "summary": meta.get("summary", ""), "tags": [t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
            "draft": meta.get("draft", "").lower() in ("1", "true", "yes"),
            "minutes": max(1, round(words / WORDS_PER_MINUTE)), "words": words,
            "body_md": body, "path": str(path)}


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_AUTOLINK = re.compile(r"(?<![\"'=>])\bhttps?://[^\s<)]+")


def inline(s: str) -> str:
    """Escape, then apply code / bold / italic / links. Code spans are protected first."""
    codes: list[str] = []

    def _code(m):
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"
    s = _INLINE_CODE.sub(_code, s)
    s = html.escape(s, quote=False)
    s = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', s)
    s = _AUTOLINK.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITAL.sub(r"<em>\1</em>", s)
    s = re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], s)
    return s


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in r)]
    if not cells:
        return ""
    head, body = cells[0], cells[1:]
    out = ['<div class="tbl"><table>', "<thead><tr>"]
    out += [f"<th>{inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for r in body:
        r = (r + [""] * len(head))[:len(head)]
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_markdown(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    para: list[str] = []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(x.strip() for x in para))}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            flush(); i += 1; continue
        if s.startswith("<"):                                   # raw HTML line(s)
            flush(); out.append(line); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush(); lvl = len(m.group(1)); txt = m.group(2).strip()
            hid = re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", txt).lower()).strip("-")
            out.append(f'<h{lvl} id="{hid}">{inline(txt)}</h{lvl}>'); i += 1; continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush(); out.append("<hr>"); i += 1; continue
        if s.startswith("|"):
            flush(); rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i]); i += 1
            out.append(_table(rows)); continue
        if s.startswith(">"):
            flush(); q = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip()); i += 1
            out.append(f"<blockquote><p>{inline(' '.join(q))}</p></blockquote>"); continue
        if re.match(r"^([-*•]|\d+[.)])\s+", s):
            flush()
            ordered = bool(re.match(r"^\d+[.)]\s+", s))
            items: list[str] = []
            while i < n:
                t = lines[i].strip()
                mm = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", t)
                if mm:
                    items.append(mm.group(1)); i += 1
                elif t and items and (lines[i].startswith("  ") or lines[i].startswith("\t")):
                    items[-1] += " " + t; i += 1               # continuation line
                else:
                    break
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(line); i += 1
    flush()
    return "\n".join(out)


# --- templates (paper theme: deploy/paper.css is the one stylesheet) -----------
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..600;1,9..144,300..500'
         '&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">\n'
         '<link rel="stylesheet" href="/paper.css">\n'
         '<script src="/site.js"></script>')

# The site's primary menu, as on the homepage (deploy/landing-index.html).
NAV = [("Registry", "/explore/"), ("Collect", "/events.html"), ("Field notes", "/blog/"),
       ("Glowtbook", "/glowtbook/"), ("Tools", "/fingerprint/enroll.html"), ("About", "/#about")]

# Page-specific rules only; everything shared lives in /paper.css (one owner).
CSS = """
  .nav ul a.on { color: var(--red); }
  /* site.js injects its own (field-data) chip rules after paper.css; outrank them here */
  .nav .gdb-chip { font: 500 12px/1 var(--mono); letter-spacing: .08em; color: var(--muted); }
  .nav .gdb-chip .gdb-dot { background: var(--rule); box-shadow: none; }
  .nav .gdb-chip.on .gdb-dot { background: var(--red); box-shadow: none; }
  .nav .gdb-chip a, .nav .gdb-chip button { color: var(--ink); border: 1px solid var(--ink); border-radius: 0; padding: .45rem .8rem; }
  .nav .gdb-chip a:hover, .nav .gdb-chip button:hover { background: var(--ink); color: var(--paper); }
  .nav .gdb-chip b { color: var(--ink); }
  /* opener */
  .opener { padding: clamp(2.5rem, 5vw, 4rem) 0 clamp(2rem, 4vw, 3rem); border-bottom: 1px solid var(--rule); }
  .opener .s-head { margin-bottom: 0; }
  .opener h1 { font-size: clamp(2.6rem, 6vw, 5.4rem); line-height: .98; font-weight: 320; margin: 1rem 0 1.4rem; max-width: 20ch; }
  .opener h2 { margin: 1rem 0 1.2rem; }
  .opener .label { line-height: 1.8; }
  .opener .sign { margin-top: 1.2rem; }
  /* post body: prose in the 9/12 column under the title */
  .body-grid { display: grid; grid-template-columns: 3fr 9fr; gap: 2rem; padding: clamp(2.5rem, 5vw, 4rem) 0; }
  .body-grid aside { display: flex; flex-direction: column; gap: 1.1rem; align-items: flex-start; align-self: start; position: sticky; top: 90px; }
  article.post { max-width: 68ch; font-size: 17px; line-height: 1.65; color: var(--ink-2); }
  article.post p { max-width: none; margin: 0 0 1.2em; }
  article.post h2 { font-size: 2.2rem; line-height: 1.1; color: var(--ink); margin: 2.4em 0 .6em; font-variation-settings: "opsz" 72; }
  article.post h2:first-child { margin-top: 0; }
  article.post h3 { color: var(--ink); margin: 1.8em 0 .5em; }
  article.post strong { color: var(--ink); font-weight: 600; }
  article.post a { color: var(--ink); text-decoration: underline; text-decoration-color: var(--rule); text-underline-offset: 3px; }
  article.post a:hover { color: var(--red); text-decoration-color: var(--red); }
  article.post code { font: 400 .9em var(--mono); background: var(--paper-2); padding: .1em .35em; }
  article.post ul, article.post ol { padding-left: 1.4rem; margin: 0 0 1.2em; }
  article.post li { margin: .35em 0; }
  article.post li::marker { color: var(--red); font-family: var(--mono); font-size: .9em; }
  article.post blockquote { border-left: 2px solid var(--red); margin: 1.6em 0; padding: .3em 0 .3em 1.3rem; color: var(--ink);
    font-family: var(--serif); font-weight: 350; font-size: 1.15em; line-height: 1.45; }
  article.post blockquote p { margin: 0; }
  article.post hr { border: 0; height: 1px; background: var(--rule); margin: 2.6em 0; }
  article.post .tbl table { min-width: 520px; }
  .post-foot { max-width: 68ch; margin-top: 4rem; padding-top: 2rem; border-top: 1px solid var(--ink); }
  .post-foot .sign { margin-top: 1rem; }
  .post-nav { display: flex; justify-content: space-between; gap: 1rem 2rem; flex-wrap: wrap; margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid var(--rule); }
  /* index */
  .posts-wrap { padding: clamp(3rem, 6vw, 5rem) 0; }
  .posts.list { margin-top: 3rem; }
  .posts.list li { grid-template-columns: 9rem 1fr; gap: 1rem; padding: 1.4rem 0; }
  .posts.list li a.t { display: block; font: 350 1.5rem/1.15 var(--serif); letter-spacing: -.01em; text-decoration: none; margin-bottom: .4rem; }
  .posts.list li a.t:hover { color: var(--red); }
  .posts.list li p { color: var(--ink-2); font-size: 16px; margin: 0 0 .8rem; }
  @media (max-width: 980px) { .body-grid { grid-template-columns: 1fr; } .body-grid aside { position: static; flex-direction: row; flex-wrap: wrap; } }
  @media (max-width: 680px) { .posts.list li { grid-template-columns: 1fr; gap: .3rem; } }
"""

SUBSCRIBE_JS = """<script>
  const nav=document.querySelector('.nav'),mb=document.querySelector('.menu-btn');
  mb.addEventListener('click',()=>{const o=nav.classList.toggle('open');mb.setAttribute('aria-expanded',o)});
  async function gdbSubscribe(e){
    e.preventDefault();
    const f = e.target, email = f.email.value.trim();
    const msg = f.nextElementSibling;
    try {
      const r = await fetch('/api/subscribe', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({email: email, source:'blog'}) });
      if (r.ok) { msg.textContent = "You're on the list — thanks."; f.reset(); }
      else { const j = await r.json().catch(function(){return {};}); msg.textContent = j.detail || "That didn't work — try again?"; }
    } catch (_) { msg.textContent = "Network error — please try again."; }
    return false;
  }
  if (window.GDB) { GDB.chip('#chip', { compact: true, next: location.pathname }); }
</script>"""


def _nav(active: str = "Field notes", index_on: bool = False) -> str:
    """The site header (as on the homepage) plus the Field-notes sub-nav row."""
    items = "".join(f'<li><a href="{h}"{" class=on" if label == active else ""}>{label}</a></li>' for label, h in NAV)
    return ('<a class="skip" href="#main">Skip to content</a>\n'
            '<header class="site"><nav class="nav wrap" aria-label="Primary">'
            '<a class="brand" href="/">Glass <i>Database</i></a>'
            f'<ul id="menu">{items}</ul>'
            '<div class="right"><span id="chip" class="signin gdb-chip"></span>'
            '<button class="menu-btn" aria-expanded="false" aria-controls="menu">Menu</button></div>'
            '</nav></header>\n'
            f'<div class="subnav wrap"><span class="crumb">{html.escape(BLOG_TITLE)}</span>'
            f'<a href="/blog/"{" class=on" if index_on else ""}>All posts</a>'
            '<a href="/blog/feed.xml">RSS</a><a href="/#notes">On the homepage</a></div>')


def _footer() -> str:
    return ('<footer><div class="wrap"><div class="fgrid">'
            '<div><p class="label">Glass Database</p>'
            '<p>A record kept by the field, for the field. Writing and data under CC BY 4.0, '
            'with attribution kept and removal on request; code under Apache 2.0.</p></div>'
            '<div><p class="label">Registry</p><ul><li><a href="/explore/">Explorer</a></li><li><a href="/graph.html">Graph</a></li>'
            '<li><a href="/api/docs">API</a></li><li><a href="/studios.html">Map</a></li><li><a href="/shop-costs.html">Shop costs</a></li></ul></div>'
            '<div><p class="label">Collect</p><ul><li><a href="/register.html">Add a find</a></li><li><a href="/events.html">Events</a></li>'
            '<li><a href="/games.html">Seasons</a></li><li><a href="/fingerprint/enroll.html">Capture app</a></li><li><a href="/glowtbook/">Glowtbook</a></li></ul></div>'
            '<div><p class="label">Elsewhere</p><ul><li><a href="https://discord.gg/9ek2UxvPT">Discord</a></li>'
            '<li><a href="https://www.instagram.com/glassdatabase/">Instagram</a></li>'
            '<li><a href="/blog/">Blog</a> · <a href="/blog/feed.xml">RSS</a></li>'
            '<li><a href="https://github.com/alibama/glass-database">GitHub</a></li><li><a href="/#about">About</a></li></ul></div>'
            '</div><div class="legal"><span>Data CC BY 4.0 · Software Apache 2.0</span><span>glassdatabase.org</span></div>'
            '</div></footer>')


def _subscribe(label: str = "Get new studies by email", uid: str = "em") -> str:
    return (f'<p class="label">{label}</p>'
            '<form class="sign light" data-source="blog" onsubmit="return gdbSubscribe(event)">'
            f'<label for="{uid}" class="skip">Email address</label>'
            f'<input id="{uid}" type="email" name="email" placeholder="you@studio.com" required>'
            '<button type="submit">Subscribe</button></form>'
            '<p class="label" role="status" aria-live="polite" style="margin-top:.6rem"></p>')


def _page(title: str, description: str, body: str, canonical: str, extra_head: str = "", index_on: bool = False) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description, quote=True)}">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<link rel="alternate" type="application/rss+xml" title="{SITE_TITLE} — {BLOG_TITLE}" href="/blog/feed.xml">
{FONTS}
<style>{CSS}</style>{extra_head}
</head>
<body>
{_nav(index_on=index_on)}
<main id="main">
{body}
</main>
{_footer()}
{SUBSCRIBE_JS}
</body>
</html>
"""


def nice_date(d: str) -> str:
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d, %Y") if sys.platform != "win32" \
            else datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y").replace(" 0", " ")
    except ValueError:
        return d


def post_url(p: dict) -> str:
    return f"/blog/{p['slug']}.html"


def render_post(p: dict, prev: dict | None, nxt: dict | None, base: str) -> str:
    meta = [f"<b>{html.escape(p['series'])}</b>" if p["series"] else "", html.escape(nice_date(p["date"])),
            html.escape(p["author"]) if p["author"] else "", f"{p['minutes']} min read"]
    meta_html = " · ".join(x for x in meta if x)
    lede = f'<p class="lede">{html.escape(p["summary"])}</p>' if p["summary"] else ""
    nav = []
    if nxt:
        nav.append(f'<a class="link" href="{post_url(nxt)}">&larr; Newer: {html.escape(nxt["title"])}</a>')
    nav.append('<a class="link" href="/blog/">All posts</a>')
    if prev:
        nav.append(f'<a class="link" href="{post_url(prev)}">Older: {html.escape(prev["title"])} &rarr;</a>')
    body = f"""
  <section class="opener">
    <div class="wrap s-head">
      <p class="label"><span class="n">{html.escape(BLOG_TITLE)}</span><br>{meta_html}</p>
      <div><h1>{html.escape(p['title'])}</h1>{lede}</div>
    </div>
  </section>
  <div class="wrap body-grid">
    <aside aria-label="Post links"><a class="link" href="/blog/">All field notes</a><a class="link" href="/blog/feed.xml">RSS</a>
      <a class="link" href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></aside>
    <div>
      <article class="post">
{render_markdown(p['body_md'])}
      </article>
      <div class="post-foot">
        {_subscribe()}
        <nav class="post-nav" aria-label="Older and newer posts">{"".join(nav)}</nav>
      </div>
    </div>
  </div>"""
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": p["title"],
          "datePublished": p["date"], "author": {"@type": "Organization", "name": p["author"] or SITE_TITLE},
          "description": p["summary"], "url": base + post_url(p), "license": "https://creativecommons.org/licenses/by/4.0/"}
    extra = f'\n<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
    return _page(f"{p['title']} — {SITE_TITLE}", p["summary"] or p["title"], body, base + post_url(p), extra)


def render_index(posts: list[dict], base: str) -> str:
    rows = []
    feature = ""
    if posts:
        p = posts[0]
        meta = " · ".join(x for x in ["Latest", html.escape(p["series"]), html.escape(nice_date(p["date"])),
                                      f"{p['minutes']} min read"] if x)
        feature = (f'<article class="feature"><div><p class="label">{meta}</p>'
                   f'<h3><a href="{post_url(p)}" style="text-decoration:none">{html.escape(p["title"])}</a></h3>'
                   f'<p class="lede">{html.escape(p["summary"])}</p>'
                   f'<div class="btns"><a class="link" href="{post_url(p)}">Read the study</a>'
                   f'<a class="link" href="/blog/feed.xml">RSS</a></div></div>'
                   f'<a class="ph" href="{post_url(p)}" aria-label="{html.escape(p["title"], quote=True)}"></a></article>')
    for p in posts[1:]:
        meta = " · ".join(x for x in [html.escape(p["series"]), f"{p['minutes']} min read"] if x)
        rows.append(f'<li><span>{html.escape(nice_date(p["date"]))}</span><div>'
                    f'<a class="t" href="{post_url(p)}">{html.escape(p["title"])}</a>'
                    f'{("<p>" + html.escape(p["summary"]) + "</p>") if p["summary"] else ""}'
                    f'<p class="label">{meta}</p><a class="link" href="{post_url(p)}">Read</a></div></li>')
    listing = f'<ul class="posts list">{"".join(rows)}</ul>' if rows else ""
    empty = '<p class="lede">Nothing published yet.</p>' if not posts else ""
    body = f"""
  <section class="opener">
    <div class="wrap s-head">
      <p class="label"><span class="n">№ —</span> {html.escape(BLOG_TITLE)}</p>
      <div><h2>On the <em>business</em> of glass.</h2>
        <p class="lede">{html.escape(BLOG_TAGLINE)}</p>
        <p>What a hot shop costs to build and run, where the buyers are, what the registry's data says,
          and the tools we're building along the way. Everything under CC BY 4.0.</p>
        {_subscribe()}
      </div>
    </div>
  </section>
  <section class="wrap posts-wrap">{feature}{listing}{empty}</section>"""
    return _page(f"{BLOG_TITLE} — {SITE_TITLE}", BLOG_TAGLINE, body, base + "/blog/", index_on=True)


def render_feed(posts: list[dict], base: str) -> str:
    def rfc(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).strftime("%a, %d %b %Y 00:00:00 +0000")
        except ValueError:
            return d
    items = []
    for p in posts:
        items.append(f"""  <item>
    <title>{html.escape(p['title'])}</title>
    <link>{base}{post_url(p)}</link>
    <guid isPermaLink="true">{base}{post_url(p)}</guid>
    <pubDate>{rfc(p['date'])}</pubDate>
    <description>{html.escape(p['summary'])}</description>
    <content:encoded><![CDATA[{render_markdown(p['body_md'])}]]></content:encoded>
  </item>""")
    # Deterministic: the newest post's date, not "now". The installer rebuilds the blog
    # on the server, and a changing stamp would dirty a tracked file on every deploy.
    now = rfc(posts[0]["date"]) if posts else "Thu, 01 Jan 2026 00:00:00 +0000"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>{SITE_TITLE} — {BLOG_TITLE}</title>
  <link>{base}/blog/</link>
  <atom:link href="{base}/blog/feed.xml" rel="self" type="application/rss+xml"/>
  <description>{html.escape(BLOG_TAGLINE)}</description>
  <language>en</language>
  <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>
"""


def render_landing_block(posts: list[dict]) -> str:
    """The 'Field notes' block on the homepage (paper theme): the latest post as the
    feature article, the next two as list rows. Markup matches landing-index.html."""
    if not posts:
        return '<p class="lede">First study coming soon.</p>'
    p = posts[0]
    meta = " · ".join(x for x in ["Latest", html.escape(p["series"]), html.escape(nice_date(p["date"])),
                                  f"{p['minutes']} min read"] if x)
    out = [f'<article class="feature">'
           f'<div><p class="label">{meta}</p><h3>{html.escape(p["title"])}</h3>'
           f'<p class="lede">{html.escape(p["summary"])}</p>'
           f'<div class="btns"><a class="link" href="{post_url(p)}">Read the study</a>'
           f'<a class="link" href="/blog/">All field notes</a><a class="link" href="/blog/feed.xml">RSS</a></div></div>'
           f'<a class="ph" href="{post_url(p)}" aria-label="{html.escape(p["title"], quote=True)}"></a></article>']
    rest = posts[1:3]
    if rest:
        rows = "".join(f'<li><span>{html.escape(nice_date(q["date"]))}</span>'
                       f'<a href="{post_url(q)}" style="text-decoration:none">{html.escape(q["title"])}</a>'
                       f'{(" — " + html.escape(q["summary"])) if q["summary"] else ""}</li>' for q in rest)
        out.append(f'<ul class="list" style="margin-top:2rem">{rows}</ul>')
    return "\n".join(out)


def update_landing(posts: list[dict], landing: Path = LANDING) -> bool:
    if not landing.exists():
        return False
    txt = landing.read_text(encoding="utf-8")
    start, end = "<!-- blog:latest -->", "<!-- /blog:latest -->"
    a, b = txt.find(start), txt.find(end)
    if a == -1 or b == -1 or b < a:
        return False
    new = txt[:a + len(start)] + "\n" + render_landing_block(posts) + "\n      " + txt[b:]
    if new != txt:
        landing.write_text(new, encoding="utf-8")
    return True


def load_posts(src: Path, include_drafts: bool = False) -> list[dict]:
    posts = [parse_post(p) for p in sorted(src.glob("*.md"))]
    posts = [p for p in posts if include_drafts or not p["draft"]]
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def build(src: Path = POSTS, out: Path = OUT, base: str = "https://glassdatabase.org",
          landing: Path | None = LANDING, include_drafts: bool = False) -> dict:
    base = base.rstrip("/")
    posts = load_posts(src, include_drafts)
    out.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(posts):
        nxt = posts[i - 1] if i > 0 else None          # newer
        prev = posts[i + 1] if i + 1 < len(posts) else None  # older
        (out / f"{p['slug']}.html").write_text(render_post(p, prev, nxt, base), encoding="utf-8")
    (out / "index.html").write_text(render_index(posts, base), encoding="utf-8")
    (out / "feed.xml").write_text(render_feed(posts, base), encoding="utf-8")
    listing = [{k: p[k] for k in ("slug", "title", "date", "author", "series", "summary", "tags", "minutes")}
               | {"url": post_url(p)} for p in posts]
    (out / "posts.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False), encoding="utf-8")
    landed = update_landing(posts, landing) if landing else False
    return {"posts": len(posts), "out": str(out), "landing_updated": landed,
            "slugs": [p["slug"] for p in posts]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the blog (Markdown -> static pages).")
    ap.add_argument("--src", default=str(POSTS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--base", default="https://glassdatabase.org", help="public origin for canonical URLs + feed")
    ap.add_argument("--landing", default=str(LANDING), help="homepage to refresh (or 'none')")
    ap.add_argument("--drafts", action="store_true", help="include posts marked draft: true")
    a = ap.parse_args(argv)
    r = build(Path(a.src), Path(a.out), a.base, None if a.landing == "none" else Path(a.landing), a.drafts)
    print(f"blog: {r['posts']} post(s) -> {r['out']}  (homepage block {'updated' if r['landing_updated'] else 'not touched'})")
    for s in r["slugs"]:
        print("  -", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
