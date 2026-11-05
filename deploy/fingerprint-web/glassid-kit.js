/* Glass ID kit — the lookup, compare and send-for-analysis pieces the two capture pages
   share (Register Your Piece = enroll.html, Check a Piece = verify.html). One global,
   window.GlassID. Needs site.js (window.GDB) for the API and JSZip for the session zip.

     GlassID.VERSION                      the app version written into every session manifest
     GlassID.deviceInfo(video, stream, x) what phone and camera this is (no identifiers)
     GlassID.lookup(q) / recent() / piece(ref) / candidates(fp, o) / compare(fp, ref)
     GlassID.stripFp(fp)                  descriptors only — never the pictures
     GlassID.localCompare(shots, ref, m)  the page's own matching, per descriptor, in the
                                          same shape the server returns (breakdown, pairs,
                                          carried_by, reasons)
     GlassID.candidateRows(el, result, o) the candidates as rows with a Compare button
     GlassID.comparePanel(el, o)          reference photos beside the new shots, the score,
                                          what carried it, Same piece / Different piece
     GlassID.sendBlock(el, o)             the decision picker + Send for analysis
     GlassID.buildManifest(o) / send(o)   the session zip (manifest.json + images/) → POST /api/me/captures

   Everything renders with the page's own tokens (--ink, --ink-dim, --line, --hot, --ok,
   --danger), so it reads right in the dark camera drawer and on the paper gate alike. */
