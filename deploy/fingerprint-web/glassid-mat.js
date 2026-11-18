/* Glass ID — the Photo Mat on the phone. One global, window.GlassMat.

   The printed mat (capture-mat.pdf, US Letter) has four ArUco markers (DICT_4X4_50,
   ids 0-3) at known places and a strip of twelve colour patches. Seen in a frame, they
   give, without a compass and on any phone:

     pose      where the camera is round the piece: azimuth (degrees round the grey
               placement area, in the mat's own frame) and elevation (above the mat
               plane), from a homography of the marker corners and a focal length
               estimated from the session's tilted views (Zhang's constraints, the same
               as glowtbook/matdetect.py on the server)
     crop      the part of the picture above the grey placement area, so the piece's
               descriptors are not computed on the table, the room or the mat's print
     neutral   the strip's five grey patches, so colour can be corrected for this light
               and this camera: per channel, in linear light, a gain and an offset fitted
               to the patches' nominal greys (clipped patches left out); refused unless
               the patches read in order, with enough range

   Geometry is page millimetres, x right, y down from the page's top-left, z into the
   table (so the camera is at z < 0), exactly as in glowtbook/matdetect.py.

   Needs vendor/js-aruco2 (CV, AR) loaded first. Everything degrades: no markers, no
   pose; strip out of view, no neutral (the session's last good one may stand in). */
