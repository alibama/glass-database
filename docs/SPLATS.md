People see this as **3D Photo** (see docs/NAMING.md); the code and URLs keep the old words.

# 3D views (Gaussian splats) — prototype

A **Gaussian splat** is a 3D photograph: a cloud of soft, coloured blobs trained from a
set of photos, rendered from any angle in real time. It keeps the view-dependent shine
and colour shifts that make glass hard to photograph and impossible to mesh. Makers want
to share their pieces turning; this prototype lets them, without us running a GPU.

Page: `/splat.html` · API: `api/splats.py` · demo: `scripts/make_demo_splat.py` →
`deploy/splat/demo-pumpkin.splat` · tests: `tests/test_splats.py`.

## The loop

1. **The maker trains the splat themselves.** On a phone, [Scaniverse](https://scaniverse.com/)
   (free, trains on-device, exports *Gaussian Splat → PLY*) or Polycam; on a laptop,
   [Brush](https://github.com/ArthurBrussee/brush) (Apache 2.0, WebGPU) or
   [OpenSplat](https://github.com/pierotofy/OpenSplat) (AGPL) from a folder of photos.
   Avoid the original Inria `gaussian-splatting` code: non-commercial research licence.
2. **Crop it** in [SuperSplat](https://superspl.at/editor) (MIT, in the browser): delete the
   table and the room, keep the piece, export PLY or SPLAT. Under 150 MB uploads; under
   30 MB loads fast on a phone.
3. **Upload** on `/splat.html`: sign in, pick the piece in your Glowtbook, upload. One splat
   per piece; uploading again replaces it. The response carries a share link
   `/splat.html?s=<id>` that anyone can open.

## What the site does

| Route | Who | What |
|---|---|---|
| `POST /api/splats?object=<oid>&name=<file>` | signed in, owner of the piece | raw body or multipart `file`; `.splat`, Gaussian `.ply` (plain or compressed), `.ksplat`; 150 MB cap; format sniffed from bytes + filename |
| `GET /api/splats/{id}` | anyone with the link | the file, cached a day |
| `GET /api/splats/{id}/meta` | anyone | format, size, title |
| `GET /api/splats/mine` | signed in | your splats |
| `DELETE /api/splats/{id}` | owner | remove |

Files live in `data/splat_media/` (gitignored, `GLASSDB_SPLAT_DIR` to move them); rows in
the central table `splats`. The viewer is [GaussianSplats3D](https://github.com/mkkellogg/GaussianSplats3D)
(MIT) over three.js, loaded from jsDelivr with `sharedMemoryForWorkers: false` so no
cross-origin-isolation headers are needed. The page frames whatever loads by sampling
splat centres, and has a **Flip** button because phone scans arrive y-down as often as
y-up. "Open a file from this device" previews a local file without uploading.

## What a splat is not

A splat is a picture. It is **never signed into a piece's Content Credentials** and
**never used as a fingerprint**: a good splat can be rendered from any angle, so it could
also be used to *fake* a fingerprint verification against a piece someone doesn't hold.
That is a reason to keep the two apart, and a reason the verify app should eventually
check for the physical mat and parallax (see the legal-review notes). Anyone can upload a
splat of anything; treat it as the maker's own rendering of their work.

## Not done yet

- No conversion or compression on upload (SPZ would be ~10× smaller); no thumbnails.
- Not shown on the public object page or Explore; the share link is the only way in.
- Apache's `LimitRequestBody` on the server may need raising for 150 MB uploads.
- A real capture from a tester, to see how a kitchen-table Scaniverse scan of glass looks.
