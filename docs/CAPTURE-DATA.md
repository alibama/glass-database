People see this as **Glass ID** (see docs/NAMING.md); the code and URLs keep the old words.

# Capture data: getting it out, re-running it, improving it

*Companion to [FINGERPRINT-WORKFLOW.md](FINGERPRINT-WORKFLOW.md) (the process) and
[GLASS-ID-MATCHING-ASSESSMENT.md](GLASS-ID-MATCHING-ASSESSMENT.md) (what the data is for).
This page is the loop for fixing the matching and the Photo Mat measurement with real
captures instead of guesses.*

## What gets kept

A **capture session** is what a capture page sends to `POST /api/me/captures`: a zip
with `manifest.json` and `images/` (the 384 px frame thumbnails and the full-size best
shot). It is sent

- when someone saves a Glass ID with *Share this capture to improve Glass ID* ticked
  (the default; enroll.html), and
- whenever someone taps *Send for analysis* (enroll.html and verify.html).

The server keeps it in `GLASSDB_CAPTURES_DIR` (default `data/capture_manifests/<id>/`)
and indexes it in the `capture_manifests` table. On arrival it adds a `server` block to
the manifest, including `server.mat`: for each picture checked, the markers seen, the
scale and the piece's outline on the mat (the reworked measurement adds the camera's
height and elevation), plus the
session's measured W×D×H (`server.mat.dimensions`). Nothing here is public.

The manifest also carries what the page computed: every frame's colour histogram,
dHash and (when on) DINOv2 vector, the rating, the candidates and their scores, the
person's decision, the device and the page's thresholds (`params`).

## Getting it out

All routes need an administrator (signed in, or `X-API-Key: $GLASSDB_ADMIN_TOKEN`).

| Route | What you get |
|---|---|
| `GET /api/admin/captures/export.jsonl` | Every session, one JSON object per line, newest first: the index row, the whole manifest, the mat report, a URL for every stored picture and the zip. `?kind=enroll\|verify`, `?since=YYYY-MM-DD`, `?descriptors=false` (drop the per-frame vectors), `?limit=` |
| `GET /api/admin/captures` | The list the admin page shows (filters `kind`, `decision`, `q`, `since`) |
| `GET /api/admin/captures/{id}` | One session with its manifest and file list |
| `GET /api/admin/captures/{id}/download` | The whole session as a zip |
| `GET /api/admin/captures/{id}/files/images/…` | One picture |
| `POST /api/admin/captures/{id}/rerun` | The mat measurement re-run with the **deployed** code: `stored` (at arrival) beside `now`. `?save=true` appends `now` to `server.mat_reruns` |
| `POST /api/admin/captures/rerun` | The same for many: `{"ids": [...]}` or `{"kind": "enroll", "since": "2026-11-01", "limit": 50, "save": false}` |

```bash
curl -H "X-API-Key: $GLASSDB_ADMIN_TOKEN" \
  "https://glassdatabase.org/api/admin/captures/export.jsonl?kind=enroll&since=2026-11-01" > captures.jsonl
jq -c '{id: .manifest_id, dims: .mat.dimensions, frames: .mat.with_mat}' captures.jsonl
```

## Re-running it with your own code

```bash
python -m scripts.pull_capture --all --since 2026-11-01   # sessions -> data/pulled_captures/
# edit glowtbook/matdetect.py (or central/glassid.py)
python -m scripts.reprocess_captures data/pulled_captures            # recorded vs now, per session
python -m scripts.reprocess_captures data/pulled_captures --frames   # and per picture
python -m scripts.reprocess_captures data/pulled_captures/cap-… --truth 80,80,80   # error vs a tape measure
python -m scripts.glassid_bench --manifests data/pulled_captures     # matching: every session against every other
```

When a change is deployed, `POST /api/admin/captures/rerun {"since": …, "save": true}`
records the new numbers beside the old in each manifest, so the history of a session's
measurement stays with it.

## Making it more useful

The single most useful thing a tester can add is **the real size**: measure the piece
with a ruler and put it in the session note (*Send for analysis* → note, e.g.
`true 80x80x80`). With that, `--truth` turns every session into a test case.