(function () {
  "use strict";
  var MARKER_MM = 29.89, X0 = 15.24, X1 = 170.69, Y0 = 49.53, Y1 = 176.53;
  var ORIGIN = { 0: [X0, Y0], 1: [X1, Y0], 2: [X0, Y1], 3: [X1, Y1] };
  function cornersMm(id) { var o = ORIGIN[id]; return [[o[0], o[1]], [o[0] + MARKER_MM, o[1]], [o[0] + MARKER_MM, o[1] + MARKER_MM], [o[0], o[1] + MARKER_MM]]; }
  var PLACE = [50.0, 83.0, 200.5, 174.0];                    // the grey placement area (x0, y0, x1, y1)
  var CENTRE = [(PLACE[0] + PLACE[2]) / 2, (PLACE[1] + PLACE[3]) / 2];
  // the colour strip: 12 patches 15.5 mm wide from x = 15 mm, y 217-229.5 mm;
  // 0 white, 1 #C0C0C0, 2 #808080, 3 #404040, 4 black, then saturated colours
  var STRIP = { x0: 15.0, w: 15.5, y0: 217.0, y1: 229.5, neutrals: 5 };
  var LIMITS = { MIN_MARKERS: 2, MAX_RMS: 3.5, MIN_SIDE: 14, MIN_EL: 12, TOP_EL: 80, HFOV: 65, CROP_H: 150,
    NEUTRAL_RANGE: 60, NEUTRAL_STEP: 4, MAX_GAIN: 4 };

  var detector = null;
  function getDetector() {
    if (detector) return detector;
    if (!window.AR || !window.AR.Detector) return null;
    try { detector = new window.AR.Detector({ dictionaryName: "ARUCO_4X4_1000", maxHammingDistance: 0 }); } catch (_) { detector = null; }
    return detector;
  }

  /* ---- small linear algebra -------------------------------------------------- */
  function solve(A, b) {                                     // Gaussian elimination, partial pivoting
    var n = b.length, M = A.map(function (r, i) { return r.concat([b[i]]); }), i, j, k;
    for (i = 0; i < n; i++) {
      var p = i; for (k = i + 1; k < n; k++) if (Math.abs(M[k][i]) > Math.abs(M[p][i])) p = k;
      if (Math.abs(M[p][i]) < 1e-12) return null;
      var t = M[i]; M[i] = M[p]; M[p] = t;
      for (k = i + 1; k < n; k++) { var f = M[k][i] / M[i][i]; for (j = i; j <= n; j++) M[k][j] -= f * M[i][j]; }
    }
    var x = new Array(n);
    for (i = n - 1; i >= 0; i--) { var s = M[i][n]; for (j = i + 1; j < n; j++) s -= M[i][j] * x[j]; x[i] = s / M[i][i]; }
    return x;
  }
  function mul3(A, B) { var C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]; for (var i = 0; i < 3; i++) for (var j = 0; j < 3; j++) for (var k = 0; k < 3; k++) C[i][j] += A[i][k] * B[k][j]; return C; }
  function inv3(m) {
    var a = m[0][0], b = m[0][1], c = m[0][2], d = m[1][0], e = m[1][1], f = m[1][2], g = m[2][0], h = m[2][1], i = m[2][2];
    var A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g, det = a * A + b * B + c * C;
    if (Math.abs(det) < 1e-15) return null;
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det], [B / det, (a * i - c * g) / det, -(a * f - c * d) / det], [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]];
  }
  function apply(H, x, y) { var w = H[2][0] * x + H[2][1] * y + H[2][2]; return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w]; }
  function normT(P) {
    var mx = 0, my = 0, d = 0, i; for (i = 0; i < P.length; i++) { mx += P[i][0]; my += P[i][1]; } mx /= P.length; my /= P.length;
    for (i = 0; i < P.length; i++) d += Math.hypot(P[i][0] - mx, P[i][1] - my); d = d / P.length || 1;
    var s = Math.SQRT2 / d; return [[s, 0, -s * mx], [0, s, -s * my], [0, 0, 1]];
  }
  /* least-squares homography mapping src (mm) to dst (px), normalised for conditioning */
  function homography(src, dst) {
    if (src.length < 4) return null;
    var Ts = normT(src), Td = normT(dst), A = [], b = [], i;
    for (i = 0; i < src.length; i++) {
      var s = apply(Ts, src[i][0], src[i][1]), d = apply(Td, dst[i][0], dst[i][1]);
      A.push([s[0], s[1], 1, 0, 0, 0, -d[0] * s[0], -d[0] * s[1]]); b.push(d[0]);
      A.push([0, 0, 0, s[0], s[1], 1, -d[1] * s[0], -d[1] * s[1]]); b.push(d[1]);
    }
    var AtA = [], Atb = [], r, c, k;
    for (r = 0; r < 8; r++) { AtA.push(new Array(8).fill(0)); Atb.push(0); }
    for (k = 0; k < A.length; k++) for (r = 0; r < 8; r++) { Atb[r] += A[k][r] * b[k]; for (c = 0; c < 8; c++) AtA[r][c] += A[k][r] * A[k][c]; }
    var h = solve(AtA, Atb); if (!h) return null;
    var Hn = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]], Tdi = inv3(Td);
    if (!Tdi) return null;
    var H = mul3(mul3(Tdi, Hn), Ts), z = H[2][2];
    return H.map(function (row) { return row.map(function (v) { return v / z; }); });
  }
  /* focal length (px) implied by a plane homography, principal point at the centre and
     square pixels; null when the view is too square-on to say (glowtbook/matdetect._focal_from_h) */
  function focalFromH(H, w, h) {
    var M = mul3([[1, 0, -w / 2], [0, 1, -h / 2], [0, 0, 1]], H);
    var h1 = [M[0][0], M[1][0], M[2][0]], h2 = [M[0][1], M[1][1], M[2][1]], ests = [];
    var d1 = h1[2] * h2[2]; if (Math.abs(d1) > 1e-12) ests.push(-(h1[0] * h2[0] + h1[1] * h2[1]) / d1);
    var d2 = h1[2] * h1[2] - h2[2] * h2[2]; if (Math.abs(d2) > 1e-12) ests.push(-(h1[0] * h1[0] + h1[1] * h1[1] - h2[0] * h2[0] - h2[1] * h2[1]) / d2);
    var lo = 0.4 * Math.max(w, h), hi = 3.0 * Math.max(w, h);
    ests = ests.filter(function (e) { return e > 0; }).map(Math.sqrt).filter(function (e) { return e >= lo && e <= hi; });
    if (!ests.length) return null; ests.sort(function (a, b) { return a - b; });
    return ests.length === 2 ? (ests[0] + ests[1]) / 2 : ests[0];
  }
  function median(xs) { if (!xs.length) return null; var s = xs.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

  /* ---- the session: focal estimates and the last good neutral ------------------- */
  var session = { focals: [], neutral: null, neutralAt: 0 };
  function reset() { session = { focals: [], neutral: null, neutralAt: 0 }; }
  function focalFor(w, h) {
    var m = session.focals.length >= 3 ? median(session.focals.slice(-25)) : null;
    if (m) return m * Math.max(w, h);                        // stored relative to the long edge
    return (w / 2) / Math.tan(LIMITS.HFOV * Math.PI / 360);
  }

  /* camera pose from H and focal f: R columns r1, r2 (the page's x, y axes), t; the
     camera centre C = -R^T t in page mm (z < 0 above the table) */
  function poseFromH(H, f, w, h) {
    var Ki = [[1 / f, 0, -w / (2 * f)], [0, 1 / f, -h / (2 * f)], [0, 0, 1]], M = mul3(Ki, H);
    var m1 = [M[0][0], M[1][0], M[2][0]], m2 = [M[0][1], M[1][1], M[2][1]], m3 = [M[0][2], M[1][2], M[2][2]];
    var n1 = Math.hypot(m1[0], m1[1], m1[2]), n2 = Math.hypot(m2[0], m2[1], m2[2]); if (!n1 || !n2) return null;
    var lam = 2 / (n1 + n2), sgn = m3[2] * lam < 0 ? -1 : 1; lam *= sgn;  // the mat is in front of the camera
    var r1 = m1.map(function (v) { return v / n1 * sgn; }), r2 = m2.map(function (v) { return v / n2 * sgn; });
    var dp = r1[0] * r2[0] + r1[1] * r2[1] + r1[2] * r2[2]; r2 = [r2[0] - dp * r1[0], r2[1] - dp * r1[1], r2[2] - dp * r1[2]];
    var n2b = Math.hypot(r2[0], r2[1], r2[2]); r2 = r2.map(function (v) { return v / n2b; });
    var r3 = [r1[1] * r2[2] - r1[2] * r2[1], r1[2] * r2[0] - r1[0] * r2[2], r1[0] * r2[1] - r1[1] * r2[0]];
    var t = m3.map(function (v) { return v * lam; });
    var R = [[r1[0], r2[0], r3[0]], [r1[1], r2[1], r3[1]], [r1[2], r2[2], r3[2]]];
    var C = [0, 1, 2].map(function (i) { return -(R[0][i] * t[0] + R[1][i] * t[1] + R[2][i] * t[2]); });
    return { R: R, t: t, C: C, f: f, w: w, h: h };
  }
  function project(P, X, Y, Z) {
    var R = P.R, x = R[0][0] * X + R[0][1] * Y + R[0][2] * Z + P.t[0], y = R[1][0] * X + R[1][1] * Y + R[1][2] * Z + P.t[1], z = R[2][0] * X + R[2][1] * Y + R[2][2] * Z + P.t[2];
    if (z <= 1e-6) return null;
    return [P.f * x / z + P.w / 2, P.f * y / z + P.h / 2];
  }

  /* ---- what one picture says -------------------------------------------------- */
  /* img: {width, height, data (RGBA)}; returns {ok, markers, ids, H, pose: {az, el, height_mm,
     dist_mm, rms_px, f}, neutral, why}. ok means a usable pose. */
  function analyze(img) {
    var out = { ok: false, markers: 0, ids: [], H: null, pose: null, neutral: null, why: "" };
    var det = getDetector(); if (!det) { out.why = "marker detector not loaded"; return out; }
    var found; try { found = det.detect(img); } catch (e) { out.why = "detector failed"; return out; }
    var src = [], dst = [], minSide = 1e9;
    (found || []).forEach(function (m) {
      if (!(m.id in ORIGIN) || out.ids.indexOf(m.id) >= 0) return;
      out.ids.push(m.id);
      var cm = cornersMm(m.id);
      for (var k = 0; k < 4; k++) {
        src.push(cm[k]); dst.push([m.corners[k].x, m.corners[k].y]);
        var nx = m.corners[(k + 1) % 4]; minSide = Math.min(minSide, Math.hypot(nx.x - m.corners[k].x, nx.y - m.corners[k].y));
      }
    });
    out.markers = out.ids.length; out.ids.sort();
    if (out.markers < LIMITS.MIN_MARKERS) { out.why = out.markers ? "only one marker in view" : "no markers in view"; return out; }
    if (minSide < LIMITS.MIN_SIDE) { out.why = "markers too small - move closer"; return out; }
    var H = homography(src, dst); if (!H) { out.why = "no homography"; return out; }
    var rms = 0; for (var i = 0; i < src.length; i++) { var p = apply(H, src[i][0], src[i][1]); rms += (p[0] - dst[i][0]) * (p[0] - dst[i][0]) + (p[1] - dst[i][1]) * (p[1] - dst[i][1]); }
    rms = Math.sqrt(rms / src.length); out.H = H;
    if (rms > LIMITS.MAX_RMS) { out.why = "markers don't fit the mat (" + rms.toFixed(1) + " px)"; return out; }
    var w = img.width, h = img.height, fe = focalFromH(H, w, h);
    if (fe) session.focals.push(fe / Math.max(w, h));
    var P = poseFromH(H, focalFor(w, h), w, h);
    if (!P || P.C[2] >= -20) { out.why = "camera pose not recovered"; return out; }
    var dx = P.C[0] - CENTRE[0], dy = P.C[1] - CENTRE[1], horiz = Math.hypot(dx, dy), height = -P.C[2];
    var az = (Math.round(Math.atan2(dy, dx) * 1800 / Math.PI) / 10 + 360) % 360, el = Math.atan2(height, horiz) * 180 / Math.PI;
    out.pose = { az: +az.toFixed(1), el: +el.toFixed(1), height_mm: Math.round(height), dist_mm: Math.round(Math.hypot(horiz, height)), rms_px: +rms.toFixed(2), f: Math.round(P.f) };
    out.P = P;
    if (el < LIMITS.MIN_EL) { out.why = "too low - raise the phone a little"; return out; }
    out.ok = true;
    out.neutral = neutral(img, H);
    if (out.neutral.ok) { session.neutral = out.neutral; session.neutralAt = Date.now(); }
    return out;
  }

  /* the five neutral patches, each the median of a 5x5 grid inside the patch */
  function neutral(img, H) {
    var res = { ok: false, white: null, black: null, patches: [], why: "" }, i, gx, gy;
    for (i = 0; i < STRIP.neutrals; i++) {
      var cx = STRIP.x0 + STRIP.w * i + STRIP.w / 2, cy = (STRIP.y0 + STRIP.y1) / 2, R = [], G = [], B = [];
      for (gx = -2; gx <= 2; gx++) for (gy = -2; gy <= 2; gy++) {
        var p = apply(H, cx + gx * 2.0, cy + gy * 1.5), x = Math.round(p[0]), y = Math.round(p[1]);
        if (!(x >= 0 && y >= 0 && x < img.width && y < img.height)) { res.why = "colour strip not fully in view"; return res; }
        var o = (y * img.width + x) * 4; R.push(img.data[o]); G.push(img.data[o + 1]); B.push(img.data[o + 2]);
      }
      res.patches.push([median(R), median(G), median(B)]);
    }
    var lum = res.patches.map(function (c) { return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]; });
    for (i = 1; i < lum.length; i++) if (lum[i] > lum[i - 1] - LIMITS.NEUTRAL_STEP) { res.why = "the grey patches don't read in order (glare or shadow on the strip)"; return res; }
    if (lum[0] - lum[4] < LIMITS.NEUTRAL_RANGE) { res.why = "too little range between the white and black patches"; return res; }
    // per channel, fit measured = offset + gain x nominal in linear light over the patches
    // that are not clipped (a warm or cool light can push the white patch past 255 in one
    // channel; the greys then carry the fit)
    res.gain = []; res.offset = [];
    for (var c = 0; c < 3; c++) {
      var X = [], Y = [];
      for (i = 0; i < STRIP.neutrals; i++) if (res.patches[i][c] < 250) { X.push(NOMINAL_LIN[i]); Y.push(lin(res.patches[i][c])); }
      if (X.length < 3 || Math.max.apply(null, X) < 0.2) { res.why = "the grey patches are clipped - less light, or lower exposure"; return res; }
      var mx = X.reduce(function (a, v) { return a + v; }, 0) / X.length, my = Y.reduce(function (a, v) { return a + v; }, 0) / Y.length, sxy = 0, sxx = 0;
      for (var k = 0; k < X.length; k++) { sxy += (X[k] - mx) * (Y[k] - my); sxx += (X[k] - mx) * (X[k] - mx); }
      var g = sxy / sxx; if (!(g >= 1 / LIMITS.MAX_GAIN)) { res.why = "the strip is too dark to correct from"; return res; }
      res.gain.push(+g.toFixed(5)); res.offset.push(+(my - g * mx).toFixed(5));
    }
    res.white = res.patches[0]; res.black = res.patches[4]; res.ok = true; return res;
  }
  var NOMINAL_LIN = [255, 192, 128, 64, 0].map(function (v) { return lin(v); });
  function lin(v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
  function srgb(l) { l = Math.max(0, Math.min(1, l)); return 255 * (l <= 0.0031308 ? 12.92 * l : 1.055 * Math.pow(l, 1 / 2.4) - 0.055); }
  /* per-channel lookup tables: corrected = (linear - black) / (white - black), back to sRGB */
  function lut(n) {
    if (!n || !n.ok) return null;
    return [0, 1, 2].map(function (c) {
      var g = n.gain[c], o = n.offset[c], t = new Uint8ClampedArray(256);
      for (var v = 0; v < 256; v++) t[v] = Math.round(srgb((lin(v) - o) / g));
      return t;
    });
  }
  /* the neutral to use for a frame: its own, else the session's within maxAgeMs */
  function neutralFor(r, maxAgeMs) {
    if (r && r.neutral && r.neutral.ok) return { n: r.neutral, cc: "strip" };
    if (session.neutral && Date.now() - session.neutralAt <= (maxAgeMs || 120000)) return { n: session.neutral, cc: "session" };
    return { n: null, cc: "none" };
  }

  /* the picture region above the grey placement area, up to heightMm above the mat:
     [x0, y0, x1, y1] in the analysed image's pixels, or null */
  function cropBox(r, heightMm) {
    if (!r || !r.ok || !r.P) return null;
    var P = r.P, H = -(heightMm || LIMITS.CROP_H), xs = [], ys = [];
    [[PLACE[0], PLACE[1]], [PLACE[2], PLACE[1]], [PLACE[2], PLACE[3]], [PLACE[0], PLACE[3]]].forEach(function (c) {
      [0, H].forEach(function (z) { var p = project(P, c[0], c[1], z); if (p) { xs.push(p[0]); ys.push(p[1]); } });
    });
    if (xs.length < 6) return null;
    var x0 = Math.max(0, Math.min.apply(null, xs)), x1 = Math.min(P.w, Math.max.apply(null, xs));
    var y0 = Math.max(0, Math.min.apply(null, ys)), y1 = Math.min(P.h, Math.max.apply(null, ys));
    if (x1 - x0 < P.w * 0.12 || y1 - y0 < P.h * 0.12) return null;
    return [Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)];
  }

  /* the view cell a pose belongs to: twelve 30-degree sectors in two bands, plus overhead */
  function cellOf(pose) {
    if (!pose) return null;
    if (pose.el >= LIMITS.TOP_EL) return "mtop";
    return "m" + Math.floor(pose.az / 30) % 12 + ":" + (pose.el < 45 ? 0 : 1);
  }
  /* an earlier pose within this many degrees means this view adds little */
  function nearPose(pose, frames, azTol, elTol) {
    if (!pose) return false;
    azTol = azTol == null ? 15 : azTol; elTol = elTol == null ? 13 : elTol;
    return frames.some(function (f) { var m = f.mat; if (!m || m.az == null) return false; var d = Math.abs(((pose.az - m.az + 540) % 360) - 180); return d < azTol && Math.abs(pose.el - m.el) < elTol; });
  }

  /* the piece's colour: the page's 16x4 hue-saturation histogram over the whole (cropped)
     buffer, per mille, after the neutral correction when there is one */
  var CH_HB = 16, CH_SB = 4;
  function pieceColour(img, luts) {
    var d = img.data, hist = new Float32Array(CH_HB * CH_SB), cnt = 0;
    for (var i = 0; i < d.length; i += 4) {
      var r = d[i], g = d[i + 1], b = d[i + 2];
      if (luts) { r = luts[0][r]; g = luts[1][g]; b = luts[2][b]; }
      var mx = Math.max(r, g, b), mn = Math.min(r, g, b), df = mx - mn, h = 0;
      if (df) { if (mx === r) h = ((g - b) / df) % 6; else if (mx === g) h = (b - r) / df + 2; else h = (r - g) / df + 4; h *= 60; if (h < 0) h += 360; }
      var s = mx ? df / mx : 0, v = mx / 255;
      if (s > 0.24 && v > 0.16 && v < 0.96) { hist[Math.min(CH_HB - 1, (h / 360 * CH_HB) | 0) * CH_SB + Math.min(CH_SB - 1, (s * CH_SB) | 0)]++; cnt++; }
    }
    if (cnt < 40) return null;
    var out = new Array(CH_HB * CH_SB); for (var k = 0; k < out.length; k++) out[k] = Math.round(hist[k] / cnt * 1000);
    return out;
  }

  /* ---- the live camera: throttled detection and the piece's crop ------------------
     var live = GlassMat.live();  live.tick(video [, force])  -> the latest analysis (also
     kept as live.last, with .t, .scale and the analysed pixels); live.recent(ms);
     live.piece(r, heightMm) -> {chist_p, crop (0..1 of the frame), cc, box} or null;
     live.cropCanvas(video, crop, maxEdge) -> a canvas of the crop at camera resolution. */
  function Live(opts) {
    opts = opts || {};
    this.maxEdge = opts.maxEdge || 800; this.every = opts.every || 300;
    this.canvas = document.createElement("canvas"); this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    this.small = document.createElement("canvas"); this.small.width = this.small.height = 128;
    this.sctx = this.small.getContext("2d", { willReadFrequently: true });
    this.last = null; this.lastT = 0;
  }
  Live.prototype.tick = function (video, force) {
    var now = Date.now();
    if (!force && now - this.lastT < this.every) return this.last;
    this.lastT = now;
    var vw = video.videoWidth, vh = video.videoHeight; if (!vw || !vh) return this.last;
    var s = Math.min(1, this.maxEdge / Math.max(vw, vh)), w = Math.round(vw * s), h = Math.round(vh * s);
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    this.ctx.drawImage(video, 0, 0, w, h);
    var img = this.ctx.getImageData(0, 0, w, h), r = analyze(img);
    r.t = now; r.scale = vw / w; r.w = w; r.h = h;
    this.last = r; return r;
  };
  Live.prototype.recent = function (ms) { var r = this.last; return r && r.ok && Date.now() - r.t <= (ms || 600) ? r : null; };
  Live.prototype.piece = function (r, heightMm) {
    var box = cropBox(r, heightMm); if (!box) return null;
    var nf = neutralFor(r), luts = lut(nf.n);
    this.sctx.drawImage(this.canvas, box[0], box[1], box[2] - box[0], box[3] - box[1], 0, 0, 128, 128);
    var ch = pieceColour(this.sctx.getImageData(0, 0, 128, 128), luts);
    return { chist_p: ch, cc: nf.cc, box: box, crop: [+(box[0] / r.w).toFixed(4), +(box[1] / r.h).toFixed(4), +(box[2] / r.w).toFixed(4), +(box[3] / r.h).toFixed(4)] };
  };
  Live.prototype.cropCanvas = function (video, crop, maxEdge) {
    var vw = video.videoWidth, vh = video.videoHeight; if (!vw || !crop) return null;
    var sx = crop[0] * vw, sy = crop[1] * vh, sw = (crop[2] - crop[0]) * vw, sh = (crop[3] - crop[1]) * vh;
    var k = Math.min(1, (maxEdge || 384) / Math.max(sw, sh)), c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(sw * k)); c.height = Math.max(1, Math.round(sh * k));
    c.getContext("2d").drawImage(video, sx, sy, sw, sh, 0, 0, c.width, c.height);
    return c;
  };
  /* what a frame records about the mat */
  function frameMat(r, piece) {
    if (!r || !r.ok) return null;
    return { az: r.pose.az, el: r.pose.el, n: r.markers, rms: r.pose.rms_px, h: r.pose.height_mm, d: r.pose.dist_mm, cc: piece ? piece.cc : "none" };
  }
  /* a short line for the camera's mat chip */
  function chipText(r) {
    if (!r) return "Mat —";
    if (r.ok) return "Mat ✓ " + Math.round(r.pose.az) + "° · " + Math.round(r.pose.el) + "° up" + (r.neutral && r.neutral.ok ? " · colour ref" : "");
    return "Mat — " + (r.why || "not seen");
  }

  window.GlassMat = { live: function (o) { return new Live(o); }, frameMat: frameMat, chipText: chipText, analyze: analyze, cropBox: cropBox, cellOf: cellOf, nearPose: nearPose, lut: lut, neutralFor: neutralFor,
    pieceColour: pieceColour, reset: reset, homography: homography, focalFromH: focalFromH, poseFromH: poseFromH, project: project,
    available: function () { return !!getDetector(); }, LIMITS: LIMITS, PLACE: PLACE, CENTRE: CENTRE, STRIP: STRIP, cornersMm: cornersMm,
    session: function () { return session; } };
})();
