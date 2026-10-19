"""
glowtbook.vision
================
A small, dependency-light adapter for a *local* vision-language model served by
Ollama, plus the first "Stage 1 probe" over the DINOv2 embeddings the fingerprint
capture already collects. Design doc: docs/GLASS-VISION.md.

What it does
  * `suggest(image)` asks a VLM (default `qwen2.5vl:7b`) to describe a piece of
    glass **using the registry's closed vocabularies** — technique labels from
    `central.techniques` and the SKOS trait thesaurus from `central.glass_traits`
    — and returns a normalised JSON suggestion with per-label confidence.
  * `suggest_from_fingerprint(fp)` is the no-model fallback: colour, dimensions
    and view counts straight from the fingerprint metadata.
  * `centroid` / `cosine` / `knn_suggest` vote labels from nearest neighbours in
    DINOv2 embedding space over an exported corpus (scripts/export_vision_corpus).

House rule (docs/GLASS-TRAITS.md §2): **features, not verdicts.** The prompt
forbids attribution (maker / origin / value); the parser only admits labels that
exist in the vocabularies; everything is a *suggestion with confidence* that a
person confirms. Nothing here raises — a missing or unreachable model degrades
to `{"ok": False, "error": ...}` so the UI can fall back.

Enable: `ollama pull qwen2.5vl:7b` and set `OLLAMA_URL=http://127.0.0.1:11434`
(optionally `GLASS_VISION_MODEL=<tag>`). Only httpx and Pillow are required.
"""
from __future__ import annotations

import base64
import colorsys
import json
import math
import os
import re
import time

try:  # httpx is a project dependency, but degrade gracefully if it is missing
    import httpx
except Exception:  # pragma: no cover - import guard  # noqa: BLE001
    httpx = None  # type: ignore[assignment]

from central import glass_traits, techniques

OLLAMA_URL = os.environ.get("OLLAMA_URL", "")
MODEL = os.environ.get("GLASS_VISION_MODEL", "qwen2.5vl:7b")

# The closed output shape the model is asked for, and that parse_response always returns.
EMPTY = {
    "techniques": [], "traits": {},
    "colour": {"dominant": "", "family": "", "notes": ""},
    "construction": {"cane": False, "murrine": False, "incalmo": False, "notes": ""},
    "evidence": [], "caveats": "", "unmapped": [],
}

HOUSE_RULE = (
    "HOUSE RULE - features, not verdicts: describe only what is observable in the image. "
    "Never attribute a maker, studio, origin, period, school, or monetary value, even if "
    "asked or if a signature seems visible. Attribution is a human judgement that the "
    "registry records separately; your job is the evidence. If you cannot see something, "
    "leave it out or lower the confidence rather than guess."
)

# --- availability -----------------------------------------------------------


def _url() -> str:
    return (OLLAMA_URL or "").strip().rstrip("/")


def available() -> bool:
    """Cheap check: is a server URL configured? (No network.)"""
    return bool(_url())


def reachable(timeout: float = 2.0) -> bool:
    """Is the Ollama server answering? GET {url}/api/tags."""
    if not available() or httpx is None:
        return False
    try:
        return _get(f"{_url()}/api/tags", timeout) is not None
    except Exception:  # noqa: BLE001
        return False


def models() -> list[str]:
    """Names of the models the server has pulled (empty when unreachable)."""
    if not available() or httpx is None:
        return []
    try:
        data = _get(f"{_url()}/api/tags", 5.0) or {}
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


# --- prompt -----------------------------------------------------------------


