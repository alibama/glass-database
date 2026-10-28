# Sharing — how a piece, a collection or a 3D Photo gets out of the site

Every piece on the site should be easy to show off: a card people can post, a link whose
preview shows that card, and, for people who connect an account, a button that posts for
them. This note says what the Share buttons do for people and how the kit is wired for
developers. Names follow `docs/NAMING.md`.

## For people

### Where the Share buttons are

| Page | What you can share |
|---|---|
| Collecting Events (`/games.html`) | every entry card, and every piece in My collection that is in a collecting event |
| My collection (`/games.html#mine`, Glowtbook → My profile) | the whole collection, once you make it public |
| A public collection (`/collection.html?c=…`) | that collection |
| Add to My Collection (`/register.html`) | a piece you added, once the maker has confirmed it and it is published |
| 3D Photo (`/splat.html`) | a 3D Photo you uploaded, or one you opened from a link |
| Glowtbook → My pieces → Publish | the piece you just published (a link and the card) |

Press **Share** and a row of buttons opens under the piece:

- **Copy link** copies the share link. Wherever you paste it (Facebook, Bluesky, Discord,
  iMessage, email) the preview shows the card, then the link opens the real page.
- **Save image** downloads the card (a 1080×1350 PNG, the shape Instagram and Bluesky like).
- **Share…** opens your phone's share sheet, with the card attached. Instagram, Messages,
  WhatsApp and the rest appear there. Not shown on browsers without a share sheet.
- **Facebook** opens Facebook's share dialog with the link.
- **Bluesky** posts the card and the text from your connected account. If you have not
  connected one, it offers Bluesky's composer with the text filled in and a link to connect.
- **Instagram** copies the caption and tells you how to post: Instagram has no web posting,
  so on a phone use **Share…** and pick Instagram; on a computer save the image and post it
  from the app.

### Connecting Bluesky

Glowtbook → **My profile** → **Connected accounts**. Enter your handle (`name.bsky.social`)
and an **app password**, not your main password. Make one in Bluesky → Settings → App
Passwords; it can be revoked there at any time and cannot change your account settings. The
site checks it with Bluesky once, then keeps it encrypted and uses it only to post for you
when you press a Share button or tick "Post to Bluesky when I publish". **Post a test** sends
"Testing my Glass Database connection"; **Disconnect** forgets the app password here.

### Public collections

Your collection is private until you say otherwise. **Make my collection public** (on the
Collecting Events page under My collection, or in Glowtbook → My profile) creates a page at
`/collection.html?c=<id>` with an unguessable id: your pieces, their makers, the collecting
events, and your display name, nothing else. Share it like anything else. **Withdraw the
link** takes the page down and the link stops working; making it public again gives you the
same link back.

## For developers

### The kit

`deploy/site.js` exports `GDB.share(el, {kind, ref, title, text, url, image})`:

- `kind` is `entry` | `object` | `collection` | `splat`; `ref` the id of that thing.
- `url` is the real page (relative is fine). `image` is the share-card URL; leave it empty
  and Save image is hidden (3D Photos have no card yet).
- The link handed to networks is the Open Graph landing page
  `/api/social/share/<kind>/<ref>`, which carries `og:*` tags pointing at the card and then
  forwards to `url`.

Pages wrap it in a `Share` button that toggles a `.share-slot` and calls `GDB.share` the
first time it opens (see `wireShare` in `games.html` and `register.html`).

### Routes (`api/social.py`)

- `GET /api/social/status` — what is connected, no secrets.
- `POST /api/social/bluesky/connect {handle, app_password}`, `DELETE /api/social/bluesky`.
- `POST /api/social/bluesky/post {text, image?, alt?, links?}` — `image` is `entry:<id>`,
  `object:<row_id>`, `photo:<row_id>` or `collection`; 401 when not signed in, 409 when no
  account is connected (the kit turns both into a link to Bluesky's composer).
- `GET /api/social/shares` — what I shared lately (`share_log`).
- `POST /api/social/collections/share {name}` → `{share_id, url}`;
  `DELETE /api/social/collections/share`; `GET /api/social/collections/mine`;
  `GET /api/social/collections/{share_id}` (public JSON);
  `GET /api/social/collections/{share_id}/card.png`.
- `GET /api/social/objects/{row_id}/card.png` — the card for a published piece. Entry cards
  stay at `/api/seasons/entries/{id}/card.png`.
- `GET /api/social/share/{kind}/{ref}` — the Open Graph landing page.

Cards are drawn by `central/cards.py` (`render_piece`, `render_grid`); `cards.to_jpeg` makes
the under-1 MB JPEG Bluesky accepts. The Streamlit apps call the Python side directly:
`central/social.py` (`status`, `connect_bluesky`, `disconnect`, `credentials`,
`share_collection`, `unshare_collection`, `my_collection_share`, `log_share`) and
`glowtbook/bluesky.py` (`verify_login`, `post`).

### Where secrets live

Connected accounts sit in the central store's `social_accounts` table, one row per
(user, provider). The app password is encrypted at rest with Fernet; the key is derived
(SHA-256) from `GLASSDB_SOCIAL_KEY`, or from the session secret (`GLASSDB_SESSION_SECRET`)
when that is unset. Rotating the key means people reconnect. `status` never returns the
secret; only `credentials()` decrypts, at post time. Public collections are rows in
`collection_shares` (an 8-byte url-safe token); `share_log` keeps kind, ref, url and time,
never content.
