"""
central.wikibase.client
=======================
The MediaWiki action-API client for a wikibase.cloud instance, ported from lexflow's pusher:
login with a bot password, a CSRF token, ``wbeditentity`` with ``maxlag``, ``assert=user``,
back-off on ``ratelimited`` / ``maxlag`` / ``readonly``, a fresh sign-in when the session
lapses (``badtoken`` / ``assertuserfailed``), retries on dropped connections, and a pause
after every write. Reads: ``wbgetentities``, ``wbsearchentities``, ``allpages`` of a
namespace, SPARQL, site and user info.

``dry_run=True`` performs no POST at all: ``edit`` hands back placeholder ids
(``Q9000001`` …, ``P900`` …) so later claims can point at what a real run would create.

Credentials never pass through here as text the caller logs: ``login()`` is the only
place they are sent, to the wiki, over https.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

USER_AGENT = "GlassDatabase-wikibase/1.0 (https://glassdatabase.org; hello@glassdatabase.org)"
MAXLAG = 5
Log = Callable[[str], None]


class WikibaseError(RuntimeError):
    pass


def error_code(e: Exception) -> str:
    """The API error code folded into a ``WikibaseError`` message (``wbeditentity: <code>: <info>``)."""
    parts = str(e).split(": ", 2)
    return parts[1] if len(parts) >= 2 and re.fullmatch(r"[a-z0-9_-]+", parts[1]) else "error"


def owner_of(error: str) -> str | None:
    """The item an "already has label" message names."""
    if "already has label" not in error:
        return None
    m = re.search(r"\b([QP]\d+)\b", error.split("already has label")[0])
    return m.group(1) if m else None


@dataclass
class Wikibase:
    base: str
    user: str = ""
    password: str = ""
    dry_run: bool = True
    pause: float = 1.0
    bot: bool = False
    attempts: int = 8
    log: Log | None = None
    writes: int = 0
    relogins: int = 0
    csrf: str | None = None
    session: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=60, follow_redirects=True,
                                                                        headers={"User-Agent": USER_AGENT}))
    _next_q: int = 9_000_000
    _next_p: int = 900

    def __post_init__(self):
        self.base = self.base.rstrip("/")
        self.api = self.base + "/w/api.php"
        self.sparql_url = self.base + "/query/sparql"
        self.entity_base = self.base + "/entity/"

    # ------------------------------------------------------------------ transport
    def _say(self, msg: str) -> None:
        if self.log:
            self.log(msg)

    def get(self, **params) -> dict:
        r = self.session.get(self.api, params={**params, "format": "json"})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            e = data["error"]
            raise WikibaseError(f"{params.get('action')}: {e.get('code')}: {e.get('info')}")
        return data

    def _post_once(self, params: dict) -> dict:
        data = {**params, "format": "json", "token": self.csrf or "+\\", "maxlag": MAXLAG}
        if self.user:
            data["assert"] = "user"
        r = self.session.post(self.api, data=data, timeout=120)
        r.raise_for_status()
        out = r.json()
        err = out.get("error")
        if err:
            raise WikibaseError(f"{params.get('action')}: {err.get('code')}: {err.get('info')}")
        return out

    def post(self, **params) -> dict:
        last: Exception | None = None
        signed_in = 0
        for attempt in range(self.attempts):
            try:
                data = self._post_once(params)
            except WikibaseError as e:
                code = error_code(e)
                if code in ("ratelimited", "maxlag", "readonly"):
                    last = e
                    self._say(f"  {code}: waiting {15 * (attempt + 1)} s")
                    time.sleep(15 * (attempt + 1))
                    continue
                if code in ("badtoken", "assertuserfailed", "assertnameduserfailed") and self.user and signed_in < 2:
                    signed_in += 1
                    self.relogins += 1
                    self._say(f"  the session lapsed ({code}): signing in again")
                    self.login()
                    last = e
                    continue
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                self._say(f"  connection trouble ({type(e).__name__}): retrying in {15 * (attempt + 1)} s")
                time.sleep(15 * (attempt + 1))
                continue
            if self.pause:
                time.sleep(self.pause)
            return data
        raise WikibaseError(f"{params.get('action')}: gave up after {self.attempts} attempts: {last}")

    # ------------------------------------------------------------------ session
    def login(self) -> str:
        if not (self.user and self.password):
            raise WikibaseError("no bot credentials: set the bot user and password on the Wikibase admin page")
        tok = self.get(action="query", meta="tokens", type="login")["query"]["tokens"]["logintoken"]
        r = self.session.post(self.api, data={"action": "login", "lgname": self.user, "lgpassword": self.password,
                                              "lgtoken": tok, "format": "json"}).json()
        res = r.get("login", {})
        if res.get("result") != "Success":
            raise WikibaseError(f"login failed: {res.get('reason') or res.get('result') or r}")
        self.csrf = self.get(action="query", meta="tokens")["query"]["tokens"]["csrftoken"]
        return res.get("lgusername") or self.user

    def whoami(self) -> dict:
        ui = self.get(action="query", meta="userinfo", uiprop="rights|groups")["query"]["userinfo"]
        rights = set(ui.get("rights") or [])
        return {"name": ui.get("name"), "anon": "anon" in ui, "groups": ui.get("groups") or [],
                "can_edit": "edit" in rights, "can_create_properties": "property-create" in rights,
                "bot": "bot" in rights or "bot" in (ui.get("groups") or [])}

    def site_info(self) -> dict:
        si = self.get(action="query", meta="siteinfo", siprop="general|extensions")["query"]
        g = si.get("general", {})
        return {"sitename": g.get("sitename"), "generator": g.get("generator"), "server": g.get("server"),
                "extensions": sorted(e.get("name", "") for e in si.get("extensions", []))}

    # ------------------------------------------------------------------ reads
    def get_entities(self, ids: list[str], props: str = "labels|descriptions|aliases|claims|datatype") -> dict:
        out: dict = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), 50):
            res = self.get(action="wbgetentities", ids="|".join(ids[i:i + 50]), props=props, languages="en")
            for q, e in res.get("entities", {}).items():
                out[q] = {} if "missing" in e else e
        return out

    def search(self, text: str, entity_type: str = "item", limit: int = 10) -> list[dict]:
        if not text.strip():
            return []
        res = self.get(action="wbsearchentities", search=text[:200], language="en", type=entity_type, limit=limit)
        return res.get("search", [])

    def allpages(self, namespace: int) -> list[str]:
        out, cont = [], {}
        while True:
            res = self.get(action="query", list="allpages", apnamespace=namespace, aplimit=500, **cont)
            out += [p["title"].split(":")[-1] for p in res["query"]["allpages"]]
            if "continue" not in res:
                return out
            cont = res["continue"]

    def properties(self) -> dict[str, dict]:
        """label → {pid, datatype} for every property on the wiki (namespace 122)."""
        pids = self.allpages(122)
        out: dict[str, dict] = {}
        for pid, e in self.get_entities(pids, "labels|datatype").items():
            lab = ((e.get("labels") or {}).get("en") or {}).get("value")
            if lab:
                out[lab] = {"pid": pid, "datatype": e.get("datatype") or ""}
        return out

    def sparql(self, query: str) -> list[dict]:
        r = self.session.get(self.sparql_url, params={"query": query},
                             headers={"Accept": "application/sparql-results+json"}, timeout=120)
        r.raise_for_status()
        return r.json().get("results", {}).get("bindings", [])

    def key_map(self, key_pid: str) -> dict[str, str]:
        """Every *Glass Database ID* on the wiki → its item, through SPARQL (paged)."""
        out: dict[str, str] = {}
        offset, page = 0, 5000
        while True:
            q = (f"PREFIX gdbt: <{self.base}/prop/direct/>\nSELECT ?item ?key WHERE {{ ?item gdbt:{key_pid} ?key }} "
                 f"LIMIT {page} OFFSET {offset}")
            try:
                rows = self.sparql(q)
            except (httpx.HTTPError, ValueError) as e:
                self._say(f"  SPARQL unavailable ({e}); using the API's label search instead")
                return out
            for b in rows:
                out.setdefault(b["key"]["value"], b["item"]["value"].rsplit("/", 1)[-1])
            if len(rows) < page:
                return out
            offset += page

    # ------------------------------------------------------------------ writes
    def placeholder(self, entity_type: str = "item") -> str:
        if entity_type == "property":
            self._next_p += 1
            return f"P{self._next_p}"
        self._next_q += 1
        return f"Q{self._next_q}"

    def edit(self, data: dict, *, qid: str | None = None, new: str | None = None, clear: bool = False,
             summary: str = "Glass Database") -> dict:
        """One ``wbeditentity``: create (``new`` = item|property) or update ``qid``; ``clear``
        replaces the entity's statements instead of appending."""
        if self.dry_run:
            self.writes += 1
            return {"entity": {"id": qid or self.placeholder(new or "item")}, "dry_run": True}
        params = {"action": "wbeditentity", "data": json.dumps(data, ensure_ascii=False), "summary": summary[:250]}
        if qid:
            params["id"] = qid
            if clear:
                params["clear"] = 1
        else:
            params["new"] = new or "item"
        if self.bot:
            params["bot"] = 1
        res = self.post(**params)
        self.writes += 1
        return res

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
