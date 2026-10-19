#!/usr/bin/env python3
"""
Build the blog — Markdown in, Field Data pages out. No dependencies.

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


# --- templates -----------------------------------------------------------------
FONTS = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT,WONK'
         '@0,9..144,100..900,0..100,0..1;1,9..144,100..900,0..100,0..1&family=Archivo:wght@100..900'
         '&family=IBM+Plex+Mono:wght@400;500&display=swap">\n'
         '<link rel="stylesheet" href="/field-data.css">')

NAV = [("Explore", "/explore/"), ("Blog", "/blog/"), ("Glowtbook", "/glowtbook/"), ("Barcoding", "/#barcoding"),
       ("Map", "/studios.html"), ("Shops", "/shop.html"), ("Costs", "/shop-costs.html"), ("Graph", "/graph.html"),
       ("Seasons", "/games.html"), ("API", "/api/docs"), ("Discord", "https://discord.gg/9ek2UxvPT")]

CSS = """
  html { scroll-behavior: smooth; } body { margin: 0; }
  .fd-page { max-width: 1280px; margin-inline: auto; padding-bottom: 7rem; }
  .fd-nav { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
    padding: 1.4rem 0 1.1rem; flex-wrap: wrap; border-bottom: 1px solid var(--fd-rule); }
  .fd-nav__brand { font-family: var(--fd-mono); font-weight: 500; letter-spacing: .12em; text-transform: uppercase;
    color: var(--fd-bone); font-size: clamp(13px,1.5vw,15px); text-decoration: none; }
  .fd-nav__links { display: flex; gap: clamp(.9rem, 2.2vw, 1.8rem); flex-wrap: wrap; }
  .fd-nav__links a { font-family: var(--fd-mono); font-size: clamp(12px,1.3vw,14px); letter-spacing: .1em;
    text-transform: uppercase; color: var(--fd-dim); text-decoration: none; }
  .fd-nav__links a:hover, .fd-nav__links a.on { color: var(--fd-amber); }
  .fd-sec { padding: clamp(3rem, 7vw, 5.5rem) 0 0; }
  .fd-sec .fd-eyebrow { display: inline-block; border-bottom: none; padding-bottom: 0; }
  .fd-kicker-rule { height: 1px; background: var(--fd-rule-soft); margin: .9rem 0 1.8rem; max-width: 62ch; }
  .meta { font-family: var(--fd-mono); font-size: clamp(11px,1.2vw,13px); letter-spacing: .08em; text-transform: uppercase;
    color: var(--fd-faint); display: flex; gap: .4rem 1rem; flex-wrap: wrap; margin: 1rem 0 0; }
  .meta b { color: var(--fd-amber); font-weight: 500; }
  .lede { font-family: var(--fd-sans); font-variation-settings: "wght" 500; color: var(--fd-bone);
    font-size: clamp(18px,2vw,22px); line-height: 1.5; max-width: 62ch; margin: 1.4rem 0 0; }
  article.post { max-width: 76ch; margin-top: 2.6rem; font-family: var(--fd-sans); color: var(--fd-dim);
    font-size: clamp(16px,1.75vw,18.5px); line-height: 1.62; }
  article.post p { margin: 0 0 1.15em; }
  article.post h2 { font-family: var(--fd-serif); font-variation-settings: "opsz" 44, "wght" 340; font-weight: 300;
    color: var(--fd-bone); font-size: clamp(28px,3.4vw,38px); line-height: 1.15; margin: 2.6em 0 .6em; letter-spacing: -.005em; }
  article.post h3 { font-family: var(--fd-serif); font-variation-settings: "opsz" 40, "wght" 400; font-weight: 400;
    color: var(--fd-bone); font-size: clamp(22px,2.4vw,26px); margin: 2em 0 .5em; }
  article.post h2:first-child { margin-top: 0; }
  article.post strong { color: var(--fd-bone); font-weight: 600; }
  article.post em { font-style: italic; color: var(--fd-bone); }
  article.post a { color: var(--fd-amber); } article.post a:hover { color: var(--fd-teal); }
  article.post code { font-family: var(--fd-mono); font-size: .9em; color: var(--fd-teal); }
  article.post ul, article.post ol { padding-left: 1.3rem; margin: 0 0 1.2em; }
  article.post li { margin: .35em 0; }
  article.post li::marker { color: var(--fd-amber); font-family: var(--fd-mono); }
  article.post blockquote { border-left: 2px solid var(--fd-amber); margin: 1.4em 0; padding: .2em 0 .2em 1.1em;
    color: var(--fd-bone); font-variation-settings: "wght" 420; }
  article.post hr { border: 0; height: 1px; background: var(--fd-rule); margin: 2.4em 0; }
  .tbl { overflow-x: auto; margin: 1.4em 0 1.8em; border: 1px solid var(--fd-rule); border-radius: 14px;
    background: linear-gradient(180deg, rgba(238,231,219,.025), rgba(238,231,219,0)); }
  .tbl table { border-collapse: collapse; width: 100%; min-width: 560px; font-size: .92em; line-height: 1.45; }
  .tbl th { font-family: var(--fd-mono); font-weight: 500; font-size: .74em; letter-spacing: .1em; text-transform: uppercase;
    color: var(--fd-amber); text-align: left; padding: .9rem 1rem .7rem; border-bottom: 1px solid var(--fd-rule-soft);
    background: rgba(10,13,17,.5); white-space: nowrap; }
  .tbl td { padding: .75rem 1rem; vertical-align: top; border-bottom: 1px solid var(--fd-rule); color: var(--fd-dim); }
  .tbl tr:last-child td { border-bottom: 0; }
  .tbl td:first-child { color: var(--fd-bone); font-variation-settings: "wght" 520; }
  .tbl td strong { color: var(--fd-bone); }
  .post-foot { max-width: 76ch; margin-top: 3.5rem; padding-top: 1.6rem; border-top: 1px solid var(--fd-rule); }
  .post-nav { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-top: 1.2rem; }
  .post-nav a { font-family: var(--fd-mono); font-size: clamp(11px,1.2vw,13px); letter-spacing: .1em; text-transform: uppercase;
    color: var(--fd-dim); text-decoration: none; } .post-nav a:hover { color: var(--fd-amber); }
  .fd-signup { display: flex; gap: .6rem; flex-wrap: wrap; max-width: 34rem; margin-top: 1.2rem; }
  .fd-signup input { flex: 1 1 15rem; padding: .8rem 1rem; border-radius: 999px; border: 1.5px solid var(--fd-rule-soft);
    background: rgba(238,231,219,.04); color: var(--fd-bone); font-family: var(--fd-sans); font-size: 1rem; }
  .fd-signup button { font-family: var(--fd-mono); font-weight: 500; letter-spacing: .16em; text-transform: uppercase;
    font-size: clamp(12px,1.4vw,14px); cursor: pointer; padding: 0 1.4rem; border-radius: 999px; border: 0;
    background: var(--fd-amber); color: var(--fd-bg-top); }
  .fd-signup-msg { font-family: var(--fd-mono); font-size: 12px; letter-spacing: .06em; color: var(--fd-amber); min-height: 1.2rem; margin: .6rem 0 0; }
  .posts { display: grid; gap: 1.1rem; margin-top: 2.2rem; }
  .post-card { display: block; text-decoration: none; padding: 1.7rem 1.6rem; border-radius: 16px; border: 1px solid var(--fd-rule);
    background: linear-gradient(180deg, rgba(238,231,219,.03), rgba(238,231,219,0)); transition: border-color .18s, transform .18s; }
  .post-card:hover { border-color: var(--fd-amber); transform: translateY(-2px); }
  .post-card h2 { font-family: var(--fd-serif); font-variation-settings: "opsz" 44, "wght" 340; font-weight: 300; color: var(--fd-bone);
    font-size: clamp(26px,3.2vw,36px); margin: .5rem 0 .6rem; line-height: 1.15; }
  .post-card p { font-family: var(--fd-sans); color: var(--fd-dim); font-size: clamp(15px,1.7vw,17.5px); line-height: 1.55; margin: 0 0 1rem; max-width: 70ch; }
  .post-card .fd-go { font-family: var(--fd-mono); font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--fd-amber); }
  .fd-footer { margin-top: clamp(4rem, 9vw, 7rem); padding-top: 2rem; border-top: 1px solid var(--fd-rule); display: flex; flex-wrap: wrap;
    gap: 1rem 2rem; justify-content: space-between; font-family: var(--fd-mono); font-size: 12px; letter-spacing: .06em; color: var(--fd-faint); }
  .fd-footer a { color: var(--fd-dim); text-decoration: none; } .fd-footer a:hover { color: var(--fd-amber); }
