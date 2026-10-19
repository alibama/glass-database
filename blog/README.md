# The blog

Markdown in, Field Data pages out, no dependencies. Posts live in `blog/posts/`;
`python -m scripts.build_blog` (or `make build-blog`) renders them into
`deploy/blog/` — an index, one page per post, an RSS feed (`feed.xml`) and a
`posts.json` — and refreshes the "From the blog" block on the homepage so the
latest post is always featured there. `deploy/install.sh` runs the build before
it copies the static pages, so publishing is: add a file, commit, deploy.

## Add a post

Create `blog/posts/YYYY-MM-DD-some-slug.md`:

```markdown
---
title: Blowing Glass in Lima
date: 2026-10-17
author: Glass Database
series: Theoretical studies
summary: One sentence for the index, the homepage card and the feed.
---

## First heading

Body in Markdown. Headings (`##`, `###`), paragraphs, **bold**, *italic*,
[links](https://…), `code`, bullet and numbered lists, block quotes, `---`
rules, and pipe tables:

| Column | Column |
|---|---|
| cell | cell |

Raw HTML lines (starting with `<`) pass straight through if you need them.
```

- The file name's date prefix becomes the date if `date:` is missing; the slug
  comes from the rest of the file name unless you set `slug:`.
- `draft: true` keeps a post out of the build (pass `--drafts` to preview it).
- `series:` is shown as the amber tag; `tags:` is a comma list kept in
  `posts.json` for later.
- Keep the opening paragraph strong: it is what the homepage card and the feed
  show after the title.

## Preview locally

```bash
python -m scripts.build_blog
python -m scripts.dev_site      # http://127.0.0.1:8080/blog/
```

Writing is published under CC BY 4.0 like the rest of the registry.
