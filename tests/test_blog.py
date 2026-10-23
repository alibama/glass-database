"""The blog build: Markdown posts -> pages, feed, listing, homepage block. No dependencies."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import build_blog as B

ROOT = Path(__file__).resolve().parent.parent

POST = """---
title: A Test Study
date: 2026-10-01
author: Glass Database
series: Theoretical studies
summary: One sentence summary.
---

## Premise

Some *emphasis*, **bold**, `code` and a [link](https://example.org).

| Item | Cost ($) |
|---|---|
| Furnace | 55,000 |
| **Total** | **≈ 60,000** |

1. first
2. second

- bullet one
- bullet two

> a quote
"""


def _site(tmp_path, posts):
    src = tmp_path / "posts"; src.mkdir()
    for name, text in posts.items():
        (src / name).write_text(text, encoding="utf-8")
    landing = tmp_path / "index.html"
    landing.write_text("<p>x</p>\n<!-- blog:latest -->\nold\n<!-- /blog:latest -->\n<p>y</p>", encoding="utf-8")
    out = tmp_path / "out"
    r = B.build(src, out, "https://example.org", landing)
    return r, out, landing


def test_markdown_subset():
    h = B.render_markdown(POST.split("---\n", 2)[2])
    assert '<h2 id="premise">Premise</h2>' in h
    assert "<em>emphasis</em>" in h and "<strong>bold</strong>" in h and "<code>code</code>" in h
    assert '<a href="https://example.org">link</a>' in h
    assert '<div class="tbl"><table>' in h and "<th>Cost ($)</th>" in h and "<td>55,000</td>" in h
    assert "<strong>≈ 60,000</strong>" in h and "|---|" not in h
    assert "<ol><li>first</li><li>second</li></ol>" in h
    assert "<ul><li>bullet one</li><li>bullet two</li></ul>" in h
    assert "<blockquote><p>a quote</p></blockquote>" in h
    assert B.render_markdown("<script>x</script> & stuff") .startswith("<script>")  # raw HTML passes
    assert "&amp; stuff" in B.render_markdown("tea & stuff")                        # text is escaped


def test_build_pages_feed_listing_and_homepage(tmp_path):
    r, out, landing = _site(tmp_path, {
        "2026-10-01-a-test-study.md": POST,
        "2026-09-01-older.md": "---\ntitle: Older\nsummary: Old one.\n---\n\nBody.",
        "2026-11-01-draft.md": "---\ntitle: Draft\ndraft: true\n---\n\nHidden.",
    })
    assert r["posts"] == 2 and r["slugs"] == ["a-test-study", "older"] and r["landing_updated"]
    page = (out / "a-test-study.html").read_text(encoding="utf-8")
    assert "<title>A Test Study — Glass Database</title>" in page
    assert 'class="tbl"' in page and "Theoretical studies" in page and "min read" in page
    assert 'href="/blog/older.html"' in page                        # older/newer navigation
    assert 'application/ld+json' in page and '"headline": "A Test Study"' in page
    idx = (out / "index.html").read_text(encoding="utf-8")
    assert idx.index("a-test-study") < idx.index("older.html")      # newest first
    assert "Draft" not in idx and not (out / "draft.html").exists()
    feed = ET.fromstring((out / "feed.xml").read_bytes())
    items = feed.findall("./channel/item")
    assert len(items) == 2 and items[0].find("link").text == "https://example.org/blog/a-test-study.html"
    listing = json.loads((out / "posts.json").read_text(encoding="utf-8"))
    assert listing[0]["url"] == "/blog/a-test-study.html" and listing[0]["minutes"] >= 1
    home = landing.read_text(encoding="utf-8")
    block = home.split("<!-- blog:latest -->")[1].split("<!-- /blog:latest -->")[0]
    assert "\nold\n" not in block                                  # the placeholder line was replaced
    assert 'class="feature"' in home and "A Test Study" in home and "older.html" in home
    assert home.startswith("<p>x</p>") and home.rstrip().endswith("<p>y</p>")


def test_drafts_flag_and_no_landing(tmp_path):
    src = tmp_path / "p"; src.mkdir()
    (src / "2026-01-01-d.md").write_text("---\ntitle: D\ndraft: yes\n---\n\nx", encoding="utf-8")
    r = B.build(src, tmp_path / "o", "https://x.org", None, include_drafts=True)
    assert r["posts"] == 1 and r["landing_updated"] is False


def test_repo_blog_builds_and_lima_is_featured(tmp_path):
    """The real posts render, and the homepage block in the repo names the latest post."""
    out = tmp_path / "blog"
    r = B.build(B.POSTS, out, "https://glassdatabase.org", None)
    assert r["posts"] >= 1 and (out / "blowing-glass-in-lima.html").exists()
    lima = (out / "blowing-glass-in-lima.html").read_text(encoding="utf-8")
    assert lima.count('<div class="tbl">') >= 7 and "Chorrillos" in lima
    home = (ROOT / "deploy" / "landing-index.html").read_text(encoding="utf-8")
    block = home.split("<!-- blog:latest -->")[1].split("<!-- /blog:latest -->")[0]
    assert "/blog/blowing-glass-in-lima.html" in block


def test_build_is_deterministic(tmp_path):
    """The installer rebuilds on the server; identical sources must give identical bytes,
    or every deploy dirties tracked files (feed.xml) and blocks the next git checkout."""
    src = tmp_path / "p"; src.mkdir()
    (src / "2026-10-01-a-test-study.md").write_text(POST, encoding="utf-8")
    a, b = tmp_path / "a", tmp_path / "b"
    B.build(src, a, "https://x.org", None)
    B.build(src, b, "https://x.org", None)
    for name in ("feed.xml", "index.html", "a-test-study.html", "posts.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