(function () {
  "use strict";
  var G = window.GDB || null;
  var VERSION = "2026.11.05";
  var T = { SIM_LO: 6, SIM_HI: 22, STRONG_T: 0.5, SPREAD_TARGET: 6, MATCH_READY: 70, NO_MATCH: 40, MIN_EFFORT: 8, MIN_CELLS_MATCH: 3, SHARP_MIN: 12, TOP_SIMS: 8 };
  var WORDS = { dino: "AI texture", color: "Colour", dhash: "Shape" };
  var DECISIONS = [["new_piece", "New piece", "Nothing on file is this piece; it gets its own Glass ID."],
    ["re_registered", "Registered again", "The piece already had a Glass ID; this capture replaces or strengthens it."],
    ["match_confirmed", "Same piece", "The piece in front of me is the registered one I compared it with."],
    ["match_rejected", "Different piece", "It looked like a registered piece but it is not that piece."],
    ["undecided", "Not sure", "Send the shots anyway; decide later."]];
  function esc(s) { return G ? G.esc(s) : String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function decisionLabel(d) { for (var i = 0; i < DECISIONS.length; i++) if (DECISIONS[i][0] === d) return DECISIONS[i][1]; return d || "Not decided"; }
  function pct(x) { return Math.round((x || 0) * 100); }
  function colourOf(cls) { return cls === "match" ? "var(--ok)" : cls === "nomatch" ? "var(--danger)" : "var(--hot)"; }
  function thumb(url, w) { return G && G.thumb ? G.thumb(url, w) : url; }

  /* ---- css, injected once ------------------------------------------------------ */
  var CSS = ".gk{font-size:12.5px;line-height:1.4;color:var(--ink-dim)}" +
    ".gk h3{margin:0 0 6px;font-size:14px;color:var(--ink)}.gk h4{margin:10px 0 4px;font-size:11px;font-family:var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-faint)}" +
    ".gk p{margin:0 0 8px}.gk .fine{font-size:11.5px;color:var(--ink-faint)}" +
    ".gk-block{border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}" +
    ".gk-row{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}.gk-row:first-of-type{border-top:0}" +
    ".gk-row .gk-img{width:54px;height:54px;flex:none;border-radius:8px;overflow:hidden;background:#0003;border:1px solid var(--line);display:grid;place-items:center;font-size:9px;color:var(--ink-faint)}.gk-row .gk-img img{width:100%;height:100%;object-fit:cover;display:block}" +
    ".gk-row .gk-meta{flex:1;min-width:0}.gk-row .gk-meta b{display:block;color:var(--ink);font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gk-row .gk-meta small{display:block;color:var(--ink-dim);font-size:11.5px}" +
    ".gk-score{flex:none;text-align:right;min-width:62px}.gk-score b{display:block;font-size:17px;line-height:1}.gk-score small{display:block;font-size:9.5px;font-family:var(--mono);letter-spacing:.06em;text-transform:uppercase}" +
    ".gk-btn{font:600 12px/1 var(--sans);border-radius:9px;padding:9px 11px;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer;white-space:nowrap}.gk-btn.hot{background:var(--hot);border-color:var(--hot);color:#fff}.gk-btn.ok{color:var(--ok);border-color:var(--ok)}.gk-btn.warn{color:var(--danger);border-color:var(--danger)}.gk-btn:disabled{opacity:.5;cursor:default}" +
    ".gk-btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.gk-btns .gk-btn{flex:1;min-width:120px;padding:11px 12px;font-size:13px}" +
    ".gk-strip{display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}.gk-strip div{position:relative;width:76px;height:76px;flex:none;border-radius:8px;overflow:hidden;background:#0003;border:1px solid var(--line);display:grid;place-items:center;font-size:9px;color:var(--ink-faint);text-align:center;padding:2px}.gk-strip img{width:100%;height:100%;object-fit:cover;display:block}.gk-strip span{position:absolute;left:3px;bottom:2px;font-size:9px;padding:1px 4px;border-radius:5px;background:#000a;color:#fff;font-family:var(--mono)}" +
    ".gk-side{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media (max-width:360px){.gk-side{grid-template-columns:1fr}}" +
    ".gk-bar{display:grid;grid-template-columns:7.5em 1fr 3.2em;gap:8px;align-items:center;font-size:12px;margin:3px 0}.gk-bar i{display:block;height:6px;border-radius:3px;background:#ffffff14;overflow:hidden}.gk-bar i b{display:block;height:100%;border-radius:3px}.gk-bar em{font-style:normal;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink)}.gk-bar.off{opacity:.55}" +
    ".gk-reasons{margin:8px 0 0;padding-left:1.1em}.gk-reasons li{margin:3px 0}" +
    ".gk-pill{display:inline-block;font:500 9.5px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 7px;border-radius:999px;border:1px solid var(--line);color:var(--ink-dim);vertical-align:middle;margin-left:4px}" +
    ".gk-radios{display:grid;gap:6px;margin:6px 0 8px}.gk-radios label{display:flex;gap:9px;align-items:flex-start;padding:8px 10px;border:1px solid var(--line);border-radius:9px;cursor:pointer;color:var(--ink)}.gk-radios label.on{border-color:var(--hot)}.gk-radios input{margin-top:2px;accent-color:var(--hot)}.gk-radios label small{display:block;color:var(--ink-dim);font-size:11.5px}" +
    ".gk textarea,.gk input[type=search],.gk input[type=text]{width:100%;box-sizing:border-box;font:inherit;font-size:13px;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:9px;padding:9px 10px}.gk textarea{min-height:52px;resize:vertical}" +
    ".gk .gk-status{margin:8px 0 0;font-size:12.5px;color:var(--ink)}.gk .gk-status.err{color:var(--danger)}.gk .gk-status.ok{color:var(--ok)}" +
    ".gk-chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}.gk-chips button{font:500 11.5px/1 var(--sans);padding:7px 10px;border-radius:999px;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".gk-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.gk-head button.link{background:none;border:0;padding:0;font:inherit;font-size:12px;color:var(--hot);text-decoration:underline;cursor:pointer}";
  function ensureCss() { if (document.getElementById("gk-css")) return; var s = document.createElement("style"); s.id = "gk-css"; s.textContent = CSS; document.head.appendChild(s); }

  /* ---- device ------------------------------------------------------------------ */
  function deviceInfo(video, stream, extra) {
    var d = { ua: navigator.userAgent, platform: navigator.platform || "", language: navigator.language || "",
      screen: { w: screen.width, h: screen.height, dpr: window.devicePixelRatio || 1 },
      viewport: { w: window.innerWidth, h: window.innerHeight },
      touch: !!(navigator.maxTouchPoints && navigator.maxTouchPoints > 0), standalone: !!(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches),
      memory_gb: navigator.deviceMemory || null, cores: navigator.hardwareConcurrency || null };
    try { if (navigator.userAgentData) d.uaData = { mobile: navigator.userAgentData.mobile, platform: navigator.userAgentData.platform, brands: navigator.userAgentData.brands }; } catch (_) {}
    if (video) d.resolution = { w: video.videoWidth || 0, h: video.videoHeight || 0 };
    try {
      var t = stream && stream.getVideoTracks && stream.getVideoTracks()[0];
      if (t) { var s = t.getSettings ? t.getSettings() : {}; d.camera = { label: t.label || "", width: s.width, height: s.height, facingMode: s.facingMode, frameRate: s.frameRate, zoom: s.zoom, focusMode: s.focusMode, whiteBalanceMode: s.whiteBalanceMode, exposureMode: s.exposureMode }; }
    } catch (_) {}
    if (extra) for (var k in extra) d[k] = extra[k];
    return d;
  }

  /* ---- api ------------------------------------------------------------------- */
  function need() { if (!G) return Promise.reject(new Error("site.js is not loaded")); return null; }
  function post(path, body) { return need() || G.json(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); }
  function stripFp(fp) {
    fp = fp || {};
    var out = {};
    for (var k in fp) if (k !== "frames") out[k] = fp[k];
    out.frames = (fp.frames || []).map(function (f, i) {
      var o = { file: f.file || ("frames/" + String(f.seq || (i + 1)).padStart(3, "0") + ".jpg"), seq: f.seq || (i + 1), cell: f.cell, sector: f.sector == null ? -1 : f.sector, band: f.band, t: f.t };
      if (f.sharp != null) o.sharp = +(+f.sharp).toFixed(1);
      if (f.detail != null) o.detail = +(+f.detail).toFixed(4);
      if (f.dhash) o.dhash = [f.dhash[0] >>> 0, f.dhash[1] >>> 0];
      if (f.chist) o.chist = f.chist;
      if (f.emb) o.emb = f.emb;
      if (f.noveltyAtCapture != null) o.noveltyAtCapture = f.noveltyAtCapture;
      if (f.match) o.match = { ref: f.match.ref ? f.match.ref.file : null, sim: f.match.sim, dist: f.match.dist };
      return o;
    });
    return out;
  }
  function candidates(fp, o) { o = o || {}; return post("/glassid/candidates", { fingerprint: stripFp(fp), k: o.k || 5, exclude: o.exclude || [], descriptor: o.descriptor || null }); }
  function compare(fp, ref, descriptor) { return post("/glassid/compare", { fingerprint: stripFp(fp), ref: ref, descriptor: descriptor || null }); }
  function lookup(q) { return need() || G.json("/glassid/lookup?q=" + encodeURIComponent(q || "")); }
  function recent() { return need() || G.json("/glassid/recent"); }
  function piece(ref) { return need() || G.json("/glassid/pieces/" + encodeURIComponent(ref)); }

  /* ---- the page's matching, per descriptor, in the server's shape ----------------- */
  function popcount(x) { x = x >>> 0; x = x - ((x >>> 1) & 0x55555555); x = (x & 0x33333333) + ((x >>> 2) & 0x33333333); x = (x + (x >>> 4)) & 0x0f0f0f0f; return (x * 0x01010101) >>> 24; }
  function ham(a, b) { if (!a || !b) return 64; return popcount((a[0] >>> 0) ^ (b[0] >>> 0)) + popcount((a[1] >>> 0) ^ (b[1] >>> 0)); }
  function histCorr(a, b) { if (!a || !b || a.length !== b.length) return 0; var n = a.length, ma = 0, mb = 0, i; for (i = 0; i < n; i++) { ma += a[i]; mb += b[i]; } ma /= n; mb /= n; var num = 0, da = 0, db = 0; for (i = 0; i < n; i++) { var u = a[i] - ma, v = b[i] - mb; num += u * v; da += u * u; db += v * v; } var d = Math.sqrt(da * db); return d ? Math.max(0, num / d) : 0; }
  function cosine(a, b) { if (!a || !b || a.length !== b.length) return 0; var d = 0, na = 0, nb = 0; for (var i = 0; i < a.length; i++) { d += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; } var den = Math.sqrt(na * nb); return den ? Math.max(0, d / den) : 0; }
  function simOf(dist) { return Math.max(0, Math.min(1, (T.SIM_HI - dist) / (T.SIM_HI - T.SIM_LO))); }
  function frameSim(a, r, desc) { if (desc === "dino") return (a.emb && r.emb) ? cosine(a.emb, r.emb) : 0; if (desc === "dhash") return simOf(ham(a.dhash, r.dhash)); return histCorr(a.chist, r.chist); }
  function has(f, desc) { return !!(desc === "dino" ? f.emb : desc === "dhash" ? f.dhash : f.chist); }
  function aggregate(pairs) {
    var strong = pairs.filter(function (p) { return p.sim >= T.STRONG_T; });
    var sorted = strong.slice().sort(function (a, b) { return b.sim - a.sim; });
    var top = sorted.slice(0, T.TOP_SIMS);
    var quality = top.length ? top.reduce(function (a, p) { return a + p.sim; }, 0) / top.length : 0;
    var cells = {}; strong.forEach(function (p) { if (p.ref_cell != null) cells[String(p.ref_cell)] = 1; });
    var n = Object.keys(cells).length, spread = Math.min(1, n / T.SPREAD_TARGET);
    return { confidence: Math.round(100 * quality * (0.4 + 0.6 * spread)), quality: +quality.toFixed(4), spread: +spread.toFixed(4), views_matched: n, strong: strong.length,
      carried_by: { shots: top.map(function (p) { return p.shot; }), reference_views: top.map(function (p) { return p.ref; }) } };
  }
  function verdictFor(agg, effort) {
    if (agg.confidence >= T.MATCH_READY && agg.views_matched >= T.MIN_CELLS_MATCH) return ["match", "Match likely"];
    if (effort >= T.MIN_EFFORT && agg.confidence < T.NO_MATCH && agg.views_matched < 2) return ["nomatch", "No match here"];
    return ["inconc", "Inconclusive"];
  }
  function localCompare(shots, ref, o) {
    o = o || {};
    var rf = (ref && ref.frames) || [], qf = shots || [];
    var effort = qf.filter(function (f) { return f.sharp == null || f.sharp >= T.SHARP_MIN; }).length;
    var breakdown = {}, pairsBy = {}, d, i, j;
    ["dino", "color", "dhash"].forEach(function (desc) {
      var qh = qf.filter(function (f) { return has(f, desc); }).length, rh = rf.filter(function (f) { return has(f, desc); }).length;
      if (!qh || !rh) { breakdown[desc] = { available: false, confidence: 0, views_matched: 0, shots_with_descriptor: qh, reference_views_with_descriptor: rh, why: !qh ? "the new capture has no " + WORDS[desc].toLowerCase() + " on any shot" : "the registered piece has no " + WORDS[desc].toLowerCase() + " on file" }; pairsBy[desc] = []; return; }
      var pairs = [];
      for (i = 0; i < qf.length; i++) {
        var best = -1, bj = -1;
        for (j = 0; j < rf.length; j++) { var s = frameSim(qf[i], rf[j], desc); if (s > best) { best = s; bj = j; } }
        var r = bj >= 0 ? rf[bj] : null, sim = Math.max(0, best);
        pairs.push({ shot: qf[i].seq || (i + 1), sim: +sim.toFixed(4), dist: Math.round((1 - sim) * 100), ref: r ? r.file : null, ref_cell: r ? (r.cell != null ? r.cell : "c" + r.file) : null, ref_index: bj });
      }
      var agg = aggregate(pairs), v = verdictFor(agg, effort);
      agg.available = true; agg.verdict = v[0]; agg.label = v[1]; agg.shots_with_descriptor = qh; agg.reference_views_with_descriptor = rh;
      breakdown[desc] = agg; pairsBy[desc] = pairs;
    });
    var desc = o.descriptor || (breakdown.dino.available ? "dino" : "color");
    if (!breakdown[desc].available) { var fb = ["color", "dhash", "dino"]; for (i = 0; i < fb.length; i++) if (breakdown[fb[i]].available) { desc = fb[i]; break; } }
    var head = breakdown[desc], cls = head.available ? head.verdict : "inconc", label = head.available ? head.label : "Inconclusive";
    var res = { descriptor: desc, confidence: head.confidence || 0, verdict: cls, label: label, quality: head.quality || 0, spread: head.spread || 0, views_matched: head.views_matched || 0, shots: qf.length, effort: effort, reference_views: rf.length, breakdown: breakdown,
      pairs: (pairsBy[desc] || []).slice().sort(function (a, b) { return b.sim - a.sim; }).slice(0, 12), carried_by: head.carried_by || { shots: [], reference_views: [] },
      size: sizeCheck(o.queryDims || null, ref && ref.metadata && ref.metadata.dimensions_mm || null), caveats: [] };
    res.size_conflict = res.size.ok === false;
    if (res.size_conflict && cls === "match") { res.verdict = "inconc"; res.label = "Inconclusive"; res.caveats.push("The pictures matched but the mat sizes do not; held at inconclusive."); }
    res.reasons = reasonsFor(res, ref);
    if (ref && ref.rating != null && ref.rating < 75) res.caveats.push("The registered Glass ID is only " + ref.rating + "/100; a thin reference matches less and mismatches more.");
    if (effort < T.MIN_EFFORT) res.caveats.push(effort + " sharp shot(s) so far; no-match is not declared before " + T.MIN_EFFORT + ".");
    if (desc === "color") res.caveats.push("Colour cannot tell two similarly coloured pieces apart; a colour-only match is weaker evidence than it looks.");
    return res;
  }
  function sizeCheck(q, r) {
    var out = { query: q, reference: r, ok: null, difference: null, text: "" };
    if (!q || !r) { out.text = !q && !r ? "no mat measurement on either side" : !q ? "no mat measurement on the new capture" : "no mat measurement on the registered piece"; return out; }
    function axes(d) { var foot = [d.width, d.depth].filter(function (v) { return v; }); return [foot.length ? Math.max.apply(null, foot) : null, d.height]; }
    var a = axes(q), b = axes(r), diffs = [];
    for (var i = 0; i < 2; i++) if (a[i] && b[i]) diffs.push(Math.abs(a[i] - b[i]) / Math.max(a[i], b[i]));
    if (!diffs.length) { out.text = "sizes could not be compared"; return out; }
    out.difference = +Math.max.apply(null, diffs).toFixed(3); out.ok = out.difference <= 0.25;
    var fmt = function (d) { return [d.width, d.depth, d.height].filter(function (v) { return v; }).map(Math.round).join(" × ") + " mm"; };
    out.text = fmt(q) + " vs " + fmt(r) + " on the mat, " + (out.ok ? "within " : "") + Math.round(out.difference * 100) + "%" + (out.ok ? "" : " apart, more than one piece can be");
    return out;
  }
  function reasonsFor(res, ref) {
    var out = [], head = res.breakdown[res.descriptor];
    if (head && head.available) {
      out.push(WORDS[res.descriptor] + ": " + res.confidence + "/100 — the " + Math.min(T.TOP_SIMS, head.strong) + " strongest of " + res.shots + " shots average " + (head.quality || 0).toFixed(2) + " against the registered views, " + head.views_matched + " distinct reference view(s) matched at " + T.STRONG_T.toFixed(2) + " or better (spread " + (head.spread || 0).toFixed(2) + " of " + T.SPREAD_TARGET + " views).");
      if (head.shots_with_descriptor < res.shots) out.push("Only " + head.shots_with_descriptor + " of " + res.shots + " shots carry the " + WORDS[res.descriptor].toLowerCase() + " descriptor; the rest score 0 under it.");
    } else out.push("Neither side has a descriptor the other also has, so nothing could be compared.");
    ["dino", "color", "dhash"].forEach(function (d) {
      if (d === res.descriptor) return;
      var b = res.breakdown[d];
      if (b.available) out.push(WORDS[d] + ", for comparison: " + b.confidence + "/100, " + b.views_matched + " view(s) matched.");
      else if (d === "dino") out.push("No AI texture comparison: " + b.why + ". Colour alone cannot separate two pieces of the same palette.");
    });
    out.push("Size: " + res.size.text + ".");
    return out;
  }

  /* ---- rendering: candidate rows ---------------------------------------------- */
  function rowHtml(it, o) {
    var img = it.image ? '<img src="' + esc(thumb(it.image, 160)) + '" alt="" loading="lazy" onerror="this.parentNode.textContent=\'no photo\'">' : "no photo";
    var who = [it.maker, it.kind === "mine" ? "one of mine" : it.row_id ? "№ " + it.row_id : ""].filter(Boolean).join(" · ");
    var fpw = it.fingerprint ? (it.fingerprint.rating != null ? "Glass ID " + it.fingerprint.rating : "") + (it.fingerprint.views ? " · " + it.fingerprint.views + " views" : "") + (it.fingerprint.has_embeddings ? " · AI" : "") : "";
    var col = colourOf(it.verdict);
    return '<div class="gk-row" data-ref="' + esc(it.ref) + '"><div class="gk-img">' + img + "</div>" +
      '<div class="gk-meta"><b>' + esc(it.title || "Untitled") + (o && o.chosen === it.ref ? '<span class="gk-pill">the piece you chose</span>' : "") + "</b><small>" + esc(who) + "</small>" + (fpw ? "<small>" + esc(fpw) + (it.views_matched != null ? " · " + it.views_matched + " view(s) matched" : "") + "</small>" : "") + "</div>" +
      '<div class="gk-score" style="color:' + col + '"><b>' + (it.confidence != null ? it.confidence : "—") + "</b><small>" + esc(it.label || "") + "</small></div>" +
      '<span style="display:flex;flex-direction:column;gap:5px"><button type="button" class="gk-btn" data-act="compare" data-ref="' + esc(it.ref) + '">Compare</button>' +
      (o && o.onPick ? '<button type="button" class="gk-btn hot" data-act="pick" data-ref="' + esc(it.ref) + '">' + esc(o.pickLabel || "Use") + "</button>" : "") + "</span></div>";
  }
  function candidateRows(el, result, o) {
    ensureCss(); o = o || {};
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    el.classList.add("gk");
    var items = (result && result.candidates) || [];
    var top = items[0];
    var head = o.title || "Already registered?";
    var line;
    if (!items.length) line = (result && result.caveats && result.caveats[0]) || "No registered piece to compare against yet.";
    else if (top.confidence >= T.MATCH_READY) line = "<b style=\"color:" + colourOf("match") + "\">This looks like “" + esc(top.title) + "”</b> (" + top.confidence + "/100, " + esc(top.label) + "). Compare before you decide.";
    else if (top.confidence >= T.NO_MATCH) line = "Closest on file: “" + esc(top.title) + "” at " + top.confidence + "/100 — inconclusive. Compare, or take more angles.";
    else line = "Nothing on file looks like it (best " + top.confidence + "/100 against “" + esc(top.title) + "”).";
    var cav = (result && result.caveats || []).map(function (c) { return '<p class="fine">' + esc(c) + "</p>"; }).join("");
    el.innerHTML = '<div class="gk-head"><h3>' + esc(head) + "</h3>" + (o.onAgain ? '<button type="button" class="link" data-act="again">Check again</button>' : "") + "</div>" +
      "<p>" + line + "</p>" + items.map(function (it) { return rowHtml(it, o); }).join("") + cav +
      (result && result.corpus ? '<p class="fine">Compared with ' + result.corpus.public + " public piece(s)" + (result.corpus.mine ? " and " + result.corpus.mine + " of yours" : "") + (result.corpus.signed_in ? "" : " — sign in to include your own pieces") + ", by " + esc(WORDS[result.descriptor] || result.descriptor).toLowerCase() + ".</p>" : "") +
      '<div class="gk-compare"></div>';
    el.onclick = function (ev) {
      var b = ev.target.closest("button"); if (!b) return;
      if (b.dataset.act === "again" && o.onAgain) { o.onAgain(); return; }
      if (b.dataset.act === "compare" || b.dataset.act === "pick") {
        var it = items.filter(function (x) { return x.ref === b.dataset.ref; })[0];
        if (!it) return;
        if (b.dataset.act === "pick" && o.onPick) o.onPick(it);
        else if (b.dataset.act === "compare" && o.onCompare) o.onCompare(it, el.querySelector(".gk-compare"));
      }
    };
  }

  /* ---- rendering: the compare panel ---------------------------------------------- */
  function bar(label, b) {
    var on = b && b.available, v = on ? b.confidence : 0, col = on ? colourOf(b.verdict) : "var(--line)";
    return '<div class="gk-bar' + (on ? "" : " off") + '"><span>' + esc(label) + "</span><i><b style=\"width:" + v + "%;background:" + col + '"></b></i><em>' + (on ? v : "—") + "</em></div>";
  }
  function strip(items, label) {
    if (!items || !items.length) return '<div class="gk-strip"><div>' + esc(label || "no photos") + "</div></div>";
    return '<div class="gk-strip">' + items.map(function (it) { return "<div>" + (it.src ? '<img src="' + esc(it.src) + '" alt="" onerror="this.remove()">' : esc(it.text || "")) + (it.tag ? "<span>" + esc(it.tag) + "</span>" : "") + "</div>"; }).join("") + "</div>";
  }
  /* o: {item (title/maker/kind/ref/image), result (compare shape), piece (bundle with images), shots:[{seq,url}], refImages:{file:url}, onDecide(decision,item), decision, onClose} */
  function comparePanel(el, o) {
    ensureCss(); o = o || {};
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    el.classList.add("gk", "gk-block");
    var it = o.item || {}, r = o.result || {}, p = o.piece || {};
    var refImgs = [];
    (p.images || (it.image ? [it.image] : [])).slice(0, 8).forEach(function (u, i) { refImgs.push({ src: thumb(u, 320), tag: i === 0 ? "on file" : "" }); });
    var byFile = o.refImages || {};
    ((r.carried_by && r.carried_by.reference_views) || []).forEach(function (f) { if (f && byFile[f] && refImgs.length < 12) refImgs.push({ src: byFile[f], tag: String(f).replace("frames/", "") }); });
    var shotsBy = {}; (o.shots || []).forEach(function (s) { shotsBy[s.seq] = s; });
    var mine = ((r.carried_by && r.carried_by.shots) || []).map(function (seq) { var s = shotsBy[seq]; return s ? { src: s.url, tag: "shot " + seq } : null; }).filter(Boolean);
    if (!mine.length) mine = (o.shots || []).slice(-6).map(function (s) { return { src: s.url, tag: "shot " + s.seq }; });
    var col = colourOf(r.verdict);
    var fp = it.fingerprint || p.fingerprint || {};
    var words = [it.maker || p.maker, fp.rating != null ? "Glass ID " + fp.rating + "/100" : "", fp.views ? fp.views + " views on file" : "", fp.dimensions_mm ? "measured " + [fp.dimensions_mm.width, fp.dimensions_mm.depth, fp.dimensions_mm.height].filter(Boolean).map(Math.round).join(" × ") + " mm" : ""].filter(Boolean).join(" · ");
    var dec = o.decision && o.decision.object && o.decision.object.ref === it.ref ? o.decision.decision : null;
    el.innerHTML = '<div class="gk-head"><h3>' + esc(it.title || p.title || "Reference") + ' <span style="color:' + col + '">' + (r.confidence != null ? r.confidence + "/100 · " + esc(r.label || "") : "") + "</span></h3>" + (o.onClose ? '<button type="button" class="link" data-act="close">Close</button>' : "") + "</div>" +
      (words ? '<p class="fine">' + esc(words) + "</p>" : "") +
      '<div class="gk-side"><div><h4>On file</h4>' + strip(refImgs, "no photo on file") + "</div><div><h4>Your shots" + (mine.length && r.carried_by && r.carried_by.shots && r.carried_by.shots.length ? " that carried the score" : "") + "</h4>" + strip(mine, "no shots yet") + "</div></div>" +
      "<h4>What the score is made of</h4>" + bar("AI texture", r.breakdown && r.breakdown.dino) + bar("Colour", r.breakdown && r.breakdown.color) + bar("Shape", r.breakdown && r.breakdown.dhash) +
      '<div class="gk-bar"><span>Views matched</span><i><b style="width:' + Math.min(100, Math.round(((r.views_matched || 0) / T.SPREAD_TARGET) * 100)) + "%;background:" + col + '"></b></i><em>' + (r.views_matched || 0) + "/" + T.SPREAD_TARGET + "</em></div>" +
      '<ul class="gk-reasons">' + (r.reasons || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" +
      (r.caveats && r.caveats.length ? '<p class="fine">' + r.caveats.map(esc).join(" ") + "</p>" : "") +
      (o.onDecide ? '<div class="gk-btns"><button type="button" class="gk-btn ok" data-act="same"' + (dec === "match_confirmed" ? " disabled" : "") + ">" + (dec === "match_confirmed" ? "✓ Same piece" : "Same piece") + '</button><button type="button" class="gk-btn warn" data-act="different"' + (dec === "match_rejected" ? " disabled" : "") + ">" + (dec === "match_rejected" ? "✓ Different piece" : "Different piece") + "</button>" + (it.check ? '<a class="gk-btn" href="' + esc(it.check) + '">Check it with the camera</a>' : "") + "</div>" +
        '<p class="fine">Same piece and Different piece are your call, recorded with the session when you send it; the numbers are evidence, not a verdict.</p>' : "");
    el.onclick = function (ev) {
      var b = ev.target.closest("button"); if (!b) return;
      if (b.dataset.act === "close" && o.onClose) { o.onClose(); return; }
      if (b.dataset.act === "same" && o.onDecide) o.onDecide("match_confirmed", it);
      if (b.dataset.act === "different" && o.onDecide) o.onDecide("match_rejected", it);
    };
  }

  /* ---- rendering: send for analysis ------------------------------------------------ */
  /* o: {signedIn, loginUrl, decision (string), note, sent ({manifest_id,...}), busy, status, statusKind, onSend(decision, note), onChange(decision, note)} */
  function sendBlock(el, o) {
    ensureCss(); o = o || {};
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    el.classList.add("gk", "gk-block");
    var cur = o.decision || "undecided";
    var radios = DECISIONS.filter(function (d) { return !o.hide || o.hide.indexOf(d[0]) < 0; }).map(function (d) {
      return '<label class="' + (d[0] === cur ? "on" : "") + '"><input type="radio" name="gk-dec" value="' + d[0] + '"' + (d[0] === cur ? " checked" : "") + "><span>" + esc(d[1]) + "<small>" + esc(d[2]) + "</small></span></label>";
    }).join("");
    var status = o.sent ? '<p class="gk-status ok">Sent as <b>' + esc(o.sent.manifest_id) + "</b> · " + (o.sent.n_images || 0) + " photo(s)" + (o.sent.mat_detected ? " · mat seen" : "") + ". Thank you — it goes into the calibration set.</p>" :
      (o.status ? '<p class="gk-status ' + (o.statusKind || "") + '">' + esc(o.status) + "</p>" : "");
    el.innerHTML = '<h3>Send this session for analysis</h3>' +
      '<p class="fine">Hands the shots, the numbers behind them, the candidates and your decision to the Glass Database so the matching can be checked against real pieces. Only administrators see it. Nothing is published.</p>' +
      (o.signedIn ? '<div class="gk-radios">' + radios + "</div>" +
        '<textarea id="gk-note" placeholder="A note (optional): what the piece is, what you saw, what went wrong">' + esc(o.note || "") + "</textarea>" +
        '<div class="gk-btns"><button type="button" class="gk-btn hot" data-act="send"' + (o.busy || o.sent ? " disabled" : "") + ">" + (o.busy ? "Sending…" : o.sent ? "Sent" : "Send for analysis") + "</button></div>" + status :
        '<p>Sign in to send it. <a href="' + esc(o.loginUrl || "#") + '" style="color:var(--hot)">Sign in</a></p>');
    el.onclick = function (ev) {
      var lab = ev.target.closest("label");
      if (lab) { setTimeout(function () { var r = el.querySelector("input[name=gk-dec]:checked"); el.querySelectorAll(".gk-radios label").forEach(function (l) { l.classList.toggle("on", l.contains(r)); }); if (o.onChange) o.onChange(r ? r.value : cur, (el.querySelector("#gk-note") || {}).value || ""); }, 0); return; }
      var b = ev.target.closest("button"); if (!b || b.dataset.act !== "send" || !o.onSend) return;
      var r = el.querySelector("input[name=gk-dec]:checked");
      o.onSend(r ? r.value : cur, (el.querySelector("#gk-note") || {}).value || "");
    };
  }

  /* ---- the session manifest ------------------------------------------------------- */
  /* o: {kind, page, fingerprint, frames:[{blob,seq,...}], hero, reference, match, candidates, decision, device, session, params, mat, note} */
  function buildManifest(o) {
    if (!window.JSZip) return Promise.reject(new Error("JSZip is not loaded"));
    var zip = new JSZip(), images = [];
    var frames = o.frames || [];
    frames.forEach(function (f, i) {
      if (!f.blob) return;
      var name = "images/" + String(f.seq || (i + 1)).padStart(3, "0") + ".jpg";
      zip.file(name, f.blob); images.push({ file: name, seq: f.seq || (i + 1), role: "frame", t: f.t || null, sharp: f.sharp != null ? +(+f.sharp).toFixed(1) : null });
    });
    if (o.hero) { zip.file("images/hero.jpg", o.hero); images.push({ file: "images/hero.jpg", role: "hero" }); }
    var man = { tool: "glass-id-capture-manifest", version: 1, kind: o.kind, created: new Date().toISOString(),
      app: { page: o.page || location.pathname.split("/").pop(), version: VERSION, url: location.origin + location.pathname },
      device: o.device || deviceInfo(null, null), session: o.session || {}, params: o.params || {}, thresholds: T,
      fingerprint: o.fingerprint ? stripFp(o.fingerprint) : undefined, reference: o.reference || undefined, match: o.match || undefined,
      candidates: o.candidates ? { source: "server", descriptor: o.candidates.descriptor, margin: o.candidates.margin, ambiguous: o.candidates.ambiguous, corpus: o.candidates.corpus, checked_at: o.candidates.checked_at || new Date().toISOString(),
        items: (o.candidates.candidates || []).map(function (c) { return { ref: c.ref, kind: c.kind, id: c.id, row_id: c.row_id, title: c.title, maker: c.maker, confidence: c.confidence, verdict: c.verdict, label: c.label, descriptor: c.descriptor, views_matched: c.views_matched, breakdown: c.breakdown, size: c.size, reasons: c.reasons, carried_by: c.carried_by, pairs: c.pairs }; }) } : undefined,
      comparisons: o.comparisons || undefined,
      decision: o.decision || { decision: "undecided", at: new Date().toISOString() },
      mat: o.mat || undefined, note: o.note || undefined, images: images };
    zip.file("manifest.json", JSON.stringify(man, null, 1));
    return zip.generateAsync({ type: "blob" }).then(function (blob) { return { blob: blob, manifest: man }; });
  }
  function send(o) {
    if (!G) return Promise.reject(new Error("site.js is not loaded"));
    return buildManifest(o).then(function (b) {
      return G.json("/me/captures", { method: "POST", headers: { "Content-Type": "application/zip" }, body: b.blob }).then(function (j) { j.manifest = b.manifest; j.bytes_sent = b.blob.size; return j; });
    });
  }

  window.GlassID = { VERSION: VERSION, T: T, DECISIONS: DECISIONS, deviceInfo: deviceInfo, stripFp: stripFp, candidates: candidates, compare: compare, lookup: lookup, recent: recent, piece: piece,
    localCompare: localCompare, reasonsFor: reasonsFor, sizeCheck: sizeCheck, candidateRows: candidateRows, comparePanel: comparePanel, sendBlock: sendBlock, buildManifest: buildManifest, send: send,
    decisionLabel: decisionLabel, esc: esc, colourOf: colourOf, ensureCss: ensureCss };
})();
