# Shows & Sales — adding the piece to a collection at the point of sale

On the site these are **Shows & Sales** (the code says events; see `docs/NAMING.md`). A
maker **hosts a show or sale** — an ornament day at a gallery, a pop-up at the market, a
demo with a sale table. Someone who **buys a piece there adds it to their collection**
online, with the piece still in their hand: sign in, say what it is, snap a photo. The host
confirms it was theirs, the site publishes it, and the piece is in the public directory with
provenance from the moment of sale — *created by the maker at the event, acquired by the
buyer* — and in the buyer's own Glowtbook, ready for its Glass ID. The rest of this document
uses the code's words (event, registration, fingerprint) because it describes the code.

Public pages: `/events.html` (directory) · `/register.html?event=<slug>` (the buyer's page,
the one behind the QR) · host side: **Glowtbook → Events** · admin: **Admin → Review queue →
Event registrations** · code: `central/events.py`, `api/events.py`.

## The loop

```
host adds the event ─▶ QR on the table ─▶ buyer registers ─▶ host approves ─▶ site publishes ─▶ buyer fingerprints
   (Glowtbook)          (print it)        (/register.html)    (bulk, Glowtbook)   (admin queue)     (capture app)
```

The event works the moment the host saves it — the link and QR are live before anyone has
reviewed anything. Only the **directory listing** (appearing on `/events.html`) waits for an
admin, like every other public row.

### Why this serves the mission

* **Provenance at the point of sale.** A certificate in a drawer gets lost. A record made the
  afternoon the piece changed hands — who made it, where, when, who took it home — doesn't.
  Published pieces get the same manifest, Content Credential and verify link as everything
  else in the registry.
* **A collector audience.** Registering is a reason to sign in, and the piece in Glowtbook is a
  reason to come back: fingerprint it, see it published, register the next one. That is how
  a maker's buyers become a maker's collectors.
* **The voluntary census.** Every event is a snapshot of real glass meeting real money: which
  studio, which venue, what kind of piece, roughly what it sold for (private), and where it
  went. Open data under CC BY 4.0, with the private fields kept private.

## Two-stage review

The host curates, the site still gates.

| Status | Shown to the buyer as | Meaning | Who moves it |
|---|---|---|---|
| `pending_host` | waiting for the host | Registered; the host hasn't looked yet | host (Glowtbook → Events → approve / reject, in bulk or one by one) |
| `host_approved` | host approved — waiting for the site | The maker says "yes, that was mine" | admin (Review queue → Event registrations) |
| `approved` | published | A public `objects` row exists with provenance, image and (if captured) the fingerprint | — |
| `rejected_host` | not confirmed by the host | Host says it wasn't theirs; nothing is published | — |
| `rejected` | not published | Admin declined (with a note the buyer can see) | — |

Stage one is the host's judgement about their own sale (did I sell this? is the photo of my
piece?). Stage two is the site's usual gate against junk and abuse. Approving at stage two
**publishes**: `publish_registration` writes an `objects` row (`_source_file=events`,
row id `sha1("evreg|<id>")[:16]`) with two provenance events — `created` by the maker at the
venue, `acquired` by the buyer — the photo as the primary image, and a manifest carrying an
`event` block (slug, title, host, venue, date). It is idempotent per registration.

## Privacy

* **Names are private by default.** The buyer ticks "show my name on the event page" or stays
  "a collector" — on the event gallery, in the published record's `acquired` event and as the
  contributor.
* **Email is never shown publicly** and never lands in the registry. The host sees the buyer's
  email in their review queue (to match a receipt), as does an admin; nobody else.
* **Photos are private until published.** `GET /events/registrations/{id}/image` serves a photo
  only when the registration is `approved`, or to the buyer and the host.
* **Price paid is private** — the buyer, the host and admins see it; it never appears on a
  public page or in the manifest. It exists for the census.
* Hosts can turn **show registrations** off, in which case the public event page shows counts
  but no gallery.

## Running a table (hosts)

1. **Glowtbook → Events → New event.** Title, studio, venue, city, date(s), what you're making
   (`piece_type`), a price note ("$40–$120 at the table"), whether you want pieces
   fingerprinted (`none` / `optional` / `all`), and optionally a registration window.
2. **Print the link and its QR.** Glowtbook shows `register_url` — `/register.html?event=<slug>`
   — and the QR for it. Put it on the table, on the tags, on the receipt. It works at once; the
   directory listing follows after review.
3. **During the event, nothing.** Buyers scan, sign in, type a title, take a photo. Each
   registration also creates the piece in *their* Glowtbook.