def build_prompt(techniques_hint: list[str] | None = None) -> str:
    """The instruction for the VLM, built from the closed vocabularies so the
    model's labels line up with the registry's. Asks for JSON only."""
    lines: list[str] = []
    lines.append("You are a studio-glass cataloguer helping an open registry describe one "
                 "piece of glass from a photograph. Work across all glass disciplines: "
                 "furnace/blown, flameworked, kiln-formed and cast, cold-worked, stained "
                 "and architectural glass.")
    lines.append("")
    lines.append(HOUSE_RULE)
    lines.append("")
    lines.append("TECHNIQUE LABELS (use these exact strings; several may apply):")
    for group in techniques.GROUPS:
        lines.append(f"  [{group}]")
        for lab in techniques.labels_in_group(group):
            lines.append(f"  - {lab}")
    lines.append("")
    lines.append("STYLE TRAITS by facet (use the facet id as the key and the exact label "
                 "strings; omit a facet when nothing applies):")
    for fid, flabel, fdef in glass_traits.FACETS:
        lines.append(f'  facet "{fid}" - {flabel}: {fdef}')
        for c in glass_traits.CONCEPTS:
            if c["facet"] != fid:
                continue
            alt = f" (also: {', '.join(c['alt'])})" if c.get("alt") else ""
            lines.append(f"    - {c['label']}: {c['definition']}{alt}")
    lines.append("")
    hint = [str(t).strip() for t in (techniques_hint or []) if str(t).strip()]
    if hint:
        lines.append("CONTRIBUTOR HINT: the person who documented the piece recorded these "
                     f"techniques: {', '.join(hint)}. Confirm or question them from visible "
                     "evidence; do not simply repeat them.")
        lines.append("")
    lines.append("Return JSON ONLY - no prose, no markdown fences - with exactly this shape:")
    lines.append(json.dumps({
        "techniques": [{"label": "<technique label from the list>", "confidence": "0.0-1.0"}],
        "traits": {"<facet_id>": [{"label": "<trait label from that facet>",
                                   "confidence": "0.0-1.0"}]},
        "colour": {"dominant": "<plain colour word, e.g. amber, cobalt, clear>",
                   "family": "<warm | cool | neutral | multicolour>",
                   "notes": "<transparency, opacity, layering, colour reactions, "
                            "striking, metallic sheen>"},
        "construction": {"cane": "true|false", "murrine": "true|false",
                         "incalmo": "true|false",
                         "notes": "<what you see: twisted canes, murrine cross-sections, "
                                  "joined colour sections, optic ribbing, seams, lead came>"},
        "evidence": ["<one short observation that supports a label>"],
        "caveats": "<what this photo cannot show: scale, pontil, weight, backlit detail>",
    }, ensure_ascii=False, indent=1))
    lines.append("Rules: confidence is a number between 0 and 1. Labels must come from the "
                 "lists above; if you want a term that is not listed, describe it in "
                 "\"evidence\" or \"notes\" instead of inventing a label. Do not output "
                 "names of people, studios, places, dates or prices anywhere.")
    return "\n".join(lines)


# --- parsing ----------------------------------------------------------------

_TECH_BY_KEY: dict[str, str] = {}
for _t in techniques.TECHNIQUES:
    _TECH_BY_KEY[_t[1].lower()] = _t[1]
    _TECH_BY_KEY[_t[0].lower()] = _t[1]
    # tolerate the Glowtbook-era short labels ("Cane & murrine", "Kiln casting")
    _TECH_BY_KEY[re.sub(r"\s*\(.*?\)\s*", " ", _t[1]).strip().lower()] = _t[1]

_TRAIT_BY_KEY: dict[str, dict] = {}
for _c in glass_traits.CONCEPTS:
    _TRAIT_BY_KEY[_c["label"].lower()] = _c
    _TRAIT_BY_KEY[_c["id"].lower()] = _c
    for _a in _c.get("alt") or []:
        _TRAIT_BY_KEY.setdefault(_a.lower(), _c)

_FACET_BY_KEY: dict[str, str] = {}
for _f in glass_traits.FACETS:
    _FACET_BY_KEY[_f[0].lower()] = _f[0]
    _FACET_BY_KEY[_f[1].lower()] = _f[0]


