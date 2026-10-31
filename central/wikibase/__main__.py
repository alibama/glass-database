"""
python -m central.wikibase <command>

  status                 counts per kind and status, the last runs
  stage                  site tables → staged items (idempotent; run after new approvals)
  dedupe                 cluster the same people / studios / places; one canonical each
  check                  reach the wiki; sign in if credentials are saved; count properties
  bootstrap [--really]   create the properties (dry run unless --really)
  dry-run [--kinds a,b] [--limit N]
  push --really [--kinds a,b] [--limit N] [--keys k1,k2]
  pull                   fetch what we pushed into wb_cache and report drift
  export <kind> [file]   CSV for OpenRefine (default: stdout)
  import <file>          an edited CSV back in
  set-bot <user>         save the bot user; the password is read from WIKIBASE_BOT_PASSWORD
                         (never from an argument); --url and --public-base set those

Credentials: what the admin page saved (encrypted in the database), else
WIKIBASE_BOT_USER / WIKIBASE_BOT_PASSWORD in the environment.
"""
from __future__ import annotations

import argparse
import os
import sys

from central.wikibase import dedupe, jobs, push, refine, stage, store, sync


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m central.wikibase", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "stage", "dedupe", "check", "bootstrap", "dry-run", "push", "pull",
                                        "export", "import", "set-bot"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--really", action="store_true")
    ap.add_argument("--kinds")
    ap.add_argument("--keys")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--url")
    ap.add_argument("--public-base")
    ap.add_argument("--no-live", action="store_true", help="do not read the wiki before planning")
    a = ap.parse_args(argv)
    conn = store.open_db()
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731
    cmd = a.command
    if cmd == "status":
        c = store.counts(conn)
        print(f"{c['total']} staged items, {c['live']} on the wiki, {c['pending']} pending, {c['properties']} properties")
        for kind, sts in sorted(c["kinds"].items()):
            print(f"  {kind:12} " + ", ".join(f"{s} {n}" for s, n in sorted(sts.items())))
        for r in store.runs(conn, 8):
            print(f"  run #{r['id']} {r['kind']} {r['status']} {r['started_at']}")
        return 0
    if cmd == "set-bot":
        if not a.args:
            ap.error("set-bot needs the bot user name")
        pw = os.environ.get("WIKIBASE_BOT_PASSWORD")
        store.set_settings(conn, url=a.url, user=a.args[0], password=pw, public_base=a.public_base)
        print("saved" + (" (password from WIKIBASE_BOT_PASSWORD)" if pw else " (user only; no password in the environment)"))
        return 0
    if cmd == "export":
        if not a.args:
            ap.error("export needs a kind (person, studio, place, …)")
        text = refine.export_csv(conn, a.args[0])
        if len(a.args) > 1:
            open(a.args[1], "w", encoding="utf-8", newline="").write(text)
            print(f"wrote {a.args[1]}")
        else:
            sys.stdout.write(text)
        return 0
    if cmd == "import":
        if not a.args:
            ap.error("import needs a CSV file")
        refine.import_csv(conn, open(a.args[0], encoding="utf-8-sig").read(), log)
        return 0
    params: dict = {}
    if a.kinds:
        params["kinds"] = a.kinds.split(",")
    if a.keys:
        params["keys"] = a.keys.split(",")
    if a.limit:
        params["limit"] = a.limit
    if a.no_live:
        params["live"] = False
    fn = {"stage": stage.run, "dedupe": dedupe.run, "check": sync.check, "pull": sync.pull,
          "bootstrap": push.run, "dry-run": push.run, "push": push.run}[cmd]
    if cmd == "bootstrap":
        params.update(what="properties", really=a.really)
    elif cmd == "dry-run":
        params.update(really=False)
    elif cmd == "push":
        if not a.really:
            print("push without --really is a dry run; use `dry-run`, or add --really to write", file=sys.stderr)
            params.update(really=False)
        else:
            params.update(really=True)
    kind = {"bootstrap": "bootstrap", "dry-run": "dry-run", "push": "push" if a.really else "dry-run"}.get(cmd, cmd)
    try:
        run_id = jobs.start(conn, kind, fn, params, by_email="cli", thread=False)
    except jobs.Busy as e:
        print(str(e), file=sys.stderr)
        return 3
    r = store.run(conn, run_id)
    print(f"run #{run_id}: {r['status']}", file=sys.stderr)
    return 0 if r["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
