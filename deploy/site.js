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

  /* The primary menu — the SAME list as central/nav.py (tests/test_nav.py keeps them equal).
     Pages carry <ul id="menu" data-nav data-active="key"></ul> and this fills it. */
  var NAV = [["explore","Find Artists & Studios","/explore/"],["games","Collecting Events","/games.html"],["glassid","Glass ID","/fingerprint/enroll.html"],["glowtbook","Glowtbook","/glowtbook/"],["notes","Field notes","/blog/"],["about","About","/#about"],["account","My account","/account.html"]];
  function menu(el, active) {
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    active = active || el.getAttribute("data-active") || "";
    el.innerHTML = NAV.map(function (n) {
      return '<li><a href="' + esc(n[2]) + '"' + (n[0] === active ? ' class="on"' : "") + ' data-key="' + esc(n[0]) + '">' + esc(n[1]) + "</a></li>";
    }).join("");
    // "My account" carries a red dot once the person is signed in (the same dot as the chip)
    me(true).then(function (m) {
      var a = el.querySelector('a[data-key="account"]');
      if (a && m && m.signed_in) { a.classList.add("me"); a.title = "Signed in as " + (m.name || m.email || "you"); }
    }).catch(function () {});
  }
  function renderMenus() { var els = document.querySelectorAll("ul#menu[data-nav]"); for (var i = 0; i < els.length; i++) menu(els[i]); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", renderMenus); else renderMenus();


  /* ---- share kit ---------------------------------------------------------
     GDB.share(el, {kind:"entry"|"object"|"collection"|"splat", ref, title, text, image})
     renders: Copy link · Save image · Share… (phone share sheet: Instagram lives there)
     · Facebook · Bluesky (posts with the connected account, else opens Bluesky's
     composer) · Instagram tip. The link handed to networks is the Open Graph
     landing page /api/social/share/<kind>/<ref>, so previews show the card. */
  var SHARE_CSS = ".gdb-share{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-top:.8rem}" +
    ".gdb-share button,.gdb-share a.sb{font:500 11px/1 var(--mono,'IBM Plex Mono',ui-monospace,monospace);letter-spacing:.08em;text-transform:uppercase;" +
    "color:var(--ink,#1F1A16);background:none;border:1px solid var(--ink,#1F1A16);padding:.5rem .7rem;cursor:pointer;text-decoration:none}" +
    ".gdb-share button:hover,.gdb-share a.sb:hover{background:var(--ink,#1F1A16);color:var(--paper,#F4EEE4)}" +
    ".gdb-share .msg{font:400 13px/1.4 var(--sans,Inter,system-ui,sans-serif);color:var(--muted,#6B6157);flex-basis:100%}";
  function ensureShareCss() {
    if (document.getElementById("gdb-share-css")) return;
    var s = document.createElement("style"); s.id = "gdb-share-css"; s.textContent = SHARE_CSS; document.head.appendChild(s);
  }
  function share(el, o) {
    if (typeof el === "string") el = document.querySelector(el);
    if (!el || !o) return;
    ensureShareCss();
    var origin = location.origin;
    var landing = origin + API + "/social/share/" + encodeURIComponent(o.kind) + "/" + encodeURIComponent(o.ref);
    var page = o.url ? (o.url.indexOf("http") === 0 ? o.url : origin + o.url) : landing;
    var image = o.image ? (o.image.indexOf("http") === 0 ? o.image : origin + o.image) : "";
    var text = o.text || o.title || "";
    var caption = text + (text ? "\n" : "") + landing;
    el.classList.add("gdb-share");
    el.innerHTML = '<button type="button" data-s="copy">Copy link</button>' +
      (image ? '<a class="sb" data-s="save" href="' + esc(image) + '" download>Save image</a>' : "") +
      (navigator.share ? '<button type="button" data-s="native">Share…</button>' : "") +
      '<a class="sb" data-s="fb" target="_blank" rel="noopener" href="https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(landing) + '">Facebook</a>' +
      '<button type="button" data-s="bsky">Bluesky</button>' +
      '<button type="button" data-s="ig">Instagram</button>' +
      '<span class="msg" role="status" aria-live="polite"></span>';
    var msg = el.querySelector(".msg");
    function say(t) { msg.textContent = t; }
    el.querySelector('[data-s="copy"]').onclick = function () {
      navigator.clipboard.writeText(landing).then(function () { say("Link copied. Paste it anywhere: the preview shows the card."); },
        function () { say(landing); });
    };
    var nat = el.querySelector('[data-s="native"]');
    if (nat) nat.onclick = function () {
      var data = { title: o.title || "", text: text, url: landing };
      if (image && navigator.canShare) {
        fetch(image).then(function (r) { return r.blob(); }).then(function (b) {
          var f = new File([b], (o.kind || "share") + ".png", { type: b.type || "image/png" });
          if (navigator.canShare({ files: [f] })) return navigator.share({ files: [f], title: data.title, text: caption });
          return navigator.share(data);
        }).catch(function () { navigator.share(data).catch(function () {}); });
      } else navigator.share(data).catch(function () {});
    };
    el.querySelector('[data-s="ig"]').onclick = function () {
      navigator.clipboard && navigator.clipboard.writeText(caption).catch(function () {});
      say("Instagram has no web posting. Caption copied. On a phone, tap Share… and pick Instagram; on a computer, save the image and post it from the app.");
    };
    el.querySelector('[data-s="bsky"]').onclick = function () {
      var btn = this; btn.disabled = true; say("Posting to Bluesky…");
      var body = { text: caption, links: [landing] };
      if (o.kind === "entry") body.image = "entry:" + o.ref;
      else if (o.kind === "object") body.image = "object:" + o.ref;
      else if (o.kind === "collection") body.image = "collection";
      api("/social/bluesky/post", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
        .then(function (x) {
          btn.disabled = false;
          if (x.ok) { say("Posted."); msg.innerHTML = 'Posted to Bluesky: <a href="' + esc(x.j.url) + '" target="_blank" rel="noopener">see it</a>'; return; }
          if (x.status === 401 || x.status === 409) {
            var compose = "https://bsky.app/intent/compose?text=" + encodeURIComponent(caption);
            msg.innerHTML = (x.status === 401 ? 'Not signed in. ' : 'Bluesky isn\'t connected yet. ') +
              '<a href="' + esc(compose) + '" target="_blank" rel="noopener">Open Bluesky\'s composer</a> with the text, or ' +
              '<a href="/glowtbook/?page=profile">connect your account</a> once so this button posts for you.';
            return;
          }
          say((x.j && x.j.detail) || "That didn't work.");
        }).catch(function () { btn.disabled = false; say("Network error."); });
    };
  }

  window.GDB = { API: API, api: api, json: json, me: me, token: token, headers: headers, NAV: NAV, menu: menu, share: share,
    loginUrl: loginUrl, logout: logout, chip: chip, param: param, frag: frag, esc: esc };
})();