def _norm_key(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def canonical_technique(label: str) -> str | None:
    """The registry's technique label for a free-text/model label (case-insensitive,
    tolerant of the older short Glowtbook spellings), or None."""
    return _TECH_BY_KEY.get(_norm_key(label))


def canonical_trait(label_or_id: str) -> dict | None:
    """The trait concept ({id, label, facet, ...}) for a label, id or altLabel, or None."""
    return _TRAIT_BY_KEY.get(_norm_key(label_or_id))


def _clamp(x, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return max(0.0, min(1.0, v))


def _as_bool(x) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x != 0
    return _norm_key(x) in ("true", "yes", "y", "1", "present", "visible")


def _as_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "; ".join(_as_str(i) for i in x if i is not None)
    return str(x).strip()


def _labelled(items) -> list[tuple[str, float]]:
    """Coerce whatever the model sent for a label list into [(label, confidence)]."""
    out: list[tuple[str, float]] = []
    if isinstance(items, dict):  # {"Label": 0.8, ...}
        items = [{"label": k, "confidence": v} for k, v in items.items()]
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, (list, tuple)):
        return out
    for it in items:
        if isinstance(it, str):
            out.append((it, 0.5))
        elif isinstance(it, dict):
            lab = it.get("label") or it.get("name") or it.get("trait") or it.get("technique")
            if lab:
                out.append((str(lab), _clamp(it.get("confidence", it.get("score", 0.5)))))
    return out


def _extract_json(text: str) -> dict | None:
    """Strip ``` fences and return the first balanced {...} object that parses."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if "```" in s:
        s = re.sub(r"```[a-zA-Z0-9_-]*\s*", "", s).replace("```", "")
    # scan every '{' and try the shortest balanced block from it
    depth, start = 0, -1
    in_str, esc = False, False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(s[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
                start = -1
    # last resort: first '{' to last '}'
    a, b = s.find("{"), s.rfind("}")
    if 0 <= a < b:
        try:
            obj = json.loads(s[a:b + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_response(text: str) -> dict:
    """Normalise a model reply into the closed shape. Tolerant of fences, prose
    around the JSON, missing keys, strings instead of objects, and percentages;
    unknown labels go to `unmapped`; confidences are clamped to [0, 1]. Never raises."""
    out = json.loads(json.dumps(EMPTY))  # deep copy
    try:
        raw = _extract_json(text)
        if not raw:
            return out
        unmapped: list[str] = []
        seen_t: set[str] = set()
        for lab, conf in _labelled(raw.get("techniques")):
            canon = _TECH_BY_KEY.get(_norm_key(lab))
            if canon and canon not in seen_t:
                seen_t.add(canon)
                out["techniques"].append({"label": canon, "confidence": conf})
            elif not canon:
                unmapped.append(str(lab).strip())

        traits_in = raw.get("traits")
        pairs: list[tuple[str | None, str, float]] = []
        if isinstance(traits_in, dict):
            for fk, items in traits_in.items():
                for lab, conf in _labelled(items):
                    pairs.append((_FACET_BY_KEY.get(_norm_key(fk)), lab, conf))
        else:  # a flat list — resolve the facet from the label
            for lab, conf in _labelled(traits_in):
                pairs.append((None, lab, conf))
        seen_tr: set[str] = set()
        for facet, lab, conf in pairs:
            c = _TRAIT_BY_KEY.get(_norm_key(lab))
            if not c:
                unmapped.append(str(lab).strip())
                continue
            if c["label"] in seen_tr:
                continue
            seen_tr.add(c["label"])
            # a label always lands in *its* facet, whatever key the model used
            out["traits"].setdefault(c["facet"], []).append(
                {"label": c["label"], "confidence": conf})
        for f in out["traits"].values():
            f.sort(key=lambda d: -d["confidence"])
        out["techniques"].sort(key=lambda d: -d["confidence"])

        col = raw.get("colour") or raw.get("color") or {}
        if isinstance(col, str):
            col = {"dominant": col}
        if isinstance(col, dict):
            out["colour"] = {"dominant": _as_str(col.get("dominant") or col.get("name")),
                             "family": _as_str(col.get("family")),
                             "notes": _as_str(col.get("notes"))}

        con = raw.get("construction") or {}
        if isinstance(con, str):
            con = {"notes": con}
        if isinstance(con, dict):
            out["construction"] = {"cane": _as_bool(con.get("cane")),
                                   "murrine": _as_bool(con.get("murrine")),
                                   "incalmo": _as_bool(con.get("incalmo")),
                                   "notes": _as_str(con.get("notes"))}

        ev = raw.get("evidence")
        if isinstance(ev, str):
            ev = [ev]
        if isinstance(ev, (list, tuple)):
            out["evidence"] = [_as_str(e) for e in ev if _as_str(e)]
        out["caveats"] = _as_str(raw.get("caveats"))
        # de-duplicate while keeping order
        out["unmapped"] = list(dict.fromkeys(u for u in unmapped if u))
    except Exception:  # noqa: BLE001 - a parser that raises defeats its purpose
        return json.loads(json.dumps(EMPTY))
    return out


# --- the model call ---------------------------------------------------------


def _get(url: str, timeout: float) -> dict | None:
    with httpx.Client(timeout=timeout) as cl:
        r = cl.get(url)
        r.raise_for_status()
        return r.json()


def _post(url: str, payload: dict, timeout: float) -> dict:
    """POST JSON and return the decoded reply. Tests monkeypatch this."""
    with httpx.Client(timeout=timeout) as cl:
        r = cl.post(url, json=payload)
        r.raise_for_status()
        return r.json()


def _friendly(ex: Exception, model: str) -> str:
    if httpx is not None:
        if isinstance(ex, httpx.ConnectError):
            return (f"Ollama isn't answering at {_url()} - start it with `ollama serve` "
                    "or fix OLLAMA_URL.")
        if isinstance(ex, httpx.TimeoutException):
            return ("The model took too long. CPU-only machines need a longer timeout or "
                    "a smaller model (try GLASS_VISION_MODEL=qwen2.5vl:3b).")
        if isinstance(ex, httpx.HTTPStatusError):
            code = ex.response.status_code
            if code == 404:
                return f"Model '{model}' isn't on this server - run `ollama pull {model}`."
            return f"Ollama returned HTTP {code}: {ex.response.text[:200]}"
    return f"{type(ex).__name__}: {ex}"


def suggest(image_bytes: bytes, *, techniques_hint=None, model: str | None = None,
            timeout: float = 90.0) -> dict:
    """Ask the local VLM for a suggestion about one image. Returns
    {"ok": True, "model", "latency_s", ...parsed} or {"ok": False, "error", "model"}."""
    model = model or MODEL
    if not available():
        return {"ok": False, "model": model,
                "error": ("Vision suggestions are off. To enable them, run Ollama locally "
                          "(`ollama pull qwen2.5vl:7b`) and set "
                          "OLLAMA_URL=http://127.0.0.1:11434 in .env.")}
    if httpx is None:
        return {"ok": False, "model": model, "error": "httpx is not installed."}
    try:
        from glowtbook.media import condense_image
        small, _mime = condense_image(image_bytes, max_px=1024)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": build_prompt(techniques_hint),
                          "images": [base64.b64encode(small).decode("ascii")]}],
            "format": "json", "stream": False,
            "options": {"temperature": 0.1},
        }
        t0 = time.monotonic()
        data = _post(f"{_url()}/api/chat", payload, timeout) or {}
        latency = round(time.monotonic() - t0, 2)
        msg = data.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if content is None:
            content = data.get("response", "")
        parsed = parse_response(content if isinstance(content, str) else json.dumps(content))
        return {"ok": True, "model": data.get("model") or model, "latency_s": latency, **parsed}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "model": model, "error": _friendly(ex, model)}


# --- no-model fallback: what the fingerprint already tells us -----------------


def colour_family(hex_or_name: str) -> str:
    """A coarse colour family from a hex code (or a colour word as a weak fallback)."""
    s = (hex_or_name or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", s)
    if not m:
        w = s.lower()
        if not w:
            return ""
        if any(k in w for k in ("clear", "colourless", "colorless", "crystal", "white", "grey",
                                "gray", "black", "smoke")):
            return "neutral"
        if any(k in w for k in ("blue", "cobalt", "teal", "green", "aqua", "violet", "purple")):
            return "cool"
        return "warm"
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    if val < 0.12:
        return "black"
    if sat < 0.12:
        return "neutral"
    deg = hue * 360
    for limit, name in ((15, "red"), (45, "orange"), (70, "yellow"), (170, "green"),
                        (200, "teal"), (260, "blue"), (300, "purple"), (345, "pink")):
        if deg < limit:
            return name
    return "red"


def _fp_obj(fp) -> dict:
    if isinstance(fp, (str, bytes)):
        try:
            fp = json.loads(fp)
        except Exception:  # noqa: BLE001
            return {}
    return fp if isinstance(fp, dict) else {}


def suggest_from_fingerprint(fp: dict) -> dict:
    """Describe a piece from its fingerprint metadata alone - no model needed.
    This is what the UI shows when OLLAMA_URL is not configured."""
    out = json.loads(json.dumps(EMPTY))
    f = _fp_obj(fp)
    md = f.get("metadata") or {}
    frames = f.get("frames") or []
    dc = md.get("dominantColor") or {}
    name = _as_str(dc.get("name")) if isinstance(dc, dict) else _as_str(dc)
    hexv = _as_str(dc.get("hex")) if isinstance(dc, dict) else ""
    dims = md.get("dimensions_mm") if isinstance(md.get("dimensions_mm"), dict) else None
    n_emb = sum(1 for fr in frames if isinstance(fr, dict) and fr.get("emb"))
    has_emb = bool(md.get("hasEmbeddings")) or n_emb > 0
    evidence: list[str] = []
    if name or hexv:
        out["colour"] = {"dominant": name, "family": colour_family(hexv or name),
                         "notes": "white-balanced against the capture mat; aggregated over "
                                  f"{len(frames)} view(s)"}
        evidence.append(f"dominant colour {name or hexv}" + (f" ({hexv})" if name and hexv else ""))
    if dims:
        w, d, h = dims.get("width"), dims.get("depth"), dims.get("height")
        evidence.append(f"measured {w} x {d} x {h} mm on the capture mat")
    if has_emb:
        evidence.append(f"{n_emb or 'some'} DINOv2 view(s) available for similarity search")
    out["evidence"] = evidence
    out["caveats"] = ("No vision model configured - this is descriptor metadata from the "
                      "fingerprint, not a reading of the piece. Set OLLAMA_URL for suggestions.")
    out.update({"ok": True, "model": None, "source": "fingerprint", "hex": hexv,
                "dimensions_mm": dims, "has_embeddings": has_emb, "views": len(frames),
                "rating": f.get("rating"), "tier": f.get("tier")})
    return out


# --- Stage 1 probe: nearest neighbours in DINOv2 space -----------------------


def centroid(fp: dict) -> list[float] | None:
    """Mean of frames[*].emb, L2-normalised. None when no frame carries an embedding."""
    f = _fp_obj(fp)
    embs = [fr["emb"] for fr in (f.get("frames") or [])
            if isinstance(fr, dict) and isinstance(fr.get("emb"), (list, tuple)) and fr["emb"]]
    if not embs:
        return None
    n = min(len(e) for e in embs)
    if n == 0:
        return None
    acc = [0.0] * n
    for e in embs:
        for i in range(n):
            try:
                acc[i] += float(e[i])
            except (TypeError, ValueError):
                pass
    acc = [v / len(embs) for v in acc]
    norm = math.sqrt(sum(v * v for v in acc))
    if norm == 0:
        return None
    return [v / norm for v in acc]


def cosine(a, b) -> float:
    """Cosine similarity; 0.0 for empty, mismatched or zero vectors."""
    try:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        dot = sum(float(a[i]) * float(b[i]) for i in range(n))
        na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n)))
        nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n)))
        if na == 0 or nb == 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (na * nb)))
    except (TypeError, ValueError):
        return 0.0


def _labels_of(rec: dict, key: str) -> list[str]:
    """Labels under `key` - a list of strings, a {facet: [labels]} dict, or a dotted
    path like "traits.filigrana"."""
    v = rec
    for part in key.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    if isinstance(v, dict):
        v = [x for vals in v.values() for x in (vals if isinstance(vals, list) else [vals])]
    if isinstance(v, str):
        v = [x.strip() for x in re.split(r"[|,]", v)]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("label")
        if x:
            out.append(str(x).strip())
    return out


def knn_suggest(fp_or_centroid, corpus: list[dict], k: int = 5,
                key: str = "techniques") -> list[dict]:
    """Vote labels from the k nearest corpus records (by cosine over
    `embedding_centroid`). Returns [{"label", "score", "support"}] sorted by
    weighted vote, where score is the neighbour-weighted share in [0, 1] and
    support is how many of the k neighbours carry the label."""
    if isinstance(fp_or_centroid, (list, tuple)):
        q = [float(x) for x in fp_or_centroid]
    else:
        q = centroid(fp_or_centroid)
    if not q:
        return []
    scored = []
    for rec in corpus or []:
        if not isinstance(rec, dict):
            continue
        emb = rec.get("embedding_centroid")
        if not isinstance(emb, (list, tuple)) or not emb:
            continue
        scored.append((cosine(q, emb), rec))
    scored.sort(key=lambda t: -t[0])
    top = scored[:max(1, int(k))]
    votes: dict[str, float] = {}
    support: dict[str, int] = {}
    total = 0.0
    for sim, rec in top:
        w = max(sim, 0.0)
        if w <= 0:
            continue
        labels = _labels_of(rec, key)
        if not labels:
            continue
        total += w
        for lab in dict.fromkeys(labels):
            votes[lab] = votes.get(lab, 0.0) + w
            support[lab] = support.get(lab, 0) + 1
    if total <= 0:
        return []
    out = [{"label": lab, "score": round(v / total, 4), "support": support[lab]}
           for lab, v in votes.items()]
    out.sort(key=lambda d: (-d["score"], -d["support"], d["label"]))
    return out
