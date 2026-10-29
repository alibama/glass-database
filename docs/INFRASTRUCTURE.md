# Infrastructure: what runs where, and the private network we are building

The public version of this page is [`/infrastructure.html`](../deploy/infrastructure.html) ("How
it runs"). This file is the operator's version: services, ports, the overlay layout, what is
planned and what is only wanted. Keep the two in step; the web page says *today / being built /
wanted*, and so should this one.

Last reviewed: 29 October 2026.

## 1. Today

One DigitalOcean droplet serves everything public. It also hosts the NetBird control plane that
the Lexipedia project already uses; the two share the box, not the application.

| Piece | Runs as | Listens on | Notes |
|---|---|---|---|
| Apache | system service | 80/443 | TLS, the static pages under `/opt/glassdatabase/public`, reverse proxy to the three Streamlit apps and the API (`deploy/apache-glassdatabase-proxy.conf`) |
| API | `glassdb-api.service` (uvicorn, `api.main:app`) | 127.0.0.1 only | `/api/*`; auth, me, seasons, events, graph, splats, social |
| Glowtbook / Explore / Admin | `glassdb-glowtbook.service`, `glassdb-explore.service`, `glassdb-admin.service` (Streamlit) | 127.0.0.1 only | themed by `brand/`, menu from `central/nav.py` |
| Registry | SQLite files under `data/` | — | the public record, per-user Glowtbook store, sessions, seasons, social |
| Archive | BagIt under `data/`; C2PA signer | — | `deploy/PROVENANCE-ARCHITECTURE.md`, `deploy/VAULT.md` |
| Media | `data/` (object images, splat files) | — | served through the API |
| NetBird control plane | docker compose, Caddy on **8443**, STUN 3478/udp | public | `net.lexipedia.xyz:8443`; management, signal, relay and the embedded IdP in one container (runbook in the lexflow repository, `deploy/netbird-server/`) |
| Sign-in | Google OAuth through `/api/auth/*` | — | `deploy/AUTH.md` |
| Notices | Discord webhook | outbound | `central/notify.py` |

Model code on the server today is Ollama-only (`glowtbook/vision.py`, `OLLAMA_URL`, off unless
set). The capture app's optional sharper read is DINOv2 in the browser (transformers.js); the
weights download from a CDN, the photos never leave the phone. **No server code sends uploads to a
commercial AI service.** Keep it that way; the public page promises it.

Update path: `cd /root/glass-database && git pull --ff-only && bash deploy/update.sh` (and
`sudo systemctl reload apache2` when the Apache files changed).

## 2. The private network (being built)

Decisions taken, 28–29 October 2026:

- **One overlay, not two.** Self-hosted NetBird runs in single-account mode; glassdatabase is a
  set of groups and policies inside the existing account, not a second network. Sharing a node with
  Lexipedia is putting the peer in both groups.
- **Site users never join the overlay.** Members sign in with Google as now. The API server is a
  peer and is the only thing that reaches the nodes. Only machines we trust join: the server, the
  operator's laptops, lent machines.
- **The handshake stays on the droplet.** It only brokers connections. Watch the relay: a node that
  cannot connect directly relays through the droplet, which is capped at 512 MB for NetBird and
  shares the box with the website. Check nodes connect direct (`netbird status -d`) before sending
  video over the overlay.

### 2.1 Layout in the NetBird dashboard

| Thing | Name | Purpose |
|---|---|---|
| Group | `gdb-server` | the droplet (the API) |
| Group | `gdb-compute` | nodes that run Ollama and take work |
| Group | `gdb-storage` | the node that holds the object store |
| Policy | `gdb-server → gdb-compute` | TCP 11434 (Ollama), 9101 (host status). Nothing else. |
| Policy | `gdb-server → gdb-storage` | TCP 9000 (object store). Nothing else. |
| Service user | `glassdatabase-reader` | read-only; its personal access token lets the API list peers in `gdb-compute`. Separate from Lexipedia's token so either can be revoked alone. |
| Setup key | `gdb-compute` auto-group, reusable, expiring | handed to a lender on the Discord, one per machine; revoke it after they join |

### 2.2 Joining a node (what a lender does)

1. Install the NetBird client (native package) and Ollama from their official sources.
2. `netbird up --management-url https://net.lexipedia.xyz:8443 --setup-key <key>`.
3. Make Ollama listen on the overlay address only: `OLLAMA_HOST=<netbird ip>:11434`,
   `OLLAMA_NUM_PARALLEL=1` on a CPU box, `OLLAMA_KEEP_ALIVE=10m`. Pull the models the Compute page
   asks for (`qwen2.5vl:7b` first; see `glowtbook/vision.py`).
4. Optional: the host-status endpoint on 9101 (CPU, RAM, load, temperature) so the card shows it.
5. Leaving: `netbird down`, or drain it on the Compute page first so calls in flight finish.

What we log about a lent machine: its overlay name and address, when it was online, which model
calls ran there with token counts and durations. Never the content of a call.

### 2.3 On the server (to build)

Port the Lexipedia compute package (`lexflow/compute/`: nodes, health, pool, overlay poller,
Compute page; about 2,000 lines, tested) with the names changed:

```sh
GLASSDB_OVERLAY=netbird
GLASSDB_OVERLAY_URL=https://net.lexipedia.xyz:8443
GLASSDB_OVERLAY_TOKEN=<token of glassdatabase-reader>
GLASSDB_OVERLAY_COMPUTE_GROUP=gdb-compute
GLASSDB_OVERLAY_RANGE=<the overlay range>
GLASSDB_LLM_NODES=                      # the hand-written fallback, name=url|options
```

Its own `data/compute.db` (never synced), a Compute tab in Admin, and later a read-only strip for
signed-in members (which machines are on, what they hold, what ran). Every call is recorded with
model and node; the row it produced carries both, the way the fingerprint rows carry their tool and
version.

### 2.4 Workloads, in order

1. **Vision on intake** (queue). Each new capture or published piece: caption, kind of piece,
   colours, visible marks, condition note, written onto the record with model + node. Search,
   moderation and the start of "does this photo match the registered piece". A part-time laptop
   doing it an hour late is fine.
2. **3D Photo training** (queue, GPU). Brush or similar on a GPU node turns the capture photos
   into a splat overnight, so lenders of a GPU remove the "train it on your phone" step.
3. **Video** (queue, GPU, storage next to it). Frame sampling with ffmpeg, then the vision model.
4. **Interactive model features for members** (request time). Need an always-on node; build last,
   with a visible "the workshop is asleep" state.

## 3. Storage and failover (being built)

- **Object store next to the workers.** Garage or MinIO on the node with the disk, reached only by
  the server over the overlay. The API writes originals and derived files there; SQLite stays on
  the droplet. Back the store up off that machine (restic to a second disk or a bucket). A laptop
  is a worker, not an archive.
- **The public door is the droplet.** A home machine behind NAT cannot be the public endpoint, so
  "failover to the shop machine" means: the shop machine holds a current copy of the databases and
  the files, and can be promoted (DNS + a second small public box, or the droplet rebuilt from the
  copy) when the droplet is lost. It does not mean the site keeps serving while the droplet is down.
- **Database copies.** Litestream (SQLite WAL shipping to the store) gives a continuous copy with
  no change to the application and keeps the registry in our hands. Turso (libSQL) embedded
  replicas would also work, and give read replicas on the shop machine for free, but the primary
  then lives with a third party; weigh that against the provenance story and the legal review
  before choosing it. Either way the application keeps using SQLite files; neither is "the API".
- **What this does not do.** Glowtbook's promise that originals stay on "this machine" is about
  the operator's machine, not the public server; moving the archive copy to the object store keeps
  that promise as long as the store is ours.

## 4. Open questions

- The server as a peer: the droplet runs the NetBird client natively (not in the control-plane
  compose), joins `gdb-server`. Confirm the client and the server container coexist on one box.
- Whether `shop` and the Mac are the compute, or whether one always-on GPU box is bought or
  borrowed. Queue workloads are fine on what exists; interactive ones are not.
- Hosting region and data-residency wording for the legal review (`docs/` legal notes).
