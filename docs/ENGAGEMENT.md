People see this as **Find Artists & Studios** and **Add to My Collection** (see docs/NAMING.md); the code and URLs keep the old words.

# Getting people in: the profile push, claiming listings, and the CMS question

*Design note, October 2026. Nothing here is built yet except where marked.*

## What we have

- A directory of artists and studios ingested from spreadsheets, most rows with a
  private email column (withheld from the API and Explore by the privacy patterns).
- Google sign-in in Glowtbook and, for the static pages, through the API; a `users`
  ledger of who has signed in.
- Community intake forms (artist, studio, resource, exchange, job, shop, lineage),
  events with buyer registration, Seasons, the capture app (alpha), the blog.
- No outbound email at all. Notifications go to Discord.

The push you want — "log in and update your profile and your shop" — needs two
things the site doesn't have: a way to **claim** an existing row as yours, and a
way to **reach** the people in those rows.

## Claiming a listing

The row already knows the email. Sign-in already proves an email. So claiming is a
match, not a form:

1. **Automatic claim on sign-in.** When someone signs in (Glowtbook or the API) and
   their Google email equals the private `email` of one or more directory rows, those
   rows are offered to them: "Is this you? *Rae Sutter · Crozet, VA*." Accepting
   links `users.email` to the row (`claims(tbl, row_id, uid, claimed_at, verified_by
   = 'email-match')`). No token needed, no email needed; it works the moment they
   log in.
2. **Edit in place.** A claimed row becomes editable in Glowtbook → **My listings**:
   the same intake form, pre-filled, writing a *pending edit* that an admin approves
   (the publication gate already exists; edits go through it like everything else).
   Studios the same way, including the map pin and the open-data source.
3. **Email mismatch fallback.** If they signed in with a different address, a
   "claim this listing" button on the Explore row sends a one-time link to the email
   on file (this is the only place a token is needed). Clicking it from any device
   completes the claim.
4. **Admin override.** Admin → Users can attach a listing to a person by hand.

Everything above is small: one table, one match query at login, one Glowtbook
page, one admin widget. It reuses the intake forms and the gate.

## Reaching them

You have the addresses; you do not have a sender. Options, cheapest first:

- **A transactional provider** (Postmark, Mailgun, SES): a few dollars a month, an
  API key in `.env`, and a `central/mail.py` with `send(to, subject, text, html)`.
  Needed anyway for claim links and, later, event and season notifications.
- **A newsletter tool** (Buttondown, Mailchimp) for the campaign itself: import the
  list, write the "come update your listing" note, get unsubscribe handling and
  bounce management for free. The newsletter signups already collected on the
  homepage belong there too.

Consent and tone: these people gave an email to be listed, not to be marketed to.
One honest message that says what changed, why updating helps them (their shop on
the map, their pieces with provenance, events they can host), with a one-click
unsubscribe, is fine. A drip sequence is not. Send from a person, not "noreply".

Sequencing that works with the sign-in flow: the email says "sign in with the
Google account for *this* address and your listing will be waiting"; the automatic
claim does the rest. For people whose Google account is a different address, the
fallback link in the same email carries a claim token for that row.

## Drupal, or keep going?

**Keep going, and API-ize what's missing.** Reasons:

- The hard parts of this project are not content management: provenance manifests,
  C2PA signing, fingerprints, the approval gate, the relationship graph, events and
  seasons. A CMS gives you none of them; you'd write them again as modules, in PHP,
  against a second data model, and keep two systems in sync.
- The data layer is already the right shape for the future you describe: one SQLite
  (or Turso) store, a self-describing registry of datasets, and a read API with
  write endpoints growing where they're needed (me/objects, events, seasons,
  claims). That is what "future-proof" means here. Any front end — a native app,
  a partner museum's site, a future CMS — can sit on it.
- What a CMS would actually buy you is *non-developer page editing*. You already
  have most of that for the blog (Markdown in, pages out). Extend the same generator
  to a `pages/` folder for About, For institutions, Accessibility and Privacy, and a
  collaborator can edit those on GitHub with no tooling.

What to add instead of a CMS, in order:

1. **Claims + My listings** (above). This is the profile push.
2. **Outbound mail** module and the campaign.
3. **Write endpoints for the intake forms** (`POST /api/submit/{form}`), so
   listings can be added or edited from any client, not only the Streamlit page.
4. **Markdown pages** through the blog generator.
5. **A proper account page** in Glowtbook: my listings, my pieces, my events, my
   collection, my votes — one place that answers "what's mine here".

If, a year from now, editorial volume outgrows Markdown-on-GitHub, put a headless
CMS *beside* the API for prose only (blog and pages), fed into the same static
build. Never for the registry data.

## Alpha status for the barcode

The fingerprint work is labelled **alpha — for testers** on the homepage, in the
capture and verify apps and in Glowtbook, with a tester signup (newsletter list,
source `alpha-testers`, exportable from Admin). The protocol is sound; what's
missing is calibration data from real pieces and real siblings, which is exactly
what testers produce. Graduation criteria: a labelled set of same-piece pairs and
hard negatives, published false-accept / false-reject rates, and thresholds set
from that data rather than by hand.
