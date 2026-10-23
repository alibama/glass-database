/* Glass Database — shared client helper for the static pages (capture apps,
   Seasons, graph). One small global, window.GDB:

     GDB.me()            → {signed_in, name, email, counts…} (cached; me(true) refetches)
     GDB.json(path, o)   → fetch(API+path) with credentials + Bearer token, parsed JSON, throws on !ok
     GDB.api(path, o)    → same, raw Response
     GDB.loginUrl(next)  → where to send someone to sign in (Google via /api/auth/login)
     GDB.logout()        → clear the cookie + any pairing token
     GDB.chip(el, opts)  → render a sign-in / signed-in-as chip into an element
     GDB.param(name)     → ?name= or #name= from the URL (pairing links use the fragment)

   Pairing: Glowtbook hands a signed-in user a link like
       /fingerprint/enroll.html#token=…&object=12
   The token is kept in localStorage (never in the URL after first load) and
   sent as  Authorization: Bearer  — so a phone opened from a desktop Glowtbook
   session is signed in without a second login. */
(function () {
  "use strict";
  var API = (document.querySelector('meta[name="gdb-api"]') || {}).content || "/api";
  var KEY = "gdb_token";
  var frag = {};

  (function takeFragment() {
    var h = location.hash.replace(/^#/, "");
    if (!h) return;
    var p = new URLSearchParams(h);
    var paired = false;
    if (p.get("token")) {
      try { localStorage.setItem(KEY, p.get("token")); } catch (_) {}
      p.delete("token"); paired = true;
    }
    p.forEach(function (v, k) { frag[k] = v; });
    if (paired) {
      frag.paired = "1";
      var rest = p.toString();
      history.replaceState(null, "", location.pathname + location.search + (rest ? "#" + rest : ""));
    }
  })();

  function token() { try { return localStorage.getItem(KEY) || ""; } catch (_) { return ""; } }
  function headers(extra) {
    var h = Object.assign({}, extra || {});
    var t = token(); if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }
  function api(path, opts) {
    opts = Object.assign({}, opts || {});
    opts.headers = headers(opts.headers);
    opts.credentials = "same-origin";
    return fetch(API + path, opts).then(function (r) {
      if (r.status === 401 && token()) { try { localStorage.removeItem(KEY); } catch (_) {} }
      return r;
    });
  }
  function json(path, opts) {
    return api(path, opts).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = t ? JSON.parse(t) : null; } catch (_) {}
        if (!r.ok) {
          var e = new Error((j && (j.detail || j.error)) || r.statusText || ("HTTP " + r.status));
          e.status = r.status; e.body = j; throw e;
        }
        return j;
      });
    });
  }
  var _me = null;
  function me(force) {
    if (_me && !force) return Promise.resolve(_me);
    return json("/me").then(function (j) { _me = j || { signed_in: false }; return _me; })
      .catch(function () { _me = { signed_in: false, offline: true }; return _me; });
  }
  function here() { return location.pathname + location.search; }
  function loginUrl(next) { return API + "/auth/login?next=" + encodeURIComponent(next || here()); }
  function logout() {
    try { localStorage.removeItem(KEY); } catch (_) {}
    _me = null;
    return api("/auth/logout", { method: "POST" }).catch(function () {});
  }
  function param(name) {
    var q = new URLSearchParams(location.search).get(name);
    return q != null ? q : (frag[name] != null ? frag[name] : null);
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  var CSS = ".gdb-chip{display:inline-flex;align-items:center;gap:.6rem;font-family:var(--mono,'IBM Plex Mono',ui-monospace,monospace);" +
    "font-size:12px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--muted,#6B625A)}" +
    ".gdb-chip .gdb-dot{width:8px;height:8px;border-radius:50%;background:var(--rule,#CFC2AE)}" +
    ".gdb-chip.on .gdb-dot{background:var(--red,#B3261E)}" +
    ".gdb-chip a,.gdb-chip button{font:inherit;letter-spacing:inherit;text-transform:inherit;color:var(--ink,#1F1A16);" +
    "background:none;border:1px solid var(--ink,#1F1A16);border-radius:0;padding:.45rem .8rem;cursor:pointer;text-decoration:none}" +
    ".gdb-chip a:hover,.gdb-chip button:hover{background:var(--ink,#1F1A16);color:var(--paper,#F4EEE4)}" +
    ".gdb-chip b{color:var(--ink,#1F1A16);font-weight:500;max-width:14ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}";
  function ensureCss() {
    if (document.getElementById("gdb-chip-css")) return;
    var s = document.createElement("style"); s.id = "gdb-chip-css"; s.textContent = CSS; document.head.appendChild(s);
  }

  /* chip(el, {next, onchange, compact}) */
  function chip(el, opts) {
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return Promise.resolve(null);
    opts = opts || {};
    ensureCss();
    el.classList.add("gdb-chip");
    return me(true).then(function (m) {
      el.classList.toggle("on", !!m.signed_in);
      if (m.signed_in) {
        el.innerHTML = '<span class="gdb-dot" aria-hidden="true"></span>' +
          (opts.compact ? "" : "<span>Signed in</span>") +
          "<b title=\"" + esc(m.email || "") + "\">" + esc(m.name || m.email || "you") + "</b>" +
          '<button type="button" class="gdb-out">Sign out</button>';
        var b = el.querySelector(".gdb-out");
        b.onclick = function () { logout().then(function () { chip(el, opts); if (opts.onchange) opts.onchange(null); }); };
      } else {
        el.innerHTML = '<span class="gdb-dot" aria-hidden="true"></span>' +
          (opts.compact ? "" : "<span>Not signed in</span>") +
          '<a href="' + esc(loginUrl(opts.next)) + '">Sign in</a>';
      }
      if (opts.onchange) opts.onchange(m.signed_in ? m : null);
      return m;
    });
  }

  window.GDB = { API: API, api: api, json: json, me: me, token: token, headers: headers,
    loginUrl: loginUrl, logout: logout, chip: chip, param: param, frag: frag, esc: esc };
})();