4. **Afterwards, approve.** Glowtbook → Events → the event lists every registration with photo,
   piece number and the buyer's name/email. *Approve all*, or reject the one that wasn't yours
   (with a note). Approved rows go to the site's queue; you'll see them flip to *published*.
5. **Close it** when you're done (status `closed`, or set `registration_closes`). Closed events
   stay on the directory with their published pieces.

## From registration to Glowtbook piece to fingerprint

`POST /events/{slug}/registrations` does two things in one go:

* creates the registration (`event_registrations`), and
* creates a piece in the **buyer's** Glowtbook — title, maker = host, year = event year,
  with `created` and `acquired` journal events — and links it (`object_id`).

The response carries `fingerprint_url` (`/fingerprint/enroll.html?object=<id>`) and
`glowtbook_url`. The register page shows "Fingerprint it now" right after success, because the
best moment to capture a fingerprint is while the piece is in the buyer's hand on a plain
tablecloth. If they do it later, `GET /me/registrations` keeps the link.

When an admin publishes the registration, the fingerprint captured on the buyer's Glowtbook
piece (if any) is copied into the public record (`manifest.fingerprint`, `fingerprint_json`),
so the published piece is verifiable from day one.

## API

All under `/api`. Public unless marked.

| Call | What |
|---|---|
| `GET /events` | Listed events (admin-approved), newest first. Each: `slug, title, host_name, studio, venue, city, region, country, starts, ends, description, piece_type, price_note, fingerprinting, registration_closes, status, listed, can_register, registered, published` |
| `GET /events/{slug}` | One event — works unlisted (the QR). Adds `pieces[]` (`id, title, buyer, piece_ref, has_image, created_at, object_url?`; approved only, if the host shows them) and `is_host` |
| `POST /events/{slug}/registrations` *(signed in)* | `{title, description?, piece_ref?, price_paid?, image_b64? (data URL ok), show_name?}` → `{id, status:"pending_host", title, object_id, fingerprint_url, glowtbook_url, next, …}`. 400 with `detail` when closed / no title; 401 anonymous; 413 image > 8 MB |
| `GET /me/registrations` *(signed in)* | Your registrations: `event_slug, event_title, host_name, venue, starts, title, piece_ref, status, created_at, image_url?, fingerprint_url?, glowtbook_url?, object_url?` |
| `GET /events/registrations/{id}/image` | The photo (approved, or yours / your event's) |
| `POST /events` *(host)* | Create an event; you are the host; usable at once. Returns `register_url` |
| `POST /events/{slug}` *(host)* | Edit fields, open / close |
| `GET /me/events` *(host)* | Your events with per-status counts |
| `GET /events/{slug}/registrations?status=` *(host)* | The review queue, with emails and image URLs |
| `POST /events/{slug}/review` *(host)* | `{ids:[…], approve:true|false, note?}` or `{all:true, approve:true}` |

Stage two (publishing) lives in the admin console, not in the public API.

## Admin runbook

* **Review queue → Event registrations.** The stage-two queue: every `host_approved` row across
  events, with photo, buyer, host and piece number. *Approve* publishes (object + image +
  provenance, approved through the normal gate); *Reject* records a note the buyer can see.
  A new host approval pings the notification channel ("Host approved registrations — waiting
  for site review").
* **Event listings.** New events arrive as `listing=pending` ("New maker event (listing pending
  review)"). Approve to put the event on `/events.html`; reject to keep it link-only. The host's
  QR works either way.
* **Acting for a host.** `central.events.host_review(conn, event_id, None, ids, True)` approves
  stage one as admin (host_uid `None`) when a host is unreachable.
* **CLI fallbacks** (`GLASSDB_SESSION_SECRET` set, repo root):
  `from central import events as E; from central.dbconn import connect; c = connect()` then
  `E.set_listing(c, <event_id>, "approved")`, `E.pending_admin(c)`,
  `E.admin_review(c, [<reg_id>], True, reviewer="<you>")`, `E.summary(c)`.

## What's next

* **Claim codes on tags.** A short code printed on the price tag that pre-fills the piece and
  lets the host auto-confirm — the buyer types it, stage one is already done.
* **Per-event auto-approval.** A host setting ("approve everything with a photo", "approve
  registrations made during the event hours") so small sales need no review at all.
* **Seasons tie-in.** A piece registered at an event during a season counts as a claim for the
  collector and an entry for the maker — one scan, both boards.
* **CSV export** of an event's registrations for the host's own records and for tax season.
