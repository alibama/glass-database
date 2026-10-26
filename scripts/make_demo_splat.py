"""
scripts.make_demo_splat
=======================
Write a small procedural Gaussian splat of a blown-glass pumpkin so the 3D view
page has something to show before anyone has captured a real piece.

Output: ``deploy/splat/demo-pumpkin.splat`` in the plain ``.splat`` layout used by
the antimatter15 viewer and read by GaussianSplats3D: 32 bytes per splat ::

    float32 x, y, z          position (y up)
    float32 sx, sy, sz       scale (already exponentiated)
    uint8   r, g, b, a       colour and opacity
    uint8   qw, qx, qy, qz   rotation quaternion, (q * 128 + 128)

Deterministic (seeded), dependency-free, about 1.3 MB.  Run::

    python -m scripts.make_demo_splat
"""
from __future__ import annotations

import math
import random
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deploy" / "splat" / "demo-pumpkin.splat"
RNG = random.Random(20261025)
IDENTITY = bytes((255, 128, 128, 128))        # quaternion (1,0,0,0)


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _splat(x, y, z, s, rgb, a=1.0) -> bytes:
    r, g, b = rgb
    return (struct.pack("<3f", x, y, z) + struct.pack("<3f", s, s, s)
            + bytes((_clamp(r), _clamp(g), _clamp(b), _clamp(a * 255))) + IDENTITY)


def pumpkin(n_body: int = 36000, n_stem: int = 2600, n_ground: int = 1800) -> bytes:
    out = bytearray()
    ridges = 10
    # --- body: a squashed sphere with soft ridges, orange with amber shading ---
    for _ in range(n_body):
        u = RNG.random()
        v = RNG.random()
        theta = 2 * math.pi * u                      # around the axis
        phi = math.acos(2 * v - 1)                   # from the top
        ridge = 1 + 0.07 * math.cos(ridges * theta)  # lobes
        ridge *= 1 - 0.18 * abs(math.cos(phi)) ** 3   # flatten towards the poles
        r = ridge * (1 + RNG.gauss(0, 0.006))
        x = r * math.sin(phi) * math.cos(theta)
        z = r * math.sin(phi) * math.sin(theta)
        y = 0.78 * r * math.cos(phi)
        # colour: valleys darker, top slightly greener, a glassy highlight band
        valley = 0.5 + 0.5 * math.cos(ridges * theta)   # 1 on a lobe crest, 0 in a valley
        shade = 0.72 + 0.28 * valley
        top = max(0.0, math.cos(phi))
        base = (214 * shade, (104 + 30 * top) * shade, 28 * shade)
        hi = max(0.0, math.sin(phi) * math.cos(theta - 0.9)) ** 14   # a highlight facing the light
        rgb = tuple(c + (240 - c) * 0.75 * hi for c in base)
        a = 0.78 + 0.2 * RNG.random()
        out += _splat(x, y, z, 0.028 + 0.012 * RNG.random(), rgb, a)
    # --- a few bright seeds / bubbles under the surface ---
    for _ in range(600):
        theta = 2 * math.pi * RNG.random(); phi = math.acos(2 * RNG.random() - 1)
        r = 0.9 + 0.08 * RNG.random()
        out += _splat(r * math.sin(phi) * math.cos(theta), 0.78 * r * math.cos(phi),
                      r * math.sin(phi) * math.sin(theta), 0.012, (250, 236, 200), 0.9)
    # --- stem: a curved, tapering twist in olive glass ---
    for _ in range(n_stem):
        t = RNG.random()                            # 0 at the base, 1 at the tip
        ang = 2 * math.pi * RNG.random()
        rad = (0.16 - 0.09 * t) * (1 + 0.1 * math.cos(5 * ang + 6 * t))
        bend = 0.22 * t * t
        x = rad * math.cos(ang) + bend
        z = rad * math.sin(ang)
        y = 0.62 + 0.42 * t
        g = 0.65 + 0.35 * t
        out += _splat(x, y, z, 0.02 + 0.01 * RNG.random(), (92 * g, 112 * g, 48 * g), 0.95)
    # --- ground: a faint paper disc so the piece sits on something ---
    for _ in range(n_ground):
        ang = 2 * math.pi * RNG.random(); rad = 1.65 * math.sqrt(RNG.random())
        out += _splat(rad * math.cos(ang), -0.63, rad * math.sin(ang), 0.07,
                      (227, 215, 196), 0.22)
    return bytes(out)


def main() -> None:
    data = pumpkin()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT} ({len(data) // 32} splats, {len(data) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