"""

SUBSCRIBE_JS = """<script>
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
</script>"""


def _nav(active: str = "Blog") -> str:
    links = "".join(f'<a href="{h}"{" class=on" if label == active else ""}>{label}</a>' for label, h in NAV)
    return (f'<nav class="fd-nav"><a class="fd-nav__brand" href="/">Glass&nbsp;Database</a>'
            f'<div class="fd-nav__links">{links}</div></nav>')


def _footer() -> str:
    return ('<footer class="fd-footer"><p>Writing under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
            ' · <a href="/blog/feed.xml">RSS</a></p><p><a href="/">Home</a> · <a href="/explore/">Explore</a> · '
            '<a href="/glowtbook/">Glowtbook</a> · <a href="/games.html">Seasons</a> · '
            '<a href="https://github.com/alibama/glass-database">GitHub</a></p></footer>')


def _subscribe(label: str = "Get new studies by email") -> str:
    return (f'<p class="fd-label" style="color:var(--fd-amber)">{label}</p>'
            '<form class="fd-signup" onsubmit="return gdbSubscribe(event)">'
            '<input type="email" name="email" placeholder="your@email.com" aria-label="Email address" required>'
            '<button type="submit">Subscribe</button></form><p class="fd-signup-msg" role="status" aria-live="polite"></p>')


def _page(title: str, description: str, body: str, canonical: str, extra_head: str = "") -> str:
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
<body class="fd-ground">
<div class="fd-page">
{_nav()}
{body}
{_footer()}
</div>
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
        nav.append(f'<a href="{post_url(nxt)}">&larr; Newer: {html.escape(nxt["title"])}</a>')
    nav.append('<a href="/blog/">All posts</a>')
    if prev:
        nav.append(f'<a href="{post_url(prev)}">Older: {html.escape(prev["title"])} &rarr;</a>')
    body = f"""
  <section class="fd-sec" style="padding-top:2.4rem">
    <p class="fd-eyebrow">{html.escape(BLOG_TITLE)}</p>
    <div class="fd-kicker-rule"></div>
    <h1 class="fd-headline" style="max-width:22ch">{html.escape(p['title'])}</h1>
    <p class="meta">{meta_html}</p>
    {lede}
  </section>
  <article class="post">
{render_markdown(p['body_md'])}
  </article>
  <div class="post-foot">
    {_subscribe()}
    <div class="post-nav">{"".join(nav)}</div>
  </div>"""
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": p["title"],
          "datePublished": p["date"], "author": {"@type": "Organization", "name": p["author"] or SITE_TITLE},
          "description": p["summary"], "url": base + post_url(p), "license": "https://creativecommons.org/licenses/by/4.0/"}
    extra = f'\n<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
    return _page(f"{p['title']} — {SITE_TITLE}", p["summary"] or p["title"], body, base + post_url(p), extra)


def render_index(posts: list[dict], base: str) -> str:
    cards = []
    for i, p in enumerate(posts):
        tag = "Latest" if i == 0 else ""
        meta = " · ".join(x for x in [tag, html.escape(p["series"]), html.escape(nice_date(p["date"])),
                                      f"{p['minutes']} min"] if x)
        cards.append(f'<a class="post-card" href="{post_url(p)}"><span class="fd-label" style="color:var(--fd-amber)">{meta}</span>'
                     f'<h2>{html.escape(p["title"])}</h2><p>{html.escape(p["summary"])}</p>'
                     f'<span class="fd-go">Read &rarr;</span></a>')
    empty = '<p class="fd-body" style="margin-top:2rem">Nothing published yet.</p>' if not posts else ""
    body = f"""
  <section class="fd-sec" style="padding-top:2.4rem">
    <p class="fd-eyebrow">Blog</p>
    <div class="fd-kicker-rule"></div>
    <h1 class="fd-headline">{html.escape(BLOG_TITLE)} on the <em>business of glass.</em></h1>
    <p class="fd-body fd-lede" style="margin-top:1.4rem;max-width:62ch">{html.escape(BLOG_TAGLINE)}</p>
    <p class="fd-body" style="margin-top:1rem;max-width:62ch">What a hot shop costs to build and run, where the buyers are,
      what the registry's data says, and the tools we're building along the way. Everything under CC BY 4.0.</p>
    {_subscribe()}
  </section>
  <div class="posts">{"".join(cards)}{empty}</div>"""
    return _page(f"{BLOG_TITLE} — {SITE_TITLE}", BLOG_TAGLINE, body, base + "/blog/")


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
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
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
    """The 'From the blog' block on the homepage: the latest post as a feature card,
    the next two as index rows. Markup matches landing-index.html's classes."""
    if not posts:
        return '<p class="fd-body" style="margin-top:1.6rem">First post coming soon.</p>'
    p = posts[0]
    meta = " · ".join(x for x in ["Latest", html.escape(p["series"]), html.escape(nice_date(p["date"])),
                                  f"{p['minutes']} min read"] if x)
    out = [f'<a class="fd-card fd-card--feature" href="{post_url(p)}">'
           f'<span class="fd-label" style="color:var(--fd-amber)">{meta}</span>'
           f'<h3>{html.escape(p["title"])}</h3><p>{html.escape(p["summary"])}</p>'
           f'<span class="fd-go">Read the study &rarr;</span></a>']
    rest = posts[1:3]
    if rest:
        rows = "".join(f'<div class="fd-row"><span class="fd-label">{html.escape(nice_date(q["date"]))}</span>'
                       f'<p><a href="{post_url(q)}">{html.escape(q["title"])}</a>'
                       f'{(" — " + html.escape(q["summary"])) if q["summary"] else ""}</p></div>' for q in rest)
        out.append(f'<div class="fd-rows" style="margin-top:1.2rem">{rows}</div>')
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
